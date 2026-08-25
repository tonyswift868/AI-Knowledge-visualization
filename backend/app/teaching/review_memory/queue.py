"""Weak-item priority policy with bounded same-round retry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..contracts import HintDependency
from .models import (
    MacroRoundBoundary,
    ReviewMemoryItem,
    ReviewObservationOutcome,
    ReviewPriority,
    ReviewPriorityBand,
)


_WEAK_OUTCOMES = frozenset(
    {
        ReviewObservationOutcome.FAILED,
        ReviewObservationOutcome.PARTIAL,
        ReviewObservationOutcome.HINT_DEPENDENT,
        ReviewObservationOutcome.RECALLED_WITH_DIFFICULTY,
    }
)


@dataclass(frozen=True, slots=True)
class WeakItemPriorityPolicy:
    max_same_round_retries: int = 2

    def __post_init__(self) -> None:
        if not 0 <= self.max_same_round_retries <= 10:
            raise ValueError("max_same_round_retries must be between 0 and 10")

    def priority(
        self,
        item: ReviewMemoryItem,
        *,
        boundary: MacroRoundBoundary,
        now: datetime,
    ) -> ReviewPriority:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        current_time = now.astimezone(timezone.utc)
        score = 45.0
        reasons: list[str] = ["base_review_priority"]
        outcome_scores = {
            ReviewObservationOutcome.FAILED: 100.0,
            ReviewObservationOutcome.HINT_DEPENDENT: 90.0,
            ReviewObservationOutcome.PARTIAL: 80.0,
            ReviewObservationOutcome.RECALLED_WITH_DIFFICULTY: 60.0,
            ReviewObservationOutcome.RECALLED: 25.0,
            None: 45.0,
        }
        score = outcome_scores[item.last_observation]
        if item.last_observation is not None:
            reasons.append(f"last_observation:{item.last_observation.value}")

        failure_bonus = min(50.0, item.failure_count * 10.0)
        if failure_bonus:
            score += failure_bonus
            reasons.append("historical_failures_raise_priority")
        if item.hint_dependency is HintDependency.PERSISTENT:
            score += 35.0
            reasons.append("persistent_hint_dependency")
        elif item.hint_dependency is HintDependency.RECENT:
            score += 20.0
            reasons.append("recent_hint_dependency")

        if item.due_at is not None:
            seconds_overdue = (current_time - item.due_at.astimezone(timezone.utc)).total_seconds()
            if seconds_overdue >= 0:
                score += min(30.0, 5.0 + seconds_overdue / 86_400.0 * 2.0)
                reasons.append("due_or_overdue")
            else:
                score -= 10.0
                reasons.append("not_yet_due")

        if item.critical_weight > 1.0:
            score += (item.critical_weight - 1.0) * 30.0
            reasons.append("critical_concept_weight")

        if item.consecutive_recalls >= 3 and (item.stability or 0.0) >= 14.0:
            score -= min(35.0, 20.0 + item.consecutive_recalls * 2.0)
            reasons.append("stable_repeated_recall_lowers_priority")

        score = round(max(0.0, min(1_000.0, score)), 6)
        if score >= 140.0:
            band = ReviewPriorityBand.URGENT
        elif score >= 90.0:
            band = ReviewPriorityBand.HIGH
        elif score >= 45.0:
            band = ReviewPriorityBand.NORMAL
        else:
            band = ReviewPriorityBand.LOW

        completed_attempts = boundary.attempts_for(item.item_id)
        retries_already_used = max(0, completed_attempts - 1)
        remaining = max(0, self.max_same_round_retries - retries_already_used)
        weak = item.last_observation in _WEAK_OUTCOMES
        retry_allowed = weak and remaining > 0
        reasons.append(
            "same_round_retry_available"
            if retry_allowed
            else "same_round_retry_not_available"
        )
        return ReviewPriority(
            item_id=item.item_id,
            score=score,
            band=band,
            reasons=tuple(reasons),
            due_at=item.due_at,
            critical_weight=item.critical_weight,
            same_round_retry_allowed=retry_allowed,
            remaining_same_round_retries=remaining if weak else 0,
        )


class WeakItemQueue:
    """A deterministic, bounded view; it never creates product review rounds."""

    def __init__(self, entries: tuple[ReviewPriority, ...]) -> None:
        item_ids = tuple(item.item_id for item in entries)
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("weak queue item ids must be unique")
        self._entries = tuple(
            sorted(
                entries,
                key=lambda item: (
                    -item.score,
                    item.due_at or datetime.max.replace(tzinfo=timezone.utc),
                    item.item_id,
                ),
            )
        )

    @classmethod
    def from_items(
        cls,
        items: tuple[ReviewMemoryItem, ...],
        *,
        policy: WeakItemPriorityPolicy,
        boundary: MacroRoundBoundary,
        now: datetime,
    ) -> "WeakItemQueue":
        return cls(
            tuple(
                policy.priority(item, boundary=boundary, now=now) for item in items
            )
        )

    @property
    def entries(self) -> tuple[ReviewPriority, ...]:
        return self._entries

    def next_items(
        self,
        *,
        limit: int = 20,
        include_retry_exhausted: bool = True,
    ) -> tuple[ReviewPriority, ...]:
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        entries = self._entries
        if not include_retry_exhausted:
            entries = tuple(item for item in entries if item.same_round_retry_allowed)
        return entries[:limit]


__all__ = ["WeakItemPriorityPolicy", "WeakItemQueue"]
