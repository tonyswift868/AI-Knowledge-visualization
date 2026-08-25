"""Immutable contracts for the active-recall review runtime."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import HintDependency
from ..evaluator_v2 import EvaluationEvidence, EvaluationResult, EvaluationRubric


REVIEW_SESSION_SCHEMA_VERSION = "review-session-v1.v1"
MAX_REVIEW_ITEMS = 2_000
MAX_REVIEW_ATTEMPTS = 8_000


class _ReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewSessionState(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    EVALUATING = "evaluating"
    PAUSED = "paused"
    READY_TO_COMPLETE = "ready_to_complete"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


class ReviewRetrievalOutcome(str, Enum):
    RECALLED = "recalled"
    RECALLED_WITH_DIFFICULTY = "recalled_with_difficulty"
    PARTIAL = "partial"
    FAILED = "failed"
    HINT_DEPENDENT = "hint_dependent"


class ReviewCompletionStatus(str, Enum):
    PASSED = "passed"
    NOT_PASSED = "not_passed"


class QuestionSourcePolicy(str, Enum):
    TRUSTED_BALANCED = "trusted_balanced"
    WEAK_FIRST = "weak_first"
    CRITICAL_FIRST = "critical_first"


class ReviewWeakReason(str, Enum):
    DIFFICULT_RECALL = "difficult_recall"
    PARTIAL_RECALL = "partial_recall"
    RETRIEVAL_FAILURE = "retrieval_failure"
    HINT_DEPENDENCY = "hint_dependency"
    ANSWER_REVEALED = "answer_revealed"


class ReviewCompletionPolicy(_ReviewModel):
    policy_id: str = Field(
        default="review-completion-policy.v1",
        min_length=1,
        max_length=160,
    )
    require_all_required_items_pass: bool = True
    minimum_retrieval_score: float = Field(default=0.8, ge=0.0, le=1.0)
    require_critical_concepts_pass: bool = True
    strong_recall_min_score: float = Field(default=0.9, ge=0.0, le=1.0)
    strong_recall_max_latency_ms: int = Field(default=30_000, ge=0, le=600_000)
    recalled_with_difficulty_score: float = Field(default=0.8, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_score_order(self) -> "ReviewCompletionPolicy":
        if self.recalled_with_difficulty_score >= 1.0:
            raise ValueError("difficult recall must score below clean recall")
        return self


class ReviewSessionInput(_ReviewModel):
    concept_scope: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_REVIEW_ITEMS,
    )
    round_number: int = Field(ge=1, le=10_000)
    question_source_policy: QuestionSourcePolicy = (
        QuestionSourcePolicy.TRUSTED_BALANCED
    )
    target_item_count: int = Field(ge=1, le=MAX_REVIEW_ITEMS)
    completion_policy: ReviewCompletionPolicy = Field(
        default_factory=ReviewCompletionPolicy
    )
    max_same_round_retries: int = Field(default=1, ge=0, le=3)

    @model_validator(mode="after")
    def validate_scope(self) -> "ReviewSessionInput":
        if len(self.concept_scope) != len(set(self.concept_scope)):
            raise ValueError("review concept scope must be unique")
        return self


class ReviewMemorySignal(_ReviewModel):
    item_id: str = Field(min_length=1, max_length=160)
    concept_id: str = Field(min_length=1, max_length=240)
    weakness_score: float = Field(ge=0.0, le=1.0)
    last_outcome: ReviewRetrievalOutcome | None = None


class ReviewMemorySnapshot(_ReviewModel):
    snapshot_id: str = Field(min_length=1, max_length=160)
    concept_scope: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_REVIEW_ITEMS,
    )
    weak_signals: tuple[ReviewMemorySignal, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ATTEMPTS,
    )
    position_token: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_memory_snapshot(self) -> "ReviewMemorySnapshot":
        if len(self.concept_scope) != len(set(self.concept_scope)):
            raise ValueError("review memory scope must be unique")
        signal_ids = tuple(item.item_id for item in self.weak_signals)
        if len(signal_ids) != len(set(signal_ids)):
            raise ValueError("review memory item signals must be unique")
        if any(
            item.concept_id not in set(self.concept_scope)
            for item in self.weak_signals
        ):
            raise ValueError("review memory signal must be within snapshot scope")
        return self


class StudentProgressItem(_ReviewModel):
    concept_id: str = Field(min_length=1, max_length=240)
    critical: bool = False
    progress_score: float | None = Field(default=None, ge=0.0, le=1.0)


class StudentProgressSnapshot(_ReviewModel):
    snapshot_id: str = Field(min_length=1, max_length=160)
    concept_scope: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_REVIEW_ITEMS,
    )
    items: tuple[StudentProgressItem, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ITEMS,
    )
    position_token: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_progress_snapshot(self) -> "StudentProgressSnapshot":
        if len(self.concept_scope) != len(set(self.concept_scope)):
            raise ValueError("student progress scope must be unique")
        concept_ids = tuple(item.concept_id for item in self.items)
        if len(concept_ids) != len(set(concept_ids)):
            raise ValueError("student progress concepts must be unique")
        if not set(concept_ids) <= set(self.concept_scope):
            raise ValueError("student progress item must be within snapshot scope")
        return self


class ReviewItem(_ReviewModel):
    item_id: str = Field(min_length=1, max_length=160)
    concept_id: str = Field(min_length=1, max_length=240)
    prompt: str = Field(min_length=1, max_length=10_000)
    rubric: EvaluationRubric
    evidence: tuple[EvaluationEvidence, ...] = Field(default=(), max_length=32)
    source_id: str = Field(min_length=1, max_length=240)
    trusted: Literal[True] = True
    required: bool = True
    critical: bool = False

    @model_validator(mode="after")
    def validate_item_grounding(self) -> "ReviewItem":
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("review item evidence IDs must be unique")
        if not set(self.rubric.required_evidence_ids) <= set(evidence_ids):
            raise ValueError("review item must carry rubric-required evidence")
        return self


class ReviewQuestionSelectionRequest(_ReviewModel):
    session_id: str = Field(min_length=1, max_length=160)
    concept_scope: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_REVIEW_ITEMS,
    )
    round_number: int = Field(ge=1, le=10_000)
    source_policy: QuestionSourcePolicy
    target_item_count: int = Field(ge=1, le=MAX_REVIEW_ITEMS)
    memory: ReviewMemorySnapshot
    progress: StudentProgressSnapshot


class ReviewQuestionView(_ReviewModel):
    session_id: str = Field(min_length=1, max_length=160)
    round_id: str = Field(min_length=1, max_length=160)
    item_id: str = Field(min_length=1, max_length=160)
    concept_id: str = Field(min_length=1, max_length=240)
    prompt: str = Field(min_length=1, max_length=10_000)
    occurrence_number: int = Field(ge=1, le=4)
    required: bool
    critical: bool


class ReviewAttempt(_ReviewModel):
    submission_id: str = Field(min_length=1, max_length=160)
    attempt_id: str = Field(min_length=1, max_length=160)
    item_id: str = Field(min_length=1, max_length=160)
    answer: str = Field(default="", max_length=30_000)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=32)
    hint_used: bool = False
    hint_ids: tuple[str, ...] = Field(default=(), max_length=16)
    hint_dependency: HintDependency = HintDependency.NONE
    answer_revealed_before_submission: bool = False
    response_latency_ms: int = Field(ge=0, le=3_600_000)
    submitted_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_attempt(self) -> "ReviewAttempt":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("review attempt evidence IDs must be unique")
        if len(self.hint_ids) != len(set(self.hint_ids)):
            raise ValueError("review attempt hint IDs must be unique")
        if self.hint_ids and not self.hint_used:
            raise ValueError("hint IDs require hint_used=true")
        return self


class ReviewQueueEntry(_ReviewModel):
    entry_id: str = Field(min_length=1, max_length=64)
    item_id: str = Field(min_length=1, max_length=160)
    occurrence_number: int = Field(ge=1, le=4)
    retry_number: int = Field(ge=0, le=3)


class ReviewWeakItemProposal(_ReviewModel):
    proposal_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=160)
    round_id: str = Field(min_length=1, max_length=160)
    item_id: str = Field(min_length=1, max_length=160)
    concept_id: str = Field(min_length=1, max_length=240)
    attempt_id: str = Field(min_length=1, max_length=160)
    outcome: ReviewRetrievalOutcome
    reason: ReviewWeakReason
    same_round_retry_number: int = Field(ge=0, le=3)
    same_round_retry_eligible: bool
    requires_memory_port: Literal[True] = True
    executed: Literal[False] = False

    @model_validator(mode="after")
    def validate_weak_outcome(self) -> "ReviewWeakItemProposal":
        if self.outcome is ReviewRetrievalOutcome.RECALLED:
            raise ValueError("clean recall cannot produce a weak-item proposal")
        return self


class ReviewAttemptReceipt(_ReviewModel):
    receipt_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=160)
    round_id: str = Field(min_length=1, max_length=160)
    submission_id: str = Field(min_length=1, max_length=160)
    attempt_id: str = Field(min_length=1, max_length=160)
    item_id: str = Field(min_length=1, max_length=160)
    occurrence_number: int = Field(ge=1, le=4)
    accepted: bool
    duplicate: bool = False
    conflict: bool = False
    evaluation_result: EvaluationResult | None = None
    retrieval_outcome: ReviewRetrievalOutcome | None = None
    retrieval_score: float | None = Field(default=None, ge=0.0, le=1.0)
    weak_item_proposal: ReviewWeakItemProposal | None = None
    provider_calls: int = Field(ge=0, le=1)
    state_before: ReviewSessionState
    state_after: ReviewSessionState
    reason: str = Field(min_length=1, max_length=1_000)
    recorded_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_receipt(self) -> "ReviewAttemptReceipt":
        evaluated = (
            self.evaluation_result is not None
            and self.retrieval_outcome is not None
            and self.retrieval_score is not None
        )
        if self.accepted != evaluated:
            raise ValueError("accepted review attempts require a complete evaluation")
        if self.accepted and self.retrieval_outcome is ReviewRetrievalOutcome.RECALLED:
            if self.weak_item_proposal is not None:
                raise ValueError("clean recall cannot carry a weak-item proposal")
        elif self.accepted and self.weak_item_proposal is None:
            raise ValueError("non-clean recall requires a weak-item proposal")
        if self.evaluation_result is not None and not self.duplicate:
            if self.provider_calls != self.evaluation_result.provider_calls:
                raise ValueError("receipt Provider calls must match evaluator result")
        if self.duplicate and self.provider_calls != 0:
            raise ValueError("duplicate replay cannot add Provider calls")
        if self.conflict and self.accepted:
            raise ValueError("conflicting review attempt cannot be accepted")
        return self


class ReviewItemResult(_ReviewModel):
    item_id: str = Field(min_length=1, max_length=160)
    concept_id: str = Field(min_length=1, max_length=240)
    best_outcome: ReviewRetrievalOutcome
    retrieval_score: float = Field(ge=0.0, le=1.0)
    passed: bool
    required: bool
    critical: bool
    attempt_count: int = Field(ge=1, le=4)
    contaminated_by_answer_reveal: bool

    @model_validator(mode="after")
    def validate_item_result(self) -> "ReviewItemResult":
        passing = self.best_outcome in {
            ReviewRetrievalOutcome.RECALLED,
            ReviewRetrievalOutcome.RECALLED_WITH_DIFFICULTY,
        }
        if self.passed != (passing and not self.contaminated_by_answer_reveal):
            raise ValueError("review item pass must come from uncontaminated recall")
        if self.contaminated_by_answer_reveal and (
            self.best_outcome is not ReviewRetrievalOutcome.HINT_DEPENDENT
            or self.retrieval_score != 0.0
        ):
            raise ValueError("answer-revealed item must remain hint-dependent")
        return self


class ReviewCompletionEvaluation(_ReviewModel):
    policy_id: str = Field(min_length=1, max_length=160)
    passed: bool
    retrieval_score: float = Field(ge=0.0, le=1.0)
    required_item_ids: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ITEMS,
    )
    passed_required_item_ids: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ITEMS,
    )
    critical_concept_ids: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ITEMS,
    )
    passed_critical_concept_ids: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ITEMS,
    )
    failed_gates: tuple[str, ...] = Field(default=(), max_length=3)
    reason: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_completion(self) -> "ReviewCompletionEvaluation":
        if self.passed == bool(self.failed_gates):
            raise ValueError("review completion pass must match failed gates")
        return self


class ReviewResult(_ReviewModel):
    schema_version: Literal["review-session-v1.v1"] = REVIEW_SESSION_SCHEMA_VERSION
    session_id: str = Field(min_length=1, max_length=160)
    round_id: str = Field(min_length=1, max_length=160)
    status: ReviewCompletionStatus
    passed: bool
    completion: ReviewCompletionEvaluation
    item_results: tuple[ReviewItemResult, ...] = Field(
        min_length=1,
        max_length=MAX_REVIEW_ITEMS,
    )
    total_attempts: int = Field(ge=1, le=MAX_REVIEW_ATTEMPTS)
    evaluator_provider_calls: int = Field(ge=0, le=MAX_REVIEW_ATTEMPTS)
    teacher_provider_calls: int = Field(ge=0, le=MAX_REVIEW_ATTEMPTS)
    completed_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_result(self) -> "ReviewResult":
        expected = ReviewCompletionStatus.PASSED if self.passed else (
            ReviewCompletionStatus.NOT_PASSED
        )
        if self.status is not expected or self.completion.passed != self.passed:
            raise ValueError("review result status must match completion policy")
        if self.total_attempts != sum(
            item.attempt_count for item in self.item_results
        ):
            raise ValueError("review result attempt count must match item results")
        return self


class ReviewRoundReceipt(_ReviewModel):
    schema_version: Literal["review-session-v1.v1"] = REVIEW_SESSION_SCHEMA_VERSION
    receipt_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=160)
    round_id: str = Field(min_length=1, max_length=160)
    round_number: int = Field(ge=1, le=10_000)
    result: ReviewResult
    attempt_receipt_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_REVIEW_ATTEMPTS,
    )
    weak_proposal_ids: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ATTEMPTS,
    )
    recorded_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_round_receipt(self) -> "ReviewRoundReceipt":
        if (
            self.result.session_id != self.session_id
            or self.result.round_id != self.round_id
        ):
            raise ValueError("round receipt result binding mismatch")
        if len(self.attempt_receipt_ids) != len(set(self.attempt_receipt_ids)):
            raise ValueError("round attempt receipt IDs must be unique")
        if len(self.weak_proposal_ids) != len(set(self.weak_proposal_ids)):
            raise ValueError("round weak proposal IDs must be unique")
        return self


class TeacherExplanationRequest(_ReviewModel):
    session_id: str = Field(min_length=1, max_length=160)
    round_id: str = Field(min_length=1, max_length=160)
    item_id: str = Field(min_length=1, max_length=160)
    concept_id: str = Field(min_length=1, max_length=240)
    review_question: str = Field(min_length=1, max_length=10_000)
    learner_question: str = Field(min_length=1, max_length=5_000)


class TeacherExplanation(_ReviewModel):
    text: str = Field(min_length=1, max_length=20_000)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)
    provider_calls: int = Field(ge=0, le=1)
    reveals_answer: Literal[False] = False


class TeacherExplanationReceipt(_ReviewModel):
    receipt_id: str = Field(min_length=1, max_length=64)
    request: TeacherExplanationRequest
    available: bool
    explanation: TeacherExplanation | None = None
    provider_calls: int = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=1_000)
    recorded_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_explanation(self) -> "TeacherExplanationReceipt":
        if self.available != (self.explanation is not None):
            raise ValueError(
                "available explanation receipt requires explanation content"
            )
        if self.explanation is not None:
            if self.provider_calls != self.explanation.provider_calls:
                raise ValueError("teacher Provider calls must match explanation")
        return self


class ReviewRound(_ReviewModel):
    round_id: str = Field(min_length=1, max_length=160)
    round_number: int = Field(ge=1, le=10_000)
    items: tuple[ReviewItem, ...] = Field(
        min_length=1,
        max_length=MAX_REVIEW_ITEMS,
    )
    initial_item_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_REVIEW_ITEMS,
    )
    pending_entries: tuple[ReviewQueueEntry, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ATTEMPTS,
    )
    current_entry: ReviewQueueEntry | None = None
    attempts: tuple[ReviewAttempt, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ATTEMPTS,
    )
    attempt_receipts: tuple[ReviewAttemptReceipt, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ATTEMPTS,
    )
    weak_item_proposals: tuple[ReviewWeakItemProposal, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ATTEMPTS,
    )

    @model_validator(mode="after")
    def validate_round(self) -> "ReviewRound":
        item_ids = tuple(item.item_id for item in self.items)
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("review round item IDs must be unique")
        if item_ids != self.initial_item_ids:
            raise ValueError("initial review item order must match selected items")
        entries = (
            *((self.current_entry,) if self.current_entry is not None else ()),
            *self.pending_entries,
        )
        if any(entry.item_id not in item_ids for entry in entries):
            raise ValueError("review queue entry must reference a selected item")
        entry_ids = tuple(entry.entry_id for entry in entries)
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("active review queue entry IDs must be unique")
        submission_ids = tuple(item.submission_id for item in self.attempts)
        attempt_ids = tuple(item.attempt_id for item in self.attempts)
        if len(submission_ids) != len(set(submission_ids)):
            raise ValueError("review submission IDs must be unique")
        if len(attempt_ids) != len(set(attempt_ids)):
            raise ValueError("review attempt IDs must be unique")
        if any(attempt.item_id not in item_ids for attempt in self.attempts):
            raise ValueError("review attempt must reference a selected item")
        receipt_ids = tuple(item.receipt_id for item in self.attempt_receipts)
        receipt_submissions = tuple(
            item.submission_id for item in self.attempt_receipts
        )
        if len(receipt_ids) != len(set(receipt_ids)):
            raise ValueError("review attempt receipt IDs must be unique")
        if len(receipt_submissions) != len(set(receipt_submissions)):
            raise ValueError("stored review receipts must be idempotent")
        proposal_ids = tuple(
            item.proposal_id for item in self.weak_item_proposals
        )
        if len(proposal_ids) != len(set(proposal_ids)):
            raise ValueError("weak-item proposal IDs must be unique")
        return self


class ReviewSession(_ReviewModel):
    schema_version: Literal["review-session-v1.v1"] = REVIEW_SESSION_SCHEMA_VERSION
    session_id: str = Field(min_length=1, max_length=160)
    input: ReviewSessionInput
    state: ReviewSessionState = ReviewSessionState.CREATED
    round: ReviewRound
    memory_snapshot: ReviewMemorySnapshot
    progress_snapshot: StudentProgressSnapshot
    pending_submission_id: str | None = Field(default=None, max_length=160)
    explanation_receipts: tuple[TeacherExplanationReceipt, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ATTEMPTS,
    )
    result: ReviewResult | None = None
    round_receipt: ReviewRoundReceipt | None = None
    state_history: tuple[ReviewSessionState, ...] = (ReviewSessionState.CREATED,)
    failure_reason: str | None = Field(default=None, max_length=1_000)
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_session(self) -> "ReviewSession":
        if self.round.round_number != self.input.round_number:
            raise ValueError("round number must match review session input")
        if len(self.round.items) != self.input.target_item_count:
            raise ValueError("selected item count must match review target")
        scope = set(self.input.concept_scope)
        if set(self.memory_snapshot.concept_scope) != scope:
            raise ValueError("review memory snapshot must match session scope")
        if set(self.progress_snapshot.concept_scope) != scope:
            raise ValueError("student progress snapshot must match session scope")
        if any(item.concept_id not in scope for item in self.round.items):
            raise ValueError("review item concept must be within session scope")
        active_entries = (
            *((self.round.current_entry,) if self.round.current_entry else ()),
            *self.round.pending_entries,
        )
        if any(
            entry.retry_number > self.input.max_same_round_retries
            for entry in active_entries
        ):
            raise ValueError("review queue exceeds same-round retry policy")
        for item in self.round.items:
            item_attempts = sum(
                attempt.item_id == item.item_id
                for attempt in self.round.attempts
            )
            if item_attempts > self.input.max_same_round_retries + 1:
                raise ValueError("review attempts exceed same-round retry policy")
        current_required = {
            ReviewSessionState.ACTIVE,
            ReviewSessionState.EVALUATING,
            ReviewSessionState.PAUSED,
        }
        if (self.state in current_required) != (self.round.current_entry is not None):
            raise ValueError("active review states require one current queue entry")
        if (self.state is ReviewSessionState.EVALUATING) != (
            self.pending_submission_id is not None
        ):
            raise ValueError("only evaluating review sessions may have a submission")
        if self.state in {
            ReviewSessionState.READY_TO_COMPLETE,
            ReviewSessionState.COMPLETED,
        } and self.round.pending_entries:
            raise ValueError("finished review queue cannot retain pending entries")
        terminal = {
            ReviewSessionState.COMPLETED,
            ReviewSessionState.ABORTED,
            ReviewSessionState.FAILED,
        }
        if self.state in terminal and self.round.current_entry is not None:
            raise ValueError("terminal review session cannot retain current item")
        completed = self.state is ReviewSessionState.COMPLETED
        if completed != (self.result is not None and self.round_receipt is not None):
            raise ValueError("completed review requires result and round receipt")
        if not completed and (
            self.result is not None or self.round_receipt is not None
        ):
            raise ValueError("non-completed review cannot carry final result")
        explanation_ids = tuple(
            item.receipt_id for item in self.explanation_receipts
        )
        if len(explanation_ids) != len(set(explanation_ids)):
            raise ValueError("teacher explanation receipt IDs must be unique")
        if not self.state_history or self.state_history[-1] is not self.state:
            raise ValueError("review state history must end at current state")
        return self


class ReviewSessionCrashDTO(_ReviewModel):
    schema_version: Literal["review-session-v1.v1"] = REVIEW_SESSION_SCHEMA_VERSION
    session: ReviewSession
    exported_at_ms: int = Field(ge=0)


__all__ = [
    "MAX_REVIEW_ATTEMPTS",
    "MAX_REVIEW_ITEMS",
    "REVIEW_SESSION_SCHEMA_VERSION",
    "QuestionSourcePolicy",
    "ReviewAttempt",
    "ReviewAttemptReceipt",
    "ReviewCompletionEvaluation",
    "ReviewCompletionPolicy",
    "ReviewCompletionStatus",
    "ReviewItem",
    "ReviewItemResult",
    "ReviewMemorySignal",
    "ReviewMemorySnapshot",
    "ReviewQuestionSelectionRequest",
    "ReviewQuestionView",
    "ReviewQueueEntry",
    "ReviewResult",
    "ReviewRetrievalOutcome",
    "ReviewRound",
    "ReviewRoundReceipt",
    "ReviewSession",
    "ReviewSessionCrashDTO",
    "ReviewSessionInput",
    "ReviewSessionState",
    "ReviewWeakItemProposal",
    "ReviewWeakReason",
    "StudentProgressItem",
    "StudentProgressSnapshot",
    "TeacherExplanation",
    "TeacherExplanationReceipt",
    "TeacherExplanationRequest",
]
