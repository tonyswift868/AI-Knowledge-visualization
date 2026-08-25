"""Docling-backed document conversion with per-file failure isolation."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .contracts import (
    DOCUMENT_SOURCE_SCHEMA,
    DoclingDependencyInfo,
    DocumentBatchResult,
    DocumentBatchStatus,
    DocumentEvidenceBundle,
    DocumentFileReceipt,
    DocumentFileStatus,
    DocumentPreflightResult,
    DocumentPreflightStatus,
    DocumentSourceMetadata,
    DocumentSourcePolicy,
    DocumentSourceReceipt,
    MetadataByPath,
    sha256_json,
)
from .evidence import DocumentEvidenceBuildError, DocumentEvidenceBuilder
from .preflight import DependencyProbe, DocumentSourcePreflight, probe_docling_dependency


class DoclingConverterProtocol(Protocol):
    def convert(self, source: Path, **kwargs: Any) -> Any: ...


ConverterFactory = Callable[[DocumentSourcePolicy], DoclingConverterProtocol]
Clock = Callable[[], datetime]


def build_docling_converter(policy: DocumentSourcePolicy) -> DoclingConverterProtocol:
    """Build a local-only converter with an explicit five-format allowlist.

    PDF conversion is enabled only when the caller supplies an existing local
    artifact directory.  OCR, VLM, external plugins, and remote services remain
    disabled.  Other v1 formats use Docling's declarative local backends.
    """

    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.backend_options import HTMLBackendOptions
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, HTMLFormatOption, PdfFormatOption

    allowed_formats = [
        InputFormat.DOCX,
        InputFormat.PPTX,
        InputFormat.HTML,
        InputFormat.MD,
    ]
    format_options: dict[Any, Any] = {
        InputFormat.HTML: HTMLFormatOption(
            backend_options=HTMLBackendOptions(
                enable_remote_fetch=False,
                enable_local_fetch=False,
                render_page=False,
                fetch_images=False,
            )
        )
    }
    artifacts = policy.pdf_artifacts_path
    if artifacts is not None and artifacts.is_dir():
        pdf_options = PdfPipelineOptions(
            artifacts_path=artifacts,
            enable_remote_services=False,
            allow_external_plugins=False,
            do_ocr=False,
            do_table_structure=policy.pdf_table_structure_enabled,
            do_picture_classification=False,
            do_picture_description=False,
            do_chart_extraction=False,
            do_code_enrichment=False,
            do_formula_enrichment=False,
            generate_page_images=False,
            generate_picture_images=False,
            generate_table_images=False,
            generate_parsed_pages=False,
        )
        allowed_formats.insert(0, InputFormat.PDF)
        format_options[InputFormat.PDF] = PdfFormatOption(
            pipeline_options=pdf_options,
            backend=PyPdfiumDocumentBackend,
        )
    return DocumentConverter(
        allowed_formats=allowed_formats,
        format_options=format_options,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_source_label(path: str | Path) -> str:
    normalized = str(path or "").replace("\\", "/").rstrip("/")
    return normalized.rsplit("/", 1)[-1] or "document"


def _safe_error_message(value: Any) -> str:
    message = " ".join(str(value or "").split()) or "Document conversion failed"
    message = re.sub(r"https?://\S+", "[redacted-url]", message, flags=re.IGNORECASE)
    message = re.sub(
        r"(?i)\b(api[-_ ]?key|authorization|cookie|password|secret|token)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        message,
    )
    message = re.sub(r"(?i)\b[A-Z]:\\[^\s]+", "[redacted-path]", message)
    return message[:400]


def _status_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "unknown").strip().casefold()


def _conversion_error(result: Any, *, fallback: str = "Docling conversion failed") -> str:
    messages: list[str] = []
    for error in getattr(result, "errors", ()) or ():
        raw = getattr(error, "error_message", None) or getattr(error, "message", None) or error
        if raw:
            messages.append(str(raw))
    return _safe_error_message("; ".join(messages) or fallback)


def _metadata_for(
    path: str | Path,
    metadata_by_path: MetadataByPath | None,
) -> DocumentSourceMetadata:
    if not metadata_by_path:
        return DocumentSourceMetadata()
    candidates: tuple[Any, ...] = (path, str(path), Path(path))
    for key in candidates:
        if key in metadata_by_path:
            return metadata_by_path[key]
    normalized = str(path).replace("\\", "/").casefold()
    for key, value in metadata_by_path.items():
        if str(key).replace("\\", "/").casefold() == normalized:
            return value
    return DocumentSourceMetadata()


class DoclingDocumentSource:
    """Convert one or many local files without coupling to an endpoint or DB."""

    def __init__(
        self,
        policy: DocumentSourcePolicy,
        *,
        dependency_probe: DependencyProbe = probe_docling_dependency,
        converter_factory: ConverterFactory = build_docling_converter,
        clock: Clock = _utc_now,
    ) -> None:
        self.policy = policy
        self.dependency: DoclingDependencyInfo = dependency_probe()
        self.preflight = DocumentSourcePreflight(
            policy,
            dependency_probe=lambda: self.dependency,
        )
        self._converter_factory = converter_factory
        self._clock = clock
        self._converter: DoclingConverterProtocol | None = None

    def _get_converter(self) -> DoclingConverterProtocol:
        if self._converter is None:
            self._converter = self._converter_factory(self.policy)
        return self._converter

    def _preflight_receipt(
        self,
        path: str | Path,
        result: DocumentPreflightResult,
    ) -> DocumentFileReceipt:
        candidate = result.candidate
        if result.status is DocumentPreflightStatus.DEPENDENCY_UNAVAILABLE:
            status = DocumentFileStatus.DEPENDENCY_UNAVAILABLE
        elif result.accepted and not result.conversion_ready:
            status = DocumentFileStatus.CONVERSION_FAILED
        else:
            status = DocumentFileStatus.REJECTED
        return DocumentFileReceipt(
            source_id=candidate.source_id if candidate else None,
            source_file=candidate.source_file if candidate else _safe_source_label(path),
            format=candidate.format.value if candidate else None,
            preflight_status=result.status,
            status=status,
            docling_status=None,
            evidence_count=0,
            bundle_id=None,
            error_code=result.error_code
            or ("DOCUMENT_PREFLIGHT_LIMITATION" if result.accepted else "DOCUMENT_REJECTED"),
            error_message=result.error_message
            or (
                "Document requires caller-provided local PDF artifacts"
                if result.accepted
                else "Document failed preflight"
            ),
            limitations=result.limitations,
        )

    def _convert_candidate(
        self,
        preflight: DocumentPreflightResult,
    ) -> tuple[DocumentFileReceipt, DocumentEvidenceBundle | None]:
        candidate = preflight.candidate
        assert candidate is not None and preflight.accepted and preflight.conversion_ready
        try:
            result = self._get_converter().convert(
                candidate.resolved_path,
                raises_on_error=False,
                max_num_pages=self.policy.hard_max_pages or sys.maxsize,
                max_file_size=self.policy.hard_max_file_bytes or sys.maxsize,
            )
        except Exception as exc:
            return (
                DocumentFileReceipt(
                    source_id=candidate.source_id,
                    source_file=candidate.source_file,
                    format=candidate.format.value,
                    preflight_status=preflight.status,
                    status=DocumentFileStatus.CONVERSION_FAILED,
                    docling_status="exception",
                    evidence_count=0,
                    bundle_id=None,
                    error_code="DOCLING_CONVERSION_EXCEPTION",
                    error_message=_safe_error_message(exc),
                    limitations=preflight.limitations,
                ),
                None,
            )
        docling_status = _status_text(getattr(result, "status", None))
        if docling_status not in {"success", "partial_success"}:
            return (
                DocumentFileReceipt(
                    source_id=candidate.source_id,
                    source_file=candidate.source_file,
                    format=candidate.format.value,
                    preflight_status=preflight.status,
                    status=DocumentFileStatus.CONVERSION_FAILED,
                    docling_status=docling_status,
                    evidence_count=0,
                    bundle_id=None,
                    error_code="DOCLING_CONVERSION_FAILED",
                    error_message=_conversion_error(result),
                    limitations=preflight.limitations,
                ),
                None,
            )
        document = getattr(result, "document", None)
        if document is None:
            return (
                DocumentFileReceipt(
                    source_id=candidate.source_id,
                    source_file=candidate.source_file,
                    format=candidate.format.value,
                    preflight_status=preflight.status,
                    status=DocumentFileStatus.CONVERSION_FAILED,
                    docling_status=docling_status,
                    evidence_count=0,
                    bundle_id=None,
                    error_code="DOCLING_DOCUMENT_MISSING",
                    error_message="Docling returned no structured document",
                    limitations=preflight.limitations,
                ),
                None,
            )
        try:
            bundle = DocumentEvidenceBuilder(
                docling_version=self.dependency.version or "unknown",
            ).build(document, candidate)
        except DocumentEvidenceBuildError as exc:
            return (
                DocumentFileReceipt(
                    source_id=candidate.source_id,
                    source_file=candidate.source_file,
                    format=candidate.format.value,
                    preflight_status=preflight.status,
                    status=DocumentFileStatus.CONVERSION_FAILED,
                    docling_status=docling_status,
                    evidence_count=0,
                    bundle_id=None,
                    error_code=exc.code,
                    error_message=exc.message,
                    limitations=preflight.limitations,
                ),
                None,
            )
        except Exception as exc:
            return (
                DocumentFileReceipt(
                    source_id=candidate.source_id,
                    source_file=candidate.source_file,
                    format=candidate.format.value,
                    preflight_status=preflight.status,
                    status=DocumentFileStatus.CONVERSION_FAILED,
                    docling_status=docling_status,
                    evidence_count=0,
                    bundle_id=None,
                    error_code="DOCUMENT_EVIDENCE_MAPPING_FAILED",
                    error_message=_safe_error_message(exc),
                    limitations=preflight.limitations,
                ),
                None,
            )
        if not bundle.evidence:
            return (
                DocumentFileReceipt(
                    source_id=candidate.source_id,
                    source_file=candidate.source_file,
                    format=candidate.format.value,
                    preflight_status=preflight.status,
                    status=DocumentFileStatus.CONVERSION_FAILED,
                    docling_status=docling_status,
                    evidence_count=0,
                    bundle_id=None,
                    error_code="DOCUMENT_NO_EVIDENCE",
                    error_message="Converted document contains no usable text or table evidence",
                    limitations=preflight.limitations,
                ),
                None,
            )
        file_status = (
            DocumentFileStatus.PARTIAL_SUCCESS
            if docling_status == "partial_success"
            else DocumentFileStatus.SUCCESS
        )
        partial_message = (
            _conversion_error(result, fallback="Docling reported partial conversion")
            if file_status is DocumentFileStatus.PARTIAL_SUCCESS
            else None
        )
        return (
            DocumentFileReceipt(
                source_id=candidate.source_id,
                source_file=candidate.source_file,
                format=candidate.format.value,
                preflight_status=preflight.status,
                status=file_status,
                docling_status=docling_status,
                evidence_count=len(bundle.evidence),
                bundle_id=bundle.bundle_id,
                error_code=(
                    "DOCLING_PARTIAL_SUCCESS"
                    if file_status is DocumentFileStatus.PARTIAL_SUCCESS
                    else None
                ),
                error_message=partial_message,
                limitations=preflight.limitations,
            ),
            bundle,
        )

    def convert_batch(
        self,
        paths: Sequence[str | Path],
        *,
        metadata_by_path: MetadataByPath | None = None,
    ) -> DocumentBatchResult:
        requested = tuple(paths)
        if self.policy.max_batch_files is not None and len(requested) > self.policy.max_batch_files:
            files = tuple(
                DocumentFileReceipt(
                    source_id=None,
                    source_file=_safe_source_label(path),
                    format=None,
                    preflight_status=DocumentPreflightStatus.REJECTED,
                    status=DocumentFileStatus.REJECTED,
                    docling_status=None,
                    evidence_count=0,
                    bundle_id=None,
                    error_code="DOCUMENT_BATCH_SIZE_LIMIT",
                    error_message="Batch exceeds the caller-configured file-count limit",
                )
                for path in requested
            )
            return self._result(files, ())

        files: list[DocumentFileReceipt] = []
        bundles: list[DocumentEvidenceBundle] = []
        seen_sources: dict[str, str] = {}
        for path in requested:
            preflight = self.preflight.inspect(
                path,
                metadata=_metadata_for(path, metadata_by_path),
            )
            if not preflight.accepted or not preflight.conversion_ready:
                files.append(self._preflight_receipt(path, preflight))
                continue
            candidate = preflight.candidate
            assert candidate is not None
            duplicate_of = seen_sources.get(candidate.source_id)
            if duplicate_of is not None:
                files.append(
                    DocumentFileReceipt(
                        source_id=candidate.source_id,
                        source_file=candidate.source_file,
                        format=candidate.format.value,
                        preflight_status=preflight.status,
                        status=DocumentFileStatus.DUPLICATE,
                        docling_status=None,
                        evidence_count=0,
                        bundle_id=None,
                        error_code="DUPLICATE_DOCUMENT_SOURCE",
                        error_message=(
                            "Duplicate document content was already processed in this batch"
                        ),
                        limitations=preflight.limitations,
                        duplicate_of=duplicate_of,
                    )
                )
                continue
            seen_sources[candidate.source_id] = candidate.source_file
            file_receipt, bundle = self._convert_candidate(preflight)
            files.append(file_receipt)
            if bundle is not None:
                bundles.append(bundle)
        return self._result(tuple(files), tuple(bundles))

    def convert_one(
        self,
        path: str | Path,
        *,
        metadata: DocumentSourceMetadata | None = None,
    ) -> DocumentBatchResult:
        metadata_map: Mapping[str | Path, DocumentSourceMetadata] | None = None
        if metadata is not None:
            metadata_map = {path: metadata}
        return self.convert_batch((path,), metadata_by_path=metadata_map)

    def _result(
        self,
        files: tuple[DocumentFileReceipt, ...],
        bundles: tuple[DocumentEvidenceBundle, ...],
    ) -> DocumentBatchResult:
        success_count = sum(item.status is DocumentFileStatus.SUCCESS for item in files)
        partial_count = sum(item.status is DocumentFileStatus.PARTIAL_SUCCESS for item in files)
        duplicate_count = sum(item.status is DocumentFileStatus.DUPLICATE for item in files)
        failure_count = len(files) - success_count - partial_count
        if files and success_count == len(files):
            status = DocumentBatchStatus.SUCCESS
        elif success_count or partial_count:
            status = DocumentBatchStatus.PARTIAL_SUCCESS
        else:
            status = DocumentBatchStatus.FAILURE
        receipt_payload = {
            "schema": DOCUMENT_SOURCE_SCHEMA,
            "status": status.value,
            "docling_available": self.dependency.available,
            "docling_version": self.dependency.version,
            "files": [item.to_dict() for item in files],
        }
        receipt_hash = sha256_json(receipt_payload)
        created_at = self._clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        receipt = DocumentSourceReceipt(
            schema=DOCUMENT_SOURCE_SCHEMA,
            receipt_id=f"docreceipt_{receipt_hash[:32]}",
            status=status,
            docling_available=self.dependency.available,
            docling_version=self.dependency.version,
            requested_count=len(files),
            success_count=success_count,
            partial_count=partial_count,
            failure_count=failure_count,
            duplicate_count=duplicate_count,
            evidence_count=sum(len(bundle.evidence) for bundle in bundles),
            created_at=created_at,
            files=files,
        )
        return DocumentBatchResult(receipt=receipt, bundles=bundles)


__all__ = [
    "ConverterFactory",
    "DoclingConverterProtocol",
    "DoclingDocumentSource",
    "build_docling_converter",
]
