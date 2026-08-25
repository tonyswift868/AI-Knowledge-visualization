"""Stateful facade over immutable schedules, progress events, and plan shifts."""

from __future__ import annotations

from datetime import date

from ..scheduling.contracts import (
    AllocationKind,
    PlanScheduleProblem,
    PlanScheduleSolution,
    ScheduleStatus,
)
from ..scheduling.validator import ScheduleValidator
from .contracts import (
    ImmutablePlanSchedule,
    LearningPlanSnapshot,
    OverdueLedger,
    PlanShiftApplication,
    PlanShiftConfirmation,
    PlanShiftPreview,
    PlanShiftRequest,
    RuntimeTaskDefinition,
    ShiftPreviewStatus,
    TaskCompletion,
    TaskSkip,
)
from .progress import PlanProgressTracker
from .shift import PlanShiftError, PlanShiftService, StaleScheduleError


def build_immutable_plan_schedule(
    problem: PlanScheduleProblem,
    solution: PlanScheduleSolution,
    *,
    plan_id: str,
) -> ImmutablePlanSchedule:
    """Capture a feasible solver result as runtime version 1."""

    if solution.status is not ScheduleStatus.FEASIBLE:
        raise ValueError("plan runtime requires a feasible initial schedule")
    if solution.problem_id != problem.problem_id:
        raise ValueError("schedule solution does not match problem")
    ScheduleValidator().require_valid(problem, solution)
    task_ids_with_allocations = {
        item.task_id
        for item in solution.allocations
        if item.kind is AllocationKind.TASK and item.task_id is not None
    }
    missing = [
        item.task_id for item in problem.tasks if item.task_id not in task_ids_with_allocations
    ]
    if missing:
        raise ValueError(
            "initial schedule has tasks without allocations: " + ", ".join(missing)
        )
    prerequisites: dict[str, list[str]] = {
        item.task_id: [] for item in problem.tasks
    }
    for edge in problem.prerequisite_edges:
        prerequisites[edge.dependent_task_id].append(edge.prerequisite_task_id)
    tasks = tuple(
        RuntimeTaskDefinition(
            task_id=item.task_id,
            title=item.title,
            sequence_index=index,
            prerequisite_task_ids=tuple(prerequisites[item.task_id]),
        )
        for index, item in enumerate(problem.tasks)
    )
    allocations = tuple(
        sorted(
            solution.allocations,
            key=lambda item: (
                item.day,
                item.order_in_day,
                item.kind.value,
                item.task_id or item.reservation_id or "",
                item.allocation_id,
            ),
        )
    )
    return ImmutablePlanSchedule(
        schedule_id=f"runtime-{solution.solution_id}",
        plan_id=plan_id,
        version=1,
        source_problem_id=problem.problem_id,
        source_solution_id=solution.solution_id,
        start_date=min(item.day for item in allocations),
        end_date=max(item.day for item in allocations),
        deadline=problem.deadline,
        tasks=tasks,
        allocations=allocations,
    )


class PlanRuntime:
    """Track progress without mutating dates unless a shift is confirmed."""

    def __init__(
        self,
        schedule: ImmutablePlanSchedule,
        *,
        progress_tracker: PlanProgressTracker | None = None,
        shift_service: PlanShiftService | None = None,
    ) -> None:
        self._initial_schedule = schedule
        self._current_schedule = schedule
        self._tracker = progress_tracker or PlanProgressTracker()
        self._shift_service = shift_service or PlanShiftService()
        self._completions: dict[str, TaskCompletion] = {}
        self._skips: dict[str, TaskSkip] = {}
        for allocation in schedule.task_allocations:
            if allocation.completed and allocation.task_id is not None:
                existing = self._completions.get(allocation.task_id)
                completed_on = allocation.day
                if existing is None or completed_on > existing.completed_on:
                    self._completions[allocation.task_id] = TaskCompletion(
                        task_id=allocation.task_id,
                        completed_on=completed_on,
                    )

    @classmethod
    def from_solver_result(
        cls,
        problem: PlanScheduleProblem,
        solution: PlanScheduleSolution,
        *,
        plan_id: str,
    ) -> "PlanRuntime":
        return cls(
            build_immutable_plan_schedule(
                problem,
                solution,
                plan_id=plan_id,
            )
        )

    @property
    def initial_schedule(self) -> ImmutablePlanSchedule:
        return self._initial_schedule

    @property
    def current_schedule(self) -> ImmutablePlanSchedule:
        return self._current_schedule

    @property
    def completions(self) -> tuple[TaskCompletion, ...]:
        return tuple(
            sorted(self._completions.values(), key=lambda item: item.task_id)
        )

    @property
    def skips(self) -> tuple[TaskSkip, ...]:
        return tuple(sorted(self._skips.values(), key=lambda item: item.task_id))

    def record_completion(self, task_id: str, *, completed_on: date) -> TaskCompletion:
        self._require_task(task_id)
        if task_id in self._skips:
            raise ValueError("skipped task cannot also be completed")
        existing = self._completions.get(task_id)
        if existing is not None:
            if existing.completed_on != completed_on:
                raise ValueError("completion event is immutable once recorded")
            return existing
        event = TaskCompletion(task_id=task_id, completed_on=completed_on)
        self._completions[task_id] = event
        return event

    def record_skip(self, task_id: str, *, skipped_on: date) -> TaskSkip:
        self._require_task(task_id)
        if task_id in self._completions:
            raise ValueError("completed task cannot also be skipped")
        existing = self._skips.get(task_id)
        if existing is not None:
            if existing.skipped_on != skipped_on:
                raise ValueError("skip event is immutable once recorded")
            return existing
        event = TaskSkip(task_id=task_id, skipped_on=skipped_on)
        self._skips[task_id] = event
        return event

    def snapshot(self, *, today: date) -> LearningPlanSnapshot:
        return self._tracker.snapshot(
            self._current_schedule,
            today=today,
            completions=self.completions,
            skips=self.skips,
        )

    def overdue_ledger(self, *, today: date) -> OverdueLedger:
        return self._tracker.overdue_ledger(
            self._current_schedule,
            today=today,
            completions=self.completions,
            skips=self.skips,
        )

    def create_shift_preview(self, request: PlanShiftRequest) -> PlanShiftPreview:
        effective_request = request.model_copy(
            update={
                "completed_task_ids": frozenset(
                    set(request.completed_task_ids) | set(self._completions)
                )
            }
        )
        return self._shift_service.create_preview(
            self._current_schedule,
            effective_request,
        )

    def confirm_shift(
        self,
        preview_id: str,
        *,
        confirmation_id: str,
    ) -> PlanShiftConfirmation:
        return self._shift_service.confirm(
            preview_id,
            confirmation_id=confirmation_id,
        )

    def apply_shift(self, preview_id: str) -> PlanShiftApplication:
        preview = self._shift_service.get_preview(preview_id)
        if (
            self._current_schedule.schedule_id
            == preview.proposed_schedule.schedule_id
        ):
            return self._shift_service.apply(preview_id)
        if self._current_schedule.schedule_id != preview.source_schedule_id:
            raise StaleScheduleError("cannot apply preview to a newer schedule")
        moved_task_ids = {
            item.task_id for item in preview.moves if item.task_id is not None
        }
        terminal_task_ids = set(self._completions) | set(self._skips)
        if moved_task_ids & terminal_task_ids:
            raise StaleScheduleError(
                "progress changed after preview; create a new shift preview"
            )
        application = self._shift_service.apply(preview_id)
        self._current_schedule = application.applied_schedule
        return application

    def shift_preview_status(self, preview_id: str) -> ShiftPreviewStatus:
        return self._shift_service.preview_status(preview_id)

    def _require_task(self, task_id: str) -> None:
        if task_id not in self._current_schedule.task_by_id:
            raise ValueError("unknown runtime task")


__all__ = [
    "PlanRuntime",
    "PlanShiftError",
    "build_immutable_plan_schedule",
]
