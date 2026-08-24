"""Provider-free teaching decision contracts for Teacher v2."""

from .contracts import (
    PEDAGOGY_PLANNER_SCHEMA_VERSION,
    AssessmentState,
    CurrentTaskKind,
    DecisionReason,
    EvidenceRequirement,
    ExampleDepth,
    ExampleHistory,
    ExampleStrategy,
    HintDependency,
    LearnerContext,
    MasteryState,
    StudentMove,
    TeacherMove,
    TeachingBrief,
    TeachingDepth,
    TransitionLink,
)
from .pedagogy import plan_teaching

__all__ = [
    "PEDAGOGY_PLANNER_SCHEMA_VERSION",
    "AssessmentState",
    "CurrentTaskKind",
    "DecisionReason",
    "EvidenceRequirement",
    "ExampleDepth",
    "ExampleHistory",
    "ExampleStrategy",
    "HintDependency",
    "LearnerContext",
    "MasteryState",
    "StudentMove",
    "TeacherMove",
    "TeachingBrief",
    "TeachingDepth",
    "TransitionLink",
    "plan_teaching",
]
