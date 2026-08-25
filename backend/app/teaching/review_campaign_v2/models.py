"""Immutable contracts for fixed-round review campaigns."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..review_v1 import (
    MAX_REVIEW_ATTEMPTS,
    MAX_REVIEW_ITEMS,
    QuestionSourcePolicy,
    ReviewAttemptReceipt,
    ReviewCompletionPolicy,
    ReviewRetrievalOutcome,
    ReviewRoundReceipt,
    ReviewSession,
)


REVIEW_CAMPAIGN_SCHEMA_VERSION = "review-campaign-v2.v1"
MAX_CAMPAIGN_ROUNDS = 20
MAX_CAMPAIGN_EXPOSURES = MAX_REVIEW_ATTEMPTS * MAX_CAMPAIGN_ROUNDS * 5


class _CampaignModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewCampaignState(str, Enum):
    CREATED = "created"
    ROUND_ACTIVE = "round_active"
    EVALUATING = "evaluating"
    ROUND_EVALUATED = "round_evaluated"
    PAUSED = "paused"
    BETWEEN_ROUNDS = "between_rounds"
    RECOVERY_REQUIRED = "recovery_required"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


class ReviewRoundState(str, Enum):
    ACTIVE = "active"
    EVALUATING = "evaluating"
    PAUSED = "paused"
    EVALUATED = "evaluated"
    PASSED = "passed"
    NOT_PASSED = "not_passed"
    RECOVERY_REQUIRED = "recovery_required"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewBudgetAction(str, Enum):
    SEMANTIC_EVALUATION = "semantic_evaluation"
    TEACHER_EXPLANATION = "teacher_explanation"
    FAIL_FEEDBACK = "fail_feedback"


class ReviewFeedbackMode(str, Enum):
    LOCAL_MINIMAL = "local_minimal"
    TEACHER_ON_FAILED = "teacher_on_failed"
    TEACHER_ON_PARTIAL_OR_FAILED = "teacher_on_partial_or_failed"


class ReviewFeedbackSource(str, Enum):
    LOCAL = "local"
    TEACHER = "teacher"
    BUDGET_DENIED = "budget_denied"


class ReviewExposureEventType(str, Enum):
    QUESTION_SHOWN = "question_shown"
    ATTEMPT_SUBMITTED = "attempt_submitted"
    ANSWER_REVEALED = "answer_revealed"
    HINT_USED = "hint_used"
    RESULT_RECORDED = "result_recorded"


class ReviewCampaignFeedbackPolicy(_CampaignModel):
    policy_id: str = Field(
        default="review-campaign-feedback.v1",
        min_length=1,
        max_length=160,
    )
    mode: ReviewFeedbackMode = ReviewFeedbackMode.LOCAL_MINIMAL
    max_teacher_feedback_calls_per_round: int = Field(default=1, ge=0, le=50)


class ReviewCampaignConfig(_CampaignModel):
    concept_scope: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_REVIEW_ITEMS,
    )
    fixed_round_count: int = Field(ge=1, le=MAX_CAMPAIGN_ROUNDS)
    target_item_count: int = Field(ge=1, le=MAX_REVIEW_ITEMS)
    question_source_policy: QuestionSourcePolicy = (
        QuestionSourcePolicy.TRUSTED_BALANCED
    )
    completion_policy: ReviewCompletionPolicy = Field(
        default_factory=ReviewCompletionPolicy
    )
    max_same_round_retries: int = Field(default=1, ge=0, le=3)
    feedback_policy: ReviewCampaignFeedbackPolicy = Field(
        default_factory=ReviewCampaignFeedbackPolicy
    )

    @model_validator(mode="after")
    def validate_scope(self) -> "ReviewCampaignConfig":
        if len(self.concept_scope) != len(set(self.concept_scope)):
            raise ValueError("campaign concept scope must be unique")
        return self


class ReviewDuePriorityItem(_CampaignModel):
    item_id: str = Field(min_length=1, max_length=160)
    concept_id: str = Field(min_length=1, max_length=240)
    due: bool
    priority: float = Field(ge=0.0, le=1.0)
    critical: bool = False
    last_outcome: ReviewRetrievalOutcome | None = None


class ReviewDuePriorityRequest(_CampaignModel):
    campaign_id: str = Field(min_length=1, max_length=160)
    round_number: int = Field(ge=1, le=MAX_CAMPAIGN_ROUNDS)
    fixed_round_count: int = Field(ge=1, le=MAX_CAMPAIGN_ROUNDS)
    concept_scope: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_REVIEW_ITEMS,
    )
    previously_shown_item_ids: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ATTEMPTS * MAX_CAMPAIGN_ROUNDS,
    )


class ReviewDuePrioritySnapshot(_CampaignModel):
    snapshot_id: str = Field(min_length=1, max_length=160)
    position_token: str = Field(min_length=1, max_length=240)
    items: tuple[ReviewDuePriorityItem, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ITEMS,
    )
    fsrs_suggested_round_count: int | None = Field(
        default=None,
        ge=1,
        le=10_000,
    )

    @model_validator(mode="after")
    def validate_items(self) -> "ReviewDuePrioritySnapshot":
        item_ids = tuple(item.item_id for item in self.items)
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("due-priority item IDs must be unique")
        return self


class ReviewObservation(_CampaignModel):
    item_id: str = Field(min_length=1, max_length=160)
    concept_id: str = Field(min_length=1, max_length=240)
    outcome: ReviewRetrievalOutcome
    retrieval_score: float = Field(ge=0.0, le=1.0)
    weak: bool
    hint_dependent: bool
    answer_revealed: bool
    critical: bool


class ReviewObservationProposal(_CampaignModel):
    proposal_id: str = Field(min_length=1, max_length=64)
    campaign_id: str = Field(min_length=1, max_length=160)
    round_number: int = Field(ge=1, le=MAX_CAMPAIGN_ROUNDS)
    source_round_receipt_id: str = Field(min_length=1, max_length=64)
    observations: tuple[ReviewObservation, ...] = Field(
        min_length=1,
        max_length=MAX_REVIEW_ITEMS,
    )
    idempotency_key: str = Field(min_length=1, max_length=64)
    requires_memory_adapter: Literal[True] = True
    executed: Literal[False] = False

    @model_validator(mode="after")
    def validate_observations(self) -> "ReviewObservationProposal":
        item_ids = tuple(item.item_id for item in self.observations)
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("review observations must be unique per item")
        return self


class ReviewObservationProposalReceipt(_CampaignModel):
    proposal_id: str = Field(min_length=1, max_length=64)
    accepted: bool
    duplicate: bool = False
    position_token: str = Field(min_length=1, max_length=240)
    reason: str = Field(min_length=1, max_length=1_000)


class ReviewExecutionBudgetRequest(_CampaignModel):
    request_id: str = Field(min_length=1, max_length=64)
    campaign_id: str = Field(min_length=1, max_length=160)
    round_number: int = Field(ge=1, le=MAX_CAMPAIGN_ROUNDS)
    action: ReviewBudgetAction
    idempotency_key: str = Field(min_length=1, max_length=160)
    maximum_provider_calls: Literal[1] = 1


class ReviewExecutionBudgetDecision(_CampaignModel):
    request_id: str = Field(min_length=1, max_length=64)
    action: ReviewBudgetAction
    allowed: bool
    provider_calls_reserved: int = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_reservation(self) -> "ReviewExecutionBudgetDecision":
        if self.allowed != (self.provider_calls_reserved == 1):
            raise ValueError("budget approval must reserve exactly one Provider call")
        return self


class ReviewQuestionExposure(_CampaignModel):
    exposure_id: str = Field(min_length=1, max_length=64)
    campaign_id: str = Field(min_length=1, max_length=160)
    round_number: int = Field(ge=1, le=MAX_CAMPAIGN_ROUNDS)
    item_id: str = Field(min_length=1, max_length=160)
    occurrence_number: int = Field(ge=1, le=4)
    event_type: ReviewExposureEventType
    submission_id: str | None = Field(default=None, max_length=160)
    attempt_id: str | None = Field(default=None, max_length=160)
    retrieval_outcome: ReviewRetrievalOutcome | None = None
    recorded_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_event(self) -> "ReviewQuestionExposure":
        attempt_event = self.event_type in {
            ReviewExposureEventType.ATTEMPT_SUBMITTED,
            ReviewExposureEventType.ANSWER_REVEALED,
            ReviewExposureEventType.HINT_USED,
            ReviewExposureEventType.RESULT_RECORDED,
        }
        if attempt_event and (
            self.submission_id is None or self.attempt_id is None
        ):
            raise ValueError("attempt exposure event requires submission identity")
        if (
            self.event_type is ReviewExposureEventType.RESULT_RECORDED
        ) != (self.retrieval_outcome is not None):
            raise ValueError("only result exposure events carry retrieval outcome")
        return self


class ReviewQuestionExposureLedger(_CampaignModel):
    events: tuple[ReviewQuestionExposure, ...] = Field(
        default=(),
        max_length=MAX_CAMPAIGN_EXPOSURES,
    )

    @model_validator(mode="after")
    def validate_ledger(self) -> "ReviewQuestionExposureLedger":
        event_ids = tuple(event.exposure_id for event in self.events)
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("question exposure IDs must be unique")
        return self


class ReviewFeedbackReceipt(_CampaignModel):
    feedback_id: str = Field(min_length=1, max_length=64)
    campaign_id: str = Field(min_length=1, max_length=160)
    round_number: int = Field(ge=1, le=MAX_CAMPAIGN_ROUNDS)
    item_id: str = Field(min_length=1, max_length=160)
    attempt_id: str | None = Field(default=None, max_length=160)
    explicit_request: bool
    source: ReviewFeedbackSource
    text: str = Field(min_length=1, max_length=20_000)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)
    provider_calls: int = Field(ge=0, le=1)
    budget_request_id: str | None = Field(default=None, max_length=64)
    reveals_answer: Literal[False] = False
    recorded_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_feedback(self) -> "ReviewFeedbackReceipt":
        if self.source is ReviewFeedbackSource.TEACHER:
            if self.provider_calls != 1 or self.budget_request_id is None:
                raise ValueError("teacher feedback requires budgeted Provider call")
        elif self.provider_calls != 0:
            raise ValueError("local or denied feedback cannot count Provider calls")
        return self


class ReviewCampaignAttemptReceipt(_CampaignModel):
    attempt_receipt: ReviewAttemptReceipt
    feedback: ReviewFeedbackReceipt | None = None
    budget_request_ids: tuple[str, ...] = Field(default=(), max_length=2)
    campaign_state_after: ReviewCampaignState


class ReviewRoundAnalytics(_CampaignModel):
    round_number: int = Field(ge=1, le=MAX_CAMPAIGN_ROUNDS)
    retrieval_score: float = Field(ge=0.0, le=1.0)
    weak_count: int = Field(ge=0, le=MAX_REVIEW_ATTEMPTS)
    hint_rate: float = Field(ge=0.0, le=1.0)
    provider_call_count: int = Field(ge=0, le=MAX_REVIEW_ATTEMPTS * 2)
    question_show_count: int = Field(ge=1, le=MAX_REVIEW_ATTEMPTS)
    question_reuse_count: int = Field(ge=0, le=MAX_REVIEW_ATTEMPTS)
    critical_pass: bool
    duration_ms: int = Field(ge=0)


class ReviewCampaignAnalytics(_CampaignModel):
    retrieval_score: float = Field(ge=0.0, le=1.0)
    weak_count: int = Field(ge=0, le=MAX_REVIEW_ATTEMPTS * MAX_CAMPAIGN_ROUNDS)
    hint_rate: float = Field(ge=0.0, le=1.0)
    provider_call_count: int = Field(
        ge=0,
        le=MAX_REVIEW_ATTEMPTS * MAX_CAMPAIGN_ROUNDS * 2,
    )
    question_show_count: int = Field(
        ge=1,
        le=MAX_REVIEW_ATTEMPTS * MAX_CAMPAIGN_ROUNDS,
    )
    unique_question_count: int = Field(
        ge=1,
        le=MAX_REVIEW_ITEMS * MAX_CAMPAIGN_ROUNDS,
    )
    question_reuse_count: int = Field(
        ge=0,
        le=MAX_REVIEW_ATTEMPTS * MAX_CAMPAIGN_ROUNDS,
    )
    same_round_reuse_count: int = Field(
        ge=0,
        le=MAX_REVIEW_ATTEMPTS * MAX_CAMPAIGN_ROUNDS,
    )
    critical_pass: bool
    round_durations_ms: tuple[int, ...] = Field(
        min_length=1,
        max_length=MAX_CAMPAIGN_ROUNDS,
    )


class ReviewCampaignRound(_CampaignModel):
    round_number: int = Field(ge=1, le=MAX_CAMPAIGN_ROUNDS)
    state: ReviewRoundState
    review_session: ReviewSession
    round_receipt: ReviewRoundReceipt | None = None
    observation_proposal: ReviewObservationProposal | None = None
    observation_receipt: ReviewObservationProposalReceipt | None = None
    analytics: ReviewRoundAnalytics | None = None
    state_history: tuple[ReviewRoundState, ...] = Field(
        default=(ReviewRoundState.ACTIVE,),
        max_length=MAX_REVIEW_ATTEMPTS * 2 + 10,
    )
    started_at_ms: int = Field(ge=0)
    completed_at_ms: int | None = Field(default=None, ge=0)
    recovery_count: int = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def validate_round(self) -> "ReviewCampaignRound":
        if self.review_session.input.round_number != self.round_number:
            raise ValueError("campaign round must bind matching review session")
        completed = self.state is ReviewRoundState.COMPLETED
        artifacts = (
            self.round_receipt,
            self.observation_proposal,
            self.observation_receipt,
            self.analytics,
            self.completed_at_ms,
        )
        if completed != all(item is not None for item in artifacts):
            raise ValueError("completed campaign round requires all final artifacts")
        if not completed and any(item is not None for item in artifacts):
            raise ValueError("unfinished campaign round cannot carry final artifacts")
        if completed:
            if self.review_session.state.value != "completed":
                raise ValueError("completed campaign round requires completed session")
            if self.round_receipt.result.session_id != self.review_session.session_id:
                raise ValueError("campaign round receipt session binding mismatch")
            if self.observation_proposal.round_number != self.round_number:
                raise ValueError("campaign observation round binding mismatch")
            if (
                self.observation_receipt.proposal_id
                != self.observation_proposal.proposal_id
            ):
                raise ValueError("campaign observation receipt binding mismatch")
            expected_evaluation = (
                ReviewRoundState.PASSED
                if self.round_receipt.result.passed
                else ReviewRoundState.NOT_PASSED
            )
            if expected_evaluation not in self.state_history:
                raise ValueError("round history must record pass or not-pass")
            if self.completed_at_ms < self.started_at_ms:
                raise ValueError("campaign round duration cannot be negative")
        if not self.state_history or self.state_history[-1] is not self.state:
            raise ValueError("round state history must end at current state")
        return self


class ReviewCampaignReceipt(_CampaignModel):
    schema_version: Literal["review-campaign-v2.v1"] = (
        REVIEW_CAMPAIGN_SCHEMA_VERSION
    )
    receipt_id: str = Field(min_length=1, max_length=64)
    campaign_id: str = Field(min_length=1, max_length=160)
    fixed_round_count: int = Field(ge=1, le=MAX_CAMPAIGN_ROUNDS)
    completed_round_count: int = Field(ge=1, le=MAX_CAMPAIGN_ROUNDS)
    passed_round_count: int = Field(ge=0, le=MAX_CAMPAIGN_ROUNDS)
    all_rounds_passed: bool
    round_receipt_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_CAMPAIGN_ROUNDS,
    )
    analytics: ReviewCampaignAnalytics
    completed_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_receipt(self) -> "ReviewCampaignReceipt":
        if self.completed_round_count != self.fixed_round_count:
            raise ValueError("campaign receipt requires every fixed round")
        if len(self.round_receipt_ids) != self.completed_round_count:
            raise ValueError("campaign receipt round IDs must match completed rounds")
        if self.all_rounds_passed != (
            self.passed_round_count == self.fixed_round_count
        ):
            raise ValueError("all-round pass flag must match passed round count")
        return self


class ReviewCampaign(_CampaignModel):
    schema_version: Literal["review-campaign-v2.v1"] = (
        REVIEW_CAMPAIGN_SCHEMA_VERSION
    )
    campaign_id: str = Field(min_length=1, max_length=160)
    config: ReviewCampaignConfig
    state: ReviewCampaignState = ReviewCampaignState.CREATED
    current_round_number: int = Field(default=0, ge=0, le=MAX_CAMPAIGN_ROUNDS)
    rounds: tuple[ReviewCampaignRound, ...] = Field(
        default=(),
        max_length=MAX_CAMPAIGN_ROUNDS,
    )
    exposure_ledger: ReviewQuestionExposureLedger = Field(
        default_factory=ReviewQuestionExposureLedger
    )
    budget_decisions: tuple[ReviewExecutionBudgetDecision, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ATTEMPTS * MAX_CAMPAIGN_ROUNDS * 2,
    )
    feedback_receipts: tuple[ReviewFeedbackReceipt, ...] = Field(
        default=(),
        max_length=MAX_REVIEW_ATTEMPTS * MAX_CAMPAIGN_ROUNDS,
    )
    pending_submission_id: str | None = Field(default=None, max_length=160)
    analytics: ReviewCampaignAnalytics | None = None
    receipt: ReviewCampaignReceipt | None = None
    state_history: tuple[ReviewCampaignState, ...] = (
        ReviewCampaignState.CREATED,
    )
    failure_reason: str | None = Field(default=None, max_length=1_000)
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_campaign(self) -> "ReviewCampaign":
        if len(self.rounds) > self.config.fixed_round_count:
            raise ValueError("campaign cannot exceed fixed round count")
        round_numbers = tuple(item.round_number for item in self.rounds)
        if round_numbers != tuple(range(1, len(self.rounds) + 1)):
            raise ValueError("campaign round numbers must be contiguous")
        if self.current_round_number != len(self.rounds):
            raise ValueError("current campaign round must match created rounds")
        evaluating = self.state is ReviewCampaignState.EVALUATING
        if evaluating != (self.pending_submission_id is not None):
            raise ValueError("only evaluating campaign may retain submission")
        completed = self.state is ReviewCampaignState.COMPLETED
        if completed != (self.analytics is not None and self.receipt is not None):
            raise ValueError("completed campaign requires analytics and receipt")
        if not completed and (self.analytics is not None or self.receipt is not None):
            raise ValueError("unfinished campaign cannot carry final receipt")
        if completed and len(self.rounds) != self.config.fixed_round_count:
            raise ValueError("campaign cannot complete before every fixed round")
        state_pairs = {
            ReviewCampaignState.ROUND_ACTIVE: ReviewRoundState.ACTIVE,
            ReviewCampaignState.EVALUATING: ReviewRoundState.EVALUATING,
            ReviewCampaignState.ROUND_EVALUATED: ReviewRoundState.EVALUATED,
            ReviewCampaignState.PAUSED: ReviewRoundState.PAUSED,
            ReviewCampaignState.RECOVERY_REQUIRED: (
                ReviewRoundState.RECOVERY_REQUIRED
            ),
            ReviewCampaignState.BETWEEN_ROUNDS: ReviewRoundState.COMPLETED,
        }
        if self.state in state_pairs:
            if not self.rounds or self.rounds[-1].state is not state_pairs[self.state]:
                raise ValueError("campaign and current round states must agree")
        if completed and any(
            review_round.state is not ReviewRoundState.COMPLETED
            for review_round in self.rounds
        ):
            raise ValueError("completed campaign requires completed rounds")
        if self.receipt is not None:
            if self.receipt.campaign_id != self.campaign_id:
                raise ValueError("campaign receipt identity mismatch")
            if self.receipt.analytics != self.analytics:
                raise ValueError("campaign receipt analytics mismatch")
        budget_ids = tuple(item.request_id for item in self.budget_decisions)
        if len(budget_ids) != len(set(budget_ids)):
            raise ValueError("campaign budget decisions must be idempotent")
        feedback_ids = tuple(item.feedback_id for item in self.feedback_receipts)
        if len(feedback_ids) != len(set(feedback_ids)):
            raise ValueError("campaign feedback receipts must be unique")
        if any(
            feedback.budget_request_id is not None
            and feedback.budget_request_id not in set(budget_ids)
            for feedback in self.feedback_receipts
        ):
            raise ValueError("campaign feedback must bind a stored budget decision")
        if any(
            event.campaign_id != self.campaign_id
            or event.round_number > self.current_round_number
            for event in self.exposure_ledger.events
        ):
            raise ValueError("campaign exposure event binding mismatch")
        if not self.state_history or self.state_history[-1] is not self.state:
            raise ValueError("campaign state history must end at current state")
        return self


class ReviewCampaignResumeDTO(_CampaignModel):
    schema_version: Literal["review-campaign-v2.v1"] = (
        REVIEW_CAMPAIGN_SCHEMA_VERSION
    )
    campaign: ReviewCampaign
    exported_at_ms: int = Field(ge=0)


__all__ = [
    "MAX_CAMPAIGN_EXPOSURES",
    "MAX_CAMPAIGN_ROUNDS",
    "REVIEW_CAMPAIGN_SCHEMA_VERSION",
    "ReviewBudgetAction",
    "ReviewCampaign",
    "ReviewCampaignAnalytics",
    "ReviewCampaignAttemptReceipt",
    "ReviewCampaignConfig",
    "ReviewCampaignFeedbackPolicy",
    "ReviewCampaignReceipt",
    "ReviewCampaignResumeDTO",
    "ReviewCampaignRound",
    "ReviewCampaignState",
    "ReviewDuePriorityItem",
    "ReviewDuePriorityRequest",
    "ReviewDuePrioritySnapshot",
    "ReviewExecutionBudgetDecision",
    "ReviewExecutionBudgetRequest",
    "ReviewExposureEventType",
    "ReviewFeedbackMode",
    "ReviewFeedbackReceipt",
    "ReviewFeedbackSource",
    "ReviewObservation",
    "ReviewObservationProposal",
    "ReviewObservationProposalReceipt",
    "ReviewQuestionExposure",
    "ReviewQuestionExposureLedger",
    "ReviewRoundAnalytics",
    "ReviewRoundState",
]
