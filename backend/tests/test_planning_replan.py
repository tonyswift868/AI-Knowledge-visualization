from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.planning_replan import (
    ConfirmationLevel,
    apply_patch,
    diff_schedules,
    local_replan_patch,
)
from app.planning_scheduler import ScheduledTask, TaskPhase


def scheduled(stable_id: str, day: date, minutes: int = 30) -> ScheduledTask:
    return ScheduledTask(
        stable_id=stable_id,
        source_task_id=stable_id,
        title=stable_id,
        scheduled_date=day,
        minutes=minutes,
        phase=TaskPhase.FIRST_PASS,
    )


def test_small_diff_is_automatic_and_reversible() -> None:
    start = date(2026, 7, 1)
    before = (scheduled("a", start), scheduled("b", start + timedelta(days=1)))
    after = (scheduled("a", start), scheduled("b", start + timedelta(days=2)))
    patch = diff_schedules(before, after, reason="temporary_leave")
    assert patch.confirmation_level == ConfirmationLevel.AUTOMATIC
    assert patch.affected_from == start + timedelta(days=1)
    assert apply_patch(before, patch) == after
    assert apply_patch(after, patch.reverse()) == before


def test_large_change_requires_confirmation() -> None:
    start = date(2026, 7, 1)
    before = tuple(scheduled(str(index), start + timedelta(days=index)) for index in range(5))
    after = tuple(scheduled(str(index), start + timedelta(days=index + 5)) for index in range(5))
    patch = diff_schedules(before, after, reason="capacity_reduced")
    assert patch.confirmation_level == ConfirmationLevel.USER_CONFIRMATION


def test_completed_task_is_immutable() -> None:
    start = date(2026, 7, 1)
    before = (scheduled("done", start),)
    after = (scheduled("done", start + timedelta(days=1)),)
    with pytest.raises(ValueError, match="completed task"):
        diff_schedules(before, after, reason="absence", completed_ids={"done"})


def test_local_replan_only_changes_affected_suffix() -> None:
    start = date(2026, 7, 1)
    before = (
        scheduled("past", start),
        scheduled("done", start + timedelta(days=1)),
        scheduled("future", start + timedelta(days=2)),
    )
    proposed = (
        scheduled("past", start + timedelta(days=6)),
        scheduled("done", start + timedelta(days=7)),
        scheduled("future", start + timedelta(days=4)),
    )
    patch = local_replan_patch(
        before,
        proposed,
        reason="missed_day",
        affected_from=start + timedelta(days=2),
        completed_ids={"done"},
    )
    result = apply_patch(before, patch, completed_ids={"done"})
    by_id = {item.stable_id: item for item in result}
    assert by_id["past"].scheduled_date == start
    assert by_id["done"].scheduled_date == start + timedelta(days=1)
    assert by_id["future"].scheduled_date == start + timedelta(days=4)
    assert apply_patch(result, patch.reverse(), completed_ids={"done"}) == before


def test_patch_rejects_stale_precondition() -> None:
    start = date(2026, 7, 1)
    before = (scheduled("a", start),)
    patch = diff_schedules(before, (scheduled("a", start + timedelta(days=1)),), reason="move")
    with pytest.raises(ValueError, match="precondition"):
        apply_patch((scheduled("a", start + timedelta(days=2)),), patch)
