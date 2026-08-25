from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from ...knowledge.inventory.models import stable_hash
from .models import (
    QuestionValidationStatus,
    TrustedQuestion,
    TrustedQuestionBank,
)
from .versioning import QuestionVersion


class ReviewQuestionType(str, Enum):
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
    SHORT_ANSWER = "SHORT_ANSWER"
    TRUE_FALSE = "TRUE_FALSE"
    EXERCISE = "EXERCISE"
    OTHER = "OTHER"


class ReviewCoverageGapCode(str, Enum):
    NO_TRUSTED_QUESTION = "NO_TRUSTED_QUESTION"
    INSUFFICIENT_QUESTIONS = "INSUFFICIENT_QUESTIONS"
    DUPLICATE_RATIO_HIGH = "DUPLICATE_RATIO_HIGH"
    CRITICAL_CONCEPT_UNCOVERED = "CRITICAL_CONCEPT_UNCOVERED"
    QUESTION_TYPE_CONCENTRATION = "QUESTION_TYPE_CONCENTRATION"


@dataclass(frozen=True)
class ReviewQuestionMetadata:
    question_id: str
    question_type: ReviewQuestionType

    def __post_init__(self) -> None:
        if not self.question_id.strip():
            raise ValueError("review question metadata identity is required")

    @classmethod
    def from_version(cls, version: QuestionVersion) -> ReviewQuestionMetadata:
        try:
            question_type = ReviewQuestionType(version.question_type)
        except ValueError:
            question_type = ReviewQuestionType.OTHER
        return cls(
            question_id=version.question_id,
            question_type=question_type,
        )


@dataclass(frozen=True)
class ConceptCoverageSummary:
    concept_id: str
    critical: bool
    trusted_question_count: int
    usable_question_count: int
    candidate_question_count: int
    duplicate_ratio: float
    dominant_question_type: ReviewQuestionType | None
    dominant_question_type_ratio: float

    def __post_init__(self) -> None:
        if not self.concept_id.strip():
            raise ValueError("concept coverage summary identity is required")
        counts = (
            self.trusted_question_count,
            self.usable_question_count,
            self.candidate_question_count,
        )
        if min(counts) < 0:
            raise ValueError("concept coverage counts cannot be negative")
        if not 0 <= self.duplicate_ratio <= 1:
            raise ValueError("concept duplicate ratio must be between zero and one")
        if not 0 <= self.dominant_question_type_ratio <= 1:
            raise ValueError("question type ratio must be between zero and one")

    def to_dict(self) -> dict[str, object]:
        return {
            "concept_id": self.concept_id,
            "critical": self.critical,
            "trusted_question_count": self.trusted_question_count,
            "usable_question_count": self.usable_question_count,
            "candidate_question_count": self.candidate_question_count,
            "duplicate_ratio": self.duplicate_ratio,
            "dominant_question_type": (
                self.dominant_question_type.value
                if self.dominant_question_type is not None
                else None
            ),
            "dominant_question_type_ratio": self.dominant_question_type_ratio,
        }


@dataclass(frozen=True)
class ReviewCoverageGap:
    gap_id: str
    code: ReviewCoverageGapCode
    concept_id: str
    observed: float
    required: float

    def __post_init__(self) -> None:
        if not self.gap_id.strip() or not self.concept_id.strip():
            raise ValueError("coverage gap identities are required")
        if self.observed < 0 or self.required < 0:
            raise ValueError("coverage gap values cannot be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "gap_id": self.gap_id,
            "code": self.code.value,
            "concept_id": self.concept_id,
            "observed": self.observed,
            "required": self.required,
        }


@dataclass(frozen=True)
class ReviewCoveragePlan:
    plan_id: str
    bank_id: str
    concept_scope: tuple[str, ...]
    critical_concept_ids: tuple[str, ...]
    summaries: tuple[ConceptCoverageSummary, ...]
    gaps: tuple[ReviewCoverageGap, ...]
    minimum_trusted_questions: int
    maximum_duplicate_ratio: float
    maximum_question_type_ratio: float

    def __post_init__(self) -> None:
        if not self.plan_id.strip() or not self.bank_id.strip():
            raise ValueError("coverage plan and bank IDs are required")
        if not self.concept_scope:
            raise ValueError("coverage plan concept scope is required")
        if tuple(sorted(set(self.concept_scope))) != self.concept_scope:
            raise ValueError("coverage concept scope must be sorted and unique")
        if not set(self.critical_concept_ids) <= set(self.concept_scope):
            raise ValueError("critical concepts must be inside the concept scope")
        if len(self.summaries) != len(self.concept_scope):
            raise ValueError("coverage plan requires one summary per concept")

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "bank_id": self.bank_id,
            "concept_scope": list(self.concept_scope),
            "critical_concept_ids": list(self.critical_concept_ids),
            "summaries": [item.to_dict() for item in self.summaries],
            "gaps": [item.to_dict() for item in self.gaps],
            "minimum_trusted_questions": self.minimum_trusted_questions,
            "maximum_duplicate_ratio": self.maximum_duplicate_ratio,
            "maximum_question_type_ratio": self.maximum_question_type_ratio,
        }


def _metadata_by_question(
    metadata: Iterable[ReviewQuestionMetadata],
) -> dict[str, ReviewQuestionType]:
    result: dict[str, ReviewQuestionType] = {}
    for item in metadata:
        existing = result.get(item.question_id)
        if existing is not None and existing is not item.question_type:
            raise ValueError("question type metadata conflicts for one question")
        result[item.question_id] = item.question_type
    return result


def _gap(
    code: ReviewCoverageGapCode,
    concept_id: str,
    observed: float,
    required: float,
) -> ReviewCoverageGap:
    payload = {
        "code": code.value,
        "concept_id": concept_id,
        "observed": observed,
        "required": required,
    }
    return ReviewCoverageGap(
        gap_id=stable_hash("rcg", payload),
        code=code,
        concept_id=concept_id,
        observed=observed,
        required=required,
    )


class ReviewCoveragePlanner:
    def __init__(
        self,
        *,
        minimum_trusted_questions: int = 3,
        maximum_duplicate_ratio: float = 0.35,
        maximum_question_type_ratio: float = 0.8,
    ) -> None:
        if minimum_trusted_questions < 1:
            raise ValueError("minimum trusted question count must be positive")
        if not 0 <= maximum_duplicate_ratio <= 1:
            raise ValueError("maximum duplicate ratio must be between zero and one")
        if not 0 < maximum_question_type_ratio <= 1:
            raise ValueError("maximum question type ratio must be positive")
        self.minimum_trusted_questions = minimum_trusted_questions
        self.maximum_duplicate_ratio = maximum_duplicate_ratio
        self.maximum_question_type_ratio = maximum_question_type_ratio

    def plan(
        self,
        bank: TrustedQuestionBank,
        *,
        concept_scope: Iterable[str],
        critical_concept_ids: Iterable[str] = (),
        metadata: Iterable[ReviewQuestionMetadata] = (),
        candidate_questions: Iterable[TrustedQuestion] | None = None,
    ) -> ReviewCoveragePlan:
        scope = tuple(sorted(set(concept_scope)))
        critical = tuple(sorted(set(critical_concept_ids)))
        if not scope:
            raise ValueError("coverage planning requires a concept scope")
        if not set(critical) <= set(scope):
            raise ValueError("critical concepts must belong to the scope")
        question_types = _metadata_by_question(metadata)
        raw_candidates = tuple(
            bank.questions if candidate_questions is None else candidate_questions
        )
        summaries: list[ConceptCoverageSummary] = []
        gaps: list[ReviewCoverageGap] = []
        usable_statuses = {
            QuestionValidationStatus.TRUSTED,
            QuestionValidationStatus.VALIDATED,
        }
        for concept_id in scope:
            retained = tuple(
                item for item in bank.questions if concept_id in item.concept_ids
            )
            trusted = tuple(
                item
                for item in retained
                if item.validation_status is QuestionValidationStatus.TRUSTED
            )
            usable = tuple(
                item
                for item in retained
                if item.validation_status in usable_statuses
            )
            candidates = tuple(
                item for item in raw_candidates if concept_id in item.concept_ids
            )
            identities = {
                (
                    item.fingerprint.fingerprint_id,
                    item.answer_key.canonical_answer_signature,
                )
                for item in candidates
            }
            duplicate_ratio = (
                (len(candidates) - len(identities)) / len(candidates)
                if candidates
                else 0.0
            )
            type_counts = Counter(
                question_types.get(
                    item.question_id,
                    ReviewQuestionType.OTHER,
                )
                for item in usable
            )
            dominant_type = None
            dominant_ratio = 0.0
            if type_counts:
                dominant_type, dominant_count = min(
                    type_counts.items(),
                    key=lambda item: (-item[1], item[0].value),
                )
                dominant_ratio = dominant_count / len(usable)
            summary = ConceptCoverageSummary(
                concept_id=concept_id,
                critical=concept_id in critical,
                trusted_question_count=len(trusted),
                usable_question_count=len(usable),
                candidate_question_count=len(candidates),
                duplicate_ratio=duplicate_ratio,
                dominant_question_type=dominant_type,
                dominant_question_type_ratio=dominant_ratio,
            )
            summaries.append(summary)
            if not trusted:
                gaps.append(
                    _gap(
                        ReviewCoverageGapCode.NO_TRUSTED_QUESTION,
                        concept_id,
                        0,
                        1,
                    )
                )
            if len(trusted) < self.minimum_trusted_questions:
                gaps.append(
                    _gap(
                        ReviewCoverageGapCode.INSUFFICIENT_QUESTIONS,
                        concept_id,
                        len(trusted),
                        self.minimum_trusted_questions,
                    )
                )
            if duplicate_ratio > self.maximum_duplicate_ratio:
                gaps.append(
                    _gap(
                        ReviewCoverageGapCode.DUPLICATE_RATIO_HIGH,
                        concept_id,
                        duplicate_ratio,
                        self.maximum_duplicate_ratio,
                    )
                )
            if concept_id in critical and not trusted:
                gaps.append(
                    _gap(
                        ReviewCoverageGapCode.CRITICAL_CONCEPT_UNCOVERED,
                        concept_id,
                        0,
                        1,
                    )
                )
            if (
                len(usable) >= self.minimum_trusted_questions
                and dominant_ratio > self.maximum_question_type_ratio
            ):
                gaps.append(
                    _gap(
                        ReviewCoverageGapCode.QUESTION_TYPE_CONCENTRATION,
                        concept_id,
                        dominant_ratio,
                        self.maximum_question_type_ratio,
                    )
                )
        ordered_summaries = tuple(sorted(summaries, key=lambda item: item.concept_id))
        ordered_gaps = tuple(
            sorted(gaps, key=lambda item: (item.concept_id, item.code.value))
        )
        payload = {
            "bank_id": bank.bank_id,
            "concept_scope": scope,
            "critical_concept_ids": critical,
            "gaps": [item.to_dict() for item in ordered_gaps],
            "maximum_duplicate_ratio": self.maximum_duplicate_ratio,
            "maximum_question_type_ratio": self.maximum_question_type_ratio,
            "minimum_trusted_questions": self.minimum_trusted_questions,
            "summaries": [item.to_dict() for item in ordered_summaries],
        }
        return ReviewCoveragePlan(
            plan_id=stable_hash("rcp", payload),
            bank_id=bank.bank_id,
            concept_scope=scope,
            critical_concept_ids=critical,
            summaries=ordered_summaries,
            gaps=ordered_gaps,
            minimum_trusted_questions=self.minimum_trusted_questions,
            maximum_duplicate_ratio=self.maximum_duplicate_ratio,
            maximum_question_type_ratio=self.maximum_question_type_ratio,
        )


__all__ = [
    "ConceptCoverageSummary",
    "ReviewCoverageGap",
    "ReviewCoverageGapCode",
    "ReviewCoveragePlan",
    "ReviewCoveragePlanner",
    "ReviewQuestionMetadata",
    "ReviewQuestionType",
]
