"""Evidence-quality accounting that never invents precise source locations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..contracts import (
    DocumentEvidenceBundle,
    DocumentEvidenceKind,
    DocumentFormat,
    sha256_json,
)
from .corpus import DocumentExerciseCorpus


DOCUMENT_PROVENANCE_QUALITY_SCHEMA = "mentorflow.document-provenance-quality.v1"


@dataclass(frozen=True, slots=True)
class DocumentProvenanceSourceQuality:
    source_file: str
    source_format: DocumentFormat | None
    evidence_count: int
    exact_page_count: int
    section_only_count: int
    slide_count: int
    unknown_precise_location_count: int
    table_source_count: int

    def __post_init__(self) -> None:
        located = (
            self.exact_page_count
            + self.section_only_count
            + self.slide_count
            + self.unknown_precise_location_count
        )
        if located != self.evidence_count:
            raise ValueError("provenance location counts must cover every evidence item")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "source_format": self.source_format.value if self.source_format else None,
            "evidence_count": self.evidence_count,
            "exact_page_count": self.exact_page_count,
            "section_only_count": self.section_only_count,
            "slide_count": self.slide_count,
            "unknown_precise_location_count": self.unknown_precise_location_count,
            "table_source_count": self.table_source_count,
        }


@dataclass(frozen=True, slots=True)
class DocumentProvenanceQualityReport:
    schema: str
    report_id: str
    sources: tuple[DocumentProvenanceSourceQuality, ...]
    evidence_count: int
    exact_page_count: int
    section_only_count: int
    slide_count: int
    unknown_precise_location_count: int
    table_source_count: int
    linked_answer_count: int
    unanswered_question_count: int
    multiple_answer_question_count: int

    def __post_init__(self) -> None:
        located = (
            self.exact_page_count
            + self.section_only_count
            + self.slide_count
            + self.unknown_precise_location_count
        )
        if located != self.evidence_count:
            raise ValueError("quality report location counts must cover all evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "report_id": self.report_id,
            "sources": [source.to_dict() for source in self.sources],
            "evidence_count": self.evidence_count,
            "exact_page_count": self.exact_page_count,
            "section_only_count": self.section_only_count,
            "slide_count": self.slide_count,
            "unknown_precise_location_count": self.unknown_precise_location_count,
            "table_source_count": self.table_source_count,
            "linked_answer_count": self.linked_answer_count,
            "unanswered_question_count": self.unanswered_question_count,
            "multiple_answer_question_count": self.multiple_answer_question_count,
        }


class DocumentProvenanceQualityReporter:
    def build(
        self,
        bundles: Sequence[DocumentEvidenceBundle],
        *,
        source_formats: Mapping[str, DocumentFormat],
        corpus: DocumentExerciseCorpus,
    ) -> DocumentProvenanceQualityReport:
        sources = tuple(
            self._source_quality(
                bundle,
                source_format=source_formats.get(bundle.source_file),
            )
            for bundle in bundles
        )
        payload = {
            "schema": DOCUMENT_PROVENANCE_QUALITY_SCHEMA,
            "sources": [source.to_dict() for source in sources],
            "corpus_id": corpus.corpus_id,
        }
        return DocumentProvenanceQualityReport(
            schema=DOCUMENT_PROVENANCE_QUALITY_SCHEMA,
            report_id=f"docprovquality_{sha256_json(payload)[:40]}",
            sources=sources,
            evidence_count=sum(source.evidence_count for source in sources),
            exact_page_count=sum(source.exact_page_count for source in sources),
            section_only_count=sum(source.section_only_count for source in sources),
            slide_count=sum(source.slide_count for source in sources),
            unknown_precise_location_count=sum(
                source.unknown_precise_location_count for source in sources
            ),
            table_source_count=sum(source.table_source_count for source in sources),
            linked_answer_count=sum(bool(entry.answers) for entry in corpus.entries),
            unanswered_question_count=corpus.unanswered_count,
            multiple_answer_question_count=corpus.multiple_answer_count,
        )

    @staticmethod
    def _source_quality(
        bundle: DocumentEvidenceBundle,
        *,
        source_format: DocumentFormat | None,
    ) -> DocumentProvenanceSourceQuality:
        exact_page = 0
        section_only = 0
        slide = 0
        unknown = 0
        has_table = False
        for evidence in bundle.evidence:
            has_precise_number = any(
                item.page_no is not None for item in evidence.provenance
            )
            if source_format is DocumentFormat.PPTX and has_precise_number:
                slide += 1
            elif has_precise_number:
                exact_page += 1
            elif evidence.section_path:
                section_only += 1
            else:
                unknown += 1
            if evidence.kind is DocumentEvidenceKind.TABLE:
                has_table = True
        return DocumentProvenanceSourceQuality(
            source_file=bundle.source_file,
            source_format=source_format,
            evidence_count=len(bundle.evidence),
            exact_page_count=exact_page,
            section_only_count=section_only,
            slide_count=slide,
            unknown_precise_location_count=unknown,
            table_source_count=int(has_table),
        )


__all__ = [
    "DOCUMENT_PROVENANCE_QUALITY_SCHEMA",
    "DocumentProvenanceQualityReport",
    "DocumentProvenanceQualityReporter",
    "DocumentProvenanceSourceQuality",
]
