"""Deterministic preview/confirm/apply service for explicit plan shifts."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from hashlib import sha256

from ..scheduling.contracts import AllocationKind, ScheduleAllocation
from .contracts import (
    AllocationMove,
    ImmutablePlanSchedule,
    PlanShiftApplication,
    PlanShiftConfirmation,
    PlanShiftPreview,
    PlanShiftRequest,
    ShiftPreviewStatus,
    WeeklyLearningPreferences,
)


class PlanShiftError(ValueError):
    pass


class ShiftNotConfirmedError(PlanShiftError):
    pass


class StaleScheduleError(PlanShiftError):
    pass


class NoValidFutureLearningDay(PlanShiftError):
    pass


class PlanShiftService:
    """Shift dates only after an explicit, idempotent confirmation."""

    def __init__(self) -> None:
        self._previews: dict[str, PlanShiftPreview] = {}
        self._preview_by_request: dict[tuple[str, str], str] = {}
        self._confirmations: dict[str, PlanShiftConfirmation] = {}
        self._applications: dict[str, PlanShiftApplication] = {}

    def create_preview(
        self,
        schedule: ImmutablePlanSchedule,
        request: PlanShiftRequest,
    ) -> PlanShiftPreview:
        if request.plan_id != schedule.plan_id:
            raise PlanShiftError("shift request plan_id does not match schedule")
        if request.expected_schedule_version != schedule.version:
            raise StaleScheduleError("shift request targets a stale schedule version")
        task_ids = set(schedule.task_by_id)
        if not request.completed_task_ids.issubset(task_ids):
            raise PlanShiftError("completed task set contains an unknown task")

        request_key = (request.plan_id, request.request_id)
        previous_preview_id = self._preview_by_request.get(request_key)
        request_fingerprint = self._request_fingerprint(schedule, request)
        preview_id = f"shift-preview-{request_fingerprint[:20]}"
        if previous_preview_id is not None:
            if previous_preview_id != preview_id:
                raise PlanShiftError("request_id was reused with different shift input")
            return self._previews[previous_preview_id]

        completion_ids = set(request.completed_task_ids)
        completion_ids.update(
            item.task_id
            for item in schedule.task_allocations
            if item.completed and item.task_id is not None
        )
        initially_movable = [
            item
            for item in schedule.allocations
            if not self._is_completed_allocation(item, completion_ids)
        ]
        if not initially_movable:
            raise PlanShiftError("schedule has no remaining allocations to shift")
        shift_from = request.shift_from_date or min(
            item.day for item in initially_movable
        )
        movable = [
            item for item in initially_movable if item.day >= shift_from
        ]
        if not movable:
            raise PlanShiftError("shift_from_date leaves no allocations to shift")

        source_days = tuple(sorted({item.day for item in movable}))
        old_start = source_days[0]
        old_end = source_days[-1]
        if request.calendar_days_delta is not None:
            calendar_delta = request.calendar_days_delta
        else:
            assert request.new_start_date is not None
            calendar_delta = (request.new_start_date - old_start).days
            if calendar_delta <= 0:
                raise PlanShiftError("new_start_date must move the plan forward")

        mapped_days: dict[date, date] = {}
        previous_target: date | None = None
        skipped_days = 0
        for source_day in source_days:
            candidate = source_day + timedelta(days=calendar_delta)
            if previous_target is not None and candidate <= previous_target:
                candidate = previous_target + timedelta(days=1)
            target, skipped = self._next_learning_day(
                candidate,
                request.weekly_preferences,
            )
            mapped_days[source_day] = target
            previous_target = target
            skipped_days += skipped

        movable_ids = {item.allocation_id for item in movable}
        shifted_values = [
            item.model_copy(update={"day": mapped_days[item.day]})
            if item.allocation_id in movable_ids
            else item
            for item in schedule.allocations
        ]
        shifted_allocations = self._resolve_order_collisions(
            original=schedule.allocations,
            shifted=tuple(shifted_values),
            moved_ids=movable_ids,
        )
        self._validate_preservation(
            schedule,
            shifted_allocations,
            completed_task_ids=completion_ids,
        )
        new_start = mapped_days[old_start]
        new_end = mapped_days[old_end]
        calendar_days_added = (new_end - old_end).days
        learning_days_added = self._count_learning_days(
            old_end,
            new_end,
            request.weekly_preferences,
        )
        target_schedule = ImmutablePlanSchedule(
            schedule_id=f"shifted-{request_fingerprint[:24]}",
            plan_id=schedule.plan_id,
            version=schedule.version + 1,
            source_problem_id=schedule.source_problem_id,
            source_solution_id=schedule.source_solution_id,
            start_date=min(item.day for item in shifted_allocations),
            end_date=max(item.day for item in shifted_allocations),
            deadline=schedule.deadline + timedelta(days=calendar_days_added),
            tasks=schedule.tasks,
            allocations=shifted_allocations,
        )
        moves = tuple(
            AllocationMove(
                allocation_id=item.allocation_id,
                task_id=item.task_id,
                reservation_id=item.reservation_id,
                old_date=item.day,
                new_date=mapped_days[item.day],
                minutes=item.minutes,
            )
            for item in sorted(
                movable,
                key=lambda value: (
                    value.day,
                    value.order_in_day,
                    value.allocation_id,
                ),
            )
        )
        preview = PlanShiftPreview(
            preview_id=preview_id,
            request=request,
            source_schedule_id=schedule.schedule_id,
            source_schedule_version=schedule.version,
            old_start=old_start,
            new_start=new_start,
            old_end=old_end,
            new_end=new_end,
            calendar_days_added=calendar_days_added,
            learning_days_added=learning_days_added,
            tasks_moved=len(
                {
                    item.task_id
                    for item in moves
                    if item.task_id is not None
                }
            ),
            allocations_moved=len(moves),
            non_learning_days_skipped=skipped_days,
            moves=moves,
            proposed_schedule=target_schedule,
        )
        self._previews[preview_id] = preview
        self._preview_by_request[request_key] = preview_id
        return preview

    def confirm(
        self,
        preview_id: str,
        *,
        confirmation_id: str,
    ) -> PlanShiftConfirmation:
        if preview_id not in self._previews:
            raise PlanShiftError("unknown shift preview")
        existing = self._confirmations.get(preview_id)
        if existing is not None:
            return existing
        confirmation = PlanShiftConfirmation(
            preview_id=preview_id,
            confirmation_id=confirmation_id,
        )
        self._confirmations[preview_id] = confirmation
        return confirmation

    def apply(self, preview_id: str) -> PlanShiftApplication:
        preview = self._previews.get(preview_id)
        if preview is None:
            raise PlanShiftError("unknown shift preview")
        confirmation = self._confirmations.get(preview_id)
        if confirmation is None:
            raise ShiftNotConfirmedError(
                "shift preview must be explicitly confirmed before apply"
            )
        existing = self._applications.get(preview_id)
        if existing is not None:
            return existing
        application = PlanShiftApplication(
            application_id=f"shift-application-{preview_id.removeprefix('shift-preview-')}",
            preview_id=preview_id,
            confirmation_id=confirmation.confirmation_id,
            source_schedule_id=preview.source_schedule_id,
            applied_schedule=preview.proposed_schedule,
        )
        self._applications[preview_id] = application
        return application

    def get_preview(self, preview_id: str) -> PlanShiftPreview:
        preview = self._previews.get(preview_id)
        if preview is None:
            raise PlanShiftError("unknown shift preview")
        return preview

    def preview_status(self, preview_id: str) -> ShiftPreviewStatus:
        if preview_id in self._applications:
            return ShiftPreviewStatus.APPLIED
        if preview_id in self._confirmations:
            return ShiftPreviewStatus.CONFIRMED
        if preview_id in self._previews:
            return ShiftPreviewStatus.PENDING_CONFIRMATION
        raise PlanShiftError("unknown shift preview")

    @staticmethod
    def _is_completed_allocation(
        allocation: ScheduleAllocation,
        completed_task_ids: set[str],
    ) -> bool:
        return (
            allocation.kind is AllocationKind.TASK
            and allocation.task_id in completed_task_ids
        ) or allocation.completed

    @staticmethod
    def _next_learning_day(
        candidate: date,
        preferences: WeeklyLearningPreferences,
    ) -> tuple[date, int]:
        if not preferences.allowed_weekdays:
            raise NoValidFutureLearningDay("no allowed weekday is configured")
        current = candidate
        for skipped in range(36_501):
            if preferences.allows(current):
                return current, skipped
            current += timedelta(days=1)
        raise NoValidFutureLearningDay(
            "no valid learning day found within 100 years"
        )

    @staticmethod
    def _count_learning_days(
        old_end: date,
        new_end: date,
        preferences: WeeklyLearningPreferences,
    ) -> int:
        if new_end <= old_end:
            return 0
        current = old_end + timedelta(days=1)
        result = 0
        while current <= new_end:
            if preferences.allows(current):
                result += 1
            current += timedelta(days=1)
        return result

    @staticmethod
    def _resolve_order_collisions(
        *,
        original: tuple[ScheduleAllocation, ...],
        shifted: tuple[ScheduleAllocation, ...],
        moved_ids: set[str],
    ) -> tuple[ScheduleAllocation, ...]:
        original_by_id = {item.allocation_id: item for item in original}
        by_day: dict[date, list[ScheduleAllocation]] = defaultdict(list)
        for item in shifted:
            by_day[item.day].append(item)
        resolved: list[ScheduleAllocation] = []
        for day, values in sorted(by_day.items()):
            fixed = [item for item in values if item.allocation_id not in moved_ids]
            moved = [item for item in values if item.allocation_id in moved_ids]
            used_orders = {item.order_in_day for item in fixed}
            resolved.extend(fixed)
            previous_order = -1
            for item in sorted(
                moved,
                key=lambda value: (
                    original_by_id[value.allocation_id].day,
                    original_by_id[value.allocation_id].order_in_day,
                    value.allocation_id,
                ),
            ):
                candidate = max(item.order_in_day, previous_order + 1)
                while candidate in used_orders and candidate <= 10_000:
                    candidate += 1
                if candidate > 10_000:
                    raise PlanShiftError("shifted day exceeds order capacity")
                resolved.append(item.model_copy(update={"order_in_day": candidate}))
                used_orders.add(candidate)
                previous_order = candidate
        return tuple(
            sorted(
                resolved,
                key=lambda item: (
                    item.day,
                    item.order_in_day,
                    item.kind.value,
                    item.task_id or item.reservation_id or "",
                    item.allocation_id,
                ),
            )
        )

    @staticmethod
    def _validate_preservation(
        source: ImmutablePlanSchedule,
        shifted: tuple[ScheduleAllocation, ...],
        *,
        completed_task_ids: set[str],
    ) -> None:
        old_by_id = {item.allocation_id: item for item in source.allocations}
        new_by_id = {item.allocation_id: item for item in shifted}
        if set(old_by_id) != set(new_by_id):
            raise PlanShiftError("shift must preserve allocation identity")
        for allocation_id, old in old_by_id.items():
            new = new_by_id[allocation_id]
            if (
                old.kind != new.kind
                or old.task_id != new.task_id
                or old.reservation_id != new.reservation_id
                or old.minutes != new.minutes
                or old.chunk_index != new.chunk_index
                or old.chunk_count != new.chunk_count
            ):
                raise PlanShiftError("shift changed planned workload or identity")
            if (
                old.task_id in completed_task_ids or old.completed
            ) and old != new:
                raise PlanShiftError("shift changed completed history")

        allocations_by_task: dict[str, list[ScheduleAllocation]] = defaultdict(list)
        for item in shifted:
            if item.task_id is not None:
                allocations_by_task[item.task_id].append(item)
        for task in source.tasks:
            dependent = allocations_by_task[task.task_id]
            dependent_start = min(
                (item.day.toordinal(), item.order_in_day) for item in dependent
            )
            for prerequisite_id in task.prerequisite_task_ids:
                prerequisite = allocations_by_task[prerequisite_id]
                prerequisite_end = max(
                    (item.day.toordinal(), item.order_in_day)
                    for item in prerequisite
                )
                if prerequisite_end >= dependent_start:
                    raise PlanShiftError("shift violated prerequisite ordering")

    @staticmethod
    def _request_fingerprint(
        schedule: ImmutablePlanSchedule,
        request: PlanShiftRequest,
    ) -> str:
        request_payload = request.model_dump(mode="json")
        request_payload["completed_task_ids"] = sorted(
            request_payload["completed_task_ids"]
        )
        preferences = request_payload["weekly_preferences"]
        preferences["allowed_weekdays"] = sorted(
            preferences["allowed_weekdays"]
        )
        preferences["unavailable_dates"] = sorted(
            preferences["unavailable_dates"]
        )
        payload = schedule.schedule_id + "|" + json.dumps(
            request_payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "NoValidFutureLearningDay",
    "PlanShiftError",
    "PlanShiftService",
    "ShiftNotConfirmedError",
    "StaleScheduleError",
]
