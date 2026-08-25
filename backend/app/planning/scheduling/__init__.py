"""Constraint-scheduler contracts awaiting an OR-Tools dependency lease."""

from .availability import (
    DEPENDENCY_CHANGE_REQUIRED,
    ORTOOLS_AVAILABLE,
    dependency_unavailable_solution,
    ortools_dependency_status,
)
from .benchmark import (
    ScheduleBenchmarkResult,
    ScheduleSolver,
    run_schedule_benchmark,
)
from .contracts import (
    PROBLEM_SCHEMA_VERSION,
    REPLAN_SCHEMA_VERSION,
    SOLUTION_SCHEMA_VERSION,
    AllocationKind,
    CompletedTask,
    ConstraintViolation,
    ConstraintViolationCode,
    DailyCapacity,
    DailyLoad,
    EffortIntervalPolicy,
    FinalBuffer,
    InfeasibilityCode,
    KnowledgeTask,
    PlanScheduleProblem,
    PlanScheduleSolution,
    PrerequisiteEdge,
    ReviewWorkload,
    ScheduleAllocation,
    ScheduleDependencyStatus,
    ScheduleInfeasibility,
    ScheduleSolveConfig,
    ScheduleStatus,
    SolutionValidation,
    SolverMetadata,
    SolverTerminationStatus,
    TaskSplitPolicy,
)
from .replan import (
    DailyLoadChange,
    DeadlineImpact,
    MovedTask,
    ReplanDelta,
    build_replan_delta,
)
from .validation import (
    build_daily_loads,
    calculate_utilization,
    explain_obvious_infeasibility,
    validate_solution,
)
from .validator import ScheduleValidationError, ScheduleValidator

if ORTOOLS_AVAILABLE:
    from .solver import ORToolsScheduleSolver, SOLVER_VERSION
else:
    ORToolsScheduleSolver = None  # type: ignore[assignment]
    SOLVER_VERSION = None

__all__ = [
    "DEPENDENCY_CHANGE_REQUIRED",
    "ORTOOLS_AVAILABLE",
    "ORToolsScheduleSolver",
    "PROBLEM_SCHEMA_VERSION",
    "REPLAN_SCHEMA_VERSION",
    "SOLUTION_SCHEMA_VERSION",
    "SOLVER_VERSION",
    "AllocationKind",
    "CompletedTask",
    "ConstraintViolation",
    "ConstraintViolationCode",
    "DailyCapacity",
    "DailyLoad",
    "DailyLoadChange",
    "DeadlineImpact",
    "EffortIntervalPolicy",
    "FinalBuffer",
    "InfeasibilityCode",
    "KnowledgeTask",
    "MovedTask",
    "PlanScheduleProblem",
    "PlanScheduleSolution",
    "PrerequisiteEdge",
    "ReplanDelta",
    "ReviewWorkload",
    "ScheduleAllocation",
    "ScheduleBenchmarkResult",
    "ScheduleDependencyStatus",
    "ScheduleInfeasibility",
    "ScheduleSolveConfig",
    "ScheduleSolver",
    "ScheduleStatus",
    "ScheduleValidationError",
    "ScheduleValidator",
    "SolutionValidation",
    "SolverMetadata",
    "SolverTerminationStatus",
    "TaskSplitPolicy",
    "build_daily_loads",
    "build_replan_delta",
    "calculate_utilization",
    "dependency_unavailable_solution",
    "explain_obvious_infeasibility",
    "ortools_dependency_status",
    "run_schedule_benchmark",
    "validate_solution",
]
