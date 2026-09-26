"""Necessary-condition analysis and solution verification.

Nothing in this module assigns a task to a day.  The infeasibility analysis is
limited to deterministic necessary conditions, while ``validate_solution``
audits allocations produced by a future constraint solver or a test fixture.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterable, Mapping, Sequence

from .contracts import (
    AllocationKind,
    ConstraintViolation,
    ConstraintViolationCode,
    DailyLoad,
    InfeasibilityCode,
    PlanScheduleProblem,
    PlanScheduleSolution,
    ScheduleAllocation,
    ScheduleInfeasibility,
    ScheduleStatus,
    SolverTerminationStatus,
    SolutionValidation,
    TaskSplitPolicy,
)


def _available_capacity(
    problem: PlanScheduleProblem,
    *,
    earliest: date | None = None,
    latest: date | None = None,
) -> int:
    start = earliest or problem.start_date
    end = latest or problem.deadline
    return sum(
        minutes
        for day, minutes in problem.capacity_by_day.items()
        if start <= day <= end
    )


def _cycle_nodes(problem: PlanScheduleProblem) -> tuple[str, ...]:
    task_ids = {item.task_id for item in problem.tasks}
    indegree = {item: 0 for item in task_ids}
    dependents: dict[str, list[str]] = {item: [] for item in task_ids}
    for edge in problem.prerequisite_edges:
        indegree[edge.dependent_task_id] += 1
        dependents[edge.prerequisite_task_id].append(edge.dependent_task_id)
    ready = sorted(item for item, degree in indegree.items() if degree == 0)
    consumed = 0
    while ready:
        current = ready.pop(0)
        consumed += 1
        for dependent in sorted(dependents[current]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort()
    if consumed == len(task_ids):
        return ()
    return tuple(sorted(item for item, degree in indegree.items() if degree > 0))


def explain_obvious_infeasibility(
    problem: PlanScheduleProblem,
) -> tuple[ScheduleInfeasibility, ...]:
    """Report provable necessary-condition failures without building a schedule."""

    reasons: list[ScheduleInfeasibility] = []
    completed_ids = problem.completed_task_ids
    remaining_task_minutes = sum(
        problem.planning_minutes(task.task_id)
        for task in problem.tasks
        if task.task_id not in completed_ids
    )
    completed_window_minutes = sum(
        allocation.minutes
        for completed in problem.completed_tasks
        for allocation in completed.allocations
        if problem.start_date <= allocation.day <= problem.deadline
    )
    reserved_minutes = sum(item.minutes for item in problem.review_workloads)
    if problem.final_buffer is not None:
        reserved_minutes += problem.final_buffer.minutes
    required_minutes = (
        remaining_task_minutes + completed_window_minutes + reserved_minutes
    )
    available_minutes = _available_capacity(problem)

    completed_by_day: dict[date, int] = defaultdict(int)
    completed_allocations = [
        allocation
        for completed in problem.completed_tasks
        for allocation in completed.allocations
    ]
    completed_allocation_ids = [
        item.allocation_id for item in completed_allocations
    ]
    completed_positions = [
        (item.day, item.order_in_day) for item in completed_allocations
    ]
    if len(completed_allocation_ids) != len(
        set(completed_allocation_ids)
    ) or len(completed_positions) != len(set(completed_positions)):
        reasons.append(
            ScheduleInfeasibility(
                code=InfeasibilityCode.COMPLETED_HISTORY_CONFLICT,
                message=(
                    "Immutable completed history contains duplicate allocation "
                    "ids or day-order positions."
                ),
            )
        )
    for completed in problem.completed_tasks:
        for allocation in completed.allocations:
            if problem.start_date <= allocation.day <= problem.deadline:
                completed_by_day[allocation.day] += allocation.minutes
    for day, minutes in sorted(completed_by_day.items()):
        capacity = problem.capacity_by_day.get(day, 0)
        if minutes > capacity:
            reasons.append(
                ScheduleInfeasibility(
                    code=InfeasibilityCode.COMPLETED_WORK_CAPACITY_CONFLICT,
                    message=(
                        "Immutable completed work exceeds declared capacity "
                        "on its recorded day."
                    ),
                    dates=(day,),
                    required_minutes=minutes,
                    available_minutes=capacity,
                )
            )

    if required_minutes > 0 and available_minutes == 0:
        reasons.append(
            ScheduleInfeasibility(
                code=InfeasibilityCode.NO_AVAILABLE_CAPACITY,
                message="The planning window has no available learning capacity.",
                required_minutes=required_minutes,
                available_minutes=0,
            )
        )
    if required_minutes > available_minutes:
        selected_rounds = problem.selected_review_round_count
        reasons.append(
            ScheduleInfeasibility(
                code=(
                    InfeasibilityCode.INFEASIBLE_WITH_SELECTED_REVIEW_ROUNDS
                    if selected_rounds
                    else InfeasibilityCode.DEADLINE_CAPACITY_SHORTFALL
                ),
                message=(
                    "Initial learning plus all user-selected review rounds "
                    "exceed capacity before the deadline; review effort was "
                    "not shortened."
                    if selected_rounds
                    else "Required task, review, and buffer minutes exceed "
                    "capacity before the deadline."
                ),
                required_minutes=required_minutes,
                available_minutes=available_minutes,
                details=(
                    f"remaining_task_minutes={remaining_task_minutes}",
                    f"completed_window_minutes={completed_window_minutes}",
                    f"reserved_minutes={reserved_minutes}",
                    f"selected_review_rounds={selected_rounds}",
                ),
            )
        )

    max_daily_capacity = max(problem.capacity_by_day.values(), default=0)
    for task in problem.tasks:
        if task.task_id in completed_ids:
            continue
        task_minutes = problem.planning_minutes(task.task_id)
        if (
            task.split_policy is TaskSplitPolicy.UNSPLITTABLE
            and task_minutes > max_daily_capacity
        ):
            reasons.append(
                ScheduleInfeasibility(
                    code=(
                        InfeasibilityCode.UNSPLITTABLE_TASK_EXCEEDS_DAILY_CAPACITY
                    ),
                    message=(
                        "An unsplittable task is larger than every available "
                        "day in the planning window."
                    ),
                    task_ids=(task.task_id,),
                    required_minutes=task_minutes,
                    available_minutes=max_daily_capacity,
                )
            )

    for workload in problem.review_workloads:
        window_capacity = _available_capacity(
            problem,
            earliest=workload.earliest_date,
            latest=workload.latest_date,
        )
        if workload.minutes > window_capacity:
            reasons.append(
                ScheduleInfeasibility(
                    code=(
                        InfeasibilityCode.INFEASIBLE_WITH_SELECTED_REVIEW_ROUNDS
                        if workload.round_index is not None
                        else InfeasibilityCode.RESERVATION_WINDOW_CAPACITY_SHORTFALL
                    ),
                    message=(
                        "A selected review round exceeds capacity in its allowed "
                        "window; its effort was not shortened."
                        if workload.round_index is not None
                        else "A review workload exceeds capacity in its allowed window."
                    ),
                    dates=(workload.earliest_date, workload.latest_date),
                    required_minutes=workload.minutes,
                    available_minutes=window_capacity,
                    details=(f"reservation_id={workload.workload_id}",),
                )
            )
        max_window_capacity = max(
            (
                minutes
                for day, minutes in problem.capacity_by_day.items()
                if workload.earliest_date <= day <= workload.latest_date
            ),
            default=0,
        )
        if (
            workload.minutes <= window_capacity
            and not workload.splittable
            and workload.minutes > max_window_capacity
        ):
            reasons.append(
                ScheduleInfeasibility(
                    code=(
                        InfeasibilityCode.INFEASIBLE_WITH_SELECTED_REVIEW_ROUNDS
                        if workload.round_index is not None
                        else InfeasibilityCode.RESERVATION_WINDOW_CAPACITY_SHORTFALL
                    ),
                    message=(
                        "An unsplittable review workload is larger than every "
                        "available day in its window."
                    ),
                    dates=(workload.earliest_date, workload.latest_date),
                    required_minutes=workload.minutes,
                    available_minutes=max_window_capacity,
                    details=(f"reservation_id={workload.workload_id}",),
                )
            )
    if problem.final_buffer is not None:
        window_capacity = _available_capacity(
            problem,
            earliest=problem.final_buffer.earliest_date,
            latest=problem.final_buffer.latest_date,
        )
        if problem.final_buffer.minutes > window_capacity:
            reasons.append(
                ScheduleInfeasibility(
                    code=InfeasibilityCode.RESERVATION_WINDOW_CAPACITY_SHORTFALL,
                    message="The final buffer exceeds capacity in its allowed window.",
                    dates=(
                        problem.final_buffer.earliest_date,
                        problem.final_buffer.latest_date,
                    ),
                    required_minutes=problem.final_buffer.minutes,
                    available_minutes=window_capacity,
                    details=(
                        f"reservation_id={problem.final_buffer.buffer_id}",
                    ),
                )
            )
        max_window_capacity = max(
            (
                minutes
                for day, minutes in problem.capacity_by_day.items()
                if problem.final_buffer.earliest_date <= day
                <= problem.final_buffer.latest_date
            ),
            default=0,
        )
        if (
            problem.final_buffer.minutes <= window_capacity
            and problem.final_buffer.minutes > max_window_capacity
        ):
            reasons.append(
                ScheduleInfeasibility(
                    code=InfeasibilityCode.RESERVATION_WINDOW_CAPACITY_SHORTFALL,
                    message=(
                        "The unsplittable final buffer is larger than every "
                        "available day in its window."
                    ),
                    dates=(
                        problem.final_buffer.earliest_date,
                        problem.final_buffer.latest_date,
                    ),
                    required_minutes=problem.final_buffer.minutes,
                    available_minutes=max_window_capacity,
                    details=(
                        f"reservation_id={problem.final_buffer.buffer_id}",
                    ),
                )
            )

    completed_by_id = {
        item.task_id: item.allocations for item in problem.completed_tasks
    }
    for edge in problem.prerequisite_edges:
        dependent = completed_by_id.get(edge.dependent_task_id)
        if dependent is None:
            continue
        prerequisite = completed_by_id.get(edge.prerequisite_task_id)
        if prerequisite is None:
            reasons.append(
                ScheduleInfeasibility(
                    code=InfeasibilityCode.COMPLETED_PREREQUISITE_CONFLICT,
                    message=(
                        "A completed task has a prerequisite that is still "
                        "marked as remaining."
                    ),
                    task_ids=(
                        edge.prerequisite_task_id,
                        edge.dependent_task_id,
                    ),
                )
            )
            continue
        prerequisite_end = max(
            (item.day.toordinal(), item.order_in_day) for item in prerequisite
        )
        dependent_start = min(
            (item.day.toordinal(), item.order_in_day) for item in dependent
        )
        if prerequisite_end >= dependent_start:
            reasons.append(
                ScheduleInfeasibility(
                    code=InfeasibilityCode.COMPLETED_PREREQUISITE_CONFLICT,
                    message=(
                        "Immutable completed history violates prerequisite order."
                    ),
                    task_ids=(
                        edge.prerequisite_task_id,
                        edge.dependent_task_id,
                    ),
                )
            )

    cycle_nodes = _cycle_nodes(problem)
    if cycle_nodes:
        reasons.append(
            ScheduleInfeasibility(
                code=InfeasibilityCode.PREREQUISITE_CYCLE,
                message="Prerequisite edges contain a cycle.",
                task_ids=cycle_nodes,
            )
        )
    return tuple(
        sorted(
            reasons,
            key=lambda item: (
                item.code.value,
                item.task_ids,
                item.dates,
                item.message,
            ),
        )
    )


def build_daily_loads(
    problem: PlanScheduleProblem,
    allocations: Iterable[ScheduleAllocation],
) -> tuple[DailyLoad, ...]:
    """Summarize supplied allocations; this function does not place them."""

    by_day: dict[date, dict[AllocationKind, int]] = defaultdict(
        lambda: {kind: 0 for kind in AllocationKind}
    )
    for allocation in allocations:
        if problem.start_date <= allocation.day <= problem.deadline:
            by_day[allocation.day][allocation.kind] += allocation.minutes
    values: list[DailyLoad] = []
    for day in sorted(problem.capacity_by_day):
        kinds = by_day[day]
        expected = sum(kinds.values())
        capacity = problem.capacity_by_day[day]
        utilization = expected / capacity if capacity > 0 else 0.0
        values.append(
            DailyLoad(
                day=day,
                task_minutes=kinds[AllocationKind.TASK],
                review_minutes=kinds[AllocationKind.REVIEW],
                buffer_minutes=kinds[AllocationKind.FINAL_BUFFER],
                expected_daily_minutes=expected,
                capacity_minutes=capacity,
                utilization=utilization,
            )
        )
    return tuple(values)


def calculate_utilization(
    problem: PlanScheduleProblem,
    allocations: Iterable[ScheduleAllocation],
) -> float:
    allocated = sum(
        item.minutes
        for item in allocations
        if problem.start_date <= item.day <= problem.deadline
    )
    capacity = _available_capacity(problem)
    return allocated / capacity if capacity > 0 else 0.0


def _allocation_signature(
    allocations: Sequence[ScheduleAllocation],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        sorted(
            (
                item.allocation_id,
                item.kind.value,
                item.day,
                item.minutes,
                item.order_in_day,
                item.task_id,
                item.reservation_id,
                item.chunk_index,
                item.chunk_count,
                item.completed,
            )
            for item in allocations
        )
    )


def _position(item: ScheduleAllocation) -> tuple[int, int]:
    return item.day.toordinal(), item.order_in_day


def _canonical_allocation_key(item: ScheduleAllocation) -> tuple[object, ...]:
    return (
        item.day,
        item.order_in_day,
        item.kind.value,
        item.task_id or item.reservation_id or "",
        item.chunk_index,
        item.allocation_id,
    )


def validate_solution(
    problem: PlanScheduleProblem,
    solution: PlanScheduleSolution,
) -> SolutionValidation:
    """Audit a candidate result against all W2-07 contract constraints."""

    violations: list[ConstraintViolation] = []

    def add(
        code: ConstraintViolationCode,
        message: str,
        *,
        task_ids: tuple[str, ...] = (),
        dates: tuple[date, ...] = (),
    ) -> None:
        violations.append(
            ConstraintViolation(
                code=code,
                message=message,
                task_ids=task_ids,
                dates=dates,
            )
        )

    if solution.problem_id != problem.problem_id:
        add(
            ConstraintViolationCode.PROBLEM_ID_MISMATCH,
            "solution problem_id does not match the problem",
        )
    allowed_terminations = {
        ScheduleStatus.FEASIBLE: {
            SolverTerminationStatus.NOT_RUN,
            SolverTerminationStatus.OPTIMAL,
            SolverTerminationStatus.FEASIBLE,
        },
        ScheduleStatus.INFEASIBLE: {
            SolverTerminationStatus.NOT_RUN,
            SolverTerminationStatus.INFEASIBLE,
        },
        ScheduleStatus.NOT_SOLVED: {
            SolverTerminationStatus.NOT_RUN,
            SolverTerminationStatus.UNKNOWN,
            SolverTerminationStatus.MODEL_INVALID,
            SolverTerminationStatus.DEPENDENCY_UNAVAILABLE,
        },
    }
    if solution.solver_metadata.termination_status not in allowed_terminations[
        solution.status
    ]:
        add(
            ConstraintViolationCode.SOLVER_STATUS_MISMATCH,
            "solution status contradicts solver termination metadata",
        )
    if any(
        item.code is InfeasibilityCode.SOLVER_DEPENDENCY_UNAVAILABLE
        for item in solution.infeasibilities
    ) and solution.solver_metadata.dependency_available:
        add(
            ConstraintViolationCode.SOLVER_STATUS_MISMATCH,
            "dependency-unavailable reason contradicts solver metadata",
        )
    if solution.status is ScheduleStatus.NOT_SOLVED:
        termination_by_reason = {
            InfeasibilityCode.SOLVER_DEPENDENCY_UNAVAILABLE: (
                SolverTerminationStatus.DEPENDENCY_UNAVAILABLE
            ),
            InfeasibilityCode.SOLVER_TIMEOUT: SolverTerminationStatus.UNKNOWN,
            InfeasibilityCode.SOLVER_STATUS_UNKNOWN: SolverTerminationStatus.UNKNOWN,
            InfeasibilityCode.SOLVER_MODEL_INVALID: (
                SolverTerminationStatus.MODEL_INVALID
            ),
        }
        reported_terminations = {
            termination_by_reason[item.code]
            for item in solution.infeasibilities
            if item.code in termination_by_reason
        }
        if (
            solution.solver_metadata.termination_status
            is not SolverTerminationStatus.NOT_RUN
            and reported_terminations
            != {solution.solver_metadata.termination_status}
        ):
            add(
                ConstraintViolationCode.SOLVER_STATUS_MISMATCH,
                "solver termination reason contradicts termination metadata",
            )
    if problem.deterministic:
        if not solution.solver_metadata.deterministic:
            add(
                ConstraintViolationCode.NONDETERMINISTIC_OUTPUT,
                "deterministic problem requires deterministic solver metadata",
            )
        if list(solution.allocations) != sorted(
            solution.allocations,
            key=_canonical_allocation_key,
        ):
            add(
                ConstraintViolationCode.NONDETERMINISTIC_OUTPUT,
                "allocations are not in canonical deterministic order",
            )
        if list(solution.daily_loads) != sorted(
            solution.daily_loads,
            key=lambda item: item.day,
        ):
            add(
                ConstraintViolationCode.NONDETERMINISTIC_OUTPUT,
                "daily loads are not in canonical deterministic order",
            )
    if solution.status is not ScheduleStatus.FEASIBLE:
        if solution.status is ScheduleStatus.INFEASIBLE:
            obvious_codes = {
                item.code for item in explain_obvious_infeasibility(problem)
            }
            reported_codes = {item.code for item in solution.infeasibilities}
            if not obvious_codes.issubset(reported_codes):
                add(
                    ConstraintViolationCode.PROBLEM_OBVIOUSLY_INFEASIBLE,
                    "infeasible solution omits a provable necessary-condition reason",
                )
        return SolutionValidation(valid=not violations, violations=tuple(violations))

    obvious_reasons = explain_obvious_infeasibility(problem)
    if obvious_reasons:
        add(
            ConstraintViolationCode.PROBLEM_OBVIOUSLY_INFEASIBLE,
            "candidate claims feasibility despite a necessary-condition failure",
        )

    task_by_id = problem.task_by_id
    completed_by_id = {item.task_id: item for item in problem.completed_tasks}
    task_allocations: dict[str, list[ScheduleAllocation]] = defaultdict(list)
    review_allocations: dict[str, list[ScheduleAllocation]] = defaultdict(list)
    buffer_allocations: dict[str, list[ScheduleAllocation]] = defaultdict(list)
    order_by_day: dict[date, list[int]] = defaultdict(list)

    for allocation in solution.allocations:
        order_by_day[allocation.day].append(allocation.order_in_day)
        if allocation.kind is AllocationKind.TASK:
            if allocation.task_id not in task_by_id:
                add(
                    ConstraintViolationCode.UNKNOWN_ALLOCATION_TARGET,
                    "task allocation references an unknown task",
                    task_ids=(allocation.task_id or "",),
                )
                continue
            task_allocations[allocation.task_id].append(allocation)
        elif allocation.kind is AllocationKind.REVIEW:
            review_allocations[allocation.reservation_id or ""].append(allocation)
        else:
            buffer_allocations[allocation.reservation_id or ""].append(allocation)

        is_historical_completed = (
            allocation.kind is AllocationKind.TASK
            and allocation.task_id in completed_by_id
            and allocation.day < problem.start_date
        )
        if not is_historical_completed and not (
            problem.start_date <= allocation.day <= problem.deadline
        ):
            add(
                ConstraintViolationCode.DATE_OUTSIDE_WINDOW,
                "allocation lies outside the planning window",
                dates=(allocation.day,),
            )
        if (
            not is_historical_completed
            and allocation.day not in problem.capacity_by_day
        ):
            add(
                ConstraintViolationCode.MISSING_DAILY_CAPACITY,
                "allocation uses a day with no declared capacity",
                dates=(allocation.day,),
            )
        if (
            not is_historical_completed
            and allocation.day in problem.unavailable_days
        ):
            add(
                ConstraintViolationCode.UNAVAILABLE_DAY,
                "allocation uses an unavailable day",
                dates=(allocation.day,),
            )

    for day, order_values in order_by_day.items():
        if len(order_values) != len(set(order_values)):
            add(
                ConstraintViolationCode.SPLIT_POLICY,
                "order_in_day values must be unique for deterministic ordering",
                dates=(day,),
            )

    for task in problem.tasks:
        actual = sorted(
            task_allocations.get(task.task_id, ()),
            key=lambda item: (item.day, item.order_in_day, item.allocation_id),
        )
        completed = completed_by_id.get(task.task_id)
        if completed is not None:
            if _allocation_signature(actual) != _allocation_signature(
                completed.allocations
            ):
                add(
                    ConstraintViolationCode.COMPLETED_TASK_CHANGED,
                    "completed task allocations are not immutable",
                    task_ids=(task.task_id,),
                )
            continue

        expected_minutes = problem.planning_minutes(task.task_id)
        if sum(item.minutes for item in actual) != expected_minutes:
            add(
                ConstraintViolationCode.TASK_MINUTES_MISMATCH,
                "task allocation minutes do not match the selected effort interval",
                task_ids=(task.task_id,),
            )
        if any(item.completed for item in actual):
            add(
                ConstraintViolationCode.COMPLETED_TASK_CHANGED,
                "remaining task allocation cannot be marked completed",
                task_ids=(task.task_id,),
            )
        if task.split_policy is TaskSplitPolicy.UNSPLITTABLE:
            if len(actual) != 1 or any(
                item.chunk_index != 1 or item.chunk_count != 1 for item in actual
            ):
                add(
                    ConstraintViolationCode.SPLIT_POLICY,
                    "unsplittable task must have exactly one allocation",
                    task_ids=(task.task_id,),
                )
        elif actual:
            expected_indices = list(range(1, len(actual) + 1))
            actual_indices = [item.chunk_index for item in actual]
            counts = {item.chunk_count for item in actual}
            if actual_indices != expected_indices or counts != {len(actual)}:
                add(
                    ConstraintViolationCode.SPLIT_POLICY,
                    "split task chunk indices/counts are inconsistent",
                    task_ids=(task.task_id,),
                )
            if task.maximum_chunks is not None and len(actual) > task.maximum_chunks:
                add(
                    ConstraintViolationCode.SPLIT_POLICY,
                    "split task exceeds maximum_chunks",
                    task_ids=(task.task_id,),
                )
            if task.maximum_chunk_minutes is not None and any(
                item.minutes > task.maximum_chunk_minutes for item in actual
            ):
                add(
                    ConstraintViolationCode.SPLIT_POLICY,
                    "task chunk exceeds maximum_chunk_minutes",
                    task_ids=(task.task_id,),
                )
            chunks_to_check = actual[:-1] if task.allow_final_short_chunk else actual
            if any(item.minutes < task.minimum_chunk_minutes for item in chunks_to_check):
                add(
                    ConstraintViolationCode.SPLIT_POLICY,
                    "task chunk is smaller than minimum_chunk_minutes",
                    task_ids=(task.task_id,),
                )

    for edge in problem.prerequisite_edges:
        prerequisite = task_allocations.get(edge.prerequisite_task_id, ())
        dependent = task_allocations.get(edge.dependent_task_id, ())
        if prerequisite and dependent and max(map(_position, prerequisite)) >= min(
            map(_position, dependent)
        ):
            add(
                ConstraintViolationCode.PREREQUISITE_ORDER,
                "dependent task starts before its prerequisite is complete",
                task_ids=(
                    edge.prerequisite_task_id,
                    edge.dependent_task_id,
                ),
            )

    review_by_id = {item.workload_id: item for item in problem.review_workloads}
    for reservation_id, allocations in review_allocations.items():
        if reservation_id not in review_by_id:
            add(
                ConstraintViolationCode.UNKNOWN_ALLOCATION_TARGET,
                "review allocation references an unknown workload",
            )
    for workload in problem.review_workloads:
        actual = review_allocations.get(workload.workload_id, ())
        if sum(item.minutes for item in actual) != workload.minutes or any(
            not workload.earliest_date <= item.day <= workload.latest_date
            for item in actual
        ):
            add(
                ConstraintViolationCode.RESERVATION_MINUTES_MISMATCH,
                "review workload allocation does not match its minutes/window",
                dates=(workload.earliest_date, workload.latest_date),
            )
        if not workload.splittable and len(actual) != 1:
            add(
                ConstraintViolationCode.SPLIT_POLICY,
                "unsplittable review workload must have one allocation",
            )

    for workload in problem.fixed_review_workloads:
        review_values = review_allocations.get(workload.workload_id, ())
        if not review_values:
            continue
        review_start = min(map(_position, review_values))
        for task_id in workload.relevant_task_ids:
            task_values = task_allocations.get(task_id, ())
            if task_values and max(map(_position, task_values)) >= review_start:
                add(
                    ConstraintViolationCode.REVIEW_AFTER_LEARNING_ORDER,
                    "review round starts before relevant initial learning is complete",
                    task_ids=(task_id,),
                )
    for previous, current in zip(
        problem.fixed_review_workloads,
        problem.fixed_review_workloads[1:],
        strict=False,
    ):
        previous_values = review_allocations.get(previous.workload_id, ())
        current_values = review_allocations.get(current.workload_id, ())
        if (
            previous_values
            and current_values
            and max(map(_position, previous_values))
            >= min(map(_position, current_values))
        ):
            add(
                ConstraintViolationCode.REVIEW_ROUND_ORDER,
                "review round order does not match the selected macro rounds",
            )

    expected_buffer_id = (
        problem.final_buffer.buffer_id if problem.final_buffer is not None else None
    )
    if any(item != expected_buffer_id for item in buffer_allocations):
        add(
            ConstraintViolationCode.UNKNOWN_ALLOCATION_TARGET,
            "final-buffer allocation references an unknown buffer",
        )
    if problem.final_buffer is not None:
        actual = buffer_allocations.get(problem.final_buffer.buffer_id, ())
        if sum(item.minutes for item in actual) != problem.final_buffer.minutes or any(
            not problem.final_buffer.earliest_date
            <= item.day
            <= problem.final_buffer.latest_date
            for item in actual
        ):
            add(
                ConstraintViolationCode.RESERVATION_MINUTES_MISMATCH,
                "final-buffer allocation does not match its minutes/window",
                dates=(
                    problem.final_buffer.earliest_date,
                    problem.final_buffer.latest_date,
                ),
            )
    elif buffer_allocations:
        add(
            ConstraintViolationCode.RESERVATION_MINUTES_MISMATCH,
            "solution allocates a final buffer not present in the problem",
        )

    derived_loads = build_daily_loads(problem, solution.allocations)
    for load in derived_loads:
        if load.expected_daily_minutes > load.capacity_minutes:
            add(
                ConstraintViolationCode.DAILY_CAPACITY_EXCEEDED,
                "daily load exceeds available capacity",
                dates=(load.day,),
            )
    if [item.model_dump(mode="json") for item in solution.daily_loads] != [
        item.model_dump(mode="json") for item in derived_loads
    ]:
        add(
            ConstraintViolationCode.DAILY_LOAD_MISMATCH,
            "daily_loads do not match allocations and capacity",
        )
    expected_utilization = calculate_utilization(problem, solution.allocations)
    if abs(solution.utilization - expected_utilization) > 1e-6:
        add(
            ConstraintViolationCode.UTILIZATION_MISMATCH,
            "solution utilization does not match allocations and capacity",
        )
    return SolutionValidation(valid=not violations, violations=tuple(violations))


__all__ = [
    "build_daily_loads",
    "calculate_utilization",
    "explain_obvious_infeasibility",
    "validate_solution",
]
