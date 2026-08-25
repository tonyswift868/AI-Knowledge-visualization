"""Immutable contracts for Evaluator v2 and learning-event proposals."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import StudentMove


EVALUATOR_V2_SCHEMA_VERSION = "evaluator-v2.v1"


class _EvaluatorModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationPath(str, Enum):
    OBJECTIVE = "objective"
    SEMANTIC = "semantic"


class EvaluationOutcome(str, Enum):
    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    AMBIGUOUS = "ambiguous"
    INVALID_ATTEMPT = "invalid_attempt"


class SemanticVerdict(str, Enum):
    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"


class ProposedLearningEventType(str, Enum):
    ANSWER_CORRECT = "answer_correct"
    ANSWER_PARTIAL = "answer_partial"
    ANSWER_INCORRECT = "answer_incorrect"
    ASSESSMENT_RESULT = "assessment_result"
    INDEPENDENT_VERIFICATION_SIGNAL = "independent_verification_signal"


class AssessmentAttempt(_EvaluatorModel):
    assessment_id: str = Field(min_length=1, max_length=160)
    attempt_id: str = Field(min_length=1, max_length=160)
    question: str = Field(min_length=1, max_length=10_000)
    answer: str = Field(default="", max_length=30_000)
    student_move: StudentMove = StudentMove.ANSWER_ATTEMPT
    self_attested_mastery: bool = False
    answer_evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)


class RubricCriterion(_EvaluatorModel):
    criterion_id: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=2_000)
    weight: float = Field(gt=0.0, le=1.0)
    required: bool = True


class MisconceptionAnswer(_EvaluatorModel):
    answer: str = Field(min_length=1, max_length=10_000)
    misconception: str = Field(min_length=1, max_length=500)


class EvaluationRubric(_EvaluatorModel):
    rubric_id: str = Field(min_length=1, max_length=160)
    path: EvaluationPath
    accepted_answers: tuple[str, ...] = Field(default=(), max_length=32)
    partial_answers: tuple[str, ...] = Field(default=(), max_length=32)
    misconception_answers: tuple[MisconceptionAnswer, ...] = Field(
        default=(),
        max_length=32,
    )
    criteria: tuple[RubricCriterion, ...] = Field(default=(), max_length=24)
    required_evidence_ids: tuple[str, ...] = Field(default=(), max_length=32)
    passing_score: float = Field(default=0.8, ge=0.0, le=1.0)
    partial_credit: float = Field(default=0.5, ge=0.0, le=1.0)


class EvaluationEvidence(_EvaluatorModel):
    evidence_id: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=30_000)
    source_reference: str | None = Field(default=None, max_length=2_000)
    verified: bool = True


class EvaluatorRequest(_EvaluatorModel):
    attempt: AssessmentAttempt
    rubric: EvaluationRubric
    evidence: tuple[EvaluationEvidence, ...] = Field(default=(), max_length=32)
    processed_assessment_ids: tuple[str, ...] = Field(default=(), max_length=10_000)


class RubricCriterionResult(_EvaluatorModel):
    criterion_id: str = Field(min_length=1, max_length=160)
    satisfied: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=1_000)


class SemanticEvaluationDraft(_EvaluatorModel):
    """Strict set of fields the semantic evaluator port may return."""

    outcome: SemanticVerdict
    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=2_000)
    cited_evidence_ids: tuple[str, ...] = Field(default=(), max_length=32)
    criterion_results: tuple[RubricCriterionResult, ...] = Field(
        default=(),
        max_length=24,
    )


class SemanticEvaluationPayload(_EvaluatorModel):
    assessment_id: str = Field(min_length=1, max_length=160)
    question: str = Field(min_length=1, max_length=10_000)
    answer: str = Field(min_length=1, max_length=30_000)
    rubric_id: str = Field(min_length=1, max_length=160)
    criteria: tuple[RubricCriterion, ...] = Field(min_length=1, max_length=24)
    evidence: tuple[EvaluationEvidence, ...] = Field(default=(), max_length=32)


class EvaluationResult(_EvaluatorModel):
    schema_version: Literal["evaluator-v2.v1"] = EVALUATOR_V2_SCHEMA_VERSION
    assessment_id: str = Field(min_length=1, max_length=160)
    attempt_id: str = Field(min_length=1, max_length=160)
    rubric_id: str = Field(min_length=1, max_length=160)
    path: EvaluationPath
    outcome: EvaluationOutcome
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=2_000)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)
    rubric_signals: tuple[str, ...] = Field(default=(), max_length=32)
    criterion_results: tuple[RubricCriterionResult, ...] = Field(
        default=(),
        max_length=24,
    )
    provider_calls: int = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_result_consistency(self) -> "EvaluationResult":
        scored = {
            EvaluationOutcome.CORRECT,
            EvaluationOutcome.PARTIAL,
            EvaluationOutcome.INCORRECT,
        }
        if (self.outcome in scored) != (self.score is not None):
            raise ValueError("only evaluated answer outcomes may carry a score")
        if self.path is EvaluationPath.OBJECTIVE and self.provider_calls != 0:
            raise ValueError("objective evaluation cannot call a Provider")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("evaluation evidence references must be unique")
        return self


class ProposedLearningEvent(_EvaluatorModel):
    event_id: str = Field(min_length=1, max_length=160)
    event_type: ProposedLearningEventType
    assessment_id: str = Field(min_length=1, max_length=160)
    evaluation_outcome: EvaluationOutcome
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)
    source: Literal["evaluator_v2"] = "evaluator_v2"


class EvaluatorDecision(_EvaluatorModel):
    schema_version: Literal["evaluator-v2.v1"] = EVALUATOR_V2_SCHEMA_VERSION
    idempotency_key: str = Field(min_length=1, max_length=64)
    result: EvaluationResult
    proposed_learning_events: tuple[ProposedLearningEvent, ...] = Field(
        default=(),
        max_length=3,
    )

    @model_validator(mode="after")
    def validate_unique_event_proposals(self) -> "EvaluatorDecision":
        ids = tuple(event.event_id for event in self.proposed_learning_events)
        if len(ids) != len(set(ids)):
            raise ValueError("proposed learning event IDs must be unique")
        return self


__all__ = [
    "EVALUATOR_V2_SCHEMA_VERSION",
    "AssessmentAttempt",
    "EvaluationEvidence",
    "EvaluationOutcome",
    "EvaluationPath",
    "EvaluationResult",
    "EvaluationRubric",
    "EvaluatorDecision",
    "EvaluatorRequest",
    "MisconceptionAnswer",
    "ProposedLearningEvent",
    "ProposedLearningEventType",
    "RubricCriterion",
    "RubricCriterionResult",
    "SemanticEvaluationDraft",
    "SemanticEvaluationPayload",
    "SemanticVerdict",
]
