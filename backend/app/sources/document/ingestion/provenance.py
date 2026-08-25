"""Fail-closed provenance validation for converted document evidence."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts import (
    DocumentEvidenceBundle,
    DocumentEvidenceKind,
    DocumentFormat,
    sha256_bytes,
)


@dataclass(frozen=True, slots=True)
class DocumentProvenanceGateError(ValueError):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class DocumentProvenanceGate:
    """Verify evidence structure without inventing page or section metadata."""

    def validate(
        self,
        bundle: DocumentEvidenceBundle,
        *,
        source_type: DocumentFormat,
    ) -> tuple[str, ...]:
        if (
            not bundle.source_id.strip()
            or not bundle.source_file.strip()
            or len(bundle.source_sha256) != 64
        ):
            raise DocumentProvenanceGateError(
                "DOCUMENT_BUNDLE_SOURCE_INVALID",
                "Document bundle requires a source identity, file, and revision digest",
            )
        if not bundle.evidence:
            raise DocumentProvenanceGateError(
                "DOCUMENT_EVIDENCE_EMPTY",
                "Converted document did not produce structured text or table evidence",
            )

        evidence_ids: set[str] = set()
        item_ids: set[str] = set()
        page_backed_items = 0
        section_backed_items = 0
        content_items = 0
        for item in bundle.evidence:
            if (
                item.source_id != bundle.source_id
                or item.source_file != bundle.source_file
                or item.source_sha256 != bundle.source_sha256
            ):
                raise DocumentProvenanceGateError(
                    "DOCUMENT_EVIDENCE_SOURCE_MISMATCH",
                    "Evidence source does not match its document bundle",
                )
            if not item.evidence_id or item.evidence_id in evidence_ids:
                raise DocumentProvenanceGateError(
                    "DOCUMENT_EVIDENCE_ID_INVALID",
                    "Evidence identities must be non-empty and unique",
                )
            evidence_ids.add(item.evidence_id)
            if not item.item_id or item.item_id in item_ids:
                raise DocumentProvenanceGateError(
                    "DOCUMENT_ITEM_ID_INVALID",
                    "Document item identities must be non-empty and unique",
                )
            item_ids.add(item.item_id)
            if item.kind not in {
                DocumentEvidenceKind.HEADING,
                DocumentEvidenceKind.TEXT,
                DocumentEvidenceKind.TABLE,
            }:
                raise DocumentProvenanceGateError(
                    "DOCUMENT_EVIDENCE_KIND_INVALID",
                    "Only heading, text, and table evidence may pass the gate",
                )
            if not item.content.strip():
                raise DocumentProvenanceGateError(
                    "DOCUMENT_EVIDENCE_CONTENT_EMPTY",
                    "Evidence content must not be empty",
                )
            if item.content_hash != sha256_bytes(item.content.encode("utf-8")):
                raise DocumentProvenanceGateError(
                    "DOCUMENT_EVIDENCE_HASH_MISMATCH",
                    "Evidence content does not match its integrity digest",
                )
            if any(not section.strip() for section in item.section_path):
                raise DocumentProvenanceGateError(
                    "DOCUMENT_SECTION_HIERARCHY_INVALID",
                    "Section hierarchy entries must not be blank",
                )
            for provenance in item.provenance:
                if provenance.page_no is not None and provenance.page_no <= 0:
                    raise DocumentProvenanceGateError(
                        "DOCUMENT_PAGE_PROVENANCE_INVALID",
                        "Page or slide provenance numbers must be positive",
                    )
                if (
                    provenance.char_start is not None
                    and provenance.char_end is not None
                    and provenance.char_end < provenance.char_start
                ):
                    raise DocumentProvenanceGateError(
                        "DOCUMENT_CHAR_PROVENANCE_INVALID",
                        "Evidence character provenance must be ordered",
                    )
            if item.kind in {DocumentEvidenceKind.TEXT, DocumentEvidenceKind.TABLE}:
                content_items += 1
                if item.section_path:
                    section_backed_items += 1
            if any(value.page_no is not None for value in item.provenance):
                page_backed_items += 1

        limitations: list[str] = []
        if content_items and section_backed_items == 0:
            limitations.append("SECTION_HIERARCHY_UNAVAILABLE")
        elif section_backed_items < content_items:
            limitations.append("SECTION_HIERARCHY_PARTIAL")

        if page_backed_items == 0:
            if source_type is DocumentFormat.PPTX:
                limitations.append("EXACT_SLIDE_PROVENANCE_UNAVAILABLE")
            else:
                limitations.append("EXACT_PAGE_PROVENANCE_UNAVAILABLE")
        elif page_backed_items < len(bundle.evidence):
            if source_type is DocumentFormat.PPTX:
                limitations.append("EXACT_SLIDE_PROVENANCE_PARTIAL")
            else:
                limitations.append("EXACT_PAGE_PROVENANCE_PARTIAL")
        return tuple(limitations)


__all__ = ["DocumentProvenanceGate", "DocumentProvenanceGateError"]
