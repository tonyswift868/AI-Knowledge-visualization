"""Application service over Review Campaign v2 with privacy-safe projections."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

from ..teaching.contracts import HintDependency
from ..teaching.review_campaign_v2 import (
    ReviewCampaignFeedbackPolicy,
    ReviewCampaignResumeDTO,
    ReviewCampaignRuntime,
    ReviewCampaignState,
    ReviewExposureEventType,
    ReviewFeedbackMode,
)
from ..teaching.review_v1 import (
    QuestionSourcePolicy,
    ReviewAttempt,
    ReviewCompletionPolicy,
    ReviewQuestionView,
    ReviewRetrievalOutcome,
)
from .adapters import ReviewProductAdapters
from .demo_fixture import CONCEPT_BY_ID, DEMO_CONCEPTS, QUESTION_BY_ID


OUTCOME_UX = {
    ReviewRetrievalOutcome.RECALLED: ("答对", "success"),
    ReviewRetrievalOutcome.RECALLED_WITH_DIFFICULTY: ("比较困难但答出", "caution"),
    ReviewRetrievalOutcome.PARTIAL: ("部分正确", "partial"),
    ReviewRetrievalOutcome.FAILED: ("错误", "error"),
    ReviewRetrievalOutcome.HINT_DEPENDENT: ("依赖提示", "hint"),
}
WEAK_REPEAT_MESSAGE = "这个知识点刚才掌握不稳，再确认一次。"


class ReviewProductError(Exception):
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class CampaignRecord:
    runtime: ReviewCampaignRuntime
    adapters: ReviewProductAdapters
    local_only_operations: int = 0
    question_started_at_ms: int | None = None
    hint_keys: set[tuple[int, str, int]] = field(default_factory=set)
    revealed_keys: set[tuple[int, str]] = field(default_factory=set)
    last_result: dict | None = None
    last_round_result: dict | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(frozen=True)
class ReviewRecoveryFixture:
    """Internal close/crash fixture; never serialized by the public API."""

    resume_dto: ReviewCampaignResumeDTO
    observations: dict
    accepted_proposal_ids: frozenset[str]
    local_only_operations: int
    question_started_at_ms: int | None
    hint_keys: frozenset[tuple[int, str, int]]
    revealed_keys: frozenset[tuple[int, str]]
    last_result: dict | None
    last_round_result: dict | None


class ReviewProductService:
    def __init__(self, *, clock_ms: Callable[[], int] | None = None) -> None:
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1_000))
        self._campaigns: dict[str, CampaignRecord] = {}

    def _record(self, campaign_id: str) -> CampaignRecord:
        record = self._campaigns.get(campaign_id)
        if record is None:
            raise ReviewProductError(
                "REVIEW_CAMPAIGN_NOT_FOUND",
                "没有找到这次复习活动，请重新开始。",
                404,
            )
        return record

    def create_demo_campaign(self) -> dict:
        campaign_id = f"review-demo-{uuid4().hex[:16]}"
        adapters = ReviewProductAdapters.offline_demo()
        runtime = ReviewCampaignRuntime.create(
            campaign_id=campaign_id,
            concept_scope=tuple(item.concept_id for item in DEMO_CONCEPTS),
            fixed_round_count=3,
            target_item_count=10,
            question_source_policy=QuestionSourcePolicy.WEAK_FIRST,
            completion_policy=ReviewCompletionPolicy(
                policy_id="review-product-demo-pass.v1",
                require_all_required_items_pass=True,
                minimum_retrieval_score=0.8,
                require_critical_concepts_pass=True,
            ),
            max_same_round_retries=1,
            feedback_policy=ReviewCampaignFeedbackPolicy(
                policy_id="review-product-local-feedback.v1",
                mode=ReviewFeedbackMode.LOCAL_MINIMAL,
                max_teacher_feedback_calls_per_round=0,
            ),
            question_bank=adapters.question_bank,
            memory_port=adapters.review_memory,
            progress_port=adapters.student_progress,
            budget_port=adapters.budget,
            evaluator=adapters.evaluator,
            clock_ms=self._clock_ms,
            teacher_explanation_port=adapters.teacher_explanation,
        )
        record = CampaignRecord(
            runtime=runtime,
            adapters=adapters,
            local_only_operations=1,
        )
        self._campaigns[campaign_id] = record
        return self._projection(record)

    async def start_round(self, campaign_id: str) -> dict:
        record = self._record(campaign_id)
        async with record.lock:
            state = record.runtime.snapshot.state
            try:
                if state is ReviewCampaignState.CREATED:
                    await record.runtime.start()
                elif state is ReviewCampaignState.BETWEEN_ROUNDS:
                    await record.runtime.start_next_round()
                else:
                    raise ReviewProductError(
                        "REVIEW_ROUND_START_INVALID",
                        "当前状态不能开始新一轮。",
                    )
            except ReviewProductError:
                raise
            except Exception as exc:
                raise self._runtime_error(exc) from exc
            record.last_result = None
            record.last_round_result = None
            record.question_started_at_ms = self._clock_ms()
            record.local_only_operations += 1
            return self._projection(record)

    def get_campaign(self, campaign_id: str) -> dict:
        return self._projection(self._record(campaign_id))

    def list_campaigns(self) -> list[dict]:
        """Discover only this bound service's already restored campaigns."""
        return [
            {key: view[key] for key in ('campaign_id', 'state', 'round')}
            for view in (self.get_campaign(key) for key in sorted(self._campaigns))
        ]

    async def next_question(self, campaign_id: str) -> dict:
        record = self._record(campaign_id)
        async with record.lock:
            if record.runtime.snapshot.state is not ReviewCampaignState.ROUND_ACTIVE:
                raise ReviewProductError(
                    "REVIEW_QUESTION_UNAVAILABLE",
                    "当前没有可继续的题目。",
                )
            record.runtime.present_question()
            record.last_result = None
            record.question_started_at_ms = self._clock_ms()
            record.local_only_operations += 1
            return self._projection(record)

    async def request_hint(self, campaign_id: str) -> dict:
        record = self._record(campaign_id)
        async with record.lock:
            question = self._current_question(record)
            key = (
                record.runtime.snapshot.current_round_number,
                question.item_id,
                question.occurrence_number,
            )
            record.hint_keys.add(key)
            record.local_only_operations += 1
            return {
                **self._projection(record),
                "hint": {
                    "hint_id": f"demo-hint-{question.item_id}-{question.occurrence_number}",
                    "text": QUESTION_BY_ID[question.item_id].hint,
                    "source": "local_demo",
                    "provider_calls": 0,
                },
            }

    async def request_explanation(
        self,
        campaign_id: str,
        learner_question: str,
    ) -> dict:
        record = self._record(campaign_id)
        async with record.lock:
            self._current_question(record)
            try:
                feedback = await record.runtime.ask_why(learner_question)
            except Exception as exc:
                raise self._runtime_error(exc) from exc
            record.local_only_operations += 1
            return {
                **self._projection(record),
                "explanation": {
                    "text": (
                        "离线 DEMO 未调用 Teacher Provider。"
                        "你可以先使用本地提示，提交后再查看依据。"
                        if feedback.provider_calls == 0
                        else feedback.text
                    ),
                    "source": feedback.source.value,
                    "provider_calls": feedback.provider_calls,
                    "explicit_request": feedback.explicit_request,
                },
            }

    async def submit_answer(
        self,
        campaign_id: str,
        *,
        answer: str,
        submission_id: str | None = None,
        attempt_id: str | None = None,
    ) -> dict:
        record = self._record(campaign_id)
        async with record.lock:
            question = self._current_question(record)
            round_number = record.runtime.snapshot.current_round_number
            hint_key = (round_number, question.item_id, question.occurrence_number)
            hint_used = hint_key in record.hint_keys
            answer_revealed = (round_number, question.item_id) in record.revealed_keys
            now = self._clock_ms()
            latency = max(0, min(3_600_000, now - (record.question_started_at_ms or now)))
            submission = submission_id or f"submission-{uuid4().hex}"
            attempt = ReviewAttempt(
                submission_id=submission,
                attempt_id=attempt_id or f"attempt-{uuid4().hex}",
                item_id=question.item_id,
                answer=answer,
                hint_used=hint_used,
                hint_ids=(f"demo-hint-{question.item_id}-{question.occurrence_number}",)
                if hint_used
                else (),
                hint_dependency=HintDependency.RECENT if hint_used else HintDependency.NONE,
                answer_revealed_before_submission=answer_revealed,
                response_latency_ms=latency,
                submitted_at_ms=now,
            )
            try:
                receipt = await record.runtime.submit(attempt)
            except Exception as exc:
                raise self._runtime_error(exc) from exc
            attempt_receipt = receipt.attempt_receipt
            if not attempt_receipt.accepted or attempt_receipt.retrieval_outcome is None:
                raise ReviewProductError(
                    "REVIEW_ANSWER_REJECTED",
                    attempt_receipt.reason,
                    422,
                )
            label, tone = OUTCOME_UX[attempt_receipt.retrieval_outcome]
            fixture = QUESTION_BY_ID[question.item_id]
            record.revealed_keys.add((round_number, question.item_id))
            record.last_result = {
                "item_id": question.item_id,
                "concept_id": question.concept_id,
                "concept_label": CONCEPT_BY_ID[question.concept_id].label,
                "outcome": attempt_receipt.retrieval_outcome.value,
                "label": label,
                "tone": tone,
                "retrieval_score": attempt_receipt.retrieval_score,
                "feedback": receipt.feedback.text if receipt.feedback else label,
                "expected_answer": fixture.canonical_answer,
                "explanation": fixture.explanation,
                "provider_calls": attempt_receipt.provider_calls
                + (receipt.feedback.provider_calls if receipt.feedback else 0),
            }
            record.question_started_at_ms = None
            record.local_only_operations += 1
            return self._projection(record)

    async def pause(self, campaign_id: str) -> dict:
        record = self._record(campaign_id)
        async with record.lock:
            try:
                record.runtime.pause()
            except Exception as exc:
                raise self._runtime_error(exc) from exc
            record.local_only_operations += 1
            return self._projection(record)

    async def resume(self, campaign_id: str) -> dict:
        record = self._record(campaign_id)
        async with record.lock:
            try:
                await record.runtime.resume()
            except Exception as exc:
                raise self._runtime_error(exc) from exc
            record.question_started_at_ms = self._clock_ms()
            record.local_only_operations += 1
            return self._projection(record)

    async def finish(self, campaign_id: str) -> dict:
        record = self._record(campaign_id)
        async with record.lock:
            try:
                receipt = await record.runtime.complete_round()
            except Exception as exc:
                raise self._runtime_error(exc) from exc
            completion = receipt.result.completion
            record.last_result = None
            record.last_round_result = {
                "round_number": receipt.round_number,
                "passed": receipt.result.passed,
                "status_label": "通过" if receipt.result.passed else "未通过",
                "retrieval_score": completion.retrieval_score,
                "critical_concepts": self._critical_result(completion),
                "remaining_weak_items": sum(
                    not item.passed for item in receipt.result.item_results
                ),
                "failed_gates": list(completion.failed_gates),
            }
            record.local_only_operations += 1
            return self._projection(record)

    def export_recovery_fixture(self, campaign_id: str) -> ReviewRecoveryFixture:
        record = self._record(campaign_id)
        return ReviewRecoveryFixture(
            resume_dto=record.runtime.to_resume_dto(),
            observations=dict(record.adapters.review_memory.observations),
            accepted_proposal_ids=frozenset(
                record.adapters.review_memory.accepted_proposal_ids
            ),
            local_only_operations=record.local_only_operations,
            question_started_at_ms=record.question_started_at_ms,
            hint_keys=frozenset(record.hint_keys),
            revealed_keys=frozenset(record.revealed_keys),
            last_result=dict(record.last_result) if record.last_result else None,
            last_round_result=(
                dict(record.last_round_result) if record.last_round_result else None
            ),
        )

    def restore_recovery_fixture(self, fixture: ReviewRecoveryFixture) -> dict:
        adapters = ReviewProductAdapters.offline_demo()
        adapters.review_memory.observations.update(fixture.observations)
        adapters.review_memory.accepted_proposal_ids.update(
            fixture.accepted_proposal_ids
        )
        runtime = ReviewCampaignRuntime.from_resume_dto(
            fixture.resume_dto,
            question_bank=adapters.question_bank,
            memory_port=adapters.review_memory,
            progress_port=adapters.student_progress,
            budget_port=adapters.budget,
            evaluator=adapters.evaluator,
            clock_ms=self._clock_ms,
            teacher_explanation_port=adapters.teacher_explanation,
        )
        campaign_id = runtime.snapshot.campaign_id
        self._campaigns[campaign_id] = CampaignRecord(
            runtime=runtime,
            adapters=adapters,
            local_only_operations=fixture.local_only_operations,
            question_started_at_ms=fixture.question_started_at_ms,
            hint_keys=set(fixture.hint_keys),
            revealed_keys=set(fixture.revealed_keys),
            last_result=fixture.last_result,
            last_round_result=fixture.last_round_result,
        )
        return self.get_campaign(campaign_id)

    def _runtime_error(self, exc: Exception) -> ReviewProductError:
        return ReviewProductError(
            "REVIEW_TRANSITION_REJECTED",
            f"复习状态不允许这个操作：{exc}",
        )

    def _current_question(self, record: CampaignRecord) -> ReviewQuestionView:
        try:
            return record.runtime.present_question()
        except Exception as exc:
            raise self._runtime_error(exc) from exc

    def _question_projection(
        self,
        record: CampaignRecord,
        question: ReviewQuestionView,
    ) -> dict:
        snapshot = record.runtime.snapshot
        prior_results = [
            event
            for event in snapshot.exposure_ledger.events
            if event.item_id == question.item_id
            and event.event_type is ReviewExposureEventType.RESULT_RECORDED
        ]
        weak_repeat = question.occurrence_number > 1 or any(
            event.retrieval_outcome not in {
                ReviewRetrievalOutcome.RECALLED,
                ReviewRetrievalOutcome.RECALLED_WITH_DIFFICULTY,
            }
            for event in prior_results
        )
        hint_key = (
            snapshot.current_round_number,
            question.item_id,
            question.occurrence_number,
        )
        return {
            "item_id": question.item_id,
            "concept_id": question.concept_id,
            "concept_label": CONCEPT_BY_ID[question.concept_id].label,
            "prompt": question.prompt,
            "occurrence_number": question.occurrence_number,
            "critical": question.critical,
            "required": question.required,
            "weak_repeat": weak_repeat,
            "repeat_message": WEAK_REPEAT_MESSAGE if weak_repeat else None,
            "active_hint": (
                QUESTION_BY_ID[question.item_id].hint if hint_key in record.hint_keys else None
            ),
        }

    def _round_metrics(self, record: CampaignRecord) -> dict:
        snapshot = record.runtime.snapshot
        if not snapshot.rounds:
            return {
                "retrieval_score": 0.0,
                "evaluated_items": 0,
                "target_items": 10,
                "weak_count": 0,
                "remaining_weak_items": 0,
                "pass_status": "pending",
                "critical_concepts": [
                    {"concept_id": item.concept_id, "label": item.label, "status": "pending"}
                    for item in DEMO_CONCEPTS
                    if item.critical
                ],
            }
        current = snapshot.rounds[-1]
        receipts = [
            item
            for item in current.review_session.round.attempt_receipts
            if item.accepted and not item.duplicate and item.retrieval_outcome is not None
        ]
        latest = {}
        for receipt in receipts:
            latest[receipt.item_id] = receipt
        retrieval_score = sum(
            receipt.retrieval_score or 0.0 for receipt in latest.values()
        ) / snapshot.config.target_item_count
        pass_status = "pending"
        if current.round_receipt is not None:
            retrieval_score = current.round_receipt.result.completion.retrieval_score
            pass_status = "passed" if current.round_receipt.result.passed else "not_passed"
        weak_ids = {
            proposal.item_id
            for proposal in current.review_session.round.weak_item_proposals
        }
        remaining_weak = {
            item_id
            for item_id, receipt in latest.items()
            if receipt.retrieval_outcome not in {
                ReviewRetrievalOutcome.RECALLED,
                ReviewRetrievalOutcome.RECALLED_WITH_DIFFICULTY,
            }
        }
        critical = []
        for concept in DEMO_CONCEPTS:
            if not concept.critical:
                continue
            concept_receipts = [
                receipt
                for item_id, receipt in latest.items()
                if QUESTION_BY_ID[item_id].concept_id == concept.concept_id
            ]
            if not concept_receipts:
                status = "pending"
            elif all(receipt.retrieval_outcome in {
                ReviewRetrievalOutcome.RECALLED,
                ReviewRetrievalOutcome.RECALLED_WITH_DIFFICULTY,
            } for receipt in concept_receipts):
                status = "passed"
            else:
                status = "weak"
            critical.append(
                {"concept_id": concept.concept_id, "label": concept.label, "status": status}
            )
        return {
            "retrieval_score": round(retrieval_score, 4),
            "evaluated_items": len(latest),
            "target_items": snapshot.config.target_item_count,
            "weak_count": len(weak_ids),
            "remaining_weak_items": len(remaining_weak),
            "pass_status": pass_status,
            "critical_concepts": critical,
        }

    def _critical_result(self, completion) -> list[dict]:
        passed = set(completion.passed_critical_concept_ids)
        return [
            {
                "concept_id": concept_id,
                "label": CONCEPT_BY_ID[concept_id].label,
                "status": "passed" if concept_id in passed else "weak",
            }
            for concept_id in completion.critical_concept_ids
        ]

    def _provider_calls(self, record: CampaignRecord) -> int:
        snapshot = record.runtime.snapshot
        evaluator_calls = sum(
            receipt.provider_calls
            for review_round in snapshot.rounds
            for receipt in review_round.review_session.round.attempt_receipts
        )
        feedback_calls = sum(item.provider_calls for item in snapshot.feedback_receipts)
        return evaluator_calls + feedback_calls

    def _projection(self, record: CampaignRecord) -> dict:
        snapshot = record.runtime.snapshot
        question = None
        if snapshot.state is ReviewCampaignState.ROUND_ACTIVE:
            question = self._question_projection(record, record.runtime.present_question())
        completed_rounds = sum(item.round_receipt is not None for item in snapshot.rounds)
        campaign_result = None
        if snapshot.receipt is not None:
            remaining_weak = sum(
                observation.outcome not in {
                    ReviewRetrievalOutcome.RECALLED,
                    ReviewRetrievalOutcome.RECALLED_WITH_DIFFICULTY,
                }
                for observation in record.adapters.review_memory.observations.values()
            )
            campaign_result = {
                "completed_rounds": snapshot.receipt.completed_round_count,
                "passed_rounds": snapshot.receipt.passed_round_count,
                "all_rounds_passed": snapshot.receipt.all_rounds_passed,
                "retrieval_score": snapshot.receipt.analytics.retrieval_score,
                "critical_pass": snapshot.receipt.analytics.critical_pass,
                "remaining_weak_items": remaining_weak,
            }
        return {
            "schema_version": "review-product-v1.v1",
            "campaign_id": snapshot.campaign_id,
            "mode": "demo",
            "demo": True,
            "demo_label": "DEMO · 本地题库，账号学习记录" if getattr(self,'account_sync_enabled',False) else "DEMO · 完全离线",
            "state": snapshot.state.value,
            "round": {
                "current": snapshot.current_round_number,
                "fixed_total": snapshot.config.fixed_round_count,
                "completed": completed_rounds,
                "display": (
                    f"第 {snapshot.current_round_number} / {snapshot.config.fixed_round_count} 轮"
                    if snapshot.current_round_number
                    else f"共 {snapshot.config.fixed_round_count} 轮"
                ),
                "fsrs_controls_round_count": False,
            },
            "fixture": {"concept_count": 10, "question_count": 20, "round_count": 3},
            "question": question,
            "last_result": record.last_result,
            "round_result": record.last_round_result,
            "metrics": self._round_metrics(record),
            "cost": {
                "provider_calls": self._provider_calls(record),
                "local_only_operations": record.local_only_operations,
                "mode": "local_only",
            },
            "campaign_result": campaign_result,
            "resume": {
                "recoverable": snapshot.state not in {
                    ReviewCampaignState.COMPLETED,
                    ReviewCampaignState.ABORTED,
                    ReviewCampaignState.FAILED,
                },
                "storage_key": "mentorflow_review_campaign_v1",
            },
        }


__all__ = [
    "OUTCOME_UX",
    "WEAK_REPEAT_MESSAGE",
    "ReviewProductError",
    "ReviewProductService",
    "ReviewRecoveryFixture",
]
