from __future__ import annotations

from datetime import date

import pytest

from app.planning.product_runtime import (
    FeasibilityCategory,
    InfeasiblePlanCannotPublishError,
    PlanCreationRequest,
    PlanPreviewStatus,
    PlanProductRuntime,
    PlanPublishConfirmationRequest,
    WeeklyAvailability,
)
from app.planning.review_effort import ReviewRoundConfig
from app.planning.runtime_v2 import Weekday
from app.planning.scheduling import PrerequisiteEdge, TaskSplitPolicy
from plan_product_runtime_fixtures import (
    knowledge_task,
    review_items_for,
)


def request_for(
    *,
    request_id: str,
    tasks: tuple,
    start: date,
    deadline: date,
    weekly: tuple[WeeklyAvailability, ...],
    rounds: int = 0,
    edges: tuple[PrerequisiteEdge, ...] = (),
) -> PlanCreationRequest:
    items = review_items_for(tasks) if rounds else ()
    return PlanCreationRequest(
        request_id=request_id,
        plan_id=f"plan-{request_id}",
        knowledge_tasks=tasks,
        prerequisite_edges=edges,
        review_items=items,
        review_rounds=ReviewRoundConfig(selected_rounds=rounds),
        weekly_availability=weekly,
        start_date=start,
        deadline=deadline,
    )


def categories(preview: object) -> set[FeasibilityCategory]:
    return {item.category for item in preview.feasibility.explanations}


def test_total_capacity_and_selected_review_rounds_are_distinct_reasons() -> None:
    monday = date(2026, 10, 5)
    tasks = (knowledge_task("source", 50),)
    request = request_for(
        request_id="selected-round-shortfall",
        tasks=tasks,
        start=monday,
        deadline=monday,
        weekly=(
            WeeklyAvailability(weekday=Weekday.MONDAY, available_minutes=60),
        ),
        rounds=3,
    )

    preview = PlanProductRuntime().create_preview(request)

    assert preview.status is PlanPreviewStatus.INFEASIBLE
    assert FeasibilityCategory.NOT_ENOUGH_TOTAL_CAPACITY in categories(preview)
    assert FeasibilityCategory.SELECTED_REVIEW_ROUNDS_TOO_MANY in categories(
        preview
    )
    assert preview.feasibility.initial_learning_minutes == 50
    assert preview.feasibility.selected_review_minutes > 0
    assert preview.cost_shape.cp_sat_solve_calls == 0
    assert preview.cost_shape.provider_calls == 0
    assert any(
        "review_minutes_were_not_shortened=true" in item.evidence
        for item in preview.feasibility.explanations
        if item.category is FeasibilityCategory.SELECTED_REVIEW_ROUNDS_TOO_MANY
    )


def test_deadline_too_short_is_distinct_from_weekly_restriction() -> None:
    monday = date(2026, 10, 5)
    request = request_for(
        request_id="deadline-too-short",
        tasks=(knowledge_task("large", 50),),
        start=monday,
        deadline=monday,
        weekly=(
            WeeklyAvailability(weekday=Weekday.MONDAY, available_minutes=30),
        ),
    )

    preview = PlanProductRuntime().create_preview(request)

    assert FeasibilityCategory.DEADLINE_TOO_SHORT in categories(preview)
    assert FeasibilityCategory.WEEKLY_PREFERENCE_TOO_RESTRICTIVE not in categories(
        preview
    )


def test_weekly_preference_too_restrictive_has_calendar_evidence() -> None:
    monday = date(2026, 10, 5)
    request = request_for(
        request_id="weekly-too-restrictive",
        tasks=(knowledge_task("weekly-load", 100),),
        start=monday,
        deadline=date(2026, 10, 11),
        weekly=(
            WeeklyAvailability(weekday=Weekday.MONDAY, available_minutes=60),
        ),
    )

    preview = PlanProductRuntime().create_preview(request)
    explanation = next(
        item
        for item in preview.feasibility.explanations
        if item.category is FeasibilityCategory.WEEKLY_PREFERENCE_TOO_RESTRICTIVE
    )

    assert "allowed_weekdays=monday" in explanation.evidence
    assert explanation.available_minutes == 60
    assert explanation.required_minutes == 100


def test_unsplittable_task_has_specific_reason_even_when_total_capacity_fits() -> None:
    monday = date(2026, 10, 5)
    request = request_for(
        request_id="unsplittable-task",
        tasks=(
            knowledge_task(
                "atomic-lab",
                60,
                split_policy=TaskSplitPolicy.UNSPLITTABLE,
            ),
        ),
        start=monday,
        deadline=date(2026, 10, 6),
        weekly=(
            WeeklyAvailability(weekday=Weekday.MONDAY, available_minutes=40),
            WeeklyAvailability(weekday=Weekday.TUESDAY, available_minutes=40),
        ),
    )

    preview = PlanProductRuntime().create_preview(request)

    assert preview.feasibility.available_minutes == 80
    assert preview.feasibility.required_minutes == 60
    assert categories(preview) == {FeasibilityCategory.UNSPLITTABLE_TASK}


def test_prerequisite_cycle_has_specific_product_explanation() -> None:
    monday = date(2026, 10, 5)
    tasks = (knowledge_task("cycle-a", 10), knowledge_task("cycle-b", 10))
    request = request_for(
        request_id="prerequisite-cycle",
        tasks=tasks,
        start=monday,
        deadline=monday,
        weekly=(
            WeeklyAvailability(weekday=Weekday.MONDAY, available_minutes=60),
        ),
        edges=(
            PrerequisiteEdge(
                prerequisite_task_id="cycle-a",
                dependent_task_id="cycle-b",
            ),
            PrerequisiteEdge(
                prerequisite_task_id="cycle-b",
                dependent_task_id="cycle-a",
            ),
        ),
    )

    preview = PlanProductRuntime().create_preview(request)

    assert categories(preview) == {FeasibilityCategory.PREREQUISITE_CONFLICT}
    assert "prerequisite_cycle" in preview.feasibility.raw_infeasibility_codes


def test_infeasible_preview_cannot_be_confirmed_or_published() -> None:
    monday = date(2026, 10, 5)
    request = request_for(
        request_id="unpublishable",
        tasks=(knowledge_task("too-much", 100),),
        start=monday,
        deadline=monday,
        weekly=(
            WeeklyAvailability(weekday=Weekday.MONDAY, available_minutes=10),
        ),
    )
    runtime = PlanProductRuntime()
    preview = runtime.create_preview(request)

    assert preview.status is PlanPreviewStatus.INFEASIBLE
    assert preview.daily_load == ()
    assert preview.finish_date is None
    assert preview.feasibility.explanations
    with pytest.raises(InfeasiblePlanCannotPublishError):
        runtime.confirm_publication(
            PlanPublishConfirmationRequest(
                preview_id=preview.preview_id,
                confirmation_id="cannot-confirm",
                explicitly_confirmed_by_user=True,
            )
        )
    with pytest.raises(InfeasiblePlanCannotPublishError):
        runtime.publish(preview.preview_id, published_on=monday)


def test_feasible_report_contains_no_false_warnings() -> None:
    monday = date(2026, 10, 5)
    request = request_for(
        request_id="feasible-report",
        tasks=(knowledge_task("small", 20),),
        start=monday,
        deadline=monday,
        weekly=(
            WeeklyAvailability(weekday=Weekday.MONDAY, available_minutes=60),
        ),
    )

    preview = PlanProductRuntime().create_preview(request)

    assert preview.status is PlanPreviewStatus.READY_FOR_CONFIRMATION
    assert preview.feasibility.feasible is True
    assert preview.feasibility.explanations == ()
    assert preview.feasibility.raw_infeasibility_codes == ()
