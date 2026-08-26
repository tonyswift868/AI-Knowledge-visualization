from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, cast

from alembic import command
from alembic.config import Config
import pytest
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
    require_matching_actor_context,
)
from app.enterprise_tenancy.import_seam import LocalToEnterpriseImportService
from app.enterprise_tenancy.models import (
    ImportConfirmationError,
    LocalImportDescriptor,
    TenantContextRequiredError,
    TenantResourceKind,
)
from app.enterprise_tenancy.repository import DualPersistenceSelector
from app.enterprise_tenancy.schema import (
    FORCE_RLS_TABLES,
    postgres_rls_statements,
    rls_predicate_for,
)
from app.enterprise_tenancy.synthetic import build_synthetic_tenant_seed


BACKEND_ROOT = Path(__file__).resolve().parents[1]
TENANCY_ROOT = BACKEND_ROOT / "app" / "enterprise_tenancy"
MIGRATION_REVISION = "0029_enterprise_tenancy_rls"
DATABASE_HEAD_REVISION = "0030_enterprise_jobs_v1"


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
    definitions: set[str] = set()
    for source_path in TENANCY_ROOT.glob("*.py"):
        module = ast.parse(source_path.read_text(encoding="utf-8"))
        definitions.update(
            node.name
            for node in ast.walk(module)
            if isinstance(node, ast.ClassDef) and node.name in forbidden
        )
    assert definitions == set()


def test_actor_and_tenant_contracts_must_match_before_setting_rls_context() -> None:
    assert require_matching_actor_context(_tenant(), _actor()) == (
        "organization-a",
        "workspace-a",
        "subject-a",
    )

    with pytest.raises(TenantContextRequiredError, match="must match"):
        require_matching_actor_context(
            _tenant(),
            _actor(workspace_id="workspace-b"),
        )
    with pytest.raises(TenantContextRequiredError, match="workspace_id"):
        require_matching_actor_context(_tenant(workspace_id=None), _actor())
    with pytest.raises(TenantContextRequiredError, match="ENTERPRISE_SERVER"):
        require_matching_actor_context(
            TenantContext(mode=TenantMode.PERSONAL_LOCAL),
            _actor(organization_id=None, workspace_id=None),
        )


def test_dual_persistence_selection_uses_frozen_tenant_mode() -> None:
    personal = cast(Any, object())
    enterprise = cast(Any, object())
    selector = DualPersistenceSelector(
        personal_local=personal,
        enterprise_server=enterprise,
    )

    assert selector.select(TenantMode.PERSONAL_LOCAL) is personal
    assert selector.select(TenantMode.ENTERPRISE_SERVER) is enterprise


def test_local_import_requires_preview_and_uses_frozen_actor_subject() -> None:
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
        drafts: list[Any]

        def __init__(self) -> None:
            self.drafts = []

        def upsert(self, _transaction: TenantConnection, draft: Any) -> Any:
            self.drafts.append(draft)
            return None

    source = Source()
    repository = Repository()
    service = LocalToEnterpriseImportService(cast(Any, repository))
    tenant = _tenant()
    actor = _actor()
    transaction = TenantConnection(
        connection=cast(Connection, object()),
        context=tenant,
        actor=actor,
    )

    preview = service.preview(source, target=tenant, requested_by=actor.subject_id)
    assert source.payload_reads == 0
    assert preview.auto_upload_started is False
    assert preview.requires_explicit_confirmation is True
    with pytest.raises(ImportConfirmationError, match="token"):
        service.confirm(
            preview.preview_id,
            "not-the-token",
            confirmed_by=actor.subject_id,
            transaction=transaction,
        )

    result = service.confirm(
        preview.preview_id,
        preview.confirmation_token,
        confirmed_by=actor.subject_id,
        transaction=transaction,
    )
    assert result.imported_count == 1
    assert source.payload_reads == 1
    assert repository.drafts[0].owner_subject_id == actor.subject_id


def test_rls_contract_is_fail_closed_and_forced_for_every_tenant_table() -> None:
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


def test_synthetic_tenant_fixture_has_required_cardinality() -> None:
    seed = build_synthetic_tenant_seed()
    assert seed.organization_count == 3
    assert seed.workspace_count == 6
    assert seed.user_count == 20
    assert len({user for org in seed.organizations for user in org.subject_ids}) == 20


def test_0029_migration_static_contract() -> None:
    source_path = BACKEND_ROOT / "alembic" / "versions" / f"{MIGRATION_REVISION}.py"
    source = source_path.read_text(encoding="utf-8")
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
        "down_revision": "0028_video_collection_v1",
    }
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "NOBYPASSRLS" in source
    assert "mentorflow_app must not run migrations or own tenant tables" in source
    assert "current_setting('mentorflow.organization_id', true)" in source
    assert "current_setting('mentorflow.workspace_id', true)" in source
    assert "current_setting('mentorflow.subject_id', true)" in source


def test_0029_sqlite_upgrade_downgrade_upgrade_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "enterprise-migration.db"
    config = _alembic_config(database)
    expected_tables = {
        "organizations",
        "organization_memberships",
        "workspaces",
        "workspace_role_bindings",
        "resource_scopes",
        "documents",
        "evidence",
        "knowledge_maps",
        "plans",
        "teacher_sessions",
        "review_campaigns",
        "student_events",
        "cost_events",
    }

    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert expected_tables <= tables
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            DATABASE_HEAD_REVISION,
        )

    command.downgrade(config, "0028_video_collection_v1")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert expected_tables.isdisjoint(tables)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0028_video_collection_v1",
        )

    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            DATABASE_HEAD_REVISION,
        )
