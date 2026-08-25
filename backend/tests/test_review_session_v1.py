from __future__ import annotations

import ast
import asyncio
import inspect
from typing import Any

import pytest
from pydantic import ValidationError

import app.teaching.review_v1.runtime as runtime_module
from app.teaching.contracts import HintDependency
from app.teaching.evaluator_v2 import (
    EvaluationEvidence,
    EvaluationOutcome,
    EvaluationPath,
    EvaluationRubric,
    EvaluatorV2,
    MisconceptionAnswer,
    RubricCriterion,
)
from app.teaching.review_v1 import (
    QuestionSourcePolicy,
    ReviewAttempt,
    ReviewCompletionPolicy,
    ReviewCompletionStatus,
    ReviewItem,
    ReviewMemoryPort,
    ReviewMemorySignal,
    ReviewMemorySnapshot,
    ReviewRetrievalOutcome,
    ReviewRoundReceipt,
    ReviewSession,
    ReviewSessionCrashDTO,
    ReviewSessionError,
    ReviewSessionRuntime,
    ReviewSessionState,
    StudentProgressItem,
    StudentProgressPort,
    StudentProgressSnapshot,
    TeacherExplanation,
    TrustedQuestionBankPort,
)


class FakeClock:
    def __init__(self, value: int = 10_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


class FakeMemoryPort:
    def __init__(self, scope: tuple[str, ...] = ("concept-1", "concept-2")) -> None:
        self.scope = scope
        self.calls: list[tuple[str, ...]] = []

    async def load_review_memory(self, concept_scope):
        self.calls.append(concept_scope)
        return ReviewMemorySnapshot(
            snapshot_id="memory-1",
            concept_scope=self.scope,
            weak_signals=(
                ReviewMemorySignal(
                    item_id="item-1",
                    concept_id="concept-1",
                    weakness_score=0.8,
                    last_outcome=ReviewRetrievalOutcome.FAILED,
                ),
            ),
            position_token="memory-position-1",
        )


class FakeProgressPort:
    def __init__(self, scope: tuple[str, ...] = ("concept-1", "concept-2")) -> None:
        self.scope = scope
        self.calls: list[tuple[str, ...]] = []

    async def load_student_progress(self, concept_scope):
        self.calls.append(concept_scope)
        return StudentProgressSnapshot(
            snapshot_id="progress-1",
            concept_scope=self.scope,
            items=(
                StudentProgressItem(
                    concept_id="concept-1",
                    critical=True,
                    progress_score=0.5,
                ),
                StudentProgressItem(
                    concept_id="concept-2",
                    critical=False,
                    progress_score=0.8,
                ),
            ),
            position_token="progress-position-1",
        )


class FakeQuestionBank:
    def __init__(self, items) -> None:
        self.items = tuple(items)
        self.calls = []

    async def select_trusted_items(self, request):
        self.calls.append(request)
        return self.items


class FakeSemanticProvider:
    provider_name = "review-semantic-fake"

    def __init__(self, raw_output: object, error: Exception | None = None) -> None:
        self.raw_output = raw_output
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def structured(self, payload, response_model):
        self.calls.append({"payload": payload, "response_model": response_model})
        if self.error is not None:
            raise self.error
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


class FakeTeacherExplanationPort:
    def __init__(self) -> None:
        self.calls = []

    async def explain(self, request):
        self.calls.append(request)
        return TeacherExplanation(
            text="Because retrieval strengthens access to the concept.",
            evidence_refs=("evidence-1",),
            provider_calls=1,
        )


def _objective_item(
    number: int,
    *,
    required: bool = True,
    critical: bool = False,
) -> ReviewItem:
    evidence_id = f"evidence-{number}"
    return ReviewItem(
        item_id=f"item-{number}",
        concept_id=f"concept-{number}",
        prompt=f"Recall question {number}",
        rubric=EvaluationRubric(
            rubric_id=f"rubric-{number}",
            path=EvaluationPath.OBJECTIVE,
            accepted_answers=(f"A{number}",),
            partial_answers=(f"P{number}",),
            misconception_answers=(
                MisconceptionAnswer(
                    answer=f"W{number}",
                    misconception=f"misconception-{number}",
                ),
            ),
            required_evidence_ids=(evidence_id,),
        ),
        evidence=(
            EvaluationEvidence(
                evidence_id=evidence_id,
                content=f"Trusted grounding for item {number}",
            ),
        ),
        source_id=f"question-bank-source-{number}",
        required=required,
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
        source_id="semantic-question-source",
    )


def _semantic_output(**changes) -> dict[str, object]:
    result: dict[str, object] = {
        "outcome": "correct",
        "score": 0.95,
        "confidence": 0.95,
        "reason": "grounded explanation satisfies the criterion",
        "cited_evidence_ids": ["evidence-1"],
        "criterion_results": [
            {
                "criterion_id": "threshold",
                "satisfied": True,
                "confidence": 0.95,
                "reason": "threshold relationship is present",
            }
        ],
    }
    result.update(changes)
    return result


async def _create_runtime(
    *,
    items=None,
    target_item_count: int = 2,
    max_same_round_retries: int = 0,
    completion_policy: ReviewCompletionPolicy | None = None,
    evaluator: EvaluatorV2 | None = None,
    teacher_port=None,
    memory_port=None,
    progress_port=None,
    question_bank=None,
    source_policy: QuestionSourcePolicy = QuestionSourcePolicy.WEAK_FIRST,
    clock: FakeClock | None = None,
) -> tuple[ReviewSessionRuntime, FakeQuestionBank, FakeMemoryPort, FakeProgressPort]:
    selected_items = tuple(items or (_objective_item(1), _objective_item(2)))
    bank = question_bank or FakeQuestionBank(selected_items)
    memory = memory_port or FakeMemoryPort()
    progress = progress_port or FakeProgressPort()
    runtime = await ReviewSessionRuntime.create(
        session_id="review-session-1",
        concept_scope=("concept-1", "concept-2"),
        round_number=3,
        question_source_policy=source_policy,
        target_item_count=target_item_count,
        completion_policy=completion_policy or ReviewCompletionPolicy(),
        max_same_round_retries=max_same_round_retries,
        question_bank=bank,
        memory_port=memory,
        progress_port=progress,
        evaluator=evaluator or EvaluatorV2(),
        clock_ms=clock or FakeClock(),
        teacher_explanation_port=teacher_port,
    )
    return runtime, bank, memory, progress


def _attempt(
    item_id: str,
    answer: str,
    *,
    ordinal: int = 1,
    **changes,
) -> ReviewAttempt:
    values = {
        "submission_id": f"submission-{ordinal}",
        "attempt_id": f"attempt-{ordinal}",
        "item_id": item_id,
        "answer": answer,
        "response_latency_ms": 1_000,
        "submitted_at_ms": 10_000 + ordinal,
    }
    values.update(changes)
    return ReviewAttempt(**values)


def test_public_contracts_and_required_outcomes_are_explicit() -> None:
    assert {item.value for item in ReviewRetrievalOutcome} == {
        "recalled",
        "recalled_with_difficulty",
        "partial",
        "failed",
        "hint_dependent",
    }
    assert {
        ReviewSession,
        ReviewItem,
        ReviewAttempt,
        ReviewRoundReceipt,
        TrustedQuestionBankPort,
        ReviewMemoryPort,
        StudentProgressPort,
    }


def test_create_binds_session_inputs_and_selects_only_trusted_questions() -> None:
    async def scenario():
        runtime, bank, memory, progress = await _create_runtime()
        question = runtime.start()
        return runtime, bank, memory, progress, question

    runtime, bank, memory, progress, question = asyncio.run(scenario())
    assert runtime.snapshot.input.concept_scope == ("concept-1", "concept-2")
    assert runtime.snapshot.input.round_number == 3
    assert runtime.snapshot.input.target_item_count == 2
    assert (
        runtime.snapshot.input.question_source_policy
        is QuestionSourcePolicy.WEAK_FIRST
    )
    assert memory.calls == [("concept-1", "concept-2")]
    assert progress.calls == [("concept-1", "concept-2")]
    assert bank.calls[0].memory.position_token == "memory-position-1"
    assert bank.calls[0].progress.position_token == "progress-position-1"
    assert question.item_id == "item-1"
    assert set(question.model_dump()) == {
        "session_id",
        "round_id",
        "item_id",
        "concept_id",
        "prompt",
        "occurrence_number",
        "required",
        "critical",
    }


def test_clean_objective_active_recall_round_passes_with_zero_provider_calls() -> None:
    async def scenario():
        runtime, *_ = await _create_runtime()
        first_question = runtime.start()
        first = await runtime.submit(_attempt(first_question.item_id, "A1"))
        second_question = runtime.present_question()
        second = await runtime.submit(
            _attempt(second_question.item_id, "A2", ordinal=2)
        )
        round_receipt = runtime.complete()
        return runtime, first, second, round_receipt

    runtime, first, second, round_receipt = asyncio.run(scenario())
    assert first.retrieval_outcome is ReviewRetrievalOutcome.RECALLED
    assert second.retrieval_outcome is ReviewRetrievalOutcome.RECALLED
    assert first.provider_calls == second.provider_calls == 0
    assert round_receipt.result.status is ReviewCompletionStatus.PASSED
    assert round_receipt.result.passed is True
    assert round_receipt.result.evaluator_provider_calls == 0
    assert runtime.snapshot.state is ReviewSessionState.COMPLETED
    assert runtime.snapshot.round_receipt == round_receipt


@pytest.mark.parametrize(
    ("answer", "changes", "expected"),
    [
        (
            "A1",
            {"response_latency_ms": 30_001},
            ReviewRetrievalOutcome.RECALLED_WITH_DIFFICULTY,
        ),
        ("P1", {}, ReviewRetrievalOutcome.PARTIAL),
        ("W1", {}, ReviewRetrievalOutcome.FAILED),
        (
            "A1",
            {"hint_used": True, "hint_ids": ("hint-1",)},
            ReviewRetrievalOutcome.HINT_DEPENDENT,
        ),
        (
            "A1",
            {"hint_dependency": HintDependency.PERSISTENT},
            ReviewRetrievalOutcome.HINT_DEPENDENT,
        ),
    ],
)
def test_retrieval_classification_covers_difficulty_partial_failure_and_hint(
    answer,
    changes,
    expected,
) -> None:
    async def scenario():
        runtime, *_ = await _create_runtime(
            items=(_objective_item(1),),
            target_item_count=1,
        )
        question = runtime.start()
        return await runtime.submit(_attempt(question.item_id, answer, **changes))

    receipt = asyncio.run(scenario())
    assert receipt.retrieval_outcome is expected
    assert receipt.weak_item_proposal is not None


def test_questions_shown_does_not_imply_review_pass() -> None:
    async def scenario():
        runtime, *_ = await _create_runtime()
        runtime.start()
        await runtime.submit(_attempt("item-1", "A1"))
        await runtime.submit(_attempt("item-2", "W2", ordinal=2))
        return runtime.complete()

    receipt = asyncio.run(scenario())
    assert receipt.result.total_attempts == 2
    assert receipt.result.passed is False
    assert "required_items" in receipt.result.completion.failed_gates
    assert "minimum_retrieval_score" in receipt.result.completion.failed_gates


def test_answer_reveal_contaminates_same_round_and_can_never_pass() -> None:
    async def scenario():
        runtime, *_ = await _create_runtime(
            items=(_objective_item(1),),
            target_item_count=1,
            max_same_round_retries=1,
        )
        runtime.start()
        first = await runtime.submit(
            _attempt(
                "item-1",
                "A1",
                answer_revealed_before_submission=True,
            )
        )
        second_question = runtime.present_question()
        second = await runtime.submit(_attempt("item-1", "A1", ordinal=2))
        final = runtime.complete()
        return runtime, first, second_question, second, final

    runtime, first, question, second, final = asyncio.run(scenario())
    assert first.retrieval_outcome is ReviewRetrievalOutcome.HINT_DEPENDENT
    assert question.occurrence_number == 2
    assert second.retrieval_outcome is ReviewRetrievalOutcome.HINT_DEPENDENT
    assert final.result.passed is False
    assert final.result.item_results[0].contaminated_by_answer_reveal is True
    assert len(runtime.snapshot.round.attempts) == 2
    assert len(runtime.snapshot.round.weak_item_proposals) == 2


def test_weak_item_is_rolled_to_round_tail_and_retry_is_bounded() -> None:
    async def scenario():
        runtime, *_ = await _create_runtime(max_same_round_retries=1)
        first_question = runtime.start()
        first = await runtime.submit(_attempt("item-1", "W1"))
        second_question = runtime.present_question()
        await runtime.submit(_attempt("item-2", "A2", ordinal=2))
        retry_question = runtime.present_question()
        retry = await runtime.submit(_attempt("item-1", "W1", ordinal=3))
        return runtime, first_question, first, second_question, retry_question, retry

    runtime, first_question, first, second_question, retry_question, retry = (
        asyncio.run(scenario())
    )
    assert first_question.item_id == "item-1"
    assert first.weak_item_proposal is not None
    assert first.weak_item_proposal.same_round_retry_eligible is True
    assert first.weak_item_proposal.requires_memory_port is True
    assert first.weak_item_proposal.executed is False
    assert second_question.item_id == "item-2"
    assert retry_question.item_id == "item-1"
    assert retry_question.occurrence_number == 2
    assert retry.weak_item_proposal is not None
    assert retry.weak_item_proposal.same_round_retry_eligible is False
    assert runtime.snapshot.state is ReviewSessionState.READY_TO_COMPLETE
    assert len(runtime.snapshot.round.attempts) == 3


def test_difficult_recall_is_weak_but_can_satisfy_retrieval_pass_policy() -> None:
    policy = ReviewCompletionPolicy(minimum_retrieval_score=0.8)

    async def scenario():
        runtime, *_ = await _create_runtime(
            items=(_objective_item(1),),
            target_item_count=1,
            completion_policy=policy,
        )
        runtime.start()
        receipt = await runtime.submit(
            _attempt("item-1", "A1", response_latency_ms=30_001)
        )
        return receipt, runtime.complete()

    attempt_receipt, round_receipt = asyncio.run(scenario())
    assert attempt_receipt.weak_item_proposal is not None
    assert attempt_receipt.weak_item_proposal.same_round_retry_eligible is False
    assert round_receipt.result.passed is True
    assert round_receipt.result.completion.retrieval_score == 0.8


def test_minimum_retrieval_score_is_an_independent_completion_gate() -> None:
    policy = ReviewCompletionPolicy(minimum_retrieval_score=0.95)

    async def scenario():
        runtime, *_ = await _create_runtime(completion_policy=policy)
        runtime.start()
        await runtime.submit(_attempt("item-1", "A1"))
        await runtime.submit(
            _attempt("item-2", "A2", ordinal=2, response_latency_ms=30_001)
        )
        return runtime.complete()

    receipt = asyncio.run(scenario())
    assert all(item.passed for item in receipt.result.item_results)
    assert receipt.result.completion.retrieval_score == 0.9
    assert receipt.result.completion.failed_gates == ("minimum_retrieval_score",)


def test_critical_concept_must_pass_even_when_other_gates_are_disabled() -> None:
    policy = ReviewCompletionPolicy(
        require_all_required_items_pass=False,
        minimum_retrieval_score=0.0,
        require_critical_concepts_pass=True,
    )

    async def scenario():
        runtime, *_ = await _create_runtime(
            items=(
                _objective_item(1, required=False, critical=True),
                _objective_item(2, required=False),
            ),
            completion_policy=policy,
        )
        runtime.start()
        await runtime.submit(_attempt("item-1", "W1"))
        await runtime.submit(_attempt("item-2", "A2", ordinal=2))
        return runtime.complete()

    receipt = asyncio.run(scenario())
    assert receipt.result.passed is False
    assert receipt.result.completion.failed_gates == ("critical_concepts",)
    assert receipt.result.completion.critical_concept_ids == ("concept-1",)


def test_duplicate_answer_replays_cached_receipt_without_advancing_round() -> None:
    async def scenario():
        runtime, *_ = await _create_runtime()
        runtime.start()
        attempt = _attempt("item-1", "A1")
        first = await runtime.submit(attempt)
        before = runtime.snapshot
        duplicate = await runtime.submit(attempt)
        return runtime, before, first, duplicate

    runtime, before, first, duplicate = asyncio.run(scenario())
    assert duplicate.duplicate is True
    assert duplicate.receipt_id == first.receipt_id
    assert duplicate.provider_calls == 0
    assert runtime.snapshot == before
    assert len(runtime.snapshot.round.attempt_receipts) == 1


def test_conflicting_duplicate_identity_is_rejected_without_mutation() -> None:
    async def scenario():
        runtime, *_ = await _create_runtime()
        runtime.start()
        original = _attempt("item-1", "A1")
        await runtime.submit(original)
        before = runtime.snapshot
        conflict = await runtime.submit(
            _attempt(
                "item-1",
                "W1",
                attempt_id="conflicting-attempt",
            )
        )
        return runtime, before, conflict

    runtime, before, conflict = asyncio.run(scenario())
    assert conflict.conflict is True
    assert conflict.provider_calls == 0
    assert conflict.reason == "conflicting_review_submission_identity"
    assert runtime.snapshot == before


def test_concurrent_duplicate_semantic_answer_calls_provider_once() -> None:
    async def scenario():
        provider = SlowSemanticProvider(_semantic_output())
        runtime, *_ = await _create_runtime(
            items=(_semantic_item(),),
            target_item_count=1,
            evaluator=EvaluatorV2(provider),
        )
        runtime.start()
        attempt = _attempt("item-1", "It uses capacity and load factor")
        first_task = asyncio.create_task(runtime.submit(attempt))
        await provider.started.wait()
        duplicate_task = asyncio.create_task(runtime.submit(attempt))
        await asyncio.sleep(0)
        provider.release.set()
        first, duplicate = await asyncio.gather(first_task, duplicate_task)
        return provider, runtime, first, duplicate

    provider, runtime, first, duplicate = asyncio.run(scenario())
    assert len(provider.calls) == 1
    assert first.provider_calls == 1
    assert duplicate.duplicate is True
    assert duplicate.provider_calls == 0
    assert len(runtime.snapshot.round.attempt_receipts) == 1


def test_semantic_timeout_is_failed_retrieval_and_remains_bounded() -> None:
    provider = FakeSemanticProvider(None, error=TimeoutError())

    async def scenario():
        runtime, *_ = await _create_runtime(
            items=(_semantic_item(),),
            target_item_count=1,
            evaluator=EvaluatorV2(provider),
        )
        runtime.start()
        receipt = await runtime.submit(_attempt("item-1", "threshold"))
        return runtime, receipt

    runtime, receipt = asyncio.run(scenario())
    assert len(provider.calls) == 1
    assert receipt.evaluation_result is not None
    assert receipt.evaluation_result.outcome is EvaluationOutcome.INSUFFICIENT_EVIDENCE
    assert receipt.retrieval_outcome is ReviewRetrievalOutcome.FAILED
    assert receipt.provider_calls == 1
    assert runtime.snapshot.state is ReviewSessionState.READY_TO_COMPLETE


def test_invalid_semantic_output_fails_without_fake_score() -> None:
    provider = FakeSemanticProvider("{not-json")

    async def scenario():
        runtime, *_ = await _create_runtime(
            items=(_semantic_item(),),
            target_item_count=1,
            evaluator=EvaluatorV2(provider),
        )
        runtime.start()
        return await runtime.submit(_attempt("item-1", "threshold"))

    receipt = asyncio.run(scenario())
    assert receipt.evaluation_result is not None
    assert receipt.evaluation_result.outcome is EvaluationOutcome.INVALID_ATTEMPT
    assert receipt.evaluation_result.score is None
    assert receipt.retrieval_outcome is ReviewRetrievalOutcome.FAILED


def test_pause_resume_and_crash_dto_preserve_current_question() -> None:
    async def scenario():
        runtime, *_ = await _create_runtime()
        question = runtime.start()
        runtime.pause()
        dto = ReviewSessionCrashDTO.model_validate_json(
            runtime.to_crash_dto().model_dump_json()
        )
        restored = ReviewSessionRuntime.from_crash_dto(
            dto,
            evaluator=EvaluatorV2(),
            clock_ms=FakeClock(),
        )
        resumed = restored.resume()
        return runtime, restored, question, resumed

    runtime, restored, question, resumed = asyncio.run(scenario())
    assert runtime.snapshot.state is ReviewSessionState.PAUSED
    assert restored.snapshot.state is ReviewSessionState.ACTIVE
    assert resumed == question


def test_crash_during_evaluation_fails_closed_without_provider_replay() -> None:
    async def scenario():
        runtime, *_ = await _create_runtime()
        runtime.start()
        payload = runtime.snapshot.model_dump(mode="python")
        payload.update(
            {
                "state": ReviewSessionState.EVALUATING,
                "state_history": (
                    *runtime.snapshot.state_history,
                    ReviewSessionState.EVALUATING,
                ),
                "pending_submission_id": "submission-interrupted",
            }
        )
        dto = ReviewSessionCrashDTO(
            session=ReviewSession.model_validate(payload),
            exported_at_ms=10_000,
        )
        return ReviewSessionRuntime.from_crash_dto(
            dto,
            evaluator=EvaluatorV2(),
            clock_ms=FakeClock(),
        )

    restored = asyncio.run(scenario())
    assert restored.snapshot.state is ReviewSessionState.FAILED
    assert restored.snapshot.pending_submission_id is None
    assert restored.snapshot.failure_reason == "interrupted_inflight_review_evaluation"


def test_duplicate_answer_still_replays_after_ready_state_crash_restore() -> None:
    async def scenario():
        runtime, *_ = await _create_runtime(
            items=(_objective_item(1),),
            target_item_count=1,
        )
        runtime.start()
        attempt = _attempt("item-1", "A1")
        await runtime.submit(attempt)
        restored = ReviewSessionRuntime.from_crash_dto(
            runtime.to_crash_dto(),
            evaluator=EvaluatorV2(),
            clock_ms=FakeClock(),
        )
        duplicate = await restored.submit(attempt)
        return restored, duplicate

    restored, duplicate = asyncio.run(scenario())
    assert restored.snapshot.state is ReviewSessionState.READY_TO_COMPLETE
    assert duplicate.duplicate is True
    assert duplicate.provider_calls == 0


def test_explicit_why_uses_teacher_port_but_normal_review_does_not() -> None:
    async def scenario():
        teacher = FakeTeacherExplanationPort()
        runtime, *_ = await _create_runtime(
            items=(_objective_item(1),),
            target_item_count=1,
            teacher_port=teacher,
        )
        runtime.start()
        assert teacher.calls == []
        explanation = await runtime.ask_why("为什么主动回忆有效？")
        assert len(teacher.calls) == 1
        attempt_receipt = await runtime.submit(_attempt("item-1", "A1"))
        round_receipt = runtime.complete()
        return teacher, explanation, attempt_receipt, round_receipt

    teacher, explanation, attempt_receipt, round_receipt = asyncio.run(scenario())
    assert len(teacher.calls) == 1
    assert explanation.available is True
    assert explanation.provider_calls == 1
    assert attempt_receipt.provider_calls == 0
    assert round_receipt.result.teacher_provider_calls == 1
    assert round_receipt.result.evaluator_provider_calls == 0


def test_missing_teacher_port_is_non_blocking_and_provider_free() -> None:
    async def scenario():
        runtime, *_ = await _create_runtime()
        runtime.start()
        return await runtime.ask_why("为什么？")

    receipt = asyncio.run(scenario())
    assert receipt.available is False
    assert receipt.provider_calls == 0
    assert receipt.reason == "teacher_explanation_port_unavailable"


def test_untrusted_question_bank_output_is_rejected() -> None:
    item = _objective_item(1).model_dump(mode="python")
    item["trusted"] = False
    bank = FakeQuestionBank((item,))

    with pytest.raises(ValidationError):
        asyncio.run(
            _create_runtime(
                target_item_count=1,
                question_bank=bank,
            )
        )


def test_insufficient_question_bank_items_fail_closed() -> None:
    with pytest.raises(ReviewSessionError, match="insufficient_trusted_review_items"):
        asyncio.run(
            _create_runtime(
                target_item_count=2,
                question_bank=FakeQuestionBank((_objective_item(1),)),
            )
        )


def test_memory_scope_mismatch_fails_before_question_selection() -> None:
    bank = FakeQuestionBank((_objective_item(1), _objective_item(2)))
    with pytest.raises(ReviewSessionError, match="review_memory_scope_mismatch"):
        asyncio.run(
            _create_runtime(
                memory_port=FakeMemoryPort(("concept-1",)),
                question_bank=bank,
            )
        )
    assert bank.calls == []


def test_fabricated_attempt_evidence_is_rejected_without_evaluator_call() -> None:
    async def scenario():
        runtime, *_ = await _create_runtime()
        runtime.start()
        before = runtime.snapshot
        receipt = await runtime.submit(
            _attempt("item-1", "A1", evidence_ids=("fabricated",))
        )
        return runtime, before, receipt

    runtime, before, receipt = asyncio.run(scenario())
    assert receipt.accepted is False
    assert receipt.conflict is True
    assert receipt.provider_calls == 0
    assert receipt.reason == "review_evidence_binding_mismatch"
    assert runtime.snapshot == before


def test_round_receipt_is_strict_json_round_trippable() -> None:
    async def scenario():
        runtime, *_ = await _create_runtime(
            items=(_objective_item(1),),
            target_item_count=1,
        )
        runtime.start()
        await runtime.submit(_attempt("item-1", "A1"))
        return runtime.complete()

    receipt = asyncio.run(scenario())
    restored = ReviewRoundReceipt.model_validate_json(receipt.model_dump_json())
    assert restored == receipt
    assert restored.attempt_receipt_ids


def test_same_objective_review_replays_deterministically() -> None:
    async def run_once():
        runtime, *_ = await _create_runtime(
            items=(_objective_item(1),),
            target_item_count=1,
        )
        runtime.start()
        attempt_receipt = await runtime.submit(_attempt("item-1", "A1"))
        round_receipt = runtime.complete()
        return attempt_receipt, round_receipt, runtime.snapshot

    first = asyncio.run(run_once())
    second = asyncio.run(run_once())
    assert first == second


def test_runtime_has_no_db_or_progress_write_dependency() -> None:
    source = inspect.getsource(runtime_module)
    tree = ast.parse(source)
    evaluator_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "evaluate"
    ]
    teacher_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "explain"
    ]
    assert len(evaluator_calls) == 1
    assert len(teacher_calls) == 1
    assert all(
        forbidden not in source
        for forbidden in (
            "StudentModel",
            "SessionLocal",
            "active_teaching",
            "generic_knowledge_api",
            "memory_port.save",
            "progress_port.save",
            ".commit(",
        )
    )
