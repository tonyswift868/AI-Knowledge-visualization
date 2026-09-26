"""Solver-independent delta contract for comparing two validated schedules."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .contracts import (
    REPLAN_SCHEMA_VERSION,
    AllocationKind,
    PlanScheduleProblem,
    PlanScheduleSolution,
    ScheduleAllocation,
    ScheduleStatus,
    _FrozenContract,
)
from .validation import validate_solution


class DeadlineImpact(StrEnum):
    UNCHANGED = "unchanged"
    EXTENDED = "extended"
    COMPRESSED = "compressed"
    RECOVERED = "recovered"
    AT_RISK = "at_risk"


class MovedTask(_FrozenContract):
    task_id: str = Field(min_length=1, max_length=160)
    old_dates: tuple[date, ...]
    new_dates: tuple[date, ...]
    old_minutes: int = Field(ge=0)
    new_minutes: int = Field(ge=0)


class DailyLoadChange(_FrozenContract):
    day: date
    old_minutes: int = Field(ge=0)
    new_minutes: int = Field(ge=0)
    delta_minutes: int

    @model_validator(mode="after")
    def validate_delta(self) -> "DailyLoadChange":
        if self.delta_minutes != self.new_minutes - self.old_minutes:
            raise ValueError("delta_minutes does not match old/new load")
        return self


class ReplanDelta(_FrozenContract):
    schema_version: Literal["mentorflow.replan-delta.v1"] = REPLAN_SCHEMA_VERSION
    old_problem_id: str = Field(min_length=1, max_length=160)
    new_problem_id: str = Field(min_length=1, max_length=160)
    old_solution_id: str = Field(min_length=1, max_length=160)
    new_solution_id: str = Field(min_length=1, max_length=160)
    cause: str = Field(min_length=1, max_length=1_000)
    moved_tasks: tuple[MovedTask, ...]
    changed_daily_load: tuple[DailyLoadChange, ...]
    completed_task_ids: tuple[str, ...]
    remaining_task_ids: tuple[str, ...]
    deadline_impact: DeadlineImpact
    deadline_days_delta: int


def _task_allocations(
    solution: PlanScheduleSolution,
) -> dict[str, tuple[ScheduleAllocation, ...]]:
    result: dict[str, list[ScheduleAllocation]] = {}
    for item in solution.allocations:
        if item.kind is AllocationKind.TASK and item.task_id is not None:
            result.setdefault(item.task_id, []).append(item)
    return {
        task_id: tuple(
            sorted(values, key=lambda item: (item.day, item.order_in_day))
        )
        for task_id, values in result.items()
    }


def _allocation_identity(
    values: tuple[ScheduleAllocation, ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            item.allocation_id,
            item.day,
            item.minutes,
            item.order_in_day,
            item.chunk_index,
            item.chunk_count,
            item.completed,
        )
        for item in values
    )


def _deadline_impact(
    old_problem: PlanScheduleProblem,
    old_solution: PlanScheduleSolution,
    new_problem: PlanScheduleProblem,
    new_solution: PlanScheduleSolution,
) -> DeadlineImpact:
    if (
        old_solution.status is not ScheduleStatus.FEASIBLE
        and new_solution.status is ScheduleStatus.FEASIBLE
    ):
        return DeadlineImpact.RECOVERED
    if (
        old_solution.status is ScheduleStatus.FEASIBLE
        and new_solution.status is not ScheduleStatus.FEASIBLE
    ):
        return DeadlineImpact.AT_RISK
    if new_problem.deadline > old_problem.deadline:
        return DeadlineImpact.EXTENDED
    if new_problem.deadline < old_problem.deadline:
        return DeadlineImpact.COMPRESSED
    return DeadlineImpact.UNCHANGED


def build_replan_delta(
    old_problem: PlanScheduleProblem,
    old_solution: PlanScheduleSolution,
    new_problem: PlanScheduleProblem,
    new_solution: PlanScheduleSolution,
    *,
    cause: str,
) -> ReplanDelta:
    """Compare solver results while enforcing immutable completed work."""

    old_validation = validate_solution(old_problem, old_solution)
    new_validation = validate_solution(new_problem, new_solution)
    if not old_validation.valid:
        raise ValueError("old schedule is not valid for its problem")
    if not new_validation.valid:
        raise ValueError("new schedule is not valid for its problem")

    old_by_task = _task_allocations(old_solution)
    new_by_task = _task_allocations(new_solution)
    old_completed = {
        item.task_id: item.allocations for item in old_problem.completed_tasks
    }
    new_completed = {
        item.task_id: item.allocations for item in new_problem.completed_tasks
    }
    removed_completed_ids = sorted(old_completed.keys() - new_completed.keys())
    if removed_completed_ids:
        raise ValueError(
            "previously completed tasks cannot become remaining: "
            + ", ".join(removed_completed_ids)
        )
    for task_id, old_values in old_completed.items():
        if _allocation_identity(old_values) != _allocation_identity(
            new_completed[task_id]
        ):
            raise ValueError(f"completed task changed during replan: {task_id}")

    completed_ids = tuple(sorted(new_problem.completed_task_ids))
    remaining_ids = tuple(
        sorted(
            task.task_id
            for task in new_problem.tasks
            if task.task_id not in new_problem.completed_task_ids
        )
    )
    moved: list[MovedTask] = []
    for task_id in remaining_ids:
        old_values = old_by_task.get(task_id, ())
        new_values = new_by_task.get(task_id, ())
        old_signature = tuple((item.day, item.minutes) for item in old_values)
        new_signature = tuple((item.day, item.minutes) for item in new_values)
        if old_signature != new_signature:
            moved.append(
                MovedTask(
                    task_id=task_id,
                    old_dates=tuple(item.day for item in old_values),
                    new_dates=tuple(item.day for item in new_values),
                    old_minutes=sum(item.minutes for item in old_values),
                    new_minutes=sum(item.minutes for item in new_values),
                )
            )

    old_loads = {item.day: item.expected_daily_minutes for item in old_solution.daily_loads}
    new_loads = {item.day: item.expected_daily_minutes for item in new_solution.daily_loads}
    load_changes = tuple(
        DailyLoadChange(
            day=day,
            old_minutes=old_loads.get(day, 0),
            new_minutes=new_loads.get(day, 0),
            delta_minutes=new_loads.get(day, 0) - old_loads.get(day, 0),
        )
        for day in sorted(old_loads.keys() | new_loads.keys())
        if old_loads.get(day, 0) != new_loads.get(day, 0)
    )
    return ReplanDelta(
        old_problem_id=old_problem.problem_id,
        new_problem_id=new_problem.problem_id,
        old_solution_id=old_solution.solution_id,
        new_solution_id=new_solution.solution_id,
        cause=cause,
        moved_tasks=tuple(moved),
        changed_daily_load=load_changes,
        completed_task_ids=completed_ids,
        remaining_task_ids=remaining_ids,
        deadline_impact=_deadline_impact(
            old_problem,
            old_solution,
            new_problem,
            new_solution,
        ),
        deadline_days_delta=(new_problem.deadline - old_problem.deadline).days,
    )


__all__ = [
    "DailyLoadChange",
    "DeadlineImpact",
    "MovedTask",
    "ReplanDelta",
    "build_replan_delta",
]
