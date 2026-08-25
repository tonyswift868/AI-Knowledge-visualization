from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.planning.review_effort import (
    InitialReviewScheduleRequest,
    ReviewEffortEstimate,
    ReviewEffortInput,
    ReviewEstimateUseCase,
    ReviewItem,
    ReviewItemPriority,
    ReviewQuestionType,
    ReviewRoundConfig,
    ReviewScheduleBridge,
    initial_learning_effort_for_problem,
)
from app.planning.runtime_v2 import (
    AUTO_CP_SAT_ON_MISS,
    AUTO_PROVIDER_ON_MISS,
    AUTO_REPLAN,
    AUTO_SHIFT_ON_MISS,
    PlanRuntime,
    PlanShiftRequest,
    ScheduledTaskStatus,
    Weekday,
    WeeklyLearningPreferences,
)
from app.planning.scheduling import (
    AllocationKind,
    ConstraintViolationCode,
    DailyCapacity,
    InfeasibilityCode,
    PlanScheduleProblem,
    ScheduleStatus,
    ScheduleValidator,
)
from constraint_scheduler_fixtures import prerequisite_dag, task


def review_items(problem: PlanScheduleProblem) -> tuple[ReviewItem, ...]:
    types = tuple(ReviewQuestionType)
    return tuple(
        ReviewItem(
            item_id=f"review-{item.task_id}",
            concept_id=item.task_id,
            question_type=types[index % len(types)],
            priority=(
                ReviewItemPriority.CRITICAL
                if index == len(problem.tasks) - 1
                else ReviewItemPriority.NORMAL
            ),
            critical_concept=index == len(problem.tasks) - 1,
        )
        for index, item in enumerate(problem.tasks)
    )


def request_for(
    problem: PlanScheduleProblem,
    *,
    rounds: int,
    allowed_weekdays: frozenset[int] = frozenset(range(7)),
    unavailable_dates: frozenset[date] = frozenset(),
) -> InitialReviewScheduleRequest:
    return InitialReviewScheduleRequest(
        request_id=f"schedule-{rounds}",
        base_problem=problem,
        effort_input=ReviewEffortInput(
            request_id=f"effort-{rounds}",
            use_case=ReviewEstimateUseCase.NEW_PLAN,
            initial_learning_effort=initial_learning_effort_for_problem(problem),
            round_config=ReviewRoundConfig(selected_rounds=rounds),
            items=review_items(problem) if rounds else (),
        ),
        allowed_weekdays=allowed_weekdays,
        unavailable_dates=unavailable_dates,
    )


def position(item: object) -> tuple[int, int]:
    return item.day.toordinal(), item.order_in_day


def test_three_fixed_rounds_are_formal_cp_sat_workload_and_not_free() -> None:
    scenario = prerequisite_dag()
    immutable_base_payload = scenario.problem.model_dump_json()

    result = ReviewScheduleBridge().solve(request_for(scenario.problem, rounds=3))

    assert result.schedule_solution.status is ScheduleStatus.FEASIBLE
    assert [item.workload_id for item in result.schedule_problem.review_workloads] == [
        "REVIEW_ROUND_1",
        "REVIEW_ROUND_2",
        "REVIEW_ROUND_3",
    ]
    assert result.schedule_problem.selected_review_round_count == 3
    task_minutes = sum(
        item.minutes
        for item in result.schedule_solution.allocations
        if item.kind is AllocationKind.TASK
    )
    review_minutes = sum(
        item.minutes
        for item in result.schedule_solution.allocations
        if item.kind is AllocationKind.REVIEW
    )
    assert task_minutes == result.plan_preview.initial_learning_effort.expected_minutes
    assert review_minutes == result.plan_preview.review_total.expected_minutes
    assert task_minutes + review_minutes == result.plan_preview.grand_total.expected_minutes
    assert all(
        load.expected_daily_minutes <= load.capacity_minutes
        for load in result.schedule_solution.daily_loads
    )
    assert ScheduleValidator().validate(
        result.schedule_problem,
        result.schedule_solution,
    ).valid
    assert scenario.problem.model_dump_json() == immutable_base_payload
    assert scenario.problem.review_workloads == ()


def test_relevant_learning_precedes_review_and_round_order_is_fixed() -> None:
    scenario = prerequisite_dag()
    result = ReviewScheduleBridge().solve(request_for(scenario.problem, rounds=3))
    allocations = result.schedule_solution.allocations
    task_values = {
        item.task_id: tuple(
            value for value in allocations if value.task_id == item.task_id
        )
        for item in scenario.problem.tasks
    }
    review_values = {
        workload.workload_id: tuple(
            value
            for value in allocations
            if value.reservation_id == workload.workload_id
        )
        for workload in result.schedule_problem.review_workloads
    }

    first_review_start = min(map(position, review_values["REVIEW_ROUND_1"]))
    assert all(
        max(map(position, task_values[item.task_id])) < first_review_start
        for item in scenario.problem.tasks
    )
    assert max(map(position, review_values["REVIEW_ROUND_1"])) < min(
        map(position, review_values["REVIEW_ROUND_2"])
    )
    assert max(map(position, review_values["REVIEW_ROUND_2"])) < min(
        map(position, review_values["REVIEW_ROUND_3"])
    )


def test_independent_validator_rejects_review_and_round_order_tampering() -> None:
    scenario = prerequisite_dag()
    result = ReviewScheduleBridge().solve(request_for(scenario.problem, rounds=3))
    original = result.schedule_solution

    review_one = next(
        item
        for item in original.allocations
        if item.reservation_id == "REVIEW_ROUND_1"
    )
    same_day_learning = max(
        (
            item
            for item in original.allocations
            if item.kind is AllocationKind.TASK and item.day == review_one.day
        ),
        key=lambda item: item.order_in_day,
    )
    assert same_day_learning.order_in_day < review_one.order_in_day
    tampered_learning_order = tuple(
        item.model_copy(update={"order_in_day": review_one.order_in_day})
        if item.allocation_id == same_day_learning.allocation_id
        else item.model_copy(update={"order_in_day": same_day_learning.order_in_day})
        if item.allocation_id == review_one.allocation_id
        else item
        for item in original.allocations
    )
    tampered_learning = original.model_copy(
        update={
            "allocations": tuple(
                sorted(tampered_learning_order, key=position)
            )
        }
    )
    learning_validation = ScheduleValidator().validate(
        result.schedule_problem,
        tampered_learning,
    )
    assert ConstraintViolationCode.REVIEW_AFTER_LEARNING_ORDER in {
        item.code for item in learning_validation.violations
    }

    round_one_last = max(
        (
            item
            for item in original.allocations
            if item.reservation_id == "REVIEW_ROUND_1"
        ),
        key=position,
    )
    round_two_first = min(
        (
            item
            for item in original.allocations
            if item.reservation_id == "REVIEW_ROUND_2"
        ),
        key=position,
    )
    assert round_one_last.day == round_two_first.day
    tampered_round_order = tuple(
        item.model_copy(update={"order_in_day": round_two_first.order_in_day})
        if item.allocation_id == round_one_last.allocation_id
        else item.model_copy(update={"order_in_day": round_one_last.order_in_day})
        if item.allocation_id == round_two_first.allocation_id
        else item
        for item in original.allocations
    )
    tampered_round = original.model_copy(
        update={"allocations": tuple(sorted(tampered_round_order, key=position))}
    )
    round_validation = ScheduleValidator().validate(
        result.schedule_problem,
        tampered_round,
    )
    assert ConstraintViolationCode.REVIEW_ROUND_ORDER in {
        item.code for item in round_validation.violations
    }


def test_weekly_preferences_and_unavailable_dates_apply_to_all_work() -> None:
    scenario = prerequisite_dag()
    blocked_wednesday = scenario.problem.start_date + timedelta(days=1)
    friday = scenario.problem.start_date + timedelta(days=3)
    problem = PlanScheduleProblem.model_validate(
        {
            **scenario.problem.model_dump(mode="python"),
            "deadline": friday,
            "daily_capacities": (
                *scenario.problem.daily_capacities,
                DailyCapacity(day=friday, available_minutes=60),
            ),
        }
    )
    request = request_for(
        problem,
        rounds=3,
        allowed_weekdays=frozenset((1, 2, 3, 4)),  # Tuesday through Friday
        unavailable_dates=frozenset((blocked_wednesday,)),
    )

    result = ReviewScheduleBridge().solve(request)

    assert result.schedule_solution.status is ScheduleStatus.FEASIBLE
    assert all(
        item.day.weekday() in request.allowed_weekdays
        and item.day not in request.unavailable_dates
        for item in result.schedule_solution.allocations
    )


def test_zero_rounds_schedules_only_initial_learning() -> None:
    scenario = prerequisite_dag()

    result = ReviewScheduleBridge().solve(request_for(scenario.problem, rounds=0))

    assert result.schedule_solution.status is ScheduleStatus.FEASIBLE
    assert result.schedule_problem.review_workloads == ()
    assert not any(
        item.kind is AllocationKind.REVIEW
        for item in result.schedule_solution.allocations
    )
    assert result.plan_preview.review_total.expected_minutes == 0


def test_selected_rounds_that_do_not_fit_have_explicit_infeasibility() -> None:
    start = date(2026, 10, 5)
    provisional = PlanScheduleProblem(
        problem_id="review-capacity-shortfall",
        tasks=(task("source", 50),),
        start_date=start,
        deadline=start,
        daily_capacities=(DailyCapacity(day=start, available_minutes=1_440),),
    )
    initial = initial_learning_effort_for_problem(provisional)
    problem = PlanScheduleProblem.model_validate(
        {
            **provisional.model_dump(mode="python"),
            "daily_capacities": (
                DailyCapacity(day=start, available_minutes=initial.expected_minutes),
            ),
        }
    )

    result = ReviewScheduleBridge().solve(request_for(problem, rounds=3))

    assert result.schedule_solution.status is ScheduleStatus.INFEASIBLE
    assert InfeasibilityCode.INFEASIBLE_WITH_SELECTED_REVIEW_ROUNDS in {
        item.code for item in result.schedule_solution.infeasibilities
    }
    assert result.schedule_solution.allocations == ()
    assert [
        item.minutes for item in result.schedule_problem.review_workloads
    ] == [
        item.expected_minutes for item in result.plan_preview.review_rounds
    ]


def test_review_workload_can_span_more_than_one_daily_capacity() -> None:
    scenario = prerequisite_dag()
    # The scheduling contract accepts a multi-day macro workload above 1440.
    from app.planning.scheduling import ReviewWorkload

    workload = ReviewWorkload(
        workload_id="REVIEW_ROUND_1",
        minutes=1_600,
        earliest_date=scenario.problem.start_date,
        latest_date=scenario.problem.deadline,
        round_index=1,
        relevant_task_ids=("root",),
    )
    assert workload.minutes == 1_600


def test_missed_review_is_overdue_without_cp_sat_or_date_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = prerequisite_dag()
    result = ReviewScheduleBridge().solve(request_for(scenario.problem, rounds=1))
    runtime = PlanRuntime.from_solver_result(
        result.schedule_problem,
        result.schedule_solution,
        plan_id="review-runtime",
    )
    original = runtime.current_schedule
    today = result.schedule_problem.deadline + timedelta(days=1)
    for learning_task in scenario.problem.tasks:
        runtime.record_completion(learning_task.task_id, completed_on=today)

    from ortools.sat.python import cp_model

    def forbidden_solve(*args: object, **kwargs: object) -> object:
        raise AssertionError("missed review must not invoke CP-SAT")

    monkeypatch.setattr(cp_model.CpSolver, "Solve", forbidden_solve)
    snapshot = runtime.snapshot(today=today)
    ledger = runtime.overdue_ledger(today=today)

    review = next(
        item for item in snapshot.tasks if item.task_id == "REVIEW_ROUND_1"
    )
    assert review.status is ScheduledTaskStatus.OVERDUE
    assert review.allocation_kind is AllocationKind.REVIEW
    assert ledger.active_task_ids == ("REVIEW_ROUND_1",)
    assert runtime.current_schedule == original
    assert AUTO_REPLAN is False
    assert AUTO_CP_SAT_ON_MISS is False
    assert AUTO_PROVIDER_ON_MISS is False
    assert AUTO_SHIFT_ON_MISS is False


def test_only_explicit_confirmed_shift_moves_an_overdue_review() -> None:
    scenario = prerequisite_dag()
    result = ReviewScheduleBridge().solve(request_for(scenario.problem, rounds=1))
    runtime = PlanRuntime.from_solver_result(
        result.schedule_problem,
        result.schedule_solution,
        plan_id="review-shift-runtime",
    )
    today = result.schedule_problem.deadline + timedelta(days=1)
    for learning_task in scenario.problem.tasks:
        runtime.record_completion(learning_task.task_id, completed_on=today)
    original_review_dates = tuple(
        item.day
        for item in runtime.current_schedule.allocations
        if item.reservation_id == "REVIEW_ROUND_1"
    )
    runtime.snapshot(today=today)
    assert tuple(
        item.day
        for item in runtime.current_schedule.allocations
        if item.reservation_id == "REVIEW_ROUND_1"
    ) == original_review_dates

    preview = runtime.create_shift_preview(
        PlanShiftRequest(
            request_id="explicit-review-shift",
            plan_id=runtime.current_schedule.plan_id,
            expected_schedule_version=runtime.current_schedule.version,
            calendar_days_delta=1,
            weekly_preferences=WeeklyLearningPreferences(
                allowed_weekdays=frozenset(Weekday)
            ),
        )
    )
    assert "REVIEW_ROUND_1" in {
        item.reservation_id for item in preview.moves
    }
    assert runtime.current_schedule == runtime.initial_schedule
    runtime.confirm_shift(preview.preview_id, confirmation_id="review-confirm")
    runtime.apply_shift(preview.preview_id)
    assert runtime.current_schedule.version == 2


def test_completed_review_round_is_immutable_during_later_explicit_shift() -> None:
    scenario = prerequisite_dag()
    result = ReviewScheduleBridge().solve(request_for(scenario.problem, rounds=2))
    runtime = PlanRuntime.from_solver_result(
        result.schedule_problem,
        result.schedule_solution,
        plan_id="completed-review-shift-runtime",
    )
    completed_on = result.schedule_problem.deadline
    for learning_task in scenario.problem.tasks:
        runtime.record_completion(learning_task.task_id, completed_on=completed_on)
    runtime.record_completion("REVIEW_ROUND_1", completed_on=completed_on)
    round_one_before = tuple(
        item
        for item in runtime.current_schedule.allocations
        if item.reservation_id == "REVIEW_ROUND_1"
    )

    preview = runtime.create_shift_preview(
        PlanShiftRequest(
            request_id="shift-only-remaining-review",
            plan_id=runtime.current_schedule.plan_id,
            expected_schedule_version=runtime.current_schedule.version,
            calendar_days_delta=1,
            weekly_preferences=WeeklyLearningPreferences(
                allowed_weekdays=frozenset(Weekday)
            ),
        )
    )

    assert not any(
        item.reservation_id == "REVIEW_ROUND_1" for item in preview.moves
    )
    assert any(
        item.reservation_id == "REVIEW_ROUND_2" for item in preview.moves
    )
    assert tuple(
        item
        for item in preview.proposed_schedule.allocations
        if item.reservation_id == "REVIEW_ROUND_1"
    ) == round_one_before


def test_plan_preview_roundtrips_with_visible_total_effort() -> None:
    scenario = prerequisite_dag()
    result = ReviewScheduleBridge().solve(request_for(scenario.problem, rounds=5))

    restored = ReviewEffortEstimate.model_validate_json(
        result.plan_preview.model_dump_json()
    )

    assert restored == result.plan_preview
    assert list(restored.review_round_efforts) == [
        f"REVIEW_ROUND_{index}" for index in range(1, 6)
    ]
    assert restored.grand_total == (
        restored.initial_learning_effort + restored.review_total
    )
