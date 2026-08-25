from __future__ import annotations

from datetime import timedelta

import pytest

from app.planning.scheduling import availability
from app.planning.scheduling import (
    DeadlineImpact,
    build_replan_delta,
    dependency_unavailable_solution,
    validate_solution,
)
from constraint_scheduler_fixtures import completed_replan


def test_replan_moves_remaining_work_and_preserves_completed_task() -> None:
    scenario = completed_replan()
    delta = build_replan_delta(
        scenario.old_problem,
        scenario.old_solution,
        scenario.new_problem,
        scenario.new_solution,
        cause="learner unavailable on the previous remaining-task day",
    )

    assert delta.completed_task_ids == ("completed",)
    assert delta.remaining_task_ids == ("remaining",)
    assert [item.task_id for item in delta.moved_tasks] == ["remaining"]
    assert delta.moved_tasks[0].old_dates != delta.moved_tasks[0].new_dates
    assert delta.changed_daily_load
    assert delta.cause.startswith("learner unavailable")
    assert delta.deadline_impact is DeadlineImpact.UNCHANGED
    assert delta.deadline_days_delta == 0


def test_replan_rejects_any_change_to_completed_allocations() -> None:
    scenario = completed_replan()
    completed, remaining = scenario.new_solution.allocations
    moved_completed = completed.model_copy(
        update={
            "allocation_id": "allocation:completed:moved",
            "day": completed.day + timedelta(days=1),
            "order_in_day": 1,
        }
    )
    invalid_new = scenario.new_solution.model_copy(
        update={"allocations": (moved_completed, remaining)}
    )

    assert not validate_solution(scenario.new_problem, invalid_new).valid
    with pytest.raises(ValueError, match="new schedule is not valid"):
        build_replan_delta(
            scenario.old_problem,
            scenario.old_solution,
            scenario.new_problem,
            invalid_new,
            cause="invalid completed-task rewrite",
        )


def test_replan_rejects_previously_completed_task_becoming_remaining() -> None:
    scenario = completed_replan()
    regressed_problem = scenario.new_problem.model_copy(
        update={
            "problem_id": "replan-completion-regressed",
            "completed_tasks": (),
        }
    )
    completed, remaining = scenario.new_solution.allocations
    regressed_solution = scenario.new_solution.model_copy(
        update={
            "solution_id": "replan-completion-regressed-solution",
            "problem_id": regressed_problem.problem_id,
            "allocations": (
                completed.model_copy(update={"completed": False}),
                remaining,
            ),
        }
    )

    assert validate_solution(regressed_problem, regressed_solution).valid
    with pytest.raises(ValueError, match="cannot become remaining"):
        build_replan_delta(
            scenario.old_problem,
            scenario.old_solution,
            regressed_problem,
            regressed_solution,
            cause="invalid completion regression",
        )


def test_replan_accepts_newly_completed_work_and_keeps_it_out_of_remaining() -> None:
    scenario = completed_replan()
    old_problem = scenario.old_problem.model_copy(
        update={
            "problem_id": "replan-before-completion",
            "completed_tasks": (),
        }
    )
    completed, remaining = scenario.old_solution.allocations
    old_solution = scenario.old_solution.model_copy(
        update={
            "solution_id": "replan-before-completion-solution",
            "problem_id": old_problem.problem_id,
            "allocations": (
                completed.model_copy(update={"completed": False}),
                remaining,
            ),
        }
    )

    assert validate_solution(old_problem, old_solution).valid
    delta = build_replan_delta(
        old_problem,
        old_solution,
        scenario.new_problem,
        scenario.new_solution,
        cause="learner completed the first task",
    )

    assert delta.completed_task_ids == ("completed",)
    assert delta.remaining_task_ids == ("remaining",)


def test_replan_delta_records_deadline_extension() -> None:
    scenario = completed_replan()
    extended_problem = scenario.new_problem.model_copy(
        update={
            "problem_id": "replan-extended",
            "deadline": scenario.new_problem.deadline + timedelta(days=1),
        }
    )
    extended_solution = scenario.new_solution.model_copy(
        update={
            "problem_id": extended_problem.problem_id,
            "solution_id": "replan-extended-solution",
        }
    )
    delta = build_replan_delta(
        scenario.old_problem,
        scenario.old_solution,
        extended_problem,
        extended_solution,
        cause="deadline extended by one day",
    )

    assert delta.deadline_impact is DeadlineImpact.EXTENDED
    assert delta.deadline_days_delta == 1


def test_replan_reports_at_risk_when_dependency_blocks_new_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = completed_replan()
    monkeypatch.setattr(availability, "ORTOOLS_AVAILABLE", False)
    blocked_solution = dependency_unavailable_solution(scenario.new_problem)

    delta = build_replan_delta(
        scenario.old_problem,
        scenario.old_solution,
        scenario.new_problem,
        blocked_solution,
        cause="OR-Tools dependency unavailable",
    )

    assert delta.completed_task_ids == ("completed",)
    assert delta.remaining_task_ids == ("remaining",)
    assert delta.moved_tasks[0].new_dates == ()
    assert delta.deadline_impact is DeadlineImpact.AT_RISK
