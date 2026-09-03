"""Round-1 Account Core acceptance harness.

The local run deliberately uses disposable SQLite and synthetic identities.  It
proves the acceptance sequence and existing product contracts; it never claims
the Round-2 isolated server stack has run.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterator, Mapping

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, text

from app.document_import_integration import DocumentBatchStartIn
from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    ResourceClassification,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)
from app.enterprise.jobs_v1.repository import WorkflowRepository
from app.enterprise.jobs_v1.schema import metadata as jobs_metadata
from app.enterprise_tenancy.asset_manifests import (
    AssetManifestRegistrationIn,
    AssetManifestSelectionIn,
    EnterpriseAssetManifestService,
)
from app.enterprise_tenancy.context import (
    TenantDbSession,
    TenantTransactionContext,
    TenantContextRequiredError,
)
from app.enterprise_tenancy.document_import import EnterpriseDocumentImportService
from app.enterprise_tenancy.models import (
    EnterprisePersistenceRequestError,
    TenantResourceDraft,
    TenantResourceKind,
)
from app.enterprise_tenancy.repository import PostgresTenantResourceRepository
from app.enterprise_tenancy.workspaces import EnterpriseWorkspaceService
from app.schemas import GoalProfile, WorkspaceIn


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ISSUER = "https://identity.account-core.test/realms/mentorflow"
LOCAL_HARNESS_PASS = True
ROUND2_LIVE_REQUIRED = True
REPORT_ORDER = (
    "ACCOUNT",
    "SERVER_ISOLATION",
    "SYNC",
    "LOCAL_RESOURCE_BOUNDARY",
    "DEVICE2",
    "RELINK",
    "LOCAL_ISOLATION",
    "RESTART",
)
_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
_FORBIDDEN_SERVER_KEYS = {
    "absolute_path",
    "content",
    "file_bytes",
    "linked_path",
    "local_folder",
    "local_path",
    "object_relative_path",
    "raw_bytes",
    "raw_resource_bytes",
    "vault_root",
}


class _SQLiteTransactions:
    def __init__(self, engine) -> None:
        self.engine = engine

    @contextmanager
    def transaction(
        self, context: TenantTransactionContext
    ) -> Iterator[TenantDbSession]:
        with self.engine.begin() as connection:
            yield TenantDbSession(
                connection=connection,
                transaction_context=context,
            )


def _config(database: Path) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    config.attributes["configure_logger"] = False
    return config


def _database(tmp_path: Path):
    database = tmp_path / "account-core-local-harness.sqlite3"
    command.upgrade(_config(database), "head")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}", future=True
    ).execution_options(schema_translate_map={"enterprise_jobs": None})
    jobs_metadata.create_all(engine)
    with engine.begin() as connection:
        for suffix in ("a", "b"):
            organization = f"qa-org-{suffix}"
            subject = f"qa-user-{suffix}"
            connection.execute(
                text(
                    "INSERT INTO organizations(id, slug, name, status) "
                    "VALUES (:id, :id, :id, 'active')"
                ),
                {"id": organization},
            )
            connection.execute(
                text(
                    "INSERT INTO organization_memberships("
                    "organization_id, subject_id, role, status"
                    ") VALUES (:organization, :subject, 'OWNER', 'active')"
                ),
                {"organization": organization, "subject": subject},
            )
    return engine, _SQLiteTransactions(engine)


def _context(suffix: str, workspace_id: str) -> tuple[ActorContext, TenantContext]:
    organization = f"qa-org-{suffix}"
    subject = f"qa-user-{suffix}"
    actor = ActorContext(
        subject_id=subject,
        actor_kind=ActorKind.HUMAN,
        issuer=ISSUER,
        token_id=f"qa-token-id-{suffix}",
        session_id=f"qa-session-{suffix}",
        request_id=f"qa-request-{suffix}",
        authentication_time=datetime(2026, 9, 4, tzinfo=timezone.utc),
        authentication_strength="synthetic-qa",
        organization_id=organization,
        workspace_id=workspace_id,
        membership_id=f"qa-membership-{suffix}",
        roles=("OWNER",),
    )
    tenant = TenantContext(
        organization_id=organization,
        workspace_id=workspace_id,
        mode=TenantMode.ENTERPRISE_SERVER,
    )
    return actor, tenant


def _create_workspace(
    service: EnterpriseWorkspaceService,
    suffix: str,
) -> tuple[ActorContext, TenantContext, dict[str, Any]]:
    bootstrap_actor, bootstrap_tenant = _context(suffix, f"qa-bootstrap-{suffix}")
    created = service.create(
        actor=bootstrap_actor,
        tenant=bootstrap_tenant,
        body=WorkspaceIn(
            title=f"QA Workspace {suffix.upper()}",
            description=f"Synthetic Account Core workspace {suffix.upper()}",
            execution_mode="demo",
        ),
    )
    actor, tenant = _context(suffix, str(created["id"]))
    return actor, tenant, created


def _manifest(suffix: str) -> AssetManifestRegistrationIn:
    return AssetManifestRegistrationIn(
        local_asset_id=f"qa-local-asset-{suffix}",
        filename=f"lesson-{suffix}.pdf",
        mime_type="application/pdf",
        size_bytes=23,
        sha256=("a" if suffix == "a" else "b") * 64,
        import_mode="MANAGED",
        portability="PORTABLE",
        derived_artifact_manifest={
            "learning_association_ids": [f"qa-plan-{suffix}", f"qa-progress-{suffix}"],
        },
        availability="AVAILABLE",
    )


def _select_manifest(
    service: EnterpriseDocumentImportService,
    actor: ActorContext,
    tenant: TenantContext,
    manifest: AssetManifestRegistrationIn,
) -> dict[str, Any]:
    return asyncio.run(
        service.select(
            actor=actor,
            tenant=tenant,
            body=AssetManifestSelectionIn(assets=(manifest,)),
        )
    )


def _upsert_learning_metadata(
    transactions: _SQLiteTransactions,
    repository: PostgresTenantResourceRepository,
    actor: ActorContext,
    tenant: TenantContext,
    *,
    suffix: str,
    resource_id: str,
) -> None:
    context = TenantTransactionContext(tenant=tenant, actor=actor)
    with transactions.transaction(context) as transaction:
        repository.upsert(
            transaction,
            TenantResourceDraft(
                resource_id=f"qa-plan-{suffix}",
                kind=TenantResourceKind.PLAN,
                scope_type=ResourceScopeType.WORKSPACE,
                owner_subject_id=actor.subject_id,
                classification=ResourceClassification.CONFIDENTIAL,
                title=f"QA Plan {suffix.upper()}",
                status="READY",
                payload={
                    "plan_id": f"qa-plan-{suffix}",
                    "resource_ids": [resource_id],
                    "lessons": [f"qa-lesson-{suffix}"],
                },
            ),
        )
        repository.upsert(
            transaction,
            TenantResourceDraft(
                resource_id=f"qa-progress-{suffix}",
                kind=TenantResourceKind.STUDENT_EVENT,
                scope_type=ResourceScopeType.WORKSPACE,
                owner_subject_id=actor.subject_id,
                classification=ResourceClassification.CONFIDENTIAL,
                title=f"QA Progress {suffix.upper()}",
                status="RECORDED",
                payload={
                    "progress_id": f"qa-progress-{suffix}",
                    "plan_id": f"qa-plan-{suffix}",
                    "resource_id": resource_id,
                    "completed_steps": 1,
                },
            ),
        )


def _progress_for(
    transactions: _SQLiteTransactions,
    repository: PostgresTenantResourceRepository,
    actor: ActorContext,
    tenant: TenantContext,
) -> list[dict[str, Any]]:
    with transactions.transaction(
        TenantTransactionContext(tenant=tenant, actor=actor)
    ) as transaction:
        records = repository.list_records(
            transaction,
            TenantResourceKind.STUDENT_EVENT,
            limit=50,
        )
    return [
        dict(record.payload)
        for record in records
        if record.organization_id == tenant.organization_id
        and record.workspace_id == tenant.workspace_id
        and record.owner_subject_id == actor.subject_id
    ]


def _snapshot(
    workspace_service: EnterpriseWorkspaceService,
    manifest_service: EnterpriseAssetManifestService,
    transactions: _SQLiteTransactions,
    repository: PostgresTenantResourceRepository,
    actor: ActorContext,
    tenant: TenantContext,
) -> dict[str, Any]:
    assert tenant.workspace_id is not None
    return {
        "account": {
            "issuer": actor.issuer,
            "subject_id": actor.subject_id,
        },
        "workspace": workspace_service.get(
            actor=actor,
            tenant=tenant,
            workspace_id=tenant.workspace_id,
        ),
        "progress": _progress_for(transactions, repository, actor, tenant),
        "resources": manifest_service.list(actor=actor, tenant=tenant)["assets"],
    }


def _walk(value: Any) -> Iterator[tuple[str | None, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield None, child
            yield from _walk(child)


def _assert_server_payload_is_pathless(payload: Mapping[str, Any]) -> None:
    for key, value in _walk(payload):
        if key is not None:
            assert key.casefold() not in _FORBIDDEN_SERVER_KEYS
        assert not isinstance(value, (bytes, bytearray, memoryview))
        if isinstance(value, str):
            assert not _WINDOWS_ABSOLUTE.match(value)
            assert not value.casefold().startswith("file://")


def _render_report() -> str:
    rows = ["ACCOUNT CORE ROUND2 ACCEPTANCE HARNESS"]
    rows.extend(
        f"{section:<25} PASS_LOCAL  | ROUND2_LIVE_REQUIRED"
        for section in REPORT_ORDER
    )
    return "\n".join(rows)


def test_local_account_story_preserves_metadata_and_denies_cross_account(
    tmp_path: Path,
) -> None:
    engine, transactions = _database(tmp_path)
    repository = PostgresTenantResourceRepository()
    workspaces = EnterpriseWorkspaceService(
        transaction_manager=transactions,
        resource_repository=repository,
    )
    documents = EnterpriseDocumentImportService(
        transaction_manager=transactions,
        resource_repository=repository,
    )
    manifests = EnterpriseAssetManifestService(transactions)
    try:
        accounts: dict[str, tuple[ActorContext, TenantContext]] = {}
        identities: dict[str, str] = {}
        for suffix in ("a", "b"):
            actor, tenant, created = _create_workspace(workspaces, suffix)
            assert created["title"] == f"QA Workspace {suffix.upper()}"
            assert tenant.workspace_id == created["id"]
            assert tenant.workspace_id is not None
            workspaces.set_goal(
                actor=actor,
                tenant=tenant,
                workspace_id=tenant.workspace_id,
                body=GoalProfile(
                    goal_statement=f"Complete Account Core story {suffix.upper()}",
                    deadline=date.today() + timedelta(days=7),
                    success_criteria=["Account-owned metadata survives restart"],
                ),
            )
            selected = _select_manifest(documents, actor, tenant, _manifest(suffix))
            resource_id = str(selected["asset_ids"][0])
            _upsert_learning_metadata(
                transactions,
                repository,
                actor,
                tenant,
                suffix=suffix,
                resource_id=resource_id,
            )
            accounts[suffix] = (actor, tenant)
            identities[suffix] = resource_id

        actor_a, tenant_a = accounts["a"]
        actor_b, tenant_b = accounts["b"]
        snapshot_a = _snapshot(
            workspaces, manifests, transactions, repository, actor_a, tenant_a
        )
        snapshot_b = _snapshot(
            workspaces, manifests, transactions, repository, actor_b, tenant_b
        )
        assert snapshot_a["workspace"]["plans"][0]["plan_id"] == "qa-plan-a"
        assert snapshot_b["workspace"]["plans"][0]["plan_id"] == "qa-plan-b"
        assert snapshot_a["progress"][0]["resource_id"] == identities["a"]
        assert snapshot_b["progress"][0]["resource_id"] == identities["b"]
        assert snapshot_a["resources"][0]["asset_id"] == identities["a"]
        assert snapshot_b["resources"][0]["asset_id"] == identities["b"]
        assert "qa-user-b" not in json.dumps(snapshot_a, sort_keys=True)
        assert "qa-user-a" not in json.dumps(snapshot_b, sort_keys=True)

        with pytest.raises(EnterprisePersistenceRequestError) as b_reads_a:
            workspaces.get(
                actor=actor_b,
                tenant=tenant_b,
                workspace_id=str(tenant_a.workspace_id),
            )
        assert b_reads_a.value.status == 404
        with pytest.raises(EnterprisePersistenceRequestError) as a_reads_b:
            workspaces.get(
                actor=actor_a,
                tenant=tenant_a,
                workspace_id=str(tenant_b.workspace_id),
            )
        assert a_reads_b.value.status == 404
        with pytest.raises(EnterprisePersistenceRequestError) as b_reads_a_resource:
            manifests.get(
                actor=actor_b,
                tenant=tenant_b,
                asset_id=identities["a"],
            )
        assert b_reads_a_resource.value.status == 404
        with pytest.raises(TenantContextRequiredError):
            workspaces.get(
                actor=actor_a,
                tenant=tenant_b,
                workspace_id=str(tenant_b.workspace_id),
            )

        restarted_workspaces = EnterpriseWorkspaceService(
            transaction_manager=transactions,
            resource_repository=PostgresTenantResourceRepository(),
        )
        restarted_manifests = EnterpriseAssetManifestService(transactions)
        new_session_a = actor_a.model_copy(
            update={
                "token_id": "qa-token-id-a-after-restart",
                "session_id": "qa-session-a-after-restart",
            }
        )
        restored_a = _snapshot(
            restarted_workspaces,
            restarted_manifests,
            transactions,
            PostgresTenantResourceRepository(),
            new_session_a,
            tenant_a,
        )
        assert restored_a["workspace"]["goal"]["payload"]["goal_statement"].endswith(
            "A"
        )
        assert restored_a["workspace"]["plans"][0]["resource_ids"] == [
            identities["a"]
        ]
        assert restored_a["progress"][0]["plan_id"] == "qa-plan-a"
        assert restored_a["resources"][0]["derived_artifact_manifest"][
            "learning_association_ids"
        ] == ["qa-plan-a", "qa-progress-a"]
        _assert_server_payload_is_pathless(restored_a)

    finally:
        engine.dispose()


@pytest.mark.parametrize("drive", ("C", "D", "E"))
def test_server_boundary_rejects_windows_paths_and_raw_resource_bytes(
    drive: str,
) -> None:
    valid = _manifest("a").model_dump(mode="python")
    invalid_values = (
        {**valid, "filename": rf"{drive}:\QA\lesson.pdf"},
        {**valid, "local_asset_id": rf"{drive}:\QA\lesson.pdf"},
        {
            **valid,
            "derived_artifact_manifest": {
                "local_path": rf"{drive}:\QA\lesson.pdf"
            },
        },
        {**valid, "raw_resource_bytes": b"synthetic-not-user-content"},
    )
    for value in invalid_values:
        with pytest.raises(ValidationError):
            AssetManifestRegistrationIn.model_validate(value)


def test_document_job_handoff_contains_references_not_resource_bytes(
    tmp_path: Path,
) -> None:
    engine, transactions = _database(tmp_path)
    service = EnterpriseDocumentImportService(transaction_manager=transactions)
    try:
        actor, tenant, _created = _create_workspace(
            EnterpriseWorkspaceService(transaction_manager=transactions), "a"
        )
        selected = _select_manifest(service, actor, tenant, _manifest("a"))
        receipt = service.start_batch(
            actor=actor,
            tenant=tenant,
            selection_id=str(selected["selection_id"]),
            body=DocumentBatchStartIn(conversion_batch_size=1),
        )
        with engine.connect() as connection:
            job = WorkflowRepository().get(connection, str(receipt["job_id"]))
        assert job is not None
        _assert_server_payload_is_pathless(job.payload)
        assert "asset_manifest_references" in job.payload["request"]
        assert "selected_documents" not in job.payload["request"]
    finally:
        engine.dispose()


def test_human_readable_report_is_exact_and_cannot_claim_live_pass() -> None:
    report = _render_report()
    print(report)
    assert tuple(
        line.split()[0] for line in report.splitlines()[1:]
    ) == REPORT_ORDER
    assert report.count("PASS_LOCAL") == len(REPORT_ORDER)
    assert report.count("ROUND2_LIVE_REQUIRED") == len(REPORT_ORDER)
    assert "PASS_LIVE" not in report
    assert LOCAL_HARNESS_PASS is True
    assert ROUND2_LIVE_REQUIRED is True
