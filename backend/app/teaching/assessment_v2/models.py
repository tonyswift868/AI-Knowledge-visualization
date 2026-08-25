"""Immutable contracts for Assessment Session v2."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import HintDependency, StudentMove
from ..evaluator_v2 import (
    EvaluationEvidence,
    EvaluationOutcome,
    EvaluationResult,
    EvaluationRubric,
    ProposedLearningEvent,
    ProposedLearningEventType,
)


ASSESSMENT_SESSION_SCHEMA_VERSION = "assessment-session-v2.v1"


class _AssessmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AssessmentSessionState(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    SUBMITTED = "submitted"
    EVALUATING = "evaluating"
    EVALUATED = "evaluated"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


class AssessmentType(str, Enum):
    PRACTICE = "practice"
    FORMATIVE = "formative"
    SUMMATIVE = "summative"
    VERIFICATION = "verification"


class VerificationStatus(str, Enum):
    QUALIFIED = "qualified"
    NOT_QUALIFIED = "not_qualified"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class AssessmentQuestion(_AssessmentModel):
    question_id: str = Field(min_length=1, max_length=160)
    concept_id: str = Field(min_length=1, max_length=240)
    prompt: str = Field(min_length=1, max_length=10_000)
    rubric_id: str = Field(min_length=1, max_length=160)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=32)
    assessment_type: AssessmentType = AssessmentType.FORMATIVE
    expires_at_ms: int | None = Field(default=None, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def validate_question_ids(self) -> "AssessmentQuestion":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("question evidence IDs must be unique")
        return self


class AssessmentSubmission(_AssessmentModel):
    submission_id: str = Field(min_length=1, max_length=160)
    attempt_id: str = Field(min_length=1, max_length=160)
    question_id: str = Field(min_length=1, max_length=160)
    rubric_id: str = Field(min_length=1, max_length=160)
    answer: str = Field(default="", max_length=30_000)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=32)
    hint_used: bool = False
    hint_ids: tuple[str, ...] = Field(default=(), max_length=16)
    hint_dependency: HintDependency = HintDependency.NONE
    retry_of_attempt_id: str | None = Field(default=None, max_length=160)
    student_move: StudentMove = StudentMove.ANSWER_ATTEMPT
    self_attested_mastery: bool = False
    submitted_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_submission_identity(self) -> "AssessmentSubmission":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("submission evidence IDs must be unique")
        if len(self.hint_ids) != len(set(self.hint_ids)):
            raise ValueError("submission hint IDs must be unique")
        if self.hint_ids and not self.hint_used:
            raise ValueError("hint IDs require hint_used=true")
        if self.retry_of_attempt_id == self.attempt_id:
            raise ValueError("an attempt cannot retry itself")
        return self


class VerificationPolicy(_AssessmentModel):
    policy_id: str = Field(
        default="verification-policy.v1",
        min_length=1,
        max_length=160,
    )
    allowed_assessment_types: tuple[AssessmentType, ...] = (
        AssessmentType.SUMMATIVE,
        AssessmentType.VERIFICATION,
    )
    minimum_question_count: int = Field(default=2, ge=2, le=20)
    minimum_rubric_coverage: float = Field(default=0.8, ge=0.0, le=1.0)
    minimum_score: float = Field(default=0.8, ge=0.0, le=1.0)
    minimum_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    require_evidence_grounding: bool = True
    allow_hint_used_attempt: bool = False
    allow_persistent_hint_dependency: bool = False
    allow_retry_attempt: bool = False


class VerificationQualification(_AssessmentModel):
    status: VerificationStatus
    policy_id: str = Field(min_length=1, max_length=160)
    assessment_type: AssessmentType
    rubric_coverage: float = Field(ge=0.0, le=1.0)
    evidence_grounded: bool
    hint_free: bool
    hint_dependency: HintDependency
    hint_dependency_clear: bool
    first_attempt: bool
    attempt_conditions_met: bool
    question_qualified: bool
    qualified_question_ids: tuple[str, ...] = Field(default=(), max_length=20)
    required_question_count: int = Field(ge=2, le=20)
    reason: str = Field(min_length=1, max_length=1_000)


class AssessmentCommitPlan(_AssessmentModel):
    schema_version: Literal["assessment-session-v2.v1"] = (
        ASSESSMENT_SESSION_SCHEMA_VERSION
    )
    plan_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=160)
    submission_id: str = Field(min_length=1, max_length=160)
    verification: VerificationQualification
    proposed_events: tuple[ProposedLearningEvent, ...] = Field(default=(), max_length=3)
    requires_commit_port: Literal[True] = True
    executed: Literal[False] = False
    reason: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_event_proposals(self) -> "AssessmentCommitPlan":
        event_ids = tuple(event.event_id for event in self.proposed_events)
        event_types = tuple(event.event_type for event in self.proposed_events)
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("commit-plan event IDs must be unique")
        if len(event_types) != len(set(event_types)):
            raise ValueError("commit-plan event types must be unique")
        independent = ProposedLearningEventType.INDEPENDENT_VERIFICATION_SIGNAL
        if independent in event_types and (
            self.verification.status is not VerificationStatus.QUALIFIED
        ):
            raise ValueError(
                "independent verification requires a qualified policy result"
            )
        return self


class AssessmentReceipt(_AssessmentModel):
    schema_version: Literal["assessment-session-v2.v1"] = (
        ASSESSMENT_SESSION_SCHEMA_VERSION
    )
    receipt_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=160)
    submission_id: str = Field(min_length=1, max_length=160)
    attempt_id: str = Field(min_length=1, max_length=160)
    question_id: str = Field(min_length=1, max_length=160)
    attempt_number: int = Field(ge=1, le=10)
    accepted: bool
    duplicate: bool = False
    conflict: bool = False
    state_before: AssessmentSessionState
    state_after: AssessmentSessionState
    evaluation_result: EvaluationResult | None = None
    verification: VerificationQualification
    commit_plan: AssessmentCommitPlan | None = None
    provider_calls: int = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=1_000)
    recorded_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_receipt(self) -> "AssessmentReceipt":
        if self.accepted and (
            self.evaluation_result is None or self.commit_plan is None
        ):
            raise ValueError("an accepted submission requires result and commit plan")
        if not self.accepted and self.commit_plan is not None:
            raise ValueError("a rejected submission cannot propose a commit plan")
        if self.commit_plan is not None and self.evaluation_result is None:
            raise ValueError("a commit plan requires an evaluation result")
        if self.evaluation_result is not None and not self.duplicate and (
            self.provider_calls != self.evaluation_result.provider_calls
        ):
            raise ValueError("receipt Provider calls must match its evaluation result")
        if self.duplicate and self.provider_calls != 0:
            raise ValueError("a duplicate replay cannot add Provider calls")
        if self.conflict and self.accepted:
            raise ValueError("a conflicting submission cannot be accepted")
        return self


class AssessmentSessionResult(_AssessmentModel):
    schema_version: Literal["assessment-session-v2.v1"] = (
        ASSESSMENT_SESSION_SCHEMA_VERSION
    )
    session_id: str = Field(min_length=1, max_length=160)
    final_state: AssessmentSessionState
    evaluated_attempts: int = Field(ge=0, le=100)
    average_score: float | None = Field(default=None, ge=0.0, le=1.0)
    final_verification: VerificationQualification
    receipt_ids: tuple[str, ...] = Field(default=(), max_length=100)
    commit_plan_ids: tuple[str, ...] = Field(default=(), max_length=100)
    completed_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_final_state(self) -> "AssessmentSessionResult":
        if self.final_state not in {
            AssessmentSessionState.COMPLETED,
            AssessmentSessionState.ABORTED,
            AssessmentSessionState.FAILED,
        }:
            raise ValueError("session result requires a terminal state")
        return self


class AssessmentSession(_AssessmentModel):
    schema_version: Literal["assessment-session-v2.v1"] = (
        ASSESSMENT_SESSION_SCHEMA_VERSION
    )
    session_id: str = Field(min_length=1, max_length=160)
    concept_id: str = Field(min_length=1, max_length=240)
    state: AssessmentSessionState = AssessmentSessionState.CREATED
    questions: tuple[AssessmentQuestion, ...] = Field(min_length=1, max_length=50)
    rubrics: tuple[EvaluationRubric, ...] = Field(min_length=1, max_length=50)
    evidence: tuple[EvaluationEvidence, ...] = Field(default=(), max_length=100)
    verification_policy: VerificationPolicy = Field(default_factory=VerificationPolicy)
    active_question_id: str | None = Field(default=None, max_length=160)
    pending_submission_id: str | None = Field(default=None, max_length=160)
    submissions: tuple[AssessmentSubmission, ...] = Field(default=(), max_length=100)
    receipts: tuple[AssessmentReceipt, ...] = Field(default=(), max_length=100)
    commit_plans: tuple[AssessmentCommitPlan, ...] = Field(default=(), max_length=100)
    state_history: tuple[AssessmentSessionState, ...] = (
        AssessmentSessionState.CREATED,
    )
    result: AssessmentSessionResult | None = None
    failure_reason: str | None = Field(default=None, max_length=1_000)
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_session_graph(self) -> "AssessmentSession":
        question_ids = tuple(item.question_id for item in self.questions)
        rubric_ids = tuple(item.rubric_id for item in self.rubrics)
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("session question IDs must be unique")
        if len(rubric_ids) != len(set(rubric_ids)):
            raise ValueError("session rubric IDs must be unique")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("session evidence IDs must be unique")
        for question in self.questions:
            if question.concept_id != self.concept_id:
                raise ValueError("question concept must match session concept")
            if question.rubric_id not in rubric_ids:
                raise ValueError("question rubric must exist in the session")
            if not set(question.evidence_ids) <= set(evidence_ids):
                raise ValueError("question evidence must exist in the session")
        if (
            self.active_question_id is not None
            and self.active_question_id not in question_ids
        ):
            raise ValueError("active question must exist in the session")
        submission_ids = tuple(item.submission_id for item in self.submissions)
        attempt_ids = tuple(item.attempt_id for item in self.submissions)
        if len(submission_ids) != len(set(submission_ids)):
            raise ValueError("recorded submission IDs must be unique")
        if len(attempt_ids) != len(set(attempt_ids)):
            raise ValueError("recorded attempt IDs must be unique")
        receipt_submissions = tuple(item.submission_id for item in self.receipts)
        if len(receipt_submissions) != len(set(receipt_submissions)):
            raise ValueError("stored receipts must be idempotent per submission")
        if self.state in {
            AssessmentSessionState.COMPLETED,
            AssessmentSessionState.ABORTED,
            AssessmentSessionState.FAILED,
        } and self.pending_submission_id is not None:
            raise ValueError("terminal sessions cannot retain pending submissions")
        inflight = {
            AssessmentSessionState.SUBMITTED,
            AssessmentSessionState.EVALUATING,
        }
        if (self.state in inflight) != (self.pending_submission_id is not None):
            raise ValueError("only in-flight sessions may retain a pending submission")
        active_states = {
            AssessmentSessionState.ACTIVE,
            AssessmentSessionState.SUBMITTED,
            AssessmentSessionState.EVALUATING,
            AssessmentSessionState.EVALUATED,
        }
        if self.state in active_states and self.active_question_id is None:
            raise ValueError("active lifecycle states require an active question")
        if not self.state_history or self.state_history[-1] is not self.state:
            raise ValueError("state history must end at the current session state")
        if (self.state is AssessmentSessionState.COMPLETED) != (
            self.result is not None
        ):
            raise ValueError("only a completed session may carry a session result")
        return self


class AssessmentSessionRestartDTO(_AssessmentModel):
    schema_version: Literal["assessment-session-v2.v1"] = (
        ASSESSMENT_SESSION_SCHEMA_VERSION
    )
    session: AssessmentSession
    exported_at_ms: int = Field(ge=0)


__all__ = [
    "ASSESSMENT_SESSION_SCHEMA_VERSION",
    "AssessmentCommitPlan",
    "AssessmentQuestion",
    "AssessmentReceipt",
    "AssessmentSession",
    "AssessmentSessionRestartDTO",
    "AssessmentSessionResult",
    "AssessmentSessionState",
    "AssessmentSubmission",
    "AssessmentType",
    "VerificationPolicy",
    "VerificationQualification",
    "VerificationStatus",
]
