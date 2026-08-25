from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from ...knowledge.inventory.models import stable_hash
from .models import (
    SOURCE_TIER_RANK,
    QuestionSourceTier,
    QuestionValidationStatus,
    TrustedQuestion,
    TrustedQuestionBank,
)


@dataclass(frozen=True)
class QuestionExposureRecord:
    question_id: str
    round_index: int

    def __post_init__(self) -> None:
        if not self.question_id.strip() or self.round_index < 0:
            raise ValueError("question exposure record is invalid")


class QuestionExposureHistory(Protocol):
    def recent_exposures(
        self,
        learner_identity: str,
        *,
        limit: int,
    ) -> Iterable[QuestionExposureRecord]: ...

    def total_exposure_count(
        self,
        learner_identity: str,
        question_id: str,
    ) -> int: ...


@dataclass(frozen=True)
class ExposureSelectionResult:
    selection_id: str
    learner_identity: str
    selected_questions: tuple[TrustedQuestion, ...]
    suppressed_question_ids: tuple[str, ...]
    periodic_repeat_question_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        selected_ids = tuple(item.question_id for item in self.selected_questions)
        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError("exposure selection question IDs must be unique")
        if set(selected_ids) & set(self.suppressed_question_ids):
            raise ValueError("selected questions cannot also be suppressed")
        if not set(self.periodic_repeat_question_ids) <= set(selected_ids):
            raise ValueError("periodic repeats must be selected questions")

    def to_dict(self) -> dict[str, object]:
        return {
            "selection_id": self.selection_id,
            "learner_identity": self.learner_identity,
            "selected_question_ids": [
                item.question_id for item in self.selected_questions
            ],
            "suppressed_question_ids": list(self.suppressed_question_ids),
            "periodic_repeat_question_ids": list(
                self.periodic_repeat_question_ids
            ),
        }


class ExposureSafeQuestionSelector:
    """Avoid immediate/recent repetition while permitting periodic trusted T0 reuse."""

    def __init__(self, *, recent_window: int = 3) -> None:
        if recent_window < 1:
            raise ValueError("exposure recent window must be positive")
        self.recent_window = recent_window

    def select(
        self,
        bank: TrustedQuestionBank,
        *,
        history: QuestionExposureHistory,
        learner_identity: str,
        limit: int,
        concept_ids: Iterable[str] = (),
    ) -> ExposureSelectionResult:
        if not learner_identity.strip() or limit < 1:
            raise ValueError("learner identity and a positive limit are required")
        concepts = set(concept_ids)
        usable = tuple(
            item
            for item in bank.questions
            if item.validation_status
            in {
                QuestionValidationStatus.TRUSTED,
                QuestionValidationStatus.VALIDATED,
            }
            and (not concepts or concepts.intersection(item.concept_ids))
        )
        recent_records = tuple(
            history.recent_exposures(
                learner_identity,
                limit=self.recent_window,
            )
        )
        recent_ids = {item.question_id for item in recent_records}
        immediate_id = recent_records[0].question_id if recent_records else None
        counts = {
            item.question_id: history.total_exposure_count(
                learner_identity,
                item.question_id,
            )
            for item in usable
        }

        def order_key(item: TrustedQuestion) -> tuple[int, int, str]:
            return (
                counts[item.question_id],
                SOURCE_TIER_RANK[item.source_tier],
                item.question_id,
            )

        fresh = sorted(
            (item for item in usable if item.question_id not in recent_ids),
            key=order_key,
        )
        selected = fresh[:limit]
        selected_ids = {item.question_id for item in selected}
        periodic: list[str] = []
        if len(selected) < limit:
            repeatable_originals = sorted(
                (
                    item
                    for item in usable
                    if item.question_id in recent_ids
                    and item.question_id != immediate_id
                    and item.source_tier
                    is QuestionSourceTier.T0_SOURCE_ORIGINAL
                    and item.validation_status
                    is QuestionValidationStatus.TRUSTED
                    and item.question_id not in selected_ids
                ),
                key=order_key,
            )
            for item in repeatable_originals:
                if len(selected) >= limit:
                    break
                selected.append(item)
                selected_ids.add(item.question_id)
                periodic.append(item.question_id)
        suppressed = tuple(
            sorted(item.question_id for item in usable if item.question_id not in selected_ids)
        )
        ordered_selected = tuple(selected)
        payload = {
            "learner_identity": learner_identity,
            "periodic_repeat_question_ids": periodic,
            "selected_question_ids": [
                item.question_id for item in ordered_selected
            ],
            "suppressed_question_ids": suppressed,
        }
        return ExposureSelectionResult(
            selection_id=stable_hash("esqs", payload),
            learner_identity=learner_identity,
            selected_questions=ordered_selected,
            suppressed_question_ids=suppressed,
            periodic_repeat_question_ids=tuple(periodic),
        )


__all__ = [
    "ExposureSafeQuestionSelector",
    "ExposureSelectionResult",
    "QuestionExposureHistory",
    "QuestionExposureRecord",
]
