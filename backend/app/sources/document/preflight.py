"""Safe, configuration-driven preflight for local document sources."""

from __future__ import annotations

import codecs
import hashlib
import importlib
import importlib.metadata
from pathlib import Path
from typing import Callable

from .contracts import (
    DoclingDependencyInfo,
    DocumentFormat,
    DocumentPreflightResult,
    DocumentPreflightStatus,
    DocumentProcessingEstimate,
    DocumentProcessingShape,
    DocumentSourceCandidate,
    DocumentSourceMetadata,
    DocumentSourcePolicy,
    sha256_bytes,
)


_FORMAT_BY_EXTENSION = {
    ".pdf": DocumentFormat.PDF,
    ".docx": DocumentFormat.DOCX,
    ".pptx": DocumentFormat.PPTX,
    ".html": DocumentFormat.HTML,
    ".htm": DocumentFormat.HTML,
    ".md": DocumentFormat.MARKDOWN,
    ".markdown": DocumentFormat.MARKDOWN,
}
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_READ_CHUNK_BYTES = 1024 * 1024
_SNIFF_BYTES = 8192


DependencyProbe = Callable[[], DoclingDependencyInfo]


def probe_docling_dependency() -> DoclingDependencyInfo:
    """Inspect Docling without importing conversion pipelines or loading models."""

    try:
        module = importlib.import_module("docling")
    except ModuleNotFoundError:
        return DoclingDependencyInfo(False, None, error_code="DOCLING_UNAVAILABLE")
    except Exception:
        return DoclingDependencyInfo(False, None, error_code="DOCLING_IMPORT_FAILED")
    try:
        version = importlib.metadata.version("docling")
    except importlib.metadata.PackageNotFoundError:
        version = None
    return DoclingDependencyInfo(
        True,
        version,
        module_file=str(getattr(module, "__file__", "") or "") or None,
    )


def _fingerprint_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_signature(format_: DocumentFormat, prefix: bytes) -> bool:
    if format_ is DocumentFormat.PDF:
        return prefix.startswith(b"%PDF-")
    if format_ in {DocumentFormat.DOCX, DocumentFormat.PPTX}:
        return prefix.startswith(_ZIP_SIGNATURES)
    if b"\x00" in prefix:
        return False
    try:
        decoder = codecs.getincrementaldecoder("utf-8-sig")()
        text = decoder.decode(prefix, final=False).lstrip().casefold()
    except UnicodeDecodeError:
        return False
    if format_ is DocumentFormat.HTML:
        return any(
            marker in text[:2048]
            for marker in ("<!doctype html", "<html", "<head", "<body", "<article")
        )
    return bool(text)


def _shape_for(
    policy: DocumentSourcePolicy,
    *,
    byte_count: int,
    unit_count: int | None,
) -> DocumentProcessingShape:
    ranks: list[int] = []
    if policy.shape_large_file_bytes is not None and byte_count >= policy.shape_large_file_bytes:
        ranks.append(3)
    elif (
        policy.shape_medium_file_bytes is not None
        and byte_count >= policy.shape_medium_file_bytes
    ):
        ranks.append(2)
    elif policy.shape_medium_file_bytes is not None or policy.shape_large_file_bytes is not None:
        ranks.append(1)
    if unit_count is not None:
        if policy.shape_large_units is not None and unit_count >= policy.shape_large_units:
            ranks.append(3)
        elif policy.shape_medium_units is not None and unit_count >= policy.shape_medium_units:
            ranks.append(2)
        elif policy.shape_medium_units is not None or policy.shape_large_units is not None:
            ranks.append(1)
    if not ranks:
        return DocumentProcessingShape.UNKNOWN
    return {
        1: DocumentProcessingShape.SMALL,
        2: DocumentProcessingShape.MEDIUM,
        3: DocumentProcessingShape.LARGE,
    }[max(ranks)]


def _unit_metadata(
    format_: DocumentFormat,
    metadata: DocumentSourceMetadata,
) -> tuple[str, int | None]:
    if format_ is DocumentFormat.PDF:
        return "pages", metadata.page_count
    if format_ is DocumentFormat.PPTX:
        return "slides", metadata.slide_count
    return "sections", metadata.section_count


class DocumentSourcePreflight:
    """Classify a local candidate before Docling conversion is attempted."""

    def __init__(
        self,
        policy: DocumentSourcePolicy,
        *,
        dependency_probe: DependencyProbe = probe_docling_dependency,
    ) -> None:
        self.policy = policy
        self._dependency_probe = dependency_probe

    def _resolve_candidate(self, path: str | Path) -> tuple[Path | None, str | None]:
        root = self.policy.input_root.resolve(strict=False)
        raw = Path(path)
        candidate = raw if raw.is_absolute() else root / raw
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return None, "DOCUMENT_PATH_OUTSIDE_INPUT_ROOT"
        return resolved, None

    def _failure(
        self,
        *,
        dependency: DoclingDependencyInfo,
        code: str,
        message: str,
        candidate: DocumentSourceCandidate | None = None,
        estimate: DocumentProcessingEstimate | None = None,
    ) -> DocumentPreflightResult:
        return DocumentPreflightResult(
            status=DocumentPreflightStatus.REJECTED,
            dependency=dependency,
            candidate=candidate,
            estimate=estimate,
            conversion_ready=False,
            error_code=code,
            error_message=message,
        )

    def inspect(
        self,
        path: str | Path,
        *,
        metadata: DocumentSourceMetadata | None = None,
    ) -> DocumentPreflightResult:
        dependency = self._dependency_probe()
        metadata = metadata or DocumentSourceMetadata()
        resolved, path_error = self._resolve_candidate(path)
        if resolved is None:
            return self._failure(
                dependency=dependency,
                code=path_error or "DOCUMENT_PATH_INVALID",
                message="Document path must remain inside the configured input root",
            )
        try:
            if not resolved.exists() or not resolved.is_file():
                return self._failure(
                    dependency=dependency,
                    code="DOCUMENT_FILE_NOT_FOUND",
                    message="Document source is not a readable file",
                )
            byte_count = resolved.stat().st_size
        except OSError:
            return self._failure(
                dependency=dependency,
                code="DOCUMENT_FILE_UNREADABLE",
                message="Document source metadata could not be read",
            )

        format_ = _FORMAT_BY_EXTENSION.get(resolved.suffix.casefold())
        if format_ is None:
            return self._failure(
                dependency=dependency,
                code="DOCUMENT_FORMAT_UNSUPPORTED",
                message="Only PDF, DOCX, PPTX, HTML, and Markdown are supported",
            )
        unit_kind, unit_count = _unit_metadata(format_, metadata)
        estimate = DocumentProcessingEstimate(
            byte_count=byte_count,
            unit_kind=unit_kind,
            unit_count=unit_count,
            shape=_shape_for(self.policy, byte_count=byte_count, unit_count=unit_count),
        )
        if byte_count <= 0:
            return self._failure(
                dependency=dependency,
                code="DOCUMENT_EMPTY",
                message="Document source is empty",
                estimate=estimate,
            )
        if (
            self.policy.hard_max_file_bytes is not None
            and byte_count > self.policy.hard_max_file_bytes
        ):
            return self._failure(
                dependency=dependency,
                code="DOCUMENT_FILE_SIZE_LIMIT",
                message="Document exceeds the caller-configured file-size limit",
                estimate=estimate,
            )

        hard_unit_limit: int | None
        warning_unit_limit: int | None
        if format_ is DocumentFormat.PDF:
            hard_unit_limit = self.policy.hard_max_pages
            warning_unit_limit = self.policy.warning_pages
        elif format_ is DocumentFormat.PPTX:
            hard_unit_limit = self.policy.hard_max_slides
            warning_unit_limit = self.policy.warning_slides
        else:
            hard_unit_limit = self.policy.hard_max_sections
            warning_unit_limit = self.policy.warning_sections
        if unit_count is not None and hard_unit_limit is not None and unit_count > hard_unit_limit:
            return self._failure(
                dependency=dependency,
                code="DOCUMENT_UNIT_COUNT_LIMIT",
                message="Document exceeds the caller-configured unit-count limit",
                estimate=estimate,
            )

        try:
            with resolved.open("rb") as stream:
                prefix = stream.read(_SNIFF_BYTES)
        except OSError:
            return self._failure(
                dependency=dependency,
                code="DOCUMENT_FILE_UNREADABLE",
                message="Document source bytes could not be read",
                estimate=estimate,
            )
        if not _valid_signature(format_, prefix):
            return self._failure(
                dependency=dependency,
                code="DOCUMENT_BYTES_INVALID",
                message="Document bytes do not match the declared format",
                estimate=estimate,
            )

        try:
            source_sha256 = _fingerprint_file(resolved)
            root = self.policy.input_root.resolve(strict=False)
            source_file = resolved.relative_to(root).as_posix()
        except OSError:
            return self._failure(
                dependency=dependency,
                code="DOCUMENT_FILE_UNREADABLE",
                message="Document source could not be fingerprinted",
                estimate=estimate,
            )
        candidate = DocumentSourceCandidate(
            source_id=f"docsrc_{sha256_bytes(('v1\0' + source_sha256).encode('ascii'))[:32]}",
            source_file=source_file,
            source_sha256=source_sha256,
            format=format_,
            byte_count=byte_count,
            metadata=metadata,
            resolved_path=resolved,
        )
        if not dependency.available:
            return DocumentPreflightResult(
                status=DocumentPreflightStatus.DEPENDENCY_UNAVAILABLE,
                dependency=dependency,
                candidate=candidate,
                estimate=estimate,
                conversion_ready=False,
                error_code="DOCLING_UNAVAILABLE",
                error_message="Docling is unavailable in the selected runtime",
            )

        limitations: list[str] = []
        conversion_ready = True
        if (
            self.policy.warning_file_bytes is not None
            and byte_count > self.policy.warning_file_bytes
        ):
            limitations.append("FILE_SIZE_WARNING")
        if (
            unit_count is not None
            and warning_unit_limit is not None
            and unit_count > warning_unit_limit
        ):
            limitations.append("UNIT_COUNT_WARNING")
        if format_ is DocumentFormat.PDF:
            limitations.append("PDF_OCR_DISABLED")
            if not self.policy.pdf_table_structure_enabled:
                limitations.append("PDF_TABLE_MODEL_DISABLED")
            artifacts = self.policy.pdf_artifacts_path
            if artifacts is None or not artifacts.is_dir():
                limitations.append("PDF_LOCAL_ARTIFACTS_UNAVAILABLE")
                conversion_ready = False
        status = (
            DocumentPreflightStatus.SUPPORTED_WITH_LIMITATION
            if limitations
            else DocumentPreflightStatus.SUPPORTED
        )
        return DocumentPreflightResult(
            status=status,
            dependency=dependency,
            candidate=candidate,
            estimate=estimate,
            conversion_ready=conversion_ready,
            limitations=tuple(limitations),
        )


__all__ = [
    "DependencyProbe",
    "DocumentSourcePreflight",
    "probe_docling_dependency",
]
