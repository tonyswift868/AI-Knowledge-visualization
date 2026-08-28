from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

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
from app.enterprise.jobs_v1.models import (
    JobRequest,
    JobStatus,
    OutboxStatus,
    WorkflowKind,
)
from app.enterprise.jobs_v1.procrastinate_adapter import unwrap_psycopg_connection
from app.enterprise.jobs_v1.repository import WorkflowRepository
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
    app, task = build_procrastinate_app(
        _config(tmp_path / "readiness.json"),
        DurableJobExecutor(_engine(), _dependencies(_Handler())),
    )
    assert app.tasks["mentorflow.enterprise.execute_workflow"] is task


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
    assert "container-published-digests" in workflow
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
    assert "schema_manager.apply_schema()" in source
    assert source.index("SELECT version_num FROM alembic_version") < source.index(
        "schema_manager.apply_schema()"
    )
    assert "mentorflow_app" not in source
