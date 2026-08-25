from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.teaching.contracts import HintDependency
from app.teaching.review_memory import (
    MacroRoundBoundary,
    ReviewMemoryItem,
    ReviewObservationOutcome,
    ReviewPriorityBand,
    RoundAttemptCount,
    WeakItemPriorityPolicy,
    WeakItemQueue,
)


NOW = datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc)


def _reviewed_item(
    item_id: str,
    outcome: ReviewObservationOutcome,
    **changes: object,
) -> ReviewMemoryItem:
    data: dict[str, object] = {
        "item_id": item_id,
        "student_id": "student-a",
        "concept_id": f"concept-{item_id}",
        "last_review_at": NOW - timedelta(days=1),
        "review_count": 1,
        "failure_count": int(outcome is ReviewObservationOutcome.FAILED),
        "consecutive_recalls": int(outcome is ReviewObservationOutcome.RECALLED),
        "last_observation": outcome,
        "last_review_log_id": f"log-{item_id}",
        "due_at": NOW - timedelta(hours=1),
        "scheduler_card_json": '{"fixture":true}',
    }
    data.update(changes)
    return ReviewMemoryItem(**data)


def test_weak_hint_and_partial_items_outrank_stable_recall() -> None:
    items = (
        _reviewed_item("failed", ReviewObservationOutcome.FAILED),
        _reviewed_item("partial", ReviewObservationOutcome.PARTIAL),
        _reviewed_item(
            "hint",
            ReviewObservationOutcome.HINT_DEPENDENT,
            hint_dependency=HintDependency.PERSISTENT,
        ),
        _reviewed_item(
            "stable",
            ReviewObservationOutcome.RECALLED,
            review_count=12,
            consecutive_recalls=9,
            stability=60.0,
        ),
    )
    queue = WeakItemQueue.from_items(
        items,
        policy=WeakItemPriorityPolicy(),
        boundary=MacroRoundBoundary(configured_macro_rounds=3, current_macro_round=1),
        now=NOW,
    )

    order = tuple(item.item_id for item in queue.entries)
    assert order.index("failed") < order.index("stable")
    assert order.index("partial") < order.index("stable")
    assert order.index("hint") < order.index("stable")
    assert queue.entries[0].band in {ReviewPriorityBand.URGENT, ReviewPriorityBand.HIGH}


def test_critical_concept_weight_is_explicit_and_bounded() -> None:
    ordinary = _reviewed_item("ordinary", ReviewObservationOutcome.PARTIAL)
    critical = _reviewed_item(
        "critical",
        ReviewObservationOutcome.PARTIAL,
        critical_weight=3.0,
    )
    policy = WeakItemPriorityPolicy()
    boundary = MacroRoundBoundary(configured_macro_rounds=3, current_macro_round=1)

    ordinary_priority = policy.priority(ordinary, boundary=boundary, now=NOW)
    critical_priority = policy.priority(critical, boundary=boundary, now=NOW)

    assert critical_priority.score > ordinary_priority.score
    assert "critical_concept_weight" in critical_priority.reasons


def test_same_round_retry_is_bounded_without_adding_macro_rounds() -> None:
    item = _reviewed_item("failed", ReviewObservationOutcome.FAILED)
    policy = WeakItemPriorityPolicy(max_same_round_retries=2)
    first_boundary = MacroRoundBoundary(
        configured_macro_rounds=3,
        current_macro_round=1,
        same_round_attempts=(RoundAttemptCount(item_id=item.item_id, attempts=1),),
    )
    exhausted_boundary = MacroRoundBoundary(
        configured_macro_rounds=3,
        current_macro_round=1,
        same_round_attempts=(RoundAttemptCount(item_id=item.item_id, attempts=3),),
    )

    first = policy.priority(item, boundary=first_boundary, now=NOW)
    exhausted = policy.priority(item, boundary=exhausted_boundary, now=NOW)

    assert first.remaining_same_round_retries == 2
    assert first.same_round_retry_allowed is True
    assert exhausted.remaining_same_round_retries == 0
    assert exhausted.same_round_retry_allowed is False
    assert first_boundary.configured_macro_rounds == 3
    assert exhausted_boundary.configured_macro_rounds == 3


def test_weak_item_rolls_down_after_repeated_stable_recall() -> None:
    weak = _reviewed_item(
        "rolling",
        ReviewObservationOutcome.FAILED,
        review_count=3,
        failure_count=2,
    )
    stable_data = weak.model_dump()
    stable_data.update(
        review_count=9,
        consecutive_recalls=6,
        last_observation=ReviewObservationOutcome.RECALLED,
        hint_dependency=HintDependency.NONE,
        stability=40.0,
    )
    stable = ReviewMemoryItem.model_validate(stable_data)
    policy = WeakItemPriorityPolicy()
    boundary = MacroRoundBoundary(configured_macro_rounds=3, current_macro_round=2)

    weak_priority = policy.priority(weak, boundary=boundary, now=NOW)
    stable_priority = policy.priority(stable, boundary=boundary, now=NOW)

    assert stable_priority.score < weak_priority.score
    assert "stable_repeated_recall_lowers_priority" in stable_priority.reasons


def test_large_fixture_prioritizes_1000_items_deterministically() -> None:
    items = tuple(
        _reviewed_item(
            f"item-{index:04d}",
            (
                ReviewObservationOutcome.FAILED
                if index % 11 == 0
                else ReviewObservationOutcome.HINT_DEPENDENT
                if index % 7 == 0
                else ReviewObservationOutcome.PARTIAL
                if index % 5 == 0
                else ReviewObservationOutcome.RECALLED
            ),
            review_count=10 if index % 5 not in {0} and index % 7 and index % 11 else 1,
            consecutive_recalls=(
                7 if index % 5 not in {0} and index % 7 and index % 11 else 0
            ),
            stability=(
                45.0 if index % 5 not in {0} and index % 7 and index % 11 else None
            ),
            hint_dependency=(
                HintDependency.PERSISTENT if index % 7 == 0 else HintDependency.NONE
            ),
            critical_weight=3.0 if index % 97 == 0 else 1.0,
        )
        for index in range(1_000)
    )
    boundary = MacroRoundBoundary(configured_macro_rounds=3, current_macro_round=2)

    first = WeakItemQueue.from_items(
        items,
        policy=WeakItemPriorityPolicy(),
        boundary=boundary,
        now=NOW,
    )
    second = WeakItemQueue.from_items(
        tuple(reversed(items)),
        policy=WeakItemPriorityPolicy(),
        boundary=boundary,
        now=NOW,
    )

    assert len(first.entries) == 1_000
    assert first.entries == second.entries
    assert len({entry.item_id for entry in first.entries}) == 1_000
    assert first.entries[0].score >= first.entries[-1].score
    assert first.next_items(limit=25) == first.entries[:25]
