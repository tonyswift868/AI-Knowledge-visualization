"""Plan creation pipeline from product request to UI-ready preview."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from hashlib import sha256
import json
from typing import Any

from pydantic import BaseModel

from ..review_effort import (
    InitialReviewScheduleRequest,
    ReviewEffortInput,
    ReviewEffortEstimate,
    ReviewEstimateUseCase,
    ReviewScheduleBridge,
    initial_learning_effort_for_problem,
)
from ..scheduling import (
    AllocationKind,
    DailyCapacity,
    EffortIntervalPolicy,
    FinalBuffer,
    PlanScheduleProblem,
    PlanScheduleSolution,
    ScheduleStatus,
    SolverTerminationStatus,
)
from .contracts import (
    InitialLearningEffort,
    PlanBufferPreview,
    PlanCostShapeSeam,
    PlanCreationMode,
    PlanCreationRequest,
    PlanEffortEnvelope,
    PlanPreviewDTO,
    PlanPreviewDailyLoad,
    PlanPreviewStatus,
)
from .feasibility import explain_plan_feasibility


PLAN_CREATION_PROVIDER_CALLS = 0


def _canonical(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, frozenset | set):
        values = [_canonical(item) for item in value]
        return sorted(
            values,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    if isinstance(value, tuple | list):
        return [_canonical(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def canonical_creation_request(request: PlanCreationRequest) -> str:
    return json.dumps(
        _canonical(request),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _request_digest(request: PlanCreationRequest) -> str:
    return sha256(canonical_creation_request(request).encode("utf-8")).hexdigest()


def _aggregate_buffer(
    request: PlanCreationRequest,
    digest: str,
) -> FinalBuffer | None:
    if not request.buffers:
        return None
    first = request.buffers[0]
    return FinalBuffer(
        buffer_id=f"PLAN_BUFFER_{digest[:16]}",
        minutes=request.buffer_total_minutes,
        earliest_date=first.earliest_date,
        latest_date=first.latest_date,
    )


def build_base_schedule_problem(request: PlanCreationRequest) -> PlanScheduleProblem:
    """Materialize weekly availability into explicit per-date capacities."""

    digest = _request_digest(request)
    capacity_by_weekday = {
        item.weekday.number: item.available_minutes
        for item in request.weekly_availability
    }
    capacities: list[DailyCapacity] = []
    current = request.start_date
    while current <= request.deadline:
        capacities.append(
            DailyCapacity(
                day=current,
                available_minutes=capacity_by_weekday.get(current.weekday(), 0),
            )
        )
        current += timedelta(days=1)
    return PlanScheduleProblem(
        problem_id=f"plan-create-{digest[:24]}",
        tasks=request.knowledge_tasks,
        prerequisite_edges=request.prerequisite_edges,
        start_date=request.start_date,
        deadline=request.deadline,
        daily_capacities=tuple(capacities),
        unavailable_days=request.unavailable_dates,
        final_buffer=_aggregate_buffer(request, digest),
        effort_interval_policy=request.effort_interval_policy,
        deterministic=True,
    )


def _effort_envelope(estimate: ReviewEffortEstimate) -> PlanEffortEnvelope:
    initial = InitialLearningEffort.model_validate(
        estimate.initial_learning_effort.model_dump(mode="python")
    )
    return PlanEffortEnvelope(
        initial_learning_effort=initial,
        review_round_efforts=estimate.review_rounds,
        review_total=estimate.review_total,
        total=estimate.grand_total,
    )


def _daily_loads(solution: PlanScheduleSolution) -> tuple[PlanPreviewDailyLoad, ...]:
    return tuple(
        PlanPreviewDailyLoad(
            day=item.day,
            task_minutes=item.task_minutes,
            review_minutes=item.review_minutes,
            buffer_minutes=item.buffer_minutes,
            total_minutes=item.expected_daily_minutes,
            capacity_minutes=item.capacity_minutes,
            utilization=item.utilization,
        )
        for item in solution.daily_loads
    )


def _buffer_previews(
    request: PlanCreationRequest,
    problem: PlanScheduleProblem,
    solution: PlanScheduleSolution,
) -> tuple[PlanBufferPreview, ...]:
    scheduled_dates = tuple(
        sorted(
            {
                item.day
                for item in solution.allocations
                if item.kind is AllocationKind.FINAL_BUFFER
                and problem.final_buffer is not None
                and item.reservation_id == problem.final_buffer.buffer_id
            }
        )
    )
    return tuple(
        PlanBufferPreview(
            buffer_id=item.buffer_id,
            label=item.label,
            minutes=item.minutes,
            scheduled_dates=scheduled_dates,
        )
        for item in request.buffers
    )


@dataclass(frozen=True)
class PlanCreationOutcome:
    request: PlanCreationRequest
    preview: PlanPreviewDTO
    schedule_problem: PlanScheduleProblem
    schedule_solution: PlanScheduleSolution


class PlanCreationService:
    """Create one initial-plan preview through the real W5 review bridge."""

    def __init__(self, *, review_bridge: ReviewScheduleBridge | None = None) -> None:
        self._review_bridge = review_bridge or ReviewScheduleBridge()

    def create(self, request: PlanCreationRequest) -> PlanCreationOutcome:
        base_problem = build_base_schedule_problem(request)
        initial_effort = initial_learning_effort_for_problem(base_problem)
        effort_input = ReviewEffortInput(
            request_id=f"{request.request_id}:review-effort",
            use_case=(
                ReviewEstimateUseCase.NEW_PLAN
                if request.creation_mode is PlanCreationMode.NEW_PLAN
                else ReviewEstimateUseCase.USER_REQUESTED_REGENERATION
            ),
            initial_learning_effort=initial_effort,
            round_config=request.review_rounds,
            items=request.review_items,
            historical_calibration=request.historical_calibration,
        )
        review_request = InitialReviewScheduleRequest(
            request_id=f"{request.request_id}:initial-schedule",
            base_problem=base_problem,
            effort_input=effort_input,
            allowed_weekdays=frozenset(
                item.number for item in request.allowed_weekdays
            ),
            unavailable_dates=request.unavailable_dates,
            solve_config=request.solve_config,
        )
        result = self._review_bridge.solve(review_request)
        feasibility = explain_plan_feasibility(
            request,
            result.schedule_problem,
            result.schedule_solution,
        )
        envelope = _effort_envelope(result.effort_estimate)
        feasible = result.schedule_solution.status is ScheduleStatus.FEASIBLE
        warnings: list[str] = []
        if request.historical_calibration is None and request.review_rounds.selected_rounds:
            warnings.append("review_effort_uses_conservative_new_learner_priors")
        if request.review_rounds.selected_rounds == 0:
            warnings.append("no_review_rounds_selected")
        if request.buffers:
            warnings.append(
                "buffers_consume_schedule_capacity_but_are_excluded_from_learning_effort"
            )
        if request.effort_interval_policy is not EffortIntervalPolicy.EXPECTED:
            warnings.append(
                "schedule_capacity_uses_"
                f"{request.effort_interval_policy.value}_effort_interval"
            )
        if not feasible:
            warnings.extend(
                f"{item.category.value}:{item.summary}"
                for item in feasibility.explanations
            )
        finish_date = (
            max(item.day for item in result.schedule_solution.allocations)
            if feasible
            else None
        )
        digest = _request_digest(request)
        cp_sat_calls = (
            0
            if result.schedule_solution.solver_metadata.termination_status
            is SolverTerminationStatus.NOT_RUN
            else 1
        )
        preview = PlanPreviewDTO(
            preview_id=f"plan-preview-{digest[:24]}",
            request_id=request.request_id,
            plan_id=request.plan_id,
            status=(
                PlanPreviewStatus.READY_FOR_CONFIRMATION
                if feasible
                else (
                    PlanPreviewStatus.NOT_SOLVED
                    if result.schedule_solution.status is ScheduleStatus.NOT_SOLVED
                    else PlanPreviewStatus.INFEASIBLE
                )
            ),
            effort=envelope,
            total_hours=envelope.expected_hours,
            initial_learning=envelope.initial_learning_effort,
            review_rounds=envelope.review_round_efforts,
            review_total=envelope.review_total,
            daily_load=_daily_loads(result.schedule_solution) if feasible else (),
            finish_date=finish_date,
            buffers=_buffer_previews(
                request,
                result.schedule_problem,
                result.schedule_solution,
            ),
            buffer_total_minutes=request.buffer_total_minutes,
            warnings=tuple(warnings),
            cost_shape=PlanCostShapeSeam(cp_sat_solve_calls=cp_sat_calls),
            feasibility=feasibility,
            schedule_solution_id=result.schedule_solution.solution_id,
        )
        return PlanCreationOutcome(
            request=request,
            preview=preview,
            schedule_problem=result.schedule_problem,
            schedule_solution=result.schedule_solution,
        )


__all__ = [
    "PLAN_CREATION_PROVIDER_CALLS",
    "PlanCreationOutcome",
    "PlanCreationService",
    "build_base_schedule_problem",
    "canonical_creation_request",
]
