"""Stable, reversible schedule diffs for local replanning.

The functions operate on values supplied by the existing plan service.  They
do not persist plans and therefore cannot become a competing source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from typing import Iterable, Sequence

from .planning_scheduler import ScheduledTask


class ConfirmationLevel(StrEnum):
    NONE = "none"
    AUTOMATIC = "automatic"
    USER_CONFIRMATION = "user_confirmation"


@dataclass(frozen=True)
class TaskChange:
    stable_id: str
    before: ScheduledTask | None
    after: ScheduledTask | None
    reason: str


@dataclass(frozen=True)
class ReplanPatch:
    reason: str
    changes: tuple[TaskChange, ...]
    confirmation_level: ConfirmationLevel
    affected_from: date | None

    @property
    def reversible(self) -> bool:
        return True

    def reverse(self) -> "ReplanPatch":
        return ReplanPatch(
            reason=f"undo:{self.reason}",
            changes=tuple(
                TaskChange(
                    stable_id=item.stable_id,
                    before=item.after,
                    after=item.before,
                    reason=f"undo:{item.reason}",
                )
                for item in self.changes
            ),
            confirmation_level=ConfirmationLevel.AUTOMATIC,
            affected_from=self.affected_from,
        )


def _by_id(tasks: Sequence[ScheduledTask]) -> dict[str, ScheduledTask]:
    result = {task.stable_id: task for task in tasks}
    if len(result) != len(tasks):
        raise ValueError("scheduled stable_id values must be unique")
    return result


def diff_schedules(
    before: Sequence[ScheduledTask],
    after: Sequence[ScheduledTask],
    *,
    reason: str,
    completed_ids: Iterable[str] = (),
    automatic_change_limit: int = 3,
    automatic_shift_days: int = 2,
) -> ReplanPatch:
    old = _by_id(before)
    new = _by_id(after)
    completed = set(completed_ids)
    changes: list[TaskChange] = []
    for stable_id in sorted(old.keys() | new.keys()):
        previous = old.get(stable_id)
        current = new.get(stable_id)
        if previous == current:
            continue
        if stable_id in completed and previous != current:
            raise ValueError(f"completed task cannot be changed: {stable_id}")
        change_reason = (
            "added" if previous is None else
            "removed" if current is None else
            "moved" if previous.scheduled_date != current.scheduled_date else
            "updated"
        )
        changes.append(TaskChange(stable_id, previous, current, change_reason))
    affected_dates = [
        item.scheduled_date
        for change in changes
        for item in (change.before, change.after)
        if item is not None
    ]
    max_shift = max(
        (
            abs((change.after.scheduled_date - change.before.scheduled_date).days)
            for change in changes
            if change.before is not None and change.after is not None
        ),
        default=0,
    )
    if not changes:
        confirmation = ConfirmationLevel.NONE
    elif len(changes) <= automatic_change_limit and max_shift <= automatic_shift_days:
        confirmation = ConfirmationLevel.AUTOMATIC
    else:
        confirmation = ConfirmationLevel.USER_CONFIRMATION
    return ReplanPatch(
        reason=reason,
        changes=tuple(changes),
        confirmation_level=confirmation,
        affected_from=min(affected_dates) if affected_dates else None,
    )


def apply_patch(
    current: Sequence[ScheduledTask],
    patch: ReplanPatch,
    *,
    completed_ids: Iterable[str] = (),
) -> tuple[ScheduledTask, ...]:
    values = _by_id(current)
    completed = set(completed_ids)
    for change in patch.changes:
        actual = values.get(change.stable_id)
        if actual != change.before:
            raise ValueError(f"patch precondition failed: {change.stable_id}")
        if change.stable_id in completed and change.before != change.after:
            raise ValueError(f"completed task cannot be changed: {change.stable_id}")
        if change.after is None:
            values.pop(change.stable_id, None)
        else:
            values[change.stable_id] = change.after
    return tuple(sorted(values.values(), key=lambda item: (item.scheduled_date, item.stable_id)))


def local_replan_patch(
    before: Sequence[ScheduledTask],
    proposed: Sequence[ScheduledTask],
    *,
    reason: str,
    affected_from: date,
    completed_ids: Iterable[str] = (),
) -> ReplanPatch:
    """Limit a proposed replan to the affected suffix and preserve completed work."""

    old = _by_id(before)
    proposed_by_id = _by_id(proposed)
    completed = set(completed_ids)
    merged: dict[str, ScheduledTask] = {}
    all_ids = old.keys() | proposed_by_id.keys()
    for stable_id in all_ids:
        previous = old.get(stable_id)
        candidate = proposed_by_id.get(stable_id)
        if stable_id in completed:
            if previous is None:
                raise ValueError(f"completed task missing from current schedule: {stable_id}")
            merged[stable_id] = previous
            continue
        if previous is not None and previous.scheduled_date < affected_from:
            merged[stable_id] = previous
            continue
        if candidate is not None:
            merged[stable_id] = candidate
    after = tuple(sorted(merged.values(), key=lambda item: (item.scheduled_date, item.stable_id)))
    patch = diff_schedules(
        before,
        after,
        reason=reason,
        completed_ids=completed,
    )
    return replace(patch, affected_from=affected_from)
