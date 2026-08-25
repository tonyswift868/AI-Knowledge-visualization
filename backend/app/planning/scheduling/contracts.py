"""Solver-independent contracts for constrained learning-plan scheduling.

These models describe the problem a real constraint solver must solve and the
solution it must return.  They do not choose dates or implement a search
algorithm.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..effort import EffortEstimate


PROBLEM_SCHEMA_VERSION = "mentorflow.plan-schedule-problem.v1"
SOLUTION_SCHEMA_VERSION = "mentorflow.plan-schedule-solution.v1"
REPLAN_SCHEMA_VERSION = "mentorflow.replan-delta.v1"


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
    )


class EffortIntervalPolicy(StrEnum):
    LOW = "low"
    EXPECTED = "expected"
    HIGH = "high"


class TaskSplitPolicy(StrEnum):
    UNSPLITTABLE = "unsplittable"
    SPLITTABLE = "splittable"


class AllocationKind(StrEnum):
    TASK = "task"
    REVIEW = "review"
    FINAL_BUFFER = "final_buffer"


class ScheduleStatus(StrEnum):
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    NOT_SOLVED = "not_solved"


class InfeasibilityCode(StrEnum):
    NO_AVAILABLE_CAPACITY = "no_available_capacity"
    DEADLINE_CAPACITY_SHORTFALL = "deadline_capacity_shortfall"
    UNSPLITTABLE_TASK_EXCEEDS_DAILY_CAPACITY = (
        "unsplittable_task_exceeds_daily_capacity"
    )
    PREREQUISITE_CYCLE = "prerequisite_cycle"
    RESERVATION_WINDOW_CAPACITY_SHORTFALL = (
        "reservation_window_capacity_shortfall"
    )
    SOLVER_DEPENDENCY_UNAVAILABLE = "solver_dependency_unavailable"
    OTHER = "other"


class ConstraintViolationCode(StrEnum):
    PROBLEM_ID_MISMATCH = "problem_id_mismatch"
    PROBLEM_OBVIOUSLY_INFEASIBLE = "problem_obviously_infeasible"
    UNKNOWN_ALLOCATION_TARGET = "unknown_allocation_target"
    DATE_OUTSIDE_WINDOW = "date_outside_window"
    MISSING_DAILY_CAPACITY = "missing_daily_capacity"
    UNAVAILABLE_DAY = "unavailable_day"
    DAILY_CAPACITY_EXCEEDED = "daily_capacity_exceeded"
    TASK_MINUTES_MISMATCH = "task_minutes_mismatch"
    RESERVATION_MINUTES_MISMATCH = "reservation_minutes_mismatch"
    PREREQUISITE_ORDER = "prerequisite_order"
    SPLIT_POLICY = "split_policy"
    COMPLETED_TASK_CHANGED = "completed_task_changed"
    DAILY_LOAD_MISMATCH = "daily_load_mismatch"
    UTILIZATION_MISMATCH = "utilization_mismatch"


class KnowledgeTask(_FrozenContract):
    task_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=500)
    effort: EffortEstimate
    split_policy: TaskSplitPolicy = TaskSplitPolicy.SPLITTABLE
    minimum_chunk_minutes: int = Field(default=10, gt=0, le=240)
    maximum_chunks: int | None = Field(default=None, gt=0, le=64)
    allow_final_short_chunk: bool = True

    @model_validator(mode="after")
    def validate_split_contract(self) -> "KnowledgeTask":
        if self.split_policy is TaskSplitPolicy.UNSPLITTABLE:
            if self.maximum_chunks not in (None, 1):
                raise ValueError("unsplittable tasks may have at most one chunk")
            if self.allow_final_short_chunk:
                raise ValueError(
                    "allow_final_short_chunk applies only to splittable tasks"
                )
        return self

    def minutes_for(self, policy: EffortIntervalPolicy) -> int:
        return {
            EffortIntervalPolicy.LOW: self.effort.low_minutes,
            EffortIntervalPolicy.EXPECTED: self.effort.expected_minutes,
            EffortIntervalPolicy.HIGH: self.effort.high_minutes,
        }[policy]


class PrerequisiteEdge(_FrozenContract):
    prerequisite_task_id: str = Field(min_length=1, max_length=160)
    dependent_task_id: str = Field(min_length=1, max_length=160)


class DailyCapacity(_FrozenContract):
    day: date
    available_minutes: int = Field(ge=0, le=24 * 60)


class ReviewWorkload(_FrozenContract):
    workload_id: str = Field(min_length=1, max_length=160)
    minutes: int = Field(gt=0, le=24 * 60)
    earliest_date: date
    latest_date: date
    splittable: bool = True

    @model_validator(mode="after")
    def validate_window(self) -> "ReviewWorkload":
        if self.latest_date < self.earliest_date:
            raise ValueError("review workload latest_date precedes earliest_date")
        return self


class FinalBuffer(_FrozenContract):
    buffer_id: str = Field(default="final-buffer", min_length=1, max_length=160)
    minutes: int = Field(gt=0, le=24 * 60)
    earliest_date: date
    latest_date: date

    @model_validator(mode="after")
    def validate_window(self) -> "FinalBuffer":
        if self.latest_date < self.earliest_date:
            raise ValueError("final buffer latest_date precedes earliest_date")
        return self


class ScheduleAllocation(_FrozenContract):
    allocation_id: str = Field(min_length=1, max_length=200)
    kind: AllocationKind
    day: date
    minutes: int = Field(gt=0, le=24 * 60)
    order_in_day: int = Field(default=0, ge=0, le=10_000)
    task_id: str | None = Field(default=None, min_length=1, max_length=160)
    reservation_id: str | None = Field(default=None, min_length=1, max_length=160)
    chunk_index: int = Field(default=1, ge=1, le=64)
    chunk_count: int = Field(default=1, ge=1, le=64)
    completed: bool = False

    @model_validator(mode="after")
    def validate_identity(self) -> "ScheduleAllocation":
        if self.chunk_index > self.chunk_count:
            raise ValueError("chunk_index cannot exceed chunk_count")
        if self.kind is AllocationKind.TASK:
            if self.task_id is None or self.reservation_id is not None:
                raise ValueError("task allocations require only task_id")
        else:
            if self.reservation_id is None or self.task_id is not None:
                raise ValueError("reserved allocations require only reservation_id")
            if self.chunk_index != 1 or self.chunk_count != 1:
                raise ValueError("reserved allocations do not use task chunk fields")
            if self.completed:
                raise ValueError("only task allocations may be marked completed")
        return self


class CompletedTask(_FrozenContract):
    task_id: str = Field(min_length=1, max_length=160)
    allocations: tuple[ScheduleAllocation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_immutable_allocations(self) -> "CompletedTask":
        if len({item.allocation_id for item in self.allocations}) != len(
            self.allocations
        ):
            raise ValueError("completed allocation ids must be unique")
        for item in self.allocations:
            if (
                item.kind is not AllocationKind.TASK
                or item.task_id != self.task_id
                or not item.completed
            ):
                raise ValueError(
                    "completed task allocations must be completed task allocations"
                )
        return self


class PlanScheduleProblem(_FrozenContract):
    schema_version: Literal["mentorflow.plan-schedule-problem.v1"] = (
        PROBLEM_SCHEMA_VERSION
    )
    problem_id: str = Field(min_length=1, max_length=160)
    tasks: tuple[KnowledgeTask, ...] = Field(min_length=1)
    prerequisite_edges: tuple[PrerequisiteEdge, ...] = ()
    start_date: date
    deadline: date
    daily_capacities: tuple[DailyCapacity, ...] = Field(min_length=1)
    unavailable_days: frozenset[date] = frozenset()
    review_workloads: tuple[ReviewWorkload, ...] = ()
    final_buffer: FinalBuffer | None = None
    completed_tasks: tuple[CompletedTask, ...] = ()
    effort_interval_policy: EffortIntervalPolicy = EffortIntervalPolicy.EXPECTED
    deterministic: bool = True

    @model_validator(mode="after")
    def validate_problem_references(self) -> "PlanScheduleProblem":
        if self.deadline < self.start_date:
            raise ValueError("deadline precedes start_date")
        task_ids = [item.task_id for item in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task ids must be unique")
        task_id_set = set(task_ids)
        edge_pairs = [
            (item.prerequisite_task_id, item.dependent_task_id)
            for item in self.prerequisite_edges
        ]
        if len(edge_pairs) != len(set(edge_pairs)):
            raise ValueError("prerequisite edges must be unique")
        if any(source not in task_id_set or target not in task_id_set for source, target in edge_pairs):
            raise ValueError("prerequisite edges must reference known tasks")

        capacity_days = [item.day for item in self.daily_capacities]
        if len(capacity_days) != len(set(capacity_days)):
            raise ValueError("daily capacity dates must be unique")
        if any(not self.start_date <= day <= self.deadline for day in capacity_days):
            raise ValueError("daily capacity date lies outside the plan window")
        if any(
            not self.start_date <= day <= self.deadline
            for day in self.unavailable_days
        ):
            raise ValueError("unavailable day lies outside the plan window")

        workload_ids = [item.workload_id for item in self.review_workloads]
        if len(workload_ids) != len(set(workload_ids)):
            raise ValueError("review workload ids must be unique")
        for workload in self.review_workloads:
            if (
                workload.earliest_date < self.start_date
                or workload.latest_date > self.deadline
            ):
                raise ValueError("review workload lies outside the plan window")
        if self.final_buffer is not None and (
            self.final_buffer.earliest_date < self.start_date
            or self.final_buffer.latest_date > self.deadline
        ):
            raise ValueError("final buffer lies outside the plan window")
        if self.final_buffer is not None and self.final_buffer.buffer_id in workload_ids:
            raise ValueError("final buffer id must differ from review workload ids")

        completed_ids = [item.task_id for item in self.completed_tasks]
        if len(completed_ids) != len(set(completed_ids)):
            raise ValueError("completed task ids must be unique")
        if any(item not in task_id_set for item in completed_ids):
            raise ValueError("completed tasks must reference known tasks")
        if any(
            allocation.day > self.deadline
            for completed in self.completed_tasks
            for allocation in completed.allocations
        ):
            raise ValueError("completed allocation cannot occur after the deadline")
        return self

    @property
    def task_by_id(self) -> dict[str, KnowledgeTask]:
        return {item.task_id: item for item in self.tasks}

    @property
    def completed_task_ids(self) -> frozenset[str]:
        return frozenset(item.task_id for item in self.completed_tasks)

    @property
    def capacity_by_day(self) -> dict[date, int]:
        return {
            item.day: 0 if item.day in self.unavailable_days else item.available_minutes
            for item in self.daily_capacities
        }

    def planning_minutes(self, task_id: str) -> int:
        return self.task_by_id[task_id].minutes_for(self.effort_interval_policy)


class ScheduleInfeasibility(_FrozenContract):
    code: InfeasibilityCode
    message: str = Field(min_length=1, max_length=1_000)
    task_ids: tuple[str, ...] = ()
    dates: tuple[date, ...] = ()
    required_minutes: int | None = Field(default=None, ge=0)
    available_minutes: int | None = Field(default=None, ge=0)
    details: tuple[str, ...] = ()


class DailyLoad(_FrozenContract):
    day: date
    task_minutes: int = Field(ge=0)
    review_minutes: int = Field(ge=0)
    buffer_minutes: int = Field(ge=0)
    expected_daily_minutes: int = Field(ge=0)
    capacity_minutes: int = Field(ge=0)
    utilization: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_totals(self) -> "DailyLoad":
        expected = self.task_minutes + self.review_minutes + self.buffer_minutes
        if self.expected_daily_minutes != expected:
            raise ValueError("expected_daily_minutes must equal its components")
        calculated = (
            expected / self.capacity_minutes if self.capacity_minutes > 0 else 0.0
        )
        if abs(self.utilization - calculated) > 1e-6:
            raise ValueError("daily utilization does not match load and capacity")
        return self


class SolverMetadata(_FrozenContract):
    solver_name: str = Field(min_length=1, max_length=160)
    solver_version: str = Field(min_length=1, max_length=120)
    deterministic: bool
    random_seed: int | None = Field(default=None, ge=0)
    wall_time_ms: int = Field(default=0, ge=0)
    dependency_name: str | None = Field(default=None, max_length=120)
    dependency_version: str | None = Field(default=None, max_length=120)
    dependency_available: bool
    configuration: tuple[str, ...] = ()


class PlanScheduleSolution(_FrozenContract):
    schema_version: Literal["mentorflow.plan-schedule-solution.v1"] = (
        SOLUTION_SCHEMA_VERSION
    )
    solution_id: str = Field(min_length=1, max_length=160)
    problem_id: str = Field(min_length=1, max_length=160)
    status: ScheduleStatus
    allocations: tuple[ScheduleAllocation, ...] = ()
    daily_loads: tuple[DailyLoad, ...] = ()
    infeasibilities: tuple[ScheduleInfeasibility, ...] = ()
    utilization: float = Field(ge=0)
    solver_metadata: SolverMetadata

    @model_validator(mode="after")
    def validate_status_shape(self) -> "PlanScheduleSolution":
        if len({item.allocation_id for item in self.allocations}) != len(
            self.allocations
        ):
            raise ValueError("allocation ids must be unique")
        if len({item.day for item in self.daily_loads}) != len(self.daily_loads):
            raise ValueError("daily load dates must be unique")
        if self.status is ScheduleStatus.FEASIBLE:
            if self.infeasibilities:
                raise ValueError("feasible solution cannot contain infeasibilities")
        else:
            if not self.infeasibilities:
                raise ValueError("non-feasible solution requires a reason")
            if self.allocations or self.daily_loads:
                raise ValueError("non-feasible solution cannot claim allocations")
            if self.status is ScheduleStatus.NOT_SOLVED and not any(
                item.code is InfeasibilityCode.SOLVER_DEPENDENCY_UNAVAILABLE
                for item in self.infeasibilities
            ):
                raise ValueError(
                    "not_solved status requires solver_dependency_unavailable"
                )
        return self


class ConstraintViolation(_FrozenContract):
    code: ConstraintViolationCode
    message: str = Field(min_length=1, max_length=1_000)
    task_ids: tuple[str, ...] = ()
    dates: tuple[date, ...] = ()


class SolutionValidation(_FrozenContract):
    valid: bool
    violations: tuple[ConstraintViolation, ...] = ()

    @model_validator(mode="after")
    def validate_result(self) -> "SolutionValidation":
        if self.valid == bool(self.violations):
            raise ValueError("valid must be the inverse of whether violations exist")
        return self


class ScheduleDependencyStatus(_FrozenContract):
    dependency: Literal["ortools"] = "ortools"
    available: bool
    version: str | None = None
    dependency_change_required: str | None = None

    @model_validator(mode="after")
    def validate_change_requirement(self) -> "ScheduleDependencyStatus":
        expected = None if self.available else "ortools"
        if self.dependency_change_required != expected:
            raise ValueError("dependency change requirement contradicts availability")
        return self


__all__ = [
    "PROBLEM_SCHEMA_VERSION",
    "REPLAN_SCHEMA_VERSION",
    "SOLUTION_SCHEMA_VERSION",
    "AllocationKind",
    "CompletedTask",
    "ConstraintViolation",
    "ConstraintViolationCode",
    "DailyCapacity",
    "DailyLoad",
    "EffortIntervalPolicy",
    "FinalBuffer",
    "InfeasibilityCode",
    "KnowledgeTask",
    "PlanScheduleProblem",
    "PlanScheduleSolution",
    "PrerequisiteEdge",
    "ReviewWorkload",
    "ScheduleAllocation",
    "ScheduleDependencyStatus",
    "ScheduleInfeasibility",
    "ScheduleStatus",
    "SolutionValidation",
    "SolverMetadata",
    "TaskSplitPolicy",
]
