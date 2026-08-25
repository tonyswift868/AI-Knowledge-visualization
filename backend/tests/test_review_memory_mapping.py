from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.teaching.contracts import HintDependency
from app.teaching.review_memory import (
    ReviewEvaluationMappingPolicy,
    ReviewMemoryItem,
    ReviewObservation,
    ReviewObservationOutcome,
    ReviewRating,
)


NOW = datetime(2026, 8, 25, 13, 0, tzinfo=timezone.utc)


def _item(**changes: object) -> ReviewMemoryItem:
    data: dict[str, object] = {
        "item_id": "item-1",
        "student_id": "student-a",
        "concept_id": "hash-map",
    }
    data.update(changes)
    return ReviewMemoryItem(**data)


def _observation(
    outcome: ReviewObservationOutcome,
    **changes: object,
) -> ReviewObservation:
    data: dict[str, object] = {
        "observation_id": f"observation-{outcome.value}",
        "student_id": "student-a",
        "item_id": "item-1",
        "concept_id": "hash-map",
        "outcome": outcome,
        "observed_at": NOW,
        "macro_round_index": 1,
    }
    data.update(changes)
    return ReviewObservation(**data)


@pytest.mark.parametrize(
    ("outcome", "changes", "expected"),
    [
        (ReviewObservationOutcome.FAILED, {}, ReviewRating.AGAIN),
        (ReviewObservationOutcome.PARTIAL, {}, ReviewRating.HARD),
        (
            ReviewObservationOutcome.HINT_DEPENDENT,
            {"hint_used": True, "hint_dependency": HintDependency.RECENT},
            ReviewRating.AGAIN,
        ),
        (
            ReviewObservationOutcome.RECALLED_WITH_DIFFICULTY,
            {},
            ReviewRating.HARD,
        ),
        (ReviewObservationOutcome.RECALLED, {}, ReviewRating.GOOD),
    ],
)
def test_business_outcomes_map_to_explainable_ratings(
    outcome: ReviewObservationOutcome,
    changes: dict[str, object],
    expected: ReviewRating,
) -> None:
    decision = ReviewEvaluationMappingPolicy().map(
        _item(),
        _observation(outcome, **changes),
    )

    assert decision.rating is expected
    assert decision.reasons
    assert decision.observation_id.startswith("observation-")


def test_correct_with_hint_or_hesitation_is_never_easy() -> None:
    stable = _item(
        review_count=5,
        consecutive_recalls=5,
        stability=30.0,
        due_at=NOW,
        last_review_at=NOW,
        last_observation=ReviewObservationOutcome.RECALLED,
        last_review_log_id="log",
        scheduler_card_json='{"fixture":true}',
    )
    policy = ReviewEvaluationMappingPolicy()
    hinted = policy.map(
        stable,
        _observation(
            ReviewObservationOutcome.RECALLED,
            hint_used=True,
            hint_dependency=HintDependency.RECENT,
            confidence=1.0,
            response_latency_seconds=1.0,
        ),
    )
    hesitant = policy.map(
        stable,
        _observation(
            ReviewObservationOutcome.RECALLED,
            hesitation_observed=True,
            confidence=1.0,
            response_latency_seconds=1.0,
        ),
    )

    assert hinted.rating is ReviewRating.HARD
    assert hesitant.rating is ReviewRating.HARD
    assert all("correct_does_not_imply_easy" in item.reasons for item in (hinted, hesitant))


def test_easy_requires_repeated_stable_fast_confident_recall() -> None:
    stable = _item(
        review_count=5,
        consecutive_recalls=4,
        stability=30.0,
        due_at=NOW,
        last_review_at=NOW,
        last_observation=ReviewObservationOutcome.RECALLED,
        last_review_log_id="log",
        scheduler_card_json='{"fixture":true}',
    )
    observation = _observation(
        ReviewObservationOutcome.RECALLED,
        confidence=0.98,
        response_latency_seconds=4.0,
    )
    decision = ReviewEvaluationMappingPolicy().map(stable, observation)

    assert decision.rating is ReviewRating.EASY
    assert decision.easy_gate_satisfied is True


def test_one_correct_answer_defaults_to_good_even_when_fast() -> None:
    observation = _observation(
        ReviewObservationOutcome.RECALLED,
        confidence=1.0,
        response_latency_seconds=1.0,
    )
    decision = ReviewEvaluationMappingPolicy().map(_item(), observation)

    assert decision.rating is ReviewRating.GOOD
    assert "correct_defaults_to_good_not_easy" in decision.reasons


def test_mapping_rejects_cross_item_observation() -> None:
    observation = _observation(ReviewObservationOutcome.RECALLED)
    mismatched = observation.model_copy(update={"item_id": "other"})

    with pytest.raises(ValueError, match="does not match"):
        ReviewEvaluationMappingPolicy().map(_item(), mismatched)
