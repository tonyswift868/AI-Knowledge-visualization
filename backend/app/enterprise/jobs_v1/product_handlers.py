"""Real Engineering Preview product adapters for durable workflow jobs."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from app.document_import_integration import DOCUMENT_IMPORT_MAX_FILES
from app.enterprise.contracts_v1 import ActorKind, TenantMode
from app.knowledge.inventory.models import SourceMetadata
from app.knowledge.pipeline_v2.models import KnowledgePipelineResult
from app.knowledge.pipeline_v2.pipeline import KnowledgePipelineV2
from app.knowledge.pipeline_v2.progress import PipelineStatus
from app.knowledge_policy import CONVERSION_POLICY
from app.planning.product_runtime import PlanCreationRequest, PlanCreationService
from app.provider_runtime import provider_for_session, require_provider_session
from app.sources.document import DocumentSourceMetadata, DocumentSourcePolicy
from app.sources.document.ingestion.application import DocumentIngestionApplication
from app.sources.document.ingestion.contracts import (
    DocumentBatchRequest,
    DocumentBatchStatus,
)
from app.sources.video.transcript_evidence import (
    SelectedTrackEvidence,
    TranscriptEvidenceBundle,
    TranscriptPartEvidence,
    TranscriptSegment,
)
from app.sources.video.transcript_normalizer import GlossaryTerm, TerminologyGlossary

from .models import FailureClass, JobRecord
from .retry import WorkflowFailure


Checkpoint = Callable[[str, dict[str, Any]], None]
CancellationCheck = Callable[[], None]


def _invalid(code: str, message: str) -> WorkflowFailure:
    return WorkflowFailure(FailureClass.INVALID_INPUT, code, message)


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise _invalid("WORKFLOW_PAYLOAD_INVALID", f"{field} must be an object")
    return value


def _items(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise _invalid("WORKFLOW_PAYLOAD_INVALID", f"{field} must be an array")
    return value


def _tenant_payload(job: JobRecord) -> Mapping[str, Any]:
    payload = _mapping(job.payload, "payload")
    tenant = job.tenant_context
    if tenant.mode is not TenantMode.ENTERPRISE_SERVER:
        raise _invalid(
            "WORKFLOW_TENANT_CONTEXT_INVALID",
            "Production workflows require an Enterprise tenant",
        )
    organization_id = str(payload.get("organization_id") or "").strip()
    workspace_id = str(payload.get("workspace_id") or "").strip()
    if (
        not organization_id
        or not workspace_id
        or organization_id != tenant.organization_id
        or workspace_id != tenant.workspace_id
        or job.actor_context.organization_id != tenant.organization_id
        or job.actor_context.workspace_id != tenant.workspace_id
    ):
        raise _invalid(
            "WORKFLOW_TENANT_BINDING_INVALID",
            "Workflow payload is not bound to the authorized tenant",
        )
    return payload


def _document_input_root() -> Path:
    configured = str(os.getenv("MENTORFLOW_DOCUMENT_IMPORT_ROOT") or "").strip()
    if not configured:
        raise _invalid(
            "DOCUMENT_IMPORT_ROOT_REQUIRED",
            "Document import storage is not configured",
        )
    root = Path(configured).expanduser()
    if not root.is_absolute():
        raise _invalid(
            "DOCUMENT_IMPORT_ROOT_INVALID",
            "Document import storage must be absolute",
        )
    return (root.resolve() / "uploads").resolve()


def _document_path(value: object, *, input_root: Path) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise _invalid("DOCUMENT_SELECTION_INVALID", "Document path must not be empty")
    candidate = Path(raw).expanduser()
    resolved = (
        candidate.resolve() if candidate.is_absolute() else (input_root / candidate).resolve()
    )
    if resolved != input_root and input_root not in resolved.parents:
        raise _invalid(
            "DOCUMENT_SELECTION_OUTSIDE_TENANT_ROOT",
            "Document path is outside the configured import root",
        )
    return resolved


def document_ingestion(
    *,
    engine: Engine,
    job: JobRecord,
    checkpoint: Checkpoint,
    ensure_not_cancelled: CancellationCheck,
) -> dict[str, Any]:
    """Execute the existing DocumentIngestionApplication for one durable batch."""

    del engine
    payload = _tenant_payload(job)
    request_payload = _mapping(payload.get("request"), "payload.request")
    input_root = _document_input_root()
    selected = tuple(
        _document_path(value, input_root=input_root)
        for value in _items(
            request_payload.get("selected_documents"),
            "payload.request.selected_documents",
        )
    )
    if len(selected) > DOCUMENT_IMPORT_MAX_FILES:
        raise _invalid(
            "DOCUMENT_BATCH_SIZE_LIMIT",
            "Document batch exceeds the product limit",
        )
    request_id = str(request_payload.get("request_id") or "").strip()
    if request_id != job.request_id:
        raise _invalid(
            "DOCUMENT_REQUEST_ID_MISMATCH",
            "Document request_id must match the durable job",
        )
    metadata_payload = _mapping(
        request_payload.get("metadata_by_path") or {},
        "payload.request.metadata_by_path",
    )
    metadata = {
        _document_path(path, input_root=input_root): DocumentSourceMetadata(
            **dict(_mapping(value, f"metadata_by_path.{path}"))
        )
        for path, value in metadata_payload.items()
    }
    request = DocumentBatchRequest(
        request_id=request_id,
        selected_documents=selected,
        conversion_config_identity=str(
            request_payload.get("conversion_config_identity") or ""
        ).strip(),
        metadata_by_path=metadata,
        conversion_batch_size=int(request_payload.get("conversion_batch_size") or 16),
    )
    policy = DocumentSourcePolicy(
        input_root=input_root,
        hard_max_file_bytes=CONVERSION_POLICY.maximum_input_bytes,
        max_batch_files=DOCUMENT_IMPORT_MAX_FILES,
    )

    def progress(value: Any) -> None:
        ensure_not_cancelled()
        checkpoint("document_ingestion", value.to_dict())

    def should_stop() -> bool:
        ensure_not_cancelled()
        return False

    try:
        ensure_not_cancelled()
        result = DocumentIngestionApplication(policy).ingest(
            request,
            progress_sink=progress,
            should_stop=should_stop,
        )
        ensure_not_cancelled()
    except WorkflowFailure:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise _invalid(
            "DOCUMENT_INGESTION_INPUT_INVALID",
            "Document ingestion input is invalid",
        ) from exc
    except Exception as exc:
        raise WorkflowFailure(
            FailureClass.RETRYABLE,
            "DOCUMENT_INGESTION_FAILED",
            "Document ingestion did not complete",
        ) from exc
    if result.status is DocumentBatchStatus.FAILED:
        raise WorkflowFailure(
            FailureClass.NON_RETRYABLE,
            "DOCUMENT_INGESTION_FAILED",
            "Document ingestion rejected the batch",
        )
    if result.status is DocumentBatchStatus.INTERRUPTED:
        raise WorkflowFailure(
            FailureClass.WORKER_LOST,
            "DOCUMENT_INGESTION_INTERRUPTED",
            "Document ingestion was interrupted",
        )
    return result.to_dict()


def _selected_track(value: object) -> SelectedTrackEvidence:
    return SelectedTrackEvidence(**dict(_mapping(value, "bundle.parts.selected_track")))


def _transcript_part(value: object) -> TranscriptPartEvidence:
    payload = dict(_mapping(value, "bundle.parts"))
    payload["selected_track"] = _selected_track(payload.get("selected_track"))
    payload["segment_ids"] = tuple(
        str(item) for item in _items(payload.get("segment_ids"), "bundle.parts.segment_ids")
    )
    payload["warnings"] = tuple(
        str(item) for item in _items(payload.get("warnings") or (), "bundle.parts.warnings")
    )
    return TranscriptPartEvidence(**payload)


def _transcript_segment(value: object) -> TranscriptSegment:
    payload = dict(_mapping(value, "bundle.segments"))
    payload.pop("segment_id", None)
    return TranscriptSegment(**payload)


def _transcript_bundle(value: object) -> TranscriptEvidenceBundle:
    payload = dict(_mapping(value, "payload.bundle"))
    payload["parts"] = tuple(
        _transcript_part(item)
        for item in _items(payload.get("parts"), "payload.bundle.parts")
    )
    payload["segments"] = tuple(
        _transcript_segment(item)
        for item in _items(payload.get("segments"), "payload.bundle.segments")
    )
    payload["warnings"] = tuple(
        str(item)
        for item in _items(payload.get("warnings") or (), "payload.bundle.warnings")
    )
    return TranscriptEvidenceBundle(**payload)


def _glossary(value: object) -> TerminologyGlossary:
    payload = _mapping(value, "payload.glossary")
    terms = tuple(
        GlossaryTerm(**dict(_mapping(item, "payload.glossary.terms")))
        for item in _items(payload.get("terms") or (), "payload.glossary.terms")
    )
    return TerminologyGlossary(
        terms=terms,
        version=str(payload.get("version") or "v1"),
    )


def _run_knowledge_pipeline(
    pipeline: KnowledgePipelineV2,
    *,
    bundle: TranscriptEvidenceBundle,
    glossary: TerminologyGlossary,
    source_metadata: SourceMetadata,
    checkpoint: Checkpoint,
    ensure_not_cancelled: CancellationCheck,
) -> KnowledgePipelineResult:
    async def run() -> KnowledgePipelineResult:
        async def progress(value: Any) -> None:
            ensure_not_cancelled()
            checkpoint("knowledge_pipeline", value.to_dict())

        return await pipeline.run(
            bundle,
            glossary,
            source_metadata=source_metadata,
            progress_sink=progress,
        )

    return asyncio.run(run())


def knowledge_pipeline(
    *,
    engine: Engine,
    job: JobRecord,
    checkpoint: Checkpoint,
    ensure_not_cancelled: CancellationCheck,
) -> dict[str, Any]:
    """Run the real Provider-backed KnowledgePipelineV2 product service."""

    del engine
    payload = _tenant_payload(job)
    if job.actor_context.actor_kind is not ActorKind.HUMAN:
        raise _invalid(
            "KNOWLEDGE_PROVIDER_OWNER_REQUIRED",
            "Knowledge pipeline requires the owning authenticated user",
        )
    session_id = str(payload.get("provider_session_id") or "").strip()
    if not session_id:
        raise _invalid(
            "KNOWLEDGE_PROVIDER_SESSION_REQUIRED",
            "Knowledge pipeline requires a verified Provider session",
        )
    try:
        session = require_provider_session(
            session_id,
            owner_user_id=job.actor_context.subject_id,
        )
        provider = provider_for_session(session)
        bundle = _transcript_bundle(payload.get("bundle"))
        glossary = _glossary(payload.get("glossary") or {})
        source_metadata = SourceMetadata(
            **dict(_mapping(payload.get("source_metadata"), "payload.source_metadata"))
        )
        ensure_not_cancelled()
        result = _run_knowledge_pipeline(
            KnowledgePipelineV2(
                provider,
                provider,
                normalization_provider=provider,
            ),
            bundle=bundle,
            glossary=glossary,
            source_metadata=source_metadata,
            checkpoint=checkpoint,
            ensure_not_cancelled=ensure_not_cancelled,
        )
        ensure_not_cancelled()
    except WorkflowFailure:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise _invalid(
            "KNOWLEDGE_PIPELINE_INPUT_INVALID",
            "Knowledge pipeline input is invalid",
        ) from exc
    except Exception as exc:
        raise WorkflowFailure(
            FailureClass.PROVIDER_FAILURE,
            "KNOWLEDGE_PIPELINE_PROVIDER_FAILED",
            "Knowledge pipeline Provider execution failed",
        ) from exc
    if result.status is PipelineStatus.FAILED:
        raise WorkflowFailure(
            FailureClass.PROVIDER_FAILURE,
            "KNOWLEDGE_PIPELINE_FAILED",
            "Knowledge pipeline did not produce a verified result",
        )
    return result.to_dict()


def plan_creation(
    *,
    engine: Engine,
    job: JobRecord,
    checkpoint: Checkpoint,
    ensure_not_cancelled: CancellationCheck,
) -> dict[str, Any]:
    """Create a real immutable plan preview through PlanCreationService."""

    del engine
    payload = _tenant_payload(job)
    try:
        request = PlanCreationRequest.model_validate(
            dict(_mapping(payload.get("request"), "payload.request"))
        )
        if request.request_id != job.request_id:
            raise _invalid(
                "PLAN_REQUEST_ID_MISMATCH",
                "Plan request_id must match the durable job",
            )
        ensure_not_cancelled()
        checkpoint(
            "plan_creation",
            {"status": "STARTED", "request_id": request.request_id},
        )
        outcome = PlanCreationService().create(request)
        ensure_not_cancelled()
    except WorkflowFailure:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise _invalid(
            "PLAN_CREATION_INPUT_INVALID",
            "Plan creation input is invalid",
        ) from exc
    except Exception as exc:
        raise WorkflowFailure(
            FailureClass.RETRYABLE,
            "PLAN_CREATION_FAILED",
            "Plan creation did not complete",
        ) from exc
    return {
        "status": outcome.preview.status.value,
        "preview": outcome.preview.model_dump(mode="json"),
    }


__all__ = ["document_ingestion", "knowledge_pipeline", "plan_creation"]
