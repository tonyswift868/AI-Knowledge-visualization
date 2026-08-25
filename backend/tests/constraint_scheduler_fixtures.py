"""Deterministic solver-independent fixtures for the W2-07 contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.planning.effort import EffortEstimate, EffortFeatures
from app.planning.scheduling import (
    AllocationKind,
    CompletedTask,
    DailyCapacity,
    EffortIntervalPolicy,
    FinalBuffer,
    KnowledgeTask,
    PlanScheduleProblem,
    PlanScheduleSolution,
    PrerequisiteEdge,
    ReviewWorkload,
    ScheduleAllocation,
    ScheduleStatus,
    SolverMetadata,
    TaskSplitPolicy,
    build_daily_loads,
    calculate_utilization,
    explain_obvious_infeasibility,
)


@dataclass(frozen=True)
class FeasibleScenario:
    name: str
    problem: PlanScheduleProblem
    solution: PlanScheduleSolution


@dataclass(frozen=True)
class InfeasibleScenario:
    name: str
    problem: PlanScheduleProblem
    solution: PlanScheduleSolution


@dataclass(frozen=True)
class ReplanScenario:
    old_problem: PlanScheduleProblem
    old_solution: PlanScheduleSolution
    new_problem: PlanScheduleProblem
    new_solution: PlanScheduleSolution


def effort(
    estimate_id: str,
    expected: int,
    *,
    low: int | None = None,
    high: int | None = None,
) -> EffortEstimate:
    review = max(1, expected // 10)
    assessment = max(1, expected // 10)
    example = max(1, expected // 5)
    practice = max(1, expected // 4)
    understand = expected - review - assessment - example - practice
    if understand < 0:
        raise ValueError("fixture expected minutes are too small")
    return EffortEstimate(
        estimate_id=estimate_id,
        estimator_version="mentorflow.effort-rules.fixture-v1",
        understand_minutes=understand,
        example_minutes=example,
        practice_minutes=practice,
        assessment_minutes=assessment,
        review_minutes=review,
        low_minutes=low if low is not None else max(0, expected - 5),
        expected_minutes=expected,
        high_minutes=high if high is not None else expected + 5,
        confidence=0.6,
        basis=("contract_fixture=true", "calibration=untrained"),
        features=EffortFeatures(),
    )


def task(
    task_id: str,
    minutes: int,
    *,
    splittable: bool = False,
    low: int | None = None,
    high: int | None = None,
) -> KnowledgeTask:
    return KnowledgeTask(
        task_id=task_id,
        title=f"Fixture task {task_id}",
        effort=effort(f"fixture-{task_id}-{minutes}", minutes, low=low, high=high),
        split_policy=(
            TaskSplitPolicy.SPLITTABLE
            if splittable
            else TaskSplitPolicy.UNSPLITTABLE
        ),
        minimum_chunk_minutes=10,
        maximum_chunks=4 if splittable else 1,
        allow_final_short_chunk=splittable,
    )


def capacities(start: date, *minutes: int) -> tuple[DailyCapacity, ...]:
    return tuple(
        DailyCapacity(day=start + timedelta(days=index), available_minutes=value)
        for index, value in enumerate(minutes)
    )


def task_allocation(
    task_id: str,
    day: date,
    minutes: int,
    order: int,
    *,
    completed: bool = False,
    chunk_index: int = 1,
    chunk_count: int = 1,
) -> ScheduleAllocation:
    return ScheduleAllocation(
        allocation_id=(
            f"allocation:{task_id}:{day.isoformat()}:{chunk_index}:"
            f"{'completed' if completed else 'planned'}"
        ),
        kind=AllocationKind.TASK,
        task_id=task_id,
        day=day,
        minutes=minutes,
        order_in_day=order,
        chunk_index=chunk_index,
        chunk_count=chunk_count,
        completed=completed,
    )


def reservation_allocation(
    kind: AllocationKind,
    reservation_id: str,
    day: date,
    minutes: int,
    order: int,
) -> ScheduleAllocation:
    return ScheduleAllocation(
        allocation_id=f"allocation:{reservation_id}:{day.isoformat()}",
        kind=kind,
        reservation_id=reservation_id,
        day=day,
        minutes=minutes,
        order_in_day=order,
    )


def metadata() -> SolverMetadata:
    return SolverMetadata(
        solver_name="contract_fixture",
        solver_version="1",
        deterministic=True,
        random_seed=0,
        dependency_name="ortools",
        dependency_available=False,
        configuration=("manual_fixture=true", "calendar_search=false"),
    )


def feasible_solution(
    problem: PlanScheduleProblem,
    solution_id: str,
    allocations: tuple[ScheduleAllocation, ...],
) -> PlanScheduleSolution:
    return PlanScheduleSolution(
        solution_id=solution_id,
        problem_id=problem.problem_id,
        status=ScheduleStatus.FEASIBLE,
        allocations=allocations,
        daily_loads=build_daily_loads(problem, allocations),
        utilization=calculate_utilization(problem, allocations),
        solver_metadata=metadata(),
    )


def infeasible_solution(
    problem: PlanScheduleProblem,
    solution_id: str,
) -> PlanScheduleSolution:
    return PlanScheduleSolution(
        solution_id=solution_id,
        problem_id=problem.problem_id,
        status=ScheduleStatus.INFEASIBLE,
        infeasibilities=explain_obvious_infeasibility(problem),
        utilization=0.0,
        solver_metadata=metadata(),
    )


def simple_feasible() -> FeasibleScenario:
    start = date(2026, 9, 1)
    review = ReviewWorkload(
        workload_id="review-1",
        minutes=10,
        earliest_date=start + timedelta(days=1),
        latest_date=start + timedelta(days=2),
    )
    buffer = FinalBuffer(
        minutes=15,
        earliest_date=start + timedelta(days=2),
        latest_date=start + timedelta(days=2),
    )
    problem = PlanScheduleProblem(
        problem_id="simple-feasible",
        tasks=(task("a", 30), task("b", 25)),
        start_date=start,
        deadline=start + timedelta(days=2),
        daily_capacities=capacities(start, 60, 60, 60),
        review_workloads=(review,),
        final_buffer=buffer,
    )
    allocations = (
        task_allocation("a", start, 30, 0),
        task_allocation("b", start + timedelta(days=1), 25, 0),
        reservation_allocation(
            AllocationKind.REVIEW,
            review.workload_id,
            start + timedelta(days=1),
            10,
            1,
        ),
        reservation_allocation(
            AllocationKind.FINAL_BUFFER,
            buffer.buffer_id,
            start + timedelta(days=2),
            15,
            0,
        ),
    )
    return FeasibleScenario(
        "simple_feasible",
        problem,
        feasible_solution(problem, "simple-feasible-solution", allocations),
    )


def prerequisite_chain() -> FeasibleScenario:
    start = date(2026, 9, 1)
    problem = PlanScheduleProblem(
        problem_id="prerequisite-chain",
        tasks=(task("foundation", 20), task("application", 20)),
        prerequisite_edges=(
            PrerequisiteEdge(
                prerequisite_task_id="foundation",
                dependent_task_id="application",
            ),
        ),
        start_date=start,
        deadline=start + timedelta(days=1),
        daily_capacities=capacities(start, 60, 60),
    )
    allocations = (
        task_allocation("foundation", start, 20, 0),
        task_allocation("application", start, 20, 1),
    )
    return FeasibleScenario(
        "prerequisite_chain",
        problem,
        feasible_solution(problem, "prerequisite-chain-solution", allocations),
    )


def prerequisite_dag() -> FeasibleScenario:
    start = date(2026, 9, 1)
    problem = PlanScheduleProblem(
        problem_id="prerequisite-dag",
        tasks=(
            task("root", 15),
            task("left", 20),
            task("right", 20),
            task("leaf", 15),
        ),
        prerequisite_edges=(
            PrerequisiteEdge(
                prerequisite_task_id="root",
                dependent_task_id="left",
            ),
            PrerequisiteEdge(
                prerequisite_task_id="root",
                dependent_task_id="right",
            ),
            PrerequisiteEdge(
                prerequisite_task_id="left",
                dependent_task_id="leaf",
            ),
            PrerequisiteEdge(
                prerequisite_task_id="right",
                dependent_task_id="leaf",
            ),
        ),
        start_date=start,
        deadline=start + timedelta(days=2),
        daily_capacities=capacities(start, 60, 60, 60),
    )
    allocations = (
        task_allocation("root", start, 15, 0),
        task_allocation("left", start + timedelta(days=1), 20, 0),
        task_allocation("right", start + timedelta(days=1), 20, 1),
        task_allocation("leaf", start + timedelta(days=2), 15, 0),
    )
    return FeasibleScenario(
        "prerequisite_dag",
        problem,
        feasible_solution(problem, "prerequisite-dag-solution", allocations),
    )


def parallel_independent() -> FeasibleScenario:
    start = date(2026, 9, 1)
    problem = PlanScheduleProblem(
        problem_id="parallel-independent",
        tasks=(task("left", 20), task("right", 20)),
        start_date=start,
        deadline=start,
        daily_capacities=capacities(start, 60),
    )
    allocations = (
        task_allocation("left", start, 20, 0),
        task_allocation("right", start, 20, 1),
    )
    return FeasibleScenario(
        "parallel_independent",
        problem,
        feasible_solution(problem, "parallel-independent-solution", allocations),
    )


def daily_capacity_edge() -> FeasibleScenario:
    start = date(2026, 9, 1)
    problem = PlanScheduleProblem(
        problem_id="daily-capacity-edge",
        tasks=(task("capacity-a", 30), task("capacity-b", 30)),
        start_date=start,
        deadline=start,
        daily_capacities=capacities(start, 60),
    )
    allocations = (
        task_allocation("capacity-a", start, 30, 0),
        task_allocation("capacity-b", start, 30, 1),
    )
    return FeasibleScenario(
        "daily_capacity_deadline_edge",
        problem,
        feasible_solution(problem, "daily-capacity-edge-solution", allocations),
    )


def review_workload_feasible() -> FeasibleScenario:
    start = date(2026, 9, 1)
    review = ReviewWorkload(
        workload_id="review-only",
        minutes=20,
        earliest_date=start + timedelta(days=1),
        latest_date=start + timedelta(days=1),
        splittable=False,
    )
    problem = PlanScheduleProblem(
        problem_id="review-workload-feasible",
        tasks=(task("review-source", 30),),
        start_date=start,
        deadline=start + timedelta(days=1),
        daily_capacities=capacities(start, 60, 60),
        review_workloads=(review,),
    )
    allocations = (
        task_allocation("review-source", start, 30, 0),
        reservation_allocation(
            AllocationKind.REVIEW,
            review.workload_id,
            start + timedelta(days=1),
            review.minutes,
            0,
        ),
    )
    return FeasibleScenario(
        "review_workload",
        problem,
        feasible_solution(problem, "review-workload-solution", allocations),
    )


def final_buffer_feasible() -> FeasibleScenario:
    start = date(2026, 9, 1)
    buffer = FinalBuffer(
        minutes=20,
        earliest_date=start + timedelta(days=1),
        latest_date=start + timedelta(days=1),
    )
    problem = PlanScheduleProblem(
        problem_id="final-buffer-feasible",
        tasks=(task("buffer-source", 30),),
        start_date=start,
        deadline=start + timedelta(days=1),
        daily_capacities=capacities(start, 60, 60),
        final_buffer=buffer,
    )
    allocations = (
        task_allocation("buffer-source", start, 30, 0),
        reservation_allocation(
            AllocationKind.FINAL_BUFFER,
            buffer.buffer_id,
            start + timedelta(days=1),
            buffer.minutes,
            0,
        ),
    )
    return FeasibleScenario(
        "final_buffer",
        problem,
        feasible_solution(problem, "final-buffer-solution", allocations),
    )


def split_allowed() -> FeasibleScenario:
    start = date(2026, 9, 1)
    problem = PlanScheduleProblem(
        problem_id="split-allowed",
        tasks=(task("split-task", 90, splittable=True),),
        start_date=start,
        deadline=start + timedelta(days=1),
        daily_capacities=capacities(start, 60, 60),
    )
    allocations = (
        task_allocation(
            "split-task",
            start,
            50,
            0,
            chunk_index=1,
            chunk_count=2,
        ),
        task_allocation(
            "split-task",
            start + timedelta(days=1),
            40,
            0,
            chunk_index=2,
            chunk_count=2,
        ),
    )
    return FeasibleScenario(
        "split_allowed",
        problem,
        feasible_solution(problem, "split-allowed-solution", allocations),
    )


def split_forbidden() -> FeasibleScenario:
    start = date(2026, 9, 1)
    problem = PlanScheduleProblem(
        problem_id="split-forbidden",
        tasks=(task("single-block", 60),),
        start_date=start,
        deadline=start,
        daily_capacities=capacities(start, 60),
    )
    allocations = (task_allocation("single-block", start, 60, 0),)
    return FeasibleScenario(
        "split_forbidden",
        problem,
        feasible_solution(problem, "split-forbidden-solution", allocations),
    )


def weekend_unavailable() -> FeasibleScenario:
    friday = date(2026, 9, 4)
    saturday = friday + timedelta(days=1)
    sunday = friday + timedelta(days=2)
    monday = friday + timedelta(days=3)
    problem = PlanScheduleProblem(
        problem_id="weekend-unavailable",
        tasks=(task("friday-task", 30), task("monday-task", 30)),
        start_date=friday,
        deadline=monday,
        daily_capacities=capacities(friday, 60, 60, 60, 60),
        unavailable_days=frozenset((saturday, sunday)),
    )
    allocations = (
        task_allocation("friday-task", friday, 30, 0),
        task_allocation("monday-task", monday, 30, 0),
    )
    return FeasibleScenario(
        "weekend_unavailable",
        problem,
        feasible_solution(problem, "weekend-unavailable-solution", allocations),
    )


def interval_scenarios() -> tuple[FeasibleScenario, ...]:
    start = date(2026, 9, 1)
    values = (
        (EffortIntervalPolicy.LOW, 20),
        (EffortIntervalPolicy.EXPECTED, 30),
        (EffortIntervalPolicy.HIGH, 40),
    )
    scenarios: list[FeasibleScenario] = []
    for policy, minutes in values:
        problem = PlanScheduleProblem(
            problem_id=f"interval-{policy.value}",
            tasks=(task("interval-task", 30, low=20, high=40),),
            start_date=start,
            deadline=start,
            daily_capacities=capacities(start, 60),
            effort_interval_policy=policy,
        )
        allocations = (task_allocation("interval-task", start, minutes, 0),)
        scenarios.append(
            FeasibleScenario(
                f"interval_{policy.value}",
                problem,
                feasible_solution(
                    problem,
                    f"interval-{policy.value}-solution",
                    allocations,
                ),
            )
        )
    return tuple(scenarios)


def deadline_impossible() -> InfeasibleScenario:
    start = date(2026, 9, 1)
    problem = PlanScheduleProblem(
        problem_id="deadline-impossible",
        tasks=(task("too-large", 40),),
        start_date=start,
        deadline=start,
        daily_capacities=capacities(start, 10),
    )
    return InfeasibleScenario(
        "deadline_impossible",
        problem,
        infeasible_solution(problem, "deadline-impossible-solution"),
    )


def daily_capacity_impossible() -> InfeasibleScenario:
    start = date(2026, 9, 1)
    problem = PlanScheduleProblem(
        problem_id="daily-capacity-impossible",
        tasks=(task("unsplittable-90", 90),),
        start_date=start,
        deadline=start + timedelta(days=2),
        daily_capacities=capacities(start, 40, 40, 40),
    )
    return InfeasibleScenario(
        "daily_capacity_impossible",
        problem,
        infeasible_solution(problem, "daily-capacity-impossible-solution"),
    )


def prerequisite_cycle() -> InfeasibleScenario:
    start = date(2026, 9, 1)
    problem = PlanScheduleProblem(
        problem_id="prerequisite-cycle",
        tasks=(task("cycle-a", 20), task("cycle-b", 20)),
        prerequisite_edges=(
            PrerequisiteEdge(
                prerequisite_task_id="cycle-a",
                dependent_task_id="cycle-b",
            ),
            PrerequisiteEdge(
                prerequisite_task_id="cycle-b",
                dependent_task_id="cycle-a",
            ),
        ),
        start_date=start,
        deadline=start + timedelta(days=1),
        daily_capacities=capacities(start, 60, 60),
    )
    return InfeasibleScenario(
        "prerequisite_cycle",
        problem,
        infeasible_solution(problem, "prerequisite-cycle-solution"),
    )


def zero_capacity_impossible() -> InfeasibleScenario:
    start = date(2026, 9, 1)
    problem = PlanScheduleProblem(
        problem_id="zero-capacity-impossible",
        tasks=(task("no-room", 20, splittable=True),),
        start_date=start,
        deadline=start + timedelta(days=1),
        daily_capacities=capacities(start, 0, 0),
    )
    return InfeasibleScenario(
        "zero_capacity",
        problem,
        infeasible_solution(problem, "zero-capacity-solution"),
    )


def completed_replan() -> ReplanScenario:
    start = date(2026, 9, 1)
    completed_allocation = task_allocation(
        "completed",
        start,
        20,
        0,
        completed=True,
    )
    common = {
        "tasks": (task("completed", 20), task("remaining", 30)),
        "prerequisite_edges": (
            PrerequisiteEdge(
                prerequisite_task_id="completed",
                dependent_task_id="remaining",
            ),
        ),
        "start_date": start,
        "deadline": start + timedelta(days=2),
        "daily_capacities": capacities(start, 60, 60, 60),
        "completed_tasks": (
            CompletedTask(
                task_id="completed",
                allocations=(completed_allocation,),
            ),
        ),
    }
    old_problem = PlanScheduleProblem(problem_id="replan-old", **common)
    new_problem = PlanScheduleProblem(problem_id="replan-new", **common)
    old_allocations = (
        completed_allocation,
        task_allocation("remaining", start + timedelta(days=1), 30, 0),
    )
    new_allocations = (
        completed_allocation,
        task_allocation("remaining", start + timedelta(days=2), 30, 0),
    )
    return ReplanScenario(
        old_problem=old_problem,
        old_solution=feasible_solution(
            old_problem,
            "replan-old-solution",
            old_allocations,
        ),
        new_problem=new_problem,
        new_solution=feasible_solution(
            new_problem,
            "replan-new-solution",
            new_allocations,
        ),
    )


def completed_history_feasible() -> FeasibleScenario:
    scenario = completed_replan()
    return FeasibleScenario(
        "completed_work_immutable",
        scenario.new_problem,
        scenario.new_solution,
    )


def high_load_synthetic(task_count: int = 120) -> FeasibleScenario:
    if task_count < 100:
        raise ValueError("high-load fixture requires at least 100 tasks")
    start = date(2026, 9, 1)
    tasks_per_day = 10
    day_count = (task_count + tasks_per_day - 1) // tasks_per_day
    fixture_tasks = tuple(
        task(f"synthetic-{index:03d}", 10) for index in range(task_count)
    )
    problem = PlanScheduleProblem(
        problem_id=f"high-load-{task_count}",
        tasks=fixture_tasks,
        start_date=start,
        deadline=start + timedelta(days=day_count - 1),
        daily_capacities=capacities(start, *(100 for _ in range(day_count))),
    )
    allocations = tuple(
        task_allocation(
            item.task_id,
            start + timedelta(days=index // tasks_per_day),
            10,
            index % tasks_per_day,
        )
        for index, item in enumerate(fixture_tasks)
    )
    return FeasibleScenario(
        f"high_load_{task_count}",
        problem,
        feasible_solution(problem, f"high-load-{task_count}-solution", allocations),
    )


def all_feasible_scenarios() -> tuple[FeasibleScenario, ...]:
    return (
        simple_feasible(),
        prerequisite_chain(),
        prerequisite_dag(),
        parallel_independent(),
        daily_capacity_edge(),
        weekend_unavailable(),
        review_workload_feasible(),
        final_buffer_feasible(),
        split_allowed(),
        split_forbidden(),
        completed_history_feasible(),
        *interval_scenarios(),
        high_load_synthetic(),
    )


def all_infeasible_scenarios() -> tuple[InfeasibleScenario, ...]:
    return (
        deadline_impossible(),
        daily_capacity_impossible(),
        prerequisite_cycle(),
        zero_capacity_impossible(),
    )
