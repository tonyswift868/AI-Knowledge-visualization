"""Contracts for resumable document batch ingestion."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..contracts import (
    DocumentEvidenceBundle,
    DocumentFormat,
    DocumentSourceMetadata,
    canonical_json_bytes,
    sha256_bytes,
    sha256_json,
)


if TYPE_CHECKING:
    from .exercises import DocumentExerciseCandidate


DOCUMENT_INGESTION_SCHEMA = "mentorflow.document-ingestion.v1"
DOCUMENT_HANDOFF_SCHEMA = "mentorflow.document-pipeline-handoff.v1"


class DocumentIngestionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"
    LIMITED = "LIMITED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"


class DocumentBatchStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class DocumentIngestionStage(str, Enum):
    PREFLIGHT = "PREFLIGHT"
    CONVERSION = "CONVERSION"
    EVIDENCE_MAPPING = "EVIDENCE_MAPPING"
    PROVENANCE_GATE = "PROVENANCE_GATE"
    HANDOFF = "HANDOFF"
    COMPLETE = "COMPLETE"
    INTERRUPTED = "INTERRUPTED"


class DoclingCompileMode(str, Enum):
    DISABLED_UNCOMPILED_HERON = "DISABLED_UNCOMPILED_HERON"
    RUNTIME_DEFAULT = "RUNTIME_DEFAULT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DoclingRuntimeStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class DocumentFingerprint:
    sha256: str
    byte_count: int

    def __post_init__(self) -> None:
        if len(self.sha256) != 64 or any(value not in "0123456789abcdef" for value in self.sha256):
            raise ValueError("fingerprint sha256 must be a lowercase hexadecimal digest")
        if self.byte_count < 0:
            raise ValueError("fingerprint byte_count must not be negative")

    def to_dict(self) -> dict[str, Any]:
        return {"sha256": self.sha256, "byte_count": self.byte_count}


@dataclass(frozen=True, slots=True)
class DocumentConversionIdentity:
    value: str
    fingerprint: DocumentFingerprint
    source_type: DocumentFormat
    conversion_config_identity: str

    @classmethod
    def create(
        cls,
        *,
        fingerprint: DocumentFingerprint,
        source_type: DocumentFormat,
        conversion_config_identity: str,
    ) -> "DocumentConversionIdentity":
        config = conversion_config_identity.strip()
        if not config:
            raise ValueError("conversion_config_identity must not be blank")
        digest = sha256_json(
            {
                "schema": DOCUMENT_INGESTION_SCHEMA,
                "content_sha256": fingerprint.sha256,
                "source_type": source_type.value,
                "conversion_config_identity": config,
            }
        )
        return cls(
            value=f"docconv_{digest[:40]}",
            fingerprint=fingerprint,
            source_type=source_type,
            conversion_config_identity=config,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "fingerprint": self.fingerprint.to_dict(),
            "source_type": self.source_type.value,
            "conversion_config_identity": self.conversion_config_identity,
        }


@dataclass(frozen=True, slots=True)
class DocumentIngestionFailure:
    error_class: str
    source_identity: str
    source_file: str
    stage: DocumentIngestionStage
    retryable: bool
    error_code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": self.error_class,
            "source_identity": self.source_identity,
            "source_file": self.source_file,
            "stage": self.stage.value,
            "retryable": self.retryable,
            "error_code": self.error_code,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class DocumentIngestionReceipt:
    index: int
    source_file: str
    status: DocumentIngestionStatus
    stage: DocumentIngestionStage
    source_id: str | None = None
    fingerprint: DocumentFingerprint | None = None
    conversion_identity: DocumentConversionIdentity | None = None
    bundle_id: str | None = None
    evidence_count: int = 0
    limitations: tuple[str, ...] = ()
    failure: DocumentIngestionFailure | None = None
    duplicate_of: str | None = None
    resumed: bool = False
    handoff_submitted: bool = False

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("receipt index must not be negative")
        if self.evidence_count < 0:
            raise ValueError("evidence_count must not be negative")
        failure_statuses = {
            DocumentIngestionStatus.FAILED,
            DocumentIngestionStatus.UNSUPPORTED,
        }
        if self.status in failure_statuses and self.failure is None:
            raise ValueError("failed or unsupported receipt requires failure details")
        if self.status not in failure_statuses and self.failure is not None:
            raise ValueError("successful, limited, or duplicate receipt cannot include failure")
        if self.status is DocumentIngestionStatus.SKIPPED_DUPLICATE and not self.duplicate_of:
            raise ValueError("duplicate receipt requires duplicate_of")

    def resumed_copy(self, *, index: int, source_file: str) -> "DocumentIngestionReceipt":
        return replace(
            self,
            index=index,
            source_file=source_file,
            resumed=True,
            duplicate_of=None,
            failure=None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "source_file": self.source_file,
            "source_id": self.source_id,
            "status": self.status.value,
            "stage": self.stage.value,
            "fingerprint": self.fingerprint.to_dict() if self.fingerprint else None,
            "conversion_identity": (
                self.conversion_identity.to_dict() if self.conversion_identity else None
            ),
            "bundle_id": self.bundle_id,
            "evidence_count": self.evidence_count,
            "limitations": list(self.limitations),
            "failure": self.failure.to_dict() if self.failure else None,
            "duplicate_of": self.duplicate_of,
            "resumed": self.resumed,
            "handoff_submitted": self.handoff_submitted,
        }


@dataclass(frozen=True, slots=True)
class DocumentPipelineHandoff:
    schema: str
    handoff_id: str
    conversion_identity: DocumentConversionIdentity
    source_id: str
    source_revision: str
    evidence_bundle: DocumentEvidenceBundle
    generic_evidence: tuple[Mapping[str, Any], ...]
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "handoff_id": self.handoff_id,
            "conversion_identity": self.conversion_identity.to_dict(),
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "evidence_bundle": self.evidence_bundle.to_dict(),
            "generic_evidence": [dict(item) for item in self.generic_evidence],
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class DocumentCheckpointEntry:
    conversion_identity: DocumentConversionIdentity
    receipt: DocumentIngestionReceipt
    bundle: DocumentEvidenceBundle | None
    handoff: DocumentPipelineHandoff | None
    handoff_submitted: bool = False

    def __post_init__(self) -> None:
        if self.receipt.status not in {
            DocumentIngestionStatus.SUCCESS,
            DocumentIngestionStatus.LIMITED,
        }:
            raise ValueError("checkpoint may only contain completed conversions")
        if self.receipt.conversion_identity != self.conversion_identity:
            raise ValueError("checkpoint conversion identity is inconsistent")

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversion_identity": self.conversion_identity.to_dict(),
            "receipt": self.receipt.to_dict(),
            "bundle": self.bundle.to_dict() if self.bundle else None,
            "handoff": self.handoff.to_dict() if self.handoff else None,
            "handoff_submitted": self.handoff_submitted,
        }


@dataclass(slots=True)
class DocumentIngestionCheckpoint:
    schema: str = DOCUMENT_INGESTION_SCHEMA
    completed: dict[str, DocumentCheckpointEntry] = field(default_factory=dict)

    def get(self, identity: DocumentConversionIdentity) -> DocumentCheckpointEntry | None:
        return self.completed.get(identity.value)

    def record(self, entry: DocumentCheckpointEntry) -> None:
        self.completed[entry.conversion_identity.value] = entry

    def mark_handoff_submitted(self, identity: DocumentConversionIdentity) -> None:
        entry = self.completed[identity.value]
        self.completed[identity.value] = replace(
            entry,
            receipt=replace(entry.receipt, handoff_submitted=True),
            handoff_submitted=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "completed": {
                key: self.completed[key].to_dict() for key in sorted(self.completed)
            },
        }

    def to_json_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())


@dataclass(frozen=True, slots=True)
class DocumentIngestionProgress:
    sequence: int
    request_id: str
    stage: DocumentIngestionStage
    total_documents: int
    completed_documents: int
    success_count: int
    failed_count: int
    limited_count: int
    duplicate_count: int
    current_source: str | None
    elapsed_seconds: float

    @property
    def fraction(self) -> float:
        if self.total_documents == 0:
            return 1.0
        return min(1.0, self.completed_documents / self.total_documents)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "request_id": self.request_id,
            "stage": self.stage.value,
            "total_documents": self.total_documents,
            "completed_documents": self.completed_documents,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "limited_count": self.limited_count,
            "duplicate_count": self.duplicate_count,
            "current_source": self.current_source,
            "elapsed_seconds": self.elapsed_seconds,
            "fraction": self.fraction,
        }


@dataclass(frozen=True, slots=True)
class DocumentIngestionMetrics:
    preflight_seconds: float
    conversion_seconds: float
    evidence_mapping_seconds: float
    provenance_gate_seconds: float
    handoff_seconds: float
    preflight_documents: int
    converted_documents: int
    mapped_evidence_items: int

    @staticmethod
    def _throughput(count: int, seconds: float) -> float | None:
        return count / seconds if count > 0 and seconds > 0 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "preflight_seconds": self.preflight_seconds,
            "conversion_seconds": self.conversion_seconds,
            "evidence_mapping_seconds": self.evidence_mapping_seconds,
            "provenance_gate_seconds": self.provenance_gate_seconds,
            "handoff_seconds": self.handoff_seconds,
            "preflight_documents": self.preflight_documents,
            "converted_documents": self.converted_documents,
            "mapped_evidence_items": self.mapped_evidence_items,
            "preflight_documents_per_second": self._throughput(
                self.preflight_documents, self.preflight_seconds
            ),
            "conversion_fixture_documents_per_second": self._throughput(
                self.converted_documents, self.conversion_seconds
            ),
            "evidence_items_per_second": self._throughput(
                self.mapped_evidence_items, self.evidence_mapping_seconds
            ),
        }


@dataclass(frozen=True, slots=True)
class DoclingRuntimeReceipt:
    receipt_id: str
    status: DoclingRuntimeStatus
    platform: str
    process_isolated: bool
    utf8_mode: bool
    default_encoding: str
    compile_mode: DoclingCompileMode
    fallback_reason: str | None
    msvc_available: bool | None
    network_allowed: bool
    network_attempts: int
    model_download_attempts: int
    artifact_sha256_before: str | None
    artifact_sha256_after: str | None
    source_files: tuple[str, ...]
    worker_exit_code: int | None
    error_class: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.network_attempts < 0 or self.model_download_attempts < 0:
            raise ValueError("runtime attempt counts must not be negative")
        if self.status is DoclingRuntimeStatus.FAILED and not self.error_code:
            raise ValueError("failed runtime receipt requires an error code")
        if self.status is DoclingRuntimeStatus.SUCCESS and (
            self.error_class is not None or self.error_code is not None
        ):
            raise ValueError("successful runtime receipt cannot include an error")

    @property
    def artifact_hash_unchanged(self) -> bool | None:
        if self.artifact_sha256_before is None or self.artifact_sha256_after is None:
            return None
        return self.artifact_sha256_before == self.artifact_sha256_after

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "status": self.status.value,
            "platform": self.platform,
            "process_isolated": self.process_isolated,
            "utf8_mode": self.utf8_mode,
            "default_encoding": self.default_encoding,
            "compile_mode": self.compile_mode.value,
            "fallback_reason": self.fallback_reason,
            "msvc_available": self.msvc_available,
            "network_allowed": self.network_allowed,
            "network_attempts": self.network_attempts,
            "model_download_attempts": self.model_download_attempts,
            "artifact_sha256_before": self.artifact_sha256_before,
            "artifact_sha256_after": self.artifact_sha256_after,
            "artifact_hash_unchanged": self.artifact_hash_unchanged,
            "source_files": list(self.source_files),
            "worker_exit_code": self.worker_exit_code,
            "error_class": self.error_class,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class DocumentBatchRequest:
    request_id: str
    selected_documents: tuple[str | Path, ...]
    conversion_config_identity: str
    metadata_by_path: Mapping[str | Path, DocumentSourceMetadata] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    conversion_batch_size: int = 16

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("request_id must not be blank")
        if not self.selected_documents:
            raise ValueError("selected_documents must not be empty")
        if not self.conversion_config_identity.strip():
            raise ValueError("conversion_config_identity must not be blank")
        if self.conversion_batch_size <= 0:
            raise ValueError("conversion_batch_size must be positive")


@dataclass(frozen=True, slots=True)
class DocumentBatchResult:
    request_id: str
    status: DocumentBatchStatus
    receipts: tuple[DocumentIngestionReceipt, ...]
    bundles: tuple[DocumentEvidenceBundle, ...]
    handoffs: tuple[DocumentPipelineHandoff, ...]
    checkpoint: DocumentIngestionCheckpoint
    progress: tuple[DocumentIngestionProgress, ...]
    metrics: DocumentIngestionMetrics
    runtime_receipts: tuple[DoclingRuntimeReceipt, ...] = ()
    exercise_candidates: tuple["DocumentExerciseCandidate", ...] = ()

    def __post_init__(self) -> None:
        if [item.index for item in self.receipts] != list(range(len(self.receipts))):
            raise ValueError("receipts must preserve request order")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": DOCUMENT_INGESTION_SCHEMA,
            "request_id": self.request_id,
            "status": self.status.value,
            "receipts": [item.to_dict() for item in self.receipts],
            "bundles": [item.to_dict() for item in self.bundles],
            "handoffs": [item.to_dict() for item in self.handoffs],
            "checkpoint": self.checkpoint.to_dict(),
            "progress": [item.to_dict() for item in self.progress],
            "metrics": self.metrics.to_dict(),
            "runtime_receipts": [item.to_dict() for item in self.runtime_receipts],
            "exercise_candidates": [
                item.to_dict() for item in self.exercise_candidates
            ],
        }
        payload["result_hash"] = sha256_bytes(canonical_json_bytes(payload))
        return payload


__all__ = [
    "DOCUMENT_HANDOFF_SCHEMA",
    "DOCUMENT_INGESTION_SCHEMA",
    "DocumentBatchRequest",
    "DocumentBatchResult",
    "DocumentBatchStatus",
    "DoclingCompileMode",
    "DoclingRuntimeReceipt",
    "DoclingRuntimeStatus",
    "DocumentCheckpointEntry",
    "DocumentConversionIdentity",
    "DocumentFingerprint",
    "DocumentIngestionCheckpoint",
    "DocumentIngestionFailure",
    "DocumentIngestionMetrics",
    "DocumentIngestionProgress",
    "DocumentIngestionReceipt",
    "DocumentIngestionStage",
    "DocumentIngestionStatus",
    "DocumentPipelineHandoff",
]
