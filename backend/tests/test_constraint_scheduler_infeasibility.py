from __future__ import annotations

from datetime import date

import pytest

from app.planning.scheduling import (
    CompletedTask,
    InfeasibilityCode,
    PlanScheduleProblem,
    PlanScheduleSolution,
    PrerequisiteEdge,
    ReviewWorkload,
    ScheduleInfeasibility,
    ScheduleStatus,
    SolverMetadata,
    SolverTerminationStatus,
    explain_obvious_infeasibility,
    validate_solution,
)
from constraint_scheduler_fixtures import (
    all_infeasible_scenarios,
    capacities,
    daily_capacity_impossible,
    deadline_impossible,
    prerequisite_cycle,
    task,
    task_allocation,
)


def _codes(problem: PlanScheduleProblem) -> set[InfeasibilityCode]:
    return {item.code for item in explain_obvious_infeasibility(problem)}


def test_deadline_impossible_reports_required_and_available_minutes() -> None:
    scenario = deadline_impossible()
    reasons = explain_obvious_infeasibility(scenario.problem)

    assert InfeasibilityCode.DEADLINE_CAPACITY_SHORTFALL in {
        item.code for item in reasons
    }
    shortfall = next(
        item
        for item in reasons
        if item.code is InfeasibilityCode.DEADLINE_CAPACITY_SHORTFALL
    )
    assert shortfall.required_minutes == 40
    assert shortfall.available_minutes == 10
    assert shortfall.message


def test_daily_capacity_impossible_is_distinct_from_total_capacity() -> None:
    scenario = daily_capacity_impossible()
    codes = _codes(scenario.problem)

    assert InfeasibilityCode.UNSPLITTABLE_TASK_EXCEEDS_DAILY_CAPACITY in codes
    assert InfeasibilityCode.DEADLINE_CAPACITY_SHORTFALL not in codes


def test_prerequisite_cycle_has_deterministic_task_evidence() -> None:
    scenario = prerequisite_cycle()
    reasons = explain_obvious_infeasibility(scenario.problem)
    cycle = next(
        item for item in reasons if item.code is InfeasibilityCode.PREREQUISITE_CYCLE
    )

    assert cycle.task_ids == ("cycle-a", "cycle-b")
    assert cycle.message == "Prerequisite edges contain a cycle."


def test_unavailable_window_with_no_capacity_is_explained() -> None:
    day = date(2026, 9, 1)
    problem = PlanScheduleProblem(
        problem_id="no-capacity",
        tasks=(task("remaining", 20, splittable=True),),
        start_date=day,
        deadline=day,
        daily_capacities=capacities(day, 60),
        unavailable_days=frozenset((day,)),
    )

    codes = _codes(problem)

    assert InfeasibilityCode.NO_AVAILABLE_CAPACITY in codes
    assert InfeasibilityCode.DEADLINE_CAPACITY_SHORTFALL in codes


def test_unsplittable_review_requires_one_day_with_sufficient_capacity() -> None:
    day = date(2026, 9, 1)
    problem = PlanScheduleProblem(
        problem_id="unsplittable-review-capacity",
        tasks=(task("source", 10),),
        start_date=day,
        deadline=date(2026, 9, 2),
        daily_capacities=capacities(day, 40, 40),
        review_workloads=(
            ReviewWorkload(
                workload_id="large-review",
                minutes=70,
                earliest_date=day,
                latest_date=date(2026, 9, 2),
                splittable=False,
            ),
        ),
    )

    assert InfeasibilityCode.RESERVATION_WINDOW_CAPACITY_SHORTFALL in _codes(
        problem
    )


def test_completed_dependent_cannot_leave_prerequisite_remaining() -> None:
    day = date(2026, 9, 1)
    dependent_allocation = task_allocation(
        "dependent",
        day,
        20,
        0,
        completed=True,
    )
    problem = PlanScheduleProblem(
        problem_id="completed-prerequisite-conflict",
        tasks=(task("prerequisite", 20), task("dependent", 20)),
        prerequisite_edges=(
            PrerequisiteEdge(
                prerequisite_task_id="prerequisite",
                dependent_task_id="dependent",
            ),
        ),
        start_date=day,
        deadline=day,
        daily_capacities=capacities(day, 60),
        completed_tasks=(
            CompletedTask(
                task_id="dependent",
                allocations=(dependent_allocation,),
            ),
        ),
    )

    assert InfeasibilityCode.COMPLETED_PREREQUISITE_CONFLICT in _codes(problem)


def test_infeasible_fixtures_are_well_formed_explanations() -> None:
    for scenario in all_infeasible_scenarios():
        result = validate_solution(scenario.problem, scenario.solution)

        assert scenario.solution.status is ScheduleStatus.INFEASIBLE
        assert scenario.solution.infeasibilities
        assert result.valid, result.violations
        assert all(item.message for item in scenario.solution.infeasibilities)


def test_not_solved_status_requires_dependency_unavailable_reason() -> None:
    scenario = deadline_impossible()
    metadata = SolverMetadata(
        solver_name="dependency_gate",
        solver_version="1",
        deterministic=True,
        dependency_name="ortools",
        dependency_available=False,
        termination_status=SolverTerminationStatus.DEPENDENCY_UNAVAILABLE,
    )

    solution = PlanScheduleSolution(
        solution_id="not-solved-without-ortools",
        problem_id=scenario.problem.problem_id,
        status=ScheduleStatus.NOT_SOLVED,
        infeasibilities=(
            ScheduleInfeasibility(
                code=InfeasibilityCode.SOLVER_DEPENDENCY_UNAVAILABLE,
                message="OR-Tools is not available in the provisioned environment.",
            ),
        ),
        utilization=0,
        solver_metadata=metadata,
    )

    assert solution.status is ScheduleStatus.NOT_SOLVED


def test_not_solved_status_supports_explicit_timeout_without_fake_proof() -> None:
    scenario = deadline_impossible()
    metadata = SolverMetadata(
        solver_name="cp-sat",
        solver_version="1",
        deterministic=True,
        dependency_name="ortools",
        dependency_available=True,
        termination_status=SolverTerminationStatus.UNKNOWN,
    )

    solution = PlanScheduleSolution(
        solution_id="cp-sat-timeout",
        problem_id=scenario.problem.problem_id,
        status=ScheduleStatus.NOT_SOLVED,
        infeasibilities=(
            ScheduleInfeasibility(
                code=InfeasibilityCode.SOLVER_TIMEOUT,
                message="CP-SAT reached the configured solve timeout.",
            ),
        ),
        utilization=0,
        solver_metadata=metadata,
    )

    assert solution.solver_metadata.termination_status is (
        SolverTerminationStatus.UNKNOWN
    )
    with pytest.raises(ValueError, match="explicit solver termination reason"):
        PlanScheduleSolution(
            solution_id="ambiguous-not-solved",
            problem_id=scenario.problem.problem_id,
            status=ScheduleStatus.NOT_SOLVED,
            infeasibilities=(
                ScheduleInfeasibility(
                    code=InfeasibilityCode.OTHER,
                    message="No unsupported proof claim is permitted.",
                ),
            ),
            utilization=0,
            solver_metadata=metadata,
        )


def test_validator_rejects_solver_status_metadata_contradiction() -> None:
    scenario = deadline_impossible()
    contradictory = scenario.solution.model_copy(
        update={
            "solver_metadata": scenario.solution.solver_metadata.model_copy(
                update={"termination_status": SolverTerminationStatus.OPTIMAL}
            )
        }
    )

    validation = validate_solution(scenario.problem, contradictory)

    assert not validation.valid
    assert any(
        item.code.value == "solver_status_mismatch"
        for item in validation.violations
    )
