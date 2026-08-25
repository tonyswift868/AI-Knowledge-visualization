"""Endpoint-neutral contracts for MentorFlow document source ingestion."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


DOCUMENT_SOURCE_SCHEMA = "mentorflow.document-source.v1"
DOCUMENT_EVIDENCE_SCHEMA = "mentorflow.document-evidence.v1"
DOCUMENT_ADAPTER_NAME = "docling"
DOCUMENT_ADAPTER_VERSION = "mentorflow.docling.v1"


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


class DocumentFormat(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    HTML = "html"
    MARKDOWN = "markdown"


class DocumentPreflightStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    SUPPORTED_WITH_LIMITATION = "SUPPORTED_WITH_LIMITATION"
    REJECTED = "REJECTED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


class DocumentProcessingShape(str, Enum):
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"
    UNKNOWN = "UNKNOWN"


class DocumentEvidenceKind(str, Enum):
    HEADING = "heading"
    TEXT = "text"
    TABLE = "table"


class DocumentFileStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    REJECTED = "REJECTED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    CONVERSION_FAILED = "CONVERSION_FAILED"
    DUPLICATE = "DUPLICATE"


class DocumentBatchStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILURE = "FAILURE"


@dataclass(frozen=True, slots=True)
class DoclingDependencyInfo:
    available: bool
    version: str | None
    module_file: str | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "version": self.version,
            "module_file": self.module_file,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class DocumentSourceMetadata:
    """Metadata known before conversion, normally supplied by the caller."""

    page_count: int | None = None
    slide_count: int | None = None
    section_count: int | None = None
    table_count: int | None = None

    def __post_init__(self) -> None:
        for name in ("page_count", "slide_count", "section_count", "table_count"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must not be negative")

    def to_dict(self) -> dict[str, int | None]:
        return {
            "page_count": self.page_count,
            "slide_count": self.slide_count,
            "section_count": self.section_count,
            "table_count": self.table_count,
        }


@dataclass(frozen=True, slots=True)
class DocumentSourcePolicy:
    """Caller-owned limits and model-use policy.

    No hard product limit is implicit.  A ``None`` hard limit means that the
    preflight records the shape but does not reject on that dimension.
    """

    input_root: Path
    warning_file_bytes: int | None = None
    hard_max_file_bytes: int | None = None
    warning_pages: int | None = None
    hard_max_pages: int | None = None
    warning_slides: int | None = None
    hard_max_slides: int | None = None
    warning_sections: int | None = None
    hard_max_sections: int | None = None
    shape_medium_file_bytes: int | None = None
    shape_large_file_bytes: int | None = None
    shape_medium_units: int | None = None
    shape_large_units: int | None = None
    max_batch_files: int | None = None
    pdf_artifacts_path: Path | None = None
    pdf_table_structure_enabled: bool = False

    def __post_init__(self) -> None:
        if not self.input_root.is_absolute():
            raise ValueError("input_root must be absolute")
        if self.pdf_artifacts_path is not None and not self.pdf_artifacts_path.is_absolute():
            raise ValueError("pdf_artifacts_path must be absolute when provided")
        numeric_fields = (
            "warning_file_bytes",
            "hard_max_file_bytes",
            "warning_pages",
            "hard_max_pages",
            "warning_slides",
            "hard_max_slides",
            "warning_sections",
            "hard_max_sections",
            "shape_medium_file_bytes",
            "shape_large_file_bytes",
            "shape_medium_units",
            "shape_large_units",
            "max_batch_files",
        )
        for name in numeric_fields:
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when provided")
        for warning_name, hard_name in (
            ("warning_file_bytes", "hard_max_file_bytes"),
            ("warning_pages", "hard_max_pages"),
            ("warning_slides", "hard_max_slides"),
            ("warning_sections", "hard_max_sections"),
        ):
            warning = getattr(self, warning_name)
            hard = getattr(self, hard_name)
            if warning is not None and hard is not None and warning > hard:
                raise ValueError(f"{warning_name} must not exceed {hard_name}")
        for medium_name, large_name in (
            ("shape_medium_file_bytes", "shape_large_file_bytes"),
            ("shape_medium_units", "shape_large_units"),
        ):
            medium = getattr(self, medium_name)
            large = getattr(self, large_name)
            if medium is not None and large is not None and medium > large:
                raise ValueError(f"{medium_name} must not exceed {large_name}")


@dataclass(frozen=True, slots=True)
class DocumentProcessingEstimate:
    byte_count: int
    unit_kind: str | None
    unit_count: int | None
    shape: DocumentProcessingShape

    def to_dict(self) -> dict[str, Any]:
        return {
            "byte_count": self.byte_count,
            "unit_kind": self.unit_kind,
            "unit_count": self.unit_count,
            "shape": self.shape.value,
        }


@dataclass(frozen=True, slots=True)
class DocumentSourceCandidate:
    source_id: str
    source_file: str
    source_sha256: str
    format: DocumentFormat
    byte_count: int
    metadata: DocumentSourceMetadata
    resolved_path: Path = field(repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
            "format": self.format.value,
            "byte_count": self.byte_count,
            "metadata": self.metadata.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class DocumentPreflightResult:
    status: DocumentPreflightStatus
    dependency: DoclingDependencyInfo
    candidate: DocumentSourceCandidate | None
    estimate: DocumentProcessingEstimate | None
    conversion_ready: bool
    limitations: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status in {
            DocumentPreflightStatus.SUPPORTED,
            DocumentPreflightStatus.SUPPORTED_WITH_LIMITATION,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "dependency": self.dependency.to_dict(),
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "estimate": self.estimate.to_dict() if self.estimate else None,
            "conversion_ready": self.conversion_ready,
            "limitations": list(self.limitations),
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass(frozen=True, slots=True)
class DocumentBoundingBox:
    left: float
    top: float
    right: float
    bottom: float
    coord_origin: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
            "coord_origin": self.coord_origin,
        }


@dataclass(frozen=True, slots=True)
class DocumentItemProvenance:
    page_no: int | None
    bbox: DocumentBoundingBox | None
    char_start: int | None
    char_end: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "bbox": self.bbox.to_dict() if self.bbox else None,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }


@dataclass(frozen=True, slots=True)
class DocumentEvidence:
    evidence_id: str
    source_id: str
    source_file: str
    source_sha256: str
    item_id: str
    parent_item_id: str | None
    ordinal: int
    hierarchy_level: int
    kind: DocumentEvidenceKind
    content: str
    content_hash: str
    section_path: tuple[str, ...]
    content_layer: str | None
    provenance: tuple[DocumentItemProvenance, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_id": self.source_id,
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
            "item_id": self.item_id,
            "parent_item_id": self.parent_item_id,
            "ordinal": self.ordinal,
            "hierarchy_level": self.hierarchy_level,
            "kind": self.kind.value,
            "content": self.content,
            "content_hash": self.content_hash,
            "section_path": list(self.section_path),
            "content_layer": self.content_layer,
            "provenance": [item.to_dict() for item in self.provenance],
        }

    def to_generic_evidence(self) -> dict[str, Any]:
        """Return an EvidenceSegmentV1-compatible mapping plus richer fields."""

        page_numbers = [item.page_no for item in self.provenance if item.page_no is not None]
        first = self.provenance[0] if self.provenance else None
        char_start = first.char_start if first and first.char_start is not None else 0
        char_end = first.char_end if first and first.char_end is not None else len(self.content)
        bbox = None
        if first and first.bbox:
            bbox = [
                first.bbox.left,
                first.bbox.top,
                first.bbox.right,
                first.bbox.bottom,
            ]
        return {
            "material_id": self.source_id,
            "source_id": self.source_id,
            "source_revision_id": self.source_sha256,
            "chunk_id": self.evidence_id,
            "char_start": char_start,
            "char_end": max(char_start, char_end),
            "quote": self.content,
            "source_file": self.source_file,
            "heading": self.section_path[-1] if self.section_path else "",
            "page_start": min(page_numbers) if page_numbers else None,
            "page_end": max(page_numbers) if page_numbers else None,
            "paragraph_start": None,
            "paragraph_end": None,
            "content_hash": self.content_hash,
            "bbox": bbox,
            "quote_hash": self.content_hash,
            "adapter_name": DOCUMENT_ADAPTER_NAME,
            "adapter_version": DOCUMENT_ADAPTER_VERSION,
            "document_item_id": self.item_id,
            "document_kind": self.kind.value,
            "document_section_path": list(self.section_path),
            "document_provenance": [item.to_dict() for item in self.provenance],
        }


@dataclass(frozen=True, slots=True)
class DocumentEvidenceBundle:
    schema: str
    adapter_name: str
    adapter_version: str
    docling_version: str
    bundle_id: str
    bundle_hash: str
    source_id: str
    source_file: str
    source_sha256: str
    document_name: str
    evidence: tuple[DocumentEvidence, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "adapter_name": self.adapter_name,
            "adapter_version": self.adapter_version,
            "docling_version": self.docling_version,
            "bundle_id": self.bundle_id,
            "bundle_hash": self.bundle_hash,
            "source_id": self.source_id,
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
            "document_name": self.document_name,
            "evidence": [item.to_dict() for item in self.evidence],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class DocumentFileReceipt:
    source_id: str | None
    source_file: str
    format: str | None
    preflight_status: DocumentPreflightStatus
    status: DocumentFileStatus
    docling_status: str | None
    evidence_count: int
    bundle_id: str | None
    error_code: str | None
    error_message: str | None
    limitations: tuple[str, ...] = ()
    duplicate_of: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_file": self.source_file,
            "format": self.format,
            "preflight_status": self.preflight_status.value,
            "status": self.status.value,
            "docling_status": self.docling_status,
            "evidence_count": self.evidence_count,
            "bundle_id": self.bundle_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "limitations": list(self.limitations),
            "duplicate_of": self.duplicate_of,
        }


@dataclass(frozen=True, slots=True)
class DocumentSourceReceipt:
    schema: str
    receipt_id: str
    status: DocumentBatchStatus
    docling_available: bool
    docling_version: str | None
    requested_count: int
    success_count: int
    partial_count: int
    failure_count: int
    duplicate_count: int
    evidence_count: int
    created_at: str
    files: tuple[DocumentFileReceipt, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "receipt_id": self.receipt_id,
            "status": self.status.value,
            "docling_available": self.docling_available,
            "docling_version": self.docling_version,
            "requested_count": self.requested_count,
            "success_count": self.success_count,
            "partial_count": self.partial_count,
            "failure_count": self.failure_count,
            "duplicate_count": self.duplicate_count,
            "evidence_count": self.evidence_count,
            "created_at": self.created_at,
            "files": [item.to_dict() for item in self.files],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class DocumentBatchResult:
    receipt: DocumentSourceReceipt
    bundles: tuple[DocumentEvidenceBundle, ...]


MetadataByPath = Mapping[str | Path, DocumentSourceMetadata]


__all__ = [
    "DOCUMENT_ADAPTER_NAME",
    "DOCUMENT_ADAPTER_VERSION",
    "DOCUMENT_EVIDENCE_SCHEMA",
    "DOCUMENT_SOURCE_SCHEMA",
    "DoclingDependencyInfo",
    "DocumentBatchResult",
    "DocumentBatchStatus",
    "DocumentBoundingBox",
    "DocumentEvidence",
    "DocumentEvidenceBundle",
    "DocumentEvidenceKind",
    "DocumentFileReceipt",
    "DocumentFileStatus",
    "DocumentFormat",
    "DocumentItemProvenance",
    "DocumentPreflightResult",
    "DocumentPreflightStatus",
    "DocumentProcessingEstimate",
    "DocumentProcessingShape",
    "DocumentSourceCandidate",
    "DocumentSourceMetadata",
    "DocumentSourcePolicy",
    "DocumentSourceReceipt",
    "MetadataByPath",
    "canonical_json_bytes",
    "sha256_bytes",
    "sha256_json",
]
