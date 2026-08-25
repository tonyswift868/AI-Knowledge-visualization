from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.planning.runtime_v2 import (
    NoValidFutureLearningDay,
    PlanRuntime,
    PlanShiftRequest,
    ShiftNotConfirmedError,
    ShiftPreviewStatus,
    StaleScheduleError,
    Weekday,
    WeeklyLearningPreferences,
)
from constraint_scheduler_fixtures import (
    completed_replan,
    prerequisite_dag,
    simple_feasible,
)


ALLOWED_MON_TUE_THU_FRI = WeeklyLearningPreferences(
    allowed_weekdays=frozenset(
        (
            Weekday.MONDAY,
            Weekday.TUESDAY,
            Weekday.THURSDAY,
            Weekday.FRIDAY,
        )
    )
)


def runtime_for_shift() -> tuple[object, PlanRuntime]:
    scenario = prerequisite_dag()
    runtime = PlanRuntime.from_solver_result(
        scenario.problem,
        scenario.solution,
        plan_id="shift-plan",
    )
    return scenario, runtime


def request_for(
    runtime: PlanRuntime,
    *,
    request_id: str = "shift-request-1",
    delta: int = 1,
    preferences: WeeklyLearningPreferences = ALLOWED_MON_TUE_THU_FRI,
) -> PlanShiftRequest:
    return PlanShiftRequest(
        request_id=request_id,
        plan_id=runtime.current_schedule.plan_id,
        expected_schedule_version=runtime.current_schedule.version,
        calendar_days_delta=delta,
        weekly_preferences=preferences,
    )


def allocation_signature(schedule: object) -> dict[str, tuple[object, ...]]:
    return {
        item.allocation_id: (
            item.kind,
            item.task_id,
            item.reservation_id,
            item.minutes,
            item.chunk_index,
            item.chunk_count,
        )
        for item in schedule.allocations
    }


def test_shift_preview_maps_each_learning_day_to_next_allowed_day() -> None:
    scenario, runtime = runtime_for_shift()
    original = runtime.current_schedule

    preview = runtime.create_shift_preview(request_for(runtime))

    assert preview.status is ShiftPreviewStatus.PENDING_CONFIRMATION
    assert preview.old_start == scenario.problem.start_date
    assert preview.new_start == date(2026, 9, 3)
    assert preview.old_end == date(2026, 9, 3)
    assert preview.new_end == date(2026, 9, 7)
    assert preview.calendar_days_added == 4
    assert preview.learning_days_added == 2
    assert preview.tasks_moved == 4
    assert preview.allocations_moved == 4
    assert preview.non_learning_days_skipped == 3
    assert runtime.current_schedule == original


def test_unconfirmed_preview_cannot_apply_and_plan_stays_immutable() -> None:
    _, runtime = runtime_for_shift()
    original = runtime.current_schedule
    preview = runtime.create_shift_preview(request_for(runtime))

    with pytest.raises(ShiftNotConfirmedError):
        runtime.apply_shift(preview.preview_id)

    assert runtime.current_schedule == original
    assert runtime.shift_preview_status(preview.preview_id) is (
        ShiftPreviewStatus.PENDING_CONFIRMATION
    )


def test_confirm_and_apply_are_idempotent_without_duplicate_shift() -> None:
    _, runtime = runtime_for_shift()
    immutable_initial = runtime.initial_schedule
    request = request_for(runtime)
    preview = runtime.create_shift_preview(request)
    duplicate_preview = runtime.create_shift_preview(request)

    first_confirmation = runtime.confirm_shift(
        preview.preview_id,
        confirmation_id="confirm-once",
    )
    duplicate_confirmation = runtime.confirm_shift(
        preview.preview_id,
        confirmation_id="ignored-duplicate-confirmation",
    )
    first_application = runtime.apply_shift(preview.preview_id)
    duplicate_application = runtime.apply_shift(preview.preview_id)

    assert duplicate_preview == preview
    assert duplicate_confirmation == first_confirmation
    assert duplicate_application == first_application
    assert runtime.current_schedule.version == 2
    assert runtime.current_schedule == preview.proposed_schedule
    assert runtime.initial_schedule == immutable_initial
    assert runtime.current_schedule.deadline == (
        immutable_initial.deadline
        + timedelta(days=preview.calendar_days_added)
    )
    assert runtime.shift_preview_status(preview.preview_id) is ShiftPreviewStatus.APPLIED


def test_two_distinct_confirmed_shifts_advance_exactly_one_version_each() -> None:
    _, runtime = runtime_for_shift()
    first = runtime.create_shift_preview(
        request_for(runtime, request_id="version-shift-1")
    )
    runtime.confirm_shift(first.preview_id, confirmation_id="version-confirm-1")
    runtime.apply_shift(first.preview_id)
    second = runtime.create_shift_preview(
        request_for(runtime, request_id="version-shift-2")
    )
    runtime.confirm_shift(second.preview_id, confirmation_id="version-confirm-2")
    runtime.apply_shift(second.preview_id)

    assert runtime.current_schedule.version == 3


def test_shift_preserves_workload_task_sequence_and_prerequisite_order() -> None:
    _, runtime = runtime_for_shift()
    source = runtime.current_schedule
    preview = runtime.create_shift_preview(request_for(runtime))
    target = preview.proposed_schedule

    assert allocation_signature(target) == allocation_signature(source)
    assert target.tasks == source.tasks
    positions: dict[str, list[tuple[int, int]]] = {}
    for allocation in target.task_allocations:
        positions.setdefault(allocation.task_id, []).append(
            (allocation.day.toordinal(), allocation.order_in_day)
        )
    for task in target.tasks:
        for prerequisite_id in task.prerequisite_task_ids:
            assert max(positions[prerequisite_id]) < min(positions[task.task_id])


def test_shift_moves_review_and_final_buffer_without_changing_workload() -> None:
    scenario = simple_feasible()
    runtime = PlanRuntime.from_solver_result(
        scenario.problem,
        scenario.solution,
        plan_id="reservation-shift",
    )
    source = runtime.current_schedule
    preview = runtime.create_shift_preview(
        PlanShiftRequest(
            request_id="reservation-shift-request",
            plan_id=source.plan_id,
            expected_schedule_version=1,
            calendar_days_delta=1,
            weekly_preferences=WeeklyLearningPreferences(
                allowed_weekdays=frozenset(Weekday)
            ),
        )
    )

    assert preview.allocations_moved == len(source.allocations)
    assert {item.reservation_id for item in preview.moves if item.reservation_id} == {
        "review-1",
        "final-buffer",
    }
    assert allocation_signature(preview.proposed_schedule) == (
        allocation_signature(source)
    )


def test_completed_history_is_never_shifted() -> None:
    scenario = completed_replan()
    runtime = PlanRuntime.from_solver_result(
        scenario.new_problem,
        scenario.new_solution,
        plan_id="completed-shift-plan",
    )
    completed = scenario.new_problem.completed_tasks[0].allocations[0]
    request = PlanShiftRequest(
        request_id="completed-shift",
        plan_id=runtime.current_schedule.plan_id,
        expected_schedule_version=1,
        calendar_days_delta=1,
        weekly_preferences=ALLOWED_MON_TUE_THU_FRI,
        completed_task_ids=frozenset(("completed",)),
    )

    preview = runtime.create_shift_preview(request)

    assert completed in preview.proposed_schedule.allocations
    remaining_move = next(
        item for item in preview.moves if item.task_id == "remaining"
    )
    assert remaining_move.new_date > remaining_move.old_date
    assert preview.tasks_moved == 1


def test_runtime_completion_event_is_automatically_frozen_during_shift() -> None:
    scenario, runtime = runtime_for_shift()
    root_before = next(
        item
        for item in runtime.current_schedule.allocations
        if item.task_id == "root"
    )
    runtime.record_completion(
        "root",
        completed_on=scenario.problem.start_date,
    )

    preview = runtime.create_shift_preview(request_for(runtime))
    root_after = next(
        item
        for item in preview.proposed_schedule.allocations
        if item.task_id == "root"
    )

    assert root_after == root_before
    assert "root" not in {item.task_id for item in preview.moves}


def test_weekend_holiday_and_consecutive_blocked_dates_are_skipped() -> None:
    _, runtime = runtime_for_shift()
    preferences = WeeklyLearningPreferences(
        allowed_weekdays=frozenset(
            (
                Weekday.MONDAY,
                Weekday.TUESDAY,
                Weekday.WEDNESDAY,
                Weekday.THURSDAY,
                Weekday.FRIDAY,
            )
        ),
        unavailable_dates=frozenset((date(2026, 9, 7), date(2026, 9, 8))),
    )

    preview = runtime.create_shift_preview(
        request_for(
            runtime,
            request_id="blocked-calendar",
            delta=4,
            preferences=preferences,
        )
    )

    assert preview.new_start == date(2026, 9, 9)
    assert preview.non_learning_days_skipped >= 4
    assert all(
        preferences.allows(move.new_date) for move in preview.moves
    )


def test_new_start_date_mode_is_explicit_and_forward_only() -> None:
    _, runtime = runtime_for_shift()
    request = PlanShiftRequest(
        request_id="new-start-mode",
        plan_id=runtime.current_schedule.plan_id,
        expected_schedule_version=1,
        new_start_date=date(2026, 9, 10),
        weekly_preferences=ALLOWED_MON_TUE_THU_FRI,
    )

    preview = runtime.create_shift_preview(request)

    assert preview.new_start == date(2026, 9, 10)
    with pytest.raises(ValueError, match="exactly one"):
        PlanShiftRequest(
            request_id="invalid-modes",
            plan_id=runtime.current_schedule.plan_id,
            expected_schedule_version=1,
            new_start_date=date(2026, 9, 10),
            calendar_days_delta=1,
            weekly_preferences=ALLOWED_MON_TUE_THU_FRI,
        )


def test_shift_from_date_leaves_earlier_uncompleted_work_unchanged() -> None:
    scenario, runtime = runtime_for_shift()
    root_before = next(
        item
        for item in runtime.current_schedule.allocations
        if item.task_id == "root"
    )
    request = PlanShiftRequest(
        request_id="partial-forward-shift",
        plan_id=runtime.current_schedule.plan_id,
        expected_schedule_version=1,
        shift_from_date=scenario.problem.start_date + timedelta(days=1),
        calendar_days_delta=1,
        weekly_preferences=ALLOWED_MON_TUE_THU_FRI,
    )

    preview = runtime.create_shift_preview(request)
    root_after = next(
        item
        for item in preview.proposed_schedule.allocations
        if item.task_id == "root"
    )

    assert root_after == root_before
    assert preview.old_start == scenario.problem.start_date + timedelta(days=1)
    assert preview.tasks_moved == 3


def test_no_valid_future_learning_day_is_explicit_failure() -> None:
    _, runtime = runtime_for_shift()
    request = request_for(
        runtime,
        request_id="no-learning-days",
        preferences=WeeklyLearningPreferences(allowed_weekdays=frozenset()),
    )

    with pytest.raises(NoValidFutureLearningDay):
        runtime.create_shift_preview(request)


def test_stale_preview_cannot_apply_after_another_shift() -> None:
    _, runtime = runtime_for_shift()
    first = runtime.create_shift_preview(
        request_for(runtime, request_id="first-preview")
    )
    second = runtime.create_shift_preview(
        request_for(runtime, request_id="second-preview", delta=2)
    )
    runtime.confirm_shift(first.preview_id, confirmation_id="confirm-first")
    runtime.apply_shift(first.preview_id)
    runtime.confirm_shift(second.preview_id, confirmation_id="confirm-second")

    with pytest.raises(StaleScheduleError):
        runtime.apply_shift(second.preview_id)
    assert runtime.shift_preview_status(second.preview_id) is (
        ShiftPreviewStatus.CONFIRMED
    )


def test_preview_becomes_stale_if_moved_task_completes_before_apply() -> None:
    scenario, runtime = runtime_for_shift()
    preview = runtime.create_shift_preview(request_for(runtime))
    runtime.record_completion(
        "root",
        completed_on=scenario.problem.start_date,
    )
    runtime.confirm_shift(preview.preview_id, confirmation_id="late-completion")

    with pytest.raises(StaleScheduleError, match="progress changed"):
        runtime.apply_shift(preview.preview_id)
    assert runtime.shift_preview_status(preview.preview_id) is (
        ShiftPreviewStatus.CONFIRMED
    )


def test_explicit_shift_uses_zero_cp_sat_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ortools.sat.python import cp_model
    scenario = prerequisite_dag()

    def forbidden_solve(*args: object, **kwargs: object) -> object:
        raise AssertionError("ordinary plan shift must not run CP-SAT")

    monkeypatch.setattr(cp_model.CpSolver, "Solve", forbidden_solve)
    runtime = PlanRuntime.from_solver_result(
        scenario.problem,
        scenario.solution,
        plan_id="no-cp-shift-plan",
    )
    preview = runtime.create_shift_preview(request_for(runtime))
    runtime.confirm_shift(preview.preview_id, confirmation_id="no-cp-confirm")
    runtime.apply_shift(preview.preview_id)
