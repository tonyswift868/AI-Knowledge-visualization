from __future__ import annotations

from datetime import timedelta
from time import perf_counter

import pytest

from app.planning.scheduling import (
    AllocationKind,
    ConstraintViolationCode,
    PlanScheduleProblem,
    PlanScheduleSolution,
    ScheduleSolveConfig,
    ScheduleValidationError,
    ScheduleValidator,
    run_schedule_benchmark,
)
from constraint_scheduler_fixtures import (
    all_feasible_scenarios,
    all_infeasible_scenarios,
    capacities,
    feasible_solution,
    high_load_synthetic,
    prerequisite_dag,
    simple_feasible,
    split_forbidden,
    task,
    task_allocation,
    weekend_unavailable,
)


def test_production_acceptance_corpus_covers_required_fixture_families() -> None:
    feasible_names = {item.name for item in all_feasible_scenarios()}
    infeasible_names = {item.name for item in all_infeasible_scenarios()}

    assert {
        "simple_feasible",
        "prerequisite_dag",
        "parallel_independent",
        "daily_capacity_deadline_edge",
        "weekend_unavailable",
        "review_workload",
        "final_buffer",
        "split_allowed",
        "split_forbidden",
        "completed_work_immutable",
        "interval_low",
        "interval_expected",
        "interval_high",
        "high_load_120",
    }.issubset(feasible_names)
    assert {
        "deadline_impossible",
        "daily_capacity_impossible",
        "prerequisite_cycle",
        "zero_capacity",
    }.issubset(infeasible_names)


@pytest.mark.parametrize(
    "scenario",
    all_feasible_scenarios(),
    ids=lambda scenario: scenario.name,
)
def test_schedule_validator_accepts_every_feasible_fixture(scenario: object) -> None:
    validator = ScheduleValidator()

    validation = validator.validate(
        scenario.problem,
        scenario.solution.model_dump(mode="json"),
    )

    assert validation.valid, validation.violations
    assert validator.require_valid(
        scenario.problem,
        scenario.solution,
    ) == scenario.solution


@pytest.mark.parametrize(
    "scenario",
    all_infeasible_scenarios(),
    ids=lambda scenario: scenario.name,
)
def test_schedule_validator_accepts_well_formed_infeasible_results(
    scenario: object,
) -> None:
    assert ScheduleValidator().validate(scenario.problem, scenario.solution).valid


def test_schedule_validator_supports_serialization_roundtrip() -> None:
    scenario = prerequisite_dag()
    payload = scenario.solution.model_dump_json()

    validation = ScheduleValidator().validate_json(scenario.problem, payload)

    assert validation.valid, validation.violations


def test_schedule_validator_rejects_malformed_solver_payload() -> None:
    scenario = simple_feasible()
    payload = scenario.solution.model_dump(mode="json")
    payload["allocations"][0]["minutes"] = -1
    validator = ScheduleValidator()

    validation = validator.validate(scenario.problem, payload)

    assert not validation.valid
    assert validation.violations[0].code is (
        ConstraintViolationCode.INVALID_SOLUTION_PAYLOAD
    )
    with pytest.raises(ScheduleValidationError):
        validator.require_valid(scenario.problem, payload)


def test_schedule_validator_revalidates_tampered_model_instances() -> None:
    scenario = simple_feasible()
    first, *remaining = scenario.solution.allocations
    tampered = scenario.solution.model_copy(
        update={
            "allocations": (
                first.model_copy(update={"minutes": -1}),
                *remaining,
            )
        }
    )

    validation = ScheduleValidator().validate(scenario.problem, tampered)

    assert not validation.valid
    assert validation.violations[0].code is (
        ConstraintViolationCode.INVALID_SOLUTION_PAYLOAD
    )


def test_schedule_validator_rejects_nondeterministic_output_order() -> None:
    scenario = simple_feasible()
    candidate = scenario.solution.model_copy(
        update={"allocations": tuple(reversed(scenario.solution.allocations))}
    )

    validation = ScheduleValidator().validate(scenario.problem, candidate)

    assert ConstraintViolationCode.NONDETERMINISTIC_OUTPUT in {
        item.code for item in validation.violations
    }


def test_schedule_validator_rejects_split_forbidden_task_fragmentation() -> None:
    scenario = split_forbidden()
    start = scenario.problem.start_date
    problem = scenario.problem.model_copy(
        update={
            "deadline": start + timedelta(days=1),
            "daily_capacities": capacities(start, 60, 60),
        }
    )
    allocations = (
        task_allocation(
            "single-block",
            start,
            30,
            0,
            chunk_index=1,
            chunk_count=2,
        ),
        task_allocation(
            "single-block",
            problem.deadline,
            30,
            0,
            chunk_index=2,
            chunk_count=2,
        ),
    )
    candidate = feasible_solution(
        problem,
        "invalid-split-forbidden-solution",
        allocations,
    )

    validation = ScheduleValidator().validate(problem, candidate)

    assert ConstraintViolationCode.SPLIT_POLICY in {
        item.code for item in validation.violations
    }


def test_schedule_validator_rejects_review_and_buffer_outside_windows() -> None:
    scenario = simple_feasible()
    moved = []
    for allocation in scenario.solution.allocations:
        if allocation.kind is AllocationKind.REVIEW:
            moved.append(
                allocation.model_copy(
                    update={"day": scenario.problem.start_date, "order_in_day": 1}
                )
            )
        elif allocation.kind is AllocationKind.FINAL_BUFFER:
            moved.append(
                allocation.model_copy(
                    update={
                        "day": scenario.problem.start_date,
                        "order_in_day": 2,
                    }
                )
            )
        else:
            moved.append(allocation)
    candidate = feasible_solution(
        scenario.problem,
        "invalid-reservation-windows",
        tuple(sorted(moved, key=lambda item: (item.day, item.order_in_day))),
    )

    validation = ScheduleValidator().validate(scenario.problem, candidate)

    assert ConstraintViolationCode.RESERVATION_MINUTES_MISMATCH in {
        item.code for item in validation.violations
    }


def test_schedule_validator_rejects_allocation_on_unavailable_day() -> None:
    scenario = weekend_unavailable()
    friday_allocation, monday_allocation = scenario.solution.allocations
    saturday = scenario.problem.start_date + timedelta(days=1)
    candidate = feasible_solution(
        scenario.problem,
        "invalid-unavailable-day",
        (
            friday_allocation,
            monday_allocation.model_copy(update={"day": saturday}),
        ),
    )

    validation = ScheduleValidator().validate(scenario.problem, candidate)

    assert ConstraintViolationCode.UNAVAILABLE_DAY in {
        item.code for item in validation.violations
    }


def test_problem_contract_rejects_invalid_task_graph() -> None:
    scenario = prerequisite_dag()
    edge = scenario.problem.prerequisite_edges[0].model_copy(
        update={"prerequisite_task_id": "unknown-task"}
    )

    with pytest.raises(ValueError, match="known tasks"):
        PlanScheduleProblem(
            problem_id="invalid-task-graph",
            tasks=(task("known", 20),),
            prerequisite_edges=(edge,),
            start_date=scenario.problem.start_date,
            deadline=scenario.problem.start_date,
            daily_capacities=capacities(scenario.problem.start_date, 60),
        )


def test_high_load_validator_benchmark_is_linear_scale() -> None:
    scenario = high_load_synthetic(120)
    started = perf_counter()

    validation = ScheduleValidator().validate(scenario.problem, scenario.solution)

    elapsed_seconds = perf_counter() - started
    assert validation.valid, validation.violations
    assert len(scenario.problem.tasks) == 120
    assert len(scenario.solution.allocations) == 120
    assert elapsed_seconds < 5.0


def test_solver_neutral_benchmark_harness_validates_high_load_output() -> None:
    scenario = high_load_synthetic(120)

    class FixtureSolver:
        def solve(
            self,
            problem: PlanScheduleProblem,
            *,
            config: ScheduleSolveConfig | None = None,
        ) -> PlanScheduleSolution:
            assert problem == scenario.problem
            assert config is not None
            return scenario.solution

    result = run_schedule_benchmark(
        FixtureSolver(),
        scenario.problem,
        config=ScheduleSolveConfig(max_time_seconds=5),
    )

    assert result.task_count == 120
    assert result.within_timeout
    assert result.solution == scenario.solution
    assert result.validation.valid
    assert result.model_validate_json(result.model_dump_json()) == result


def test_benchmark_harness_flags_timeout_overrun() -> None:
    scenario = simple_feasible()

    class FixtureSolver:
        def solve(
            self,
            problem: PlanScheduleProblem,
            *,
            config: ScheduleSolveConfig | None = None,
        ) -> PlanScheduleSolution:
            return scenario.solution

    result = run_schedule_benchmark(
        FixtureSolver(),
        scenario.problem,
        config=ScheduleSolveConfig(max_time_seconds=1e-9),
    )

    assert not result.within_timeout


def test_deterministic_solve_config_requires_one_worker() -> None:
    with pytest.raises(ValueError, match="num_search_workers=1"):
        ScheduleSolveConfig(deterministic=True, num_search_workers=2)
