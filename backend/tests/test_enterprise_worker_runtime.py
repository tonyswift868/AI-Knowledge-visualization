from __future__ import annotations

import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.pool import StaticPool

from app.enterprise import worker_entry
from app.enterprise.contracts_v1 import (
    AccessDecision,
    AccessDecisionKind,
    ActorContext,
    ActorKind,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)
from app.enterprise.jobs_v1 import postgres_schema
from app.enterprise.jobs_v1.models import (
    JobRequest,
    JobStatus,
    OutboxStatus,
    WorkflowKind,
)
from app.enterprise.jobs_v1.procrastinate_adapter import unwrap_psycopg_connection
from app.enterprise.jobs_v1.repository import WorkflowRepository, _insert_do_nothing
from app.enterprise.jobs_v1.retry import RetryPolicy
from app.enterprise.jobs_v1.runtime import (
    DurableJobExecutor,
    DurableWorkerDependencies,
    WorkerConfigurationError,
    WorkerRuntimeConfig,
    WorkerSupervisor,
    build_procrastinate_app,
    load_worker_dependencies,
    read_readiness,
)
from app.enterprise.jobs_v1.schema import ENTERPRISE_JOBS_SCHEMA, jobs, metadata, outbox
from app.enterprise.jobs_v1.service import WorkflowService, WorkflowSubmissionDenied
from app.enterprise.worker_entry import load_config


NOW = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[2]


def _engine() -> sa.Engine:
    engine = sa.create_engine(
        "sqlite+pysqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={ENTERPRISE_JOBS_SCHEMA: None})
    with engine.begin() as connection:
        metadata.create_all(connection)
    return engine


def _contexts() -> tuple[ActorContext, TenantContext]:
    actor = ActorContext(
        subject_id="worker-subject",
        actor_kind=ActorKind.SERVICE,
        issuer="https://identity.example.test/realms/mentorflow",
        token_id="worker-token",
        session_id="worker-session",
        request_id="worker-request",
        authentication_time=NOW,
        authentication_strength="client-credentials",
        organization_id="org-1",
        workspace_id="ws-1",
        service_permissions=("WORKFLOW_EXECUTE",),
    )
    return actor, TenantContext(
        organization_id="org-1",
        workspace_id="ws-1",
        mode=TenantMode.ENTERPRISE_SERVER,
    )


def _decision(actor: ActorContext, allow: bool = True) -> AccessDecision:
    return AccessDecision(
        decision=AccessDecisionKind.ALLOW if allow else AccessDecisionKind.DENY,
        policy_id="worker-revalidation",
        reason_code="CURRENT_MEMBERSHIP" if allow else "MEMBERSHIP_REVOKED",
        actor=actor,
        resource=ResourceScope(
            scope_type=ResourceScopeType.WORKSPACE,
            organization_id="org-1",
            workspace_id="ws-1",
            classification=ResourceClassification.INTERNAL,
        ),
        action="enterprise_jobs.execute",
        evaluated_at=NOW,
    )


def _request(key: str, *, max_attempts: int = 3) -> JobRequest:
    actor, tenant = _contexts()
    return JobRequest(
        workflow_kind=WorkflowKind.KNOWLEDGE_PIPELINE,
        payload={"source_id": "source-1"},
        actor_context=actor,
        tenant_context=tenant,
        request_id=actor.request_id,
        trace_id="worker-trace",
        idempotency_key=key,
        max_attempts=max_attempts,
        scheduled_at=NOW,
    )


class _Revalidator:
    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.seen: list[tuple[str | None, str | None]] = []

    def revalidate(self, *, actor, tenant, workflow_kind):
        self.seen.append((tenant.organization_id, tenant.workspace_id))
        return _decision(actor, self.allow)


class _Handler:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.calls = 0
        self.seen_partition: str | None = None

    def execute(self, *, job, checkpoint, ensure_not_cancelled):
        self.calls += 1
        self.seen_partition = job.tenant_partition
        ensure_not_cancelled()
        if self.fail_once and self.calls == 1:
            raise RuntimeError("Bearer handler-super-secret")
        checkpoint("processed", {"source_id": job.payload["source_id"]})
        return {"status": "processed"}


def _dependencies(handler: _Handler, revalidator: _Revalidator | None = None):
    handlers = {kind: handler for kind in WorkflowKind}
    return DurableWorkerDependencies(revalidator or _Revalidator(), handlers)


def _config(readiness_path: Path) -> WorkerRuntimeConfig:
    return WorkerRuntimeConfig(
        database_url=sa.make_url(
            "postgresql+psycopg://mentorflow_app:password@database/mentorflow"
        ),
        runtime_factory="tests.worker_fixture:build",
        readiness_path=readiness_path,
    )


def test_worker_config_is_enterprise_only_and_never_echoes_database_secret() -> None:
    source = {
        "MENTORFLOW_DEPLOYMENT_MODE": "ENTERPRISE_SERVER",
        "MENTORFLOW_DATABASE_URL": (
            "postgresql+psycopg://mentorflow_app:very-secret@database/mentorflow"
        ),
        "MENTORFLOW_WORKER_RUNTIME_FACTORY": "mentorflow.worker:build",
        "MENTORFLOW_WORKER_QUEUES": "mentorflow.enterprise,mentorflow.export",
    }
    config = load_config(source)
    assert config.database_url.username == "mentorflow_app"
    assert config.policy.queues == ("mentorflow.enterprise", "mentorflow.export")
    with pytest.raises(WorkerConfigurationError) as failure:
        load_config({"MENTORFLOW_DEPLOYMENT_MODE": "PERSONAL_LOCAL"})
    assert "very-secret" not in str(failure.value)


def test_dependency_factory_must_register_every_workflow_kind(monkeypatch) -> None:
    engine = _engine()
    handler = _Handler()
    module = types.ModuleType("worker_fixture")
    module.build = lambda *, engine: DurableWorkerDependencies(  # type: ignore[attr-defined]
        _Revalidator(), {WorkflowKind.EXPORT: handler}
    )
    monkeypatch.setitem(sys.modules, "worker_fixture", module)
    with pytest.raises(WorkerConfigurationError, match="every workflow kind"):
        load_worker_dependencies("worker_fixture:build", engine=engine)
    complete = _dependencies(handler)
    module.build = lambda *, engine: complete  # type: ignore[attr-defined]
    assert load_worker_dependencies("worker_fixture:build", engine=engine) is complete


def test_authorization_deny_cannot_enqueue_privileged_work() -> None:
    engine = _engine()
    request = _request("idem-denied-enqueue")
    decision = _decision(request.actor_context, False).model_copy(
        update={"action": "enterprise_jobs.enqueue"}
    )
    with engine.begin() as connection:
        with pytest.raises(WorkflowSubmissionDenied):
            WorkflowService().submit_authorized(
                connection,
                request,
                authorization=decision,
            )
    with engine.begin() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(jobs)) == 0
        assert connection.scalar(sa.select(sa.func.count()).select_from(outbox)) == 0


def test_runtime_executes_with_frozen_tenant_context_and_blocks_duplicate_delivery() -> None:
    engine = _engine()
    repository = WorkflowRepository()
    handler = _Handler()
    revalidator = _Revalidator()
    with engine.begin() as connection:
        receipt = repository.submit(connection, _request("idem-runtime-success"))
    executor = DurableJobExecutor(
        engine,
        _dependencies(handler, revalidator),
        repository=repository,
        clock=lambda: NOW,
    )
    first = executor.execute(job_id=receipt.job_id, dispatch_generation=0)
    duplicate = executor.execute(job_id=receipt.job_id, dispatch_generation=0)
    assert first is not None and first.status is JobStatus.SUCCEEDED
    assert duplicate is not None and duplicate.status is JobStatus.SUCCEEDED
    assert handler.calls == 1
    assert handler.seen_partition == "ENTERPRISE_SERVER:org-1:ws-1"
    assert revalidator.seen == [("org-1", "ws-1")]
    assert first.checkpoint["processed"]["source_id"] == "source-1"


@pytest.mark.parametrize(
    ("inserted_key", "expected_created"),
    [("job_inserted", True), (None, False)],
)
def test_postgresql_insert_conflict_uses_returning_primary_key(
    inserted_key: str | None,
    expected_created: bool,
) -> None:
    class _Result:
        def scalar_one_or_none(self) -> str | None:
            return inserted_key

    class _Connection:
        dialect = postgresql.dialect()
        statement: sa.Executable | None = None

        def execute(self, statement: sa.Executable) -> _Result:
            self.statement = statement
            return _Result()

    connection = _Connection()
    created = _insert_do_nothing(
        connection,  # type: ignore[arg-type]
        jobs,
        {"job_id": "job_inserted"},
        index_elements=(
            jobs.c.tenant_partition,
            jobs.c.workflow_kind,
            jobs.c.idempotency_key,
        ),
    )

    assert connection.statement is not None
    compiled = str(connection.statement.compile(dialect=connection.dialect))
    assert "ON CONFLICT (tenant_partition, workflow_kind, idempotency_key) DO NOTHING" in compiled
    assert "RETURNING enterprise_jobs.jobs.job_id" in compiled
    assert created is expected_created


def test_repository_submission_creates_one_job_and_outbox_per_new_key() -> None:
    engine = _engine()
    repository = WorkflowRepository()

    with engine.begin() as connection:
        first = repository.submit(connection, _request("idem-repository-first"))
        assert first.duplicate is False
        assert connection.scalar(sa.select(sa.func.count()).select_from(jobs)) == 1
        assert connection.scalar(sa.select(sa.func.count()).select_from(outbox)) == 1

    with engine.begin() as connection:
        duplicate = repository.submit(connection, _request("idem-repository-first"))
        assert duplicate.job_id == first.job_id
        assert duplicate.duplicate is True
        assert connection.scalar(sa.select(sa.func.count()).select_from(jobs)) == 1
        assert connection.scalar(sa.select(sa.func.count()).select_from(outbox)) == 1

    with engine.begin() as connection:
        distinct = repository.submit(connection, _request("idem-repository-distinct"))
        assert distinct.job_id != first.job_id
        assert distinct.duplicate is False
        assert connection.scalar(sa.select(sa.func.count()).select_from(jobs)) == 2
        assert connection.scalar(sa.select(sa.func.count()).select_from(outbox)) == 2


def test_runtime_retries_with_new_generation_then_replays_to_success(caplog) -> None:
    engine = _engine()
    repository = WorkflowRepository()
    handler = _Handler(fail_once=True)
    current_time = [NOW]
    service = WorkflowService(
        repository,
        RetryPolicy(base_seconds=1, maximum_seconds=10, jitter_ratio=0),
    )
    with engine.begin() as connection:
        receipt = repository.submit(
            connection,
            _request("idem-runtime-retry", max_attempts=2),
        )
    executor = DurableJobExecutor(
        engine,
        _dependencies(handler),
        repository=repository,
        service=service,
        clock=lambda: current_time[0],
    )
    failed = executor.execute(job_id=receipt.job_id, dispatch_generation=0)
    stale = executor.execute(job_id=receipt.job_id, dispatch_generation=0)
    assert failed is not None and failed.status is JobStatus.FAILED_RETRYABLE
    assert failed.dispatch_generation == 1
    assert stale is not None and stale.status is JobStatus.FAILED_RETRYABLE
    assert handler.calls == 1
    assert "handler-super-secret" not in caplog.text
    current_time[0] = NOW + timedelta(seconds=2)
    succeeded = executor.execute(job_id=receipt.job_id, dispatch_generation=1)
    assert succeeded is not None and succeeded.status is JobStatus.SUCCEEDED
    assert handler.calls == 2


def test_runtime_revalidation_denial_commits_dead_letter_without_handler_call() -> None:
    engine = _engine()
    repository = WorkflowRepository()
    handler = _Handler()
    with engine.begin() as connection:
        receipt = repository.submit(connection, _request("idem-runtime-denied"))
    executor = DurableJobExecutor(
        engine,
        _dependencies(handler, _Revalidator(allow=False)),
        repository=repository,
        clock=lambda: NOW,
    )
    denied = executor.execute(job_id=receipt.job_id, dispatch_generation=0)
    assert denied is not None and denied.status is JobStatus.DEAD_LETTER
    assert handler.calls == 0
    with engine.begin() as connection:
        assert repository.get_dead_letter(connection, receipt.job_id) is not None


def test_supervisor_recovers_expired_claim_and_publishes_replay() -> None:
    engine = _engine()
    repository = WorkflowRepository()
    with engine.begin() as connection:
        receipt = repository.submit(connection, _request("idem-supervisor-recovery"))
        claimed = repository.claim_job(
            connection, receipt.job_id, now=NOW, lease_seconds=15
        )
        assert claimed is not None

    class _Dispatcher:
        def dispatch(self, connection, event):
            return f"engine-{event.dispatch_generation}"

    supervisor = WorkerSupervisor(
        _config(Path.cwd() / "readiness-unused.json"),
        engine,
        object(),
        repository=repository,
    )
    supervisor.dispatcher = _Dispatcher()  # type: ignore[assignment]
    recovered, published = supervisor.run_once()
    assert recovered == 1
    assert published == 2
    with engine.begin() as connection:
        events = repository.get_outbox_for_job(connection, receipt.job_id)
    assert [event.status for event in events] == [
        OutboxStatus.DISPATCHED,
        OutboxStatus.DISPATCHED,
    ]


def test_readiness_requires_fresh_ready_evidence(tmp_path: Path) -> None:
    path = tmp_path / "worker-readiness.json"
    payload = {
        "schema_version": "mentorflow.worker-readiness.v1",
        "status": "READY",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert read_readiness(path)["status"] == "READY"
    payload["updated_at"] = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(WorkerConfigurationError, match="stale"):
        read_readiness(path, maximum_age_seconds=30)


def test_procrastinate_app_registers_canonical_task_and_exact_version(tmp_path: Path) -> None:
    app, defer_task = build_procrastinate_app(
        _config(tmp_path / "readiness.json"),
        DurableJobExecutor(_engine(), _dependencies(_Handler())),
    )
    consume_task = app.tasks["mentorflow.enterprise.execute_workflow"]
    assert consume_task.name == defer_task.name == "mentorflow.enterprise.execute_workflow"
    assert type(app.connector).__name__ == "PsycopgConnector"
    assert type(defer_task.blueprint.connector).__name__ == "SyncPsycopgConnector"


def test_external_connection_unwrap_is_fail_closed_for_sqlite() -> None:
    engine = _engine()
    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="active PostgreSQL transaction"):
            unwrap_psycopg_connection(connection)


def test_worker_image_and_release_workflow_are_digest_capable() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "container-release.yml"
    ).read_text(encoding="utf-8")
    assert "FROM backend-base AS worker-image" in dockerfile
    assert '"app.enterprise.worker_entry", "health"' in dockerfile
    assert '"app.enterprise.worker_entry", "run"' in dockerfile
    assert "backend frontend document-worker worker" in workflow
    assert "final-image-evidence.json" in workflow
    assert "final-image-digest-lock.json" in workflow
    assert "rp05-application-images.json" in workflow
    assert 'digest_source:"docker buildx imagetools inspect"' in workflow
    assert "sha256:[0-9a-f]{64}" in workflow


def test_worker_schema_bootstrap_preserves_canonical_alembic_head_contract() -> None:
    source = (
        REPO_ROOT
        / "backend"
        / "app"
        / "enterprise"
        / "jobs_v1"
        / "postgres_schema.py"
    ).read_text(encoding="utf-8")
    assert "ENTERPRISE_MIGRATION_REVISION" in source
    assert "with app.open():" in source
    assert "schema_manager.apply_schema()" in source
    assert source.index("SELECT version_num FROM alembic_version") < source.index(
        "with app.open():"
    ) < source.index(
        "schema_manager.apply_schema()"
    )
    assert "mentorflow_app" not in source


@pytest.mark.parametrize("schema_fails", [False, True])
def test_worker_schema_bootstrap_owns_procrastinate_app_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    schema_fails: bool,
) -> None:
    events: list[str] = []

    class _Connection:
        def __init__(self, phase: str) -> None:
            self.phase = phase

        def scalar(self, statement) -> str:
            assert "alembic_version" in str(statement)
            events.append(f"revision.{self.phase}")
            return postgres_schema.ENTERPRISE_MIGRATION_REVISION

    class _ConnectionContext:
        def __init__(self, phase: str) -> None:
            self.connection = _Connection(phase)

        def __enter__(self) -> _Connection:
            return self.connection

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            return None

    class _Engine:
        def connect(self) -> _ConnectionContext:
            return _ConnectionContext("before")

        def begin(self) -> _ConnectionContext:
            return _ConnectionContext("after")

        def dispose(self) -> None:
            events.append("engine.dispose")

    class _SchemaManager:
        def apply_schema(self) -> None:
            events.append("schema.apply")
            if schema_fails:
                raise RuntimeError("schema-apply-failed")

    schema_manager = _SchemaManager()

    class _App:
        def __init__(self, *, connector) -> None:
            assert connector == "connector"
            self.opened = False
            events.append("app.create")

        def open(self) -> "_App":
            assert self.opened is False
            self.opened = True
            events.append("app.open")
            return self

        def __enter__(self) -> "_App":
            assert self.opened is True
            events.append("app.enter")
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            events.append("app.exit")
            self.close()

        def close(self) -> None:
            assert self.opened is True
            self.opened = False
            events.append("app.close")

        @property
        def schema_manager(self) -> _SchemaManager:
            assert self.opened is True
            return schema_manager

    connector_options: dict[str, object] = {}

    def _connector(**options):
        connector_options.update(options)
        events.append("connector.create")
        return "connector"

    engine = _Engine()
    monkeypatch.setattr(
        postgres_schema.sa,
        "create_engine",
        lambda *args, **kwargs: engine,
    )
    monkeypatch.setattr(postgres_schema.procrastinate, "PsycopgConnector", _connector)
    monkeypatch.setattr(postgres_schema.procrastinate, "App", _App)
    monkeypatch.setattr(
        postgres_schema,
        "_grant_runtime_objects",
        lambda connection: events.append("grants.apply"),
    )
    database_url = sa.make_url(
        "postgresql+psycopg://migration_owner:test-password@database/mentorflow"
    )

    if schema_fails:
        with pytest.raises(RuntimeError, match="schema-apply-failed"):
            postgres_schema.apply_worker_schema(database_url)
    else:
        postgres_schema.apply_worker_schema(database_url)

    expected = [
        "revision.before",
        "connector.create",
        "app.create",
        "app.open",
        "app.enter",
        "schema.apply",
        "app.exit",
        "app.close",
    ]
    if schema_fails:
        expected.append("engine.dispose")
    else:
        expected.extend(["revision.after", "grants.apply", "engine.dispose"])
    assert events == expected
    assert connector_options == {
        "conninfo": (
            "postgresql://migration_owner:test-password@database/mentorflow"
        ),
        "min_size": 1,
        "max_size": 1,
    }


class _GrantConnection:
    def __init__(self, *, missing: set[str] | None = None) -> None:
        self.missing = missing or set()
        self.lookups: list[tuple[str, str]] = []
        self.statements: list[str] = []

    def scalar(self, statement, parameters: dict[str, str]) -> str | None:
        object_name = parameters["object_name"]
        self.lookups.append((str(statement), object_name))
        if object_name in self.missing:
            return None
        return object_name

    def execute(self, statement) -> None:
        self.statements.append(str(statement))


def test_worker_schema_grants_exact_current_procrastinate_objects_without_version(
) -> None:
    connection = _GrantConnection()

    skipped = postgres_schema._grant_runtime_objects(connection)

    assert skipped == ("procrastinate_version",)
    granted_sql = "\n".join(connection.statements)
    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.procrastinate_events"
        in granted_sql
    )
    assert "pg_get_serial_sequence(" in granted_sql
    for table_name in postgres_schema.PROCRASTINATE_REQUIRED_TABLES:
        assert f"'{table_name}'" in granted_sql
    assert "pg_catalog.pg_depend" not in granted_sql
    assert "information_schema.sequences" not in granted_sql
    assert "sequence_name LIKE 'procrastinate_%'" not in granted_sql
    assert "owning_table.relname LIKE" not in granted_sql
    assert "function.proname LIKE 'procrastinate_%'" in granted_sql
    assert "public.procrastinate_version" not in granted_sql
    assert "ON ALL TABLES IN SCHEMA" not in granted_sql
    assert "ON ALL SEQUENCES IN SCHEMA" not in granted_sql
    assert "SUPERUSER" not in granted_sql
    assert "BYPASSRLS" not in granted_sql
    looked_up = {object_name for _, object_name in connection.lookups}
    assert "public.procrastinate_job_event_type" in looked_up
    assert (
        "public.procrastinate_defer_jobs_v1("
        "public.procrastinate_job_to_defer_v1[])"
    ) in looked_up
    assert "public.procrastinate_version" not in looked_up


def test_worker_schema_requires_all_current_serial_and_identity_sequences() -> None:
    connection = _GrantConnection()

    postgres_schema._grant_runtime_objects(connection)

    assert postgres_schema.PROCRASTINATE_REQUIRED_SEQUENCES == (
        "procrastinate_events_id_seq",
        "procrastinate_jobs_id_seq",
        "procrastinate_periodic_defers_id_seq",
        "procrastinate_workers_id_seq",
    )
    looked_up = {object_name for _, object_name in connection.lookups}
    assert {
        f"public.{sequence_name}"
        for sequence_name in postgres_schema.PROCRASTINATE_REQUIRED_SEQUENCES
    } <= looked_up


def test_worker_schema_never_grants_retired_procrastinate_version() -> None:
    connection = _GrantConnection()

    skipped = postgres_schema._grant_runtime_objects(connection)

    assert skipped == ("procrastinate_version",)
    assert all(
        "public.procrastinate_version" not in statement
        for statement in connection.statements
    )


@pytest.mark.parametrize(
    "missing_object",
    [
        "public.procrastinate_jobs",
        "public.procrastinate_jobs_id_seq",
        "public.procrastinate_job_status",
        "public.procrastinate_fetch_job_v2(character varying[],bigint)",
    ],
)
def test_worker_schema_fails_closed_when_required_procrastinate_object_is_missing(
    missing_object: str,
) -> None:
    connection = _GrantConnection(missing={missing_object})

    with pytest.raises(
        postgres_schema.WorkerSchemaError,
        match="required Procrastinate runtime objects missing",
    ):
        postgres_schema._grant_runtime_objects(connection)

    assert connection.statements == []


@pytest.mark.parametrize(
    ("otel_enabled", "run_error"),
    [
        (True, None),
        (True, RuntimeError("postgresql://user:password@database credential=secret")),
        (False, None),
    ],
)
def test_production_entrypoint_owns_worker_otel_lifecycle(
    monkeypatch,
    otel_enabled: bool,
    run_error: RuntimeError | None,
) -> None:
    events: list[str] = []
    engine = types.SimpleNamespace(dispose=lambda: events.append("dispose"))
    config = types.SimpleNamespace(
        policy=types.SimpleNamespace(
            queues=("mentorflow.enterprise",),
            concurrency=2,
            stalled_worker_timeout_seconds=30,
        ),
        worker_name="worker-entrypoint-test",
        shutdown_graceful_timeout_seconds=20.0,
    )

    class _App:
        def run_worker(self, **kwargs):
            events.append("run")
            assert kwargs["queues"] == ["mentorflow.enterprise"]
            assert type(kwargs["queues"]) is list
            assert kwargs["wait"] is True
            if run_error is not None:
                raise run_error

    class _Supervisor:
        def start(self):
            events.append("supervisor.start")

        def stop(self):
            events.append("supervisor.stop")

    def configure_otel(*, sqlalchemy_engine):
        assert sqlalchemy_engine is engine
        events.append("otel.configure")

    monkeypatch.setenv("MENTORFLOW_OTEL_ENABLED", "true" if otel_enabled else "false")
    monkeypatch.setattr(worker_entry, "load_config", lambda source: config)
    monkeypatch.setattr(
        worker_entry.orm,
        "configure_enterprise_orm",
        lambda source: engine,
    )
    monkeypatch.setattr(
        worker_entry,
        "configure_worker_open_telemetry",
        configure_otel,
    )
    monkeypatch.setattr(
        worker_entry,
        "verify_worker_database",
        lambda candidate: events.append("database.verify"),
    )
    monkeypatch.setattr(
        worker_entry,
        "_build_runtime",
        lambda config, engine: (_App(), object()),
    )
    monkeypatch.setattr(
        worker_entry,
        "_check_connection",
        lambda app: events.append("queue.check"),
    )
    monkeypatch.setattr(
        worker_entry,
        "WorkerSupervisor",
        lambda config, engine, task: _Supervisor(),
    )
    monkeypatch.setattr(
        worker_entry,
        "force_flush_open_telemetry",
        lambda timeout: events.append(f"otel.flush:{timeout}"),
    )
    monkeypatch.setattr(
        worker_entry,
        "shutdown_open_telemetry",
        lambda timeout: events.append(f"otel.shutdown:{timeout}"),
    )

    if run_error is None:
        assert worker_entry._run(dict(os.environ)) == 0
    else:
        with pytest.raises(RuntimeError) as failure:
            worker_entry._run(dict(os.environ))
        assert failure.value is run_error

    assert events == [
        "otel.configure",
        "database.verify",
        "queue.check",
        "supervisor.start",
        "run",
        "supervisor.stop",
        "otel.flush:5000",
        "otel.shutdown:5000",
        "dispose",
    ]


def test_worker_entrypoint_failure_output_redacts_database_credentials(
    monkeypatch,
    caplog,
    capsys,
) -> None:
    sensitive = "postgresql://mentorflow_app:password@database credential=secret"

    def fail_run(source):
        raise RuntimeError(sensitive)

    monkeypatch.setattr(worker_entry, "_run", fail_run)
    assert worker_entry.main(["run"]) == 1
    captured = capsys.readouterr()
    projection = captured.out + captured.err + caplog.text
    assert "RuntimeError" in projection
    assert "postgresql://" not in projection
    assert "password" not in projection
    assert "credential" not in projection
    assert "secret" not in projection
