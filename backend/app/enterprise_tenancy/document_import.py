"""Tenant-partitioned Enterprise document selection and durable batch facade."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastapi import UploadFile

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
from app.knowledge_policy import CONVERSION_POLICY

from .context import PostgresTenantTransactionManager, TenantTransactionContext
from .models import (
    EnterprisePersistenceRequestError,
    TenantResourceDraft,
    TenantResourceKind,
)
from .repository import PostgresTenantResourceRepository


def _error(code: str, message: str, status: int) -> EnterprisePersistenceRequestError:
    return EnterprisePersistenceRequestError(code, message, status=status)


def _runtime_root(configured: Path | None = None) -> Path:
    candidate = configured
    if candidate is None:
        raw = str(os.getenv("MENTORFLOW_DOCUMENT_IMPORT_ROOT") or "").strip()
        if not raw:
            raise _error(
                "DOCUMENT_IMPORT_ROOT_REQUIRED",
                "Enterprise document import storage is not configured",
                503,
            )
        candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise _error(
            "DOCUMENT_IMPORT_ROOT_INVALID",
            "Enterprise document import storage must be absolute",
            503,
        )
    resolved = candidate.resolve()
    normalized = resolved.as_posix().casefold().rstrip("/")
    if normalized == "/app/data" or normalized.startswith("/app/data/"):
        raise _error(
            "DOCUMENT_IMPORT_ROOT_INVALID",
            "Enterprise document import storage must not use /app/data",
            503,
        )
    return resolved


def _tenant_partition(tenant: TenantContext) -> str:
    digest = hashlib.sha256(
        f"{tenant.organization_id}\0{tenant.workspace_id}".encode("utf-8")
    ).hexdigest()
    return f"tenant-{digest[:32]}"


def _selection_id() -> str:
    return f"selection_{uuid.uuid4().hex}"


def _document_id() -> str:
    return f"document_{uuid.uuid4().hex}"


def _safe_suffix(filename: str) -> str:
    suffix = Path(Path(filename.replace("\\", "/")).name).suffix.casefold()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,12}", suffix) else ""


def _ensure_descendant(path: Path, parent: Path) -> None:
    if path == parent or parent not in path.parents:
        raise RuntimeError("document storage path escaped the tenant partition")


class EnterpriseDocumentImportService:
    def __init__(
        self,
        engine=None,
        *,
        transaction_manager=None,
        resource_repository: PostgresTenantResourceRepository | None = None,
        workflow_repository: WorkflowRepository | None = None,
        runtime_root: Path | None = None,
    ) -> None:
        if transaction_manager is None:
            if engine is None:
                raise ValueError("engine or transaction_manager is required")
            transaction_manager = PostgresTenantTransactionManager(engine)
        self.transactions = transaction_manager
        self.resources = resource_repository or PostgresTenantResourceRepository()
        self.workflows = workflow_repository or WorkflowRepository()
        self.root = _runtime_root(runtime_root)
        self.upload_root = (self.root / "uploads").resolve()
        _ensure_descendant(self.upload_root, self.root)

    @staticmethod
    def _context(actor: ActorContext, tenant: TenantContext) -> TenantTransactionContext:
        return TenantTransactionContext(tenant=tenant, actor=actor)

    def _selection_root(self, tenant: TenantContext, selection_id: str) -> Path:
        tenant_root = (self.upload_root / _tenant_partition(tenant)).resolve()
        _ensure_descendant(tenant_root, self.upload_root)
        selected = (tenant_root / selection_id).resolve()
        _ensure_descendant(selected, tenant_root)
        return selected

    async def select(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        files: list[UploadFile],
    ) -> dict[str, Any]:
        context = self._context(actor, tenant)
        if not files or len(files) > DOCUMENT_IMPORT_MAX_FILES:
            raise _error(
                "DOCUMENT_SELECTION_SIZE_INVALID",
                f"Select between 1 and {DOCUMENT_IMPORT_MAX_FILES} documents",
                422,
            )
        selection_id = _selection_id()
        selection_root = self._selection_root(tenant, selection_id)
        selection_root.mkdir(parents=True, exist_ok=False)
        staged: list[dict[str, Any]] = []
        try:
            for upload in files:
                data = await upload.read(CONVERSION_POLICY.maximum_input_bytes + 1)
                if not data:
                    raise _error(
                        "DOCUMENT_EMPTY",
                        "Uploaded documents must not be empty",
                        422,
                    )
                if len(data) > CONVERSION_POLICY.maximum_input_bytes:
                    raise _error(
                        "DOCUMENT_TOO_LARGE",
                        "Uploaded document exceeds the configured size limit",
                        413,
                    )
                document_id = _document_id()
                filename = Path(str(upload.filename or "document").replace("\\", "/")).name
                target = (selection_root / f"{document_id}{_safe_suffix(filename)}").resolve()
                _ensure_descendant(target, selection_root)
                target.write_bytes(data)
                staged.append(
                    {
                        "document_id": document_id,
                        "filename": filename[:320] or document_id,
                        "media_type": str(upload.content_type or "application/octet-stream")[:160],
                        "size_bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "storage_relative_path": target.relative_to(
                            self.upload_root
                        ).as_posix(),
                    }
                )
            with self.transactions.transaction(context) as transaction:
                for item in staged:
                    self.resources.upsert(
                        transaction,
                        TenantResourceDraft(
                            resource_id=item["document_id"],
                            kind=TenantResourceKind.DOCUMENT,
                            scope_type=ResourceScopeType.WORKSPACE,
                            owner_subject_id=actor.subject_id,
                            classification=ResourceClassification.CONFIDENTIAL,
                            title=item["filename"],
                            status="SELECTED",
                            payload={
                                "record_type": "document",
                                "selection_id": selection_id,
                                **item,
                            },
                        ),
                    )
                self.resources.upsert(
                    transaction,
                    TenantResourceDraft(
                        resource_id=selection_id,
                        kind=TenantResourceKind.DOCUMENT,
                        scope_type=ResourceScopeType.WORKSPACE,
                        owner_subject_id=actor.subject_id,
                        classification=ResourceClassification.CONFIDENTIAL,
                        title=f"Document selection ({len(staged)})",
                        status="SELECTED",
                        payload={
                            "record_type": "selection",
                            "document_ids": [item["document_id"] for item in staged],
                        },
                    ),
                )
        except BaseException:
            if selection_root.exists() and self.upload_root in selection_root.parents:
                shutil.rmtree(selection_root)
            raise
        return {
            "selection_id": selection_id,
            "document_ids": [item["document_id"] for item in staged],
            "files": [
                {
                    "file_id": item["document_id"],
                    "name": item["filename"],
                    "size_bytes": item["size_bytes"],
                    "media_type": item["media_type"],
                }
                for item in staged
            ],
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
            or selection.payload.get("record_type") != "selection"
        ):
            raise _error(
                "DOCUMENT_SELECTION_NOT_FOUND",
                "Document selection was not found in the selected tenant context",
                404,
            )
        return selection

    def _document_records(
        self,
        transaction,
        *,
        selection_id: str,
        document_ids: Sequence[object],
    ):
        normalized = tuple(str(value or "").strip() for value in document_ids)
        if (
            not normalized
            or len(normalized) > DOCUMENT_IMPORT_MAX_FILES
            or any(not value for value in normalized)
            or len(set(normalized)) != len(normalized)
        ):
            raise _error(
                "DOCUMENT_SELECTION_INVALID",
                "Document identifiers must be a unique non-empty collection",
                422,
            )
        records = []
        for document_id in normalized:
            record = self.resources.get(
                transaction, TenantResourceKind.DOCUMENT, document_id
            )
            if (
                record is None
                or record.organization_id != transaction.tenant.organization_id
                or record.workspace_id != transaction.tenant.workspace_id
                or record.payload.get("record_type") != "document"
                or record.payload.get("selection_id") != selection_id
            ):
                raise _error(
                    "DOCUMENT_SELECTION_NOT_FOUND",
                    "A selected document is unavailable in the tenant context",
                    404,
                )
            records.append(record)
        return tuple(records)

    def _absolute_path(self, record, *, require_exists: bool = True) -> Path:
        relative = Path(str(record.payload.get("storage_relative_path") or ""))
        if relative.is_absolute():
            raise _error(
                "DOCUMENT_STORAGE_INVALID",
                "Persisted document storage metadata is invalid",
                409,
            )
        resolved = (self.upload_root / relative).resolve()
        _ensure_descendant(resolved, self.upload_root)
        if require_exists and not resolved.is_file():
            raise _error(
                "DOCUMENT_CONTENT_NOT_FOUND",
                "Persisted document content is unavailable",
                409,
            )
        return resolved

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
            records = self._document_records(
                transaction,
                selection_id=selection_id,
                document_ids=selection.payload.get("document_ids") or (),
            )
            documents = [
                {
                    "file_id": record.resource_id,
                    "name": record.title,
                    "size_bytes": int(record.payload.get("size_bytes") or 0),
                    "media_type": record.payload.get("media_type"),
                    "ready": self._absolute_path(record).is_file(),
                }
                for record in records
            ]
        return {
            "selection_id": selection_id,
            "status": "ready",
            "ready": True,
            "documents": documents,
            "warnings": [],
        }

    def resolve_selection_paths(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        selection_id: str,
        document_ids: Sequence[object],
    ) -> tuple[str, ...]:
        context = self._context(actor, tenant)
        with self.transactions.transaction(context) as transaction:
            self._selection(transaction, selection_id)
            records = self._document_records(
                transaction,
                selection_id=selection_id,
                document_ids=document_ids,
            )
            # Learning-run creation validates ownership from canonical rows only.
            # The durable Worker owns content availability checks when it consumes
            # this persisted path after the API transaction has committed.
            return tuple(
                str(self._absolute_path(record, require_exists=False))
                for record in records
            )

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
                selection.payload.get("document_ids") or ()
            )
            records = self._document_records(
                transaction,
                selection_id=selection_id,
                document_ids=selected_ids,
            )
            paths = tuple(str(self._absolute_path(record)) for record in records)
            fingerprint = hashlib.sha256(
                ("\0".join((*selected_ids, str(body.conversion_batch_size)))).encode(
                    "utf-8"
                )
            ).hexdigest()
            trace_id = f"document-batch:{fingerprint}"
            request = JobRequest(
                workflow_kind=WorkflowKind.DOCUMENT_INGESTION,
                payload={
                    "organization_id": tenant.organization_id,
                    "workspace_id": tenant.workspace_id,
                    "document_batch": {
                        "selection_id": selection_id,
                        "document_ids": list(selected_ids),
                    },
                    "request": {
                        "request_id": actor.request_id,
                        "selected_documents": list(paths),
                        "conversion_config_identity": "enterprise-document-batch-v1",
                        "conversion_batch_size": body.conversion_batch_size,
                        "metadata_by_path": {},
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
