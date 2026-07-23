from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.planning_policy import CapacityPolicy, LeaveRange
from app.planning_scheduler import (
    PlanningCycleError,
    PlanningTask,
    TaskAccess,
    TaskPhase,
    add_bridge_tasks,
    schedule_tasks,
    stable_task_id,
    task_access,
    topological_order,
)


def task(
    stable_id: str,
    minutes: int,
    *,
    phase: TaskPhase = TaskPhase.FIRST_PASS,
    prerequisites: tuple[str, ...] = (),
    splittable: bool = True,
) -> PlanningTask:
    return PlanningTask(
        stable_id=stable_id,
        title=stable_id,
        estimated_minutes=minutes,
        phase=phase,
        prerequisites=prerequisites,
        splittable=splittable,
        expected_output=f"output:{stable_id}",
        acceptance=f"accept:{stable_id}",
    )


def test_stable_id_is_deterministic_and_semantically_scoped() -> None:
    first = stable_task_id("goal", "node", TaskPhase.FIRST_PASS)
    assert first == stable_task_id("goal", "node", TaskPhase.FIRST_PASS)
    assert first != stable_task_id("goal", "node", TaskPhase.REVIEW)


def test_topology_detects_cycles_and_bridge_fills_external_prerequisite() -> None:
    with pytest.raises(PlanningCycleError):
        topological_order((
            task("a", 10, prerequisites=("b",)),
            task("b", 10, prerequisites=("a",)),
        ))

    prepared = add_bridge_tasks((task("advanced", 20, prerequisites=("foundation",)),))
    assert prepared[0].stable_id == "bridge:foundation"
    assert prepared[1].prerequisites == ("bridge:foundation",)
    assert [item.stable_id for item in topological_order(prepared)] == [
        "bridge:foundation",
        "advanced",
    ]


@pytest.mark.parametrize("days", [7, 30, 90])
def test_schedule_reserves_40_50_first_pass_and_50_60_review(days: int) -> None:
    start = date(2026, 3, 2)
    end = start + timedelta(days=days - 1)
    policy = CapacityPolicy(
        daily_minutes=60,
        weekly_minutes=300,
        recurring_blackout_weekdays=frozenset({5, 6}),
    )
    result = schedule_tasks(
        (
            task("foundation", 60),
            task("practice", 60, prerequisites=("foundation",)),
            task("review-foundation", 60, phase=TaskPhase.REVIEW, prerequisites=("foundation",)),
        ),
        start=start,
        end=end,
        capacity_policy=policy,
        first_pass_ratio=0.45,
    )
    assert 0.40 <= result.first_pass_ratio <= 0.50
    assert 0.50 <= 1 - result.first_pass_ratio <= 0.60
    first_dates = [item.scheduled_date for item in result.tasks if item.phase == TaskPhase.FIRST_PASS]
    review_dates = [item.scheduled_date for item in result.tasks if item.phase == TaskPhase.REVIEW]
    assert first_dates and review_dates
    assert max(first_dates) <= min(review_dates)


def test_blackout_leave_and_weekly_capacity_are_honoured() -> None:
    start = date(2026, 4, 6)
    leave = date(2026, 4, 8)
    result = schedule_tasks(
        (task("long", 120),),
        start=start,
        end=start + timedelta(days=6),
        capacity_policy=CapacityPolicy(
            daily_minutes=40,
            weekly_minutes=120,
            recurring_blackout_weekdays=frozenset({5, 6}),
            blackout_dates=frozenset({date(2026, 4, 7)}),
            temporary_leave=(LeaveRange(leave, leave),),
        ),
    )
    scheduled_dates = {item.scheduled_date for item in result.tasks}
    assert date(2026, 4, 7) not in scheduled_dates
    assert leave not in scheduled_dates
    assert all(day.weekday() < 5 for day in scheduled_dates)
    assert sum(item.minutes for item in result.tasks) <= 120


def test_unsplittable_task_is_not_fragmented_and_infeasible_goal_is_reported() -> None:
    start = date(2026, 5, 4)
    result = schedule_tasks(
        (task("exam", 90, splittable=False),),
        start=start,
        end=start + timedelta(days=4),
        capacity_policy=CapacityPolicy(daily_minutes=60),
    )
    assert not result.tasks
    assert [item.stable_id for item in result.unscheduled] == ["exam"]
    assert result.feasibility["capacity_feasible"] is True
    assert result.feasibility["feasible"] is False
    assert result.feasibility["unscheduled_minutes"] == 90


def test_task_access_locks_future_and_allows_controlled_advance() -> None:
    today = date(2026, 6, 1)
    result = schedule_tasks(
        (
            task("first", 30),
            task("second", 30, prerequisites=("first",)),
        ),
        start=today,
        end=today + timedelta(days=4),
        capacity_policy=CapacityPolicy(daily_minutes=30),
    )
    first = next(item for item in result.tasks if item.source_task_id == "first")
    second = next(item for item in result.tasks if item.source_task_id == "second")
    assert task_access(first, today=today) == TaskAccess.AVAILABLE_TODAY
    assert task_access(second, today=today) == TaskAccess.BLOCKED_PREREQUISITE
    assert task_access(second, today=today, completed_ids={"first"}) == TaskAccess.LOCKED_FUTURE
    assert task_access(
        second,
        today=today,
        completed_ids={"first"},
        allow_advance=True,
        max_advance_days=2,
    ) == TaskAccess.AVAILABLE_ADVANCE
