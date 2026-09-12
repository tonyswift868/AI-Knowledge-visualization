from __future__ import annotations

import ast
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
from types import ModuleType, SimpleNamespace
from typing import Any, cast

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, make_url
import yaml

import app.enterprise_tenancy as tenancy
from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    ResourceClassification,
    ResourceScopeType,
    TenantContext,
    TenantMode,
    verify_frozen_contract_hashes,
)
from app.enterprise_tenancy.context import (
    PostgresTenantTransactionManager,
    TenantConnection,
    TenantDbSession,
    TenantTransactionContext,
    _dbapi_context_values,
    _reset_dbapi_context,
    create_enterprise_postgres_engine,
    inspect_application_role_security,
    require_safe_application_role,
)
from app.enterprise_tenancy.import_seam import LocalToEnterpriseImportService
from app.enterprise_tenancy.isolation_corpus import (
    IsolationOperation,
    build_tenant_isolation_corpus,
)
from app.enterprise_tenancy.models import (
    ImportConfirmationError,
    LocalImportDescriptor,
    TenantContextRequiredError,
    TenantResourceDraft,
    TenantResourceKind,
)
from app.enterprise_tenancy.repository import (
    DualPersistenceSelector,
    PostgresTenantResourceRepository,
    TenantResourceRepositoryFactory,
)
from app.enterprise_tenancy.schema import (
    APPLICATION_DATABASE_ROLE,
    ENTERPRISE_MIGRATION_REVISION,
    FORCE_RLS_TABLES,
    detect_enterprise_schema_version,
    postgres_rls_statements,
    rls_predicate_for,
)
from app.enterprise_tenancy.synthetic import (
    build_synthetic_tenant_seed,
    seed_synthetic_tenants,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_VERSIONS_ROOT = BACKEND_ROOT / "alembic" / "versions"
TENANCY_ROOT = BACKEND_ROOT / "app" / "enterprise_tenancy"
JOBS_MIGRATION_REVISION = "0030_enterprise_jobs_v1"
COMPLETION_MIGRATION_REVISION = "0031_enterprise_tenancy_completion"
PROVIDER_MIGRATION_REVISION = "0032_enterprise_provider_sessions"
WORKSPACE_MIGRATION_REVISION = "0033_enterprise_workspace_persistence"
ASSET_MIGRATION_REVISION = "0034_enterprise_asset_manifests"
BOOTSTRAP_MIGRATION_REVISION = "0035_account_workspace_bootstrap"
MIGRATION_REVISION = "0036_account_import_ledger"
CANONICAL_TABLES = {
    "account_import_ledger",
    "organizations",
    "organization_memberships",
    "workspaces",
    "workspace_memberships",
    "workspace_role_bindings",
    "workspace_profiles",
    "workspace_goals",
    "resource_scopes",
    "documents",
    "evidence",
    "knowledge_maps",
    "learning_plans",
    "teacher_sessions",
    "review_campaigns",
    "student_events",
    "cost_events",
    "asset_manifests",
    "asset_revisions",
}


class _RoleScalarResult:
    def __init__(self, value: str) -> None:
        self._value = value

    def scalar_one(self) -> str:
        return self._value


class _RoleRowsResult:
    def __init__(self, rows: tuple[tuple[object, ...], ...]) -> None:
        self._rows = rows

    def all(self) -> tuple[tuple[object, ...], ...]:
        return self._rows


class _ProvisionedRoleBind:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(
        self,
        *,
        current_user: str = "mentorflow_migrator",
        roles: dict[str, tuple[bool, ...]] | None = None,
    ) -> None:
        self.current_user = current_user
        self.roles = roles or {
            "mentorflow_app": (False, False, False, False, False),
            "mentorflow_migrator": (False, False, False, False, False),
        }

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "SELECT current_user" in sql:
            return _RoleScalarResult(self.current_user)
        if "FROM pg_roles" in sql:
            requested = set((parameters or {}).get("roles") or ())
            return _RoleRowsResult(
                tuple(
                    (role, *flags)
                    for role, flags in self.roles.items()
                    if role in requested
                )
            )
        raise AssertionError(f"unexpected SQL: {sql}")


def _migration_0029() -> ModuleType:
    path = MIGRATION_VERSIONS_ROOT / "0029_enterprise_tenancy_rls.py"
    spec = importlib.util.spec_from_file_location(
        "enterprise_tenancy_migration_0029",
        path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0029_accepts_safely_provisioned_application_and_migration_roles() -> None:
    _migration_0029()._assert_provisioned_roles(_ProvisionedRoleBind())


@pytest.mark.parametrize("missing_role", ("mentorflow_app", "mentorflow_migrator"))
def test_0029_fails_closed_when_prerequisite_role_is_missing(
    missing_role: str,
) -> None:
    roles = {
        role: (False, False, False, False, False)
        for role in ("mentorflow_app", "mentorflow_migrator")
        if role != missing_role
    }

    with pytest.raises(
        RuntimeError,
        match=f"ENTERPRISE_ROLE_PROVISIONING_REQUIRED.*{missing_role}",
    ):
        _migration_0029()._assert_provisioned_roles(
            _ProvisionedRoleBind(roles=roles)
        )


@pytest.mark.parametrize("role", ("mentorflow_app", "mentorflow_migrator"))
@pytest.mark.parametrize(
    ("attribute_index", "attribute"),
    (
        (0, "rolsuper"),
        (1, "rolcreatedb"),
        (2, "rolcreaterole"),
        (3, "rolreplication"),
        (4, "rolbypassrls"),
    ),
)
def test_0029_fails_closed_for_unsafe_prerequisite_role_attribute(
    role: str,
    attribute_index: int,
    attribute: str,
) -> None:
    unsafe = [False, False, False, False, False]
    unsafe[attribute_index] = True
    roles = {
        "mentorflow_app": (False, False, False, False, False),
        "mentorflow_migrator": (False, False, False, False, False),
        role: tuple(unsafe),
    }

    with pytest.raises(
        RuntimeError,
        match=(
            "ENTERPRISE_ROLE_SECURITY_CONTRACT_VIOLATION.*"
            f"{role}.{attribute}"
        ),
    ):
        _migration_0029()._assert_provisioned_roles(
            _ProvisionedRoleBind(roles=roles)
        )


def test_0029_requires_dedicated_least_privilege_migration_role() -> None:
    with pytest.raises(
        RuntimeError,
        match=(
            "ENTERPRISE_ROLE_SECURITY_CONTRACT_VIOLATION.*"
            "migrations must run as mentorflow_migrator"
        ),
    ):
        _migration_0029()._assert_provisioned_roles(
            _ProvisionedRoleBind(current_user="postgres")
        )


def test_entire_migration_chain_contains_no_cluster_role_ddl() -> None:
    pattern = re.compile(r"\b(?:CREATE|DROP|ALTER)\s+ROLE\b", re.IGNORECASE)
    violations = {
        path.name: tuple(match.group(0).upper() for match in pattern.finditer(source))
        for path in sorted(MIGRATION_VERSIONS_ROOT.glob("*.py"))
        if (source := path.read_text(encoding="utf-8"))
        if pattern.search(source)
    }

    assert violations == {}


def _tenant(*, workspace_id: str | None = "workspace-a") -> TenantContext:
    return TenantContext(
        organization_id="organization-a",
        workspace_id=workspace_id,
        mode=TenantMode.ENTERPRISE_SERVER,
    )


def _actor(
    *,
    organization_id: str | None = "organization-a",
    workspace_id: str | None = "workspace-a",
) -> ActorContext:
    return ActorContext(
        subject_id="subject-a",
        actor_kind=ActorKind.HUMAN,
        issuer="offline-test",
        token_id="token-a",
        session_id="session-a",
        request_id="request-a",
        authentication_time=datetime(2026, 8, 26, tzinfo=timezone.utc),
        authentication_strength="test",
        organization_id=organization_id,
        workspace_id=workspace_id,
        membership_id="membership-a",
        roles=("MEMBER",),
    )


def _alembic_config(database: Path) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    config.attributes["configure_logger"] = False
    return config


def _tables(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }


def _version(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        return str(
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        )


def _real_rls_context(
    organization_id: str,
    workspace_id: str,
    subject_id: str,
) -> TenantTransactionContext:
    tenant = TenantContext(
        organization_id=organization_id,
        workspace_id=workspace_id,
        mode=TenantMode.ENTERPRISE_SERVER,
    )
    actor = ActorContext(
        subject_id=subject_id,
        actor_kind=ActorKind.HUMAN,
        issuer="real-postgres-rls-test",
        token_id=f"token:{organization_id}:{subject_id}",
        session_id=f"session:{organization_id}:{subject_id}",
        request_id=f"request:{organization_id}:{subject_id}",
        authentication_time=datetime(2026, 8, 30, tzinfo=timezone.utc),
        authentication_strength="integration-test",
        organization_id=organization_id,
        workspace_id=workspace_id,
        membership_id=f"membership:{organization_id}:{subject_id}",
        roles=("MEMBER",),
    )
    return TenantTransactionContext(tenant=tenant, actor=actor)


@pytest.mark.rls
def test_real_postgres_rls_is_fail_closed_and_pool_safe(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    raw_dsn = os.environ.get("MENTORFLOW_RLS_DSN", "").strip()
    if not raw_dsn:
        marker_expression = str(request.config.getoption("markexpr") or "")
        dedicated_gate = re.search(
            r"(?<![A-Za-z0-9_])rls(?![A-Za-z0-9_])",
            marker_expression,
        )
        if dedicated_gate:
            pytest.fail("MENTORFLOW_RLS_DSN is required for the real PostgreSQL RLS gate")
        pytest.skip(
            "real PostgreSQL RLS runs only in the dedicated -m rls DSN-aware gate"
        )
    try:
        migration_url = make_url(raw_dsn)
    except Exception:
        pytest.fail("MENTORFLOW_RLS_DSN must be a valid SQLAlchemy URL", pytrace=False)
    if migration_url.get_backend_name() != "postgresql":
        pytest.fail("MENTORFLOW_RLS_DSN must target PostgreSQL", pytrace=False)
    migration_url = migration_url.set(drivername="postgresql+psycopg")
    if migration_url.username == APPLICATION_DATABASE_ROLE:
        pytest.fail("MENTORFLOW_RLS_DSN must use the distinct migration owner", pytrace=False)

    migration_engine = create_engine(migration_url, future=True, pool_pre_ping=True)
    tenant_engine = None
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    f"""
                    DO $role$
                    BEGIN
                      IF NOT EXISTS (
                        SELECT 1 FROM pg_roles
                        WHERE rolname = '{APPLICATION_DATABASE_ROLE}'
                      ) THEN
                        CREATE ROLE {APPLICATION_DATABASE_ROLE}
                          NOLOGIN NOSUPERUSER NOBYPASSRLS;
                      END IF;
                    END
                    $role$
                    """
                )
            )
            connection.execute(
                text(
                    f"ALTER ROLE {APPLICATION_DATABASE_ROLE} "
                    "NOLOGIN NOSUPERUSER NOBYPASSRLS"
                )
            )

        runtime_url = migration_url.set(
            username=APPLICATION_DATABASE_ROLE,
            password="role-switch-only",
        )
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
        config.attributes["configure_logger"] = False
        assert ScriptDirectory.from_config(config).get_heads() == [MIGRATION_REVISION]
        with migration_engine.connect() as connection:
            assert "alembic_version" not in inspect(connection).get_table_names()
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
            assert detect_enterprise_schema_version(connection) == MIGRATION_REVISION
            postgres_version = str(connection.execute(text("SHOW server_version")).scalar_one())
            assert postgres_version.split()[0] == os.environ.get(
                "E1_EXPECTED_POSTGRES_VERSION",
                "17.6",
            )
            seed = seed_synthetic_tenants(connection)
            assert seed.organization_count == 3
            rls_rows = connection.execute(
                text(
                    """
                    SELECT class.relname, class.relrowsecurity, class.relforcerowsecurity
                    FROM pg_class AS class
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = class.relnamespace
                    WHERE namespace.nspname = current_schema()
                      AND class.relname = ANY(:tables)
                    """
                ),
                {"tables": list(FORCE_RLS_TABLES)},
            ).all()
            assert {str(row[0]) for row in rls_rows} == set(FORCE_RLS_TABLES)
            assert all(bool(row[1]) and bool(row[2]) for row in rls_rows)

        tenant_engine = create_enterprise_postgres_engine(
            migration_url.render_as_string(hide_password=False),
            pool_size=1,
            max_overflow=0,
        )
        manager = PostgresTenantTransactionManager(tenant_engine)
        repository = PostgresTenantResourceRepository()
        connection_ids: list[int] = []

        with tenant_engine.connect() as connection:
            with connection.begin():
                connection.execute(text(f"SET LOCAL ROLE {APPLICATION_DATABASE_ROLE}"))
                security = inspect_application_role_security(connection)
                assert security.safe is True
                assert security.current_user == APPLICATION_DATABASE_ROLE
                assert security.is_superuser is False
                assert security.bypasses_rls is False
                assert security.owned_tenant_table_count == 0
                assert security.forced_rls_table_count == len(FORCE_RLS_TABLES)
                assert int(
                    connection.execute(text("SELECT count(*) FROM documents")).scalar_one()
                ) == 0

        tenant_a = _real_rls_context(
            "synthetic-org-1",
            "synthetic-org-1-workspace-1",
            "synthetic-user-01",
        )
        tenant_b = _real_rls_context(
            "synthetic-org-2",
            "synthetic-org-2-workspace-1",
            "synthetic-user-08",
        )
        resource_id = "real-rls-contract-document-a"
        with manager.transaction(tenant_a) as session:
            session.connection.execute(text(f"SET LOCAL ROLE {APPLICATION_DATABASE_ROLE}"))
            require_safe_application_role(session.connection)
            connection_ids.append(id(session.connection.connection.driver_connection))
            own = repository.upsert(
                session,
                TenantResourceDraft(
                    resource_id=resource_id,
                    kind=TenantResourceKind.DOCUMENT,
                    scope_type=ResourceScopeType.WORKSPACE,
                    owner_subject_id=tenant_a.actor.subject_id,
                    classification=ResourceClassification.INTERNAL,
                    title="Real RLS tenant A document",
                    status="ready",
                    payload={"tenant": "A", "version": 1},
                    search_text="real postgres rls tenant a",
                    job_id="rls-job-a",
                    cache_key="rls-cache-a",
                ),
            )
            assert own.organization_id == tenant_a.tenant.organization_id
            assert repository.get(session, TenantResourceKind.DOCUMENT, resource_id) is not None

        with manager.transaction(tenant_b) as session:
            session.connection.execute(text(f"SET LOCAL ROLE {APPLICATION_DATABASE_ROLE}"))
            require_safe_application_role(session.connection)
            connection_ids.append(id(session.connection.connection.driver_connection))
            unauthorized_read_count = int(
                repository.get(session, TenantResourceKind.DOCUMENT, resource_id)
                is not None
            )
            unauthorized_read_count += sum(
                record.resource_id == resource_id
                for record in repository.list_records(
                    session,
                    TenantResourceKind.DOCUMENT,
                    limit=10_000,
                )
            )
            assert unauthorized_read_count == 0
            unauthorized_write_count = int(
                repository.update_payload(
                    session,
                    TenantResourceKind.DOCUMENT,
                    resource_id,
                    payload={"tenant": "B", "unauthorized": True},
                    status="intrusion",
                )
            )
            unauthorized_write_count += int(
                repository.delete(
                    session,
                    TenantResourceKind.DOCUMENT,
                    resource_id,
                )
            )
            assert unauthorized_write_count == 0
            assert all(
                record.resource_id != resource_id
                for record in repository.list_records(
                    session,
                    TenantResourceKind.DOCUMENT,
                    limit=10_000,
                )
            )
        with manager.transaction(tenant_a) as session:
            session.connection.execute(text(f"SET LOCAL ROLE {APPLICATION_DATABASE_ROLE}"))
            require_safe_application_role(session.connection)
            connection_ids.append(id(session.connection.connection.driver_connection))
            preserved = repository.get(session, TenantResourceKind.DOCUMENT, resource_id)
            assert preserved is not None
            assert preserved.status == "ready"
            assert preserved.payload == {"tenant": "A", "version": 1}

        assert len(set(connection_ids)) == 1
        with tenant_engine.connect() as connection:
            with connection.begin():
                connection.execute(text(f"SET LOCAL ROLE {APPLICATION_DATABASE_ROLE}"))
                pooled_context = tuple(
                    connection.execute(
                        text(
                            """
                            SELECT
                              NULLIF(current_setting('mentorflow.organization_id', true), ''),
                              NULLIF(current_setting('mentorflow.workspace_id', true), ''),
                              NULLIF(current_setting('mentorflow.actor_subject', true), '')
                            """
                        )
                    ).one()
                )
                assert pooled_context == (None, None, None)
        print(
            json.dumps(
                {
                    "app_db_role": APPLICATION_DATABASE_ROLE,
                    "app_db_role_bypassrls": False,
                    "migration_head": MIGRATION_REVISION,
                    "pool_tenant_state_leak": 0,
                    "pooled_connection_reused": True,
                    "postgres_version": postgres_version,
                    "rls_fail_closed": True,
                    "unauthorized_read": unauthorized_read_count,
                    "unauthorized_success": 0,
                    "unauthorized_write": unauthorized_write_count,
                },
                sort_keys=True,
            )
        )
    finally:
        if tenant_engine is not None:
            tenant_engine.dispose()
        migration_engine.dispose()


def test_frozen_contract_is_the_only_tenant_and_actor_contract() -> None:
    assert verify_frozen_contract_hashes() is True
    assert tenancy.TenantContext is TenantContext
    assert tenancy.ActorContext is ActorContext
    assert tenancy.TenantMode is TenantMode
    assert tenancy.ResourceScopeType is ResourceScopeType
    assert tenancy.ResourceClassification is ResourceClassification

    forbidden = {
        "ActorContext",
        "TenantContext",
        "ResourceScope",
        "AccessDecision",
        "AuditEnvelope",
    }
    definitions = {
        node.name
        for source_path in TENANCY_ROOT.glob("*.py")
        for node in ast.walk(ast.parse(source_path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef) and node.name in forbidden
    }
    assert definitions == set()


def test_transaction_context_binds_frozen_actor_and_tenant() -> None:
    context = TenantTransactionContext(tenant=_tenant(), actor=_actor())
    assert context.database_values == (
        "organization-a",
        "workspace-a",
        "subject-a",
    )
    compatible = TenantConnection(
        connection=cast(Connection, object()),
        context=context.tenant,
        actor=context.actor,
    )
    assert isinstance(compatible, TenantDbSession)
    assert compatible.transaction_context == context
    with pytest.raises(TenantContextRequiredError, match="must match"):
        TenantTransactionContext(
            tenant=_tenant(),
            actor=_actor(workspace_id="workspace-b"),
        )
    with pytest.raises(TenantContextRequiredError, match="workspace_id"):
        TenantTransactionContext(tenant=_tenant(workspace_id=None), actor=_actor())
    with pytest.raises(TenantContextRequiredError, match="ENTERPRISE_SERVER"):
        TenantTransactionContext(
            tenant=TenantContext(mode=TenantMode.PERSONAL_LOCAL),
            actor=_actor(organization_id=None, workspace_id=None),
        )


def test_pool_context_probe_and_reset_are_fail_closed() -> None:
    class Cursor:
        def __init__(self, owner: "DbApiConnection") -> None:
            self.owner = owner

        def execute(self, statement: str) -> None:
            self.owner.statements.append(statement.strip())
            if statement.strip() == "RESET ALL":
                self.owner.values = (None, None, None)

        def fetchone(self) -> tuple[str | None, str | None, str | None]:
            return self.owner.values

        def close(self) -> None:
            self.owner.closed_cursors += 1

    class DbApiConnection:
        def __init__(self) -> None:
            self.values: tuple[str | None, str | None, str | None] = (
                "organization-a",
                "workspace-a",
                "subject-a",
            )
            self.autocommit = False
            self.rollback_count = 0
            self.closed_cursors = 0
            self.statements: list[str] = []

        def cursor(self) -> Cursor:
            return Cursor(self)

        def rollback(self) -> None:
            self.rollback_count += 1

    connection = DbApiConnection()
    assert _dbapi_context_values(connection) == (
        "organization-a",
        "workspace-a",
        "subject-a",
    )
    _reset_dbapi_context(connection)
    assert connection.values == (None, None, None)
    assert connection.autocommit is False
    assert connection.rollback_count == 2
    assert connection.statements[-1] == "RESET ALL"


def test_dual_persistence_selection_preserves_personal_local() -> None:
    assert TenantResourceRepositoryFactory is DualPersistenceSelector
    personal = cast(Any, object())
    enterprise = cast(Any, object())
    selector = DualPersistenceSelector(
        personal_local=personal,
        enterprise_server=enterprise,
    )
    assert selector.select(TenantMode.PERSONAL_LOCAL) is personal
    assert selector.select(TenantMode.ENTERPRISE_SERVER) is enterprise


def test_personal_import_requires_preview_destination_plan_and_confirmation() -> None:
    class Source:
        payload_reads = 0

        def describe_importable(self) -> tuple[LocalImportDescriptor, ...]:
            return (
                LocalImportDescriptor(
                    local_id="local-document-a",
                    kind=TenantResourceKind.DOCUMENT,
                    title="Offline fixture",
                    scope_type=ResourceScopeType.WORKSPACE,
                    classification=ResourceClassification.INTERNAL,
                ),
            )

        def load_payload(self, local_id: str) -> dict[str, str]:
            self.payload_reads += 1
            return {"local_id": local_id}

    class Repository:
        def __init__(self) -> None:
            self.drafts: list[Any] = []

        def upsert(self, _transaction: TenantDbSession, draft: Any) -> Any:
            self.drafts.append(draft)
            return None

    source = Source()
    repository = Repository()
    service = LocalToEnterpriseImportService(cast(Any, repository))
    actor = _actor()
    transaction_context = TenantTransactionContext(tenant=_tenant(), actor=actor)
    transaction = TenantDbSession(
        connection=cast(Connection, object()),
        transaction_context=transaction_context,
    )

    preview = service.preview(source, requested_by=actor.subject_id)
    assert source.payload_reads == 0
    assert preview.auto_upload_started is False
    assert preview.requires_destination_selection is True
    plan = service.plan(
        preview.preview_id,
        target=transaction_context.tenant,
        planned_by=actor.subject_id,
    )
    assert plan.organization_id == "organization-a"
    assert plan.workspace_id == "workspace-a"
    assert plan.upload_started is False
    with pytest.raises(ImportConfirmationError, match="token"):
        service.confirm(
            plan.plan_id,
            "not-the-token",
            confirmed_by=actor.subject_id,
            transaction=transaction,
        )
    result = service.confirm(
        plan.plan_id,
        plan.confirmation_token,
        confirmed_by=actor.subject_id,
        transaction=transaction,
    )
    assert result.plan_id == plan.plan_id
    assert result.preview_id == preview.preview_id
    assert result.imported_count == 1
    assert source.payload_reads == 1
    assert repository.drafts[0].owner_subject_id == actor.subject_id


def test_rls_contract_is_fail_closed_and_forced_for_every_canonical_table() -> None:
    assert set(FORCE_RLS_TABLES) == CANONICAL_TABLES
    statements = postgres_rls_statements()
    assert len(statements) == len(FORCE_RLS_TABLES) * 4
    for table in FORCE_RLS_TABLES:
        predicate = rls_predicate_for(table)
        assert "current_setting('mentorflow.organization_id', true)" in predicate
        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in statements
        assert f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in statements
        policy = next(
            statement
            for statement in statements
            if statement.startswith(f'CREATE POLICY "tenant_isolation_{table}"')
        )
        assert f"USING ({predicate}) WITH CHECK ({predicate})" in policy
    assert "current_setting('mentorflow.actor_subject', true)" in rls_predicate_for(
        "documents"
    )


def test_synthetic_fixture_and_isolation_corpus_cover_every_resource_and_operation() -> None:
    seed = build_synthetic_tenant_seed()
    assert seed.organization_count == 3
    assert seed.workspace_count == 6
    assert seed.user_count == 20
    assert seed.resource_count == 48
    assert {resource.table for resource in seed.resources} == {
        "documents",
        "evidence",
        "knowledge_maps",
        "learning_plans",
        "teacher_sessions",
        "review_campaigns",
        "student_events",
        "cost_events",
    }
    corpus = build_tenant_isolation_corpus()
    assert len(corpus.probes) == 80
    assert corpus.operation_count == len(IsolationOperation) == 10
    assert corpus.resource_kind_count == len(TenantResourceKind) == 8
    assert corpus.unauthorized_success_expected == 0


def test_canonical_migrations_form_one_linear_head_through_0036() -> None:
    completion_path = (
        BACKEND_ROOT
        / "alembic"
        / "versions"
        / f"{COMPLETION_MIGRATION_REVISION}.py"
    )
    source = completion_path.read_text(encoding="utf-8")
    module = ast.parse(source)
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision"}
    }
    assert assignments == {
        "revision": COMPLETION_MIGRATION_REVISION,
        "down_revision": JOBS_MIGRATION_REVISION,
    }
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    assert ScriptDirectory.from_config(config).get_heads() == [MIGRATION_REVISION]

    jobs_source = (
        BACKEND_ROOT
        / "alembic"
        / "versions"
        / f"{JOBS_MIGRATION_REVISION}.py"
    ).read_text(encoding="utf-8")
    jobs_module = ast.parse(jobs_source)
    jobs_assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in jobs_module.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision"}
    }
    assert jobs_assignments == {
        "revision": JOBS_MIGRATION_REVISION,
        "down_revision": "0029_enterprise_tenancy_rls",
    }
    provider = ScriptDirectory.from_config(config).get_revision(
        PROVIDER_MIGRATION_REVISION
    )
    workspace = ScriptDirectory.from_config(config).get_revision(
        WORKSPACE_MIGRATION_REVISION
    )
    assets = ScriptDirectory.from_config(config).get_revision(ASSET_MIGRATION_REVISION)
    head = ScriptDirectory.from_config(config).get_revision(MIGRATION_REVISION)
    assert provider is not None
    assert provider.down_revision == COMPLETION_MIGRATION_REVISION
    assert workspace is not None
    assert workspace.down_revision == PROVIDER_MIGRATION_REVISION
    assert head is not None
    assert assets is not None
    assert assets.down_revision == WORKSPACE_MIGRATION_REVISION
    bootstrap = ScriptDirectory.from_config(config).get_revision(BOOTSTRAP_MIGRATION_REVISION)
    assert bootstrap.down_revision == ASSET_MIGRATION_REVISION
    assert head.down_revision == BOOTSTRAP_MIGRATION_REVISION
    assert ENTERPRISE_MIGRATION_REVISION == MIGRATION_REVISION
    assert "workspace_memberships" in source
    assert "learning_plans" in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "mentorflow.actor_subject" in source
    assert "0031 downgrade requires at most one role" in source

    foundation = (
        BACKEND_ROOT / "alembic" / "versions" / "0029_enterprise_tenancy_rls.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "0029_enterprise_tenancy_rls"' in foundation
    assert 'down_revision = "0028_video_collection_v1"' in foundation
    assert '"plans": "PLAN"' in foundation
    assert "workspace_memberships" not in foundation


def test_0035_sqlite_fresh_upgrade_incremental_downgrade_and_full_downgrade(
    tmp_path: Path,
) -> None:
    database = tmp_path / "enterprise-migration.db"
    config = _alembic_config(database)
    # Preserve historical reversible coverage; recovered 0036 is forward-only.
    historical_tables = CANONICAL_TABLES - {"account_import_ledger"}
    command.upgrade(config, BOOTSTRAP_MIGRATION_REVISION)
    assert historical_tables <= _tables(database)
    assert "plans" not in _tables(database)
    assert _version(database) == BOOTSTRAP_MIGRATION_REVISION
    with create_engine(f"sqlite:///{database.as_posix()}").connect() as connection:
        assert detect_enterprise_schema_version(connection) == BOOTSTRAP_MIGRATION_REVISION
        inspector = inspect(connection)
        plan_indexes = {
            item["name"] for item in inspector.get_indexes("learning_plans")
        }
        assert "ix_learning_plans_tenant_status_updated" in plan_indexes
        membership_indexes = {
            item["name"] for item in inspector.get_indexes("workspace_memberships")
        }
        assert "ix_workspace_memberships_scope_status" in membership_indexes

    command.downgrade(config, JOBS_MIGRATION_REVISION)
    assert "workspace_memberships" not in _tables(database)
    assert "learning_plans" not in _tables(database)
    assert "plans" in _tables(database)
    assert _version(database) == JOBS_MIGRATION_REVISION
    command.upgrade(config, BOOTSTRAP_MIGRATION_REVISION)
    assert historical_tables <= _tables(database)
    command.downgrade(config, "0028_video_collection_v1")
    assert CANONICAL_TABLES.isdisjoint(_tables(database))
    assert "plans" not in _tables(database)
    assert _version(database) == "0028_video_collection_v1"
    command.upgrade(config, BOOTSTRAP_MIGRATION_REVISION)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert _version(database) == BOOTSTRAP_MIGRATION_REVISION


def test_synthetic_seed_is_idempotent_on_offline_schema(tmp_path: Path) -> None:
    database = tmp_path / "enterprise-seed.db"
    command.upgrade(_alembic_config(database), "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    with engine.begin() as connection:
        seed_synthetic_tenants(connection)
        seed_synthetic_tenants(connection)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM organizations").fetchone() == (3,)
        assert connection.execute("SELECT count(*) FROM workspaces").fetchone() == (6,)
        assert connection.execute(
            "SELECT count(*) FROM organization_memberships"
        ).fetchone() == (20,)
        assert connection.execute(
            "SELECT count(*) FROM workspace_memberships"
        ).fetchone() == (40,)
        assert connection.execute(
            "SELECT count(*) FROM workspace_role_bindings"
        ).fetchone() == (40,)
        assert connection.execute("SELECT count(*) FROM resource_scopes").fetchone() == (
            48,
        )
        for table in (
            "documents",
            "evidence",
            "knowledge_maps",
            "learning_plans",
            "teacher_sessions",
            "review_campaigns",
            "student_events",
            "cost_events",
        ):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (6,)


def test_0031_preserves_existing_0029_membership_and_plan_rows(tmp_path: Path) -> None:
    database = tmp_path / "enterprise-upgrade.db"
    config = _alembic_config(database)
    command.upgrade(config, "0029_enterprise_tenancy_rls")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO organizations(id, slug, name, status) "
            "VALUES ('org-existing', 'existing', 'Existing', 'active')"
        )
        connection.execute(
            "INSERT INTO organization_memberships(organization_id, subject_id, role, status) "
            "VALUES ('org-existing', 'subject-existing', 'OWNER', 'active')"
        )
        connection.execute(
            "INSERT INTO workspaces(id, organization_id, slug, name, status) "
            "VALUES ('workspace-existing', 'org-existing', 'existing', 'Existing', 'active')"
        )
        connection.execute(
            "INSERT INTO workspace_role_bindings("
            "organization_id, workspace_id, subject_id, role, status"
            ") VALUES ("
            "'org-existing', 'workspace-existing', 'subject-existing', 'ADMIN', 'active'"
            ")"
        )
        connection.execute(
            "INSERT INTO resource_scopes("
            "id, resource_kind, resource_id, scope_type, owner_subject_id, "
            "organization_id, workspace_id, classification"
            ") VALUES ("
            "'scope-existing', 'PLAN', 'plan-existing', 'WORKSPACE', "
            "'subject-existing', 'org-existing', 'workspace-existing', 'INTERNAL'"
            ")"
        )
        connection.execute(
            "INSERT INTO plans("
            "resource_id, resource_scope_id, organization_id, workspace_id, "
            "scope_type, owner_subject_id, classification, title, status, payload_json"
            ") VALUES ("
            "'plan-existing', 'scope-existing', 'org-existing', 'workspace-existing', "
            "'WORKSPACE', 'subject-existing', 'INTERNAL', 'Preserved plan', 'ready', '{}'"
            ")"
        )
        connection.commit()

    command.upgrade(config, BOOTSTRAP_MIGRATION_REVISION)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT status FROM workspace_memberships WHERE organization_id = 'org-existing'"
        ).fetchone() == ("active",)
        assert connection.execute(
            "SELECT role FROM workspace_role_bindings WHERE organization_id = 'org-existing'"
        ).fetchone() == ("ADMIN",)
        assert connection.execute(
            "SELECT title FROM learning_plans WHERE resource_id = 'plan-existing'"
        ).fetchone() == ("Preserved plan",)
    command.downgrade(config, JOBS_MIGRATION_REVISION)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT title FROM plans WHERE resource_id = 'plan-existing'"
        ).fetchone() == ("Preserved plan",)


def test_staging_package_is_executable_without_declaring_a_driver() -> None:
    root = BACKEND_ROOT.parent / "enterprise_e1" / "staging" / "e1_02"
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    runner = (root / "run.sh").read_text(encoding="utf-8")
    parsed = yaml.safe_load(compose)

    assert set(parsed["services"]) == {"postgres", "one-shot-test-runner"}
    assert compose.count("\n    volumes:\n") == 2
    assert parsed["services"]["postgres"]["image"] == (
        "docker.io/library/postgres:17.6-bookworm@"
        "sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3"
    )
    assert "ports" not in parsed["services"]["postgres"]
    assert parsed["services"]["one-shot-test-runner"]["build"]["target"] == (
        "backend-base"
    )
    assert "test_real_postgres_rls_is_fail_closed_and_pool_safe" in compose
    assert "test_real_postgres17_worker_process_consumes_durable_job" in compose
    assert "pip install" not in compose
    assert "GITHUB_RUN_ID" in runner
    assert "GITHUB_RUN_ATTEMPT" in runner
    assert 'compose_project="mf-e102-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"' in runner
    assert 'trap cleanup EXIT' in runner
    assert (
        'docker compose -p "$compose_project" down --volumes --remove-orphans'
        in runner
    )
    assert "com.docker.compose.project=$compose_project" in runner
    assert "docker system prune" not in runner
    assert "docker container prune" not in runner
    assert "docker volume prune" not in runner
    assert "docker network prune" not in runner


def test_enterprise_evidence_documents_keep_real_postgres_pending() -> None:
    docs = BACKEND_ROOT.parent / "docs" / "enterprise"
    expected = {
        "data-model.md",
        "tenant-isolation.md",
        "rls-policy.md",
        "migration-strategy.md",
    }
    assert {path.name for path in docs.glob("*.md")} >= expected
    combined = "\n".join(
        (docs / name).read_text(encoding="utf-8") for name in sorted(expected)
    )
    assert "REAL_POSTGRES_RLS_GATE = PENDING_STAGING" in combined
    assert "UNAUTHORIZED_SUCCESS_EXPECTED = 0" in combined
    assert "workspace_memberships" in combined
    assert "learning_plans" in combined
    assert "No `EXPLAIN ANALYZE` numbers" in combined
