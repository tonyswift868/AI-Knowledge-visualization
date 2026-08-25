from __future__ import annotations

from datetime import timedelta

import pytest

from app.planning.runtime_v2 import (
    AUTO_CP_SAT_ON_MISS,
    AUTO_LLM_ON_MISS,
    AUTO_PROVIDER_ON_MISS,
    AUTO_REPLAN,
    AUTO_SHIFT_ON_MISS,
    DEFAULT_MUTABILITY,
    PlanRuntime,
    PlanRuntimeStatus,
    ScheduledTaskStatus,
)
from constraint_scheduler_fixtures import completed_replan, prerequisite_dag


def runtime_for_dag() -> tuple[object, PlanRuntime]:
    scenario = prerequisite_dag()
    runtime = PlanRuntime.from_solver_result(
        scenario.problem,
        scenario.solution,
        plan_id="runtime-dag",
    )
    return scenario, runtime


def status_by_task(snapshot: object) -> dict[str, ScheduledTaskStatus]:
    return {item.task_id: item.status for item in snapshot.tasks}


def test_product_policy_constants_are_frozen_to_no_automatic_mutation() -> None:
    assert DEFAULT_MUTABILITY == "IMMUTABLE"
    assert AUTO_REPLAN is False
    assert AUTO_CP_SAT_ON_MISS is False
    assert AUTO_LLM_ON_MISS is False
    assert AUTO_PROVIDER_ON_MISS is False
    assert AUTO_SHIFT_ON_MISS is False


def test_one_missed_day_becomes_overdue_without_moving_later_tasks() -> None:
    scenario, runtime = runtime_for_dag()
    original = runtime.current_schedule
    tuesday = scenario.problem.start_date
    wednesday = tuesday + timedelta(days=1)

    snapshot = runtime.snapshot(today=wednesday)
    statuses = status_by_task(snapshot)

    assert statuses["root"] is ScheduledTaskStatus.OVERDUE
    assert statuses["left"] is ScheduledTaskStatus.DUE_TODAY
    assert statuses["right"] is ScheduledTaskStatus.DUE_TODAY
    assert statuses["leaf"] is ScheduledTaskStatus.UPCOMING
    assert runtime.current_schedule == original
    assert runtime.current_schedule.allocations == scenario.solution.allocations


def test_multiple_overdue_and_due_today_coexist() -> None:
    scenario, runtime = runtime_for_dag()
    thursday = scenario.problem.start_date + timedelta(days=2)

    snapshot = runtime.snapshot(today=thursday)

    assert {item.task_id for item in snapshot.overdue} == {
        "root",
        "left",
        "right",
    }
    assert [item.task_id for item in snapshot.due_today] == ["leaf"]
    ledger = runtime.overdue_ledger(today=thursday)
    assert ledger.active_task_ids == ("root", "left", "right")
    assert ledger.active_minutes == 55


def test_deadline_expiration_keeps_tasks_and_overdue_ledger() -> None:
    scenario, runtime = runtime_for_dag()
    after_deadline = scenario.problem.deadline + timedelta(days=1)

    snapshot = runtime.snapshot(today=after_deadline)

    assert snapshot.status is PlanRuntimeStatus.EXPIRED_WITH_OVERDUE
    assert len(snapshot.tasks) == len(scenario.problem.tasks)
    assert all(
        item.status is ScheduledTaskStatus.OVERDUE for item in snapshot.tasks
    )
    assert len(runtime.overdue_ledger(today=after_deadline).active_entries) == 4


def test_expired_plan_accepts_late_completion_without_replanning() -> None:
    scenario, runtime = runtime_for_dag()
    immutable_schedule = runtime.current_schedule
    after_deadline = scenario.problem.deadline + timedelta(days=2)
    runtime.record_completion("root", completed_on=after_deadline)

    snapshot = runtime.snapshot(today=after_deadline)

    assert snapshot.status is PlanRuntimeStatus.EXPIRED_WITH_OVERDUE
    assert next(item for item in snapshot.tasks if item.task_id == "root").status is (
        ScheduledTaskStatus.COMPLETED
    )
    assert runtime.current_schedule == immutable_schedule


def test_overdue_task_completed_later_moves_to_resolved_ledger() -> None:
    scenario, runtime = runtime_for_dag()
    immutable_schedule = runtime.current_schedule
    completed_on = scenario.problem.start_date + timedelta(days=2)
    runtime.record_completion("root", completed_on=completed_on)

    snapshot = runtime.snapshot(today=completed_on)
    ledger = runtime.overdue_ledger(today=completed_on)

    root = next(item for item in snapshot.tasks if item.task_id == "root")
    assert root.status is ScheduledTaskStatus.COMPLETED
    assert root.completed_on == completed_on
    assert "root" not in ledger.active_task_ids
    resolved = next(item for item in ledger.resolved_entries if item.task_id == "root")
    assert resolved.resolved_on == completed_on
    assert resolved.days_overdue == 2
    assert runtime.current_schedule == immutable_schedule


def test_user_skip_is_explicit_terminal_state() -> None:
    scenario, runtime = runtime_for_dag()
    today = scenario.problem.start_date
    runtime.record_skip("root", skipped_on=today)

    snapshot = runtime.snapshot(today=today)

    root = next(item for item in snapshot.tasks if item.task_id == "root")
    assert root.status is ScheduledTaskStatus.SKIPPED_BY_USER
    assert root.skipped_on == today
    with pytest.raises(ValueError, match="skipped task"):
        runtime.record_completion("root", completed_on=today)


def test_plan_completed_when_every_task_is_completed_or_user_skipped() -> None:
    scenario, runtime = runtime_for_dag()
    today = scenario.problem.deadline + timedelta(days=3)
    for task in scenario.problem.tasks[:-1]:
        runtime.record_completion(task.task_id, completed_on=today)
    runtime.record_skip(scenario.problem.tasks[-1].task_id, skipped_on=today)

    snapshot = runtime.snapshot(today=today)

    assert snapshot.status is PlanRuntimeStatus.COMPLETED


def test_completed_history_from_solver_is_seeded_and_immutable() -> None:
    scenario = completed_replan()
    runtime = PlanRuntime.from_solver_result(
        scenario.new_problem,
        scenario.new_solution,
        plan_id="completed-history",
    )
    today = scenario.new_problem.start_date + timedelta(days=1)

    snapshot = runtime.snapshot(today=today)
    completed = next(item for item in snapshot.tasks if item.task_id == "completed")

    assert completed.status is ScheduledTaskStatus.COMPLETED
    assert runtime.current_schedule.allocations[0] == (
        scenario.new_problem.completed_tasks[0].allocations[0]
    )


def test_normal_progress_projection_never_calls_cp_sat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ortools.sat.python import cp_model
    scenario = prerequisite_dag()

    def forbidden_solve(*args: object, **kwargs: object) -> object:
        raise AssertionError("CP-SAT must not run on a normal missed day")

    monkeypatch.setattr(cp_model.CpSolver, "Solve", forbidden_solve)

    runtime = PlanRuntime.from_solver_result(
        scenario.problem,
        scenario.solution,
        plan_id="no-cp-runtime",
    )

    runtime.snapshot(today=scenario.problem.start_date + timedelta(days=1))
    runtime.overdue_ledger(
        today=scenario.problem.start_date + timedelta(days=2)
    )
