from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, inspect, text

from app.document_import_integration import DocumentBatchStartIn
from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    TenantContext,
    TenantMode,
)
from app.enterprise.jobs_v1.repository import WorkflowRepository
from app.enterprise.jobs_v1.schema import metadata as jobs_metadata
from app.enterprise_tenancy.asset_manifests import (
    ASSET_MANIFEST_CREATE_CONTRACT,
    ASSET_MANIFEST_READ_CONTRACT,
    EXPORT_MANIFEST_SCHEMA_VERSION,
    LOCAL_ASSET_REFERENCE_CONTRACT,
    MISSING_ASSET_STATE_CONTRACT,
    AssetManifestRegistrationIn,
    AssetManifestSelectionIn,
    EnterpriseAssetManifestService,
    validate_asset_references,
)
from app.enterprise_tenancy.context import TenantDbSession, TenantTransactionContext
from app.enterprise_tenancy.document_import import EnterpriseDocumentImportService
from app.enterprise_tenancy.models import EnterprisePersistenceRequestError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_REVISION = "0034_enterprise_asset_manifests"
PREVIOUS_REVISION = "0033_enterprise_workspace_persistence"


def _config(database: Path) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    config.attributes["configure_logger"] = False
    return config


class _SQLiteTransactions:
    def __init__(self, engine) -> None:
        self.engine = engine

    @contextmanager
    def transaction(self, context: TenantTransactionContext):
        with self.engine.begin() as connection:
            yield TenantDbSession(connection=connection, transaction_context=context)


def _context(
    *,
    organization: str = "org-a",
    workspace: str = "workspace-a",
    subject: str = "owner-a",
    issuer: str = "https://identity.example.test/realms/mentorflow",
) -> tuple[ActorContext, TenantContext]:
    actor = ActorContext(
        subject_id=subject,
        actor_kind=ActorKind.HUMAN,
        issuer=issuer,
        token_id=f"token-{subject}",
        session_id=f"session-{subject}",
        request_id=f"request-{subject}",
        authentication_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
        authentication_strength="mfa",
        organization_id=organization,
        workspace_id=workspace,
        membership_id=f"membership-{organization}-{workspace}-{subject}",
        roles=("OWNER",),
    )
    tenant = TenantContext(
        organization_id=organization,
        workspace_id=workspace,
        mode=TenantMode.ENTERPRISE_SERVER,
    )
    return actor, tenant


def _seed_identity(
    engine,
    *,
    organization: str,
    workspace: str,
    subjects: tuple[str, ...],
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations(id, slug, name, status) "
                "VALUES (:id, :id, :id, 'active')"
            ),
            {"id": organization},
        )
        connection.execute(
            text(
                "INSERT INTO workspaces(id, organization_id, slug, name, status) "
                "VALUES (:workspace, :organization, :workspace, :workspace, 'active')"
            ),
            {"workspace": workspace, "organization": organization},
        )
        for subject in subjects:
            parameters = {
                "organization": organization,
                "workspace": workspace,
                "subject": subject,
            }
            connection.execute(
                text(
                    "INSERT INTO organization_memberships("
                    "organization_id, subject_id, role, status"
                    ") VALUES (:organization, :subject, 'OWNER', 'active')"
                ),
                parameters,
            )
            connection.execute(
                text(
                    "INSERT INTO workspace_memberships("
                    "organization_id, workspace_id, subject_id, status"
                    ") VALUES (:organization, :workspace, :subject, 'active')"
                ),
                parameters,
            )
            connection.execute(
                text(
                    "INSERT INTO workspace_role_bindings("
                    "organization_id, workspace_id, subject_id, role, status"
                    ") VALUES (:organization, :workspace, :subject, 'OWNER', 'active')"
                ),
                parameters,
            )


def _database(tmp_path: Path):
    database = tmp_path / "enterprise-assets.sqlite3"
    command.upgrade(_config(database), "head")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}", future=True
    ).execution_options(schema_translate_map={"enterprise_jobs": None})
    jobs_metadata.create_all(engine)
    _seed_identity(
        engine,
        organization="org-a",
        workspace="workspace-a",
        subjects=("owner-a", "owner-a2"),
    )
    _seed_identity(
        engine,
        organization="org-b",
        workspace="workspace-b",
        subjects=("owner-b",),
    )
    return database, engine, _SQLiteTransactions(engine)


def _manifest(
    *,
    sha256: str = "a" * 64,
    availability: str = "AVAILABLE",
) -> AssetManifestRegistrationIn:
    return AssetManifestRegistrationIn(
        local_asset_id="vault-asset-lesson-1",
        filename="lesson.pdf",
        mime_type="application/pdf",
        size_bytes=1_024,
        sha256=sha256,
        import_mode="MANAGED",
        portability="PORTABLE",
        verified_source_url="https://materials.example.test/lesson.pdf",
        derived_artifact_manifest={
            "evidence_manifest_id": "evidence-manifest-1",
            "knowledge_anchor_ids": ["anchor-1"],
        },
        availability=availability,
    )


def _select(service, actor, tenant, manifest):
    return asyncio.run(
        service.select(
            actor=actor,
            tenant=tenant,
            body=AssetManifestSelectionIn(assets=(manifest,)),
        )
    )


def test_0034_is_single_head_and_supports_fresh_incremental_downgrade(
    tmp_path: Path,
) -> None:
    scripts = ScriptDirectory.from_config(_config(tmp_path / "unused.sqlite3"))
    assert scripts.get_heads() == [MIGRATION_REVISION]
    assert scripts.get_revision(MIGRATION_REVISION).down_revision == PREVIOUS_REVISION

    fresh = tmp_path / "fresh.sqlite3"
    command.upgrade(_config(fresh), "head")
    with sqlite3.connect(fresh) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"asset_manifests", "asset_revisions"} <= tables
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
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "asset_manifests" not in tables
        assert "asset_revisions" not in tables
        assert connection.execute(
            "SELECT name FROM workspaces WHERE id = 'workspace-old'"
        ).fetchone() == ("Old",)


def test_manifest_contract_requires_ids_hashes_and_rejects_content_paths_secrets() -> None:
    assert ASSET_MANIFEST_CREATE_CONTRACT == "mentorflow.asset-manifest.create.v1"
    assert ASSET_MANIFEST_READ_CONTRACT == "mentorflow.asset-manifest.read.v1"
    assert LOCAL_ASSET_REFERENCE_CONTRACT == "mentorflow.local-asset-reference.v1"
    assert MISSING_ASSET_STATE_CONTRACT == "mentorflow.missing-asset-state.v1"
    assert EXPORT_MANIFEST_SCHEMA_VERSION == "mentorflow.asset-manifest-export.v1"

    valid = _manifest().model_dump(mode="python")
    for missing in ("local_asset_id", "sha256"):
        invalid = dict(valid)
        invalid.pop(missing)
        with pytest.raises(ValidationError):
            AssetManifestRegistrationIn.model_validate(invalid)

    invalid_cases = (
        {**valid, "local_asset_id": r"C:\Users\owner\lesson.pdf"},
        {**valid, "filename": r"C:\Users\owner\lesson.pdf"},
        {**valid, "verified_source_url": "/home/owner/lesson.pdf"},
        {**valid, "verified_source_url": "file:///C:/Users/owner/lesson.pdf"},
        {
            **valid,
            "verified_source_url": "https://example.test/file?access_token=secret",
        },
        {**valid, "derived_artifact_manifest": {"raw_document_bytes": "AA=="}},
        {**valid, "derived_artifact_manifest": {"document_payload": "AA=="}},
        {
            **valid,
            "derived_artifact_manifest": {"source_location": "/home/owner/lesson.pdf"},
        },
        {**valid, "derived_artifact_manifest": {"embedding_cache": [1, 2]}},
        {**valid, "derived_artifact_manifest": {"score": float("nan")}},
        {**valid, "raw_file_bytes": b"must-not-cross-server-boundary"},
    )
    for invalid in invalid_cases:
        with pytest.raises(ValidationError):
            AssetManifestRegistrationIn.model_validate(invalid)


def test_manifest_revisions_are_idempotent_pathless_and_job_handoff_is_reference_only(
    tmp_path: Path,
) -> None:
    _database_path, engine, transactions = _database(tmp_path)
    actor, tenant = _context()
    service = EnterpriseDocumentImportService(transaction_manager=transactions)
    try:
        first = _select(service, actor, tenant, _manifest())
        duplicate = _select(service, actor, tenant, _manifest())
        assert duplicate["asset_ids"] == first["asset_ids"]
        assert duplicate["asset_revision_ids"] == first["asset_revision_ids"]
        selection_id = str(first["selection_id"])
        revision_id = str(first["asset_revision_ids"][0])
        references = service.resolve_selection_references(
            actor=actor,
            tenant=tenant,
            selection_id=selection_id,
            document_ids=[revision_id],
        )
        assert validate_asset_references(references) == references
        assert all("path" not in key.casefold() for key in references[0])

        receipt = service.start_batch(
            actor=actor,
            tenant=tenant,
            selection_id=selection_id,
            body=DocumentBatchStartIn(conversion_batch_size=1),
        )
        with engine.connect() as connection:
            job = WorkflowRepository().get(connection, str(receipt["job_id"]))
            assert job is not None
            request = job.payload["request"]
            assert request["asset_manifest_references"] == list(references)
            assert "selected_documents" not in request
            encoded = json.dumps(job.payload, sort_keys=True)
            assert "C:" not in encoded
            assert "file://" not in encoded.casefold()
            assert connection.scalar(text("SELECT count(*) FROM asset_manifests")) == 1
            assert connection.scalar(text("SELECT count(*) FROM asset_revisions")) == 1
            scopes = connection.execute(
                text(
                    "SELECT scope_type, owner_subject_id FROM resource_scopes "
                    "WHERE resource_id IN (:asset_id, :selection_id)"
                ),
                {
                    "asset_id": first["asset_ids"][0],
                    "selection_id": first["selection_id"],
                },
            ).all()
            assert scopes == [("PERSONAL", actor.subject_id)] * 2

        changed = _select(service, actor, tenant, _manifest(sha256="b" * 64))
        assert changed["asset_ids"] == first["asset_ids"]
        assert changed["asset_revision_ids"] != first["asset_revision_ids"]
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM asset_manifests")) == 1
            assert connection.scalar(text("SELECT count(*) FROM asset_revisions")) == 2

        with pytest.raises(EnterprisePersistenceRequestError) as stale:
            service.preflight(
                actor=actor,
                tenant=tenant,
                selection_id=selection_id,
            )
        assert stale.value.status == 404
    finally:
        engine.dispose()


def test_cross_tenant_and_cross_subject_access_fail_closed_and_missing_preserves_metadata(
    tmp_path: Path,
) -> None:
    _database_path, engine, transactions = _database(tmp_path)
    actor, tenant = _context()
    document_service = EnterpriseDocumentImportService(transaction_manager=transactions)
    manifests = EnterpriseAssetManifestService(transactions)
    try:
        selected = _select(document_service, actor, tenant, _manifest())
        asset_id = str(selected["asset_ids"][0])
        selection_id = str(selected["selection_id"])
        revision_id = str(selected["asset_revision_ids"][0])

        missing = manifests.set_missing(
            actor=actor,
            tenant=tenant,
            asset_id=asset_id,
        )
        assert missing["availability"] == "MISSING"
        assert missing["missing_state_contract"] == MISSING_ASSET_STATE_CONTRACT
        assert missing["derived_artifact_manifest"]["knowledge_anchor_ids"] == [
            "anchor-1"
        ]
        assert document_service.preflight(
            actor=actor,
            tenant=tenant,
            selection_id=selection_id,
        )["ready"] is False
        with pytest.raises(EnterprisePersistenceRequestError) as unavailable:
            document_service.resolve_selection_references(
                actor=actor,
                tenant=tenant,
                selection_id=selection_id,
                document_ids=[revision_id],
            )
        assert unavailable.value.code == "LOCAL_ASSET_MISSING"

        other_actor, other_tenant = _context(
            organization="org-b",
            workspace="workspace-b",
            subject="owner-b",
        )
        assert manifests.list(actor=other_actor, tenant=other_tenant)["assets"] == []
        with pytest.raises(EnterprisePersistenceRequestError) as cross_tenant:
            manifests.get(
                actor=other_actor,
                tenant=other_tenant,
                asset_id=asset_id,
            )
        assert cross_tenant.value.status == 404
        with pytest.raises(EnterprisePersistenceRequestError) as cross_delete:
            manifests.tombstone(
                actor=other_actor,
                tenant=other_tenant,
                asset_id=asset_id,
            )
        assert cross_delete.value.status == 404

        other_subject, same_tenant = _context(subject="owner-a2")
        assert manifests.list(actor=other_subject, tenant=same_tenant)["assets"] == []
        with pytest.raises(EnterprisePersistenceRequestError) as cross_subject:
            manifests.set_missing(
                actor=other_subject,
                tenant=same_tenant,
                asset_id=asset_id,
            )
        assert cross_subject.value.status == 404

        other_issuer, same_identity_tenant = _context(
            issuer="https://other-identity.example.test/realms/mentorflow"
        )
        assert manifests.list(
            actor=other_issuer,
            tenant=same_identity_tenant,
        )["assets"] == []
        with pytest.raises(EnterprisePersistenceRequestError) as cross_issuer:
            manifests.get(
                actor=other_issuer,
                tenant=same_identity_tenant,
                asset_id=asset_id,
            )
        assert cross_issuer.value.status == 404

        tombstone = manifests.tombstone(
            actor=actor,
            tenant=tenant,
            asset_id=asset_id,
        )
        assert tombstone["availability"] == "DELETED"
        assert manifests.list(actor=actor, tenant=tenant)["assets"] == []
        exported = manifests.export_manifest(actor=actor, tenant=tenant)
        assert exported["schema_version"] == EXPORT_MANIFEST_SCHEMA_VERSION
        assert exported["assets"][0]["availability"] == "DELETED"
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM asset_manifests")) == 1
            assert connection.scalar(text("SELECT count(*) FROM asset_revisions")) == 1
            derived = connection.execute(
                text(
                    "SELECT derived_artifact_json FROM asset_revisions "
                    "WHERE asset_revision_id = :revision"
                ),
                {"revision": revision_id},
            ).scalar_one()
            assert "anchor-1" in str(derived)
    finally:
        engine.dispose()


def test_server_boundary_and_postgresql_rls_contract_are_exact() -> None:
    source_paths = (
        BACKEND_ROOT / "app" / "enterprise_tenancy" / "document_import.py",
        BACKEND_ROOT / "app" / "enterprise" / "product_bridge.py",
        BACKEND_ROOT / "app" / "enterprise" / "jobs_v1" / "learning_runs.py",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)
    for forbidden in (
        "UploadFile",
        "write_bytes(",
        "storage_relative_path",
        "runtime_root",
        "selected_documents",
        "MENTORFLOW_DOCUMENT_IMPORT_ROOT",
    ):
        assert forbidden not in combined

    migration = (
        BACKEND_ROOT
        / "alembic"
        / "versions"
        / "0034_enterprise_asset_manifests.py"
    ).read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "mentorflow.actor_subject" in migration
    assert "mentorflow.actor_issuer" in migration
    assert "NOSUPERUSER" in migration
    assert "NOBYPASSRLS" in migration
    assert 'GRANT {privileges} ON TABLE "{table}"' in migration
    assert '"SELECT, INSERT, UPDATE"' in migration
    assert 'else "SELECT, INSERT"' in migration
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" not in migration
    assert "GRANT ON ALL" not in migration
    assert "GRANT ALL" not in migration
