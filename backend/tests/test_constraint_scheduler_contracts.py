from __future__ import annotations

import ast
import inspect
from datetime import timedelta

import pytest

from app.planning.scheduling import (
    DEPENDENCY_CHANGE_REQUIRED,
    ORTOOLS_AVAILABLE,
    AllocationKind,
    ConstraintViolationCode,
    DailyCapacity,
    KnowledgeTask,
    PlanScheduleProblem,
    PrerequisiteEdge,
    ScheduleStatus,
    TaskSplitPolicy,
    build_daily_loads,
    calculate_utilization,
    dependency_unavailable_solution,
    ortools_dependency_status,
    validate_solution,
)
from app.planning.scheduling import (
    availability,
    benchmark,
    contracts,
    replan,
    validation,
    validator,
)
from constraint_scheduler_fixtures import (
    all_feasible_scenarios,
    capacities,
    feasible_solution,
    prerequisite_chain,
    simple_feasible,
    task,
)


def _import_names(module: object) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_dependency_status_requires_ortools_without_importing_or_installing_it() -> None:
    status = ortools_dependency_status()
    imported = set().union(
        *(
            _import_names(module)
            for module in (
                availability,
                benchmark,
                contracts,
                validation,
                validator,
                replan,
            )
        )
    )

    assert status.available is ORTOOLS_AVAILABLE
    assert DEPENDENCY_CHANGE_REQUIRED == (
        None if ORTOOLS_AVAILABLE else "ortools"
    )
    assert status.dependency_change_required == DEPENDENCY_CHANGE_REQUIRED
    assert (status.version is not None) is ORTOOLS_AVAILABLE
    assert "ortools" not in imported
    assert not hasattr(availability, "ORToolsScheduleSolver")


def test_dependency_blocker_returns_explicit_valid_non_solution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = simple_feasible()
    monkeypatch.setattr(availability, "ORTOOLS_AVAILABLE", False)

    solution = dependency_unavailable_solution(scenario.problem)
    result = validate_solution(scenario.problem, solution)

    assert solution.status is ScheduleStatus.NOT_SOLVED
    assert solution.allocations == ()
    assert solution.solver_metadata.termination_status.value == (
        "dependency_unavailable"
    )
    assert result.valid, result.violations


@pytest.mark.parametrize(
    "scenario",
    all_feasible_scenarios(),
    ids=lambda scenario: scenario.name,
)
def test_feasible_contract_fixtures_pass_every_constraint(scenario: object) -> None:
    result = validate_solution(scenario.problem, scenario.solution)

    assert scenario.solution.status is ScheduleStatus.FEASIBLE
    assert result.valid, result.violations
    assert all(
        load.expected_daily_minutes <= load.capacity_minutes
        for load in scenario.solution.daily_loads
    )
    assert scenario.solution.solver_metadata.deterministic is True
    assert scenario.solution.solver_metadata.random_seed == 0


def test_simple_fixture_reserves_review_and_final_buffer_capacity() -> None:
    scenario = simple_feasible()

    assert sum(
        item.minutes
        for item in scenario.solution.allocations
        if item.kind is AllocationKind.REVIEW
    ) == 10
    assert sum(
        item.minutes
        for item in scenario.solution.allocations
        if item.kind is AllocationKind.FINAL_BUFFER
    ) == 15
    assert scenario.solution.utilization == pytest.approx(80 / 180)


def test_prerequisite_order_is_a_validated_constraint() -> None:
    scenario = prerequisite_chain()
    first, second = scenario.solution.allocations
    reversed_allocations = (
        first.model_copy(update={"order_in_day": 1}),
        second.model_copy(update={"order_in_day": 0}),
    )
    invalid = feasible_solution(
        scenario.problem,
        "reversed-prerequisite",
        reversed_allocations,
    )

    result = validate_solution(scenario.problem, invalid)

    assert not result.valid
    assert ConstraintViolationCode.PREREQUISITE_ORDER in {
        item.code for item in result.violations
    }


def test_candidate_daily_capacity_overflow_is_rejected() -> None:
    scenario = simple_feasible()
    start = scenario.problem.start_date
    constrained_problem = scenario.problem.model_copy(
        update={
            "problem_id": "simple-capacity-overflow",
            "daily_capacities": capacities(start, 60, 30, 60),
        }
    )
    invalid = feasible_solution(
        constrained_problem,
        "simple-capacity-overflow-solution",
        scenario.solution.allocations,
    )

    result = validate_solution(constrained_problem, invalid)

    assert not result.valid
    assert ConstraintViolationCode.DAILY_CAPACITY_EXCEEDED in {
        item.code for item in result.violations
    }


def test_candidate_cannot_allocate_on_day_without_declared_capacity() -> None:
    scenario = simple_feasible()
    allocation = scenario.solution.allocations[0]
    undeclared_day = scenario.problem.start_date + timedelta(days=1)
    sparse_problem = scenario.problem.model_copy(
        update={
            "problem_id": "sparse-capacity",
            "daily_capacities": (
                scenario.problem.daily_capacities[0],
                scenario.problem.daily_capacities[2],
            ),
            "review_workloads": (),
            "final_buffer": None,
        }
    )
    moved = allocation.model_copy(update={"day": undeclared_day})
    invalid = feasible_solution(
        sparse_problem,
        "sparse-capacity-solution",
        (moved, scenario.solution.allocations[1]),
    )

    result = validate_solution(sparse_problem, invalid)

    assert ConstraintViolationCode.MISSING_DAILY_CAPACITY in {
        item.code for item in result.violations
    }


def test_problem_contract_rejects_unknown_prerequisite_and_duplicate_day() -> None:
    start = simple_feasible().problem.start_date
    with pytest.raises(ValueError, match="known tasks"):
        PlanScheduleProblem(
            problem_id="unknown-edge",
            tasks=(task("known", 20),),
            prerequisite_edges=(
                PrerequisiteEdge(
                    prerequisite_task_id="missing",
                    dependent_task_id="known",
                ),
            ),
            start_date=start,
            deadline=start,
            daily_capacities=(DailyCapacity(day=start, available_minutes=60),),
        )
    with pytest.raises(ValueError, match="capacity dates"):
        PlanScheduleProblem(
            problem_id="duplicate-capacity",
            tasks=(task("known", 20),),
            start_date=start,
            deadline=start,
            daily_capacities=(
                DailyCapacity(day=start, available_minutes=60),
                DailyCapacity(day=start, available_minutes=30),
            ),
        )


def test_task_split_policy_is_explicit_and_internally_consistent() -> None:
    fixture_effort = task("source", 20).effort

    with pytest.raises(ValueError, match="only to splittable"):
        KnowledgeTask(
            task_id="invalid-unsplittable",
            title="Invalid",
            effort=fixture_effort,
            split_policy=TaskSplitPolicy.UNSPLITTABLE,
            allow_final_short_chunk=True,
        )


def test_daily_load_and_utilization_are_derived_not_solver_decisions() -> None:
    scenario = simple_feasible()

    assert build_daily_loads(
        scenario.problem, scenario.solution.allocations
    ) == scenario.solution.daily_loads
    assert calculate_utilization(
        scenario.problem, scenario.solution.allocations
    ) == scenario.solution.utilization
