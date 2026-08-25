"""Immutable contracts for plan runtime state and explicit calendar shifts."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..scheduling.contracts import AllocationKind, ScheduleAllocation


RUNTIME_SCHEMA_VERSION = "mentorflow.plan-runtime.v2"
SHIFT_SCHEMA_VERSION = "mentorflow.plan-shift.v1"


class _FrozenRuntimeContract(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
    )


class ScheduledTaskStatus(StrEnum):
    UPCOMING = "upcoming"
    DUE_TODAY = "due_today"
    OVERDUE = "overdue"
    COMPLETED = "completed"
    SKIPPED_BY_USER = "skipped_by_user"


class PlanRuntimeStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED_WITH_OVERDUE = "expired_with_overdue"


class Weekday(StrEnum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"

    @property
    def number(self) -> int:
        return {
            Weekday.MONDAY: 0,
            Weekday.TUESDAY: 1,
            Weekday.WEDNESDAY: 2,
            Weekday.THURSDAY: 3,
            Weekday.FRIDAY: 4,
            Weekday.SATURDAY: 5,
            Weekday.SUNDAY: 6,
        }[self]


class OverdueEntryStatus(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"


class ShiftPreviewStatus(StrEnum):
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"
    APPLIED = "applied"


class RuntimeTaskDefinition(_FrozenRuntimeContract):
    task_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=500)
    sequence_index: int = Field(ge=0)
    prerequisite_task_ids: tuple[str, ...] = ()
    allocation_kind: AllocationKind = AllocationKind.TASK

    @model_validator(mode="after")
    def validate_trackable_kind(self) -> "RuntimeTaskDefinition":
        if self.allocation_kind not in {AllocationKind.TASK, AllocationKind.REVIEW}:
            raise ValueError("runtime tasks may track learning or review allocations")
        return self


class ImmutablePlanSchedule(_FrozenRuntimeContract):
    schema_version: Literal["mentorflow.plan-runtime.v2"] = RUNTIME_SCHEMA_VERSION
    schedule_id: str = Field(min_length=1, max_length=200)
    plan_id: str = Field(min_length=1, max_length=160)
    version: int = Field(ge=1)
    source_problem_id: str = Field(min_length=1, max_length=160)
    source_solution_id: str = Field(min_length=1, max_length=160)
    start_date: date
    end_date: date
    deadline: date
    tasks: tuple[RuntimeTaskDefinition, ...] = Field(min_length=1)
    allocations: tuple[ScheduleAllocation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_schedule(self) -> "ImmutablePlanSchedule":
        if self.end_date < self.start_date:
            raise ValueError("schedule end_date precedes start_date")
        if self.deadline < self.end_date:
            raise ValueError("runtime deadline precedes scheduled work")
        task_ids = [item.task_id for item in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("runtime task ids must be unique")
        if [item.sequence_index for item in self.tasks] != list(
            range(len(self.tasks))
        ):
            raise ValueError("runtime task sequence indices must be contiguous")
        task_id_set = set(task_ids)
        if any(
            prerequisite not in task_id_set
            for item in self.tasks
            for prerequisite in item.prerequisite_task_ids
        ):
            raise ValueError("runtime prerequisite references an unknown task")
        allocation_ids = [item.allocation_id for item in self.allocations]
        if len(allocation_ids) != len(set(allocation_ids)):
            raise ValueError("runtime allocation ids must be unique")
        if any(
            item.task_id not in task_id_set
            for item in self.allocations
            if item.task_id is not None
        ):
            raise ValueError("runtime allocation references an unknown task")
        allocated_task_ids = {
            item.task_id
            if item.kind is AllocationKind.TASK
            else item.reservation_id
            for item in self.allocations
            if item.kind in {AllocationKind.TASK, AllocationKind.REVIEW}
        }
        missing_task_ids = task_id_set - allocated_task_ids
        if missing_task_ids:
            raise ValueError("runtime task is missing schedule allocations")
        for definition in self.tasks:
            if not any(
                (
                    definition.allocation_kind is AllocationKind.TASK
                    and item.kind is AllocationKind.TASK
                    and item.task_id == definition.task_id
                )
                or (
                    definition.allocation_kind is AllocationKind.REVIEW
                    and item.kind is AllocationKind.REVIEW
                    and item.reservation_id == definition.task_id
                )
                for item in self.allocations
            ):
                raise ValueError("runtime task allocation kind does not match")
        allocation_start = min(item.day for item in self.allocations)
        allocation_end = max(item.day for item in self.allocations)
        if self.start_date != allocation_start or self.end_date != allocation_end:
            raise ValueError("runtime start/end must match allocation bounds")
        return self

    @property
    def task_by_id(self) -> dict[str, RuntimeTaskDefinition]:
        return {item.task_id: item for item in self.tasks}

    @property
    def task_allocations(self) -> tuple[ScheduleAllocation, ...]:
        tracked_allocation_ids = {
            item.allocation_id
            for definition in self.tasks
            for item in self.allocations_for(definition.task_id)
        }
        return tuple(
            item
            for item in self.allocations
            if item.allocation_id in tracked_allocation_ids
        )

    def allocations_for(self, task_id: str) -> tuple[ScheduleAllocation, ...]:
        definition = self.task_by_id[task_id]
        if definition.allocation_kind is AllocationKind.TASK:
            return tuple(item for item in self.allocations if item.task_id == task_id)
        return tuple(
            item
            for item in self.allocations
            if item.kind is AllocationKind.REVIEW and item.reservation_id == task_id
        )


class TaskCompletion(_FrozenRuntimeContract):
    task_id: str = Field(min_length=1, max_length=160)
    completed_on: date


class TaskSkip(_FrozenRuntimeContract):
    task_id: str = Field(min_length=1, max_length=160)
    skipped_on: date


class ScheduledTaskRuntime(_FrozenRuntimeContract):
    task_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=500)
    sequence_index: int = Field(ge=0)
    prerequisite_task_ids: tuple[str, ...] = ()
    allocation_kind: AllocationKind = AllocationKind.TASK
    scheduled_dates: tuple[date, ...] = Field(min_length=1)
    planned_minutes: int = Field(gt=0)
    status: ScheduledTaskStatus
    overdue_since: date | None = None
    completed_on: date | None = None
    skipped_on: date | None = None

    @model_validator(mode="after")
    def validate_runtime_state(self) -> "ScheduledTaskRuntime":
        if tuple(sorted(set(self.scheduled_dates))) != self.scheduled_dates:
            raise ValueError("scheduled_dates must be sorted and unique")
        if self.status is ScheduledTaskStatus.COMPLETED:
            if self.completed_on is None or self.skipped_on is not None:
                raise ValueError("completed task requires only completed_on")
        elif self.status is ScheduledTaskStatus.SKIPPED_BY_USER:
            if self.skipped_on is None or self.completed_on is not None:
                raise ValueError("skipped task requires only skipped_on")
        elif self.completed_on is not None or self.skipped_on is not None:
            raise ValueError("active task cannot contain a terminal event")
        if self.status is ScheduledTaskStatus.OVERDUE:
            if self.overdue_since is None:
                raise ValueError("overdue task requires overdue_since")
        elif self.overdue_since is not None:
            raise ValueError("only overdue task may expose overdue_since")
        return self


class LearningPlanSnapshot(_FrozenRuntimeContract):
    schema_version: Literal["mentorflow.plan-runtime.v2"] = RUNTIME_SCHEMA_VERSION
    snapshot_id: str = Field(min_length=1, max_length=200)
    plan_id: str = Field(min_length=1, max_length=160)
    schedule_id: str = Field(min_length=1, max_length=200)
    schedule_version: int = Field(ge=1)
    as_of_date: date
    deadline: date
    status: PlanRuntimeStatus
    tasks: tuple[ScheduledTaskRuntime, ...] = Field(min_length=1)

    @property
    def due_today(self) -> tuple[ScheduledTaskRuntime, ...]:
        return tuple(
            item for item in self.tasks if item.status is ScheduledTaskStatus.DUE_TODAY
        )

    @property
    def overdue(self) -> tuple[ScheduledTaskRuntime, ...]:
        return tuple(
            item for item in self.tasks if item.status is ScheduledTaskStatus.OVERDUE
        )

    @property
    def upcoming(self) -> tuple[ScheduledTaskRuntime, ...]:
        return tuple(
            item for item in self.tasks if item.status is ScheduledTaskStatus.UPCOMING
        )


class OverdueLedgerEntry(_FrozenRuntimeContract):
    task_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=500)
    original_due_date: date
    planned_minutes: int = Field(gt=0)
    allocation_kind: AllocationKind = AllocationKind.TASK
    status: OverdueEntryStatus
    days_overdue: int = Field(ge=1)
    resolved_on: date | None = None

    @model_validator(mode="after")
    def validate_resolution(self) -> "OverdueLedgerEntry":
        if self.status is OverdueEntryStatus.RESOLVED:
            if self.resolved_on is None:
                raise ValueError("resolved overdue entry requires resolved_on")
        elif self.resolved_on is not None:
            raise ValueError("active overdue entry cannot have resolved_on")
        return self


class OverdueLedger(_FrozenRuntimeContract):
    plan_id: str = Field(min_length=1, max_length=160)
    as_of_date: date
    active_entries: tuple[OverdueLedgerEntry, ...] = ()
    resolved_entries: tuple[OverdueLedgerEntry, ...] = ()

    @property
    def active_task_ids(self) -> tuple[str, ...]:
        return tuple(item.task_id for item in self.active_entries)

    @property
    def active_minutes(self) -> int:
        return sum(item.planned_minutes for item in self.active_entries)


class WeeklyLearningPreferences(_FrozenRuntimeContract):
    allowed_weekdays: frozenset[Weekday] = frozenset(
        (
            Weekday.MONDAY,
            Weekday.TUESDAY,
            Weekday.WEDNESDAY,
            Weekday.THURSDAY,
            Weekday.FRIDAY,
        )
    )
    unavailable_dates: frozenset[date] = frozenset()

    def allows(self, day: date) -> bool:
        return (
            any(item.number == day.weekday() for item in self.allowed_weekdays)
            and day not in self.unavailable_dates
        )


class PlanShiftRequest(_FrozenRuntimeContract):
    schema_version: Literal["mentorflow.plan-shift.v1"] = SHIFT_SCHEMA_VERSION
    request_id: str = Field(min_length=1, max_length=160)
    plan_id: str = Field(min_length=1, max_length=160)
    expected_schedule_version: int = Field(ge=1)
    shift_from_date: date | None = None
    new_start_date: date | None = None
    calendar_days_delta: int | None = Field(default=None, gt=0, le=36_500)
    weekly_preferences: WeeklyLearningPreferences
    completed_task_ids: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def validate_shift_mode(self) -> "PlanShiftRequest":
        if (self.new_start_date is None) == (self.calendar_days_delta is None):
            raise ValueError(
                "exactly one of new_start_date or calendar_days_delta is required"
            )
        return self

    @property
    def allowed_weekdays(self) -> frozenset[Weekday]:
        return self.weekly_preferences.allowed_weekdays

    @property
    def unavailable_dates(self) -> frozenset[date]:
        return self.weekly_preferences.unavailable_dates


class AllocationMove(_FrozenRuntimeContract):
    allocation_id: str = Field(min_length=1, max_length=200)
    task_id: str | None = Field(default=None, min_length=1, max_length=160)
    reservation_id: str | None = Field(default=None, min_length=1, max_length=160)
    old_date: date
    new_date: date
    minutes: int = Field(gt=0)


class PlanShiftPreview(_FrozenRuntimeContract):
    schema_version: Literal["mentorflow.plan-shift.v1"] = SHIFT_SCHEMA_VERSION
    preview_id: str = Field(min_length=1, max_length=200)
    request: PlanShiftRequest
    source_schedule_id: str = Field(min_length=1, max_length=200)
    source_schedule_version: int = Field(ge=1)
    status: ShiftPreviewStatus = ShiftPreviewStatus.PENDING_CONFIRMATION
    old_start: date
    new_start: date
    old_end: date
    new_end: date
    calendar_days_added: int = Field(ge=0)
    learning_days_added: int = Field(ge=0)
    tasks_moved: int = Field(ge=0)
    allocations_moved: int = Field(ge=0)
    non_learning_days_skipped: int = Field(ge=0)
    moves: tuple[AllocationMove, ...]
    proposed_schedule: ImmutablePlanSchedule


class PlanShiftConfirmation(_FrozenRuntimeContract):
    preview_id: str = Field(min_length=1, max_length=200)
    confirmation_id: str = Field(min_length=1, max_length=200)
    status: Literal["confirmed"] = "confirmed"


class PlanShiftApplication(_FrozenRuntimeContract):
    application_id: str = Field(min_length=1, max_length=200)
    preview_id: str = Field(min_length=1, max_length=200)
    confirmation_id: str = Field(min_length=1, max_length=200)
    source_schedule_id: str = Field(min_length=1, max_length=200)
    applied_schedule: ImmutablePlanSchedule
    status: Literal["applied"] = "applied"


__all__ = [
    "AllocationMove",
    "ImmutablePlanSchedule",
    "LearningPlanSnapshot",
    "OverdueEntryStatus",
    "OverdueLedger",
    "OverdueLedgerEntry",
    "PlanRuntimeStatus",
    "PlanShiftApplication",
    "PlanShiftConfirmation",
    "PlanShiftPreview",
    "PlanShiftRequest",
    "RUNTIME_SCHEMA_VERSION",
    "RuntimeTaskDefinition",
    "SHIFT_SCHEMA_VERSION",
    "ScheduledTaskRuntime",
    "ScheduledTaskStatus",
    "ShiftPreviewStatus",
    "TaskCompletion",
    "TaskSkip",
    "Weekday",
    "WeeklyLearningPreferences",
]
