"""Lossless DoclingDocument to MentorFlow generic evidence mapping."""

from __future__ import annotations

import math
from typing import Any, Iterable

from .contracts import (
    DOCUMENT_ADAPTER_NAME,
    DOCUMENT_ADAPTER_VERSION,
    DOCUMENT_EVIDENCE_SCHEMA,
    DocumentBoundingBox,
    DocumentEvidence,
    DocumentEvidenceBundle,
    DocumentEvidenceKind,
    DocumentItemProvenance,
    DocumentSourceCandidate,
    sha256_bytes,
    sha256_json,
)


class DocumentEvidenceBuildError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _enum_text(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    text = str(raw).strip()
    return text or None


def _reference_text(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "cref", value)
    text = str(raw).strip()
    return text or None


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _bbox(value: Any) -> DocumentBoundingBox | None:
    if value is None:
        return None
    left = _finite_float(getattr(value, "l", None))
    top = _finite_float(getattr(value, "t", None))
    right = _finite_float(getattr(value, "r", None))
    bottom = _finite_float(getattr(value, "b", None))
    if None in {left, top, right, bottom}:
        return None
    return DocumentBoundingBox(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        coord_origin=_enum_text(getattr(value, "coord_origin", None)),
    )


def _char_span(value: Any) -> tuple[int | None, int | None]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None, None
    start = _positive_int(value[0])
    end = _positive_int(value[1])
    if start is None or end is None or end < start:
        return None, None
    return start, end


def _provenance(item: Any) -> tuple[DocumentItemProvenance, ...]:
    result: list[DocumentItemProvenance] = []
    for raw in getattr(item, "prov", ()) or ():
        char_start, char_end = _char_span(getattr(raw, "charspan", None))
        result.append(
            DocumentItemProvenance(
                page_no=_positive_int(getattr(raw, "page_no", None)),
                bbox=_bbox(getattr(raw, "bbox", None)),
                char_start=char_start,
                char_end=char_end,
            )
        )
    return tuple(result)


def _kind(item: Any) -> DocumentEvidenceKind | None:
    label = (_enum_text(getattr(item, "label", None)) or "").casefold()
    type_name = type(item).__name__.casefold()
    if label in {"title", "section_header"} or "sectionheader" in type_name:
        return DocumentEvidenceKind.HEADING
    if label == "table" or "tableitem" in type_name:
        return DocumentEvidenceKind.TABLE
    text = getattr(item, "text", None)
    if isinstance(text, str) and text.strip():
        return DocumentEvidenceKind.TEXT
    return None


def _table_content(item: Any, document: Any) -> str:
    exporter = getattr(item, "export_to_markdown", None)
    if not callable(exporter):
        return ""
    try:
        value = exporter(document)
    except TypeError:
        value = exporter()
    return str(value or "").strip()


def _item_content(item: Any, kind: DocumentEvidenceKind, document: Any) -> str:
    if kind is DocumentEvidenceKind.TABLE:
        return _table_content(item, document)
    return str(getattr(item, "text", "") or "").strip()


def _heading_level(item: Any, hierarchy_level: int) -> int:
    label = (_enum_text(getattr(item, "label", None)) or "").casefold()
    if label == "title":
        return 1
    try:
        value = int(getattr(item, "level", hierarchy_level or 1))
    except (TypeError, ValueError, OverflowError):
        value = hierarchy_level or 1
    return max(1, value)


def _iterate_items(document: Any) -> Iterable[tuple[Any, int]]:
    iterator = getattr(document, "iterate_items", None)
    if not callable(iterator):
        raise DocumentEvidenceBuildError(
            "DOCLING_DOCUMENT_INVALID",
            "Converted document does not expose iterate_items",
        )
    try:
        return iterator(with_groups=True)
    except TypeError:
        return iterator()


class DocumentEvidenceBuilder:
    """Map structured items instead of flattening the document to Markdown."""

    def __init__(self, *, docling_version: str) -> None:
        self.docling_version = str(docling_version or "unknown")

    def build(
        self,
        document: Any,
        candidate: DocumentSourceCandidate,
    ) -> DocumentEvidenceBundle:
        evidence: list[DocumentEvidence] = []
        warnings: list[str] = []
        section_stack: list[str] = []
        seen_item_ids: set[str] = set()
        for raw_ordinal, pair in enumerate(_iterate_items(document), start=1):
            try:
                item, raw_level = pair
            except (TypeError, ValueError):
                raise DocumentEvidenceBuildError(
                    "DOCLING_ITEM_INVALID",
                    "Docling item iteration returned an invalid entry",
                ) from None
            kind = _kind(item)
            if kind is None:
                continue
            try:
                hierarchy_level = max(0, int(raw_level))
            except (TypeError, ValueError, OverflowError):
                hierarchy_level = 0
            content = _item_content(item, kind, document)
            if not content:
                warnings.append(f"EMPTY_{kind.value.upper()}_ITEM_SKIPPED")
                continue
            item_id = _reference_text(getattr(item, "self_ref", None)) or f"ordinal:{raw_ordinal}"
            if item_id in seen_item_ids:
                raise DocumentEvidenceBuildError(
                    "DUPLICATE_DOCUMENT_ITEM_ID",
                    "Docling document contains a duplicate item identity",
                )
            seen_item_ids.add(item_id)
            if kind is DocumentEvidenceKind.HEADING:
                heading_level = _heading_level(item, hierarchy_level)
                section_stack = section_stack[: heading_level - 1]
                section_stack.append(content)
            section_path = tuple(section_stack)
            content_hash = sha256_bytes(content.encode("utf-8"))
            provenance = _provenance(item)
            identity_payload = {
                "schema": DOCUMENT_EVIDENCE_SCHEMA,
                "source_id": candidate.source_id,
                "item_id": item_id,
                "kind": kind.value,
                "content_hash": content_hash,
                "provenance": [value.to_dict() for value in provenance],
            }
            evidence_id = f"docev_{sha256_json(identity_payload)[:40]}"
            evidence.append(
                DocumentEvidence(
                    evidence_id=evidence_id,
                    source_id=candidate.source_id,
                    source_file=candidate.source_file,
                    source_sha256=candidate.source_sha256,
                    item_id=item_id,
                    parent_item_id=_reference_text(getattr(item, "parent", None)),
                    ordinal=len(evidence) + 1,
                    hierarchy_level=hierarchy_level,
                    kind=kind,
                    content=content,
                    content_hash=content_hash,
                    section_path=section_path,
                    content_layer=_enum_text(getattr(item, "content_layer", None)),
                    provenance=provenance,
                )
            )
        document_name = str(
            getattr(document, "name", "") or _path_name(candidate.source_file)
        ).strip()
        bundle_payload = {
            "schema": DOCUMENT_EVIDENCE_SCHEMA,
            "adapter_name": DOCUMENT_ADAPTER_NAME,
            "adapter_version": DOCUMENT_ADAPTER_VERSION,
            "docling_version": self.docling_version,
            "source_id": candidate.source_id,
            "source_file": candidate.source_file,
            "source_sha256": candidate.source_sha256,
            "document_name": document_name,
            "evidence": [item.to_dict() for item in evidence],
            "warnings": warnings,
        }
        bundle_hash = sha256_json(bundle_payload)
        return DocumentEvidenceBundle(
            schema=DOCUMENT_EVIDENCE_SCHEMA,
            adapter_name=DOCUMENT_ADAPTER_NAME,
            adapter_version=DOCUMENT_ADAPTER_VERSION,
            docling_version=self.docling_version,
            bundle_id=f"docbundle_{bundle_hash[:32]}",
            bundle_hash=bundle_hash,
            source_id=candidate.source_id,
            source_file=candidate.source_file,
            source_sha256=candidate.source_sha256,
            document_name=document_name,
            evidence=tuple(evidence),
            warnings=tuple(warnings),
        )


def _path_name(value: str) -> str:
    """Return a cross-platform filename without trusting the host separator."""

    normalized = str(value or "").replace("\\", "/").rstrip("/")
    return normalized.rsplit("/", 1)[-1] or "document"


__all__ = ["DocumentEvidenceBuildError", "DocumentEvidenceBuilder"]
