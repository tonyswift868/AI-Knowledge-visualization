"""Pure daily status projection over an immutable schedule."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from hashlib import sha256

from ..scheduling.contracts import ScheduleAllocation
from .contracts import (
    ImmutablePlanSchedule,
    LearningPlanSnapshot,
    OverdueEntryStatus,
    OverdueLedger,
    OverdueLedgerEntry,
    PlanRuntimeStatus,
    ScheduledTaskRuntime,
    ScheduledTaskStatus,
    TaskCompletion,
    TaskSkip,
)


class PlanProgressTracker:
    """Compute progress without changing allocation dates or invoking a solver."""

    def snapshot(
        self,
        schedule: ImmutablePlanSchedule,
        *,
        today: date,
        completions: tuple[TaskCompletion, ...] = (),
        skips: tuple[TaskSkip, ...] = (),
    ) -> LearningPlanSnapshot:
        completion_by_id, skip_by_id = self._validate_events(
            schedule,
            today=today,
            completions=completions,
            skips=skips,
        )
        allocations_by_task = self._allocations_by_task(schedule)
        tasks: list[ScheduledTaskRuntime] = []
        for definition in schedule.tasks:
            allocations = allocations_by_task[definition.task_id]
            scheduled_dates = tuple(sorted({item.day for item in allocations}))
            completion = completion_by_id.get(definition.task_id)
            skip = skip_by_id.get(definition.task_id)
            overdue_since: date | None = None
            if completion is not None:
                status = ScheduledTaskStatus.COMPLETED
            elif skip is not None:
                status = ScheduledTaskStatus.SKIPPED_BY_USER
            elif any(day < today for day in scheduled_dates):
                status = ScheduledTaskStatus.OVERDUE
                overdue_since = min(day for day in scheduled_dates if day < today)
            elif today in scheduled_dates:
                status = ScheduledTaskStatus.DUE_TODAY
            else:
                status = ScheduledTaskStatus.UPCOMING
            tasks.append(
                ScheduledTaskRuntime(
                    task_id=definition.task_id,
                    title=definition.title,
                    sequence_index=definition.sequence_index,
                    prerequisite_task_ids=definition.prerequisite_task_ids,
                    allocation_kind=definition.allocation_kind,
                    scheduled_dates=scheduled_dates,
                    planned_minutes=sum(item.minutes for item in allocations),
                    status=status,
                    overdue_since=overdue_since,
                    completed_on=(
                        completion.completed_on if completion is not None else None
                    ),
                    skipped_on=skip.skipped_on if skip is not None else None,
                )
            )
        terminal_statuses = {
            ScheduledTaskStatus.COMPLETED,
            ScheduledTaskStatus.SKIPPED_BY_USER,
        }
        if all(item.status in terminal_statuses for item in tasks):
            plan_status = PlanRuntimeStatus.COMPLETED
        elif today > schedule.deadline:
            plan_status = PlanRuntimeStatus.EXPIRED_WITH_OVERDUE
        else:
            plan_status = PlanRuntimeStatus.ACTIVE
        return LearningPlanSnapshot(
            snapshot_id=self._snapshot_id(
                schedule,
                today=today,
                completions=completions,
                skips=skips,
            ),
            plan_id=schedule.plan_id,
            schedule_id=schedule.schedule_id,
            schedule_version=schedule.version,
            as_of_date=today,
            deadline=schedule.deadline,
            status=plan_status,
            tasks=tuple(tasks),
        )

    def overdue_ledger(
        self,
        schedule: ImmutablePlanSchedule,
        *,
        today: date,
        completions: tuple[TaskCompletion, ...] = (),
        skips: tuple[TaskSkip, ...] = (),
    ) -> OverdueLedger:
        snapshot = self.snapshot(
            schedule,
            today=today,
            completions=completions,
            skips=skips,
        )
        completion_by_id = {item.task_id: item for item in completions}
        active: list[OverdueLedgerEntry] = []
        resolved: list[OverdueLedgerEntry] = []
        for task in snapshot.tasks:
            original_due_date = min(task.scheduled_dates)
            if task.status is ScheduledTaskStatus.OVERDUE:
                active.append(
                    OverdueLedgerEntry(
                        task_id=task.task_id,
                        title=task.title,
                        original_due_date=original_due_date,
                        planned_minutes=task.planned_minutes,
                        allocation_kind=task.allocation_kind,
                        status=OverdueEntryStatus.ACTIVE,
                        days_overdue=(today - original_due_date).days,
                    )
                )
                continue
            completion = completion_by_id.get(task.task_id)
            if (
                completion is not None
                and completion.completed_on > original_due_date
            ):
                resolved.append(
                    OverdueLedgerEntry(
                        task_id=task.task_id,
                        title=task.title,
                        original_due_date=original_due_date,
                        planned_minutes=task.planned_minutes,
                        allocation_kind=task.allocation_kind,
                        status=OverdueEntryStatus.RESOLVED,
                        days_overdue=(
                            completion.completed_on - original_due_date
                        ).days,
                        resolved_on=completion.completed_on,
                    )
                )
        return OverdueLedger(
            plan_id=schedule.plan_id,
            as_of_date=today,
            active_entries=tuple(
                sorted(active, key=lambda item: (item.original_due_date, item.task_id))
            ),
            resolved_entries=tuple(
                sorted(
                    resolved,
                    key=lambda item: (item.original_due_date, item.task_id),
                )
            ),
        )

    @staticmethod
    def _allocations_by_task(
        schedule: ImmutablePlanSchedule,
    ) -> dict[str, tuple[ScheduleAllocation, ...]]:
        values: dict[str, list[ScheduleAllocation]] = defaultdict(list)
        for definition in schedule.tasks:
            values[definition.task_id].extend(
                schedule.allocations_for(definition.task_id)
            )
        missing = [item.task_id for item in schedule.tasks if not values[item.task_id]]
        if missing:
            raise ValueError(
                "runtime schedule has tasks without allocations: " + ", ".join(missing)
            )
        return {
            task_id: tuple(
                sorted(
                    allocations,
                    key=lambda item: (
                        item.day,
                        item.order_in_day,
                        item.allocation_id,
                    ),
                )
            )
            for task_id, allocations in values.items()
        }

    @staticmethod
    def _validate_events(
        schedule: ImmutablePlanSchedule,
        *,
        today: date,
        completions: tuple[TaskCompletion, ...],
        skips: tuple[TaskSkip, ...],
    ) -> tuple[dict[str, TaskCompletion], dict[str, TaskSkip]]:
        completion_by_id = {item.task_id: item for item in completions}
        skip_by_id = {item.task_id: item for item in skips}
        if len(completion_by_id) != len(completions):
            raise ValueError("completion task ids must be unique")
        if len(skip_by_id) != len(skips):
            raise ValueError("skip task ids must be unique")
        task_ids = set(schedule.task_by_id)
        unknown = (set(completion_by_id) | set(skip_by_id)) - task_ids
        if unknown:
            raise ValueError("progress event references unknown task")
        if set(completion_by_id) & set(skip_by_id):
            raise ValueError("task cannot be both completed and skipped")
        if any(item.completed_on > today for item in completions):
            raise ValueError("future completion cannot appear in current snapshot")
        if any(item.skipped_on > today for item in skips):
            raise ValueError("future skip cannot appear in current snapshot")
        return completion_by_id, skip_by_id

    @staticmethod
    def _snapshot_id(
        schedule: ImmutablePlanSchedule,
        *,
        today: date,
        completions: tuple[TaskCompletion, ...],
        skips: tuple[TaskSkip, ...],
    ) -> str:
        completion_value = ",".join(
            f"{item.task_id}:{item.completed_on.isoformat()}"
            for item in sorted(completions, key=lambda item: item.task_id)
        )
        skip_value = ",".join(
            f"{item.task_id}:{item.skipped_on.isoformat()}"
            for item in sorted(skips, key=lambda item: item.task_id)
        )
        payload = "|".join(
            (
                schedule.schedule_id,
                today.isoformat(),
                completion_value,
                skip_value,
            )
        )
        return "snapshot-" + sha256(payload.encode("utf-8")).hexdigest()[:20]


__all__ = ["PlanProgressTracker"]
