from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from threading import Barrier
import time

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import URL, make_url
import yaml

from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    TenantContext,
    TenantMode,
)
from app.enterprise.jobs_v1.models import (
    JobRequest,
    JobStatus,
    OutboxStatus,
    WorkflowKind,
)
from app.enterprise.jobs_v1.postgres_schema import (
    PROCRASTINATE_OPTIONAL_FUNCTIONS,
    PROCRASTINATE_REQUIRED_FUNCTIONS,
    PROCRASTINATE_REQUIRED_SEQUENCES,
    PROCRASTINATE_REQUIRED_TABLES,
    PROCRASTINATE_REQUIRED_TYPES,
    PROCRASTINATE_RETIRED_TABLES,
    apply_worker_schema,
)
from app.enterprise.jobs_v1.production import (
    PRODUCTION_FACTORY_DOTTED_PATH,
    handler_environment_name,
)
from app.enterprise.jobs_v1.repository import WorkflowRepository
from app.enterprise.jobs_v1.runtime import verify_worker_database
from app.enterprise.jobs_v1.schema import jobs as jobs_table
from app.enterprise.jobs_v1.schema import outbox as outbox_table
from app.enterprise_tenancy.context import (
    create_enterprise_postgres_engine,
    inspect_application_role_security,
)
from app.enterprise_tenancy.schema import (
    APPLICATION_DATABASE_ROLE,
    ENTERPRISE_MIGRATION_REVISION,
    FORCE_RLS_TABLES,
)
from app.enterprise_tenancy.synthetic import seed_synthetic_tenants


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
EXPECTED_SOURCE_HEAD = "6e3eaafd36a2a42351d236473a8ae72a2c70aefd"
EXPECTED_SOURCE_TREE = "375f3bcdedf1985f992d464e9fc05516ce838825"
EXPECTED_RUNNER = "mentorflow-e1-turkey-01"
REAL_GATE_BRANCH = "codex/e1-postgres17-real-gate-r1"
LOCKED_POSTGRES_IMAGE = (
    "docker.io/library/postgres:17.6-bookworm@"
    "sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3"
)
ISSUER = "https://identity.e102.invalid/realms/mentorflow"


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        if os.environ.get("E1_REAL_GATE_REQUIRED") == "1":
            pytest.fail(f"{name} is required by the dedicated real PostgreSQL gate")
        pytest.skip(f"{name} is available only in the dedicated real PostgreSQL gate")
    return value


def _database_name(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{7,62}", value):
        pytest.fail("worker database name is outside the disposable gate contract")
    return value


def _password(name: str) -> str:
    value = _required_environment(name)
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,120}", value):
        pytest.fail(f"{name} is outside the disposable credential contract")
    return value


def _postgres_url(name: str, *, database: str | None = None) -> URL:
    raw = _required_environment(name)
    try:
        parsed = make_url(raw)
    except Exception:
        pytest.fail(f"{name} must be a valid PostgreSQL URL", pytrace=False)
    if parsed.get_backend_name() != "postgresql":
        pytest.fail(f"{name} must target PostgreSQL", pytrace=False)
    result = parsed.set(drivername="postgresql+psycopg")
    if database is not None and result.database != database:
        pytest.fail(f"{name} must target the exact disposable worker database")
    return result


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.attributes["configure_logger"] = False
    return config


def _membership_id(issuer: str, subject: str, organization_id: str) -> str:
    material = "\x1f".join((issuer, subject, organization_id)).encode("utf-8")
    return f"membership:{hashlib.sha256(material).hexdigest()}"


def _redacted_tail(value: str, *secrets: str) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted[-2_000:]


def _stop_worker(process: subprocess.Popen[str]) -> tuple[int, str, str]:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        stdout, stderr = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate(timeout=10)
    assert process.returncode is not None
    return process.returncode, stdout, stderr


def test_postgres17_real_gate_assets_are_exact_and_runner_scoped() -> None:
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "enterprise-ci.yml"
    ).read_text(encoding="utf-8")
    compose = (
        REPO_ROOT / "enterprise_e1" / "staging" / "e1_02" / "compose.yaml"
    ).read_text(encoding="utf-8")
    runner = (
        REPO_ROOT / "enterprise_e1" / "staging" / "e1_02" / "run.sh"
    ).read_text(encoding="utf-8")

    job_start = workflow.index("  postgres17-rls-worker-real-gate:")
    job_end = workflow.index("\n  readiness-provenance:", job_start)
    job = workflow[job_start:job_end]
    lane_start = workflow.index("  lane-contract:")
    lane_end = workflow.index("\n  postgres17-rls-worker-real-gate:", lane_start)
    lane_contract = workflow[lane_start:lane_end]
    parsed_workflow = yaml.safe_load(workflow)
    jobs = parsed_workflow["jobs"]
    workflow_triggers = parsed_workflow.get("on", parsed_workflow.get(True))
    assert workflow_triggers is not None
    parsed_compose = yaml.safe_load(compose)
    runner_environment = parsed_compose["services"]["one-shot-test-runner"][
        "environment"
    ]

    assert f'      - "{REAL_GATE_BRANCH}"' in workflow
    assert "github.event_name == 'push'" in job
    assert f"github.ref == 'refs/heads/{REAL_GATE_BRANCH}'" in job
    assert "runs-on: [self-hosted, linux, x64, mentorflow-e1, turkey-ci]" in job
    assert EXPECTED_RUNNER in job
    assert EXPECTED_SOURCE_HEAD in job
    assert EXPECTED_SOURCE_TREE in job
    assert 'git merge-base --is-ancestor "$EXPECTED_SOURCE_HEAD" HEAD' in job
    assert (
        'git rev-list --merges --count "$EXPECTED_SOURCE_HEAD..HEAD"' in job
    )
    assert "git rev-list --count" not in job
    assert (
        "comm -23 /tmp/e102-changed.txt /tmp/e102-allowed.txt "
        "> /tmp/e102-unauthorized.txt" in job
    )
    assert "test ! -s /tmp/e102-unauthorized.txt" in job
    assert "diff -u /tmp/e102-allowed.txt /tmp/e102-changed.txt" not in job
    assert workflow_triggers["push"]["paths"] == [
        ".github/workflows/enterprise-ci.yml",
        "backend/tests/test_enterprise_tenant_persistence.py",
        "backend/tests/test_enterprise_postgres_worker_integration.py",
        "enterprise_e1/staging/e1_02/compose.yaml",
        "enterprise_e1/staging/e1_02/run.sh",
        "backend/app/enterprise/jobs_v1/postgres_schema.py",
        "backend/tests/test_enterprise_worker_runtime.py",
        "backend/app/enterprise/jobs_v1/repository.py",
    ]
    assert "ubuntu-latest" not in job
    assert "ubuntu-24.04" not in job
    assert "windows-latest" not in job
    assert re.findall(r"uses: [^\s]+@([0-9a-f]{40})", job) == [
        "11d5960a326750d5838078e36cf38b85af677262"
    ]
    assert f"github.ref != 'refs/heads/{REAL_GATE_BRANCH}'" in lane_contract
    assert jobs["postgres17-rls-worker-real-gate"]["runs-on"] == [
        "self-hosted",
        "linux",
        "x64",
        "mentorflow-e1",
        "turkey-ci",
    ]
    assert jobs["lane-contract"]["if"] == (
        f"github.ref != 'refs/heads/{REAL_GATE_BRANCH}'"
    )

    def depends_on_lane(job_name: str, seen: frozenset[str] = frozenset()) -> bool:
        if job_name == "lane-contract":
            return True
        assert job_name not in seen
        raw_needs = jobs[job_name].get("needs", [])
        needs = [raw_needs] if isinstance(raw_needs, str) else list(raw_needs)
        return any(depends_on_lane(name, seen | {job_name}) for name in needs)

    for job_name, contract in jobs.items():
        if job_name in {"lane-contract", "postgres17-rls-worker-real-gate"}:
            continue
        assert depends_on_lane(job_name), f"{job_name} bypasses the lane fence"
        expression = str(contract.get("if", ""))
        if "always()" in expression:
            assert REAL_GATE_BRANCH not in expression
    assert "if: always()" in job
    assert LOCKED_POSTGRES_IMAGE in compose
    assert runner_environment["E1_POSTGRES_PASSWORD"] == (
        "${E1_POSTGRES_PASSWORD:?E1_POSTGRES_PASSWORD is required}"
    )
    assert compose.count("\n    volumes:\n") == 2
    assert "ports:" not in compose
    assert "postgres migrate rls-gate pool-gate" not in runner
    assert 'docker compose -p "$compose_project" down --volumes --remove-orphans' in (
        workflow + runner
    )
    assert "docker system prune" not in workflow + runner


def test_real_postgres17_worker_process_consumes_durable_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker_database = _database_name(_required_environment("E1_WORKER_DATABASE"))
    expected_version = _required_environment("E1_EXPECTED_POSTGRES_VERSION")
    app_password = _password("E1_APP_PASSWORD")
    owner_password = _password("E1_POSTGRES_PASSWORD")
    admin_url = _postgres_url("E1_POSTGRES_ADMIN_DSN", database="postgres")
    migration_url = _postgres_url(
        "E1_WORKER_MIGRATION_DSN",
        database=worker_database,
    )
    runtime_url = _postgres_url(
        "E1_WORKER_RUNTIME_DSN",
        database=worker_database,
    )
    if admin_url.username != "postgres" or migration_url.username != "postgres":
        pytest.fail("real gate owner URLs must use the disposable postgres owner")
    if admin_url.password != owner_password or migration_url.password != owner_password:
        pytest.fail("real gate owner URLs must use the exact disposable credential")
    if runtime_url.username != APPLICATION_DATABASE_ROLE:
        pytest.fail("worker runtime URL must use the non-owner application role")
    if runtime_url.password != app_password:
        pytest.fail("worker runtime URL must use the exact disposable credential")

    admin_engine = sa.create_engine(
        admin_url,
        future=True,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    migration_engine: sa.Engine | None = None
    runtime_engine: sa.Engine | None = None
    worker_process: subprocess.Popen[str] | None = None
    worker_return_code: int | None = None
    worker_stdout = ""
    worker_stderr = ""
    try:
        with admin_engine.connect() as connection:
            existing = connection.scalar(
                sa.text("SELECT count(*) FROM pg_database WHERE datname = :database"),
                {"database": worker_database},
            )
            assert int(existing or 0) == 0
            connection.exec_driver_sql(
                f'CREATE DATABASE "{worker_database}" TEMPLATE template0'
            )
            connection.exec_driver_sql(
                f"""
                DO $role$
                BEGIN
                  IF NOT EXISTS (
                    SELECT 1 FROM pg_roles
                    WHERE rolname = '{APPLICATION_DATABASE_ROLE}'
                  ) THEN
                    CREATE ROLE {APPLICATION_DATABASE_ROLE};
                  END IF;
                END
                $role$
                """
            )
            connection.exec_driver_sql(
                f"ALTER ROLE {APPLICATION_DATABASE_ROLE} WITH LOGIN "
                f"PASSWORD '{app_password}' NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            )

        migration_engine = sa.create_engine(
            migration_url,
            future=True,
            pool_pre_ping=True,
        )
        with migration_engine.connect() as connection:
            assert sa.inspect(connection).get_table_names(schema="public") == []

        config = _alembic_config()
        assert ScriptDirectory.from_config(config).get_heads() == [
            ENTERPRISE_MIGRATION_REVISION
        ]
        with monkeypatch.context() as environment:
            environment.setenv("MENTORFLOW_DEPLOYMENT_MODE", "ENTERPRISE_SERVER")
            environment.setenv(
                "MENTORFLOW_DATABASE_URL",
                runtime_url.render_as_string(hide_password=False),
            )
            environment.setenv(
                "MENTORFLOW_MIGRATION_DATABASE_URL",
                migration_url.render_as_string(hide_password=False),
            )
            command.upgrade(config, "head")

        with migration_engine.begin() as connection:
            assert connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            ) == ENTERPRISE_MIGRATION_REVISION
            postgres_version = str(
                connection.scalar(sa.text("SHOW server_version"))
            )
            assert postgres_version.split()[0] == expected_version
            seed = seed_synthetic_tenants(connection)
            assert seed.organization_count == 3
            connection.execute(
                sa.text("CREATE SEQUENCE public.mentorflow_unrelated_sequence")
            )

        apply_worker_schema(
            migration_url,
            engine_options={"pool_pre_ping": True},
        )
        with migration_engine.begin() as connection:
            for table_name in PROCRASTINATE_RETIRED_TABLES:
                assert connection.scalar(
                    sa.text("SELECT to_regclass(:object_name)"),
                    {"object_name": f"public.{table_name}"},
                ) is None
        runtime_engine = create_enterprise_postgres_engine(
            runtime_url.render_as_string(hide_password=False),
            pool_size=2,
            max_overflow=0,
        )
        verify_worker_database(runtime_engine)
        with runtime_engine.connect() as connection:
            with connection.begin():
                security = inspect_application_role_security(connection)
                assert security.current_user == APPLICATION_DATABASE_ROLE
                assert security.is_superuser is False
                assert security.bypasses_rls is False
                assert security.owned_tenant_table_count == 0
                assert security.forced_rls_table_count == len(FORCE_RLS_TABLES)
                for table_name in PROCRASTINATE_REQUIRED_TABLES:
                    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                        assert connection.scalar(
                            sa.text(
                                "SELECT has_table_privilege("
                                "current_user, :object_name, :privilege)"
                            ),
                            {
                                "object_name": f"public.{table_name}",
                                "privilege": privilege,
                            },
                        ) is True
                for sequence_name in PROCRASTINATE_REQUIRED_SEQUENCES:
                    for privilege in ("USAGE", "SELECT"):
                        assert connection.scalar(
                            sa.text(
                                "SELECT has_sequence_privilege("
                                "current_user, :object_name, :privilege)"
                            ),
                            {
                                "object_name": f"public.{sequence_name}",
                                "privilege": privilege,
                            },
                        ) is True
                for privilege in ("USAGE", "SELECT"):
                    assert connection.scalar(
                        sa.text(
                            "SELECT has_sequence_privilege("
                            "current_user, 'public.mentorflow_unrelated_sequence', "
                            ":privilege)"
                        ),
                        {"privilege": privilege},
                    ) is False
                for type_name in PROCRASTINATE_REQUIRED_TYPES:
                    assert connection.scalar(
                        sa.text(
                            "SELECT has_type_privilege("
                            "current_user, :object_name, 'USAGE')"
                        ),
                        {"object_name": f"public.{type_name}"},
                    ) is True
                for function_name in PROCRASTINATE_REQUIRED_FUNCTIONS:
                    assert connection.scalar(
                        sa.text(
                            "SELECT has_function_privilege("
                            "current_user, to_regprocedure(:object_name), 'EXECUTE')"
                        ),
                        {"object_name": f"public.{function_name}"},
                    ) is True
                for function_name in PROCRASTINATE_OPTIONAL_FUNCTIONS:
                    object_name = f"public.{function_name}"
                    function_oid = connection.scalar(
                        sa.text("SELECT to_regprocedure(:object_name)"),
                        {"object_name": object_name},
                    )
                    if function_oid is not None:
                        assert connection.scalar(
                            sa.text(
                                "SELECT has_function_privilege("
                                "current_user, :function_oid, 'EXECUTE')"
                            ),
                            {"function_oid": function_oid},
                        ) is True

        tenant = TenantContext(
            mode=TenantMode.ENTERPRISE_SERVER,
            organization_id="synthetic-org-1",
            workspace_id="synthetic-org-1-workspace-1",
        )
        subject = "synthetic-user-01"
        request_id = "e102-real-worker-request"
        actor = ActorContext(
            subject_id=subject,
            actor_kind=ActorKind.HUMAN,
            issuer=ISSUER,
            token_id="e102-real-worker-token",
            session_id="e102-real-worker-session",
            request_id=request_id,
            authentication_time=datetime.now(timezone.utc),
            authentication_strength="integration-test",
            organization_id=tenant.organization_id,
            workspace_id=tenant.workspace_id,
            membership_id=_membership_id(ISSUER, subject, tenant.organization_id or ""),
            roles=("OWNER", "ADMIN"),
        )
        repository = WorkflowRepository()

        def job_request(idempotency_key: str) -> JobRequest:
            return JobRequest(
                workflow_kind=WorkflowKind.DOCUMENT_INGESTION,
                payload={"gate": "postgres17-real-worker"},
                actor_context=actor,
                tenant_context=tenant,
                request_id=request_id,
                trace_id="e102-real-worker-trace",
                idempotency_key=idempotency_key,
            )

        def repository_totals(connection: sa.Connection) -> tuple[int, int]:
            job_count = int(
                connection.scalar(sa.select(sa.func.count()).select_from(jobs_table))
                or 0
            )
            outbox_count = int(
                connection.scalar(sa.select(sa.func.count()).select_from(outbox_table))
                or 0
            )
            return job_count, outbox_count

        with runtime_engine.begin() as connection:
            receipt = repository.submit(
                connection,
                job_request("e102-real-worker-idempotency"),
            )
            assert receipt.status is JobStatus.PENDING
            assert receipt.duplicate is False
            assert repository_totals(connection) == (1, 1)

        with runtime_engine.begin() as connection:
            duplicate_receipt = repository.submit(
                connection,
                job_request("e102-real-worker-idempotency"),
            )
            assert duplicate_receipt.job_id == receipt.job_id
            assert duplicate_receipt.duplicate is True
            assert repository_totals(connection) == (1, 1)

        with runtime_engine.begin() as connection:
            distinct_receipt = repository.submit(
                connection,
                job_request("e102-real-worker-distinct-idempotency"),
            )
            assert distinct_receipt.job_id != receipt.job_id
            assert distinct_receipt.duplicate is False
            assert repository_totals(connection) == (2, 2)

        concurrent_key = "e102-real-worker-concurrent-idempotency"
        concurrent_barrier = Barrier(2)

        def submit_concurrently():
            with runtime_engine.begin() as connection:
                concurrent_barrier.wait(timeout=10)
                return repository.submit(connection, job_request(concurrent_key))

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(submit_concurrently) for _ in range(2)]
            concurrent_receipts = [future.result(timeout=30) for future in futures]

        assert len({item.job_id for item in concurrent_receipts}) == 1
        assert sorted(item.duplicate for item in concurrent_receipts) == [False, True]
        with runtime_engine.begin() as connection:
            assert repository_totals(connection) == (3, 3)
            concurrent_job_count = int(
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(jobs_table)
                    .where(jobs_table.c.idempotency_key == concurrent_key)
                )
                or 0
            )
            concurrent_outbox_count = int(
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(
                        outbox_table.join(
                            jobs_table,
                            outbox_table.c.job_id == jobs_table.c.job_id,
                        )
                    )
                    .where(jobs_table.c.idempotency_key == concurrent_key)
                )
                or 0
            )
        assert concurrent_job_count == 1
        assert concurrent_outbox_count == 1

        handler_module = tmp_path / "mentorflow_e102_real_handler.py"
        handler_module.write_text(
            """def execute(*, engine, job, checkpoint, ensure_not_cancelled):
    ensure_not_cancelled()
    checkpoint(
        \"real-postgres-worker\",
        {\"database\": engine.dialect.name, \"job_id\": job.job_id},
    )
    return {\"handled_by\": \"real-postgres-worker\", \"job_id\": job.job_id}
""",
            encoding="utf-8",
        )
        readiness_path = tmp_path / "worker-readiness.json"
        worker_environment = dict(os.environ)
        worker_environment.update(
            {
                "MENTORFLOW_DEPLOYMENT_MODE": "ENTERPRISE_SERVER",
                "MENTORFLOW_DATABASE_URL": runtime_url.render_as_string(
                    hide_password=False
                ),
                "MENTORFLOW_MIGRATION_DATABASE_URL": migration_url.render_as_string(
                    hide_password=False
                ),
                "MENTORFLOW_WORKER_RUNTIME_FACTORY": PRODUCTION_FACTORY_DOTTED_PATH,
                "MENTORFLOW_KEYCLOAK_ISSUER": ISSUER,
                "MENTORFLOW_WORKER_NAME": "e102-postgres17-real-worker",
                "MENTORFLOW_WORKER_CONCURRENCY": "1",
                "MENTORFLOW_WORKER_STALLED_TIMEOUT_SECONDS": "10",
                "MENTORFLOW_WORKER_JOB_LEASE_SECONDS": "15",
                "MENTORFLOW_WORKER_MAINTENANCE_INTERVAL_SECONDS": "0.2",
                "MENTORFLOW_WORKER_SHUTDOWN_TIMEOUT_SECONDS": "10",
                "MENTORFLOW_WORKER_READINESS_PATH": str(readiness_path),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": os.pathsep.join((str(tmp_path), str(BACKEND_ROOT))),
            }
        )
        for kind in WorkflowKind:
            worker_environment[handler_environment_name(kind)] = (
                "mentorflow_e102_real_handler:execute"
            )

        worker_process = subprocess.Popen(
            [sys.executable, "-B", "-m", "app.enterprise.worker_entry", "run"],
            cwd=REPO_ROOT,
            env=worker_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        current = None
        readiness = None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            with runtime_engine.begin() as connection:
                current = repository.get(connection, receipt.job_id)
            if current is not None and current.status is JobStatus.SUCCEEDED:
                if readiness_path.is_file():
                    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
                break
            if worker_process.poll() is not None:
                break
            time.sleep(0.25)

        worker_return_code, worker_stdout, worker_stderr = _stop_worker(worker_process)
        if current is None or current.status is not JobStatus.SUCCEEDED:
            pytest.fail(
                "real Worker did not complete the durable PostgreSQL job; "
                f"return_code={worker_return_code}; "
                f"stdout={_redacted_tail(worker_stdout, app_password, owner_password)}; "
                f"stderr={_redacted_tail(worker_stderr, app_password, owner_password)}",
                pytrace=False,
            )
        assert current.result == {
            "handled_by": "real-postgres-worker",
            "job_id": receipt.job_id,
        }
        assert current.checkpoint["real-postgres-worker"] == {
            "database": "postgresql",
            "job_id": receipt.job_id,
        }
        assert readiness is not None and readiness["status"] == "READY"
        assert worker_process.poll() is not None

        with runtime_engine.begin() as connection:
            outbox = repository.get_outbox_for_job(connection, receipt.job_id)
            queue_rows = int(
                connection.scalar(sa.text("SELECT count(*) FROM procrastinate_jobs"))
                or 0
            )
        assert [event.status for event in outbox] == [OutboxStatus.DISPATCHED]
        assert queue_rows >= 1
        print(
            json.dumps(
                {
                    "alembic_head": ENTERPRISE_MIGRATION_REVISION,
                    "app_role_bypassrls": False,
                    "app_role_superuser": False,
                    "empty_database_migrated": True,
                    "postgres_version": postgres_version,
                    "procrastinate_jobs": queue_rows,
                    "procrastinate_version_required": False,
                    "repository_concurrent_fresh_inserts": sum(
                        not item.duplicate for item in concurrent_receipts
                    ),
                    "repository_first_insert_duplicate": receipt.duplicate,
                    "repository_jobs": 3,
                    "repository_outbox": 3,
                    "repository_same_key_duplicate": duplicate_receipt.duplicate,
                    "required_objects_missing": [],
                    "optional_legacy_objects_skipped": list(
                        PROCRASTINATE_RETIRED_TABLES
                    ),
                    "real_worker_executed": True,
                    "worker_job_status": current.status.value,
                    "worker_process_reaped": True,
                    "worker_readiness": readiness["status"],
                    "worker_return_code": worker_return_code,
                },
                sort_keys=True,
            )
        )
    finally:
        if worker_process is not None and worker_process.poll() is None:
            _stop_worker(worker_process)
        if runtime_engine is not None:
            runtime_engine.dispose()
        if migration_engine is not None:
            migration_engine.dispose()
        admin_engine.dispose()
