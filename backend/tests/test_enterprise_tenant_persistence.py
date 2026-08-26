from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, cast

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Connection

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
    TenantConnection,
    TenantDbSession,
    TenantTransactionContext,
    _dbapi_context_values,
    _reset_dbapi_context,
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
    TenantResourceKind,
)
from app.enterprise_tenancy.repository import (
    DualPersistenceSelector,
    TenantResourceRepositoryFactory,
)
from app.enterprise_tenancy.schema import (
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
TENANCY_ROOT = BACKEND_ROOT / "app" / "enterprise_tenancy"
JOBS_MIGRATION_REVISION = "0030_enterprise_jobs_v1"
MIGRATION_REVISION = "0031_enterprise_tenancy_completion"
CANONICAL_TABLES = {
    "organizations",
    "organization_memberships",
    "workspaces",
    "workspace_memberships",
    "workspace_role_bindings",
    "resource_scopes",
    "documents",
    "evidence",
    "knowledge_maps",
    "learning_plans",
    "teacher_sessions",
    "review_campaigns",
    "student_events",
    "cost_events",
}


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


def test_0031_migration_is_the_only_head_after_canonical_0030_jobs() -> None:
    completion_path = BACKEND_ROOT / "alembic" / "versions" / f"{MIGRATION_REVISION}.py"
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
        "revision": MIGRATION_REVISION,
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


def test_0031_sqlite_fresh_upgrade_incremental_downgrade_and_full_downgrade(
    tmp_path: Path,
) -> None:
    database = tmp_path / "enterprise-migration.db"
    config = _alembic_config(database)
    command.upgrade(config, "head")
    assert CANONICAL_TABLES <= _tables(database)
    assert "plans" not in _tables(database)
    assert _version(database) == MIGRATION_REVISION
    with create_engine(f"sqlite:///{database.as_posix()}").connect() as connection:
        assert detect_enterprise_schema_version(connection) == MIGRATION_REVISION
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
    command.upgrade(config, "head")
    assert CANONICAL_TABLES <= _tables(database)
    command.downgrade(config, "0028_video_collection_v1")
    assert CANONICAL_TABLES.isdisjoint(_tables(database))
    assert "plans" not in _tables(database)
    assert _version(database) == "0028_video_collection_v1"
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert _version(database) == MIGRATION_REVISION


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

    command.upgrade(config, "head")
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
    readme = (root / "README.md").read_text(encoding="utf-8")
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    runner = (root / "run.sh").read_text(encoding="utf-8")
    pool_gate = (root / "run_pool_gate.py").read_text(encoding="utf-8")
    rls_gate = (root / "sql" / "rls_negative.sql").read_text(encoding="utf-8")
    pool_sql = (root / "sql" / "pool_context.sql").read_text(encoding="utf-8")
    plans = (root / "sql" / "query_plans.sql").read_text(encoding="utf-8")

    assert "postgres:16-bookworm" in compose
    assert "0030_enterprise_jobs_v1" in readme
    assert MIGRATION_REVISION in readme
    assert "MENTORFLOW_BACKEND_IMAGE" in compose
    assert "alembic -x seed_synthetic=true upgrade head" in compose
    assert "pip install" not in compose
    assert "docker compose up" in runner
    assert "pool_size=1" in pool_gate
    assert "same_dbapi_connection_reused" in pool_gate
    assert "UNAUTHORIZED_SUCCESS=0" in rls_gate
    assert "foreign ID read" in rls_gate
    assert "background job and cache ownership" in rls_gate
    assert "transaction-local tenant context leaked" in pool_sql
    assert "EXPLAIN (COSTS ON, VERBOSE OFF)" in plans
    assert "EXPLAIN ANALYZE" not in plans


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
