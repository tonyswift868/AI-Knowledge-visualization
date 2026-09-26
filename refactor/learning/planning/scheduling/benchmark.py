"""Solver-neutral benchmark harness with independent result validation."""

from __future__ import annotations

from time import perf_counter
from typing import Protocol

from pydantic import Field

from .contracts import (
    PlanScheduleProblem,
    PlanScheduleSolution,
    ScheduleSolveConfig,
    ScheduleStatus,
    SolutionValidation,
    _FrozenContract,
)
from .validator import ScheduleValidator


class ScheduleSolver(Protocol):
    def solve(
        self,
        problem: PlanScheduleProblem,
        *,
        config: ScheduleSolveConfig | None = None,
    ) -> PlanScheduleSolution: ...


class ScheduleBenchmarkResult(_FrozenContract):
    problem_id: str = Field(min_length=1, max_length=160)
    task_count: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)
    timeout_seconds: float = Field(gt=0)
    within_timeout: bool
    solution_status: ScheduleStatus
    solution: PlanScheduleSolution
    validation: SolutionValidation


def run_schedule_benchmark(
    solver: ScheduleSolver,
    problem: PlanScheduleProblem,
    *,
    config: ScheduleSolveConfig | None = None,
    validator: ScheduleValidator | None = None,
) -> ScheduleBenchmarkResult:
    effective_config = config or ScheduleSolveConfig()
    effective_validator = validator or ScheduleValidator()
    started = perf_counter()
    solution = solver.solve(problem, config=effective_config)
    elapsed_seconds = perf_counter() - started
    validation = effective_validator.validate(problem, solution)
    return ScheduleBenchmarkResult(
        problem_id=problem.problem_id,
        task_count=len(problem.tasks),
        elapsed_ms=round(elapsed_seconds * 1_000),
        timeout_seconds=effective_config.max_time_seconds,
        within_timeout=elapsed_seconds <= effective_config.max_time_seconds,
        solution_status=solution.status,
        solution=solution,
        validation=validation,
    )


__all__ = [
    "ScheduleBenchmarkResult",
    "ScheduleSolver",
    "run_schedule_benchmark",
]
