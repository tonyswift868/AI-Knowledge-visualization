from __future__ import annotations

from datetime import date

from app.planning.scheduling import (
    InfeasibilityCode,
    PlanScheduleProblem,
    PlanScheduleSolution,
    ScheduleInfeasibility,
    ScheduleStatus,
    SolverMetadata,
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
