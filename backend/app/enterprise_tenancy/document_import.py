"""Manifest-only Enterprise document selection and durable batch facade."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from typing import Any

from app.document_import_integration import (
    DOCUMENT_IMPORT_MAX_FILES,
    DocumentBatchStartIn,
)
from app.enterprise.contracts_v1 import (
    ActorContext,
    ResourceClassification,
    ResourceScopeType,
    TenantContext,
)
from app.enterprise.jobs_v1.models import JobRequest, JobStatus, WorkflowKind
from app.enterprise.jobs_v1.repository import WorkflowRepository

from .asset_manifests import (
    ASSET_MANIFEST_CREATE_CONTRACT,
    ASSET_MANIFEST_SCHEMA_VERSION,
    AssetManifestRecord,
    AssetManifestRepository,
    AssetManifestSelectionIn,
)
from .context import PostgresTenantTransactionManager, TenantTransactionContext
from .models import (
    EnterprisePersistenceRequestError,
    TenantResourceDraft,
    TenantResourceKind,
)
from .repository import PostgresTenantResourceRepository


def _error(code: str, message: str, status: int) -> EnterprisePersistenceRequestError:
    return EnterprisePersistenceRequestError(code, message, status=status)


def _selection_id() -> str:
    return f"selection_{uuid.uuid4().hex}"


class EnterpriseDocumentImportService:
    """Registers Desktop vault manifests without receiving or storing raw bytes."""

    def __init__(
        self,
        engine=None,
        *,
        transaction_manager=None,
        resource_repository: PostgresTenantResourceRepository | None = None,
        asset_repository: AssetManifestRepository | None = None,
        workflow_repository: WorkflowRepository | None = None,
    ) -> None:
        if transaction_manager is None:
            if engine is None:
                raise ValueError("engine or transaction_manager is required")
            transaction_manager = PostgresTenantTransactionManager(engine)
        self.transactions = transaction_manager
        self.resources = resource_repository or PostgresTenantResourceRepository()
        self.assets = asset_repository or AssetManifestRepository()
        self.workflows = workflow_repository or WorkflowRepository()

    @staticmethod
    def _context(actor: ActorContext, tenant: TenantContext) -> TenantTransactionContext:
        return TenantTransactionContext(tenant=tenant, actor=actor)

    @staticmethod
    def _projection(record: AssetManifestRecord, selection_id: str) -> dict[str, Any]:
        return {
            "schema_version": ASSET_MANIFEST_SCHEMA_VERSION,
            "record_type": "asset_manifest_projection",
            "selection_id": selection_id,
            "asset_id": record.asset_id,
            "asset_revision_id": record.asset_revision_id,
            "local_asset_id": record.local_asset_id,
            "device_id": record.device_id,
            "filename": record.filename,
            "mime_type": record.mime_type,
            "size_bytes": record.size_bytes,
            "sha256": record.sha256,
            "source_kind": record.source_kind,
            "import_mode": record.import_mode,
            "portability": record.portability,
            "verified_source_url": record.verified_source_url,
            "availability": record.availability,
            "derived_artifact_manifest": dict(record.derived_artifact_manifest),
        }

    async def select(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        body: AssetManifestSelectionIn,
    ) -> dict[str, Any]:
        selection_id = _selection_id()
        context = self._context(actor, tenant)
        with self.transactions.transaction(context) as transaction:
            records: list[AssetManifestRecord] = []
            for manifest in body.assets:
                record = self.assets.upsert(transaction, manifest)
                records.append(record)
                self.resources.upsert(
                    transaction,
                    TenantResourceDraft(
                        resource_id=record.asset_id,
                        kind=TenantResourceKind.DOCUMENT,
                        scope_type=ResourceScopeType.PERSONAL,
                        owner_subject_id=actor.subject_id,
                        classification=ResourceClassification.CONFIDENTIAL,
                        title=record.filename,
                        status=record.availability,
                        payload=self._projection(record, selection_id),
                        search_text=f"{record.filename} {record.mime_type}",
                    ),
                )
            asset_ids = [record.asset_id for record in records]
            revision_ids = [record.asset_revision_id for record in records]
            self.resources.upsert(
                transaction,
                TenantResourceDraft(
                    resource_id=selection_id,
                    kind=TenantResourceKind.DOCUMENT,
                    scope_type=ResourceScopeType.PERSONAL,
                    owner_subject_id=actor.subject_id,
                    classification=ResourceClassification.CONFIDENTIAL,
                    title=f"Asset manifest selection ({len(records)})",
                    status="SELECTED",
                    payload={
                        "schema_version": ASSET_MANIFEST_CREATE_CONTRACT,
                        "record_type": "asset_manifest_selection",
                        "asset_ids": asset_ids,
                        "asset_revision_ids": revision_ids,
                    },
                ),
            )
        return {
            "schema_version": ASSET_MANIFEST_CREATE_CONTRACT,
            "selection_id": selection_id,
            "asset_ids": asset_ids,
            "asset_revision_ids": revision_ids,
            # The durable learning API still names this collection document_ids;
            # its values are opaque asset revision ids, never paths.
            "document_ids": revision_ids,
            "assets": [record.public_contract() for record in records],
            "status": "selected",
        }

    def _selection(self, transaction, selection_id: str):
        selection = self.resources.get(
            transaction, TenantResourceKind.DOCUMENT, selection_id
        )
        if (
            selection is None
            or selection.organization_id != transaction.tenant.organization_id
            or selection.workspace_id != transaction.tenant.workspace_id
            or selection.owner_subject_id != transaction.actor.subject_id
            or selection.payload.get("record_type") != "asset_manifest_selection"
        ):
            raise _error(
                "DOCUMENT_SELECTION_NOT_FOUND",
                "Document selection was not found in the selected tenant context",
                404,
            )
        return selection

    def _records(
        self,
        transaction,
        *,
        selection_id: str,
        revision_ids: Sequence[object],
    ) -> tuple[AssetManifestRecord, ...]:
        normalized = tuple(str(value or "").strip() for value in revision_ids)
        selection = self._selection(transaction, selection_id)
        selected = tuple(selection.payload.get("asset_revision_ids") or ())
        if (
            not normalized
            or len(normalized) > DOCUMENT_IMPORT_MAX_FILES
            or any(not value for value in normalized)
            or len(set(normalized)) != len(normalized)
            or any(value not in selected for value in normalized)
        ):
            raise _error(
                "DOCUMENT_SELECTION_INVALID",
                "Asset revision identifiers must be a selected unique collection",
                422,
            )
        records: list[AssetManifestRecord] = []
        for revision_id in normalized:
            record = self.assets.get_current_revision(transaction, revision_id)
            if record is None or record.availability == "DELETED":
                raise _error(
                    "DOCUMENT_SELECTION_NOT_FOUND",
                    "A selected asset revision is unavailable in the tenant context",
                    404,
                )
            records.append(record)
        return tuple(records)

    def preflight(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        selection_id: str,
    ) -> dict[str, Any]:
        context = self._context(actor, tenant)
        with self.transactions.transaction(context) as transaction:
            selection = self._selection(transaction, selection_id)
            records = self._records(
                transaction,
                selection_id=selection_id,
                revision_ids=selection.payload.get("asset_revision_ids") or (),
            )
            assets = [record.public_contract() for record in records]
            ready = all(record.availability == "AVAILABLE" for record in records)
        return {
            "selection_id": selection_id,
            "status": "ready" if ready else "missing_assets",
            "ready": ready,
            "assets": assets,
            "warnings": (
                []
                if ready
                else ["One or more local account-vault assets are unavailable"]
            ),
        }

    def resolve_selection_references(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        selection_id: str,
        document_ids: Sequence[object],
    ) -> tuple[dict[str, Any], ...]:
        context = self._context(actor, tenant)
        with self.transactions.transaction(context) as transaction:
            records = self._records(
                transaction,
                selection_id=selection_id,
                revision_ids=document_ids,
            )
            missing = [
                record.asset_revision_id
                for record in records
                if record.availability != "AVAILABLE"
            ]
            if missing:
                raise _error(
                    "LOCAL_ASSET_MISSING",
                    "A selected local asset is unavailable; metadata was preserved",
                    409,
                )
            return tuple(record.local_reference() for record in records)

    def start_batch(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        selection_id: str,
        body: DocumentBatchStartIn,
    ) -> dict[str, Any]:
        context = self._context(actor, tenant)
        with self.transactions.transaction(context) as transaction:
            selection = self._selection(transaction, selection_id)
            selected_ids = body.selected_file_ids or list(
                selection.payload.get("asset_revision_ids") or ()
            )
            records = self._records(
                transaction,
                selection_id=selection_id,
                revision_ids=selected_ids,
            )
            missing = [
                record.asset_revision_id
                for record in records
                if record.availability != "AVAILABLE"
            ]
            if missing:
                raise _error(
                    "LOCAL_ASSET_MISSING",
                    "A selected local asset is unavailable; metadata was preserved",
                    409,
                )
            references = [record.local_reference() for record in records]
            fingerprint = hashlib.sha256(
                (
                    "\0".join(
                        (
                            *[record.asset_revision_id for record in records],
                            str(body.conversion_batch_size),
                        )
                    )
                ).encode("utf-8")
            ).hexdigest()
            trace_id = f"document-batch:{fingerprint}"
            request = JobRequest(
                workflow_kind=WorkflowKind.DOCUMENT_INGESTION,
                payload={
                    "organization_id": tenant.organization_id,
                    "workspace_id": tenant.workspace_id,
                    "document_batch": {
                        "selection_id": selection_id,
                        "asset_revision_ids": [
                            record.asset_revision_id for record in records
                        ],
                    },
                    "request": {
                        "request_id": actor.request_id,
                        "asset_manifest_references": references,
                        "conversion_config_identity": (
                            "enterprise-asset-manifest-batch-v1"
                        ),
                        "conversion_batch_size": body.conversion_batch_size,
                        "metadata_by_asset_revision": {},
                    },
                },
                actor_context=actor,
                tenant_context=tenant,
                request_id=actor.request_id,
                trace_id=trace_id,
                idempotency_key=f"document-batch:{selection_id}:{fingerprint}",
            )
            receipt = self.workflows.submit(transaction.connection, request)
        return {
            "batch_id": receipt.job_id,
            "selection_id": selection_id,
            "job_id": receipt.job_id,
            "status": receipt.status.value,
            "duplicate": receipt.duplicate,
        }

    def _batch(self, transaction, batch_id: str):
        job = self.workflows.get(transaction.connection, batch_id)
        document_batch = (
            job.payload.get("document_batch")
            if job is not None and isinstance(job.payload, dict)
            else None
        )
        if (
            job is None
            or job.workflow_kind is not WorkflowKind.DOCUMENT_INGESTION
            or job.tenant_context.organization_id
            != transaction.tenant.organization_id
            or job.tenant_context.workspace_id != transaction.tenant.workspace_id
            or job.actor_context.subject_id != transaction.actor.subject_id
            or job.actor_context.issuer != transaction.actor.issuer
            or not isinstance(document_batch, dict)
        ):
            raise _error(
                "DOCUMENT_BATCH_NOT_FOUND",
                "Document batch was not found in the selected tenant context",
                404,
            )
        return document_batch, job

    def progress(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        batch_id: str,
    ) -> dict[str, Any]:
        context = self._context(actor, tenant)
        with self.transactions.transaction(context) as transaction:
            batch, job = self._batch(transaction, batch_id)
            checkpoint = dict(job.checkpoint)
            return {
                "batch_id": batch_id,
                "selection_id": batch.get("selection_id"),
                "job_id": job.job_id,
                "status": job.status.value,
                "progress": checkpoint.get("document_ingestion") or checkpoint,
                "attempts": job.attempts,
            }

    def result(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        batch_id: str,
    ) -> dict[str, Any] | None:
        context = self._context(actor, tenant)
        with self.transactions.transaction(context) as transaction:
            batch, job = self._batch(transaction, batch_id)
            if job.status is not JobStatus.SUCCEEDED:
                return None
            return {
                "batch_id": batch_id,
                "selection_id": batch.get("selection_id"),
                "job_id": job.job_id,
                "status": job.status.value,
                "result": dict(job.result),
            }


__all__ = ["EnterpriseDocumentImportService"]
