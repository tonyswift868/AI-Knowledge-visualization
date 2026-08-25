"""Immutable public contracts for the standalone Teacher Runtime v2."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..composer import TeacherCitation, TeacherResponseDraft
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
    "TeacherTurnResult",
]
