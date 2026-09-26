"""Read-only dependency capability reporting for the future CP-SAT adapter."""

from __future__ import annotations

from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec

from .contracts import (
    InfeasibilityCode,
    PlanScheduleProblem,
    PlanScheduleSolution,
    ScheduleDependencyStatus,
    ScheduleInfeasibility,
    ScheduleStatus,
    SolverMetadata,
    SolverTerminationStatus,
)


def _ortools_cp_sat_available() -> bool:
    try:
        return (
            find_spec("ortools") is not None
            and find_spec("ortools.sat.python.cp_model") is not None
        )
    except (AttributeError, ImportError, ModuleNotFoundError):
        return False


ORTOOLS_AVAILABLE = _ortools_cp_sat_available()
DEPENDENCY_CHANGE_REQUIRED = None if ORTOOLS_AVAILABLE else "ortools"


def ortools_dependency_status() -> ScheduleDependencyStatus:
    dependency_version: str | None = None
    if ORTOOLS_AVAILABLE:
        try:
            dependency_version = version("ortools")
        except PackageNotFoundError:
            dependency_version = None
    return ScheduleDependencyStatus(
        available=ORTOOLS_AVAILABLE,
        version=dependency_version,
        dependency_change_required=DEPENDENCY_CHANGE_REQUIRED,
    )


def dependency_unavailable_solution(
    problem: PlanScheduleProblem,
) -> PlanScheduleSolution:
    """Return an explicit non-solution without claiming mathematical infeasibility."""

    if ORTOOLS_AVAILABLE:
        raise RuntimeError("OR-Tools is available; dependency blocker is not valid")
    problem_digest = sha256(problem.problem_id.encode("utf-8")).hexdigest()[:16]
    return PlanScheduleSolution(
        solution_id=f"ortools-unavailable-{problem_digest}",
        problem_id=problem.problem_id,
        status=ScheduleStatus.NOT_SOLVED,
        infeasibilities=(
            ScheduleInfeasibility(
                code=InfeasibilityCode.SOLVER_DEPENDENCY_UNAVAILABLE,
                message=(
                    "OR-Tools is not available; CP-SAT was not run and no "
                    "feasibility claim was made."
                ),
                details=("dependency_change_required=ortools",),
            ),
        ),
        utilization=0,
        solver_metadata=SolverMetadata(
            solver_name="ortools-cp-sat",
            solver_version="not-installed",
            deterministic=True,
            dependency_name="ortools",
            dependency_available=False,
            termination_status=SolverTerminationStatus.DEPENDENCY_UNAVAILABLE,
            configuration=("solver_not_run=true",),
        ),
    )


__all__ = [
    "DEPENDENCY_CHANGE_REQUIRED",
    "ORTOOLS_AVAILABLE",
    "dependency_unavailable_solution",
    "ortools_dependency_status",
]
