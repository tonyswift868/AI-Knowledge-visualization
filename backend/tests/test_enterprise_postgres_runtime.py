from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy.engine import make_url

from app import orm
from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    TenantContext,
    TenantMode,
)
from app.enterprise_tenancy.context import (
    ApplicationRoleSecurity,
    TenantTransactionContext,
)
from app.enterprise_tenancy.models import TenantContextRequiredError
from app.enterprise_tenancy.schema import (
    ENTERPRISE_MIGRATION_REVISION,
    FORCE_RLS_TABLES,
    postgres_rls_statements,
)
from app.enterprise_tenancy.synthetic import build_synthetic_tenant_seed


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
RUNTIME_URL = (
    "postgresql+psycopg://mentorflow_app:runtime-secret@postgres:5432/mentorflow"
)
MIGRATION_URL = (
    "postgresql+psycopg://mentorflow_migration:migration-secret@postgres:5432/mentorflow"
)


def _enterprise_env(**overrides: str) -> dict[str, str]:
    values = {
        orm.DEPLOYMENT_MODE_ENV: TenantMode.ENTERPRISE_SERVER.value,
        orm.RUNTIME_DATABASE_URL_ENV: RUNTIME_URL,
        orm.MIGRATION_DATABASE_URL_ENV: MIGRATION_URL,
    }
    values.update(overrides)
    return values


def _alembic_config(database: Path) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    config.attributes["configure_logger"] = False
    return config


def test_psycopg_dependency_and_lock_are_exact_and_consistent() -> None:
    requirements = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")
    lock = (BACKEND_ROOT / "requirements.lock.txt").read_text(encoding="utf-8")
    assert "psycopg[binary]==3.3.4" in requirements.splitlines()
    assert "psycopg==3.3.4" in lock.splitlines()
    assert "psycopg-binary==3.3.4" in lock.splitlines()

    import psycopg
    from psycopg import pq

    assert psycopg.__version__ == "3.3.4"
    assert pq.__impl__ == "binary"


def test_personal_local_defaults_to_sqlite_and_accepts_explicit_sqlite(
    tmp_path: Path,
) -> None:
    default_url = orm.resolve_runtime_database_url(
        {},
        personal_database_path=tmp_path / "personal.db",
    )
    assert default_url.drivername == "sqlite"
    assert default_url.database == (tmp_path / "personal.db").as_posix()

    explicit_url = orm.resolve_runtime_database_url(
        {orm.RUNTIME_DATABASE_URL_ENV: "sqlite+pysqlite:///:memory:"}
    )
    assert explicit_url.drivername == "sqlite+pysqlite"

    with pytest.raises(
        orm.DatabaseRuntimeConfigurationError,
        match="must use SQLite",
    ):
        orm.resolve_runtime_database_url(
            {orm.RUNTIME_DATABASE_URL_ENV: RUNTIME_URL}
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({orm.RUNTIME_DATABASE_URL_ENV: ""}, "is required"),
        (
            {orm.RUNTIME_DATABASE_URL_ENV: "sqlite:///enterprise.db"},
            "postgresql\\+psycopg",
        ),
        (
            {
                orm.RUNTIME_DATABASE_URL_ENV: (
                    "postgresql://mentorflow_app:secret@postgres/mentorflow"
                )
            },
            "postgresql\\+psycopg",
        ),
        (
            {
                orm.RUNTIME_DATABASE_URL_ENV: (
                    "postgresql+psycopg://postgres:secret@postgres/mentorflow"
                )
            },
            "non-owner mentorflow_app",
        ),
    ],
)
def test_enterprise_runtime_database_configuration_fails_closed(
    overrides: dict[str, str],
    message: str,
) -> None:
    environment = _enterprise_env(**overrides)
    with pytest.raises(orm.DatabaseRuntimeConfigurationError, match=message):
        orm.resolve_runtime_database_url(environment)


def test_enterprise_runtime_url_requires_application_role_without_leaking_secret() -> None:
    environment = _enterprise_env()
    resolved = orm.resolve_runtime_database_url(environment)
    assert resolved.drivername == "postgresql+psycopg"
    assert resolved.username == "mentorflow_app"
    assert resolved.password == "runtime-secret"
    assert "runtime-secret" not in str(resolved)

    secret = "must-not-appear"
    invalid = _enterprise_env(
        **{
            orm.RUNTIME_DATABASE_URL_ENV: (
                f"postgresql+psycopg://postgres:{secret}@postgres/mentorflow"
            )
        }
    )
    with pytest.raises(orm.DatabaseRuntimeConfigurationError) as error:
        orm.resolve_runtime_database_url(invalid)
    assert secret not in str(error.value)


def test_migration_url_requires_distinct_postgresql_owner() -> None:
    migration = orm.resolve_migration_database_url(
        "sqlite:///ignored.db",
        _enterprise_env(),
    )
    assert migration.drivername == "postgresql+psycopg"
    assert migration.username == "mentorflow_migration"

    for value in (
        "",
        "sqlite:///migration.db",
        "postgresql+psycopg://mentorflow_app:secret@postgres/mentorflow",
    ):
        with pytest.raises(orm.DatabaseRuntimeConfigurationError):
            orm.resolve_migration_database_url(
                "sqlite:///ignored.db",
                _enterprise_env(**{orm.MIGRATION_DATABASE_URL_ENV: value}),
            )


def test_postgresql_engine_installs_fail_closed_pool_context_safety() -> None:
    engine = orm._create_engine_from_url(make_url(RUNTIME_URL))
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
        assert getattr(engine, "_mentorflow_tenant_pool_safety", False) is True
        assert str(engine.url).find("runtime-secret") == -1
    finally:
        engine.dispose()


def test_configure_orm_cannot_downgrade_enterprise_to_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as test_state:
        test_state.setenv(
            orm.DEPLOYMENT_MODE_ENV,
            TenantMode.ENTERPRISE_SERVER.value,
        )
        with pytest.raises(
            orm.DatabaseRuntimeConfigurationError,
            match="cannot switch",
        ):
            orm.configure_orm(tmp_path / "forbidden.db")
    assert not (tmp_path / "forbidden.db").exists()


def test_enterprise_startup_verifies_head_without_running_migrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    with monkeypatch.context() as test_state:
        test_state.setenv(
            orm.DEPLOYMENT_MODE_ENV,
            TenantMode.ENTERPRISE_SERVER.value,
        )
        test_state.setattr(
            orm,
            "configure_enterprise_orm",
            lambda: calls.append("configure"),
        )
        test_state.setattr(
            orm,
            "verify_enterprise_runtime_schema",
            lambda: calls.append("verify"),
        )
        orm.upgrade_orm_schema()
        assert calls == ["configure", "verify"]
        with pytest.raises(
            orm.DatabaseRuntimeConfigurationError,
            match="canonical head",
        ):
            orm.upgrade_orm_schema("0030_enterprise_jobs_v1")


def test_enterprise_runtime_schema_check_requires_role_and_exact_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.enterprise_tenancy import context as context_module
    from app.enterprise_tenancy import schema as schema_module

    connection = object()
    role_checks: list[object] = []

    class ConnectionContext:
        def __enter__(self) -> object:
            return connection

        def __exit__(self, *_args: object) -> None:
            return None

    class Engine:
        def connect(self) -> ConnectionContext:
            return ConnectionContext()

    with monkeypatch.context() as test_state:
        test_state.setattr(orm, "engine", Engine())
        test_state.setattr(
            context_module,
            "require_safe_application_role",
            lambda item: role_checks.append(item),
        )
        test_state.setattr(
            schema_module,
            "detect_enterprise_schema_version",
            lambda _connection: ENTERPRISE_MIGRATION_REVISION,
        )
        from app.enterprise_tenancy import import_ledger_schema, sync_schema
        test_state.setattr(import_ledger_schema, "require_account_import_ledger_schema", lambda _connection: None)
        test_state.setattr(sync_schema, "require_structured_sync_schema", lambda _connection: None)
        orm.verify_enterprise_runtime_schema()
        assert role_checks == [connection]

        test_state.setattr(
            schema_module,
            "detect_enterprise_schema_version",
            lambda _connection: "0030_enterprise_jobs_v1",
        )
        with pytest.raises(
            orm.DatabaseRuntimeConfigurationError,
            match="canonical migration head",
        ):
            orm.verify_enterprise_runtime_schema()


def test_application_role_privilege_expectations_are_fail_closed() -> None:
    safe = ApplicationRoleSecurity(
        current_user="mentorflow_app",
        session_user="mentorflow_app",
        is_superuser=False,
        bypasses_rls=False,
        owned_tenant_table_count=0,
        forced_rls_table_count=len(FORCE_RLS_TABLES),
    )
    assert safe.safe is True
    assert ApplicationRoleSecurity(
        current_user="mentorflow_app",
        session_user="mentorflow_app",
        is_superuser=True,
        bypasses_rls=False,
        owned_tenant_table_count=0,
        forced_rls_table_count=len(FORCE_RLS_TABLES),
    ).safe is False
    assert ApplicationRoleSecurity(
        current_user="mentorflow_app",
        session_user="mentorflow_app",
        is_superuser=False,
        bypasses_rls=True,
        owned_tenant_table_count=0,
        forced_rls_table_count=len(FORCE_RLS_TABLES),
    ).safe is False
    assert ApplicationRoleSecurity(
        current_user="mentorflow_app",
        session_user="mentorflow_app",
        is_superuser=False,
        bypasses_rls=False,
        owned_tenant_table_count=1,
        forced_rls_table_count=len(FORCE_RLS_TABLES),
    ).safe is False


def test_current_migration_graph_and_rls_contract_are_canonical() -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [ENTERPRISE_MIGRATION_REVISION]
    revision = scripts.get_revision(ENTERPRISE_MIGRATION_REVISION)
    assert revision is not None
    assert revision.down_revision == "0039_generic_sync_kinds"
    jobs = scripts.get_revision("0030_enterprise_jobs_v1")
    assert jobs is not None
    assert jobs.down_revision == "0029_enterprise_tenancy_rls"

    statements = postgres_rls_statements()
    assert len(statements) == len(FORCE_RLS_TABLES) * 4
    assert all(
        f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in statements
        and f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in statements
        for table in FORCE_RLS_TABLES
    )

    migration_source = (
        BACKEND_ROOT / "alembic" / "versions" / "0031_enterprise_tenancy_completion.py"
    ).read_text(encoding="utf-8")
    assert "mentorflow_app must be NOSUPERUSER and NOBYPASSRLS" in migration_source
    assert "mentorflow_app must own zero tenant tables" in migration_source


def test_tenant_context_and_job_actor_cannot_cross_workspace() -> None:
    tenant = TenantContext(
        organization_id="organization-a",
        workspace_id="workspace-a",
        mode=TenantMode.ENTERPRISE_SERVER,
    )
    actor = ActorContext(
        subject_id="worker-a",
        actor_kind=ActorKind.SERVICE,
        issuer="offline-runtime-test",
        token_id="token-a",
        session_id="session-a",
        request_id="request-a",
        authentication_time=datetime(2026, 8, 28, tzinfo=timezone.utc),
        authentication_strength="service",
        organization_id="organization-a",
        workspace_id="workspace-b",
        membership_id="membership-a",
        service_permissions=("jobs.execute",),
    )
    with pytest.raises(TenantContextRequiredError, match="must match"):
        TenantTransactionContext(tenant=tenant, actor=actor)


def test_synthetic_seam_keeps_three_organizations_and_twenty_users() -> None:
    seed = build_synthetic_tenant_seed()
    assert seed.organization_count == 3
    assert seed.workspace_count == 6
    assert seed.user_count == 20
    assert seed.resource_count == 48


def test_personal_local_fresh_migration_still_reaches_single_head(
    tmp_path: Path,
) -> None:
    database = tmp_path / "personal-local.db"
    config = _alembic_config(database)
    command.upgrade(config, "head")
    with database.open("rb") as stream:
        assert stream.read(16).startswith(b"SQLite format 3")


def test_alembic_environment_uses_dedicated_owner_contract() -> None:
    source = (BACKEND_ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "resolve_migration_database_url" in source
    assert "mentorflow_app cannot be the Alembic migration owner" in source
    assert "GRANT SELECT ON TABLE alembic_version" in source
    assert "engine_from_config" not in source

    changed_paths = {
        path.as_posix()
        for path in REPO_ROOT.glob("backend/alembic/versions/*.py")
        if path.name.startswith(("0029_", "0030_", "0031_"))
    }
    assert len(changed_paths) == 3
