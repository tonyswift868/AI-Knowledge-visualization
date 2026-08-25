"""Bridge fixed review-round effort into an initial OR-Tools schedule."""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..effort import EffortEstimate
from ..scheduling import (
    EffortIntervalPolicy,
    KnowledgeTask,
    ORToolsScheduleSolver,
    PlanScheduleProblem,
    PlanScheduleSolution,
    ReviewWorkload,
    ScheduleSolveConfig,
)
from .contracts import EffortRange, ReviewEffortEstimate, ReviewEffortInput
from .estimator import estimate_review_effort


REVIEW_SCHEDULE_BRIDGE_VERSION = "mentorflow.review-schedule-bridge.v1"
REVIEW_SCHEDULE_PROVIDER_CALLS = 0


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
    )


def _initial_only_estimate(estimate: EffortEstimate) -> EffortEstimate:
    """Remove the legacy embedded review component for the W5 bridge only."""

    review = estimate.review_minutes
    expected = estimate.expected_minutes - review
    low = min(expected, max(0, estimate.low_minutes - review))
    high = max(expected, estimate.high_minutes - review)
    payload = estimate.model_dump(mode="python")
    payload.update(
        {
            "review_minutes": 0,
            "low_minutes": low,
            "expected_minutes": expected,
            "high_minutes": high,
            "basis": (
                *estimate.basis,
                "w5_initial_learning=understand+example+practice+assessment",
                "legacy_embedded_review=excluded;selected_rounds_estimated_separately",
            ),
        }
    )
    return EffortEstimate.model_validate(payload)


def initial_learning_effort_for_problem(
    problem: PlanScheduleProblem,
) -> EffortRange:
    estimates = tuple(_initial_only_estimate(item.effort) for item in problem.tasks)
    return EffortRange(
        low_minutes=sum(item.low_minutes for item in estimates),
        expected_minutes=sum(item.expected_minutes for item in estimates),
        high_minutes=sum(item.high_minutes for item in estimates),
    )


class InitialReviewScheduleRequest(_FrozenContract):
    request_id: str = Field(min_length=1, max_length=160)
    base_problem: PlanScheduleProblem
    effort_input: ReviewEffortInput
    allowed_weekdays: frozenset[int] = frozenset(range(7))
    unavailable_dates: frozenset[date] = frozenset()
    solve_config: ScheduleSolveConfig = ScheduleSolveConfig()

    @model_validator(mode="after")
    def validate_request(self) -> "InitialReviewScheduleRequest":
        if any(not isinstance(value, int) or not 0 <= value <= 6 for value in self.allowed_weekdays):
            raise ValueError("allowed_weekdays must contain weekday numbers 0..6")
        if self.base_problem.review_workloads:
            raise ValueError(
                "initial review bridge requires a base problem without review workloads"
            )
        if self.effort_input.initial_learning_effort != (
            initial_learning_effort_for_problem(self.base_problem)
        ):
            raise ValueError(
                "review preview initial effort must match the base learning tasks"
            )
        task_ids = set(self.base_problem.task_by_id)
        unknown = {
            item.concept_id for item in self.effort_input.items
        } - task_ids
        if unknown:
            raise ValueError("review item references an unknown learning task")
        if any(
            not self.base_problem.start_date <= day <= self.base_problem.deadline
            for day in self.unavailable_dates
        ):
            raise ValueError("unavailable review date lies outside plan window")
        return self


class InitialReviewScheduleResult(_FrozenContract):
    bridge_version: str = REVIEW_SCHEDULE_BRIDGE_VERSION
    request_id: str = Field(min_length=1, max_length=160)
    effort_estimate: ReviewEffortEstimate
    schedule_problem: PlanScheduleProblem
    schedule_solution: PlanScheduleSolution

    @model_validator(mode="after")
    def validate_result(self) -> "InitialReviewScheduleResult":
        if self.schedule_problem.selected_review_round_count != (
            self.effort_estimate.selected_review_rounds
        ):
            raise ValueError("scheduled rounds must equal the selected rounds")
        if self.schedule_solution.problem_id != self.schedule_problem.problem_id:
            raise ValueError("schedule solution does not match the bridged problem")
        return self

    @property
    def plan_preview(self) -> ReviewEffortEstimate:
        return self.effort_estimate


def _round_minutes(
    estimate: ReviewEffortEstimate,
    round_index: int,
    policy: EffortIntervalPolicy,
) -> int:
    round_effort = estimate.review_rounds[round_index - 1]
    return {
        EffortIntervalPolicy.LOW: round_effort.low_minutes,
        EffortIntervalPolicy.EXPECTED: round_effort.expected_minutes,
        EffortIntervalPolicy.HIGH: round_effort.high_minutes,
    }[policy]


def _problem_id(
    request: InitialReviewScheduleRequest,
    estimate: ReviewEffortEstimate,
) -> str:
    payload = {
        "base_problem_id": request.base_problem.problem_id,
        "estimate_id": estimate.estimate_id,
        "allowed_weekdays": sorted(request.allowed_weekdays),
        "unavailable_dates": sorted(day.isoformat() for day in request.unavailable_dates),
    }
    digest = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    prefix = request.base_problem.problem_id[:130]
    return f"{prefix}:reviews:{digest}"


def build_initial_review_schedule_problem(
    request: InitialReviewScheduleRequest,
    estimate: ReviewEffortEstimate,
) -> PlanScheduleProblem:
    """Create a new problem; the supplied base and published plans stay immutable."""

    if estimate.selected_review_rounds != (
        request.effort_input.round_config.selected_rounds
    ):
        raise ValueError("review estimate does not match the request round count")
    if estimate.initial_learning_effort != request.effort_input.initial_learning_effort:
        raise ValueError("review estimate does not match initial learning effort")

    tasks = tuple(
        KnowledgeTask(
            **item.model_dump(mode="python", exclude={"effort"}),
            effort=_initial_only_estimate(item.effort),
        )
        for item in request.base_problem.tasks
    )
    unavailable_days = set(request.base_problem.unavailable_days)
    unavailable_days.update(request.unavailable_dates)
    unavailable_days.update(
        item.day
        for item in request.base_problem.daily_capacities
        if item.day.weekday() not in request.allowed_weekdays
    )
    workloads = tuple(
        ReviewWorkload(
            workload_id=round_effort.round_id,
            minutes=_round_minutes(
                estimate,
                round_effort.round_index,
                request.base_problem.effort_interval_policy,
            ),
            earliest_date=request.base_problem.start_date,
            latest_date=request.base_problem.deadline,
            splittable=True,
            round_index=round_effort.round_index,
            relevant_task_ids=round_effort.relevant_concept_ids,
        )
        for round_effort in estimate.review_rounds
    )
    payload = request.base_problem.model_dump(mode="python")
    payload.update(
        {
            "problem_id": _problem_id(request, estimate),
            "tasks": tasks,
            "review_workloads": workloads,
            "unavailable_days": frozenset(unavailable_days),
        }
    )
    return PlanScheduleProblem.model_validate(payload)


class ReviewScheduleBridge:
    """Estimate then solve an initial plan exactly once with formal review load."""

    def __init__(self, *, solver: ORToolsScheduleSolver | None = None) -> None:
        if ORToolsScheduleSolver is None:
            raise RuntimeError("OR-Tools scheduling dependency is unavailable")
        self._solver = solver or ORToolsScheduleSolver()

    def build_problem(
        self,
        request: InitialReviewScheduleRequest,
    ) -> tuple[ReviewEffortEstimate, PlanScheduleProblem]:
        estimate = estimate_review_effort(request.effort_input)
        return estimate, build_initial_review_schedule_problem(request, estimate)

    def solve(self, request: InitialReviewScheduleRequest) -> InitialReviewScheduleResult:
        estimate, problem = self.build_problem(request)
        solution = self._solver.solve(problem, config=request.solve_config)
        return InitialReviewScheduleResult(
            request_id=request.request_id,
            effort_estimate=estimate,
            schedule_problem=problem,
            schedule_solution=solution,
        )


__all__ = [
    "REVIEW_SCHEDULE_BRIDGE_VERSION",
    "REVIEW_SCHEDULE_PROVIDER_CALLS",
    "InitialReviewScheduleRequest",
    "InitialReviewScheduleResult",
    "ReviewScheduleBridge",
    "build_initial_review_schedule_problem",
    "initial_learning_effort_for_problem",
]
