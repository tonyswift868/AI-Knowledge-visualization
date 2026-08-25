"""Immutable public contracts for the standalone Teacher Runtime v2."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..composer import FollowUpKind, TeacherCitation, TeacherResponseDraft
from ..contracts import (
    AssessmentState,
    DecisionReason,
    ExampleDepth,
    StudentMove,
    TeacherMove,
)
from ..examples import ExamplePlanReason, ExampleType
from ..student_move import ResolutionSource


TEACHER_RUNTIME_SCHEMA_VERSION = "teacher-runtime.v2"


class _RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TeacherRuntimeChannel(str, Enum):
    LEARNING = "learning"


class TeacherStreamStatus(str, Enum):
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    INVALID = "invalid"


class TeacherLatencyClassification(str, Enum):
    FAST_EXPECTED = "fast_expected"
    FAST_SLOW = "fast_slow"
    DEEP_EXPECTED = "deep_expected"
    TIMEOUT_RISK = "timeout_risk"


class TeacherResponseChunk(_RuntimeModel):
    sequence_number: int = Field(ge=0)
    text_delta: str = Field(default="", max_length=30_000)
    is_final: bool = False
    cited_evidence_ids: tuple[str, ...] = Field(default=(), max_length=32)
    follow_up_kind: FollowUpKind | None = None

    @model_validator(mode="after")
    def validate_chunk_role(self) -> "TeacherResponseChunk":
        if not self.is_final and (self.cited_evidence_ids or self.follow_up_kind):
            raise ValueError("stream metadata is allowed only on the final chunk")
        if self.is_final and self.follow_up_kind is None:
            raise ValueError("final chunk requires follow_up_kind")
        if len(self.cited_evidence_ids) != len(set(self.cited_evidence_ids)):
            raise ValueError("stream citation IDs must be unique")
        return self


class TeacherStreamReceipt(_RuntimeModel):
    response_mode: Literal["fast", "deep"]
    status: TeacherStreamStatus
    prompt_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    stable_prefix_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dynamic_tail_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    time_to_first_chunk_ms: float | None = Field(default=None, ge=0.0)
    total_response_ms: float = Field(ge=0.0)
    chunk_count: int = Field(ge=0)
    provider_wait_ms: float = Field(ge=0.0)
    composer_overhead_ms: float = Field(ge=0.0)
    provider_call_count: int = Field(ge=0, le=1)
    latency_classification: TeacherLatencyClassification
    degradation_recommendation: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_timing_and_completion(self) -> "TeacherStreamReceipt":
        epsilon = 0.001
        if self.provider_wait_ms > self.total_response_ms + epsilon:
            raise ValueError("provider wait cannot exceed total response time")
        if self.composer_overhead_ms > self.total_response_ms + epsilon:
            raise ValueError("composer overhead cannot exceed total response time")
        if self.status is TeacherStreamStatus.COMPLETED and (
            self.time_to_first_chunk_ms is None
            or self.chunk_count < 1
            or self.provider_call_count != 1
        ):
            raise ValueError("completed stream requires first chunk and one Provider call")
        return self


class ExampleChoiceTrace(_RuntimeModel):
    example_type: ExampleType
    depth: ExampleDepth
    reason: ExamplePlanReason
    semantic_family: str = Field(min_length=1, max_length=160)
    target_misconception: str | None = Field(default=None, max_length=500)


class PedagogyDecisionTrace(_RuntimeModel):
    teaching_move: TeacherMove
    supporting_moves: tuple[TeacherMove, ...] = Field(max_length=2)
    reason: DecisionReason
    transition_recommended: bool
    assessment_recommended: bool
    preserve_current_learning_position: bool


class TeacherRuntimeTrace(_RuntimeModel):
    """Explainable receipt data without learner prose, credentials, or secrets."""

    schema_version: Literal["teacher-runtime.v2"] = TEACHER_RUNTIME_SCHEMA_VERSION
    student_move: StudentMove
    student_move_source: ResolutionSource
    student_move_confidence: float = Field(ge=0.0, le=1.0)
    student_move_ambiguous: bool
    student_move_reason: str = Field(min_length=1, max_length=1_000)
    pedagogy_decision: PedagogyDecisionTrace
    example_choice: ExampleChoiceTrace | None
    provider_call_count: int = Field(ge=0, le=1)
    evidence_ids: tuple[str, ...] = Field(max_length=32)
    assessment_state: AssessmentState
    elapsed_ms: float = Field(ge=0.0)
    stream_receipt: TeacherStreamReceipt | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


class TeacherTurnResult(_RuntimeModel):
    """Expression-only result; deliberately contains no state write command."""

    response_draft: TeacherResponseDraft
    teaching_move: TeacherMove
    citations_metadata: tuple[TeacherCitation, ...] = Field(max_length=32)
    transition_recommendation: bool
    assessment_recommendation: bool
    self_attested_signal: bool
    runtime_trace: TeacherRuntimeTrace


class TeacherRuntimeError(Exception):
    """Fail-closed runtime error with an optional safe decision trace."""

    def __init__(
        self,
        code: str,
        *,
        trace: TeacherRuntimeTrace | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.trace = trace
        self.details = details or {}


__all__ = [
    "ExampleChoiceTrace",
    "PedagogyDecisionTrace",
    "TEACHER_RUNTIME_SCHEMA_VERSION",
    "TeacherRuntimeChannel",
    "TeacherRuntimeError",
    "TeacherRuntimeTrace",
    "TeacherLatencyClassification",
    "TeacherResponseChunk",
    "TeacherStreamReceipt",
    "TeacherStreamStatus",
    "TeacherTurnResult",
]
