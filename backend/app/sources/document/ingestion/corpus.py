"""Endpoint-neutral exercise corpus built only from source-authored candidates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..contracts import sha256_json
from .exercises import (
    DocumentAnswerCandidate,
    DocumentExerciseCandidate,
    DocumentExerciseCandidateStatus,
)


DOCUMENT_EXERCISE_CORPUS_SCHEMA = "mentorflow.document-exercise-corpus.v1"


@dataclass(frozen=True, slots=True)
class DocumentExerciseSourceLineage:
    candidate_id: str
    source_id: str
    source_revision: str
    source_file: str
    bundle_id: str
    question_docling_item_id: str
    question_evidence_id: str
    question_page_numbers: tuple[int, ...]
    question_section_path: tuple[str, ...]
    answer_docling_item_id: str | None
    answer_evidence_id: str | None
    answer_page_numbers: tuple[int, ...]
    answer_section_path: tuple[str, ...]
    extraction_basis: str

    @classmethod
    def from_candidate(
        cls,
        candidate: DocumentExerciseCandidate,
    ) -> "DocumentExerciseSourceLineage":
        answer = candidate.answer
        return cls(
            candidate_id=candidate.candidate_id,
            source_id=candidate.source_id,
            source_revision=candidate.source_revision,
            source_file=candidate.source_file,
            bundle_id=candidate.bundle_id,
            question_docling_item_id=candidate.question_docling_item_id,
            question_evidence_id=candidate.question_evidence_id,
            question_page_numbers=candidate.question_page_numbers,
            question_section_path=candidate.question_section_path,
            answer_docling_item_id=answer.docling_item_id if answer else None,
            answer_evidence_id=answer.evidence_id if answer else None,
            answer_page_numbers=answer.page_numbers if answer else (),
            answer_section_path=answer.section_path if answer else (),
            extraction_basis=candidate.extraction_basis,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "source_file": self.source_file,
            "bundle_id": self.bundle_id,
            "question_docling_item_id": self.question_docling_item_id,
            "question_evidence_id": self.question_evidence_id,
            "question_page_numbers": list(self.question_page_numbers),
            "question_section_path": list(self.question_section_path),
            "answer_docling_item_id": self.answer_docling_item_id,
            "answer_evidence_id": self.answer_evidence_id,
            "answer_page_numbers": list(self.answer_page_numbers),
            "answer_section_path": list(self.answer_section_path),
            "extraction_basis": self.extraction_basis,
        }


@dataclass(frozen=True, slots=True)
class DocumentExerciseCorpusEntry:
    entry_id: str
    candidate_tier: str
    status: DocumentExerciseCandidateStatus
    question: str
    answers: tuple[DocumentAnswerCandidate, ...]
    section_group_id: str
    concept_hints: tuple[str, ...]
    source_lineage: tuple[DocumentExerciseSourceLineage, ...]
    source_generated: bool = False

    def __post_init__(self) -> None:
        if self.candidate_tier != "T0":
            raise ValueError("document corpus entries must remain T0")
        if self.source_generated:
            raise ValueError("document corpus cannot generate questions")
        if (
            self.status is DocumentExerciseCandidateStatus.T0_CANDIDATE
            and not self.answers
        ):
            raise ValueError("paired T0 corpus entry requires a source answer")
        if (
            self.status
            is DocumentExerciseCandidateStatus.NEEDS_ANSWER_VERIFICATION
            and self.answers
        ):
            raise ValueError("unanswered corpus entry cannot expose an answer")

    @property
    def has_multiple_answers(self) -> bool:
        return len(self.answers) > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "candidate_tier": self.candidate_tier,
            "status": self.status.value,
            "question": self.question,
            "answers": [answer.to_dict() for answer in self.answers],
            "section_group_id": self.section_group_id,
            "concept_hints": list(self.concept_hints),
            "source_lineage": [item.to_dict() for item in self.source_lineage],
            "source_generated": self.source_generated,
            "has_multiple_answers": self.has_multiple_answers,
        }


@dataclass(frozen=True, slots=True)
class DocumentExerciseSectionGroup:
    group_id: str
    source_id: str
    source_revision: str
    source_file: str
    section_path: tuple[str, ...]
    entry_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "source_file": self.source_file,
            "section_path": list(self.section_path),
            "entry_ids": list(self.entry_ids),
        }


@dataclass(frozen=True, slots=True)
class DocumentExerciseCorpus:
    schema: str
    corpus_id: str
    entries: tuple[DocumentExerciseCorpusEntry, ...]
    section_groups: tuple[DocumentExerciseSectionGroup, ...]

    @property
    def unanswered_count(self) -> int:
        return sum(not entry.answers for entry in self.entries)

    @property
    def multiple_answer_count(self) -> int:
        return sum(entry.has_multiple_answers for entry in self.entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "corpus_id": self.corpus_id,
            "entries": [entry.to_dict() for entry in self.entries],
            "section_groups": [group.to_dict() for group in self.section_groups],
            "unanswered_count": self.unanswered_count,
            "multiple_answer_count": self.multiple_answer_count,
        }


class DocumentExerciseCorpusBuilder:
    """Pair, group, and retain lineage without generating learning content."""

    def build(
        self,
        candidates: Sequence[DocumentExerciseCandidate],
        *,
        concept_hints_by_candidate: Mapping[str, Sequence[str]] | None = None,
    ) -> DocumentExerciseCorpus:
        hints = concept_hints_by_candidate or {}
        buckets: dict[
            tuple[str, str, tuple[str, ...], str],
            list[DocumentExerciseCandidate],
        ] = {}
        for candidate in candidates:
            key = (
                candidate.source_id,
                candidate.source_revision,
                candidate.question_section_path,
                " ".join(candidate.question.casefold().split()),
            )
            buckets.setdefault(key, []).append(candidate)

        entries: list[DocumentExerciseCorpusEntry] = []
        groups: dict[
            tuple[str, str, str, tuple[str, ...]],
            list[str],
        ] = {}
        for key in buckets:
            bucket = buckets[key]
            first = bucket[0]
            answers = self._unique_answers(bucket)
            lineage = tuple(
                DocumentExerciseSourceLineage.from_candidate(candidate)
                for candidate in bucket
            )
            concept_hints = tuple(
                dict.fromkeys(
                    hint.strip()
                    for candidate in bucket
                    for hint in hints.get(candidate.candidate_id, ())
                    if hint.strip()
                )
            )
            group_key = (
                first.source_id,
                first.source_revision,
                first.source_file,
                first.question_section_path,
            )
            group_id = self._group_id(group_key)
            entry_identity = {
                "question": first.question,
                "source_id": first.source_id,
                "source_revision": first.source_revision,
                "section_path": list(first.question_section_path),
                "candidate_ids": [item.candidate_id for item in bucket],
                "answer_evidence_ids": [answer.evidence_id for answer in answers],
            }
            entry = DocumentExerciseCorpusEntry(
                entry_id=f"doccorpusentry_{sha256_json(entry_identity)[:40]}",
                candidate_tier="T0",
                status=(
                    DocumentExerciseCandidateStatus.T0_CANDIDATE
                    if answers
                    else DocumentExerciseCandidateStatus.NEEDS_ANSWER_VERIFICATION
                ),
                question=first.question,
                answers=answers,
                section_group_id=group_id,
                concept_hints=concept_hints,
                source_lineage=lineage,
            )
            entries.append(entry)
            groups.setdefault(group_key, []).append(entry.entry_id)

        section_groups = tuple(
            DocumentExerciseSectionGroup(
                group_id=self._group_id(group_key),
                source_id=group_key[0],
                source_revision=group_key[1],
                source_file=group_key[2],
                section_path=group_key[3],
                entry_ids=tuple(entry_ids),
            )
            for group_key, entry_ids in groups.items()
        )
        corpus_identity = {
            "schema": DOCUMENT_EXERCISE_CORPUS_SCHEMA,
            "entries": [entry.to_dict() for entry in entries],
            "section_groups": [group.to_dict() for group in section_groups],
        }
        return DocumentExerciseCorpus(
            schema=DOCUMENT_EXERCISE_CORPUS_SCHEMA,
            corpus_id=f"doccorpus_{sha256_json(corpus_identity)[:40]}",
            entries=tuple(entries),
            section_groups=section_groups,
        )

    @staticmethod
    def _unique_answers(
        candidates: Sequence[DocumentExerciseCandidate],
    ) -> tuple[DocumentAnswerCandidate, ...]:
        answers: list[DocumentAnswerCandidate] = []
        seen: set[tuple[str, str]] = set()
        for candidate in candidates:
            answer = candidate.answer
            if answer is None:
                continue
            key = (answer.evidence_id, answer.content)
            if key in seen:
                continue
            seen.add(key)
            answers.append(answer)
        return tuple(answers)

    @staticmethod
    def _group_id(
        key: tuple[str, str, str, tuple[str, ...]],
    ) -> str:
        identity = {
            "source_id": key[0],
            "source_revision": key[1],
            "source_file": key[2],
            "section_path": list(key[3]),
        }
        return f"docsection_{sha256_json(identity)[:40]}"


__all__ = [
    "DOCUMENT_EXERCISE_CORPUS_SCHEMA",
    "DocumentExerciseCorpus",
    "DocumentExerciseCorpusBuilder",
    "DocumentExerciseCorpusEntry",
    "DocumentExerciseSectionGroup",
    "DocumentExerciseSourceLineage",
]
