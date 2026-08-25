"""Immutable contracts for misconception diagnosis and teaching recovery."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import ExampleDepth, HintDependency, StudentMove, TeacherMove
from ..examples import ExamplePlan, ExampleType


TEACHING_RECOVERY_SCHEMA_VERSION = "teaching-recovery.v1"
MAX_RECOVERY_ATTEMPTS = 4


class _RecoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnswerResult(str, Enum):
    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"
    UNKNOWN = "unknown"


class ErrorKindHint(str, Enum):
    KNOWLEDGE = "knowledge"
    PROCEDURAL = "procedural"
    CARELESS = "careless"


class DiagnosisCategory(str, Enum):
    KNOWLEDGE_GAP = "knowledge_gap"
    SPECIFIC_MISCONCEPTION = "specific_misconception"
    PROCEDURAL_ERROR = "procedural_error"
    CARELESS_ERROR = "careless_error"
    LOW_EVIDENCE = "low_evidence"
    PREREQUISITE_GAP = "prerequisite_gap"
    CONFUSION_UNRESOLVED = "confusion_unresolved"
    UNKNOWN = "unknown"


class RecoveryStrategy(str, Enum):
    REVIEW = "review"
    SIMPLIFY = "simplify"
    TARGETED_CORRECTION = "targeted_correction"
    CONCRETE_EXAMPLE = "concrete_example"
    CODE_EXAMPLE = "code_example"
    COUNTEREXAMPLE = "counterexample"
    CONNECT_PREREQUISITE = "connect_prerequisite"
    GUIDED_PRACTICE = "guided_practice"
    DELAY_ASSESSMENT = "delay_assessment"


class AnswerOutcome(_RecoveryModel):
    """Caller-owned result metadata; no grading is performed by this engine."""

    result: AnswerResult
    error_signature: str = Field(default="", max_length=500)
    error_kind_hint: ErrorKindHint | None = None
    used_hint: bool = False
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=16)


class TeachingRecoveryRequest(_RecoveryModel):
    """Bounded read-only context for one diagnosis and recovery decision."""

    student_move: StudentMove
    current_concept: str = Field(min_length=1, max_length=240)
    recent_answer_outcomes: tuple[AnswerOutcome, ...] = Field(
        default=(),
        max_length=12,
    )
    recent_teaching_moves: tuple[TeacherMove, ...] = Field(default=(), max_length=8)
    example_plan_history: tuple[ExamplePlan, ...] = Field(default=(), max_length=8)
    prerequisite_hints: tuple[str, ...] = Field(default=(), max_length=12)
    learner_level: str = Field(default="", max_length=120)
    known_misconception_hints: tuple[str, ...] = Field(default=(), max_length=12)
    hint_dependency: HintDependency = HintDependency.NONE
    recovery_attempts: int = Field(default=0, ge=0, le=20)


class MisconceptionDiagnosis(_RecoveryModel):
    schema_version: Literal["teaching-recovery.v1"] = TEACHING_RECOVERY_SCHEMA_VERSION
    focus_concept: str = Field(min_length=1, max_length=240)
    category: DiagnosisCategory
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: tuple[str, ...] = Field(default=(), max_length=24)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)
    reason: str = Field(min_length=1, max_length=1_000)
    target_misconception: str | None = Field(default=None, max_length=500)


class RecoveryTrace(_RecoveryModel):
    observed_signals: tuple[str, ...] = Field(default=(), max_length=24)
    selected_diagnosis: DiagnosisCategory
    rejected_alternatives: tuple[DiagnosisCategory, ...] = Field(
        default=(),
        max_length=7,
    )
    recovery_reason: str = Field(min_length=1, max_length=1_000)
    strategy_escalation_count: int = Field(ge=0, le=MAX_RECOVERY_ATTEMPTS)


class RecoveryPlan(_RecoveryModel):
    schema_version: Literal["teaching-recovery.v1"] = TEACHING_RECOVERY_SCHEMA_VERSION
    strategy: RecoveryStrategy
    recommended_teaching_move: TeacherMove
    recommended_example_type: ExampleType | None = None
    recommended_example_depth: ExampleDepth | None = None
    prerequisite_fallback: str | None = Field(default=None, max_length=500)
    assessment_deferred: bool = False
    assessment_recommended: bool = False
    attempt_number: int = Field(ge=1, le=MAX_RECOVERY_ATTEMPTS)
    max_attempts: Literal[4] = MAX_RECOVERY_ATTEMPTS
    attempts_exhausted: bool = False
    reason: str = Field(min_length=1, max_length=1_000)
    trace: RecoveryTrace

    @model_validator(mode="after")
    def validate_recovery_consistency(self) -> "RecoveryPlan":
        if self.assessment_deferred and self.assessment_recommended:
            raise ValueError("assessment cannot be deferred and recommended together")
        if (self.recommended_example_type is None) != (
            self.recommended_example_depth is None
        ):
            raise ValueError("example type and depth must be supplied together")
        if self.attempts_exhausted and self.attempt_number != self.max_attempts:
            raise ValueError("exhausted recovery must be at the bounded maximum")
        return self


class TeachingRecoveryDecision(_RecoveryModel):
    diagnosis: MisconceptionDiagnosis
    recovery: RecoveryPlan


__all__ = [
    "MAX_RECOVERY_ATTEMPTS",
    "TEACHING_RECOVERY_SCHEMA_VERSION",
    "AnswerOutcome",
    "AnswerResult",
    "DiagnosisCategory",
    "ErrorKindHint",
    "MisconceptionDiagnosis",
    "RecoveryPlan",
    "RecoveryStrategy",
    "RecoveryTrace",
    "TeachingRecoveryDecision",
    "TeachingRecoveryRequest",
]
