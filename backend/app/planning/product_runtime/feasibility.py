"""Product-level, actionable explanations over solver infeasibility evidence."""

from __future__ import annotations

from datetime import timedelta
from math import ceil

from ..scheduling import (
    InfeasibilityCode,
    PlanScheduleProblem,
    PlanScheduleSolution,
    ScheduleStatus,
)
from .contracts import (
    FeasibilityCategory,
    FeasibilityExplanation,
    PlanCreationRequest,
    PlanFeasibilityReport,
)


def _calendar_day_count(request: PlanCreationRequest) -> int:
    current = request.start_date
    count = 0
    while current <= request.deadline:
        if current not in request.unavailable_dates:
            count += 1
        current += timedelta(days=1)
    return count


def explain_plan_feasibility(
    request: PlanCreationRequest,
    problem: PlanScheduleProblem,
    solution: PlanScheduleSolution,
) -> PlanFeasibilityReport:
    initial_minutes = sum(
        problem.planning_minutes(item.task_id) for item in problem.tasks
    )
    review_minutes = sum(item.minutes for item in problem.review_workloads)
    buffer_minutes = problem.final_buffer.minutes if problem.final_buffer else 0
    required_minutes = initial_minutes + review_minutes + buffer_minutes
    available_minutes = sum(problem.capacity_by_day.values())
    if solution.status is ScheduleStatus.FEASIBLE:
        return PlanFeasibilityReport(
            solved=True,
            feasible=True,
            required_minutes=required_minutes,
            available_minutes=available_minutes,
            initial_learning_minutes=initial_minutes,
            selected_review_minutes=review_minutes,
            buffer_minutes=buffer_minutes,
        )

    raw_codes = tuple(
        sorted({item.code.value for item in solution.infeasibilities})
    )
    by_category: dict[FeasibilityCategory, FeasibilityExplanation] = {}

    def add(
        category: FeasibilityCategory,
        summary: str,
        *,
        evidence: tuple[str, ...],
        suggested_action: str,
        required: int | None = None,
        available: int | None = None,
    ) -> None:
        if category in by_category:
            return
        shortfall = (
            max(0, required - available)
            if required is not None and available is not None
            else None
        )
        by_category[category] = FeasibilityExplanation(
            category=category,
            summary=summary,
            required_minutes=required,
            available_minutes=available,
            shortfall_minutes=shortfall,
            evidence=evidence,
            suggested_action=suggested_action,
        )

    if required_minutes > available_minutes:
        add(
            FeasibilityCategory.NOT_ENOUGH_TOTAL_CAPACITY,
            "The plan needs more total minutes than the selected calendar provides.",
            required=required_minutes,
            available=available_minutes,
            evidence=(
                f"initial_learning_minutes={initial_minutes}",
                f"selected_review_minutes={review_minutes}",
                f"buffer_minutes={buffer_minutes}",
            ),
            suggested_action=(
                "Increase weekly minutes, extend the deadline, reduce explicit "
                "scope, or select fewer review rounds."
            ),
        )

        if review_minutes and required_minutes - review_minutes <= available_minutes:
            add(
                FeasibilityCategory.SELECTED_REVIEW_ROUNDS_TOO_MANY,
                "Initial learning fits, but all selected review rounds do not.",
                required=required_minutes,
                available=available_minutes,
                evidence=(
                    f"selected_review_rounds={problem.selected_review_round_count}",
                    f"selected_review_minutes={review_minutes}",
                    "review_minutes_were_not_shortened=true",
                ),
                suggested_action=(
                    "Keep the rounds and add capacity/deadline, or explicitly "
                    "choose a smaller review-round count."
                ),
            )

        maximum_daily_minutes = max(
            (item.available_minutes for item in request.weekly_availability),
            default=0,
        )
        calendar_max_capacity = maximum_daily_minutes * _calendar_day_count(request)
        weekly_restrictive = (
            len(request.allowed_weekdays) < 7
            and required_minutes <= calendar_max_capacity
        )
        if weekly_restrictive:
            add(
                FeasibilityCategory.WEEKLY_PREFERENCE_TOO_RESTRICTIVE,
                "The date window could fit the work, but selected weekdays cannot.",
                required=required_minutes,
                available=available_minutes,
                evidence=(
                    "allowed_weekdays="
                    + ",".join(
                        item.value
                        for item in sorted(
                            request.allowed_weekdays,
                            key=lambda value: value.number,
                        )
                    ),
                    f"calendar_max_capacity={calendar_max_capacity}",
                    f"configured_weekly_capacity={request.weekly_capacity_minutes}",
                ),
                suggested_action="Enable more weekdays or increase minutes on allowed days.",
            )
        else:
            positive_capacities = [
                item.available_minutes
                for item in request.weekly_availability
                if item.available_minutes > 0
            ]
            typical_day = (
                round(sum(positive_capacities) / len(positive_capacities))
                if positive_capacities
                else 0
            )
            extra_learning_days = (
                ceil((required_minutes - available_minutes) / typical_day)
                if typical_day
                else None
            )
            add(
                FeasibilityCategory.DEADLINE_TOO_SHORT,
                "The current deadline does not expose enough learning days.",
                required=required_minutes,
                available=available_minutes,
                evidence=(
                    f"window_start={request.start_date.isoformat()}",
                    f"deadline={request.deadline.isoformat()}",
                    "additional_learning_days="
                    + (
                        str(extra_learning_days)
                        if extra_learning_days is not None
                        else "unknown"
                    ),
                ),
                suggested_action="Extend the deadline by additional learning days.",
            )

    raw_code_set = {item.code for item in solution.infeasibilities}
    if solution.status is ScheduleStatus.NOT_SOLVED:
        add(
            FeasibilityCategory.SOLVER_NOT_SOLVED,
            "The solver did not reach a feasibility conclusion within its run contract.",
            evidence=("raw_codes=" + ",".join(raw_codes),),
            suggested_action=(
                "Retry only through an explicit creation or regeneration request "
                "with an appropriate solver budget."
            ),
        )
    unsplittable_codes = {
        InfeasibilityCode.UNSPLITTABLE_TASK_EXCEEDS_DAILY_CAPACITY,
    }
    if raw_code_set & unsplittable_codes:
        task_ids = tuple(
            sorted(
                {
                    task_id
                    for reason in solution.infeasibilities
                    if reason.code in unsplittable_codes
                    for task_id in reason.task_ids
                }
            )
        )
        add(
            FeasibilityCategory.UNSPLITTABLE_TASK,
            "An unsplittable learning task is larger than every allowed day.",
            evidence=(
                "task_ids=" + ",".join(task_ids),
                "split_policy=unsplittable",
            ),
            suggested_action=(
                "Increase one day's capacity or explicitly make the task splittable."
            ),
        )

    prerequisite_codes = {
        InfeasibilityCode.PREREQUISITE_CYCLE,
        InfeasibilityCode.COMPLETED_PREREQUISITE_CONFLICT,
        InfeasibilityCode.COMPLETED_HISTORY_CONFLICT,
    }
    if raw_code_set & prerequisite_codes:
        task_ids = tuple(
            sorted(
                {
                    task_id
                    for reason in solution.infeasibilities
                    if reason.code in prerequisite_codes
                    for task_id in reason.task_ids
                }
            )
        )
        add(
            FeasibilityCategory.PREREQUISITE_CONFLICT,
            "Prerequisite or immutable completion history is contradictory.",
            evidence=(
                "task_ids=" + ",".join(task_ids),
                "raw_codes=" + ",".join(raw_codes),
            ),
            suggested_action="Repair the prerequisite graph or completion evidence.",
        )

    if (
        InfeasibilityCode.INFEASIBLE_WITH_SELECTED_REVIEW_ROUNDS in raw_code_set
        and review_minutes
    ):
        add(
            FeasibilityCategory.SELECTED_REVIEW_ROUNDS_TOO_MANY,
            "The complete constraint model cannot fit all selected review rounds.",
            required=required_minutes,
            available=available_minutes,
            evidence=(
                f"selected_review_rounds={problem.selected_review_round_count}",
                "review_minutes_were_not_shortened=true",
            ),
            suggested_action=(
                "Adjust capacity, deadline, weekly preferences, or explicitly "
                "select fewer rounds."
            ),
        )

    if not by_category:
        add(
            FeasibilityCategory.CONSTRAINT_INTERACTION,
            "The combined constraint model is infeasible beyond simple capacity checks.",
            evidence=("raw_codes=" + ",".join(raw_codes),),
            suggested_action=(
                "Review reservation windows, prerequisite order, and daily constraints."
            ),
        )

    return PlanFeasibilityReport(
        solved=solution.status is not ScheduleStatus.NOT_SOLVED,
        feasible=False,
        required_minutes=required_minutes,
        available_minutes=available_minutes,
        initial_learning_minutes=initial_minutes,
        selected_review_minutes=review_minutes,
        buffer_minutes=buffer_minutes,
        explanations=tuple(
            by_category[item]
            for item in FeasibilityCategory
            if item in by_category
        ),
        raw_infeasibility_codes=raw_codes,
    )


__all__ = ["explain_plan_feasibility"]
