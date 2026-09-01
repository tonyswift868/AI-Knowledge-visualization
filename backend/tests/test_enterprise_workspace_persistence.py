from __future__ import annotations

import asyncio
import io
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import UploadFile
import pytest
from sqlalchemy import create_engine, inspect, text

from app.document_import_integration import DocumentBatchStartIn
from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    ResourceClassification,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)
from app.enterprise.jobs_v1.learning_runs import LearningRunService
from app.enterprise.jobs_v1.schema import metadata as jobs_metadata
from app.enterprise_tenancy.context import TenantDbSession, TenantTransactionContext
from app.enterprise_tenancy.document_import import EnterpriseDocumentImportService
from app.enterprise_tenancy.models import (
    EnterprisePersistenceRequestError,
    TenantResourceDraft,
    TenantResourceKind,
)
from app.enterprise_tenancy.repository import PostgresTenantResourceRepository
from app.enterprise_tenancy.teaching import EnterpriseTeachingService
from app.enterprise_tenancy.workspaces import EnterpriseWorkspaceService
from app.schemas import GoalPatch, GoalProfile, WorkspaceIn


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_REVISION = "0033_enterprise_workspace_persistence"
PREVIOUS_REVISION = "0032_enterprise_provider_sessions"


def _config(database: Path) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    config.attributes["configure_logger"] = False
    return config


class _SQLiteTransactions:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.transaction_count = 0

    @contextmanager
    def transaction(self, context: TenantTransactionContext):
        self.transaction_count += 1
        with self.engine.begin() as connection:
            yield TenantDbSession(connection=connection, transaction_context=context)


def _context(
    *,
    organization: str = "org-a",
    workspace: str = "workspace-a",
    subject: str = "owner-a",
) -> tuple[ActorContext, TenantContext]:
    tenant = TenantContext(
        organization_id=organization,
        workspace_id=workspace,
        mode=TenantMode.ENTERPRISE_SERVER,
    )
    actor = ActorContext(
        subject_id=subject,
        actor_kind=ActorKind.HUMAN,
        issuer="offline-enterprise-test",
        token_id=f"token-{subject}",
        session_id=f"session-{subject}",
        request_id=f"request-{subject}",
        authentication_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
        authentication_strength="test",
        organization_id=organization,
        workspace_id=workspace,
        membership_id=f"membership-{organization}-{subject}",
        roles=("OWNER",),
    )
    return actor, tenant


def _seed_identity(engine, *, organization: str, workspace: str, subject: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations(id, slug, name, status) "
                "VALUES (:id, :slug, :name, 'active')"
            ),
            {"id": organization, "slug": organization, "name": organization},
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
                "VALUES (:workspace, :organization, :workspace, :workspace, 'active')"
            ),
            {"workspace": workspace, "organization": organization},
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
        connection.execute(
            text(
                "INSERT INTO workspace_profiles("
                "workspace_id, organization_id, description, character_id, "
                "allow_external_knowledge, persona_prompt, execution_mode, "
                "provider_name, provider_model"
                ") VALUES (:workspace, :organization, '', 'warm', false, '', "
                "'demo', '', '')"
            ),
            {"workspace": workspace, "organization": organization},
        )


def _database(tmp_path: Path):
    path = tmp_path / "enterprise-persistence.sqlite3"
    command.upgrade(_config(path), "head")
    engine = create_engine(
        f"sqlite:///{path.as_posix()}", future=True
    ).execution_options(schema_translate_map={"enterprise_jobs": None})
    jobs_metadata.create_all(engine)
    _seed_identity(
        engine,
        organization="org-a",
        workspace="workspace-a",
        subject="owner-a",
    )
    _seed_identity(
        engine,
        organization="org-b",
        workspace="workspace-b",
        subject="owner-b",
    )
    return path, engine, _SQLiteTransactions(engine)


def test_0033_fresh_incremental_upgrade_and_downgrade_preserve_canonical_rows(
    tmp_path: Path,
) -> None:
    fresh = tmp_path / "fresh.sqlite3"
    command.upgrade(_config(fresh), "head")
    with sqlite3.connect(fresh) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"workspace_profiles", "workspace_goals"} <= tables
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            MIGRATION_REVISION,
        )

    incremental = tmp_path / "incremental.sqlite3"
    config = _config(incremental)
    command.upgrade(config, PREVIOUS_REVISION)
    with sqlite3.connect(incremental) as connection:
        connection.execute(
            "INSERT INTO organizations(id, slug, name, status) "
            "VALUES ('org-old', 'org-old', 'Old', 'active')"
        )
        connection.execute(
            "INSERT INTO workspaces(id, organization_id, slug, name, status) "
            "VALUES ('workspace-old', 'org-old', 'workspace-old', 'Old', 'active')"
        )
        connection.commit()
    command.upgrade(config, "head")
    with sqlite3.connect(incremental) as connection:
        assert connection.execute(
            "SELECT name FROM workspaces WHERE id = 'workspace-old'"
        ).fetchone() == ("Old",)
    command.downgrade(config, PREVIOUS_REVISION)
    with sqlite3.connect(incremental) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "workspace_profiles" not in tables
        assert "workspace_goals" not in tables
        assert connection.execute(
            "SELECT name FROM workspaces WHERE id = 'workspace-old'"
        ).fetchone() == ("Old",)


def test_workspace_create_goal_and_restart_use_only_canonical_tables(tmp_path: Path) -> None:
    _path, engine, transactions = _database(tmp_path)
    actor, tenant = _context()
    service = EnterpriseWorkspaceService(transaction_manager=transactions)
    inspector = inspect(engine)
    local_before = None
    if "orm_workspaces" in inspector.get_table_names():
        with engine.connect() as connection:
            local_before = connection.scalar(text("SELECT count(*) FROM orm_workspaces"))
    created = service.create(
        actor=actor,
        tenant=tenant,
        body=WorkspaceIn(
            title="Canonical workspace",
            description="PostgreSQL-only Enterprise state",
            execution_mode="real",
        ),
    )
    workspace_id = str(created["id"])
    target_actor = actor.model_copy(update={"workspace_id": workspace_id})
    target_tenant = tenant.model_copy(update={"workspace_id": workspace_id})
    goal = GoalProfile(
        goal_statement="Ship one verified Enterprise persistence slice",
        deadline=date.today() + timedelta(days=7),
        success_criteria=["focused persistence tests pass"],
    )
    saved = service.set_goal(
        actor=target_actor,
        tenant=target_tenant,
        workspace_id=workspace_id,
        body=goal,
    )
    assert saved["confirmed"] is False
    transaction_count = transactions.transaction_count
    patched = service.patch_goal(
        actor=target_actor,
        tenant=target_tenant,
        workspace_id=workspace_id,
        body=GoalPatch(minutes_per_day=45),
    )
    assert patched["id"] == saved["id"]
    assert patched["payload"]["minutes_per_day"] == 45
    assert transactions.transaction_count == transaction_count + 1

    restarted = EnterpriseWorkspaceService(transaction_manager=transactions)
    detail = restarted.get(
        actor=target_actor,
        tenant=target_tenant,
        workspace_id=workspace_id,
    )
    assert detail["title"] == "Canonical workspace"
    assert detail["goal"]["id"] == saved["id"]
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM workspaces WHERE id = :id"),
            {"id": workspace_id},
        ) == 1
        assert connection.scalar(
            text("SELECT count(*) FROM workspace_memberships WHERE workspace_id = :id"),
            {"id": workspace_id},
        ) == 1
        assert connection.scalar(
            text("SELECT count(*) FROM workspace_role_bindings WHERE workspace_id = :id"),
            {"id": workspace_id},
        ) == 1
        assert connection.scalar(
            text("SELECT count(*) FROM workspace_profiles WHERE workspace_id = :id"),
            {"id": workspace_id},
        ) == 1
        if local_before is not None:
            assert connection.scalar(text("SELECT count(*) FROM orm_workspaces")) == (
                local_before
            )
    engine.dispose()


def test_document_selection_restart_batch_and_learning_run_use_canonical_ownership(
    tmp_path: Path,
) -> None:
    _path, engine, transactions = _database(tmp_path)
    actor, tenant = _context()
    root = (tmp_path / "enterprise-document-root").resolve()
    service = EnterpriseDocumentImportService(
        transaction_manager=transactions,
        runtime_root=root,
    )
    selected = asyncio.run(
        service.select(
            actor=actor,
            tenant=tenant,
            files=[
                UploadFile(
                    filename="lesson.txt",
                    file=io.BytesIO(b"Durable tenant-owned evidence"),
                )
            ],
        )
    )
    selection_id = str(selected["selection_id"])
    document_id = str(selected["document_ids"][0])
    restarted = EnterpriseDocumentImportService(
        transaction_manager=transactions,
        runtime_root=root,
    )
    assert restarted.preflight(
        actor=actor,
        tenant=tenant,
        selection_id=selection_id,
    )["ready"] is True
    resolved = restarted.resolve_selection_paths(
        actor=actor,
        tenant=tenant,
        selection_id=selection_id,
        document_ids=[document_id],
    )
    assert len(resolved) == 1
    assert Path(resolved[0]).is_file()
    assert "/app/data" not in Path(resolved[0]).as_posix().casefold()

    first = restarted.start_batch(
        actor=actor,
        tenant=tenant,
        selection_id=selection_id,
        body=DocumentBatchStartIn(conversion_batch_size=1),
    )
    duplicate = restarted.start_batch(
        actor=actor,
        tenant=tenant,
        selection_id=selection_id,
        body=DocumentBatchStartIn(conversion_batch_size=1),
    )
    assert first["duplicate"] is False
    assert duplicate["duplicate"] is True
    assert duplicate["job_id"] == first["job_id"]
    assert first["batch_id"] == first["job_id"]
    assert duplicate["batch_id"] == first["job_id"]
    assert restarted.progress(
        actor=actor,
        tenant=tenant,
        batch_id=first["batch_id"],
    )["status"] == "PENDING"
    assert restarted.result(
        actor=actor,
        tenant=tenant,
        batch_id=first["batch_id"],
    ) is None
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM documents")) == 2
        assert connection.scalar(text("SELECT count(*) FROM jobs")) == 1
        assert connection.scalar(text("SELECT count(*) FROM outbox")) == 1

    # API submission authenticates the canonical selection but does not probe
    # Worker-local content availability. The durable Worker owns that check.
    Path(resolved[0]).unlink()
    learning_runs = LearningRunService(
        engine,
        document_selection_resolver=restarted.resolve_selection_paths,
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    learning = learning_runs.create(
        actor=actor,
        tenant=tenant,
        body={
            "idempotency_key": "canonical-learning-run-1",
            "document_selection": {
                "selection_id": selection_id,
                "document_ids": [document_id],
                "conversion_batch_size": 1,
            },
            "provider_session_id": "provider-session-a",
            "glossary": {"version": "v1", "terms": []},
            "plan": {},
        },
    )
    assert learning["created"] is True
    assert learning["status"] == "PENDING"

    other_actor, other_tenant = _context(
        organization="org-b", workspace="workspace-b", subject="owner-b"
    )
    with pytest.raises(EnterprisePersistenceRequestError) as denied:
        restarted.preflight(
            actor=other_actor,
            tenant=other_tenant,
            selection_id=selection_id,
        )
    assert denied.value.status == 404
    engine.dispose()


def test_teaching_requires_provider_and_persists_tenant_session(tmp_path: Path) -> None:
    _path, engine, transactions = _database(tmp_path)
    actor, tenant = _context()
    repository = PostgresTenantResourceRepository()
    with transactions.transaction(
        TenantTransactionContext(tenant=tenant, actor=actor)
    ) as transaction:
        repository.upsert(
            transaction,
            TenantResourceDraft(
                resource_id="plan-a",
                kind=TenantResourceKind.PLAN,
                scope_type=ResourceScopeType.WORKSPACE,
                owner_subject_id=actor.subject_id,
                classification=ResourceClassification.CONFIDENTIAL,
                title="Canonical plan",
                status="READY",
                payload={"plan_id": "plan-a", "lessons": ["lesson-a"]},
            ),
        )
    service = EnterpriseTeachingService(
        transaction_manager=transactions,
        resource_repository=repository,
    )
    with pytest.raises(EnterprisePersistenceRequestError) as missing:
        service.start(
            actor=actor,
            tenant=tenant,
            workspace_id="workspace-a",
            body={"plan_id": "plan-a"},
        )
    assert missing.value.code == "PROVIDER_CONFIGURATION_REQUIRED"
    assert missing.value.status == 409

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO enterprise_provider_sessions(
                  provider_session_id, subject_id, organization_id, workspace_id,
                  provider, base_url, model, credential_ref, temperature,
                  verified_at
                ) VALUES (
                  'provider-session-a', 'owner-a', 'org-a', 'workspace-a',
                  'openai-compatible', 'https://provider.invalid', 'model-a',
                  'credential-ref-a', 0.2, CURRENT_TIMESTAMP
                )
                """
            )
        )
    started = service.start(
        actor=actor,
        tenant=tenant,
        workspace_id="workspace-a",
        body={"plan_id": "plan-a", "provider_session_id": "provider-session-a"},
    )
    assert started["status"] == "READY"
    session_id = str(started["session_id"])
    provider_prompts: list[dict[str, object]] = []

    async def provider_turn(prompt: dict[str, object]) -> str:
        provider_prompts.append(prompt)
        return "先解释 lesson-a，再请学习者给出自己的例子。"

    restarted = EnterpriseTeachingService(
        transaction_manager=transactions,
        resource_repository=PostgresTenantResourceRepository(),
        provider_turn=provider_turn,
    )
    next_turn = asyncio.run(
        restarted.teach_next(
            actor=actor,
            tenant=tenant,
            session_id=session_id,
            body={},
        )
    )
    assert next_turn["status"] == "AWAITING_LEARNER"
    assert next_turn["state"]["teacher_turn"]["status"] == "PROVIDER_COMPLETED"
    assert "lesson-a" in next_turn["state"]["teacher_turn"]["content"]
    assert provider_prompts[0]["source_snapshot"] == {
        "plan_id": "plan-a",
        "lessons": ["lesson-a"],
    }
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM teacher_sessions WHERE resource_id = :id"),
            {"id": session_id},
        ) == 1
    engine.dispose()


def test_0033_postgresql_policy_is_operation_specific_and_never_grants_local_store() -> None:
    source = (
        BACKEND_ROOT
        / "alembic"
        / "versions"
        / "0033_enterprise_workspace_persistence.py"
    ).read_text(encoding="utf-8")
    assert "enterprise_workspaces_select" in source
    assert "enterprise_workspaces_insert" in source
    assert "enterprise_workspaces_update" in source
    assert "enterprise_workspaces_delete" in source
    assert "enterprise_workspace_memberships_select" in source
    assert "enterprise_workspace_memberships_insert" in source
    assert "workspaces.status = 'active'" in source
    assert "organization_memberships" in source
    assert "workspace_memberships" in source
    assert "current_setting('mentorflow.actor_subject', true)" in source
    assert 'REVOKE INSERT ON TABLE "orm_workspaces"' in source
    assert "GRANT INSERT ON TABLE \"orm_workspaces\"" not in source
    assert "BYPASSRLS" not in source.replace("NOBYPASSRLS", "")
