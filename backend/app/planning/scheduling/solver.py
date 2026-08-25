"""OR-Tools CP-SAT implementation for constrained learning-plan scheduling."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Any

import ortools
from ortools.sat.python import cp_model

from .contracts import (
    AllocationKind,
    InfeasibilityCode,
    PlanScheduleProblem,
    PlanScheduleSolution,
    ScheduleAllocation,
    ScheduleInfeasibility,
    ScheduleSolveConfig,
    ScheduleStatus,
    SolverMetadata,
    SolverTerminationStatus,
    TaskSplitPolicy,
)
from .replan import ReplanDelta, build_replan_delta
from .validation import (
    build_daily_loads,
    calculate_utilization,
    explain_obvious_infeasibility,
)
from .validator import ScheduleValidator


SOLVER_VERSION = "mentorflow.ortools-cp-sat.v1"


@dataclass(frozen=True)
class _DecisionAllocation:
    kind: AllocationKind
    target_id: str
    day_index: int
    used: Any
    minutes: Any


@dataclass(frozen=True)
class _ModelContext:
    model: Any
    days: tuple[date, ...]
    task_decisions: tuple[_DecisionAllocation, ...]
    reservation_decisions: tuple[_DecisionAllocation, ...]
    daily_loads: tuple[Any, ...]


class ORToolsScheduleSolver:
    """Solve a ``PlanScheduleProblem`` using integer-minute CP-SAT variables."""

    def __init__(self, *, validator: ScheduleValidator | None = None) -> None:
        self._validator = validator or ScheduleValidator()

    def solve(
        self,
        problem: PlanScheduleProblem,
        *,
        config: ScheduleSolveConfig | None = None,
    ) -> PlanScheduleSolution:
        effective_config = config or ScheduleSolveConfig()
        if problem.deterministic and not effective_config.deterministic:
            raise ValueError(
                "deterministic problem requires deterministic solve config"
            )
        precheck_reasons = explain_obvious_infeasibility(problem)
        if precheck_reasons:
            return self._non_feasible_solution(
                problem,
                effective_config,
                status=ScheduleStatus.INFEASIBLE,
                termination=SolverTerminationStatus.NOT_RUN,
                reasons=precheck_reasons,
                wall_time_ms=0,
                configuration_extra=("deterministic_precheck=true",),
            )

        context = self._build_model(problem)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = effective_config.max_time_seconds
        solver.parameters.num_search_workers = effective_config.num_search_workers
        solver.parameters.random_seed = effective_config.random_seed
        solver.parameters.log_search_progress = effective_config.log_search_progress
        status = solver.Solve(context.model)
        wall_time_ms = round(solver.WallTime() * 1_000)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            termination = (
                SolverTerminationStatus.OPTIMAL
                if status == cp_model.OPTIMAL
                else SolverTerminationStatus.FEASIBLE
            )
            solution = self._build_feasible_solution(
                problem,
                effective_config,
                context,
                solver,
                termination=termination,
                wall_time_ms=wall_time_ms,
            )
            self._validator.require_valid(problem, solution)
            return solution

        if status == cp_model.INFEASIBLE:
            return self._non_feasible_solution(
                problem,
                effective_config,
                status=ScheduleStatus.INFEASIBLE,
                termination=SolverTerminationStatus.INFEASIBLE,
                reasons=(
                    ScheduleInfeasibility(
                        code=InfeasibilityCode.OTHER,
                        message=(
                            "CP-SAT proved the complete scheduling model "
                            "infeasible; deterministic prechecks did not isolate "
                            "a narrower cause."
                        ),
                        details=("cp_sat_status=INFEASIBLE",),
                    ),
                ),
                wall_time_ms=wall_time_ms,
            )

        if status == cp_model.MODEL_INVALID:
            return self._non_feasible_solution(
                problem,
                effective_config,
                status=ScheduleStatus.NOT_SOLVED,
                termination=SolverTerminationStatus.MODEL_INVALID,
                reasons=(
                    ScheduleInfeasibility(
                        code=InfeasibilityCode.SOLVER_MODEL_INVALID,
                        message="CP-SAT rejected the generated scheduling model.",
                        details=("cp_sat_status=MODEL_INVALID",),
                    ),
                ),
                wall_time_ms=wall_time_ms,
            )

        timed_out = solver.WallTime() >= effective_config.max_time_seconds * 0.95
        reason = ScheduleInfeasibility(
            code=(
                InfeasibilityCode.SOLVER_TIMEOUT
                if timed_out
                else InfeasibilityCode.SOLVER_STATUS_UNKNOWN
            ),
            message=(
                "CP-SAT reached the configured solve timeout without a solution."
                if timed_out
                else "CP-SAT returned UNKNOWN without a feasibility conclusion."
            ),
            details=("cp_sat_status=UNKNOWN",),
        )
        return self._non_feasible_solution(
            problem,
            effective_config,
            status=ScheduleStatus.NOT_SOLVED,
            termination=SolverTerminationStatus.UNKNOWN,
            reasons=(reason,),
            wall_time_ms=wall_time_ms,
        )

    def replan(
        self,
        old_problem: PlanScheduleProblem,
        old_solution: PlanScheduleSolution,
        new_problem: PlanScheduleProblem,
        *,
        cause: str,
        config: ScheduleSolveConfig | None = None,
    ) -> tuple[PlanScheduleSolution, ReplanDelta]:
        new_solution = self.solve(new_problem, config=config)
        delta = build_replan_delta(
            old_problem,
            old_solution,
            new_problem,
            new_solution,
            cause=cause,
        )
        return new_solution, delta

    def _build_model(self, problem: PlanScheduleProblem) -> _ModelContext:
        model = cp_model.CpModel()
        days = tuple(sorted(problem.capacity_by_day))
        day_index = {day: index for index, day in enumerate(days)}
        day_count = len(days)
        load_terms: list[list[Any]] = [[] for _ in days]
        fixed_loads = [0 for _ in days]
        for completed in problem.completed_tasks:
            for allocation in completed.allocations:
                if allocation.day in day_index:
                    fixed_loads[day_index[allocation.day]] += allocation.minutes

        task_decisions: list[_DecisionAllocation] = []
        task_start: dict[str, Any] = {}
        task_end: dict[str, Any] = {}
        completed_by_id = {
            item.task_id: item.allocations for item in problem.completed_tasks
        }

        for task in problem.tasks:
            if task.task_id in completed_by_id:
                indices = tuple(
                    -1
                    if item.day < problem.start_date
                    else day_index[item.day]
                    for item in completed_by_id[task.task_id]
                )
                task_start[task.task_id] = min(indices)
                task_end[task.task_id] = max(indices)
                continue

            total_minutes = problem.planning_minutes(task.task_id)
            used_by_day: list[Any] = []
            amount_by_day: list[Any] = []
            short_by_day: list[Any] = []
            for index, _day in enumerate(days):
                used = model.NewBoolVar(f"task:{task.task_id}:used:{index}")
                used_by_day.append(used)
                if task.split_policy is TaskSplitPolicy.UNSPLITTABLE:
                    amount: Any = total_minutes * used
                else:
                    amount = model.NewIntVar(
                        0,
                        total_minutes,
                        f"task:{task.task_id}:minutes:{index}",
                    )
                    model.Add(amount <= total_minutes * used)
                    model.Add(amount >= used)
                    if task.allow_final_short_chunk:
                        short = model.NewBoolVar(
                            f"task:{task.task_id}:short:{index}"
                        )
                        short_by_day.append(short)
                        model.Add(short <= used)
                        model.Add(
                            amount
                            >= task.minimum_chunk_minutes * used
                            - task.minimum_chunk_minutes * short
                        )
                        model.Add(
                            amount
                            <= task.minimum_chunk_minutes
                            - 1
                            + total_minutes * (1 - short)
                        )
                    else:
                        model.Add(amount >= task.minimum_chunk_minutes * used)
                amount_by_day.append(amount)
                load_terms[index].append(amount)
                task_decisions.append(
                    _DecisionAllocation(
                        kind=AllocationKind.TASK,
                        target_id=task.task_id,
                        day_index=index,
                        used=used,
                        minutes=amount,
                    )
                )

            if task.split_policy is TaskSplitPolicy.UNSPLITTABLE:
                model.Add(sum(used_by_day) == 1)
            else:
                model.Add(sum(amount_by_day) == total_minutes)
                if task.maximum_chunks is not None:
                    model.Add(sum(used_by_day) <= task.maximum_chunks)
                if task.allow_final_short_chunk:
                    model.Add(sum(short_by_day) <= 1)
                    for earlier, short in enumerate(short_by_day):
                        for later in range(earlier + 1, day_count):
                            model.Add(short + used_by_day[later] <= 1)

            start_candidates: list[Any] = []
            end_candidates: list[Any] = []
            for index, used in enumerate(used_by_day):
                start_candidate = model.NewIntVar(
                    0,
                    day_count,
                    f"task:{task.task_id}:start-candidate:{index}",
                )
                model.Add(start_candidate == index).OnlyEnforceIf(used)
                model.Add(start_candidate == day_count).OnlyEnforceIf(used.Not())
                start_candidates.append(start_candidate)
                end_candidate = model.NewIntVar(
                    -1,
                    day_count - 1,
                    f"task:{task.task_id}:end-candidate:{index}",
                )
                model.Add(end_candidate == index).OnlyEnforceIf(used)
                model.Add(end_candidate == -1).OnlyEnforceIf(used.Not())
                end_candidates.append(end_candidate)
            start = model.NewIntVar(0, day_count - 1, f"task:{task.task_id}:start")
            end = model.NewIntVar(0, day_count - 1, f"task:{task.task_id}:end")
            model.AddMinEquality(start, start_candidates)
            model.AddMaxEquality(end, end_candidates)
            task_start[task.task_id] = start
            task_end[task.task_id] = end

        for edge in problem.prerequisite_edges:
            model.Add(
                task_end[edge.prerequisite_task_id]
                <= task_start[edge.dependent_task_id]
            )
            prerequisite_allocations = completed_by_id.get(
                edge.prerequisite_task_id
            )
            if (
                prerequisite_allocations is not None
                and edge.dependent_task_id not in completed_by_id
            ):
                prerequisite_end_day = task_end[edge.prerequisite_task_id]
                if prerequisite_end_day >= 0:
                    end_date = days[prerequisite_end_day]
                    end_orders = [
                        item.order_in_day
                        for item in prerequisite_allocations
                        if item.day == end_date
                    ]
                    if max(end_orders, default=-1) >= 10_000:
                        model.Add(
                            task_start[edge.dependent_task_id]
                            >= prerequisite_end_day + 1
                        )

        reservation_decisions: list[_DecisionAllocation] = []
        for workload in problem.review_workloads:
            eligible = [
                index
                for index, day in enumerate(days)
                if workload.earliest_date <= day <= workload.latest_date
            ]
            used_values: list[Any] = []
            minute_values: list[Any] = []
            for index in eligible:
                used = model.NewBoolVar(
                    f"review:{workload.workload_id}:used:{index}"
                )
                used_values.append(used)
                if workload.splittable:
                    minutes: Any = model.NewIntVar(
                        0,
                        workload.minutes,
                        f"review:{workload.workload_id}:minutes:{index}",
                    )
                    model.Add(minutes <= workload.minutes * used)
                    model.Add(minutes >= used)
                else:
                    minutes = workload.minutes * used
                minute_values.append(minutes)
                load_terms[index].append(minutes)
                reservation_decisions.append(
                    _DecisionAllocation(
                        kind=AllocationKind.REVIEW,
                        target_id=workload.workload_id,
                        day_index=index,
                        used=used,
                        minutes=minutes,
                    )
                )
            if workload.splittable:
                model.Add(sum(minute_values) == workload.minutes)
            else:
                model.Add(sum(used_values) == 1)

        if problem.final_buffer is not None:
            buffer = problem.final_buffer
            eligible = [
                index
                for index, day in enumerate(days)
                if buffer.earliest_date <= day <= buffer.latest_date
            ]
            used_values = []
            for index in eligible:
                used = model.NewBoolVar(f"buffer:{buffer.buffer_id}:used:{index}")
                used_values.append(used)
                minutes = buffer.minutes * used
                load_terms[index].append(minutes)
                reservation_decisions.append(
                    _DecisionAllocation(
                        kind=AllocationKind.FINAL_BUFFER,
                        target_id=buffer.buffer_id,
                        day_index=index,
                        used=used,
                        minutes=minutes,
                    )
                )
            model.Add(sum(used_values) == 1)

        daily_loads: list[Any] = []
        peak_utilization = model.NewIntVar(0, 1_000, "peak-utilization")
        for index, day in enumerate(days):
            capacity = problem.capacity_by_day[day]
            load = model.NewIntVar(0, max(capacity, fixed_loads[index]), f"load:{index}")
            model.Add(load == fixed_loads[index] + sum(load_terms[index]))
            model.Add(load <= capacity)
            daily_loads.append(load)
            if capacity > 0:
                model.Add(load * 1_000 <= peak_utilization * capacity)

        remaining_ends = [
            task_end[item.task_id]
            for item in problem.tasks
            if item.task_id not in completed_by_id
        ]
        latest_finish = model.NewIntVar(0, day_count, "latest-finish")
        if remaining_ends:
            for end in remaining_ends:
                model.Add(latest_finish >= end + 1)
        else:
            model.Add(latest_finish == 0)
        decision_uses = [
            item.used for item in (*task_decisions, *reservation_decisions)
        ]
        fragmentation_bound = len(decision_uses)
        finish_weight = fragmentation_bound + 1
        peak_weight = (day_count + 1) * finish_weight
        model.Minimize(
            peak_utilization * peak_weight
            + latest_finish * finish_weight
            + sum(decision_uses)
        )
        return _ModelContext(
            model=model,
            days=days,
            task_decisions=tuple(task_decisions),
            reservation_decisions=tuple(reservation_decisions),
            daily_loads=tuple(daily_loads),
        )

    def _build_feasible_solution(
        self,
        problem: PlanScheduleProblem,
        config: ScheduleSolveConfig,
        context: _ModelContext,
        solver: Any,
        *,
        termination: SolverTerminationStatus,
        wall_time_ms: int,
    ) -> PlanScheduleSolution:
        completed_allocations = [
            allocation
            for completed in problem.completed_tasks
            for allocation in completed.allocations
        ]
        active_by_task: dict[str, list[_DecisionAllocation]] = {}
        for decision in context.task_decisions:
            if solver.Value(decision.used):
                active_by_task.setdefault(decision.target_id, []).append(decision)

        pending: list[tuple[ScheduleAllocation, int]] = []
        task_rank = self._topological_task_rank(problem)
        for task_id, decisions in sorted(active_by_task.items()):
            ordered = sorted(decisions, key=lambda item: item.day_index)
            chunk_count = len(ordered)
            for chunk_index, decision in enumerate(ordered, start=1):
                day = context.days[decision.day_index]
                pending.append(
                    (
                        ScheduleAllocation(
                            allocation_id=(
                                f"cp:task:{task_id}:{day.isoformat()}:{chunk_index}"
                            ),
                            kind=AllocationKind.TASK,
                            task_id=task_id,
                            day=day,
                            minutes=solver.Value(decision.minutes),
                            order_in_day=0,
                            chunk_index=chunk_index,
                            chunk_count=chunk_count,
                        ),
                        task_rank[task_id],
                    )
                )

        kind_rank = {
            AllocationKind.REVIEW: len(task_rank) + 1,
            AllocationKind.FINAL_BUFFER: len(task_rank) + 2,
        }
        for decision in context.reservation_decisions:
            if not solver.Value(decision.used):
                continue
            day = context.days[decision.day_index]
            pending.append(
                (
                    ScheduleAllocation(
                        allocation_id=(
                            f"cp:{decision.kind.value}:{decision.target_id}:"
                            f"{day.isoformat()}"
                        ),
                        kind=decision.kind,
                        reservation_id=decision.target_id,
                        day=day,
                        minutes=solver.Value(decision.minutes),
                        order_in_day=0,
                    ),
                    kind_rank[decision.kind],
                )
            )

        completed_orders: dict[date, set[int]] = {}
        completed_task_orders: dict[tuple[date, str], list[int]] = {}
        for allocation in completed_allocations:
            completed_orders.setdefault(allocation.day, set()).add(
                allocation.order_in_day
            )
            if allocation.task_id is not None:
                completed_task_orders.setdefault(
                    (allocation.day, allocation.task_id),
                    [],
                ).append(allocation.order_in_day)
        prerequisites_by_task: dict[str, list[str]] = {}
        for edge in problem.prerequisite_edges:
            prerequisites_by_task.setdefault(edge.dependent_task_id, []).append(
                edge.prerequisite_task_id
            )
        pending_by_day: dict[date, list[tuple[ScheduleAllocation, int]]] = {}
        for item in pending:
            pending_by_day.setdefault(item[0].day, []).append(item)
        generated: list[ScheduleAllocation] = []
        for day, values in sorted(pending_by_day.items()):
            used_orders = set(completed_orders.get(day, set()))
            assigned_task_orders: dict[str, int] = {}
            for allocation, _rank in sorted(
                values,
                key=lambda item: (
                    item[1],
                    item[0].kind.value,
                    item[0].task_id or item[0].reservation_id or "",
                    item[0].chunk_index,
                ),
            ):
                minimum_order = 0
                if allocation.task_id is not None:
                    for prerequisite_id in prerequisites_by_task.get(
                        allocation.task_id,
                        (),
                    ):
                        completed_values = completed_task_orders.get(
                            (day, prerequisite_id),
                            (),
                        )
                        if completed_values:
                            minimum_order = max(
                                minimum_order,
                                max(completed_values) + 1,
                            )
                        if prerequisite_id in assigned_task_orders:
                            minimum_order = max(
                                minimum_order,
                                assigned_task_orders[prerequisite_id] + 1,
                            )
                next_order = minimum_order
                while next_order in used_orders and next_order <= 10_000:
                    next_order += 1
                if next_order > 10_000:
                    raise RuntimeError("schedule exceeds deterministic order capacity")
                generated.append(
                    allocation.model_copy(update={"order_in_day": next_order})
                )
                used_orders.add(next_order)
                if allocation.task_id is not None:
                    assigned_task_orders[allocation.task_id] = next_order

        allocations = tuple(
            sorted(
                (*completed_allocations, *generated),
                key=lambda item: (
                    item.day,
                    item.order_in_day,
                    item.kind.value,
                    item.task_id or item.reservation_id or "",
                    item.chunk_index,
                    item.allocation_id,
                ),
            )
        )
        daily_loads = build_daily_loads(problem, allocations)
        return PlanScheduleSolution(
            solution_id=self._solution_id(problem, config),
            problem_id=problem.problem_id,
            status=ScheduleStatus.FEASIBLE,
            allocations=allocations,
            daily_loads=daily_loads,
            utilization=calculate_utilization(problem, allocations),
            solver_metadata=self._metadata(
                config,
                termination=termination,
                wall_time_ms=wall_time_ms,
            ),
        )

    def _non_feasible_solution(
        self,
        problem: PlanScheduleProblem,
        config: ScheduleSolveConfig,
        *,
        status: ScheduleStatus,
        termination: SolverTerminationStatus,
        reasons: tuple[ScheduleInfeasibility, ...],
        wall_time_ms: int,
        configuration_extra: tuple[str, ...] = (),
    ) -> PlanScheduleSolution:
        solution = PlanScheduleSolution(
            solution_id=self._solution_id(problem, config),
            problem_id=problem.problem_id,
            status=status,
            infeasibilities=reasons,
            utilization=0,
            solver_metadata=self._metadata(
                config,
                termination=termination,
                wall_time_ms=wall_time_ms,
                configuration_extra=configuration_extra,
            ),
        )
        self._validator.require_valid(problem, solution)
        return solution

    @staticmethod
    def _metadata(
        config: ScheduleSolveConfig,
        *,
        termination: SolverTerminationStatus,
        wall_time_ms: int,
        configuration_extra: tuple[str, ...] = (),
    ) -> SolverMetadata:
        return SolverMetadata(
            solver_name="ortools-cp-sat",
            solver_version=SOLVER_VERSION,
            deterministic=config.deterministic,
            random_seed=config.random_seed,
            wall_time_ms=wall_time_ms,
            dependency_name="ortools",
            dependency_version=ortools.__version__,
            dependency_available=True,
            termination_status=termination,
            configuration=(
                f"max_time_seconds={config.max_time_seconds}",
                f"num_search_workers={config.num_search_workers}",
                f"log_search_progress={str(config.log_search_progress).lower()}",
                "time_unit=integer_minutes",
                "objective=peak_utilization,deadline_slack,fragmentation",
                *configuration_extra,
            ),
        )

    @staticmethod
    def _solution_id(
        problem: PlanScheduleProblem,
        config: ScheduleSolveConfig,
    ) -> str:
        problem_payload = problem.model_dump(mode="json")
        problem_payload["unavailable_days"] = sorted(
            problem_payload["unavailable_days"]
        )
        payload = json.dumps(
            {
                "config": config.model_dump(mode="json"),
                "problem": problem_payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = sha256(payload.encode("utf-8")).hexdigest()[:20]
        return f"cp-sat-{digest}"

    @staticmethod
    def _topological_task_rank(problem: PlanScheduleProblem) -> dict[str, int]:
        indegree = {item.task_id: 0 for item in problem.tasks}
        dependents = {item.task_id: [] for item in problem.tasks}
        for edge in problem.prerequisite_edges:
            indegree[edge.dependent_task_id] += 1
            dependents[edge.prerequisite_task_id].append(edge.dependent_task_id)
        ready = sorted(item for item, degree in indegree.items() if degree == 0)
        ordered: list[str] = []
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for dependent in sorted(dependents[current]):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)
                    ready.sort()
        if len(ordered) != len(problem.tasks):
            raise RuntimeError("prerequisite cycle escaped deterministic precheck")
        return {task_id: index for index, task_id in enumerate(ordered)}


__all__ = ["ORToolsScheduleSolver", "SOLVER_VERSION"]
