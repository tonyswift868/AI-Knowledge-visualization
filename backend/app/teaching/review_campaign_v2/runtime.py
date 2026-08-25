"""Fixed-round campaign orchestration over ReviewSession v1."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from ..evaluator_v2 import (
    EvaluationOutcome,
    EvaluationPath,
    EvaluationResult,
    EvaluatorDecision,
    EvaluatorRequest,
    EvaluatorV2,
)
from ..review_v1 import (
    ReviewAttempt,
    ReviewAttemptReceipt,
    ReviewCompletionPolicy,
    ReviewMemorySignal,
    ReviewMemorySnapshot,
    ReviewQuestionView,
    QuestionSourcePolicy,
    ReviewRetrievalOutcome,
    ReviewSessionCrashDTO,
    ReviewSessionRuntime,
    ReviewSessionState,
    StudentProgressPort,
    TeacherExplanation,
    TeacherExplanationPort,
    TeacherExplanationRequest,
    TrustedQuestionBankPort,
)
from .models import (
    ReviewBudgetAction,
    ReviewCampaign,
    ReviewCampaignAnalytics,
    ReviewCampaignAttemptReceipt,
    ReviewCampaignConfig,
    ReviewCampaignFeedbackPolicy,
    ReviewCampaignReceipt,
    ReviewCampaignResumeDTO,
    ReviewCampaignRound,
    ReviewCampaignState,
    ReviewDuePriorityRequest,
    ReviewDuePrioritySnapshot,
    ReviewExecutionBudgetDecision,
    ReviewExecutionBudgetRequest,
    ReviewExposureEventType,
    ReviewFeedbackMode,
    ReviewFeedbackReceipt,
    ReviewFeedbackSource,
    ReviewObservation,
    ReviewObservationProposal,
    ReviewObservationProposalReceipt,
    ReviewQuestionExposure,
    ReviewQuestionExposureLedger,
    ReviewRoundAnalytics,
    ReviewRoundState,
)
from .ports import ReviewCampaignMemoryPort, ReviewExecutionBudgetPort


class ReviewCampaignError(Exception):
    """Invalid campaign transition or fail-closed boundary violation."""


def _stable_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _budget_denied_decision(request: EvaluatorRequest) -> EvaluatorDecision:
    result = EvaluationResult(
        assessment_id=request.attempt.assessment_id,
        attempt_id=request.attempt.attempt_id,
        rubric_id=request.rubric.rubric_id,
        path=request.rubric.path,
        outcome=EvaluationOutcome.INSUFFICIENT_EVIDENCE,
        score=None,
        confidence=1.0,
        reason="review_execution_budget_denied",
        rubric_signals=("review_budget:denied",),
        provider_calls=0,
    )
    return EvaluatorDecision(
        idempotency_key=_stable_digest(
            {
                "request": request.model_dump(mode="json"),
                "reason": "review_execution_budget_denied",
            }
        ),
        result=result,
    )


class _CampaignMemoryAdapter:
    def __init__(
        self,
        *,
        due_snapshot: ReviewDuePrioritySnapshot,
        concept_scope: tuple[str, ...],
        campaign_id: str,
        round_number: int,
    ) -> None:
        self._due_snapshot = due_snapshot
        self._concept_scope = concept_scope
        self._campaign_id = campaign_id
        self._round_number = round_number

    async def load_review_memory(
        self,
        concept_scope: tuple[str, ...],
    ) -> ReviewMemorySnapshot:
        if concept_scope != self._concept_scope:
            raise ReviewCampaignError("campaign_memory_adapter_scope_mismatch")
        scope = set(concept_scope)
        signals = tuple(
            ReviewMemorySignal(
                item_id=item.item_id,
                concept_id=item.concept_id,
                weakness_score=item.priority,
                last_outcome=item.last_outcome,
            )
            for item in self._due_snapshot.items
            if item.concept_id in scope
        )
        return ReviewMemorySnapshot(
            snapshot_id=(
                f"{self._campaign_id}:round:{self._round_number}:memory"
            ),
            concept_scope=concept_scope,
            weak_signals=signals,
            position_token=self._due_snapshot.position_token,
        )


class _BudgetedEvaluator:
    def __init__(
        self,
        *,
        evaluator: EvaluatorV2,
        budget_port: ReviewExecutionBudgetPort,
        campaign_id: str,
        round_number: int,
    ) -> None:
        self._evaluator = evaluator
        self._budget_port = budget_port
        self._campaign_id = campaign_id
        self._round_number = round_number
        self.decisions: list[ReviewExecutionBudgetDecision] = []

    async def evaluate(self, request: EvaluatorRequest) -> EvaluatorDecision:
        if request.rubric.path is EvaluationPath.OBJECTIVE:
            return await self._evaluator.evaluate(request)
        budget_request = ReviewExecutionBudgetRequest(
            request_id=_stable_digest(
                {
                    "campaign_id": self._campaign_id,
                    "round_number": self._round_number,
                    "action": ReviewBudgetAction.SEMANTIC_EVALUATION.value,
                    "assessment_id": request.attempt.assessment_id,
                }
            ),
            campaign_id=self._campaign_id,
            round_number=self._round_number,
            action=ReviewBudgetAction.SEMANTIC_EVALUATION,
            idempotency_key=request.attempt.assessment_id,
        )
        try:
            raw_decision = await self._budget_port.authorize(budget_request)
            decision = ReviewExecutionBudgetDecision.model_validate(raw_decision)
            if (
                decision.request_id != budget_request.request_id
                or decision.action is not budget_request.action
            ):
                raise ValueError("semantic budget decision binding mismatch")
        except Exception as exc:
            decision = ReviewExecutionBudgetDecision(
                request_id=budget_request.request_id,
                action=budget_request.action,
                allowed=False,
                provider_calls_reserved=0,
                reason=f"budget_port_fail_closed:{type(exc).__name__}",
            )
        self.decisions.append(decision)
        if not decision.allowed:
            return _budget_denied_decision(request)
        evaluated = await self._evaluator.evaluate(request)
        if evaluated.result.provider_calls > decision.provider_calls_reserved:
            raise ReviewCampaignError("semantic_provider_exceeded_budget")
        return evaluated

    def drain_decisions(self) -> tuple[ReviewExecutionBudgetDecision, ...]:
        decisions = tuple(self.decisions)
        self.decisions.clear()
        return decisions


class ReviewCampaignRuntime:
    """Campaign coordinator; FSRS signals never control its fixed round count."""

    def __init__(
        self,
        *,
        campaign: ReviewCampaign,
        question_bank: TrustedQuestionBankPort,
        memory_port: ReviewCampaignMemoryPort,
        progress_port: StudentProgressPort,
        budget_port: ReviewExecutionBudgetPort,
        evaluator: EvaluatorV2,
        clock_ms: Callable[[], int],
        teacher_explanation_port: TeacherExplanationPort | None = None,
    ) -> None:
        self._campaign = campaign
        self._question_bank = question_bank
        self._memory_port = memory_port
        self._progress_port = progress_port
        self._budget_port = budget_port
        self._evaluator = evaluator
        self._clock_ms = clock_ms
        self._teacher_explanation_port = teacher_explanation_port
        self._round_runtime: ReviewSessionRuntime | None = None
        self._budgeted_evaluator: _BudgetedEvaluator | None = None
        self._round_due_snapshot: ReviewDuePrioritySnapshot | None = None

    @classmethod
    def create(
        cls,
        *,
        campaign_id: str,
        concept_scope: tuple[str, ...],
        fixed_round_count: int,
        target_item_count: int,
        question_source_policy: QuestionSourcePolicy,
        completion_policy: ReviewCompletionPolicy,
        max_same_round_retries: int,
        feedback_policy: ReviewCampaignFeedbackPolicy,
        question_bank: TrustedQuestionBankPort,
        memory_port: ReviewCampaignMemoryPort,
        progress_port: StudentProgressPort,
        budget_port: ReviewExecutionBudgetPort,
        evaluator: EvaluatorV2,
        clock_ms: Callable[[], int],
        teacher_explanation_port: TeacherExplanationPort | None = None,
    ) -> "ReviewCampaignRuntime":
        config = ReviewCampaignConfig(
            concept_scope=concept_scope,
            fixed_round_count=fixed_round_count,
            target_item_count=target_item_count,
            question_source_policy=question_source_policy,
            completion_policy=completion_policy,
            max_same_round_retries=max_same_round_retries,
            feedback_policy=feedback_policy,
        )
        now = clock_ms()
        campaign = ReviewCampaign(
            campaign_id=campaign_id,
            config=config,
            created_at_ms=now,
            updated_at_ms=now,
        )
        return cls(
            campaign=campaign,
            question_bank=question_bank,
            memory_port=memory_port,
            progress_port=progress_port,
            budget_port=budget_port,
            evaluator=evaluator,
            clock_ms=clock_ms,
            teacher_explanation_port=teacher_explanation_port,
        )

    @classmethod
    def from_resume_dto(
        cls,
        dto: ReviewCampaignResumeDTO,
        *,
        question_bank: TrustedQuestionBankPort,
        memory_port: ReviewCampaignMemoryPort,
        progress_port: StudentProgressPort,
        budget_port: ReviewExecutionBudgetPort,
        evaluator: EvaluatorV2,
        clock_ms: Callable[[], int],
        teacher_explanation_port: TeacherExplanationPort | None = None,
    ) -> "ReviewCampaignRuntime":
        runtime = cls(
            campaign=dto.campaign,
            question_bank=question_bank,
            memory_port=memory_port,
            progress_port=progress_port,
            budget_port=budget_port,
            evaluator=evaluator,
            clock_ms=clock_ms,
            teacher_explanation_port=teacher_explanation_port,
        )
        if not dto.campaign.rounds:
            return runtime
        current = dto.campaign.rounds[-1]
        if current.state is ReviewRoundState.COMPLETED:
            return runtime
        budgeted = _BudgetedEvaluator(
            evaluator=evaluator,
            budget_port=budget_port,
            campaign_id=dto.campaign.campaign_id,
            round_number=current.round_number,
        )
        child = ReviewSessionRuntime.from_crash_dto(
            ReviewSessionCrashDTO(
                session=current.review_session,
                exported_at_ms=dto.exported_at_ms,
            ),
            evaluator=budgeted,
            clock_ms=clock_ms,
        )
        runtime._budgeted_evaluator = budgeted
        runtime._round_runtime = child
        interrupted = (
            dto.campaign.state is ReviewCampaignState.EVALUATING
            or current.review_session.state is ReviewSessionState.EVALUATING
        )
        if interrupted:
            round_payload = current.model_dump(mode="python")
            round_payload.update(
                {
                    "state": ReviewRoundState.RECOVERY_REQUIRED,
                    "review_session": child.snapshot,
                    "state_history": (
                        *current.state_history,
                        ReviewRoundState.RECOVERY_REQUIRED,
                    ),
                }
            )
            recovered_round = ReviewCampaignRound.model_validate(round_payload)
            payload = dto.campaign.model_dump(mode="python")
            payload.update(
                {
                    "state": ReviewCampaignState.RECOVERY_REQUIRED,
                    "rounds": (*dto.campaign.rounds[:-1], recovered_round),
                    "pending_submission_id": None,
                    "failure_reason": "pending_review_evaluation_requires_restart",
                    "state_history": (
                        *dto.campaign.state_history,
                        ReviewCampaignState.RECOVERY_REQUIRED,
                    ),
                    "updated_at_ms": clock_ms(),
                    "version": dto.campaign.version + 1,
                }
            )
            runtime._campaign = ReviewCampaign.model_validate(payload)
        return runtime

    @property
    def snapshot(self) -> ReviewCampaign:
        return self._campaign

    def _validate_snapshot(self) -> None:
        self._campaign = ReviewCampaign.model_validate(
            self._campaign.model_dump(mode="python")
        )

    def _replace(self, **changes) -> None:
        self._campaign = self._campaign.model_copy(update=changes)

    def _transition(self, state: ReviewCampaignState, **changes) -> None:
        history = self._campaign.state_history
        if not history or history[-1] is not state:
            history = (*history, state)
        self._replace(
            state=state,
            state_history=history,
            updated_at_ms=self._clock_ms(),
            version=self._campaign.version + 1,
            **changes,
        )
        if state in {
            ReviewCampaignState.COMPLETED,
            ReviewCampaignState.ABORTED,
            ReviewCampaignState.FAILED,
        }:
            self._validate_snapshot()

    def _current_round(self) -> ReviewCampaignRound:
        if not self._campaign.rounds:
            raise ReviewCampaignError("campaign_has_no_current_round")
        return self._campaign.rounds[-1]

    def _replace_current_round(
        self,
        review_round: ReviewCampaignRound,
    ) -> tuple[ReviewCampaignRound, ...]:
        return (*self._campaign.rounds[:-1], review_round)

    def _round_with(self, **changes) -> ReviewCampaignRound:
        current = self._current_round()
        if "state" in changes and "state_history" not in changes:
            next_state = changes["state"]
            history = current.state_history
            if not history or history[-1] is not next_state:
                history = (*history, next_state)
            changes["state_history"] = history
        return current.model_copy(update=changes)

    def _shown_item_ids(self) -> tuple[str, ...]:
        return tuple(
            event.item_id
            for event in self._campaign.exposure_ledger.events
            if event.event_type is ReviewExposureEventType.QUESTION_SHOWN
        )

    def _exposure(
        self,
        *,
        event_type: ReviewExposureEventType,
        item_id: str,
        occurrence_number: int,
        attempt: ReviewAttempt | None = None,
        outcome: ReviewRetrievalOutcome | None = None,
        ordinal_offset: int = 0,
    ) -> ReviewQuestionExposure:
        ordinal = len(self._campaign.exposure_ledger.events) + ordinal_offset + 1
        payload = {
            "campaign_id": self._campaign.campaign_id,
            "round_number": self._campaign.current_round_number,
            "item_id": item_id,
            "occurrence_number": occurrence_number,
            "event_type": event_type.value,
            "submission_id": attempt.submission_id if attempt else None,
            "attempt_id": attempt.attempt_id if attempt else None,
            "ordinal": ordinal,
        }
        return ReviewQuestionExposure(
            exposure_id=_stable_digest(payload),
            campaign_id=self._campaign.campaign_id,
            round_number=self._campaign.current_round_number,
            item_id=item_id,
            occurrence_number=occurrence_number,
            event_type=event_type,
            submission_id=attempt.submission_id if attempt else None,
            attempt_id=attempt.attempt_id if attempt else None,
            retrieval_outcome=outcome,
            recorded_at_ms=self._clock_ms(),
        )

    def _ledger_with(
        self,
        *events: ReviewQuestionExposure,
    ) -> ReviewQuestionExposureLedger:
        return ReviewQuestionExposureLedger(
            events=(*self._campaign.exposure_ledger.events, *events)
        )

    async def _new_round_runtime(
        self,
        *,
        round_number: int,
        recovery_count: int,
    ) -> tuple[ReviewSessionRuntime, _BudgetedEvaluator, ReviewDuePrioritySnapshot]:
        request = ReviewDuePriorityRequest(
            campaign_id=self._campaign.campaign_id,
            round_number=round_number,
            fixed_round_count=self._campaign.config.fixed_round_count,
            concept_scope=self._campaign.config.concept_scope,
            previously_shown_item_ids=self._shown_item_ids(),
        )
        due_raw = await self._memory_port.read_due_priority(request)
        due = ReviewDuePrioritySnapshot.model_validate(due_raw)
        budgeted = _BudgetedEvaluator(
            evaluator=self._evaluator,
            budget_port=self._budget_port,
            campaign_id=self._campaign.campaign_id,
            round_number=round_number,
        )
        memory_adapter = _CampaignMemoryAdapter(
            due_snapshot=due,
            concept_scope=self._campaign.config.concept_scope,
            campaign_id=self._campaign.campaign_id,
            round_number=round_number,
        )
        child = await ReviewSessionRuntime.create(
            session_id=(
                f"{self._campaign.campaign_id}:round:{round_number}:"
                f"restart:{recovery_count}"
            ),
            concept_scope=self._campaign.config.concept_scope,
            round_number=round_number,
            question_source_policy=self._campaign.config.question_source_policy,
            target_item_count=self._campaign.config.target_item_count,
            completion_policy=self._campaign.config.completion_policy,
            max_same_round_retries=self._campaign.config.max_same_round_retries,
            question_bank=self._question_bank,
            memory_port=memory_adapter,
            progress_port=self._progress_port,
            evaluator=budgeted,
            clock_ms=self._clock_ms,
        )
        return child, budgeted, due

    async def start(self) -> ReviewQuestionView:
        if self._campaign.state is not ReviewCampaignState.CREATED:
            raise ReviewCampaignError("campaign_start_requires_created_state")
        return await self._start_new_fixed_round(1)

    async def start_next_round(self) -> ReviewQuestionView:
        if self._campaign.state is not ReviewCampaignState.BETWEEN_ROUNDS:
            raise ReviewCampaignError("next_round_requires_between_rounds_state")
        next_round = self._campaign.current_round_number + 1
        if next_round > self._campaign.config.fixed_round_count:
            raise ReviewCampaignError("campaign_fixed_round_count_exhausted")
        return await self._start_new_fixed_round(next_round)

    async def _start_new_fixed_round(
        self,
        round_number: int,
    ) -> ReviewQuestionView:
        child, budgeted, due = await self._new_round_runtime(
            round_number=round_number,
            recovery_count=0,
        )
        question = child.start()
        now = self._clock_ms()
        campaign_round = ReviewCampaignRound(
            round_number=round_number,
            state=ReviewRoundState.ACTIVE,
            review_session=child.snapshot,
            started_at_ms=now,
        )
        self._round_runtime = child
        self._budgeted_evaluator = budgeted
        self._round_due_snapshot = due
        exposure = ReviewQuestionExposure(
            exposure_id=_stable_digest(
                {
                    "campaign_id": self._campaign.campaign_id,
                    "round_number": round_number,
                    "item_id": question.item_id,
                    "occurrence_number": question.occurrence_number,
                    "event_type": ReviewExposureEventType.QUESTION_SHOWN.value,
                    "ordinal": len(self._campaign.exposure_ledger.events) + 1,
                }
            ),
            campaign_id=self._campaign.campaign_id,
            round_number=round_number,
            item_id=question.item_id,
            occurrence_number=question.occurrence_number,
            event_type=ReviewExposureEventType.QUESTION_SHOWN,
            recorded_at_ms=now,
        )
        self._transition(
            ReviewCampaignState.ROUND_ACTIVE,
            rounds=(*self._campaign.rounds, campaign_round),
            current_round_number=round_number,
            exposure_ledger=self._ledger_with(exposure),
            failure_reason=None,
        )
        return question

    def present_question(self) -> ReviewQuestionView:
        if (
            self._campaign.state is not ReviewCampaignState.ROUND_ACTIVE
            or self._round_runtime is None
        ):
            raise ReviewCampaignError("campaign_question_requires_active_round")
        return self._round_runtime.present_question()

    def pause(self) -> ReviewCampaign:
        if (
            self._campaign.state is not ReviewCampaignState.ROUND_ACTIVE
            or self._round_runtime is None
        ):
            raise ReviewCampaignError("campaign_pause_requires_active_round")
        self._round_runtime.pause()
        review_round = self._round_with(
            state=ReviewRoundState.PAUSED,
            review_session=self._round_runtime.snapshot,
        )
        self._transition(
            ReviewCampaignState.PAUSED,
            rounds=self._replace_current_round(review_round),
        )
        return self._campaign

    async def resume(self) -> ReviewQuestionView:
        if self._campaign.state is ReviewCampaignState.RECOVERY_REQUIRED:
            return await self._restart_recovery_round()
        if (
            self._campaign.state is not ReviewCampaignState.PAUSED
            or self._round_runtime is None
        ):
            raise ReviewCampaignError("campaign_resume_requires_paused_state")
        question = self._round_runtime.resume()
        review_round = self._round_with(
            state=ReviewRoundState.ACTIVE,
            review_session=self._round_runtime.snapshot,
        )
        self._transition(
            ReviewCampaignState.ROUND_ACTIVE,
            rounds=self._replace_current_round(review_round),
        )
        return question

    async def _restart_recovery_round(self) -> ReviewQuestionView:
        current = self._current_round()
        recovery_count = current.recovery_count + 1
        child, budgeted, due = await self._new_round_runtime(
            round_number=current.round_number,
            recovery_count=recovery_count,
        )
        question = child.start()
        restarted = ReviewCampaignRound(
            round_number=current.round_number,
            state=ReviewRoundState.ACTIVE,
            review_session=child.snapshot,
            state_history=(
                *current.state_history,
                ReviewRoundState.ACTIVE,
            ),
            started_at_ms=current.started_at_ms,
            recovery_count=recovery_count,
        )
        self._round_runtime = child
        self._budgeted_evaluator = budgeted
        self._round_due_snapshot = due
        exposure = self._exposure(
            event_type=ReviewExposureEventType.QUESTION_SHOWN,
            item_id=question.item_id,
            occurrence_number=question.occurrence_number,
        )
        self._transition(
            ReviewCampaignState.ROUND_ACTIVE,
            rounds=self._replace_current_round(restarted),
            exposure_ledger=self._ledger_with(exposure),
            failure_reason=None,
        )
        return question

    def to_resume_dto(self) -> ReviewCampaignResumeDTO:
        if self._round_runtime is not None and self._campaign.rounds:
            current = self._current_round()
            if current.state is not ReviewRoundState.COMPLETED:
                synced = self._round_with(
                    review_session=self._round_runtime.snapshot,
                )
                self._replace(rounds=self._replace_current_round(synced))
        self._validate_snapshot()
        return ReviewCampaignResumeDTO(
            campaign=self._campaign,
            exported_at_ms=self._clock_ms(),
        )

    async def _authorize(
        self,
        *,
        action: ReviewBudgetAction,
        idempotency_key: str,
    ) -> ReviewExecutionBudgetDecision:
        request = ReviewExecutionBudgetRequest(
            request_id=_stable_digest(
                {
                    "campaign_id": self._campaign.campaign_id,
                    "round_number": self._campaign.current_round_number,
                    "action": action.value,
                    "idempotency_key": idempotency_key,
                }
            ),
            campaign_id=self._campaign.campaign_id,
            round_number=self._campaign.current_round_number,
            action=action,
            idempotency_key=idempotency_key,
        )
        try:
            raw = await self._budget_port.authorize(request)
            decision = ReviewExecutionBudgetDecision.model_validate(raw)
            if (
                decision.request_id != request.request_id
                or decision.action is not action
            ):
                raise ValueError("teacher budget decision binding mismatch")
        except Exception as exc:
            decision = ReviewExecutionBudgetDecision(
                request_id=request.request_id,
                action=action,
                allowed=False,
                provider_calls_reserved=0,
                reason=f"budget_port_fail_closed:{type(exc).__name__}",
            )
        return decision

    def _item_for_attempt(self, receipt: ReviewAttemptReceipt):
        if self._round_runtime is None:
            raise ReviewCampaignError("campaign_round_runtime_missing")
        return next(
            item
            for item in self._round_runtime.snapshot.round.items
            if item.item_id == receipt.item_id
        )

    def _local_feedback(
        self,
        *,
        receipt: ReviewAttemptReceipt,
        explicit_request: bool,
        source: ReviewFeedbackSource = ReviewFeedbackSource.LOCAL,
        budget_request_id: str | None = None,
        text: str | None = None,
    ) -> ReviewFeedbackReceipt:
        return ReviewFeedbackReceipt(
            feedback_id=_stable_digest(
                {
                    "campaign_id": self._campaign.campaign_id,
                    "round_number": self._campaign.current_round_number,
                    "attempt_id": receipt.attempt_id,
                    "explicit": explicit_request,
                    "source": source.value,
                }
            ),
            campaign_id=self._campaign.campaign_id,
            round_number=self._campaign.current_round_number,
            item_id=receipt.item_id,
            attempt_id=receipt.attempt_id,
            explicit_request=explicit_request,
            source=source,
            text=text or "Recall the smallest missing relationship, then try again.",
            provider_calls=0,
            budget_request_id=budget_request_id,
            recorded_at_ms=self._clock_ms(),
        )

    def _feedback_uses_teacher(self, outcome: ReviewRetrievalOutcome) -> bool:
        mode = self._campaign.config.feedback_policy.mode
        if mode is ReviewFeedbackMode.TEACHER_ON_FAILED:
            return outcome is ReviewRetrievalOutcome.FAILED
        if mode is ReviewFeedbackMode.TEACHER_ON_PARTIAL_OR_FAILED:
            return outcome in {
                ReviewRetrievalOutcome.FAILED,
                ReviewRetrievalOutcome.PARTIAL,
            }
        return False

    def _teacher_feedback_count(self) -> int:
        current_round = self._campaign.current_round_number
        return sum(
            receipt.round_number == current_round
            and receipt.source is ReviewFeedbackSource.TEACHER
            and not receipt.explicit_request
            for receipt in self._campaign.feedback_receipts
        )

    async def _attempt_feedback(
        self,
        receipt: ReviewAttemptReceipt,
    ) -> tuple[
        ReviewFeedbackReceipt | None,
        tuple[ReviewExecutionBudgetDecision, ...],
    ]:
        outcome = receipt.retrieval_outcome
        if outcome not in {
            ReviewRetrievalOutcome.FAILED,
            ReviewRetrievalOutcome.PARTIAL,
        }:
            return None, ()
        feedback_limit = (
            self._campaign.config.feedback_policy.max_teacher_feedback_calls_per_round
        )
        if (
            not self._feedback_uses_teacher(outcome)
            or self._teacher_explanation_port is None
            or self._teacher_feedback_count() >= feedback_limit
        ):
            return self._local_feedback(
                receipt=receipt,
                explicit_request=False,
            ), ()
        decision = await self._authorize(
            action=ReviewBudgetAction.FAIL_FEEDBACK,
            idempotency_key=receipt.attempt_id,
        )
        if not decision.allowed:
            return (
                self._local_feedback(
                    receipt=receipt,
                    explicit_request=False,
                    source=ReviewFeedbackSource.BUDGET_DENIED,
                    budget_request_id=decision.request_id,
                    text=(
                        "Minimal local feedback used because Provider budget was "
                        "denied."
                    ),
                ),
                (decision,),
            )
        item = self._item_for_attempt(receipt)
        request = TeacherExplanationRequest(
            session_id=self._campaign.campaign_id,
            round_id=self._round_runtime.snapshot.round.round_id,
            item_id=item.item_id,
            concept_id=item.concept_id,
            review_question=item.prompt,
            learner_question=(
                "Give the minimum necessary feedback without revealing the answer."
            ),
        )
        try:
            explanation = TeacherExplanation.model_validate(
                await self._teacher_explanation_port.explain(request)
            )
            if explanation.provider_calls != 1:
                raise ReviewCampaignError("teacher_feedback_provider_count_mismatch")
            evidence_ids = {evidence.evidence_id for evidence in item.evidence}
            if not set(explanation.evidence_refs) <= evidence_ids:
                raise ReviewCampaignError("teacher_feedback_fabricated_evidence")
            text = explanation.text
            evidence_refs = explanation.evidence_refs
        except Exception:
            text = "Feedback Provider failed; retry from the smallest missing relation."
            evidence_refs = ()
        feedback = ReviewFeedbackReceipt(
            feedback_id=_stable_digest(
                {
                    "campaign_id": self._campaign.campaign_id,
                    "round_number": self._campaign.current_round_number,
                    "attempt_id": receipt.attempt_id,
                    "action": ReviewBudgetAction.FAIL_FEEDBACK.value,
                }
            ),
            campaign_id=self._campaign.campaign_id,
            round_number=self._campaign.current_round_number,
            item_id=receipt.item_id,
            attempt_id=receipt.attempt_id,
            explicit_request=False,
            source=ReviewFeedbackSource.TEACHER,
            text=text,
            evidence_refs=evidence_refs,
            provider_calls=1,
            budget_request_id=decision.request_id,
            recorded_at_ms=self._clock_ms(),
        )
        return feedback, (decision,)

    def _dedupe_budget_decisions(
        self,
        decisions: tuple[ReviewExecutionBudgetDecision, ...],
    ) -> tuple[ReviewExecutionBudgetDecision, ...]:
        existing = {item.request_id for item in self._campaign.budget_decisions}
        return tuple(item for item in decisions if item.request_id not in existing)

    def _assert_reappearance_authorized(self, question: ReviewQuestionView) -> None:
        if question.occurrence_number == 1:
            return
        current_session = self._round_runtime.snapshot
        prior = tuple(
            receipt
            for receipt in current_session.round.attempt_receipts
            if receipt.item_id == question.item_id
        )
        if not prior or prior[-1].weak_item_proposal is None:
            raise ReviewCampaignError("meaningless_same_round_question_reuse")
        if not prior[-1].weak_item_proposal.same_round_retry_eligible:
            raise ReviewCampaignError("question_reuse_exceeded_weak_retry_bound")

    async def submit(
        self,
        attempt: ReviewAttempt,
    ) -> ReviewCampaignAttemptReceipt:
        if self._round_runtime is None:
            raise ReviewCampaignError("campaign_round_runtime_missing")
        stored = next(
            (
                item
                for item in self._round_runtime.snapshot.round.attempts
                if item.submission_id == attempt.submission_id
            ),
            None,
        )
        if stored is not None:
            receipt = await self._round_runtime.submit(attempt)
            return ReviewCampaignAttemptReceipt(
                attempt_receipt=receipt,
                campaign_state_after=self._campaign.state,
            )
        if self._campaign.state is not ReviewCampaignState.ROUND_ACTIVE:
            raise ReviewCampaignError("campaign_submit_requires_active_round")
        question = self._round_runtime.present_question()
        pre_events = [
            self._exposure(
                event_type=ReviewExposureEventType.ATTEMPT_SUBMITTED,
                item_id=attempt.item_id,
                occurrence_number=question.occurrence_number,
                attempt=attempt,
            )
        ]
        if attempt.answer_revealed_before_submission:
            pre_events.append(
                self._exposure(
                    event_type=ReviewExposureEventType.ANSWER_REVEALED,
                    item_id=attempt.item_id,
                    occurrence_number=question.occurrence_number,
                    attempt=attempt,
                    ordinal_offset=len(pre_events),
                )
            )
        if attempt.hint_used or attempt.hint_dependency.value != "none":
            pre_events.append(
                self._exposure(
                    event_type=ReviewExposureEventType.HINT_USED,
                    item_id=attempt.item_id,
                    occurrence_number=question.occurrence_number,
                    attempt=attempt,
                    ordinal_offset=len(pre_events),
                )
            )
        evaluating_round = self._round_with(state=ReviewRoundState.EVALUATING)
        self._transition(
            ReviewCampaignState.EVALUATING,
            rounds=self._replace_current_round(evaluating_round),
            exposure_ledger=self._ledger_with(*pre_events),
            pending_submission_id=attempt.submission_id,
        )
        receipt = await self._round_runtime.submit(attempt)
        evaluator_budget = (
            self._budgeted_evaluator.drain_decisions()
            if self._budgeted_evaluator is not None
            else ()
        )
        feedback, feedback_budget = await self._attempt_feedback(receipt)
        new_decisions = self._dedupe_budget_decisions(
            (*evaluator_budget, *feedback_budget)
        )
        post_events: list[ReviewQuestionExposure] = []
        if receipt.accepted and receipt.retrieval_outcome is not None:
            post_events.append(
                self._exposure(
                    event_type=ReviewExposureEventType.RESULT_RECORDED,
                    item_id=receipt.item_id,
                    occurrence_number=receipt.occurrence_number,
                    attempt=attempt,
                    outcome=receipt.retrieval_outcome,
                )
            )
        child_state = self._round_runtime.snapshot.state
        if child_state is ReviewSessionState.ACTIVE:
            next_question = self._round_runtime.present_question()
            self._assert_reappearance_authorized(next_question)
            post_events.append(
                self._exposure(
                    event_type=ReviewExposureEventType.QUESTION_SHOWN,
                    item_id=next_question.item_id,
                    occurrence_number=next_question.occurrence_number,
                    ordinal_offset=len(post_events),
                )
            )
            round_state = ReviewRoundState.ACTIVE
            campaign_state = ReviewCampaignState.ROUND_ACTIVE
        elif child_state is ReviewSessionState.READY_TO_COMPLETE:
            round_state = ReviewRoundState.EVALUATED
            campaign_state = ReviewCampaignState.ROUND_EVALUATED
        elif child_state is ReviewSessionState.FAILED:
            round_state = ReviewRoundState.FAILED
            campaign_state = ReviewCampaignState.FAILED
        elif child_state is ReviewSessionState.ABORTED:
            round_state = ReviewRoundState.FAILED
            campaign_state = ReviewCampaignState.ABORTED
        else:
            raise ReviewCampaignError("unexpected_child_review_state")
        synced_round = self._round_with(
            state=round_state,
            review_session=self._round_runtime.snapshot,
        )
        feedback_receipts = self._campaign.feedback_receipts
        if feedback is not None:
            feedback_receipts = (*feedback_receipts, feedback)
        self._transition(
            campaign_state,
            rounds=self._replace_current_round(synced_round),
            exposure_ledger=self._ledger_with(*post_events),
            budget_decisions=(
                *self._campaign.budget_decisions,
                *new_decisions,
            ),
            feedback_receipts=feedback_receipts,
            pending_submission_id=None,
            failure_reason=(
                self._round_runtime.snapshot.failure_reason
                if campaign_state is ReviewCampaignState.FAILED
                else None
            ),
        )
        return ReviewCampaignAttemptReceipt(
            attempt_receipt=receipt,
            feedback=feedback,
            budget_request_ids=tuple(
                decision.request_id for decision in new_decisions
            ),
            campaign_state_after=campaign_state,
        )

    async def ask_why(self, learner_question: str) -> ReviewFeedbackReceipt:
        if (
            self._campaign.state is not ReviewCampaignState.ROUND_ACTIVE
            or self._round_runtime is None
        ):
            raise ReviewCampaignError("campaign_explanation_requires_active_round")
        question = self._round_runtime.present_question()
        if self._teacher_explanation_port is None:
            feedback = ReviewFeedbackReceipt(
                feedback_id=_stable_digest(
                    {
                        "campaign_id": self._campaign.campaign_id,
                        "round": self._campaign.current_round_number,
                        "question": learner_question,
                        "ordinal": len(self._campaign.feedback_receipts) + 1,
                    }
                ),
                campaign_id=self._campaign.campaign_id,
                round_number=self._campaign.current_round_number,
                item_id=question.item_id,
                explicit_request=True,
                source=ReviewFeedbackSource.LOCAL,
                text="Teacher explanation is unavailable; the review remains active.",
                provider_calls=0,
                recorded_at_ms=self._clock_ms(),
            )
            self._replace(
                feedback_receipts=(*self._campaign.feedback_receipts, feedback),
                updated_at_ms=self._clock_ms(),
                version=self._campaign.version + 1,
            )
            return feedback
        decision = await self._authorize(
            action=ReviewBudgetAction.TEACHER_EXPLANATION,
            idempotency_key=_stable_digest(
                {
                    "item_id": question.item_id,
                    "learner_question": learner_question,
                    "ordinal": len(self._campaign.feedback_receipts) + 1,
                }
            ),
        )
        if not decision.allowed:
            feedback = ReviewFeedbackReceipt(
                feedback_id=_stable_digest(
                    {
                        "budget_request_id": decision.request_id,
                        "source": ReviewFeedbackSource.BUDGET_DENIED.value,
                    }
                ),
                campaign_id=self._campaign.campaign_id,
                round_number=self._campaign.current_round_number,
                item_id=question.item_id,
                explicit_request=True,
                source=ReviewFeedbackSource.BUDGET_DENIED,
                text="Teacher explanation was not called because budget was denied.",
                provider_calls=0,
                budget_request_id=decision.request_id,
                recorded_at_ms=self._clock_ms(),
            )
        else:
            item = next(
                item
                for item in self._round_runtime.snapshot.round.items
                if item.item_id == question.item_id
            )
            request = TeacherExplanationRequest(
                session_id=self._campaign.campaign_id,
                round_id=self._round_runtime.snapshot.round.round_id,
                item_id=item.item_id,
                concept_id=item.concept_id,
                review_question=item.prompt,
                learner_question=learner_question,
            )
            try:
                explanation = TeacherExplanation.model_validate(
                    await self._teacher_explanation_port.explain(request)
                )
                if explanation.provider_calls != 1:
                    raise ReviewCampaignError(
                        "teacher_explanation_provider_count_mismatch"
                    )
                evidence_ids = {
                    evidence.evidence_id for evidence in item.evidence
                }
                if not set(explanation.evidence_refs) <= evidence_ids:
                    raise ReviewCampaignError(
                        "teacher_explanation_fabricated_evidence"
                    )
                text = explanation.text
                evidence_refs = explanation.evidence_refs
            except Exception:
                text = "Teacher explanation failed; the review remains active."
                evidence_refs = ()
            feedback = ReviewFeedbackReceipt(
                feedback_id=_stable_digest(
                    {
                        "budget_request_id": decision.request_id,
                        "source": ReviewFeedbackSource.TEACHER.value,
                    }
                ),
                campaign_id=self._campaign.campaign_id,
                round_number=self._campaign.current_round_number,
                item_id=question.item_id,
                explicit_request=True,
                source=ReviewFeedbackSource.TEACHER,
                text=text,
                evidence_refs=evidence_refs,
                provider_calls=1,
                budget_request_id=decision.request_id,
                recorded_at_ms=self._clock_ms(),
            )
        new_decisions = self._dedupe_budget_decisions((decision,))
        self._replace(
            budget_decisions=(
                *self._campaign.budget_decisions,
                *new_decisions,
            ),
            feedback_receipts=(*self._campaign.feedback_receipts, feedback),
            updated_at_ms=self._clock_ms(),
            version=self._campaign.version + 1,
        )
        return feedback

    def _observation_proposal(
        self,
        round_receipt,
    ) -> ReviewObservationProposal:
        if self._round_runtime is None:
            raise ReviewCampaignError("campaign_round_runtime_missing")
        attempts = self._round_runtime.snapshot.round.attempts
        observations = []
        for item_result in round_receipt.result.item_results:
            item_attempts = tuple(
                attempt
                for attempt in attempts
                if attempt.item_id == item_result.item_id
            )
            observations.append(
                ReviewObservation(
                    item_id=item_result.item_id,
                    concept_id=item_result.concept_id,
                    outcome=item_result.best_outcome,
                    retrieval_score=item_result.retrieval_score,
                    weak=item_result.best_outcome
                    is not ReviewRetrievalOutcome.RECALLED,
                    hint_dependent=any(
                        attempt.hint_used
                        or attempt.hint_dependency.value != "none"
                        for attempt in item_attempts
                    ),
                    answer_revealed=any(
                        attempt.answer_revealed_before_submission
                        for attempt in item_attempts
                    ),
                    critical=item_result.critical,
                )
            )
        proposal_id = _stable_digest(
            {
                "campaign_id": self._campaign.campaign_id,
                "round_number": self._campaign.current_round_number,
                "round_receipt_id": round_receipt.receipt_id,
                "observations": [
                    item.model_dump(mode="json") for item in observations
                ],
            }
        )
        return ReviewObservationProposal(
            proposal_id=proposal_id,
            campaign_id=self._campaign.campaign_id,
            round_number=self._campaign.current_round_number,
            source_round_receipt_id=round_receipt.receipt_id,
            observations=tuple(observations),
            idempotency_key=proposal_id,
        )

    def _round_analytics(
        self,
        *,
        round_receipt,
        completed_at_ms: int,
    ) -> ReviewRoundAnalytics:
        current = self._current_round()
        attempts = current.review_session.round.attempts
        hint_count = sum(
            attempt.hint_used
            or attempt.hint_dependency.value != "none"
            for attempt in attempts
        )
        shown = tuple(
            event
            for event in self._campaign.exposure_ledger.events
            if event.round_number == current.round_number
            and event.event_type is ReviewExposureEventType.QUESTION_SHOWN
        )
        feedback_calls = sum(
            receipt.provider_calls
            for receipt in self._campaign.feedback_receipts
            if receipt.round_number == current.round_number
        )
        critical = tuple(
            item for item in round_receipt.result.item_results if item.critical
        )
        return ReviewRoundAnalytics(
            round_number=current.round_number,
            retrieval_score=round_receipt.result.completion.retrieval_score,
            weak_count=len(
                {
                    proposal.item_id
                    for proposal in current.review_session.round.weak_item_proposals
                }
            ),
            hint_rate=hint_count / len(attempts) if attempts else 0.0,
            provider_call_count=(
                round_receipt.result.evaluator_provider_calls + feedback_calls
            ),
            question_show_count=len(shown),
            question_reuse_count=len(shown)
            - len({event.item_id for event in shown}),
            critical_pass=all(item.passed for item in critical),
            duration_ms=max(0, completed_at_ms - current.started_at_ms),
        )

    def _campaign_analytics(
        self,
        rounds: tuple[ReviewCampaignRound, ...],
    ) -> ReviewCampaignAnalytics:
        item_results = tuple(
            item
            for review_round in rounds
            for item in review_round.round_receipt.result.item_results
        )
        attempts = tuple(
            attempt
            for review_round in rounds
            for attempt in review_round.review_session.round.attempts
        )
        hint_count = sum(
            attempt.hint_used
            or attempt.hint_dependency.value != "none"
            for attempt in attempts
        )
        shown = tuple(
            event
            for event in self._campaign.exposure_ledger.events
            if event.event_type is ReviewExposureEventType.QUESTION_SHOWN
        )
        return ReviewCampaignAnalytics(
            retrieval_score=sum(item.retrieval_score for item in item_results)
            / len(item_results),
            weak_count=sum(
                review_round.analytics.weak_count for review_round in rounds
            ),
            hint_rate=hint_count / len(attempts) if attempts else 0.0,
            provider_call_count=sum(
                review_round.analytics.provider_call_count
                for review_round in rounds
            ),
            question_show_count=len(shown),
            unique_question_count=len({event.item_id for event in shown}),
            question_reuse_count=len(shown)
            - len({event.item_id for event in shown}),
            same_round_reuse_count=sum(
                review_round.analytics.question_reuse_count
                for review_round in rounds
            ),
            critical_pass=all(
                review_round.analytics.critical_pass for review_round in rounds
            ),
            round_durations_ms=tuple(
                review_round.analytics.duration_ms for review_round in rounds
            ),
        )

    async def complete_round(self):
        if (
            self._campaign.state is not ReviewCampaignState.ROUND_EVALUATED
            or self._round_runtime is None
        ):
            raise ReviewCampaignError("round_completion_requires_evaluated_round")
        round_receipt = self._round_runtime.complete()
        proposal = self._observation_proposal(round_receipt)
        try:
            raw_memory_receipt = (
                await self._memory_port.write_review_observation_proposal(
                    proposal
                )
            )
            memory_receipt = ReviewObservationProposalReceipt.model_validate(
                raw_memory_receipt
            )
            if memory_receipt.proposal_id != proposal.proposal_id:
                raise ReviewCampaignError("memory_proposal_receipt_binding_mismatch")
        except Exception as exc:
            memory_receipt = ReviewObservationProposalReceipt(
                proposal_id=proposal.proposal_id,
                accepted=False,
                position_token=(
                    self._round_due_snapshot.position_token
                    if self._round_due_snapshot is not None
                    else "unknown-position"
                ),
                reason=f"memory_proposal_port_error:{type(exc).__name__}",
            )
        now = self._clock_ms()
        analytics = self._round_analytics(
            round_receipt=round_receipt,
            completed_at_ms=now,
        )
        evaluation_state = (
            ReviewRoundState.PASSED
            if round_receipt.result.passed
            else ReviewRoundState.NOT_PASSED
        )
        completed_round = self._round_with(
            state=ReviewRoundState.COMPLETED,
            state_history=(
                *self._current_round().state_history,
                evaluation_state,
                ReviewRoundState.COMPLETED,
            ),
            review_session=self._round_runtime.snapshot,
            round_receipt=round_receipt,
            observation_proposal=proposal,
            observation_receipt=memory_receipt,
            analytics=analytics,
            completed_at_ms=now,
        )
        rounds = self._replace_current_round(completed_round)
        if (
            self._campaign.current_round_number
            < self._campaign.config.fixed_round_count
        ):
            self._transition(
                ReviewCampaignState.BETWEEN_ROUNDS,
                rounds=rounds,
            )
        else:
            campaign_analytics = self._campaign_analytics(rounds)
            passed_round_count = sum(
                review_round.round_receipt.result.passed
                for review_round in rounds
            )
            campaign_receipt = ReviewCampaignReceipt(
                receipt_id=_stable_digest(
                    {
                        "campaign_id": self._campaign.campaign_id,
                        "round_receipt_ids": [
                            review_round.round_receipt.receipt_id
                            for review_round in rounds
                        ],
                        "analytics": campaign_analytics.model_dump(mode="json"),
                    }
                ),
                campaign_id=self._campaign.campaign_id,
                fixed_round_count=self._campaign.config.fixed_round_count,
                completed_round_count=len(rounds),
                passed_round_count=passed_round_count,
                all_rounds_passed=(
                    passed_round_count == self._campaign.config.fixed_round_count
                ),
                round_receipt_ids=tuple(
                    review_round.round_receipt.receipt_id
                    for review_round in rounds
                ),
                analytics=campaign_analytics,
                completed_at_ms=now,
            )
            self._transition(
                ReviewCampaignState.COMPLETED,
                rounds=rounds,
                analytics=campaign_analytics,
                receipt=campaign_receipt,
            )
        return round_receipt

    def campaign_receipt(self) -> ReviewCampaignReceipt:
        if (
            self._campaign.state is not ReviewCampaignState.COMPLETED
            or self._campaign.receipt is None
        ):
            raise ReviewCampaignError("campaign_receipt_requires_completion")
        return self._campaign.receipt

    def abort(self, reason: str = "campaign_aborted_by_caller") -> ReviewCampaign:
        if self._campaign.state in {
            ReviewCampaignState.COMPLETED,
            ReviewCampaignState.ABORTED,
            ReviewCampaignState.FAILED,
        }:
            raise ReviewCampaignError("terminal_campaign_cannot_abort")
        if self._round_runtime is not None and self._campaign.rounds:
            child_state = self._round_runtime.snapshot.state
            if child_state not in {
                ReviewSessionState.COMPLETED,
                ReviewSessionState.ABORTED,
                ReviewSessionState.FAILED,
            }:
                self._round_runtime.abort(reason)
            failed_round = self._round_with(
                state=ReviewRoundState.FAILED,
                review_session=self._round_runtime.snapshot,
            )
            rounds = self._replace_current_round(failed_round)
        else:
            rounds = self._campaign.rounds
        self._transition(
            ReviewCampaignState.ABORTED,
            rounds=rounds,
            pending_submission_id=None,
            failure_reason=reason,
        )
        return self._campaign


__all__ = ["ReviewCampaignError", "ReviewCampaignRuntime"]
