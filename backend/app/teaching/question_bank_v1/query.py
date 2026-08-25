from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import (
    QuestionDifficulty,
    QuestionReviewHistory,
    QuestionSourceTier,
    QuestionValidationStatus,
    TrustedQuestion,
    TrustedQuestionBank,
)


@dataclass(frozen=True)
class QuestionBankFilter:
    concept_ids: tuple[str, ...] = ()
    difficulties: tuple[QuestionDifficulty, ...] = ()
    source_tiers: tuple[QuestionSourceTier, ...] = ()
    validation_statuses: tuple[QuestionValidationStatus, ...] = ()
    never_seen: bool = False
    previously_wrong: bool = False
    previously_passed: bool = False


class QuestionBankQuery:
    """Read-only selection API; it does not schedule review sessions or FSRS."""

    def __init__(
        self,
        bank: TrustedQuestionBank,
        *,
        history: Iterable[QuestionReviewHistory] = (),
    ) -> None:
        self.bank = bank
        aggregate: dict[str, list[int]] = {}
        for item in history:
            counts = aggregate.setdefault(item.question_id, [0, 0, 0])
            counts[0] += item.seen_count
            counts[1] += item.wrong_count
            counts[2] += item.passed_count
        self._history = {
            question_id: QuestionReviewHistory(
                question_id=question_id,
                seen_count=counts[0],
                wrong_count=counts[1],
                passed_count=counts[2],
            )
            for question_id, counts in aggregate.items()
        }

    def all(self) -> tuple[TrustedQuestion, ...]:
        return self.bank.questions

    def by_concept(self, concept_id: str) -> tuple[TrustedQuestion, ...]:
        return tuple(
            item for item in self.bank.questions if concept_id in item.concept_ids
        )

    def by_difficulty(
        self,
        difficulty: QuestionDifficulty,
    ) -> tuple[TrustedQuestion, ...]:
        return tuple(
            item for item in self.bank.questions if item.difficulty is difficulty
        )

    def by_source_tier(
        self,
        source_tier: QuestionSourceTier,
    ) -> tuple[TrustedQuestion, ...]:
        return tuple(
            item for item in self.bank.questions if item.source_tier is source_tier
        )

    def _history_for(self, question_id: str) -> QuestionReviewHistory:
        return self._history.get(
            question_id,
            QuestionReviewHistory(question_id),
        )

    def never_seen(self) -> tuple[TrustedQuestion, ...]:
        return tuple(
            item
            for item in self.bank.questions
            if self._history_for(item.question_id).seen_count == 0
        )

    def previously_wrong(self) -> tuple[TrustedQuestion, ...]:
        return tuple(
            item
            for item in self.bank.questions
            if self._history_for(item.question_id).wrong_count > 0
        )

    def previously_passed(self) -> tuple[TrustedQuestion, ...]:
        return tuple(
            item
            for item in self.bank.questions
            if self._history_for(item.question_id).passed_count > 0
        )

    def trusted_only(self) -> tuple[TrustedQuestion, ...]:
        return tuple(
            item
            for item in self.bank.questions
            if item.validation_status is QuestionValidationStatus.TRUSTED
        )

    def select(self, filters: QuestionBankFilter) -> tuple[TrustedQuestion, ...]:
        concept_ids = set(filters.concept_ids)
        difficulties = set(filters.difficulties)
        source_tiers = set(filters.source_tiers)
        statuses = set(filters.validation_statuses)
        selected = []
        for item in self.bank.questions:
            history = self._history_for(item.question_id)
            if concept_ids and not concept_ids.intersection(item.concept_ids):
                continue
            if difficulties and item.difficulty not in difficulties:
                continue
            if source_tiers and item.source_tier not in source_tiers:
                continue
            if statuses and item.validation_status not in statuses:
                continue
            if filters.never_seen and history.seen_count != 0:
                continue
            if filters.previously_wrong and history.wrong_count == 0:
                continue
            if filters.previously_passed and history.passed_count == 0:
                continue
            selected.append(item)
        return tuple(selected)
