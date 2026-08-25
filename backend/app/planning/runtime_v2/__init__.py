"""Immutable learning-plan runtime and explicit deterministic plan shifts."""

from .contracts import (
    AllocationMove,
    ImmutablePlanSchedule,
    LearningPlanSnapshot,
    OverdueEntryStatus,
    OverdueLedger,
    OverdueLedgerEntry,
    PlanRuntimeStatus,
    PlanShiftApplication,
    PlanShiftConfirmation,
    PlanShiftPreview,
    PlanShiftRequest,
    RuntimeTaskDefinition,
    ScheduledTaskRuntime,
    ScheduledTaskStatus,
    ShiftPreviewStatus,
    TaskCompletion,
    TaskSkip,
    Weekday,
    WeeklyLearningPreferences,
)
from .progress import PlanProgressTracker
from .runtime import PlanRuntime, build_immutable_plan_schedule
from .shift import (
    NoValidFutureLearningDay,
    PlanShiftError,
    PlanShiftService,
    ShiftNotConfirmedError,
    StaleScheduleError,
)

DEFAULT_MUTABILITY = "IMMUTABLE"
AUTO_REPLAN = False
AUTO_CP_SAT_ON_MISS = False
AUTO_LLM_ON_MISS = False
AUTO_PROVIDER_ON_MISS = False
AUTO_SHIFT_ON_MISS = False

__all__ = [
    "AUTO_CP_SAT_ON_MISS",
    "AUTO_LLM_ON_MISS",
    "AUTO_PROVIDER_ON_MISS",
    "AUTO_REPLAN",
    "AUTO_SHIFT_ON_MISS",
    "DEFAULT_MUTABILITY",
    "AllocationMove",
    "ImmutablePlanSchedule",
    "LearningPlanSnapshot",
    "NoValidFutureLearningDay",
    "OverdueEntryStatus",
    "OverdueLedger",
    "OverdueLedgerEntry",
    "PlanProgressTracker",
    "PlanRuntime",
    "PlanRuntimeStatus",
    "PlanShiftApplication",
    "PlanShiftConfirmation",
    "PlanShiftError",
    "PlanShiftPreview",
    "PlanShiftRequest",
    "PlanShiftService",
    "RuntimeTaskDefinition",
    "ScheduledTaskRuntime",
    "ScheduledTaskStatus",
    "ShiftNotConfirmedError",
    "ShiftPreviewStatus",
    "StaleScheduleError",
    "TaskCompletion",
    "TaskSkip",
    "Weekday",
    "WeeklyLearningPreferences",
    "build_immutable_plan_schedule",
]
