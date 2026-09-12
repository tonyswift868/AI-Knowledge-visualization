from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, text

from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)
from app.enterprise_tenancy.adapters import (
    AccountProgressSnapshotRepository,
    PostgresTenantDomainRepository,
)
from app.enterprise_tenancy.context import TenantDbSession, TenantTransactionContext
from app.enterprise_tenancy.models import (
    ACCOUNT_PROGRESS_SNAPSHOT_RECORD_TYPE,
    ACCOUNT_PROGRESS_SNAPSHOT_SCHEMA_VERSION,
    AccountProgressSnapshotDraft,
    ProgressSnapshotVersionError,
    TenantResourceDraft,
    TenantResourceKind,
)
from app.enterprise_tenancy.repository import PostgresTenantResourceRepository
from app.enterprise_tenancy.schema import (
    APPLICATION_DATABASE_ROLE,
    ENTERPRISE_MIGRATION_REVISION,
    postgres_rls_statements,
    rls_predicate_for,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _config(database: Path) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    config.attributes["configure_logger"] = False
    return config


def _context(
    *,
    organization: str,
    workspace: str,
    subject: str,
) -> tuple[ActorContext, TenantContext, ResourceScope]:
    tenant = TenantContext(
        organization_id=organization,
        workspace_id=workspace,
        mode=TenantMode.ENTERPRISE_SERVER,
    )
    actor = ActorContext(
        subject_id=subject,
        actor_kind=ActorKind.HUMAN,
        issuer="account-core-test",
        token_id=f"token-{subject}",
        session_id=f"session-{subject}",
        request_id=f"request-{subject}",
        authentication_time=datetime(2026, 9, 3, tzinfo=timezone.utc),
        authentication_strength="test",
        organization_id=organization,
        workspace_id=workspace,
        membership_id=f"membership-{organization}-{subject}",
        roles=("OWNER",),
    )
    scope = ResourceScope(
        scope_type=ResourceScopeType.PERSONAL,
        owner_subject_id=subject,
        organization_id=None,
        workspace_id=None,
        classification=ResourceClassification.CONFIDENTIAL,
    )
    return actor, tenant, scope


def _seed_account(connection, *, organization: str, workspace: str, subject: str) -> None:
    connection.execute(
        text(
            "INSERT INTO organizations(id, slug, name, status) "
            "VALUES (:organization, :organization, :organization, 'active') "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"organization": organization},
    )
    connection.execute(
        text(
            "INSERT INTO organization_memberships("
            "organization_id, subject_id, role, status"
            ") VALUES (:organization, :subject, 'OWNER', 'active')"
        ),
        {"organization": organization, "subject": subject},
    )
    connection.execute(
        text(
            "INSERT INTO workspaces(id, organization_id, slug, name, status) "
            "VALUES (:workspace, :organization, :workspace, :workspace, 'active') "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"organization": organization, "workspace": workspace},
    )
    connection.execute(
        text(
            "INSERT INTO workspace_memberships("
            "organization_id, workspace_id, subject_id, status"
            ") VALUES (:organization, :workspace, :subject, 'active')"
        ),
        {
            "organization": organization,
            "workspace": workspace,
            "subject": subject,
        },
    )
    connection.execute(
        text(
            "INSERT INTO workspace_role_bindings("
            "organization_id, workspace_id, subject_id, role, status"
            ") VALUES (:organization, :workspace, :subject, 'OWNER', 'active')"
        ),
        {
            "organization": organization,
            "workspace": workspace,
            "subject": subject,
        },
    )


def _session(connection, actor: ActorContext, tenant: TenantContext) -> TenantDbSession:
    return TenantDbSession(
        connection=connection,
        transaction_context=TenantTransactionContext(tenant=tenant, actor=actor),
    )


def _progress_repository(session: TenantDbSession) -> AccountProgressSnapshotRepository:
    return AccountProgressSnapshotRepository(
        PostgresTenantDomainRepository(
            PostgresTenantResourceRepository(),
            session,
            TenantResourceKind.STUDENT_EVENT,
        )
    )


@pytest.mark.parametrize(
    "snapshot",
    (
        {"local_path": "asset.pdf"},
        {"resource": {"path": r"C:\Users\learner\asset.pdf"}},
        {"resource": {"path": r"\\device\share\asset.pdf"}},
        {"raw_resource_bytes": "not-even-real-bytes"},
        {"resource": b"raw-resource"},
    ),
)
def test_progress_snapshot_contract_rejects_device_local_or_raw_data(
    snapshot: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        AccountProgressSnapshotDraft(
            progress_id="learning-progress",
            version=1,
            snapshot=snapshot,
        )


@pytest.mark.parametrize("version", (True, 0, -1, 1.0, "1"))
def test_progress_snapshot_version_is_a_positive_exact_integer(version: object) -> None:
    with pytest.raises(ValueError, match="positive signed 64-bit integer"):
        AccountProgressSnapshotDraft(
            progress_id="learning-progress",
            version=version,  # type: ignore[arg-type]
            snapshot={},
        )


def test_plan_and_progress_account_ownership_survive_repository_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "account-core.sqlite3"
    command.upgrade(_config(database), "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}", future=True)
    actor_a, tenant_a, scope_a = _context(
        organization="org-a",
        workspace="workspace-a",
        subject="account-a",
    )
    actor_b, tenant_b, scope_b = _context(
        organization="org-a",
        workspace="workspace-a",
        subject="account-b",
    )
    with engine.begin() as connection:
        _seed_account(
            connection,
            organization="org-a",
            workspace="workspace-a",
            subject="account-a",
        )
        _seed_account(
            connection,
            organization="org-a",
            workspace="workspace-a",
            subject="account-b",
        )
        session_a = _session(connection, actor_a, tenant_a)
        resources = PostgresTenantResourceRepository()
        resources.upsert(
            session_a,
            TenantResourceDraft(
                resource_id="plan-account-a",
                kind=TenantResourceKind.PLAN,
                scope_type=ResourceScopeType.PERSONAL,
                owner_subject_id=actor_a.subject_id,
                classification=ResourceClassification.CONFIDENTIAL,
                title="Account A plan",
                status="ACTIVE",
                payload={"goal_id": "goal-a", "version": 4},
            ),
        )
        progress_a = _progress_repository(session_a).upsert(
            tenant=tenant_a,
            scope=scope_a,
            value=AccountProgressSnapshotDraft(
                progress_id="current-learning-state",
                version=4,
                snapshot={
                    "plan_id": "plan-account-a",
                    "concepts": {"algebra": {"score": 0.75}},
                },
            ),
        )
        assert progress_a.owner_subject_id == "account-a"

        session_b = _session(connection, actor_b, tenant_b)
        progress_b_repository = _progress_repository(session_b)
        assert progress_b_repository.get(
            tenant=tenant_b,
            scope=scope_b,
            progress_id="current-learning-state",
        ) is None
        assert progress_b_repository.delete(
            tenant=tenant_b,
            scope=scope_b,
            progress_id="current-learning-state",
        ) is False
        progress_b_repository.upsert(
            tenant=tenant_b,
            scope=scope_b,
            value=AccountProgressSnapshotDraft(
                progress_id="current-learning-state",
                version=2,
                snapshot={"concepts": {"geometry": {"score": 0.25}}},
            ),
        )
    engine.dispose()

    restarted = create_engine(f"sqlite:///{database.as_posix()}", future=True)
    with restarted.begin() as connection:
        session_a = _session(connection, actor_a, tenant_a)
        persisted_plan = PostgresTenantResourceRepository().get(
            session_a,
            TenantResourceKind.PLAN,
            "plan-account-a",
        )
        assert persisted_plan is not None
        assert persisted_plan.organization_id == "org-a"
        assert persisted_plan.workspace_id == "workspace-a"
        assert persisted_plan.owner_subject_id == "account-a"
        assert persisted_plan.payload == {"goal_id": "goal-a", "version": 4}

        progress_repository = _progress_repository(session_a)
        persisted_progress = progress_repository.get(
            tenant=tenant_a,
            scope=scope_a,
            progress_id="current-learning-state",
        )
        assert persisted_progress is not None
        assert persisted_progress.version == 4
        assert persisted_progress.organization_id == "org-a"
        assert persisted_progress.workspace_id == "workspace-a"
        assert persisted_progress.owner_subject_id == "account-a"
        assert persisted_progress.snapshot["plan_id"] == "plan-account-a"
        contract = persisted_progress.public_contract()
        assert contract["schema_version"] == ACCOUNT_PROGRESS_SNAPSHOT_SCHEMA_VERSION
        assert contract["organization_id"] == "org-a"
        assert contract["workspace_id"] == "workspace-a"
        assert contract["owner_subject_id"] == "account-a"
        assert isinstance(contract["updated_at"], str)

        with pytest.raises(
            ProgressSnapshotVersionError,
            match="move backwards",
        ) as stale:
            progress_repository.upsert(
                tenant=tenant_a,
                scope=scope_a,
                value=AccountProgressSnapshotDraft(
                    progress_id="current-learning-state",
                    version=3,
                    snapshot={},
                ),
            )
        assert stale.value.code == "ENTERPRISE_PROGRESS_VERSION_CONFLICT"
        assert stale.value.status == 409
        with pytest.raises(ProgressSnapshotVersionError, match="different metadata"):
            progress_repository.upsert(
                tenant=tenant_a,
                scope=scope_a,
                value=AccountProgressSnapshotDraft(
                    progress_id="current-learning-state",
                    version=4,
                    snapshot={"conflict": True},
                ),
            )
        advanced = progress_repository.upsert(
            tenant=tenant_a,
            scope=scope_a,
            value=AccountProgressSnapshotDraft(
                progress_id="current-learning-state",
                version=5,
                snapshot={"concepts": {"algebra": {"score": 0.9}}},
            ),
        )
        assert advanced.version == 5
        assert len(
            progress_repository.list_snapshots(
                tenant=tenant_a,
                scope=scope_a,
            )
        ) == 1

        rows = connection.execute(
            text(
                "SELECT resource_kind, owner_subject_id, payload_json "
                "FROM student_events ORDER BY owner_subject_id"
            )
        ).all()
        assert len(rows) == 2
        assert {str(row[0]) for row in rows} == {TenantResourceKind.STUDENT_EVENT.value}
        assert {str(row[1]) for row in rows} == {"account-a", "account-b"}
        serialized = "\n".join(str(row[2]).casefold() for row in rows)
        assert ACCOUNT_PROGRESS_SNAPSHOT_RECORD_TYPE in serialized
        assert "local_path" not in serialized
        assert "raw_resource_bytes" not in serialized
        assert "c:\\" not in serialized
    restarted.dispose()


def test_account_core_reuses_existing_schema_with_bootstrap_forward_migration() -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    assert ScriptDirectory.from_config(config).get_heads() == [
        "0036_account_import_ledger"
    ]
    assert ENTERPRISE_MIGRATION_REVISION == "0036_account_import_ledger"
    assert not (
        BACKEND_ROOT
        / "alembic"
        / "versions"
        / "0035_account_core_metadata_sync.py"
    ).exists()

    predicate = rls_predicate_for("student_events")
    assert "mentorflow.organization_id" in predicate
    assert "mentorflow.workspace_id" in predicate
    assert "mentorflow.actor_subject" in predicate
    statements = postgres_rls_statements()
    assert 'ALTER TABLE "student_events" ENABLE ROW LEVEL SECURITY' in statements
    assert 'ALTER TABLE "student_events" FORCE ROW LEVEL SECURITY' in statements
    policy = next(
        item
        for item in statements
        if item.startswith('CREATE POLICY "tenant_isolation_student_events"')
    )
    assert f"USING ({predicate}) WITH CHECK ({predicate})" in policy

    migration = (
        BACKEND_ROOT
        / "alembic"
        / "versions"
        / "0029_enterprise_tenancy_rls.py"
    ).read_text(encoding="utf-8")
    assert (
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" '
        in migration
    )
    assert f'APPLICATION_ROLE = "{APPLICATION_DATABASE_ROLE}"' in migration
    assert '"rolsuper"' in migration
    assert '"rolbypassrls"' in migration
    assert "CREATE ROLE" not in migration
