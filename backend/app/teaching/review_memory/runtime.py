"""Pure review-memory application runtime around an injected scheduler port."""

from __future__ import annotations

import hashlib
import json
from datetime import timezone

from .mapping import ReviewEvaluationMappingPolicy
from .models import (
    MacroRoundBoundary,
    ReviewMemoryItem,
    ReviewMemoryReceipt,
    ReviewMemoryState,
    ReviewObservation,
    ReviewObservationOutcome,
    RoundAttemptCount,
)
from .queue import WeakItemPriorityPolicy
from .scheduler import ReviewSchedulerAdapter


class ReviewMemoryRuntimeError(RuntimeError):
    pass


class DuplicateReviewObservationError(ReviewMemoryRuntimeError):
    pass


class MacroRoundBoundaryError(ReviewMemoryRuntimeError):
    pass


def _unique_tail(
    values: tuple[str, ...],
    additions: tuple[str, ...],
    limit: int,
) -> tuple[str, ...]:
    combined: list[str] = list(values)
    for value in additions:
        if value in combined:
            combined.remove(value)
        combined.append(value)
    return tuple(combined[-limit:])


def _receipt_id(observation: ReviewObservation, review_log_id: str) -> str:
    payload = json.dumps(
        [observation.student_id, observation.observation_id, review_log_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ReviewMemoryRuntime:
    def __init__(
        self,
        scheduler: ReviewSchedulerAdapter,
        *,
        mapping_policy: ReviewEvaluationMappingPolicy | None = None,
        priority_policy: WeakItemPriorityPolicy | None = None,
    ) -> None:
        if not isinstance(scheduler, ReviewSchedulerAdapter):
            raise TypeError("scheduler must implement ReviewSchedulerAdapter")
        self.scheduler = scheduler
        self.mapping_policy = mapping_policy or ReviewEvaluationMappingPolicy()
        self.priority_policy = priority_policy or WeakItemPriorityPolicy()

    @staticmethod
    def add_item(
        state: ReviewMemoryState,
        item: ReviewMemoryItem,
    ) -> ReviewMemoryState:
        if item.student_id != state.student_id:
            raise ValueError("item student does not match review memory state")
        if item.item_id in state.item_by_id:
            raise ValueError(f"review memory item already exists: {item.item_id}")
        data = state.model_dump()
        data.update(
            items=tuple(sorted((*state.items, item), key=lambda value: value.item_id)),
            revision=state.revision + 1,
        )
        return ReviewMemoryState.model_validate(data)

    def apply(
        self,
        state: ReviewMemoryState,
        observation: ReviewObservation,
        *,
        boundary: MacroRoundBoundary,
    ) -> tuple[ReviewMemoryState, ReviewMemoryReceipt]:
        if observation.student_id != state.student_id:
            raise ValueError("observation student does not match review memory state")
        if observation.observation_id in state.processed_observation_ids:
            raise DuplicateReviewObservationError(observation.observation_id)
        item = state.item_by_id.get(observation.item_id)
        if item is None:
            raise KeyError(f"unknown review memory item: {observation.item_id}")
        if observation.macro_round_index != boundary.current_macro_round:
            raise MacroRoundBoundaryError(
                "observation macro round does not match product boundary"
            )
        expected_attempt = boundary.attempts_for(item.item_id) + 1
        if observation.same_round_attempt != expected_attempt:
            raise MacroRoundBoundaryError(
                "observation attempt does not match bounded same-round counter"
            )

        self.scheduler.validate_serialized_card(item.scheduler_card_json)
        rating_decision = self.mapping_policy.map(item, observation)
        scheduler_result = self.scheduler.review(
            item=item,
            rating=rating_decision.rating,
            reviewed_at=observation.observed_at,
            boundary=boundary,
        )
        if (
            scheduler_result.configured_macro_rounds
            != boundary.configured_macro_rounds
            or scheduler_result.macro_round_index != boundary.current_macro_round
        ):
            raise MacroRoundBoundaryError(
                "scheduler attempted to change the product macro-round boundary"
            )

        recalled = observation.outcome in {
            ReviewObservationOutcome.RECALLED,
            ReviewObservationOutcome.RECALLED_WITH_DIFFICULTY,
        }
        item_data = item.model_dump()
        item_data.update(
            {
                "evidence_ids": _unique_tail(
                    item.evidence_ids,
                    observation.evidence_ids,
                    64,
                ),
                "source_event_ids": _unique_tail(
                    item.source_event_ids,
                    observation.source_event_ids,
                    128,
                ),
                "last_review_at": observation.observed_at.astimezone(timezone.utc),
                "review_count": item.review_count + 1,
                "failure_count": item.failure_count
                + int(observation.outcome is ReviewObservationOutcome.FAILED),
                "consecutive_recalls": item.consecutive_recalls + 1 if recalled else 0,
                "hint_dependency": observation.hint_dependency,
                "difficulty": scheduler_result.difficulty,
                "stability": scheduler_result.stability,
                "retrievability": scheduler_result.retrievability,
                "due_at": scheduler_result.due_at,
                "last_observation": observation.outcome,
                "last_review_log_id": scheduler_result.review_log_id,
                "scheduler_card_json": scheduler_result.card_state_json,
            }
        )
        item_after = ReviewMemoryItem.model_validate(item_data)
        items = tuple(
            item_after if candidate.item_id == item_after.item_id else candidate
            for candidate in state.items
        )
        state_data = state.model_dump()
        state_data.update(
            {
                "items": items,
                "processed_observation_ids": _unique_tail(
                    state.processed_observation_ids,
                    (observation.observation_id,),
                    100_000,
                ),
                "revision": state.revision + 1,
                "updated_at": observation.observed_at.astimezone(timezone.utc),
            }
        )
        state_after = ReviewMemoryState.model_validate(state_data)
        attempts_by_item = {
            count.item_id: count.attempts for count in boundary.same_round_attempts
        }
        attempts_by_item[item.item_id] = expected_attempt
        boundary_after = MacroRoundBoundary(
            configured_macro_rounds=boundary.configured_macro_rounds,
            current_macro_round=boundary.current_macro_round,
            same_round_attempts=tuple(
                RoundAttemptCount(item_id=item_id, attempts=attempts)
                for item_id, attempts in sorted(attempts_by_item.items())
            ),
        )
        priority = self.priority_policy.priority(
            item_after,
            boundary=boundary_after,
            now=observation.observed_at,
        )
        receipt = ReviewMemoryReceipt(
            receipt_id=_receipt_id(observation, scheduler_result.review_log_id),
            observation=observation,
            item_before=item,
            item_after=item_after,
            rating_decision=rating_decision,
            scheduler_result=scheduler_result,
            priority=priority,
            state_revision=state_after.revision,
            configured_macro_rounds_before=boundary.configured_macro_rounds,
            configured_macro_rounds_after=boundary.configured_macro_rounds,
        )
        return state_after, receipt


__all__ = [
    "DuplicateReviewObservationError",
    "MacroRoundBoundaryError",
    "ReviewMemoryRuntime",
    "ReviewMemoryRuntimeError",
]
