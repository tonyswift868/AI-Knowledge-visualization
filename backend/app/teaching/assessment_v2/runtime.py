"""In-memory Assessment Session runtime bound to EvaluatorV2."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Sequence

from ..evaluator_v2 import (
    AssessmentAttempt,
    EvaluationEvidence,
    EvaluationRubric,
    EvaluatorDecision,
    EvaluatorRequest,
    EvaluatorV2,
    ProposedLearningEventType,
)
from .models import (
    AssessmentCommitPlan,
    AssessmentQuestion,
    AssessmentReceipt,
    AssessmentSession,
    AssessmentSessionRestartDTO,
    AssessmentSessionResult,
    AssessmentSessionState,
    AssessmentSubmission,
    VerificationPolicy,
    VerificationQualification,
    VerificationStatus,
)
from .policy import qualify_verification, rejected_qualification


class AssessmentSessionError(Exception):
    """Invalid runtime transition that carries no learning-state write."""


def _stable_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _submission_fingerprint(submission: AssessmentSubmission) -> str:
    return _stable_digest(submission.model_dump(mode="json"))


def _receipt_id(
    session_id: str,
    submission: AssessmentSubmission,
    reason: str,
) -> str:
    return _stable_digest(
        {
            "session_id": session_id,
            "submission": submission.model_dump(mode="json"),
            "reason": reason,
        }
    )


def _plan_id(
    session_id: str,
    submission_id: str,
    decision: EvaluatorDecision,
    verification: VerificationQualification,
) -> str:
    return _stable_digest(
        {
            "session_id": session_id,
            "submission_id": submission_id,
            "decision": decision.model_dump(mode="json"),
            "verification": verification.model_dump(mode="json"),
        }
    )


class AssessmentSessionRuntime:
    """One restartable session runtime; only its own immutable DTO is updated."""

    def __init__(
        self,
        *,
        session: AssessmentSession,
        evaluator: EvaluatorV2,
        clock_ms: Callable[[], int],
    ) -> None:
        self._session = session
        self._evaluator = evaluator
        self._clock_ms = clock_ms
        self._inflight: dict[
            str,
            tuple[str, asyncio.Future[AssessmentReceipt]],
        ] = {}

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        concept_id: str,
        questions: Sequence[AssessmentQuestion],
        rubrics: Sequence[EvaluationRubric],
        evidence: Sequence[EvaluationEvidence] = (),
        evaluator: EvaluatorV2,
        clock_ms: Callable[[], int],
        verification_policy: VerificationPolicy | None = None,
    ) -> "AssessmentSessionRuntime":
        now = clock_ms()
        session = AssessmentSession(
            session_id=session_id,
            concept_id=concept_id,
            questions=tuple(questions),
            rubrics=tuple(rubrics),
            evidence=tuple(evidence),
            verification_policy=verification_policy or VerificationPolicy(),
            created_at_ms=now,
            updated_at_ms=now,
        )
        return cls(session=session, evaluator=evaluator, clock_ms=clock_ms)

    @classmethod
    def from_restart_dto(
        cls,
        dto: AssessmentSessionRestartDTO,
        *,
        evaluator: EvaluatorV2,
        clock_ms: Callable[[], int],
    ) -> "AssessmentSessionRuntime":
        runtime = cls(session=dto.session, evaluator=evaluator, clock_ms=clock_ms)
        if runtime._session.state in {
            AssessmentSessionState.SUBMITTED,
            AssessmentSessionState.EVALUATING,
        }:
            runtime._transition(
                AssessmentSessionState.FAILED,
                pending_submission_id=None,
                failure_reason="interrupted_inflight_evaluation",
            )
        return runtime

    @property
    def snapshot(self) -> AssessmentSession:
        return self._session

    def to_restart_dto(self) -> AssessmentSessionRestartDTO:
        return AssessmentSessionRestartDTO(
            session=self._session,
            exported_at_ms=self._clock_ms(),
        )

    def _replace(self, **changes) -> None:
        payload = self._session.model_dump(mode="python")
        payload.update(changes)
        self._session = AssessmentSession.model_validate(payload)

    def _transition(self, state: AssessmentSessionState, **changes) -> None:
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

    def _question(self, question_id: str) -> AssessmentQuestion | None:
        return next(
            (
                item
                for item in self._session.questions
                if item.question_id == question_id
            ),
            None,
        )

    def _rubric(self, rubric_id: str):
        return next(
            (item for item in self._session.rubrics if item.rubric_id == rubric_id),
            None,
        )

    def _attempt_number(self, question_id: str) -> int:
        return 1 + sum(
            item.question_id == question_id for item in self._session.submissions
        )

    def start(self, question_id: str | None = None) -> AssessmentSession:
        if self._session.state is not AssessmentSessionState.CREATED:
            raise AssessmentSessionError("session_start_requires_created_state")
        selected = question_id or self._session.questions[0].question_id
        if self._question(selected) is None:
            raise AssessmentSessionError("unknown_start_question")
        self._transition(
            AssessmentSessionState.ACTIVE,
            active_question_id=selected,
            failure_reason=None,
        )
        return self._session

    def resume(self, question_id: str | None = None) -> AssessmentSession:
        if self._session.state is not AssessmentSessionState.EVALUATED:
            raise AssessmentSessionError("session_resume_requires_evaluated_state")
        selected = question_id or self._session.active_question_id
        if selected is None or self._question(selected) is None:
            raise AssessmentSessionError("unknown_resume_question")
        self._transition(
            AssessmentSessionState.ACTIVE,
            active_question_id=selected,
            pending_submission_id=None,
        )
        return self._session

    async def abort(self, reason: str = "aborted_by_caller") -> AssessmentSession:
        if self._session.state in {
            AssessmentSessionState.COMPLETED,
            AssessmentSessionState.ABORTED,
            AssessmentSessionState.FAILED,
        }:
            raise AssessmentSessionError("terminal_session_cannot_abort")
        self._transition(
            AssessmentSessionState.ABORTED,
            pending_submission_id=None,
            failure_reason=reason,
        )
        return self._session

    def complete(self) -> AssessmentSessionResult:
        if self._session.state is not AssessmentSessionState.EVALUATED:
            raise AssessmentSessionError("session_completion_requires_evaluated_state")
        evaluated = tuple(
            receipt
            for receipt in self._session.receipts
            if receipt.evaluation_result is not None
        )
        if not evaluated:
            raise AssessmentSessionError("session_has_no_evaluated_attempt")
        scores = tuple(
            receipt.evaluation_result.score
            for receipt in evaluated
            if receipt.evaluation_result is not None
            and receipt.evaluation_result.score is not None
        )
        latest = evaluated[-1].verification
        now = self._clock_ms()
        result = AssessmentSessionResult(
            session_id=self._session.session_id,
            final_state=AssessmentSessionState.COMPLETED,
            evaluated_attempts=len(evaluated),
            average_score=(sum(scores) / len(scores) if scores else None),
            final_verification=latest,
            receipt_ids=tuple(item.receipt_id for item in self._session.receipts),
            commit_plan_ids=tuple(item.plan_id for item in self._session.commit_plans),
            completed_at_ms=now,
        )
        self._transition(
            AssessmentSessionState.COMPLETED,
            pending_submission_id=None,
            result=result,
        )
        return result

    def _stored_submission(self, submission_id: str) -> AssessmentSubmission | None:
        return next(
            (
                item
                for item in self._session.submissions
                if item.submission_id == submission_id
            ),
            None,
        )

    def _stored_receipt(self, submission_id: str) -> AssessmentReceipt | None:
        return next(
            (
                item
                for item in self._session.receipts
                if item.submission_id == submission_id
            ),
            None,
        )

    def _duplicate_receipt(self, receipt: AssessmentReceipt) -> AssessmentReceipt:
        payload = receipt.model_dump(mode="python")
        payload.update(
            {
                "duplicate": True,
                "provider_calls": 0,
                "reason": "duplicate_submission_replay",
                "state_before": self._session.state,
                "state_after": self._session.state,
                "recorded_at_ms": self._clock_ms(),
            }
        )
        return AssessmentReceipt.model_validate(payload)

    def _fallback_question(self, question_id: str) -> AssessmentQuestion:
        return self._question(question_id) or self._session.questions[0]

    def _ephemeral_receipt(
        self,
        submission: AssessmentSubmission,
        *,
        reason: str,
        conflict: bool = False,
        state_after: AssessmentSessionState | None = None,
        attempt_number: int | None = None,
        provider_calls: int = 0,
        insufficient: bool = False,
    ) -> AssessmentReceipt:
        question = self._fallback_question(submission.question_id)
        qualification = rejected_qualification(
            policy=self._session.verification_policy,
            question=question,
            reason=reason,
            insufficient=insufficient,
        )
        return AssessmentReceipt(
            receipt_id=_receipt_id(self._session.session_id, submission, reason),
            session_id=self._session.session_id,
            submission_id=submission.submission_id,
            attempt_id=submission.attempt_id,
            question_id=submission.question_id,
            attempt_number=(
                attempt_number or self._attempt_number(submission.question_id)
            ),
            accepted=False,
            conflict=conflict,
            state_before=self._session.state,
            state_after=state_after or self._session.state,
            verification=qualification,
            provider_calls=provider_calls,
            reason=reason,
            recorded_at_ms=self._clock_ms(),
        )

    def _record_rejection(
        self,
        submission: AssessmentSubmission,
        receipt: AssessmentReceipt,
        *,
        state: AssessmentSessionState | None = None,
        failure_reason: str | None = None,
    ) -> AssessmentReceipt:
        changes = {
            "submissions": (*self._session.submissions, submission),
            "receipts": (*self._session.receipts, receipt),
        }
        if state is None:
            self._replace(
                **changes,
                updated_at_ms=self._clock_ms(),
                version=self._session.version + 1,
            )
        else:
            self._transition(
                state,
                **changes,
                pending_submission_id=None,
                failure_reason=failure_reason,
            )
        return receipt

    def _validate_submission(
        self,
        submission: AssessmentSubmission,
    ) -> AssessmentReceipt | None:
        if self._session.state is not AssessmentSessionState.ACTIVE:
            return self._ephemeral_receipt(
                submission,
                reason="session_not_active",
            )
        question = self._question(submission.question_id)
        if (
            question is None
            or submission.question_id != self._session.active_question_id
        ):
            receipt = self._ephemeral_receipt(
                submission,
                reason="wrong_question_binding",
                state_after=AssessmentSessionState.FAILED,
            )
            return self._record_rejection(
                submission,
                receipt,
                state=AssessmentSessionState.FAILED,
                failure_reason="wrong_question_binding",
            )
        if submission.rubric_id != question.rubric_id or self._rubric(
            submission.rubric_id
        ) is None:
            receipt = self._ephemeral_receipt(
                submission,
                reason="rubric_mismatch",
                state_after=AssessmentSessionState.FAILED,
            )
            return self._record_rejection(
                submission,
                receipt,
                state=AssessmentSessionState.FAILED,
                failure_reason="rubric_mismatch",
            )
        if set(submission.evidence_ids) != set(question.evidence_ids):
            receipt = self._ephemeral_receipt(
                submission,
                reason="evidence_binding_mismatch",
                state_after=AssessmentSessionState.FAILED,
            )
            return self._record_rejection(
                submission,
                receipt,
                state=AssessmentSessionState.FAILED,
                failure_reason="evidence_binding_mismatch",
            )
        now = max(self._clock_ms(), submission.submitted_at_ms)
        if question.expires_at_ms is not None and now > question.expires_at_ms:
            receipt = self._ephemeral_receipt(submission, reason="attempt_expired")
            return self._record_rejection(submission, receipt)

        if any(
            item.attempt_id == submission.attempt_id
            for item in self._session.submissions
        ):
            return self._ephemeral_receipt(
                submission,
                reason="duplicate_attempt_id",
                conflict=True,
            )
        prior = tuple(
            item
            for item in self._session.submissions
            if item.question_id == submission.question_id
        )
        if len(prior) >= question.max_attempts:
            receipt = self._ephemeral_receipt(
                submission,
                reason="attempt_limit_reached",
            )
            return self._record_rejection(submission, receipt)
        if not prior and submission.retry_of_attempt_id is not None:
            return self._ephemeral_receipt(
                submission,
                reason="retry_without_prior_attempt",
            )
        if prior and submission.retry_of_attempt_id != prior[-1].attempt_id:
            return self._ephemeral_receipt(
                submission,
                reason="retry_identity_mismatch",
            )
        return None

    def _evaluator_request(
        self,
        question: AssessmentQuestion,
        submission: AssessmentSubmission,
    ) -> EvaluatorRequest:
        rubric = self._rubric(question.rubric_id)
        if rubric is None:
            raise AssessmentSessionError("bound_rubric_missing")
        evidence_by_id = {
            item.evidence_id: item for item in self._session.evidence
        }
        selected_evidence = tuple(
            evidence_by_id[evidence_id] for evidence_id in question.evidence_ids
        )
        processed = tuple(
            receipt.evaluation_result.assessment_id
            for receipt in self._session.receipts
            if receipt.evaluation_result is not None
        )
        return EvaluatorRequest(
            attempt=AssessmentAttempt(
                assessment_id=submission.submission_id,
                attempt_id=submission.attempt_id,
                question=question.prompt,
                answer=submission.answer,
                student_move=submission.student_move,
                self_attested_mastery=submission.self_attested_mastery,
                answer_evidence_refs=submission.evidence_ids,
            ),
            rubric=rubric,
            evidence=selected_evidence,
            processed_assessment_ids=processed,
        )

    def _commit_plan(
        self,
        submission: AssessmentSubmission,
        decision: EvaluatorDecision,
        verification: VerificationQualification,
    ) -> AssessmentCommitPlan:
        independent = ProposedLearningEventType.INDEPENDENT_VERIFICATION_SIGNAL
        events = []
        seen_types = set()
        for event in decision.proposed_learning_events:
            if event.event_type is independent and (
                verification.status is not VerificationStatus.QUALIFIED
                or not verification.question_qualified
            ):
                continue
            if event.event_type in seen_types:
                continue
            events.append(event)
            seen_types.add(event.event_type)
        reason = (
            "proposals include a policy-qualified independent-verification signal"
            if independent in seen_types
            else "proposals require a future commit port; verification is not qualified"
        )
        return AssessmentCommitPlan(
            plan_id=_plan_id(
                self._session.session_id,
                submission.submission_id,
                decision,
                verification,
            ),
            session_id=self._session.session_id,
            submission_id=submission.submission_id,
            verification=verification,
            proposed_events=tuple(events),
            reason=reason,
        )

    async def _evaluate_submission(
        self,
        submission: AssessmentSubmission,
    ) -> AssessmentReceipt:
        validation = self._validate_submission(submission)
        if validation is not None:
            return validation
        question = self._question(submission.question_id)
        if question is None:
            raise AssessmentSessionError("validated_question_missing")
        rubric = self._rubric(submission.rubric_id)
        if rubric is None:
            raise AssessmentSessionError("validated_rubric_missing")
        attempt_number = self._attempt_number(question.question_id)
        state_before = self._session.state
        self._transition(
            AssessmentSessionState.SUBMITTED,
            submissions=(*self._session.submissions, submission),
            pending_submission_id=submission.submission_id,
        )
        self._transition(AssessmentSessionState.EVALUATING)
        try:
            decision = await self._evaluator.evaluate(
                self._evaluator_request(question, submission)
            )
        except Exception as exc:
            reason = f"evaluator_runtime_error:{type(exc).__name__}"
            if (
                self._session.state is AssessmentSessionState.ABORTED
                or self._session.pending_submission_id != submission.submission_id
            ):
                return self._ephemeral_receipt(
                    submission,
                    reason="session_aborted_during_evaluation",
                    state_after=AssessmentSessionState.ABORTED,
                    attempt_number=attempt_number,
                    insufficient=True,
                )
            receipt = self._ephemeral_receipt(
                submission,
                reason=reason,
                state_after=AssessmentSessionState.FAILED,
                attempt_number=attempt_number,
                insufficient=True,
            )
            self._transition(
                AssessmentSessionState.FAILED,
                pending_submission_id=None,
                receipts=(*self._session.receipts, receipt),
                failure_reason=reason,
            )
            return receipt

        if (
            self._session.state is AssessmentSessionState.ABORTED
            or self._session.pending_submission_id != submission.submission_id
        ):
            return self._ephemeral_receipt(
                submission,
                reason="session_aborted_during_evaluation",
                state_after=AssessmentSessionState.ABORTED,
                attempt_number=attempt_number,
                provider_calls=decision.result.provider_calls,
                insufficient=True,
            )

        verification = qualify_verification(
            policy=self._session.verification_policy,
            question=question,
            rubric=rubric,
            submission=submission,
            result=decision.result,
            attempt_number=attempt_number,
            prior_receipts=self._session.receipts,
        )
        plan = self._commit_plan(submission, decision, verification)
        receipt = AssessmentReceipt(
            receipt_id=_receipt_id(
                self._session.session_id,
                submission,
                decision.result.reason,
            ),
            session_id=self._session.session_id,
            submission_id=submission.submission_id,
            attempt_id=submission.attempt_id,
            question_id=submission.question_id,
            attempt_number=attempt_number,
            accepted=True,
            state_before=state_before,
            state_after=AssessmentSessionState.EVALUATED,
            evaluation_result=decision.result,
            verification=verification,
            commit_plan=plan,
            provider_calls=decision.result.provider_calls,
            reason=decision.result.reason,
            recorded_at_ms=self._clock_ms(),
        )
        self._transition(
            AssessmentSessionState.EVALUATED,
            pending_submission_id=None,
            receipts=(*self._session.receipts, receipt),
            commit_plans=(*self._session.commit_plans, plan),
        )
        return receipt

    def _conflict_receipt(
        self,
        submission: AssessmentSubmission,
        reason: str,
    ) -> AssessmentReceipt:
        return self._ephemeral_receipt(
            submission,
            reason=reason,
            conflict=True,
        )

    async def submit(self, submission: AssessmentSubmission) -> AssessmentReceipt:
        fingerprint = _submission_fingerprint(submission)
        stored = self._stored_submission(submission.submission_id)
        cached = self._stored_receipt(submission.submission_id)
        if stored is not None:
            if _submission_fingerprint(stored) != fingerprint:
                return self._conflict_receipt(
                    submission,
                    "conflicting_submission_identity",
                )
            if cached is not None:
                return self._duplicate_receipt(cached)

        inflight = self._inflight.get(submission.submission_id)
        if inflight is not None:
            if inflight[0] != fingerprint:
                return self._conflict_receipt(
                    submission,
                    "conflicting_inflight_submission",
                )
            receipt = await asyncio.shield(inflight[1])
            return self._duplicate_receipt(receipt)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[AssessmentReceipt] = loop.create_future()
        self._inflight[submission.submission_id] = (fingerprint, future)
        try:
            receipt = await self._evaluate_submission(submission)
            if not future.done():
                future.set_result(receipt)
            return receipt
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            self._inflight.pop(submission.submission_id, None)


__all__ = ["AssessmentSessionError", "AssessmentSessionRuntime"]
