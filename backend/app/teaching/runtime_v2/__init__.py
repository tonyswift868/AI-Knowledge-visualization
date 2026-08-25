"""Public API for the standalone Teacher Runtime v2."""

from .models import (
    ExampleChoiceTrace,
    PedagogyDecisionTrace,
    TEACHER_RUNTIME_SCHEMA_VERSION,
    TeacherCachedInputMetadata,
    TeacherCachedInputStatus,
    TeacherDegradationAction,
    TeacherDegradationDecision,
    TeacherExecutionTrace,
    TeacherRuntimeChannel,
    TeacherRuntimeCancelledError,
    TeacherRuntimeError,
    TeacherRuntimeTrace,
    TeacherLatencyClassification,
    TeacherResponseChunk,
    TeacherStreamReceipt,
    TeacherStreamStatus,
    TeacherTurnResult,
)
from .orchestrator import run_teacher_turn


__all__ = [
    "ExampleChoiceTrace",
    "PedagogyDecisionTrace",
    "TEACHER_RUNTIME_SCHEMA_VERSION",
    "TeacherCachedInputMetadata",
    "TeacherCachedInputStatus",
    "TeacherDegradationAction",
    "TeacherDegradationDecision",
    "TeacherExecutionTrace",
    "TeacherRuntimeChannel",
    "TeacherRuntimeCancelledError",
    "TeacherRuntimeError",
    "TeacherRuntimeTrace",
    "TeacherLatencyClassification",
    "TeacherResponseChunk",
    "TeacherStreamReceipt",
    "TeacherStreamStatus",
    "TeacherTurnResult",
    "run_teacher_turn",
]
