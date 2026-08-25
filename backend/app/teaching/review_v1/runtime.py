"""Active-recall review runtime with bounded weak-item rolling."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Sequence

from ..contracts import StudentMove
from ..evaluator_v2 import AssessmentAttempt, EvaluatorRequest, EvaluatorV2
from .models import (
    QuestionSourcePolicy,
    ReviewAttempt,
    ReviewAttemptReceipt,
    ReviewCompletionPolicy,
    ReviewCompletionStatus,
    ReviewItem,
    ReviewMemorySnapshot,
    ReviewQuestionSelectionRequest,
    ReviewQuestionView,
    ReviewQueueEntry,
    ReviewResult,
    ReviewRetrievalOutcome,
    ReviewRound,
    ReviewRoundReceipt,
    ReviewSession,
    ReviewSessionCrashDTO,
    ReviewSessionInput,
    ReviewSessionState,
    ReviewWeakItemProposal,
    ReviewWeakReason,
    StudentProgressSnapshot,
    TeacherExplanation,
    TeacherExplanationReceipt,
    TeacherExplanationRequest,
)
from .policy import classify_retrieval, evaluate_completion
from .ports import (
    ReviewMemoryPort,
    StudentProgressPort,
    TeacherExplanationPort,
    TrustedQuestionBankPort,
)


class ReviewSessionError(Exception):
    """Invalid review transition or a fail-closed port contract violation."""


def _stable_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _attempt_fingerprint(attempt: ReviewAttempt) -> str:
    return _stable_digest(attempt.model_dump(mode="json"))


def _queue_entry(
    *,
    round_id: str,
    item_id: str,
    retry_number: int,
) -> ReviewQueueEntry:
    occurrence_number = retry_number + 1
    return ReviewQueueEntry(
        entry_id=_stable_digest(
            {
                "round_id": round_id,
                "item_id": item_id,
                "retry_number": retry_number,
            }
        ),
        item_id=item_id,
        occurrence_number=occurrence_number,
        retry_number=retry_number,
    )


class ReviewSessionRuntime:
    """One immutable, restartable review session with no persistence writes."""

    def __init__(
        self,
        *,
        session: ReviewSession,
        evaluator: EvaluatorV2,
        clock_ms: Callable[[], int],
        teacher_explanation_port: TeacherExplanationPort | None = None,
    ) -> None:
        self._session = session
        self._evaluator = evaluator
        self._clock_ms = clock_ms
        self._teacher_explanation_port = teacher_explanation_port
        self._inflight: dict[
            str,
            tuple[str, asyncio.Future[ReviewAttemptReceipt]],
        ] = {}

    @classmethod
    async def create(
        cls,
        *,
        session_id: str,
        concept_scope: Sequence[str],
        round_number: int,
        question_source_policy: QuestionSourcePolicy,
        target_item_count: int,
        completion_policy: ReviewCompletionPolicy,
        max_same_round_retries: int,
        question_bank: TrustedQuestionBankPort,
        memory_port: ReviewMemoryPort,
        progress_port: StudentProgressPort,
        evaluator: EvaluatorV2,
        clock_ms: Callable[[], int],
        teacher_explanation_port: TeacherExplanationPort | None = None,
    ) -> "ReviewSessionRuntime":
        session_input = ReviewSessionInput(
            concept_scope=tuple(concept_scope),
            round_number=round_number,
            question_source_policy=question_source_policy,
            target_item_count=target_item_count,
            completion_policy=completion_policy,
            max_same_round_retries=max_same_round_retries,
        )
        memory_raw, progress_raw = await asyncio.gather(
            memory_port.load_review_memory(session_input.concept_scope),
            progress_port.load_student_progress(session_input.concept_scope),
        )
        memory = ReviewMemorySnapshot.model_validate(memory_raw)
        progress = StudentProgressSnapshot.model_validate(progress_raw)
        scope = set(session_input.concept_scope)
        if set(memory.concept_scope) != scope:
            raise ReviewSessionError("review_memory_scope_mismatch")
        if set(progress.concept_scope) != scope:
            raise ReviewSessionError("student_progress_scope_mismatch")
        selection = ReviewQuestionSelectionRequest(
            session_id=session_id,
            concept_scope=session_input.concept_scope,
            round_number=round_number,
            source_policy=question_source_policy,
            target_item_count=target_item_count,
            memory=memory,
            progress=progress,
        )
        raw_items = await question_bank.select_trusted_items(selection)
        items = tuple(ReviewItem.model_validate(item) for item in raw_items)
        if len(items) < target_item_count:
            raise ReviewSessionError("insufficient_trusted_review_items")
        selected = items[:target_item_count]
        item_ids = tuple(item.item_id for item in selected)
        if len(item_ids) != len(set(item_ids)):
            raise ReviewSessionError("duplicate_trusted_review_item")
        if any(item.concept_id not in scope for item in selected):
            raise ReviewSessionError("trusted_review_item_outside_scope")
        round_id = _stable_digest(
            {
                "session_id": session_id,
                "round_number": round_number,
                "item_ids": item_ids,
                "memory_position": memory.position_token,
                "progress_position": progress.position_token,
            }
        )
        pending = tuple(
            _queue_entry(round_id=round_id, item_id=item_id, retry_number=0)
            for item_id in item_ids
        )
        review_round = ReviewRound(
            round_id=round_id,
            round_number=round_number,
            items=selected,
            initial_item_ids=item_ids,
            pending_entries=pending,
        )
        now = clock_ms()
        session = ReviewSession(
            session_id=session_id,
            input=session_input,
            round=review_round,
            memory_snapshot=memory,
            progress_snapshot=progress,
            created_at_ms=now,
            updated_at_ms=now,
        )
        return cls(
            session=session,
            evaluator=evaluator,
            clock_ms=clock_ms,
            teacher_explanation_port=teacher_explanation_port,
        )

    @classmethod
    def from_crash_dto(
        cls,
        dto: ReviewSessionCrashDTO,
        *,
        evaluator: EvaluatorV2,
        clock_ms: Callable[[], int],
        teacher_explanation_port: TeacherExplanationPort | None = None,
    ) -> "ReviewSessionRuntime":
        runtime = cls(
            session=dto.session,
            evaluator=evaluator,
            clock_ms=clock_ms,
            teacher_explanation_port=teacher_explanation_port,
        )
        if runtime._session.state is ReviewSessionState.EVALUATING:
            review_round = runtime._round_with(
                current_entry=None,
                pending_entries=(),
            )
            runtime._transition(
                ReviewSessionState.FAILED,
                round=review_round,
                pending_submission_id=None,
                failure_reason="interrupted_inflight_review_evaluation",
            )
        return runtime

    @property
    def snapshot(self) -> ReviewSession:
        return self._session

    def to_crash_dto(self) -> ReviewSessionCrashDTO:
        return ReviewSessionCrashDTO(
            session=self._session,
            exported_at_ms=self._clock_ms(),
        )

    def _replace(self, **changes) -> None:
        payload = self._session.model_dump(mode="python")
        payload.update(changes)
        self._session = ReviewSession.model_validate(payload)

    def _round_with(self, **changes) -> ReviewRound:
        payload = self._session.round.model_dump(mode="python")
        payload.update(changes)
        return ReviewRound.model_validate(payload)

    def _transition(self, state: ReviewSessionState, **changes) -> None:
        history = self._session.state_history
        if not history or history[-1] is not state:
            history = (*history, state)
        self._replace(
            state=state,
            state_history=history,
            updated_at_ms=self._clock_ms(),
            version=self._session.version + 1,
            **changes,
        )

    def _item(self, item_id: str) -> ReviewItem | None:
        return next(
            (
                item
                for item in self._session.round.items
                if item.item_id == item_id
            ),
            None,
        )

    def _current(self) -> tuple[ReviewQueueEntry, ReviewItem]:
        entry = self._session.round.current_entry
        if entry is None:
            raise ReviewSessionError("review_has_no_current_item")
        item = self._item(entry.item_id)
        if item is None:
            raise ReviewSessionError("review_current_item_missing")
        return entry, item

    def start(self) -> ReviewQuestionView:
        if self._session.state is not ReviewSessionState.CREATED:
            raise ReviewSessionError("review_start_requires_created_state")
        if not self._session.round.pending_entries:
            raise ReviewSessionError("review_has_no_selected_items")
        current = self._session.round.pending_entries[0]
        review_round = self._round_with(
            current_entry=current,
            pending_entries=self._session.round.pending_entries[1:],
        )
        self._transition(
            ReviewSessionState.ACTIVE,
            round=review_round,
            failure_reason=None,
        )
        return self.present_question()

    def present_question(self) -> ReviewQuestionView:
        if self._session.state is not ReviewSessionState.ACTIVE:
            raise ReviewSessionError("question_presentation_requires_active_state")
        entry, item = self._current()
        return ReviewQuestionView(
            session_id=self._session.session_id,
            round_id=self._session.round.round_id,
            item_id=item.item_id,
            concept_id=item.concept_id,
            prompt=item.prompt,
            occurrence_number=entry.occurrence_number,
            required=item.required,
            critical=item.critical,
        )

    def pause(self) -> ReviewSession:
        if self._session.state is not ReviewSessionState.ACTIVE:
            raise ReviewSessionError("review_pause_requires_active_state")
        self._transition(ReviewSessionState.PAUSED)
        return self._session

    def resume(self) -> ReviewQuestionView:
        if self._session.state is not ReviewSessionState.PAUSED:
            raise ReviewSessionError("review_resume_requires_paused_state")
        self._transition(ReviewSessionState.ACTIVE)
        return self.present_question()

    def abort(self, reason: str = "review_aborted_by_caller") -> ReviewSession:
        if self._session.state in {
            ReviewSessionState.COMPLETED,
            ReviewSessionState.ABORTED,
            ReviewSessionState.FAILED,
        }:
            raise ReviewSessionError("terminal_review_cannot_abort")
        review_round = self._round_with(
            current_entry=None,
            pending_entries=(),
        )
        self._transition(
            ReviewSessionState.ABORTED,
            round=review_round,
            pending_submission_id=None,
            failure_reason=reason,
        )
        return self._session

    def _stored_attempt(self, submission_id: str) -> ReviewAttempt | None:
        return next(
            (
                item
                for item in self._session.round.attempts
                if item.submission_id == submission_id
            ),
            None,
        )

    def _stored_receipt(self, submission_id: str) -> ReviewAttemptReceipt | None:
        return next(
            (
                item
                for item in self._session.round.attempt_receipts
                if item.submission_id == submission_id
            ),
            None,
        )

    def _duplicate_receipt(
        self,
        receipt: ReviewAttemptReceipt,
    ) -> ReviewAttemptReceipt:
        payload = receipt.model_dump(mode="python")
        payload.update(
            {
                "duplicate": True,
                "provider_calls": 0,
                "state_before": self._session.state,
                "state_after": self._session.state,
                "reason": "duplicate_review_answer_replay",
                "recorded_at_ms": self._clock_ms(),
            }
        )
        return ReviewAttemptReceipt.model_validate(payload)

    def _ephemeral_receipt(
        self,
        attempt: ReviewAttempt,
        *,
        reason: str,
        conflict: bool = False,
        provider_calls: int = 0,
        state_after: ReviewSessionState | None = None,
    ) -> ReviewAttemptReceipt:
        entry = self._session.round.current_entry
        return ReviewAttemptReceipt(
            receipt_id=_stable_digest(
                {
                    "session_id": self._session.session_id,
                    "attempt": attempt.model_dump(mode="json"),
                    "reason": reason,
                }
            ),
            session_id=self._session.session_id,
            round_id=self._session.round.round_id,
            submission_id=attempt.submission_id,
            attempt_id=attempt.attempt_id,
            item_id=attempt.item_id,
            occurrence_number=(entry.occurrence_number if entry is not None else 1),
            accepted=False,
            conflict=conflict,
            provider_calls=provider_calls,
            state_before=self._session.state,
            state_after=state_after or self._session.state,
            reason=reason,
            recorded_at_ms=self._clock_ms(),
        )

    def _validate_attempt(
        self,
        attempt: ReviewAttempt,
    ) -> ReviewAttemptReceipt | None:
        if self._session.state is not ReviewSessionState.ACTIVE:
            return self._ephemeral_receipt(
                attempt,
                reason="review_session_not_active",
            )
        entry, item = self._current()
        if attempt.item_id != entry.item_id:
            return self._ephemeral_receipt(
                attempt,
                reason="wrong_review_item_binding",
                conflict=True,
            )
        if any(
            stored.attempt_id == attempt.attempt_id
            for stored in self._session.round.attempts
        ):
            return self._ephemeral_receipt(
                attempt,
                reason="duplicate_review_attempt_id",
                conflict=True,
            )
        evidence_ids = {evidence.evidence_id for evidence in item.evidence}
        if not set(attempt.evidence_ids) <= evidence_ids:
            return self._ephemeral_receipt(
                attempt,
                reason="review_evidence_binding_mismatch",
                conflict=True,
            )
        return None

    def _evaluator_request(
        self,
        item: ReviewItem,
        attempt: ReviewAttempt,
    ) -> EvaluatorRequest:
        processed = tuple(
            receipt.evaluation_result.assessment_id
            for receipt in self._session.round.attempt_receipts
            if receipt.evaluation_result is not None
        )
        return EvaluatorRequest(
            attempt=AssessmentAttempt(
                assessment_id=attempt.submission_id,
                attempt_id=attempt.attempt_id,
                question=item.prompt,
                answer=attempt.answer,
                student_move=StudentMove.ANSWER_ATTEMPT,
                answer_evidence_refs=attempt.evidence_ids,
            ),
            rubric=item.rubric,
            evidence=item.evidence,
            processed_assessment_ids=processed,
        )

    def _weak_proposal(
        self,
        *,
        item: ReviewItem,
        attempt: ReviewAttempt,
        entry: ReviewQueueEntry,
        outcome: ReviewRetrievalOutcome,
        weak_reason: ReviewWeakReason,
    ) -> ReviewWeakItemProposal:
        retryable = weak_reason in {
            ReviewWeakReason.PARTIAL_RECALL,
            ReviewWeakReason.RETRIEVAL_FAILURE,
            ReviewWeakReason.HINT_DEPENDENCY,
            ReviewWeakReason.ANSWER_REVEALED,
        }
        eligible = (
            retryable
            and entry.retry_number < self._session.input.max_same_round_retries
        )
        next_retry = min(
            entry.retry_number + 1,
            self._session.input.max_same_round_retries,
        )
        return ReviewWeakItemProposal(
            proposal_id=_stable_digest(
                {
                    "session_id": self._session.session_id,
                    "round_id": self._session.round.round_id,
                    "attempt_id": attempt.attempt_id,
                    "outcome": outcome.value,
                    "reason": weak_reason.value,
                }
            ),
            session_id=self._session.session_id,
            round_id=self._session.round.round_id,
            item_id=item.item_id,
            concept_id=item.concept_id,
            attempt_id=attempt.attempt_id,
            outcome=outcome,
            reason=weak_reason,
            same_round_retry_number=next_retry,
            same_round_retry_eligible=eligible,
        )

    async def _evaluate_attempt(
        self,
        attempt: ReviewAttempt,
    ) -> ReviewAttemptReceipt:
        validation = self._validate_attempt(attempt)
        if validation is not None:
            return validation
        entry, item = self._current()
        state_before = self._session.state
        prior_item_attempts = tuple(
            stored
            for stored in self._session.round.attempts
            if stored.item_id == item.item_id
        )
        evaluating_round = self._round_with(
            attempts=(*self._session.round.attempts, attempt),
        )
        self._transition(
            ReviewSessionState.EVALUATING,
            round=evaluating_round,
            pending_submission_id=attempt.submission_id,
        )
        try:
            decision = await self._evaluator.evaluate(
                self._evaluator_request(item, attempt)
            )
        except Exception as exc:
            if (
                self._session.state is ReviewSessionState.ABORTED
                or self._session.pending_submission_id != attempt.submission_id
            ):
                return self._ephemeral_receipt(
                    attempt,
                    reason="review_aborted_during_evaluation",
                    state_after=ReviewSessionState.ABORTED,
                )
            reason = f"review_evaluator_runtime_error:{type(exc).__name__}"
            receipt = self._ephemeral_receipt(
                attempt,
                reason=reason,
                state_after=ReviewSessionState.FAILED,
            )
            failed_round = self._round_with(
                current_entry=None,
                pending_entries=(),
                attempt_receipts=(*self._session.round.attempt_receipts, receipt),
            )
            self._transition(
                ReviewSessionState.FAILED,
                round=failed_round,
                pending_submission_id=None,
                failure_reason=reason,
            )
            return receipt

        if (
            self._session.state is ReviewSessionState.ABORTED
            or self._session.pending_submission_id != attempt.submission_id
        ):
            return self._ephemeral_receipt(
                attempt,
                reason="review_aborted_during_evaluation",
                provider_calls=decision.result.provider_calls,
                state_after=ReviewSessionState.ABORTED,
            )
        result = decision.result
        if (
            result.assessment_id != attempt.submission_id
            or result.attempt_id != attempt.attempt_id
            or result.rubric_id != item.rubric.rubric_id
        ):
            reason = "review_evaluator_result_binding_mismatch"
            receipt = self._ephemeral_receipt(
                attempt,
                reason=reason,
                provider_calls=result.provider_calls,
                state_after=ReviewSessionState.FAILED,
            )
            failed_round = self._round_with(
                current_entry=None,
                pending_entries=(),
                attempt_receipts=(*self._session.round.attempt_receipts, receipt),
            )
            self._transition(
                ReviewSessionState.FAILED,
                round=failed_round,
                pending_submission_id=None,
                failure_reason=reason,
            )
            return receipt

        outcome, retrieval_score, weak_reason = classify_retrieval(
            attempt=attempt,
            result=result,
            retry_number=entry.retry_number,
            prior_item_attempts=prior_item_attempts,
            policy=self._session.input.completion_policy,
        )
        weak_proposal = (
            self._weak_proposal(
                item=item,
                attempt=attempt,
                entry=entry,
                outcome=outcome,
                weak_reason=weak_reason,
            )
            if weak_reason is not None
            else None
        )
        pending = self._session.round.pending_entries
        if weak_proposal is not None and weak_proposal.same_round_retry_eligible:
            pending = (
                *pending,
                _queue_entry(
                    round_id=self._session.round.round_id,
                    item_id=item.item_id,
                    retry_number=weak_proposal.same_round_retry_number,
                ),
            )
        next_entry = pending[0] if pending else None
        remaining = pending[1:] if pending else ()
        state_after = (
            ReviewSessionState.ACTIVE
            if next_entry is not None
            else ReviewSessionState.READY_TO_COMPLETE
        )
        receipt = ReviewAttemptReceipt(
            receipt_id=_stable_digest(
                {
                    "session_id": self._session.session_id,
                    "attempt": attempt.model_dump(mode="json"),
                    "evaluation": result.model_dump(mode="json"),
                    "outcome": outcome.value,
                }
            ),
            session_id=self._session.session_id,
            round_id=self._session.round.round_id,
            submission_id=attempt.submission_id,
            attempt_id=attempt.attempt_id,
            item_id=attempt.item_id,
            occurrence_number=entry.occurrence_number,
            accepted=True,
            evaluation_result=result,
            retrieval_outcome=outcome,
            retrieval_score=retrieval_score,
            weak_item_proposal=weak_proposal,
            provider_calls=result.provider_calls,
            state_before=state_before,
            state_after=state_after,
            reason=result.reason,
            recorded_at_ms=self._clock_ms(),
        )
        proposals = self._session.round.weak_item_proposals
        if weak_proposal is not None:
            proposals = (*proposals, weak_proposal)
        reviewed_round = self._round_with(
            current_entry=next_entry,
            pending_entries=remaining,
            attempt_receipts=(*self._session.round.attempt_receipts, receipt),
            weak_item_proposals=proposals,
        )
        self._transition(
            state_after,
            round=reviewed_round,
            pending_submission_id=None,
        )
        return receipt

    async def submit(self, attempt: ReviewAttempt) -> ReviewAttemptReceipt:
        fingerprint = _attempt_fingerprint(attempt)
        stored = self._stored_attempt(attempt.submission_id)
        cached = self._stored_receipt(attempt.submission_id)
        if stored is not None:
            if _attempt_fingerprint(stored) != fingerprint:
                return self._ephemeral_receipt(
                    attempt,
                    reason="conflicting_review_submission_identity",
                    conflict=True,
                )
            if cached is not None:
                return self._duplicate_receipt(cached)

        inflight = self._inflight.get(attempt.submission_id)
        if inflight is not None:
            if inflight[0] != fingerprint:
                return self._ephemeral_receipt(
                    attempt,
                    reason="conflicting_inflight_review_submission",
                    conflict=True,
                )
            receipt = await asyncio.shield(inflight[1])
            return self._duplicate_receipt(receipt)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[ReviewAttemptReceipt] = loop.create_future()
        self._inflight[attempt.submission_id] = (fingerprint, future)
        try:
            receipt = await self._evaluate_attempt(attempt)
            if not future.done():
                future.set_result(receipt)
            return receipt
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
                future.exception()
            raise
        finally:
            self._inflight.pop(attempt.submission_id, None)

    async def ask_why(self, learner_question: str) -> TeacherExplanationReceipt:
        if self._session.state is not ReviewSessionState.ACTIVE:
            raise ReviewSessionError("teacher_explanation_requires_active_review")
        _, item = self._current()
        request = TeacherExplanationRequest(
            session_id=self._session.session_id,
            round_id=self._session.round.round_id,
            item_id=item.item_id,
            concept_id=item.concept_id,
            review_question=item.prompt,
            learner_question=learner_question,
        )
        receipt_seed = {
            "request": request.model_dump(mode="json"),
            "ordinal": len(self._session.explanation_receipts) + 1,
        }
        if self._teacher_explanation_port is None:
            receipt = TeacherExplanationReceipt(
                receipt_id=_stable_digest(receipt_seed),
                request=request,
                available=False,
                provider_calls=0,
                reason="teacher_explanation_port_unavailable",
                recorded_at_ms=self._clock_ms(),
            )
        else:
            try:
                raw = await self._teacher_explanation_port.explain(request)
                explanation = TeacherExplanation.model_validate(raw)
                evidence_ids = {
                    evidence.evidence_id for evidence in item.evidence
                }
                if not set(explanation.evidence_refs) <= evidence_ids:
                    raise ValueError("teacher explanation fabricated evidence")
                receipt = TeacherExplanationReceipt(
                    receipt_id=_stable_digest(receipt_seed),
                    request=request,
                    available=True,
                    explanation=explanation,
                    provider_calls=explanation.provider_calls,
                    reason="explicit_teacher_explanation_completed",
                    recorded_at_ms=self._clock_ms(),
                )
            except Exception as exc:
                receipt = TeacherExplanationReceipt(
                    receipt_id=_stable_digest(receipt_seed),
                    request=request,
                    available=False,
                    provider_calls=1,
                    reason=f"teacher_explanation_failed:{type(exc).__name__}",
                    recorded_at_ms=self._clock_ms(),
                )
        self._replace(
            explanation_receipts=(
                *self._session.explanation_receipts,
                receipt,
            ),
            updated_at_ms=self._clock_ms(),
            version=self._session.version + 1,
        )
        return receipt

    def complete(self) -> ReviewRoundReceipt:
        if self._session.state is not ReviewSessionState.READY_TO_COMPLETE:
            raise ReviewSessionError("review_completion_requires_exhausted_queue")
        completion, item_results = evaluate_completion(
            items=self._session.round.items,
            attempts=self._session.round.attempts,
            receipts=self._session.round.attempt_receipts,
            policy=self._session.input.completion_policy,
        )
        now = self._clock_ms()
        result = ReviewResult(
            session_id=self._session.session_id,
            round_id=self._session.round.round_id,
            status=(
                ReviewCompletionStatus.PASSED
                if completion.passed
                else ReviewCompletionStatus.NOT_PASSED
            ),
            passed=completion.passed,
            completion=completion,
            item_results=item_results,
            total_attempts=len(self._session.round.attempts),
            evaluator_provider_calls=sum(
                receipt.provider_calls
                for receipt in self._session.round.attempt_receipts
            ),
            teacher_provider_calls=sum(
                receipt.provider_calls
                for receipt in self._session.explanation_receipts
            ),
            completed_at_ms=now,
        )
        round_receipt = ReviewRoundReceipt(
            receipt_id=_stable_digest(result.model_dump(mode="json")),
            session_id=self._session.session_id,
            round_id=self._session.round.round_id,
            round_number=self._session.round.round_number,
            result=result,
            attempt_receipt_ids=tuple(
                receipt.receipt_id
                for receipt in self._session.round.attempt_receipts
            ),
            weak_proposal_ids=tuple(
                proposal.proposal_id
                for proposal in self._session.round.weak_item_proposals
            ),
            recorded_at_ms=now,
        )
        self._transition(
            ReviewSessionState.COMPLETED,
            result=result,
            round_receipt=round_receipt,
        )
        return round_receipt


__all__ = ["ReviewSessionError", "ReviewSessionRuntime"]
