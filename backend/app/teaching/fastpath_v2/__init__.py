"""Public contracts for Teacher Fast Path v2."""

from .models import (
    TEACHER_FASTPATH_SCHEMA_VERSION,
    DynamicTeacherTail,
    PromptIdentity,
    StableTeacherPrefix,
    TeacherPromptEnvelope,
    TeacherLatencyBudget,
    TeacherResponseMode,
    TeacherResponseModeDecision,
    TeacherResponseModeReason,
    TeacherResponseModeRequest,
)
from .execution import TeacherExecutionResult, TeacherExecutionService
from ..runtime_v2 import (
    TeacherLatencyClassification,
    TeacherResponseChunk,
    TeacherStreamReceipt,
    TeacherStreamStatus,
)
from .latency import TeacherLatencyClassifier
from .policy import TeacherResponseModeResolver
from .ports import StreamingTeacherPort, TeacherChunkObserverPort
from .prompt import (
    TeacherPromptAssemblyError,
    build_teacher_prompt_envelope,
    stable_teacher_prefix,
)
from .runtime import TeacherFastPathRuntime, run_streaming_teacher_turn
from .testing import FakeStreamingTeacherProvider, RecordingTeacherChunkObserver


__all__ = [
    "TEACHER_FASTPATH_SCHEMA_VERSION",
    "DynamicTeacherTail",
    "PromptIdentity",
    "StableTeacherPrefix",
    "TeacherPromptAssemblyError",
    "TeacherPromptEnvelope",
    "TeacherLatencyBudget",
    "TeacherLatencyClassification",
    "TeacherLatencyClassifier",
    "TeacherResponseChunk",
    "TeacherResponseMode",
    "TeacherResponseModeDecision",
    "TeacherResponseModeReason",
    "TeacherResponseModeRequest",
    "TeacherResponseModeResolver",
    "TeacherStreamReceipt",
    "TeacherStreamStatus",
    "StreamingTeacherPort",
    "TeacherChunkObserverPort",
    "TeacherFastPathRuntime",
    "TeacherExecutionResult",
    "TeacherExecutionService",
    "FakeStreamingTeacherProvider",
    "RecordingTeacherChunkObserver",
    "build_teacher_prompt_envelope",
    "stable_teacher_prefix",
    "run_streaming_teacher_turn",
]
