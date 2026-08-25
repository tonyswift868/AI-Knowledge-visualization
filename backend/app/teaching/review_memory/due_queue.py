"""Durable FSRS due queue projected through the bounded weak-item policy."""

from __future__ import annotations

from datetime import datetime, timezone

from .durable_store import SqliteReviewMemoryStore
from .models import (
    MacroRoundBoundary,
    ReviewObservationOutcome,
)
from .queue import WeakItemPriorityPolicy, WeakItemQueue
from .service_models import ReviewPrioritySnapshot


_WEAK_OUTCOMES = frozenset(
    {
        ReviewObservationOutcome.FAILED,
        ReviewObservationOutcome.PARTIAL,
        ReviewObservationOutcome.HINT_DEPENDENT,
        ReviewObservationOutcome.RECALLED_WITH_DIFFICULTY,
    }
)


class ReviewDueQueue:
    def __init__(
        self,
        store: SqliteReviewMemoryStore,
        *,
        priority_policy: WeakItemPriorityPolicy | None = None,
    ) -> None:
        self.store = store
        self.priority_policy = priority_policy or WeakItemPriorityPolicy()

    def snapshot(
        self,
        *,
        student_id: str,
        boundary: MacroRoundBoundary,
        as_of: datetime,
        limit: int = 100,
        candidate_limit: int = 10_000,
    ) -> ReviewPrioritySnapshot:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        current = as_of.astimezone(timezone.utc)
        persisted = self.store.queue_candidates(
            student_id=student_id,
            as_of=current,
            limit=candidate_limit,
        )
        items = tuple(entry.item for entry in persisted)
        queue = WeakItemQueue.from_items(
            items,
            policy=self.priority_policy,
            boundary=boundary,
            now=current,
        )
        return ReviewPrioritySnapshot(
            student_id=student_id,
            generated_at=current,
            boundary=boundary,
            priorities=queue.next_items(limit=limit),
            candidate_count=len(items),
            due_count=sum(
                item.due_at is not None and item.due_at <= current for item in items
            ),
            weak_count=sum(item.last_observation in _WEAK_OUTCOMES for item in items),
        )


__all__ = ["ReviewDueQueue"]
