"""Deterministic, persistence-free scheduler for the existing plan pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
from hashlib import sha256
from typing import Iterable, Mapping, Sequence

from .planning_policy import CapacityPolicy, capacity_calendar, feasibility_gap


class PlanningCycleError(ValueError):
    def __init__(self, cycle_nodes: Sequence[str]):
        self.cycle_nodes = tuple(cycle_nodes)
        super().__init__(f"planning prerequisite cycle: {', '.join(self.cycle_nodes)}")


class TaskPhase(StrEnum):
    FIRST_PASS = "first_pass"
    REVIEW = "review"


@dataclass(frozen=True)
class PlanningTask:
    stable_id: str
    title: str
    estimated_minutes: int
    phase: TaskPhase = TaskPhase.FIRST_PASS
    prerequisites: tuple[str, ...] = ()
    splittable: bool = True
    minimum_chunk_minutes: int = 10
    expected_output: str = ""
    acceptance: str = ""
    bridge: bool = False

    def __post_init__(self) -> None:
        if not self.stable_id:
            raise ValueError("stable_id is required")
        if self.estimated_minutes <= 0:
            raise ValueError("estimated_minutes must be positive")
        if self.minimum_chunk_minutes <= 0:
            raise ValueError("minimum_chunk_minutes must be positive")
        if self.stable_id in self.prerequisites:
            raise PlanningCycleError((self.stable_id,))


@dataclass(frozen=True)
class ScheduledTask:
    stable_id: str
    source_task_id: str
    title: str
    scheduled_date: date
    minutes: int
    phase: TaskPhase
    chunk_index: int = 1
    chunk_count: int = 1
    prerequisites: tuple[str, ...] = ()
    expected_output: str = ""
    acceptance: str = ""
    bridge: bool = False


@dataclass(frozen=True)
class ScheduleResult:
    tasks: tuple[ScheduledTask, ...]
    unscheduled: tuple[PlanningTask, ...]
    capacities: Mapping[date, int]
    first_pass_capacity_minutes: int
    review_capacity_minutes: int
    first_pass_ratio: float
    feasibility: Mapping[str, int | bool]

    @property
    def scheduled_minutes(self) -> int:
        return sum(item.minutes for item in self.tasks)


def stable_task_id(goal_id: str, node_id: str, phase: TaskPhase | str, variant: str = "") -> str:
    raw = "\x1f".join((goal_id, node_id, str(phase), variant))
    return f"task_{sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def add_bridge_tasks(
    tasks: Sequence[PlanningTask],
    *,
    completed_ids: Iterable[str] = (),
    bridge_minutes: int = 15,
) -> tuple[PlanningTask, ...]:
    """Add explicit bridge tasks for prerequisites absent from the task set."""

    known = {task.stable_id for task in tasks}
    completed = set(completed_ids)
    missing = sorted({
        prerequisite
        for task in tasks
        for prerequisite in task.prerequisites
        if prerequisite not in known and prerequisite not in completed
    })
    bridges = tuple(
        PlanningTask(
            stable_id=f"bridge:{item}",
            title=f"补齐前置：{item}",
            estimated_minutes=bridge_minutes,
            splittable=False,
            expected_output=f"说明前置 {item} 的关键定义和一个例子",
            acceptance=f"能够独立解释 {item}",
            bridge=True,
        )
        for item in missing
    )
    remap = {item: f"bridge:{item}" for item in missing}
    remapped = tuple(
        replace(
            task,
            prerequisites=tuple(remap.get(item, item) for item in task.prerequisites),
        )
        for task in tasks
    )
    return bridges + remapped


def topological_order(
    tasks: Sequence[PlanningTask],
    *,
    completed_ids: Iterable[str] = (),
) -> tuple[PlanningTask, ...]:
    by_id = {task.stable_id: task for task in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("task stable_id values must be unique")
    completed = set(completed_ids)
    indegree = {task.stable_id: 0 for task in tasks}
    dependents: dict[str, list[str]] = {task.stable_id: [] for task in tasks}
    for task in tasks:
        for prerequisite in task.prerequisites:
            if prerequisite in completed:
                continue
            if prerequisite not in by_id:
                raise ValueError(f"missing prerequisite: {prerequisite}")
            indegree[task.stable_id] += 1
            dependents[prerequisite].append(task.stable_id)
    ready = [task.stable_id for task in tasks if indegree[task.stable_id] == 0]
    ordered: list[PlanningTask] = []
    while ready:
        current = ready.pop(0)
        ordered.append(by_id[current])
        for dependent in dependents[current]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if len(ordered) != len(tasks):
        raise PlanningCycleError([item for item, value in indegree.items() if value > 0])
    return tuple(ordered)


def _phase_capacity(
    capacities: Mapping[date, int],
    first_pass_ratio: float,
) -> tuple[dict[date, int], dict[date, int], int, int]:
    total = sum(capacities.values())
    first_target = round(total * first_pass_ratio)
    first: dict[date, int] = {}
    review: dict[date, int] = {}
    consumed = 0
    for day, value in capacities.items():
        first_value = min(value, max(0, first_target - consumed))
        first[day] = first_value
        review[day] = value - first_value
        consumed += first_value
    return first, review, sum(first.values()), sum(review.values())


def _pack_phase(
    tasks: Sequence[PlanningTask],
    capacities: Mapping[date, int],
    *,
    completed_ids: set[str],
) -> tuple[list[ScheduledTask], list[PlanningTask], set[str]]:
    remaining = dict(capacities)
    scheduled: list[ScheduledTask] = []
    unscheduled: list[PlanningTask] = []
    satisfied = set(completed_ids)
    for task in tasks:
        if any(item not in satisfied for item in task.prerequisites):
            unscheduled.append(task)
            continue
        left = task.estimated_minutes
        chunks: list[tuple[date, int]] = []
        if not task.splittable:
            slot = next((day for day, value in remaining.items() if value >= left), None)
            if slot is None:
                unscheduled.append(task)
                continue
            chunks.append((slot, left))
            remaining[slot] -= left
        else:
            for day, value in remaining.items():
                if left <= 0:
                    break
                if value < task.minimum_chunk_minutes:
                    continue
                chunk = min(left, value)
                leftover = left - chunk
                if 0 < leftover < task.minimum_chunk_minutes:
                    needed = task.minimum_chunk_minutes - leftover
                    if chunk - needed >= task.minimum_chunk_minutes:
                        chunk -= needed
                if chunk < task.minimum_chunk_minutes and chunk != left:
                    continue
                chunks.append((day, chunk))
                remaining[day] -= chunk
                left -= chunk
            if left > 0:
                for day, value in chunks:
                    remaining[day] += value
                unscheduled.append(task)
                continue
        chunk_count = len(chunks)
        for index, (day, minutes) in enumerate(chunks, 1):
            scheduled.append(ScheduledTask(
                stable_id=task.stable_id if chunk_count == 1 else f"{task.stable_id}:chunk:{index}",
                source_task_id=task.stable_id,
                title=task.title,
                scheduled_date=day,
                minutes=minutes,
                phase=task.phase,
                chunk_index=index,
                chunk_count=chunk_count,
                prerequisites=task.prerequisites,
                expected_output=task.expected_output,
                acceptance=task.acceptance,
                bridge=task.bridge,
            ))
        satisfied.add(task.stable_id)
    return scheduled, unscheduled, satisfied


def schedule_tasks(
    tasks: Sequence[PlanningTask],
    *,
    start: date,
    end: date,
    capacity_policy: CapacityPolicy,
    first_pass_ratio: float = 0.45,
    completed_ids: Iterable[str] = (),
    create_bridges: bool = True,
) -> ScheduleResult:
    if not 0.40 <= first_pass_ratio <= 0.50:
        raise ValueError("first_pass_ratio must be between 0.40 and 0.50")
    prepared = add_bridge_tasks(tasks, completed_ids=completed_ids) if create_bridges else tuple(tasks)
    ordered = topological_order(prepared, completed_ids=completed_ids)
    capacities = capacity_calendar(start, end, capacity_policy)
    first_capacity, review_capacity, first_total, review_total = _phase_capacity(
        capacities, first_pass_ratio
    )
    first_tasks = [task for task in ordered if task.phase == TaskPhase.FIRST_PASS]
    review_tasks = [task for task in ordered if task.phase == TaskPhase.REVIEW]
    completed = set(completed_ids)
    first_scheduled, first_unscheduled, satisfied = _pack_phase(
        first_tasks, first_capacity, completed_ids=completed
    )
    review_scheduled, review_unscheduled, _ = _pack_phase(
        review_tasks, review_capacity, completed_ids=satisfied
    )
    required = sum(task.estimated_minutes for task in prepared)
    feasibility = dict(feasibility_gap(required, capacities))
    unscheduled = first_unscheduled + review_unscheduled
    feasibility["capacity_feasible"] = feasibility["feasible"]
    feasibility["unscheduled_minutes"] = sum(task.estimated_minutes for task in unscheduled)
    feasibility["feasible"] = bool(feasibility["feasible"] and not unscheduled)
    return ScheduleResult(
        tasks=tuple(first_scheduled + review_scheduled),
        unscheduled=tuple(unscheduled),
        capacities=capacities,
        first_pass_capacity_minutes=first_total,
        review_capacity_minutes=review_total,
        first_pass_ratio=first_total / max(1, first_total + review_total),
        feasibility=feasibility,
    )


class TaskAccess(StrEnum):
    AVAILABLE_TODAY = "available_today"
    AVAILABLE_ADVANCE = "available_advance"
    LOCKED_FUTURE = "locked_future"
    BLOCKED_PREREQUISITE = "blocked_prerequisite"
    PAST_DUE = "past_due"


def task_access(
    task: ScheduledTask,
    *,
    today: date,
    completed_ids: Iterable[str] = (),
    allow_advance: bool = False,
    max_advance_days: int = 0,
) -> TaskAccess:
    completed = set(completed_ids)
    if any(item not in completed for item in task.prerequisites):
        return TaskAccess.BLOCKED_PREREQUISITE
    if task.scheduled_date < today:
        return TaskAccess.PAST_DUE
    if task.scheduled_date == today:
        return TaskAccess.AVAILABLE_TODAY
    if allow_advance and 0 < (task.scheduled_date - today).days <= max(0, max_advance_days):
        return TaskAccess.AVAILABLE_ADVANCE
    return TaskAccess.LOCKED_FUTURE
