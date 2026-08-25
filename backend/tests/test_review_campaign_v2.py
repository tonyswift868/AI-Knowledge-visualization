from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

import app.teaching.review_campaign_v2.runtime as campaign_runtime_module
from app.teaching.evaluator_v2 import (
    EvaluationEvidence,
    EvaluationPath,
    EvaluationRubric,
    EvaluatorV2,
    MisconceptionAnswer,
    RubricCriterion,
)
from app.teaching.review_campaign_v2 import (
    ReviewBudgetAction,
    ReviewCampaign,
    ReviewCampaignFeedbackPolicy,
    ReviewCampaignMemoryPort,
    ReviewCampaignReceipt,
    ReviewCampaignResumeDTO,
    ReviewCampaignRuntime,
    ReviewCampaignState,
    ReviewDuePriorityItem,
    ReviewDuePrioritySnapshot,
    ReviewExecutionBudgetDecision,
    ReviewExecutionBudgetPort,
    ReviewExposureEventType,
    ReviewFeedbackMode,
    ReviewFeedbackSource,
    ReviewObservationProposalReceipt,
    ReviewRoundState,
)
from app.teaching.review_v1 import (
    QuestionSourcePolicy,
    ReviewAttempt,
    ReviewCompletionPolicy,
    ReviewItem,
    ReviewRetrievalOutcome,
    StudentProgressItem,
    StudentProgressSnapshot,
    TeacherExplanation,
)


class FakeClock:
    def __init__(self, value: int = 100_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value

    def advance(self, milliseconds: int) -> None:
        self.value += milliseconds


class FakeQuestionBank:
    def __init__(self, items) -> None:
        self.items = tuple(items)
        self.calls = []

    async def select_trusted_items(self, request):
        self.calls.append(request)
        return self.items


class FakeProgressPort:
    def __init__(
        self,
        scope: tuple[str, ...],
        critical: set[str] | None = None,
    ) -> None:
        self.scope = scope
        self.critical = critical or set()
        self.calls = []

    async def load_student_progress(self, concept_scope):
        self.calls.append(concept_scope)
        return StudentProgressSnapshot(
            snapshot_id=f"progress-{len(self.calls)}",
            concept_scope=self.scope,
            items=tuple(
                StudentProgressItem(
                    concept_id=concept_id,
                    critical=concept_id in self.critical,
                    progress_score=0.5,
                )
                for concept_id in self.scope
            ),
            position_token=f"progress-position-{len(self.calls)}",
        )


class FakeCampaignMemoryPort:
    def __init__(
        self,
        items,
        *,
        fsrs_suggested_round_count: int | None = 99,
    ) -> None:
        self.items = tuple(items)
        self.fsrs_suggested_round_count = fsrs_suggested_round_count
        self.reads = []
        self.writes = []
        self._accepted: set[str] = set()

    async def read_due_priority(self, request):
        self.reads.append(request)
        return ReviewDuePrioritySnapshot(
            snapshot_id=f"due-{request.round_number}-{len(self.reads)}",
            position_token=f"memory-position-{len(self.reads)}",
            items=tuple(
                ReviewDuePriorityItem(
                    item_id=item.item_id,
                    concept_id=item.concept_id,
                    due=True,
                    priority=0.9,
                    critical=item.critical,
                )
                for item in self.items
            ),
            fsrs_suggested_round_count=self.fsrs_suggested_round_count,
        )

    async def write_review_observation_proposal(self, proposal):
        self.writes.append(proposal)
        duplicate = proposal.proposal_id in self._accepted
        self._accepted.add(proposal.proposal_id)
        return ReviewObservationProposalReceipt(
            proposal_id=proposal.proposal_id,
            accepted=True,
            duplicate=duplicate,
            position_token=f"written-{len(self._accepted)}",
            reason="accepted idempotent observation proposal",
        )


class FakeBudgetPort:
    def __init__(
        self,
        *,
        allowed_actions: set[ReviewBudgetAction] | None = None,
        order: list[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.allowed_actions = allowed_actions or set()
        self.calls = []
        self.order = order
        self.error = error

    async def authorize(self, request):
        self.calls.append(request)
        if self.order is not None:
            self.order.append("budget")
        if self.error is not None:
            raise self.error
        allowed = request.action in self.allowed_actions
        return ReviewExecutionBudgetDecision(
            request_id=request.request_id,
            action=request.action,
            allowed=allowed,
            provider_calls_reserved=1 if allowed else 0,
            reason="allowed" if allowed else "denied",
        )


class FakeSemanticProvider:
    provider_name = "campaign-semantic-fake"

    def __init__(
        self,
        raw_output: object,
        *,
        order: list[str] | None = None,
    ) -> None:
        self.raw_output = raw_output
        self.calls: list[dict[str, Any]] = []
        self.order = order

    async def structured(self, payload, response_model):
        self.calls.append({"payload": payload, "response_model": response_model})
        if self.order is not None:
            self.order.append("provider")
        return self.raw_output


class SlowSemanticProvider(FakeSemanticProvider):
    def __init__(self, raw_output: object) -> None:
        super().__init__(raw_output)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def structured(self, payload, response_model):
        self.calls.append({"payload": payload, "response_model": response_model})
        self.started.set()
        await self.release.wait()
        return self.raw_output


class FakeTeacherPort:
    def __init__(self, order: list[str] | None = None) -> None:
        self.calls = []
        self.order = order

    async def explain(self, request):
        self.calls.append(request)
        if self.order is not None:
            self.order.append("teacher")
        return TeacherExplanation(
            text="Recall the relationship, without revealing the final answer.",
            evidence_refs=(f"evidence-{request.item_id.split('-')[-1]}",),
            provider_calls=1,
        )


def _objective_item(
    number: int,
    *,
    critical: bool = False,
) -> ReviewItem:
    suffix = str(number)
    return ReviewItem(
        item_id=f"item-{suffix}",
        concept_id=f"concept-{suffix}",
        prompt=f"Recall objective {suffix}",
        rubric=EvaluationRubric(
            rubric_id=f"rubric-{suffix}",
            path=EvaluationPath.OBJECTIVE,
            accepted_answers=(f"A{suffix}",),
            partial_answers=(f"P{suffix}",),
            misconception_answers=(
                MisconceptionAnswer(
                    answer=f"W{suffix}",
                    misconception=f"misconception-{suffix}",
                ),
            ),
            required_evidence_ids=(f"evidence-{suffix}",),
        ),
        evidence=(
            EvaluationEvidence(
                evidence_id=f"evidence-{suffix}",
                content=f"Trusted evidence {suffix}",
            ),
        ),
        source_id=f"trusted-bank-{suffix}",
        critical=critical,
    )


def _semantic_item() -> ReviewItem:
    return ReviewItem(
        item_id="item-1",
        concept_id="concept-1",
        prompt="Explain the threshold relationship",
        rubric=EvaluationRubric(
            rubric_id="semantic-rubric",
            path=EvaluationPath.SEMANTIC,
            criteria=(
                RubricCriterion(
                    criterion_id="threshold",
                    description="Explains threshold and capacity",
                    weight=1.0,
                ),
            ),
            required_evidence_ids=("evidence-1",),
        ),
        evidence=(
            EvaluationEvidence(
                evidence_id="evidence-1",
                content="Threshold equals capacity multiplied by load factor.",
            ),
        ),
        source_id="trusted-semantic-bank",
    )


def _semantic_output() -> dict[str, object]:
    return {
        "outcome": "correct",
        "score": 0.95,
        "confidence": 0.95,
        "reason": "grounded threshold relationship",
        "cited_evidence_ids": ["evidence-1"],
        "criterion_results": [
            {
                "criterion_id": "threshold",
                "satisfied": True,
                "confidence": 0.95,
                "reason": "relationship present",
            }
        ],
    }


def _campaign(
    *,
    items=None,
    fixed_round_count: int = 1,
    max_same_round_retries: int = 0,
    completion_policy: ReviewCompletionPolicy | None = None,
    feedback_policy: ReviewCampaignFeedbackPolicy | None = None,
    evaluator: EvaluatorV2 | None = None,
    budget_port: FakeBudgetPort | None = None,
    memory_port: FakeCampaignMemoryPort | None = None,
    teacher_port=None,
    clock: FakeClock | None = None,
) -> tuple[
    ReviewCampaignRuntime,
    FakeQuestionBank,
    FakeCampaignMemoryPort,
    FakeProgressPort,
    FakeBudgetPort,
]:
    selected = tuple(items or (_objective_item(1), _objective_item(2)))
    scope = tuple(item.concept_id for item in selected)
    bank = FakeQuestionBank(selected)
    memory = memory_port or FakeCampaignMemoryPort(selected)
    progress = FakeProgressPort(
        scope,
        {item.concept_id for item in selected if item.critical},
    )
    budget = budget_port or FakeBudgetPort()
    runtime = ReviewCampaignRuntime.create(
        campaign_id="campaign-1",
        concept_scope=scope,
        fixed_round_count=fixed_round_count,
        target_item_count=len(selected),
        question_source_policy=QuestionSourcePolicy.WEAK_FIRST,
        completion_policy=completion_policy or ReviewCompletionPolicy(),
        max_same_round_retries=max_same_round_retries,
        feedback_policy=feedback_policy or ReviewCampaignFeedbackPolicy(),
        question_bank=bank,
        memory_port=memory,
        progress_port=progress,
        budget_port=budget,
        evaluator=evaluator or EvaluatorV2(),
        clock_ms=clock or FakeClock(),
        teacher_explanation_port=teacher_port,
    )
    return runtime, bank, memory, progress, budget


def _attempt(
    *,
    round_number: int,
    ordinal: int,
    item_id: str,
    answer: str,
    **changes,
) -> ReviewAttempt:
    values = {
        "submission_id": f"r{round_number}-submission-{ordinal}",
        "attempt_id": f"r{round_number}-attempt-{ordinal}",
        "item_id": item_id,
        "answer": answer,
        "response_latency_ms": 1_000,
        "submitted_at_ms": 100_000 + ordinal,
    }
    values.update(changes)
    return ReviewAttempt(**values)


async def _pass_current_round(
    runtime: ReviewCampaignRuntime,
    *,
    round_number: int,
) -> None:
    ordinal = 0
    while runtime.snapshot.state is ReviewCampaignState.ROUND_ACTIVE:
        ordinal += 1
        question = runtime.present_question()
        suffix = question.item_id.split("-")[-1]
        await runtime.submit(
            _attempt(
                round_number=round_number,
                ordinal=ordinal,
                item_id=question.item_id,
                answer=f"A{suffix}",
            )
        )


def test_public_campaign_contracts_and_fixed_round_states_exist() -> None:
    assert {
        ReviewCampaign,
        ReviewCampaignReceipt,
        ReviewCampaignMemoryPort,
        ReviewExecutionBudgetPort,
    }
    assert {
        "active",
        "evaluating",
        "evaluated",
        "passed",
        "not_passed",
        "completed",
    } <= {item.value for item in ReviewRoundState}


def test_fsrs_suggestion_cannot_change_fixed_campaign_round_count() -> None:
    async def scenario():
        runtime, _, memory, _, _ = _campaign(fixed_round_count=3)
        await runtime.start()
        return runtime, memory

    runtime, memory = asyncio.run(scenario())
    assert memory.reads[0].fixed_round_count == 3
    assert memory.fsrs_suggested_round_count == 99
    assert runtime.snapshot.config.fixed_round_count == 3
    assert runtime.snapshot.current_round_number == 1


def test_two_round_campaign_runs_full_lifecycle_and_emits_receipt() -> None:
    async def scenario():
        runtime, *_ = _campaign(fixed_round_count=2)
        await runtime.start()
        await _pass_current_round(runtime, round_number=1)
        await runtime.complete_round()
        assert runtime.snapshot.state is ReviewCampaignState.BETWEEN_ROUNDS
        await runtime.start_next_round()
        await _pass_current_round(runtime, round_number=2)
        await runtime.complete_round()
        return runtime

    runtime = asyncio.run(scenario())
    receipt = runtime.campaign_receipt()
    assert runtime.snapshot.state is ReviewCampaignState.COMPLETED
    assert receipt.fixed_round_count == 2
    assert receipt.completed_round_count == 2
    assert receipt.passed_round_count == 2
    assert len(runtime.snapshot.rounds) == 2
    for review_round in runtime.snapshot.rounds:
        assert ReviewRoundState.EVALUATED in review_round.state_history
        assert ReviewRoundState.PASSED in review_round.state_history
        assert review_round.state_history[-1] is ReviewRoundState.COMPLETED


def test_round_not_passed_is_audited_but_still_completes_fixed_round() -> None:
    async def scenario():
        runtime, *_ = _campaign(items=(_objective_item(1),))
        await runtime.start()
        await runtime.submit(
            _attempt(
                round_number=1,
                ordinal=1,
                item_id="item-1",
                answer="W1",
            )
        )
        await runtime.complete_round()
        return runtime

    runtime = asyncio.run(scenario())
    receipt = runtime.campaign_receipt()
    assert receipt.all_rounds_passed is False
    assert ReviewRoundState.NOT_PASSED in runtime.snapshot.rounds[0].state_history


def test_exposure_ledger_records_show_attempt_reveal_hint_result_and_retry() -> None:
    async def scenario():
        runtime, *_ = _campaign(
            items=(_objective_item(1),),
            max_same_round_retries=1,
        )
        await runtime.start()
        first = await runtime.submit(
            _attempt(
                round_number=1,
                ordinal=1,
                item_id="item-1",
                answer="A1",
                hint_used=True,
                hint_ids=("hint-1",),
                answer_revealed_before_submission=True,
            )
        )
        second_question = runtime.present_question()
        second = await runtime.submit(
            _attempt(
                round_number=1,
                ordinal=2,
                item_id="item-1",
                answer="A1",
            )
        )
        return runtime, first, second_question, second

    runtime, first, question, second = asyncio.run(scenario())
    types = tuple(event.event_type for event in runtime.snapshot.exposure_ledger.events)
    assert types.count(ReviewExposureEventType.QUESTION_SHOWN) == 2
    assert types.count(ReviewExposureEventType.ATTEMPT_SUBMITTED) == 2
    assert ReviewExposureEventType.ANSWER_REVEALED in types
    assert ReviewExposureEventType.HINT_USED in types
    assert types.count(ReviewExposureEventType.RESULT_RECORDED) == 2
    assert question.occurrence_number == 2
    assert first.attempt_receipt.weak_item_proposal is not None
    assert (
        second.attempt_receipt.retrieval_outcome
        is ReviewRetrievalOutcome.HINT_DEPENDENT
    )
    assert runtime.snapshot.state is ReviewCampaignState.ROUND_EVALUATED


def test_clean_recall_never_reappears_in_same_round_without_weak_proposal() -> None:
    async def scenario():
        runtime, *_ = _campaign(
            items=(_objective_item(1),),
            max_same_round_retries=3,
        )
        await runtime.start()
        await runtime.submit(
            _attempt(
                round_number=1,
                ordinal=1,
                item_id="item-1",
                answer="A1",
            )
        )
        return runtime

    runtime = asyncio.run(scenario())
    shown = [
        event
        for event in runtime.snapshot.exposure_ledger.events
        if event.event_type is ReviewExposureEventType.QUESTION_SHOWN
    ]
    assert len(shown) == 1
    assert runtime.snapshot.state is ReviewCampaignState.ROUND_EVALUATED


def test_round_writes_only_idempotent_memory_observation_proposal() -> None:
    async def scenario():
        runtime, _, memory, _, _ = _campaign(items=(_objective_item(1),))
        await runtime.start()
        await _pass_current_round(runtime, round_number=1)
        await runtime.complete_round()
        return runtime, memory

    runtime, memory = asyncio.run(scenario())
    assert len(memory.writes) == 1
    proposal = memory.writes[0]
    assert proposal.requires_memory_adapter is True
    assert proposal.executed is False
    assert proposal.observations[0].outcome is ReviewRetrievalOutcome.RECALLED
    assert runtime.snapshot.rounds[0].observation_receipt.accepted is True


def test_malformed_memory_receipt_becomes_rejected_proposal_without_stuck_round(
) -> None:
    class MalformedMemoryPort(FakeCampaignMemoryPort):
        async def write_review_observation_proposal(self, proposal):
            self.writes.append(proposal)
            return ReviewObservationProposalReceipt(
                proposal_id="wrong-proposal-id",
                accepted=True,
                position_token="wrong-position",
                reason="malformed binding",
            )

    items = (_objective_item(1),)
    memory = MalformedMemoryPort(items)

    async def scenario():
        runtime, *_ = _campaign(items=items, memory_port=memory)
        await runtime.start()
        await _pass_current_round(runtime, round_number=1)
        await runtime.complete_round()
        return runtime

    runtime = asyncio.run(scenario())
    assert runtime.snapshot.state is ReviewCampaignState.COMPLETED
    observation_receipt = runtime.snapshot.rounds[0].observation_receipt
    assert observation_receipt.proposal_id == memory.writes[0].proposal_id
    assert observation_receipt.accepted is False
    assert observation_receipt.reason == (
        "memory_proposal_port_error:ReviewCampaignError"
    )


def test_objective_review_never_queries_budget_or_provider() -> None:
    async def scenario():
        runtime, _, _, _, budget = _campaign(items=(_objective_item(1),))
        await runtime.start()
        receipt = await runtime.submit(
            _attempt(
                round_number=1,
                ordinal=1,
                item_id="item-1",
                answer="A1",
            )
        )
        return runtime, budget, receipt

    runtime, budget, receipt = asyncio.run(scenario())
    assert budget.calls == []
    assert receipt.attempt_receipt.provider_calls == 0
    assert runtime.snapshot.budget_decisions == ()


def test_semantic_action_is_budgeted_before_single_provider_call() -> None:
    order: list[str] = []
    budget = FakeBudgetPort(
        allowed_actions={ReviewBudgetAction.SEMANTIC_EVALUATION},
        order=order,
    )
    provider = FakeSemanticProvider(_semantic_output(), order=order)

    async def scenario():
        runtime, *_ = _campaign(
            items=(_semantic_item(),),
            budget_port=budget,
            evaluator=EvaluatorV2(provider),
        )
        await runtime.start()
        receipt = await runtime.submit(
            _attempt(
                round_number=1,
                ordinal=1,
                item_id="item-1",
                answer="threshold depends on capacity and load factor",
            )
        )
        return runtime, receipt

    runtime, receipt = asyncio.run(scenario())
    assert order == ["budget", "provider"]
    assert len(provider.calls) == 1
    assert receipt.attempt_receipt.provider_calls == 1
    assert runtime.snapshot.budget_decisions[0].allowed is True


def test_denied_semantic_budget_prevents_provider_and_becomes_weak_failure() -> None:
    budget = FakeBudgetPort()
    provider = FakeSemanticProvider(_semantic_output())

    async def scenario():
        runtime, *_ = _campaign(
            items=(_semantic_item(),),
            budget_port=budget,
            evaluator=EvaluatorV2(provider),
        )
        await runtime.start()
        receipt = await runtime.submit(
            _attempt(
                round_number=1,
                ordinal=1,
                item_id="item-1",
                answer="threshold",
            )
        )
        return runtime, receipt

    runtime, receipt = asyncio.run(scenario())
    assert len(budget.calls) == 1
    assert provider.calls == []
    assert receipt.attempt_receipt.provider_calls == 0
    assert receipt.attempt_receipt.retrieval_outcome is ReviewRetrievalOutcome.FAILED
    assert receipt.attempt_receipt.weak_item_proposal is not None
    assert runtime.snapshot.budget_decisions[0].allowed is False


def test_budget_port_error_fails_closed_without_semantic_provider() -> None:
    budget = FakeBudgetPort(error=TimeoutError())
    provider = FakeSemanticProvider(_semantic_output())

    async def scenario():
        runtime, *_ = _campaign(
            items=(_semantic_item(),),
            budget_port=budget,
            evaluator=EvaluatorV2(provider),
        )
        await runtime.start()
        receipt = await runtime.submit(
            _attempt(
                round_number=1,
                ordinal=1,
                item_id="item-1",
                answer="threshold",
            )
        )
        return runtime, receipt

    runtime, receipt = asyncio.run(scenario())
    assert provider.calls == []
    assert receipt.attempt_receipt.provider_calls == 0
    assert receipt.attempt_receipt.retrieval_outcome is ReviewRetrievalOutcome.FAILED
    assert runtime.snapshot.budget_decisions[0].reason == (
        "budget_port_fail_closed:TimeoutError"
    )


def test_explicit_why_is_budgeted_and_not_called_on_normal_turn() -> None:
    order: list[str] = []
    teacher = FakeTeacherPort(order)
    budget = FakeBudgetPort(
        allowed_actions={ReviewBudgetAction.TEACHER_EXPLANATION},
        order=order,
    )

    async def scenario():
        runtime, *_ = _campaign(
            items=(_objective_item(1),),
            budget_port=budget,
            teacher_port=teacher,
        )
        await runtime.start()
        assert teacher.calls == [] and budget.calls == []
        feedback = await runtime.ask_why("为什么？")
        return runtime, feedback

    runtime, feedback = asyncio.run(scenario())
    assert order == ["budget", "teacher"]
    assert feedback.source is ReviewFeedbackSource.TEACHER
    assert feedback.provider_calls == 1
    assert runtime.snapshot.state is ReviewCampaignState.ROUND_ACTIVE


def test_denied_teacher_budget_never_calls_teacher() -> None:
    teacher = FakeTeacherPort()
    budget = FakeBudgetPort()

    async def scenario():
        runtime, *_ = _campaign(
            items=(_objective_item(1),),
            budget_port=budget,
            teacher_port=teacher,
        )
        await runtime.start()
        return await runtime.ask_why("为什么？")

    feedback = asyncio.run(scenario())
    assert len(budget.calls) == 1
    assert teacher.calls == []
    assert feedback.source is ReviewFeedbackSource.BUDGET_DENIED
    assert feedback.provider_calls == 0


def test_teacher_budget_port_error_uses_local_denial_without_teacher_call() -> None:
    teacher = FakeTeacherPort()
    budget = FakeBudgetPort(error=RuntimeError())

    async def scenario():
        runtime, *_ = _campaign(
            items=(_objective_item(1),),
            budget_port=budget,
            teacher_port=teacher,
        )
        await runtime.start()
        return runtime, await runtime.ask_why("为什么？")

    runtime, feedback = asyncio.run(scenario())
    assert teacher.calls == []
    assert feedback.source is ReviewFeedbackSource.BUDGET_DENIED
    assert feedback.provider_calls == 0
    assert runtime.snapshot.state is ReviewCampaignState.ROUND_ACTIVE


def test_failed_attempt_gets_local_minimal_feedback_without_provider() -> None:
    teacher = FakeTeacherPort()

    async def scenario():
        runtime, _, _, _, budget = _campaign(
            items=(_objective_item(1),),
            teacher_port=teacher,
        )
        await runtime.start()
        receipt = await runtime.submit(
            _attempt(
                round_number=1,
                ordinal=1,
                item_id="item-1",
                answer="W1",
            )
        )
        return budget, receipt

    budget, receipt = asyncio.run(scenario())
    assert receipt.feedback is not None
    assert receipt.feedback.source is ReviewFeedbackSource.LOCAL
    assert receipt.feedback.provider_calls == 0
    assert budget.calls == []
    assert teacher.calls == []


def test_fail_feedback_policy_calls_teacher_only_after_budget_approval() -> None:
    order: list[str] = []
    teacher = FakeTeacherPort(order)
    budget = FakeBudgetPort(
        allowed_actions={ReviewBudgetAction.FAIL_FEEDBACK},
        order=order,
    )
    policy = ReviewCampaignFeedbackPolicy(
        mode=ReviewFeedbackMode.TEACHER_ON_PARTIAL_OR_FAILED,
    )

    async def scenario():
        runtime, *_ = _campaign(
            items=(_objective_item(1),),
            budget_port=budget,
            teacher_port=teacher,
            feedback_policy=policy,
        )
        await runtime.start()
        return await runtime.submit(
            _attempt(
                round_number=1,
                ordinal=1,
                item_id="item-1",
                answer="P1",
            )
        )

    receipt = asyncio.run(scenario())
    assert order == ["budget", "teacher"]
    assert receipt.feedback is not None
    assert receipt.feedback.source is ReviewFeedbackSource.TEACHER
    assert receipt.feedback.reveals_answer is False


def test_answer_reveal_prevents_clean_pass_and_is_written_to_memory() -> None:
    async def scenario():
        runtime, _, memory, _, _ = _campaign(
            items=(_objective_item(1, critical=True),),
        )
        await runtime.start()
        await runtime.submit(
            _attempt(
                round_number=1,
                ordinal=1,
                item_id="item-1",
                answer="A1",
                answer_revealed_before_submission=True,
            )
        )
        await runtime.complete_round()
        return runtime, memory

    runtime, memory = asyncio.run(scenario())
    receipt = runtime.campaign_receipt()
    assert receipt.all_rounds_passed is False
    assert receipt.analytics.critical_pass is False
    assert memory.writes[0].observations[0].answer_revealed is True
    assert (
        memory.writes[0].observations[0].outcome
        is ReviewRetrievalOutcome.HINT_DEPENDENT
    )


def test_pause_resume_dto_preserves_round_and_fixed_count() -> None:
    async def scenario():
        runtime, bank, memory, progress, budget = _campaign(fixed_round_count=3)
        question = await runtime.start()
        runtime.pause()
        dto = ReviewCampaignResumeDTO.model_validate_json(
            runtime.to_resume_dto().model_dump_json()
        )
        restored = ReviewCampaignRuntime.from_resume_dto(
            dto,
            question_bank=bank,
            memory_port=memory,
            progress_port=progress,
            budget_port=budget,
            evaluator=EvaluatorV2(),
            clock_ms=FakeClock(),
        )
        resumed = await restored.resume()
        return restored, question, resumed

    restored, question, resumed = asyncio.run(scenario())
    assert resumed == question
    assert restored.snapshot.state is ReviewCampaignState.ROUND_ACTIVE
    assert restored.snapshot.current_round_number == 1
    assert restored.snapshot.config.fixed_round_count == 3


def test_duplicate_submission_is_idempotent_for_ledger_budget_and_provider() -> None:
    budget = FakeBudgetPort(
        allowed_actions={ReviewBudgetAction.SEMANTIC_EVALUATION}
    )
    provider = FakeSemanticProvider(_semantic_output())

    async def scenario():
        runtime, *_ = _campaign(
            items=(_semantic_item(),),
            budget_port=budget,
            evaluator=EvaluatorV2(provider),
        )
        await runtime.start()
        attempt = _attempt(
            round_number=1,
            ordinal=1,
            item_id="item-1",
            answer="threshold capacity load factor",
        )
        first = await runtime.submit(attempt)
        before = runtime.snapshot
        duplicate = await runtime.submit(attempt)
        return runtime, before, first, duplicate

    runtime, before, first, duplicate = asyncio.run(scenario())
    assert first.attempt_receipt.provider_calls == 1
    assert duplicate.attempt_receipt.duplicate is True
    assert duplicate.attempt_receipt.provider_calls == 0
    assert len(provider.calls) == len(budget.calls) == 1
    assert runtime.snapshot == before


def test_concurrent_duplicate_semantic_submission_uses_one_budget_and_provider(
) -> None:
    async def scenario():
        provider = SlowSemanticProvider(_semantic_output())
        budget = FakeBudgetPort(
            allowed_actions={ReviewBudgetAction.SEMANTIC_EVALUATION}
        )
        runtime, *_ = _campaign(
            items=(_semantic_item(),),
            budget_port=budget,
            evaluator=EvaluatorV2(provider),
        )
        await runtime.start()
        attempt = _attempt(
            round_number=1,
            ordinal=1,
            item_id="item-1",
            answer="threshold capacity load factor",
        )
        first_task = asyncio.create_task(runtime.submit(attempt))
        await provider.started.wait()
        duplicate_task = asyncio.create_task(runtime.submit(attempt))
        await asyncio.sleep(0)
        provider.release.set()
        first, duplicate = await asyncio.gather(first_task, duplicate_task)
        return runtime, provider, budget, first, duplicate

    runtime, provider, budget, first, duplicate = asyncio.run(scenario())
    assert first.attempt_receipt.duplicate is False
    assert duplicate.attempt_receipt.duplicate is True
    assert len(provider.calls) == len(budget.calls) == 1
    attempt_events = [
        event
        for event in runtime.snapshot.exposure_ledger.events
        if event.event_type is ReviewExposureEventType.ATTEMPT_SUBMITTED
    ]
    assert len(attempt_events) == 1


def test_pending_evaluation_resume_requires_restart_without_provider_replay() -> None:
    async def scenario():
        old_provider = SlowSemanticProvider(_semantic_output())
        old_budget = FakeBudgetPort(
            allowed_actions={ReviewBudgetAction.SEMANTIC_EVALUATION}
        )
        runtime, bank, memory, progress, _ = _campaign(
            items=(_semantic_item(),),
            fixed_round_count=3,
            budget_port=old_budget,
            evaluator=EvaluatorV2(old_provider),
        )
        await runtime.start()
        task = asyncio.create_task(
            runtime.submit(
                _attempt(
                    round_number=1,
                    ordinal=1,
                    item_id="item-1",
                    answer="threshold capacity load factor",
                )
            )
        )
        await old_provider.started.wait()
        dto = runtime.to_resume_dto()
        old_provider.release.set()
        await task

        new_provider = FakeSemanticProvider(_semantic_output())
        new_budget = FakeBudgetPort(
            allowed_actions={ReviewBudgetAction.SEMANTIC_EVALUATION}
        )
        restored = ReviewCampaignRuntime.from_resume_dto(
            dto,
            question_bank=bank,
            memory_port=memory,
            progress_port=progress,
            budget_port=new_budget,
            evaluator=EvaluatorV2(new_provider),
            clock_ms=FakeClock(),
        )
        assert restored.snapshot.state is ReviewCampaignState.RECOVERY_REQUIRED
        question = await restored.resume()
        return (
            restored,
            question,
            old_provider,
            old_budget,
            new_provider,
            new_budget,
        )

    restored, question, old_provider, old_budget, new_provider, new_budget = (
        asyncio.run(scenario())
    )
    assert len(old_provider.calls) == len(old_budget.calls) == 1
    assert new_provider.calls == [] and new_budget.calls == []
    assert restored.snapshot.state is ReviewCampaignState.ROUND_ACTIVE
    assert restored.snapshot.current_round_number == 1
    assert restored.snapshot.config.fixed_round_count == 3
    assert restored.snapshot.rounds[0].recovery_count == 1
    assert question.item_id == "item-1"


def test_analytics_include_retrieval_weak_hint_provider_reuse_critical_duration(
) -> None:
    clock = FakeClock()

    async def scenario():
        runtime, *_ = _campaign(
            items=(
                _objective_item(1, critical=True),
                _objective_item(2),
            ),
            max_same_round_retries=1,
            clock=clock,
        )
        await runtime.start()
        await runtime.submit(
            _attempt(
                round_number=1,
                ordinal=1,
                item_id="item-1",
                answer="W1",
                hint_used=True,
                hint_ids=("hint-1",),
            )
        )
        await runtime.submit(
            _attempt(
                round_number=1,
                ordinal=2,
                item_id="item-2",
                answer="A2",
            )
        )
        await runtime.submit(
            _attempt(
                round_number=1,
                ordinal=3,
                item_id="item-1",
                answer="A1",
            )
        )
        clock.advance(12_345)
        await runtime.complete_round()
        return runtime

    runtime = asyncio.run(scenario())
    analytics = runtime.campaign_receipt().analytics
    assert analytics.retrieval_score == pytest.approx(0.9)
    assert analytics.weak_count == 1
    assert analytics.hint_rate == pytest.approx(1 / 3)
    assert analytics.provider_call_count == 0
    assert analytics.question_show_count == 3
    assert analytics.question_reuse_count == 1
    assert analytics.same_round_reuse_count == 1
    assert analytics.critical_pass is True
    assert analytics.round_durations_ms == (12_345,)


def test_campaign_receipt_and_resume_dto_are_strict_json_round_trippable() -> None:
    async def scenario():
        runtime, *_ = _campaign(items=(_objective_item(1),))
        await runtime.start()
        await _pass_current_round(runtime, round_number=1)
        await runtime.complete_round()
        return runtime

    runtime = asyncio.run(scenario())
    receipt = ReviewCampaignReceipt.model_validate_json(
        runtime.campaign_receipt().model_dump_json()
    )
    dto = ReviewCampaignResumeDTO.model_validate_json(
        runtime.to_resume_dto().model_dump_json()
    )
    assert receipt == runtime.campaign_receipt()
    assert dto.campaign == runtime.snapshot


def test_large_1000_item_three_round_objective_campaign_is_provider_free() -> None:
    items = tuple(
        _objective_item(number, critical=(number % 100 == 0))
        for number in range(1, 1_001)
    )
    runtime, bank, memory, progress, budget = _campaign(
        items=items,
        fixed_round_count=3,
        max_same_round_retries=1,
    )

    async def scenario(active_runtime: ReviewCampaignRuntime):
        runtime = active_runtime
        duplicate_checks = 0
        for round_number in range(1, 4):
            if round_number == 1:
                await runtime.start()
            else:
                await runtime.start_next_round()
            ordinal = 0
            first_attempt = None
            while runtime.snapshot.state is ReviewCampaignState.ROUND_ACTIVE:
                ordinal += 1
                question = runtime.present_question()
                number = int(question.item_id.split("-")[-1])
                weak_initial = question.occurrence_number == 1 and number <= 15
                if weak_initial and number <= 10:
                    answer = f"W{number}"
                    changes = {}
                elif weak_initial:
                    answer = f"A{number}"
                    changes = {
                        "hint_used": True,
                        "hint_ids": (f"hint-{number}",),
                    }
                else:
                    answer = f"A{number}"
                    changes = {}
                attempt = _attempt(
                    round_number=round_number,
                    ordinal=ordinal,
                    item_id=question.item_id,
                    answer=answer,
                    **changes,
                )
                receipt = await runtime.submit(attempt)
                if first_attempt is None:
                    first_attempt = attempt
                    duplicate = await runtime.submit(attempt)
                    assert duplicate.attempt_receipt.duplicate is True
                    duplicate_checks += 1
                if ordinal == 500:
                    runtime.pause()
                    dto = runtime.to_resume_dto()
                    runtime = ReviewCampaignRuntime.from_resume_dto(
                        dto,
                        question_bank=bank,
                        memory_port=memory,
                        progress_port=progress,
                        budget_port=budget,
                        evaluator=EvaluatorV2(),
                        clock_ms=FakeClock(),
                    )
                    await runtime.resume()
                assert receipt.attempt_receipt.provider_calls == 0
            assert runtime.snapshot.state is ReviewCampaignState.ROUND_EVALUATED
            await runtime.complete_round()
        return runtime, duplicate_checks

    runtime, duplicate_checks = asyncio.run(scenario(runtime))
    receipt = runtime.campaign_receipt()
    assert receipt.completed_round_count == 3
    assert len(runtime.snapshot.rounds) == 3
    assert all(
        len(item.review_session.round.items) == 1_000
        for item in runtime.snapshot.rounds
    )
    assert all(
        len(item.review_session.round.attempts) == 1_015
        for item in runtime.snapshot.rounds
    )
    assert receipt.analytics.provider_call_count == 0
    assert receipt.analytics.weak_count == 45
    assert receipt.analytics.same_round_reuse_count == 45
    assert receipt.analytics.critical_pass is True
    assert duplicate_checks == 3
    assert budget.calls == []
    assert len(memory.reads) == len(memory.writes) == 3


def test_campaign_runtime_has_no_w6_peer_or_db_implementation_dependency() -> None:
    source = inspect.getsource(campaign_runtime_module)
    assert all(
        forbidden not in source
        for forbidden in (
            "StudentModel",
            "SessionLocal",
            "active_teaching",
            "generic_knowledge_api",
            "review_memory_v2",
            "from ..review_scheduling",
            "from ..cost_engine",
        )
    )
    submit_source = inspect.getsource(ReviewCampaignRuntime.submit)
    assert ".explain(" not in submit_source
    assert source.count("self._evaluator.evaluate(") == 2
