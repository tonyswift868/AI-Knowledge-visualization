"""Conservative T0 extraction of exercises already present in source documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..contracts import (
    DocumentEvidence,
    DocumentEvidenceBundle,
    DocumentEvidenceKind,
    sha256_json,
)


class DocumentExerciseCandidateStatus(str, Enum):
    T0_CANDIDATE = "T0_CANDIDATE"
    NEEDS_ANSWER_VERIFICATION = "NEEDS_ANSWER_VERIFICATION"


@dataclass(frozen=True, slots=True)
class DocumentAnswerCandidate:
    content: str
    source_file: str
    docling_item_id: str
    evidence_id: str
    page_numbers: tuple[int, ...]
    section_path: tuple[str, ...]
    match_basis: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "source_file": self.source_file,
            "docling_item_id": self.docling_item_id,
            "evidence_id": self.evidence_id,
            "page_numbers": list(self.page_numbers),
            "section_path": list(self.section_path),
            "match_basis": self.match_basis,
        }


@dataclass(frozen=True, slots=True)
class DocumentExerciseCandidate:
    candidate_id: str
    candidate_tier: str
    status: DocumentExerciseCandidateStatus
    question: str
    source_id: str
    source_revision: str
    source_file: str
    bundle_id: str
    question_docling_item_id: str
    question_evidence_id: str
    question_page_numbers: tuple[int, ...]
    question_section_path: tuple[str, ...]
    answer: DocumentAnswerCandidate | None
    extraction_basis: str
    source_generated: bool = False

    def __post_init__(self) -> None:
        if self.candidate_tier != "T0":
            raise ValueError("document exercise candidates must remain T0")
        if self.source_generated:
            raise ValueError("source exercise extraction cannot generate questions")
        if (
            self.status is DocumentExerciseCandidateStatus.NEEDS_ANSWER_VERIFICATION
            and self.answer is not None
        ):
            raise ValueError("unverified-answer candidate cannot expose an answer")
        if (
            self.status is DocumentExerciseCandidateStatus.T0_CANDIDATE
            and self.answer is None
        ):
            raise ValueError("paired T0 candidate requires a source answer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_tier": self.candidate_tier,
            "status": self.status.value,
            "question": self.question,
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "source_file": self.source_file,
            "bundle_id": self.bundle_id,
            "question_docling_item_id": self.question_docling_item_id,
            "question_evidence_id": self.question_evidence_id,
            "question_page_numbers": list(self.question_page_numbers),
            "question_section_path": list(self.question_section_path),
            "answer": self.answer.to_dict() if self.answer else None,
            "extraction_basis": self.extraction_basis,
            "source_generated": self.source_generated,
        }


_QUESTION_PREFIX = re.compile(
    r"^\s*(?:question|exercise|quiz|example\s+problem|problem|q(?:uestion)?\s*\d*|"
    r"问题|练习|测验|例题|问)\s*(?:\d+[.)、]?\s*)?[:：.\-]\s*\S+",
    re.IGNORECASE,
)
_ANSWER_PREFIX = re.compile(
    r"^\s*(?:answer|solution|explanation|a(?:nswer)?\s*\d*|答案|解答|解析)"
    r"\s*[:：.\-]\s*\S+",
    re.IGNORECASE,
)
_SECTION_MARKER = re.compile(
    r"(?:questions?|exercises?|quiz(?:zes)?|q\s*&\s*a|example\s+problems?|"
    r"problems?|问题|练习|测验|问答|例题)",
    re.IGNORECASE,
)
_NUMBERED_ITEM = re.compile(r"^\s*(?:\d+[.)、]|[A-Z][.)])\s+\S+", re.IGNORECASE)
_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")


def _pages(evidence: DocumentEvidence) -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                provenance.page_no
                for provenance in evidence.provenance
                if provenance.page_no is not None
            }
        )
    )


def _section_is_exercise(section_path: tuple[str, ...]) -> bool:
    return any(_SECTION_MARKER.search(section) for section in section_path)


class DocumentExerciseExtractor:
    """Extract source-authored candidates; it never asks a model to invent one."""

    def __init__(self, *, max_answer_scan_items: int = 6) -> None:
        if max_answer_scan_items <= 0:
            raise ValueError("max_answer_scan_items must be positive")
        self.max_answer_scan_items = max_answer_scan_items

    def extract(
        self,
        bundle: DocumentEvidenceBundle,
    ) -> tuple[DocumentExerciseCandidate, ...]:
        evidence = tuple(bundle.evidence)
        first_content_for_section: dict[tuple[str, ...], str] = {}
        for item in evidence:
            if item.kind in {DocumentEvidenceKind.TEXT, DocumentEvidenceKind.TABLE}:
                first_content_for_section.setdefault(item.section_path, item.evidence_id)

        questions: list[tuple[int, DocumentEvidence, str]] = []
        candidates: list[DocumentExerciseCandidate] = []
        for index, item in enumerate(evidence):
            if item.kind is DocumentEvidenceKind.TABLE:
                candidates.extend(self._extract_table_candidates(bundle, item))
                continue
            if item.kind is not DocumentEvidenceKind.TEXT:
                continue
            basis = self._question_basis(item, first_content_for_section)
            if basis is not None:
                questions.append((index, item, basis))

        question_indices = {index for index, _item, _basis in questions}
        used_answer_ids: set[str] = set()
        for index, question, basis in questions:
            answer = self._find_answer(
                evidence,
                question_index=index,
                question_indices=question_indices,
                used_answer_ids=used_answer_ids,
            )
            if answer is not None:
                used_answer_ids.add(answer.evidence_id)
            candidates.append(
                self._candidate(
                    bundle,
                    question=question,
                    question_text=question.content,
                    answer=answer,
                    extraction_basis=basis,
                    location_suffix=None,
                )
            )
        return tuple(candidates)

    def extract_batch(
        self,
        bundles: tuple[DocumentEvidenceBundle, ...],
    ) -> tuple[DocumentExerciseCandidate, ...]:
        return tuple(candidate for bundle in bundles for candidate in self.extract(bundle))

    def _question_basis(
        self,
        item: DocumentEvidence,
        first_content_for_section: dict[tuple[str, ...], str],
    ) -> str | None:
        content = item.content.strip()
        if _QUESTION_PREFIX.match(content):
            return "EXPLICIT_SOURCE_QUESTION_MARKER"
        if _ANSWER_PREFIX.match(content):
            return None
        section_marked = _section_is_exercise(item.section_path)
        if section_marked and (content.endswith(("?", "？")) or _NUMBERED_ITEM.match(content)):
            return "EXERCISE_SECTION_QUESTION"
        if (
            section_marked
            and first_content_for_section.get(item.section_path) == item.evidence_id
        ):
            return "EXERCISE_SECTION_FIRST_ITEM"
        if content.endswith(("?", "？")):
            return "SOURCE_INTERROGATIVE"
        return None

    def _find_answer(
        self,
        evidence: tuple[DocumentEvidence, ...],
        *,
        question_index: int,
        question_indices: set[int],
        used_answer_ids: set[str],
    ) -> DocumentAnswerCandidate | None:
        stop = min(len(evidence), question_index + self.max_answer_scan_items + 1)
        for index in range(question_index + 1, stop):
            if index in question_indices:
                break
            item = evidence[index]
            if item.evidence_id in used_answer_ids:
                continue
            if item.kind is not DocumentEvidenceKind.TEXT:
                continue
            if not _ANSWER_PREFIX.match(item.content.strip()):
                continue
            return DocumentAnswerCandidate(
                content=item.content,
                source_file=item.source_file,
                docling_item_id=item.item_id,
                evidence_id=item.evidence_id,
                page_numbers=_pages(item),
                section_path=item.section_path,
                match_basis="FOLLOWING_EXPLICIT_SOURCE_ANSWER",
            )
        return None

    def _extract_table_candidates(
        self,
        bundle: DocumentEvidenceBundle,
        item: DocumentEvidence,
    ) -> list[DocumentExerciseCandidate]:
        rows = self._markdown_table_rows(item.content)
        if len(rows) < 2:
            return []
        header = [self._normalize_table_header(value) for value in rows[0]]
        question_column = self._column_index(header, _QUESTION_PREFIX, ("q", "question"))
        answer_column = self._column_index(
            header,
            _ANSWER_PREFIX,
            ("a", "answer", "solution", "explanation"),
        )
        if question_column is None:
            return []
        candidates: list[DocumentExerciseCandidate] = []
        for row_number, row in enumerate(rows[1:], start=1):
            if question_column >= len(row) or not row[question_column].strip():
                continue
            question_text = row[question_column].strip()
            answer_text = (
                row[answer_column].strip()
                if answer_column is not None and answer_column < len(row)
                else ""
            )
            answer = (
                DocumentAnswerCandidate(
                    content=answer_text,
                    source_file=item.source_file,
                    docling_item_id=item.item_id,
                    evidence_id=item.evidence_id,
                    page_numbers=_pages(item),
                    section_path=item.section_path,
                    match_basis=f"SOURCE_QA_TABLE_ROW_{row_number}",
                )
                if answer_text
                else None
            )
            candidates.append(
                self._candidate(
                    bundle,
                    question=item,
                    question_text=question_text,
                    answer=answer,
                    extraction_basis="SOURCE_QA_TABLE",
                    location_suffix=f"row:{row_number}",
                )
            )
        return candidates

    @staticmethod
    def _markdown_table_rows(content: str) -> list[list[str]]:
        rows: list[list[str]] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|") or not stripped.endswith("|"):
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and all(_TABLE_SEPARATOR.match(cell) for cell in cells):
                continue
            rows.append(cells)
        return rows

    @staticmethod
    def _column_index(
        header: list[str],
        marker: re.Pattern[str],
        exact_values: tuple[str, ...],
    ) -> int | None:
        for index, value in enumerate(header):
            if value in exact_values or marker.match(f"{value}: placeholder"):
                return index
        return None

    @staticmethod
    def _normalize_table_header(value: str) -> str:
        return re.sub(r"[*_`]", "", value).strip().casefold()

    @staticmethod
    def _candidate(
        bundle: DocumentEvidenceBundle,
        *,
        question: DocumentEvidence,
        question_text: str,
        answer: DocumentAnswerCandidate | None,
        extraction_basis: str,
        location_suffix: str | None,
    ) -> DocumentExerciseCandidate:
        identity = {
            "bundle_hash": bundle.bundle_hash,
            "question_evidence_id": question.evidence_id,
            "question": question_text,
            "answer_evidence_id": answer.evidence_id if answer else None,
            "answer": answer.content if answer else None,
            "location_suffix": location_suffix,
        }
        return DocumentExerciseCandidate(
            candidate_id=f"docex_{sha256_json(identity)[:40]}",
            candidate_tier="T0",
            status=(
                DocumentExerciseCandidateStatus.T0_CANDIDATE
                if answer is not None
                else DocumentExerciseCandidateStatus.NEEDS_ANSWER_VERIFICATION
            ),
            question=question_text,
            source_id=bundle.source_id,
            source_revision=bundle.source_sha256,
            source_file=bundle.source_file,
            bundle_id=bundle.bundle_id,
            question_docling_item_id=question.item_id,
            question_evidence_id=question.evidence_id,
            question_page_numbers=_pages(question),
            question_section_path=question.section_path,
            answer=answer,
            extraction_basis=extraction_basis,
        )


__all__ = [
    "DocumentAnswerCandidate",
    "DocumentExerciseCandidate",
    "DocumentExerciseCandidateStatus",
    "DocumentExerciseExtractor",
]
