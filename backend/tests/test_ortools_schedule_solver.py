from __future__ import annotations

from datetime import date, timedelta

import pytest

ortools = pytest.importorskip("ortools")
pytest.importorskip("ortools.sat.python.cp_model")

from app.planning.scheduling import (
    CompletedTask,
    InfeasibilityCode,
    ORTOOLS_AVAILABLE,
    ORToolsScheduleSolver,
    PlanScheduleProblem,
    PlanScheduleSolution,
    PrerequisiteEdge,
    ReviewWorkload,
    ScheduleSolveConfig,
    ScheduleStatus,
    ScheduleValidator,
    SolverTerminationStatus,
    run_schedule_benchmark,
)
from constraint_scheduler_fixtures import (
    all_feasible_scenarios,
    all_infeasible_scenarios,
    capacities,
    completed_replan,
    high_load_synthetic,
    simple_feasible,
    task,
    task_allocation,
)


STANDARD_FEASIBLE_SCENARIOS = tuple(
    scenario
    for scenario in all_feasible_scenarios()
    if not scenario.name.startswith("high_load_")
)


@pytest.mark.parametrize(
    "scenario",
    STANDARD_FEASIBLE_SCENARIOS,
    ids=lambda scenario: scenario.name,
)
def test_cp_sat_solves_every_standard_feasible_fixture(scenario: object) -> None:
    solution = ORToolsScheduleSolver().solve(
        scenario.problem,
        config=ScheduleSolveConfig(max_time_seconds=5),
    )
    validation = ScheduleValidator().validate(scenario.problem, solution)

    assert solution.status is ScheduleStatus.FEASIBLE
    assert solution.solver_metadata.termination_status in {
        SolverTerminationStatus.OPTIMAL,
        SolverTerminationStatus.FEASIBLE,
    }
    assert solution.solver_metadata.dependency_version == ortools.__version__
    assert validation.valid, validation.violations
    assert all(
        load.expected_daily_minutes <= load.capacity_minutes
        for load in solution.daily_loads
    )


@pytest.mark.parametrize(
    "scenario",
    all_infeasible_scenarios(),
    ids=lambda scenario: scenario.name,
)
def test_cp_sat_returns_deterministic_precheck_reasons(scenario: object) -> None:
    solution = ORToolsScheduleSolver().solve(scenario.problem)

    assert solution.status is ScheduleStatus.INFEASIBLE
    assert solution.solver_metadata.termination_status is (
        SolverTerminationStatus.NOT_RUN
    )
    assert "deterministic_precheck=true" in (
        solution.solver_metadata.configuration
    )
    assert ScheduleValidator().validate(
        scenario.problem,
        solution,
    ).valid


def test_cp_sat_proves_infeasible_when_prechecks_are_insufficient() -> None:
    start = date(2026, 9, 1)
    problem = PlanScheduleProblem(
        problem_id="cp-only-infeasible",
        tasks=(task("small-task", 10),),
        start_date=start,
        deadline=start + timedelta(days=1),
        daily_capacities=capacities(start, 60, 60),
        review_workloads=(
            ReviewWorkload(
                workload_id="review-a",
                minutes=40,
                earliest_date=start,
                latest_date=start,
                splittable=False,
            ),
            ReviewWorkload(
                workload_id="review-b",
                minutes=40,
                earliest_date=start,
                latest_date=start,
                splittable=False,
            ),
        ),
    )

    solution = ORToolsScheduleSolver().solve(problem)

    assert solution.status is ScheduleStatus.INFEASIBLE
    assert solution.solver_metadata.termination_status is (
        SolverTerminationStatus.INFEASIBLE
    )
    assert solution.infeasibilities[0].code is InfeasibilityCode.OTHER
    assert "proved" in solution.infeasibilities[0].message
    assert ScheduleValidator().validate(problem, solution).valid


def test_cp_sat_is_deterministic_for_identical_problem_and_config() -> None:
    scenario = simple_feasible()
    config = ScheduleSolveConfig(max_time_seconds=5, random_seed=17)
    solver = ORToolsScheduleSolver()

    first = solver.solve(scenario.problem, config=config)
    second = solver.solve(scenario.problem, config=config)

    assert first.solution_id == second.solution_id
    assert first.allocations == second.allocations
    assert first.daily_loads == second.daily_loads


def test_cp_sat_objective_balances_daily_load_before_other_tiebreakers() -> None:
    start = date(2026, 9, 1)
    problem = PlanScheduleProblem(
        problem_id="objective-load-balance",
        tasks=(task("balance-a", 30), task("balance-b", 30)),
        start_date=start,
        deadline=start + timedelta(days=1),
        daily_capacities=capacities(start, 60, 60),
    )

    solution = ORToolsScheduleSolver().solve(problem)

    assert [item.expected_daily_minutes for item in solution.daily_loads] == [
        30,
        30,
    ]


def test_cp_sat_objective_uses_earliest_finish_as_balance_tiebreaker() -> None:
    start = date(2026, 9, 1)
    problem = PlanScheduleProblem(
        problem_id="objective-deadline-slack",
        tasks=(task("finish-early", 30),),
        start_date=start,
        deadline=start + timedelta(days=1),
        daily_capacities=capacities(start, 60, 60),
    )

    solution = ORToolsScheduleSolver().solve(problem)

    assert solution.allocations[0].day == start


def test_cp_sat_replan_preserves_completed_history_and_moves_only_remaining() -> None:
    scenario = completed_replan()
    unavailable_day = scenario.old_problem.start_date + timedelta(days=1)
    new_problem = scenario.new_problem.model_copy(
        update={
            "problem_id": "cp-sat-replan-new",
            "unavailable_days": frozenset((unavailable_day,)),
        }
    )

    solution, delta = ORToolsScheduleSolver().replan(
        scenario.old_problem,
        scenario.old_solution,
        new_problem,
        cause="learner became unavailable",
    )

    assert solution.status is ScheduleStatus.FEASIBLE
    completed = scenario.new_problem.completed_tasks[0].allocations[0]
    assert completed in solution.allocations
    assert delta.completed_task_ids == ("completed",)
    assert [item.task_id for item in delta.moved_tasks] == ["remaining"]
    assert delta.moved_tasks[0].new_dates == (
        scenario.old_problem.start_date + timedelta(days=2),
    )


def test_cp_sat_reuses_free_order_slots_beside_unrelated_completed_work() -> None:
    start = date(2026, 9, 1)
    completed_allocation = task_allocation(
        "completed",
        start,
        20,
        10_000,
        completed=True,
    )
    problem = PlanScheduleProblem(
        problem_id="completed-order-gap",
        tasks=(task("completed", 20), task("remaining", 20)),
        start_date=start,
        deadline=start,
        daily_capacities=capacities(start, 60),
        completed_tasks=(
            CompletedTask(
                task_id="completed",
                allocations=(completed_allocation,),
            ),
        ),
    )

    solution = ORToolsScheduleSolver().solve(problem)

    remaining = next(
        item for item in solution.allocations if item.task_id == "remaining"
    )
    assert remaining.order_in_day == 0
    assert completed_allocation in solution.allocations


def test_cp_sat_moves_dependent_after_completed_prerequisite_at_last_order() -> None:
    start = date(2026, 9, 1)
    completed_allocation = task_allocation(
        "completed",
        start,
        20,
        10_000,
        completed=True,
    )
    problem = PlanScheduleProblem(
        problem_id="completed-last-order-prerequisite",
        tasks=(task("completed", 20), task("dependent", 20)),
        prerequisite_edges=(
            PrerequisiteEdge(
                prerequisite_task_id="completed",
                dependent_task_id="dependent",
            ),
        ),
        start_date=start,
        deadline=start + timedelta(days=1),
        daily_capacities=capacities(start, 60, 60),
        completed_tasks=(
            CompletedTask(
                task_id="completed",
                allocations=(completed_allocation,),
            ),
        ),
    )

    solution = ORToolsScheduleSolver().solve(problem)

    dependent = next(
        item for item in solution.allocations if item.task_id == "dependent"
    )
    assert dependent.day == start + timedelta(days=1)


def test_cp_sat_high_load_120_task_benchmark() -> None:
    scenario = high_load_synthetic(120)

    result = run_schedule_benchmark(
        ORToolsScheduleSolver(),
        scenario.problem,
        config=ScheduleSolveConfig(max_time_seconds=10),
    )

    assert result.task_count == 120
    assert result.solution_status is ScheduleStatus.FEASIBLE
    assert result.validation.valid, result.validation.violations
    assert result.within_timeout
    assert len(result.solution.allocations) == 120


def test_cp_sat_tiny_timeout_returns_explicit_non_solution() -> None:
    scenario = high_load_synthetic(120)

    solution = ORToolsScheduleSolver().solve(
        scenario.problem,
        config=ScheduleSolveConfig(max_time_seconds=1e-9),
    )

    assert solution.status is ScheduleStatus.NOT_SOLVED
    assert solution.solver_metadata.termination_status is (
        SolverTerminationStatus.UNKNOWN
    )
    assert solution.infeasibilities[0].code in {
        InfeasibilityCode.SOLVER_TIMEOUT,
        InfeasibilityCode.SOLVER_STATUS_UNKNOWN,
    }
    assert ScheduleValidator().validate(
        scenario.problem,
        solution,
    ).valid


def test_cp_sat_solution_serialization_roundtrip() -> None:
    scenario = simple_feasible()
    solution = ORToolsScheduleSolver().solve(scenario.problem)

    restored = PlanScheduleSolution.model_validate_json(solution.model_dump_json())

    assert restored == solution
    assert ScheduleValidator().validate(scenario.problem, restored).valid


def test_solver_requires_deterministic_config_for_deterministic_problem() -> None:
    scenario = simple_feasible()

    with pytest.raises(ValueError, match="deterministic solve config"):
        ORToolsScheduleSolver().solve(
            scenario.problem,
            config=ScheduleSolveConfig(
                deterministic=False,
                num_search_workers=2,
            ),
        )


def test_runtime_dependency_capability_is_real_cp_sat() -> None:
    assert ORTOOLS_AVAILABLE is True
    assert ORToolsScheduleSolver is not None
