"""Explainable business-outcome to scheduler-rating policy."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts import HintDependency
from .models import (
    ReviewMemoryItem,
    ReviewObservation,
    ReviewObservationOutcome,
    ReviewRating,
    ReviewRatingDecision,
)


@dataclass(frozen=True, slots=True)
class ReviewEvaluationMappingPolicy:
    easy_min_prior_reviews: int = 3
    easy_min_prior_recall_streak: int = 3
    easy_min_stability: float = 14.0
    easy_min_confidence: float = 0.9
    easy_max_latency_seconds: float = 12.0

    def __post_init__(self) -> None:
        if self.easy_min_prior_reviews < 1:
            raise ValueError("easy_min_prior_reviews must be positive")
        if self.easy_min_prior_recall_streak < 1:
            raise ValueError("easy_min_prior_recall_streak must be positive")
        if self.easy_min_stability < 0:
            raise ValueError("easy_min_stability cannot be negative")
        if not 0.0 <= self.easy_min_confidence <= 1.0:
            raise ValueError("easy_min_confidence must be between 0 and 1")
        if self.easy_max_latency_seconds <= 0:
            raise ValueError("easy_max_latency_seconds must be positive")

    def map(
        self,
        item: ReviewMemoryItem,
        observation: ReviewObservation,
    ) -> ReviewRatingDecision:
        if item.item_id != observation.item_id:
            raise ValueError("observation item does not match review memory item")
        if item.student_id != observation.student_id:
            raise ValueError("observation student does not match review memory item")
        if item.concept_id != observation.concept_id:
            raise ValueError("observation concept does not match review memory item")
        if item.question_id is not None and item.question_id != observation.question_id:
            raise ValueError("observation question does not match review memory item")

        outcome = observation.outcome
        if outcome is ReviewObservationOutcome.FAILED:
            return self._decision(observation, ReviewRating.AGAIN, "failed_recall")
        if outcome is ReviewObservationOutcome.HINT_DEPENDENT:
            return self._decision(
                observation,
                ReviewRating.AGAIN,
                "recall_required_hint",
                "assisted_recall_is_not_independent_recall",
            )
        if outcome is ReviewObservationOutcome.PARTIAL:
            return self._decision(
                observation,
                ReviewRating.HARD,
                "partial_recall",
                "partial_is_not_failure_but_requires_weak_item_priority",
            )
        if outcome is ReviewObservationOutcome.RECALLED_WITH_DIFFICULTY:
            return self._decision(
                observation,
                ReviewRating.HARD,
                "recalled_with_observed_difficulty",
            )
        if observation.hint_used or observation.hint_dependency is not HintDependency.NONE:
            return self._decision(
                observation,
                ReviewRating.HARD,
                "correct_recall_had_hint_dependency",
                "correct_does_not_imply_easy",
            )
        if observation.hesitation_observed:
            return self._decision(
                observation,
                ReviewRating.HARD,
                "correct_recall_had_clear_hesitation",
                "correct_does_not_imply_easy",
            )

        easy_gates = (
            item.review_count >= self.easy_min_prior_reviews,
            item.consecutive_recalls >= self.easy_min_prior_recall_streak,
            (item.stability or 0.0) >= self.easy_min_stability,
            observation.confidence is not None
            and observation.confidence >= self.easy_min_confidence,
            observation.response_latency_seconds is not None
            and observation.response_latency_seconds <= self.easy_max_latency_seconds,
        )
        if all(easy_gates):
            return ReviewRatingDecision(
                observation_id=observation.observation_id,
                rating=ReviewRating.EASY,
                reasons=(
                    "recalled_without_hint_or_hesitation",
                    "easy_requires_repeated_stable_recall",
                    "all_easy_gates_satisfied",
                ),
                easy_gate_satisfied=True,
            )
        return self._decision(
            observation,
            ReviewRating.GOOD,
            "recalled_without_failure",
            "correct_defaults_to_good_not_easy",
        )

    @staticmethod
    def _decision(
        observation: ReviewObservation,
        rating: ReviewRating,
        *reasons: str,
    ) -> ReviewRatingDecision:
        return ReviewRatingDecision(
            observation_id=observation.observation_id,
            rating=rating,
            reasons=tuple(reasons),
            easy_gate_satisfied=False,
        )


__all__ = ["ReviewEvaluationMappingPolicy"]
