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
from .context_budget import (
    ContextBudgetPlan,
    ContextBudgetPlanner,
    ContextSelectionReason,
    ContextSelectionReceipt,
    TeacherContextBudget,
    TeacherContextBudgetError,
    estimate_text_tokens,
)
from .execution import TeacherExecutionResult, TeacherExecutionService
from .degradation import TeacherDegradationPolicy
from .response_length import (
    TeacherResponseLengthDecision,
    TeacherResponseLengthPolicy,
    TeacherResponseLengthReason,
)
from ..runtime_v2 import (
    TeacherCachedInputMetadata,
    TeacherCachedInputStatus,
    TeacherDegradationAction,
    TeacherDegradationDecision,
    TeacherExecutionTrace,
    TeacherLatencyClassification,
    TeacherResponseChunk,
    TeacherStreamReceipt,
    TeacherStreamStatus,
    TeacherRuntimeCancelledError,
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
    "ContextBudgetPlan",
    "ContextBudgetPlanner",
    "ContextSelectionReason",
    "ContextSelectionReceipt",
    "PromptIdentity",
    "StableTeacherPrefix",
    "TeacherPromptAssemblyError",
    "TeacherPromptEnvelope",
    "TeacherLatencyBudget",
    "TeacherContextBudget",
    "TeacherContextBudgetError",
    "TeacherCachedInputMetadata",
    "TeacherCachedInputStatus",
    "TeacherDegradationAction",
    "TeacherDegradationDecision",
    "TeacherDegradationPolicy",
    "TeacherExecutionTrace",
    "TeacherLatencyClassification",
    "TeacherLatencyClassifier",
    "TeacherResponseChunk",
    "TeacherResponseMode",
    "TeacherResponseModeDecision",
    "TeacherResponseModeReason",
    "TeacherResponseModeRequest",
    "TeacherResponseModeResolver",
    "TeacherResponseLengthDecision",
    "TeacherResponseLengthPolicy",
    "TeacherResponseLengthReason",
    "TeacherStreamReceipt",
    "TeacherStreamStatus",
    "TeacherRuntimeCancelledError",
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
    "estimate_text_tokens",
]
