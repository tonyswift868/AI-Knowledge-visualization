from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.teaching.contracts import HintDependency
from app.teaching.review_memory import (
    DuplicateReviewObservationError,
    MacroRoundBoundary,
    MacroRoundBoundaryError,
    ReviewMemoryItem,
    ReviewMemoryRuntime,
    ReviewMemoryState,
    ReviewObservation,
    ReviewObservationOutcome,
    ReviewRating,
    RoundAttemptCount,
    SchedulerReviewResult,
)


NOW = datetime(2026, 8, 25, 16, 0, tzinfo=timezone.utc)


class FakeReviewScheduler:
    adapter_id = "fake-review-scheduler.v1"

    def serialize_scheduler(self) -> str:
        return '{"adapter":"fake-review-scheduler.v1"}'

    def validate_serialized_card(self, card_state_json: str) -> None:
        value = json.loads(card_state_json)
        if not isinstance(value, dict):
            raise ValueError("fake card must be an object")

    def review(
        self,
        *,
        item: ReviewMemoryItem,
        rating: ReviewRating,
        reviewed_at: datetime,
        boundary: MacroRoundBoundary,
    ) -> SchedulerReviewResult:
        previous = json.loads(item.scheduler_card_json).get("reviews", 0)
        interval = {
            ReviewRating.AGAIN: 0,
            ReviewRating.HARD: 1,
            ReviewRating.GOOD: 3,
            ReviewRating.EASY: 7,
        }[rating]
        return SchedulerReviewResult(
            adapter_id=self.adapter_id,
            card_state_json=json.dumps(
                {"item_id": item.item_id, "reviews": previous + 1},
                sort_keys=True,
            ),
            review_log_id=f"fake-log:{item.item_id}:{previous + 1}:{rating.value}",
            due_at=reviewed_at + timedelta(days=interval),
            difficulty=max(1.0, 6.0 - interval / 2),
            stability=float(interval),
            retrievability=1.0,
            scheduled_at=reviewed_at,
            macro_round_index=boundary.current_macro_round,
            configured_macro_rounds=boundary.configured_macro_rounds,
        )


class ExpandingRoundScheduler(FakeReviewScheduler):
    def review(self, **kwargs: object) -> SchedulerReviewResult:
        result = super().review(**kwargs)
        return result.model_copy(update={"configured_macro_rounds": 17})


def _state() -> ReviewMemoryState:
    item = ReviewMemoryItem(
        item_id="item-1",
        student_id="student-a",
        concept_id="hash-map",
        question_id="question-1",
        critical_weight=2.0,
    )
    return ReviewMemoryState(
        student_id="student-a",
        items=(item,),
        updated_at=NOW,
    )


def _observation(
    observation_id: str,
    outcome: ReviewObservationOutcome,
    *,
    attempt: int = 1,
    hint: bool = False,
) -> ReviewObservation:
    return ReviewObservation(
        observation_id=observation_id,
        student_id="student-a",
        item_id="item-1",
        concept_id="hash-map",
        question_id="question-1",
        outcome=outcome,
        observed_at=NOW + timedelta(minutes=attempt),
        evidence_ids=(f"evidence-{attempt}",),
        source_event_ids=(f"event-{attempt}",),
        hint_used=hint,
        hint_dependency=HintDependency.RECENT if hint else HintDependency.NONE,
        macro_round_index=1,
        same_round_attempt=attempt,
    )


def test_runtime_applies_fake_adapter_and_returns_explainable_receipt() -> None:
    runtime = ReviewMemoryRuntime(FakeReviewScheduler())
    boundary = MacroRoundBoundary(configured_macro_rounds=3, current_macro_round=1)

    state_after, receipt = runtime.apply(
        _state(),
        _observation("observation-1", ReviewObservationOutcome.FAILED),
        boundary=boundary,
    )

    item = state_after.items[0]
    assert item.review_count == 1
    assert item.failure_count == 1
    assert item.evidence_ids == ("evidence-1",)
    assert item.source_event_ids == ("event-1",)
    assert item.last_review_log_id.startswith("fake-log:")
    assert item.difficulty is not None
    assert item.stability == 0.0
    assert item.retrievability == 1.0
    assert receipt.rating_decision.rating is ReviewRating.AGAIN
    assert receipt.mastery_changed is False
    assert receipt.configured_macro_rounds_before == 3
    assert receipt.configured_macro_rounds_after == 3
    assert receipt.priority.remaining_same_round_retries == 2


def test_bounded_same_round_retry_counts_down_without_new_rounds() -> None:
    runtime = ReviewMemoryRuntime(FakeReviewScheduler())
    state = _state()
    remaining: list[int] = []
    for attempt in range(1, 4):
        boundary = MacroRoundBoundary(
            configured_macro_rounds=3,
            current_macro_round=1,
            same_round_attempts=(
                RoundAttemptCount(item_id="item-1", attempts=attempt - 1),
            )
            if attempt > 1
            else (),
        )
        state, receipt = runtime.apply(
            state,
            _observation(
                f"observation-{attempt}",
                ReviewObservationOutcome.FAILED,
                attempt=attempt,
            ),
            boundary=boundary,
        )
        remaining.append(receipt.priority.remaining_same_round_retries)
        assert receipt.configured_macro_rounds_after == 3

    assert remaining == [2, 1, 0]
    assert receipt.priority.same_round_retry_allowed is False


def test_runtime_rejects_duplicate_and_boundary_mismatch() -> None:
    runtime = ReviewMemoryRuntime(FakeReviewScheduler())
    observation = _observation("same", ReviewObservationOutcome.PARTIAL)
    boundary = MacroRoundBoundary(configured_macro_rounds=3, current_macro_round=1)
    state, _ = runtime.apply(_state(), observation, boundary=boundary)

    with pytest.raises(DuplicateReviewObservationError):
        runtime.apply(state, observation, boundary=boundary)
    with pytest.raises(MacroRoundBoundaryError, match="attempt"):
        runtime.apply(
            _state(),
            observation.model_copy(update={"same_round_attempt": 2}),
            boundary=boundary,
        )


def test_scheduler_cannot_expand_three_product_rounds_to_seventeen() -> None:
    runtime = ReviewMemoryRuntime(ExpandingRoundScheduler())

    with pytest.raises(MacroRoundBoundaryError, match="change"):
        runtime.apply(
            _state(),
            _observation("observation-1", ReviewObservationOutcome.RECALLED),
            boundary=MacroRoundBoundary(
                configured_macro_rounds=3,
                current_macro_round=1,
            ),
        )


def test_good_or_easy_scheduler_rating_cannot_create_verified_mastery() -> None:
    runtime = ReviewMemoryRuntime(FakeReviewScheduler())
    state = _state()
    for index in range(1, 5):
        item = state.items[0]
        if index == 4:
            item_data = item.model_dump()
            item_data.update(
                review_count=4,
                consecutive_recalls=4,
                stability=30.0,
                last_review_at=NOW,
                last_observation=ReviewObservationOutcome.RECALLED,
                last_review_log_id="prior-log",
            )
            state = ReviewMemoryState(
                student_id=state.student_id,
                items=(ReviewMemoryItem.model_validate(item_data),),
                revision=state.revision,
                updated_at=state.updated_at,
                processed_observation_ids=state.processed_observation_ids,
            )
        observation = _observation(
            f"recall-{index}",
            ReviewObservationOutcome.RECALLED,
            attempt=1,
        ).model_copy(
            update={"confidence": 0.99, "response_latency_seconds": 2.0}
        )
        state, receipt = runtime.apply(
            state,
            observation,
            boundary=MacroRoundBoundary(
                configured_macro_rounds=3,
                current_macro_round=1,
            ),
        )
        assert receipt.mastery_changed is False
        assert "mastery" not in ReviewMemoryState.model_fields

    assert receipt.rating_decision.rating is ReviewRating.EASY
