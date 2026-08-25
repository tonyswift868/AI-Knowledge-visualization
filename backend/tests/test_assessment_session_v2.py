from __future__ import annotations

import ast
import asyncio
import inspect
from typing import Any

import pytest
from pydantic import ValidationError

from app.teaching.assessment_v2 import (
    AssessmentCommitPlan,
    AssessmentQuestion,
    AssessmentSessionRestartDTO,
    AssessmentSessionRuntime,
    AssessmentSessionState,
    AssessmentSubmission,
    AssessmentType,
    VerificationStatus,
)
from app.teaching.contracts import HintDependency, StudentMove
from app.teaching.evaluator_v2 import (
    EvaluationEvidence,
    EvaluationOutcome,
    EvaluationPath,
    EvaluationRubric,
    EvaluatorV2,
    MisconceptionAnswer,
    ProposedLearningEventType,
    RubricCriterion,
)
import app.teaching.assessment_v2.runtime as runtime_module


class FakeClock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


EVIDENCE_1 = EvaluationEvidence(
    evidence_id="chunk-1",
    content="Resize threshold is capacity multiplied by load factor.",
)
EVIDENCE_2 = EvaluationEvidence(
    evidence_id="chunk-2",
    content="A resize starts after size crosses the threshold.",
)


def _objective_rubric(
    rubric_id: str = "rubric-1",
    *,
    accepted: str = "12",
    partial: str = "about twelve",
    misconception: str = "16",
    evidence_id: str = "chunk-1",
) -> EvaluationRubric:
    return EvaluationRubric(
        rubric_id=rubric_id,
        path=EvaluationPath.OBJECTIVE,
        accepted_answers=(accepted,),
        partial_answers=(partial,),
        misconception_answers=(
            MisconceptionAnswer(
                answer=misconception,
                misconception="size equals capacity before resize",
            ),
        ),
        required_evidence_ids=(evidence_id,),
    )


def _semantic_rubric() -> EvaluationRubric:
    return EvaluationRubric(
        rubric_id="semantic-rubric",
        path=EvaluationPath.SEMANTIC,
        criteria=(
            RubricCriterion(
                criterion_id="threshold",
                description="Explains threshold and load factor",
                weight=1.0,
            ),
        ),
        required_evidence_ids=("chunk-1",),
    )


def _question(
    question_id: str = "question-1",
    *,
    rubric_id: str = "rubric-1",
    evidence_id: str = "chunk-1",
    assessment_type: AssessmentType = AssessmentType.VERIFICATION,
    expires_at_ms: int | None = None,
) -> AssessmentQuestion:
    return AssessmentQuestion(
        question_id=question_id,
        concept_id="hashmap-resize",
        prompt=f"Assessment prompt for {question_id}",
        rubric_id=rubric_id,
        evidence_ids=(evidence_id,),
        assessment_type=assessment_type,
        expires_at_ms=expires_at_ms,
    )


def _runtime(
    *,
    evaluator: EvaluatorV2 | None = None,
    clock: FakeClock | None = None,
    two_questions: bool = False,
    assessment_type: AssessmentType = AssessmentType.VERIFICATION,
) -> AssessmentSessionRuntime:
    clock = clock or FakeClock()
    questions = [
        _question(assessment_type=assessment_type),
    ]
    rubrics = [_objective_rubric()]
    evidence = [EVIDENCE_1]
    if two_questions:
        questions.append(
            _question(
                "question-2",
                rubric_id="rubric-2",
                evidence_id="chunk-2",
                assessment_type=assessment_type,
            )
        )
        rubrics.append(
            _objective_rubric(
                "rubric-2",
                accepted="threshold crossed",
                partial="near threshold",
                misconception="capacity full",
                evidence_id="chunk-2",
            )
        )
        evidence.append(EVIDENCE_2)
    return AssessmentSessionRuntime.create(
        session_id="session-1",
        concept_id="hashmap-resize",
        questions=questions,
        rubrics=rubrics,
        evidence=evidence,
        evaluator=evaluator or EvaluatorV2(),
        clock_ms=clock,
    )


def _submission(
    *,
    submission_id: str = "submission-1",
    attempt_id: str = "attempt-1",
    question_id: str = "question-1",
    rubric_id: str = "rubric-1",
    answer: str = "12",
    evidence_ids: tuple[str, ...] = ("chunk-1",),
    hint_used: bool = False,
    hint_ids: tuple[str, ...] = (),
    hint_dependency: HintDependency = HintDependency.NONE,
    retry_of_attempt_id: str | None = None,
    submitted_at_ms: int = 1_000,
    student_move: StudentMove = StudentMove.ANSWER_ATTEMPT,
    self_attested_mastery: bool = False,
) -> AssessmentSubmission:
    return AssessmentSubmission(
        submission_id=submission_id,
        attempt_id=attempt_id,
        question_id=question_id,
        rubric_id=rubric_id,
        answer=answer,
        evidence_ids=evidence_ids,
        hint_used=hint_used,
        hint_ids=hint_ids,
        hint_dependency=hint_dependency,
        retry_of_attempt_id=retry_of_attempt_id,
        submitted_at_ms=submitted_at_ms,
        student_move=student_move,
        self_attested_mastery=self_attested_mastery,
    )


class FakeSemanticProvider:
    provider_name = "assessment-session-fake"

    def __init__(
        self,
        *,
        raw_output: object,
        error: Exception | None = None,
    ) -> None:
        self.raw_output = raw_output
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def structured(self, payload, response_model):
        self.calls.append({"payload": payload, "response_model": response_model})
        if self.error is not None:
            raise self.error
        return self.raw_output


class SlowSemanticProvider(FakeSemanticProvider):
    def __init__(self, *, raw_output: object) -> None:
        super().__init__(raw_output=raw_output)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def structured(self, payload, response_model):
        self.calls.append({"payload": payload, "response_model": response_model})
        self.started.set()
        await self.release.wait()
        return self.raw_output


class SlowFailingEvaluator(EvaluatorV2):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def evaluate(self, request):
        self.started.set()
        await self.release.wait()
        raise RuntimeError("synthetic evaluator failure")


def _valid_semantic_output(**changes) -> dict[str, object]:
    values: dict[str, object] = {
        "outcome": "correct",
        "score": 0.9,
        "confidence": 0.9,
        "reason": "answer satisfies the grounded threshold criterion",
        "cited_evidence_ids": ["chunk-1"],
        "criterion_results": [
            {
                "criterion_id": "threshold",
                "satisfied": True,
                "confidence": 0.9,
                "reason": "threshold relationship is present",
            }
        ],
    }
    values.update(changes)
    return values


def _semantic_runtime(provider, clock: FakeClock | None = None):
    clock = clock or FakeClock()
    return AssessmentSessionRuntime.create(
        session_id="semantic-session",
        concept_id="hashmap-resize",
        questions=(
            _question(
                rubric_id="semantic-rubric",
                assessment_type=AssessmentType.VERIFICATION,
            ),
        ),
        rubrics=(_semantic_rubric(),),
        evidence=(EVIDENCE_1,),
        evaluator=EvaluatorV2(provider),
        clock_ms=clock,
    )


def _event_types(receipt) -> set[ProposedLearningEventType]:
    if receipt.commit_plan is None:
        return set()
    return {event.event_type for event in receipt.commit_plan.proposed_events}


def test_session_contract_declares_every_required_lifecycle_state() -> None:
    assert {item.value for item in AssessmentSessionState} == {
        "created",
        "active",
        "submitted",
        "evaluating",
        "evaluated",
        "completed",
        "aborted",
        "failed",
    }


def test_complete_objective_session_lifecycle_and_receipt() -> None:
    runtime = _runtime()
    assert runtime.snapshot.state is AssessmentSessionState.CREATED
    runtime.start()
    receipt = asyncio.run(runtime.submit(_submission()))
    assert receipt.accepted is True
    assert receipt.evaluation_result is not None
    assert receipt.evaluation_result.outcome is EvaluationOutcome.CORRECT
    assert receipt.provider_calls == 0
    assert receipt.verification.status is VerificationStatus.NOT_QUALIFIED
    assert receipt.commit_plan is not None
    assert receipt.commit_plan.requires_commit_port is True
    assert receipt.commit_plan.executed is False
    assert ProposedLearningEventType.INDEPENDENT_VERIFICATION_SIGNAL not in (
        _event_types(receipt)
    )
    assert runtime.snapshot.state_history == (
        AssessmentSessionState.CREATED,
        AssessmentSessionState.ACTIVE,
        AssessmentSessionState.SUBMITTED,
        AssessmentSessionState.EVALUATING,
        AssessmentSessionState.EVALUATED,
    )
    result = runtime.complete()
    assert result.final_state is AssessmentSessionState.COMPLETED
    assert result.evaluated_attempts == 1
    assert runtime.snapshot.state is AssessmentSessionState.COMPLETED


def test_partial_first_attempt_can_retry_but_retry_does_not_auto_verify() -> None:
    runtime = _runtime()
    runtime.start()
    first = asyncio.run(runtime.submit(_submission(answer="about twelve")))
    assert first.evaluation_result is not None
    assert first.evaluation_result.outcome is EvaluationOutcome.PARTIAL
    runtime.resume()
    retry = asyncio.run(
        runtime.submit(
            _submission(
                submission_id="submission-2",
                attempt_id="attempt-2",
                retry_of_attempt_id="attempt-1",
            )
        )
    )
    assert retry.attempt_number == 2
    assert retry.evaluation_result is not None
    assert retry.evaluation_result.outcome is EvaluationOutcome.CORRECT
    assert retry.verification.first_attempt is False
    assert retry.verification.question_qualified is False
    assert ProposedLearningEventType.INDEPENDENT_VERIFICATION_SIGNAL not in (
        _event_types(retry)
    )


def test_hint_used_correct_attempt_is_not_verification_qualified() -> None:
    runtime = _runtime()
    runtime.start()
    receipt = asyncio.run(
        runtime.submit(
            _submission(hint_used=True, hint_ids=("hint-1",))
        )
    )
    assert receipt.evaluation_result is not None
    assert receipt.evaluation_result.outcome is EvaluationOutcome.CORRECT
    assert receipt.verification.hint_free is False
    assert receipt.verification.status is VerificationStatus.NOT_QUALIFIED
    assert ProposedLearningEventType.INDEPENDENT_VERIFICATION_SIGNAL not in (
        _event_types(receipt)
    )


def test_persistent_hint_dependency_blocks_verification_without_current_hint() -> None:
    runtime = _runtime()
    runtime.start()
    receipt = asyncio.run(
        runtime.submit(
            _submission(hint_dependency=HintDependency.PERSISTENT)
        )
    )
    assert receipt.evaluation_result is not None
    assert receipt.evaluation_result.outcome is EvaluationOutcome.CORRECT
    assert receipt.verification.hint_free is True
    assert receipt.verification.hint_dependency is HintDependency.PERSISTENT
    assert receipt.verification.hint_dependency_clear is False
    assert receipt.verification.question_qualified is False


def test_invalid_blank_answer_is_evaluated_without_a_score() -> None:
    runtime = _runtime()
    runtime.start()
    receipt = asyncio.run(runtime.submit(_submission(answer="")))
    assert receipt.evaluation_result is not None
    assert receipt.evaluation_result.outcome is EvaluationOutcome.INVALID_ATTEMPT
    assert receipt.evaluation_result.score is None
    assert receipt.verification.status is VerificationStatus.NOT_QUALIFIED
    assert runtime.snapshot.state is AssessmentSessionState.EVALUATED


def test_expired_attempt_is_rejected_before_evaluator_binding() -> None:
    clock = FakeClock(1_000)
    runtime = AssessmentSessionRuntime.create(
        session_id="expired-session",
        concept_id="hashmap-resize",
        questions=(_question(expires_at_ms=999),),
        rubrics=(_objective_rubric(),),
        evidence=(EVIDENCE_1,),
        evaluator=EvaluatorV2(),
        clock_ms=clock,
    )
    runtime.start()
    receipt = asyncio.run(runtime.submit(_submission()))
    assert receipt.accepted is False
    assert receipt.reason == "attempt_expired"
    assert receipt.evaluation_result is None
    assert receipt.provider_calls == 0
    assert runtime.snapshot.state is AssessmentSessionState.ACTIVE


def test_duplicate_submission_replays_receipt_without_evaluator_call() -> None:
    runtime = _runtime()
    runtime.start()
    submission = _submission()
    original = asyncio.run(runtime.submit(submission))
    duplicate = asyncio.run(runtime.submit(submission))
    assert original.provider_calls == 0
    assert duplicate.duplicate is True
    assert duplicate.provider_calls == 0
    assert duplicate.receipt_id == original.receipt_id
    assert len(runtime.snapshot.receipts) == 1


def test_conflicting_submission_identity_is_rejected_without_mutation() -> None:
    runtime = _runtime()
    runtime.start()
    original = _submission()
    asyncio.run(runtime.submit(original))
    before = runtime.snapshot
    conflict = asyncio.run(
        runtime.submit(
            _submission(
                submission_id=original.submission_id,
                attempt_id="attempt-conflict",
                answer="16",
            )
        )
    )
    assert conflict.conflict is True
    assert conflict.provider_calls == 0
    assert conflict.reason == "conflicting_submission_identity"
    assert runtime.snapshot == before


def test_concurrent_duplicate_semantic_submission_calls_provider_once() -> None:
    async def scenario():
        provider = SlowSemanticProvider(raw_output=_valid_semantic_output())
        runtime = _semantic_runtime(provider)
        runtime.start()
        submission = _submission(
            answer="It crosses capacity times load factor",
            rubric_id="semantic-rubric",
        )
        first_task = asyncio.create_task(runtime.submit(submission))
        await provider.started.wait()
        duplicate_task = asyncio.create_task(runtime.submit(submission))
        await asyncio.sleep(0)
        provider.release.set()
        first, duplicate = await asyncio.gather(first_task, duplicate_task)
        return provider, runtime, first, duplicate

    provider, runtime, first, duplicate = asyncio.run(scenario())
    assert len(provider.calls) == 1
    assert first.provider_calls == 1
    assert duplicate.duplicate is True
    assert duplicate.provider_calls == 0
    assert len(runtime.snapshot.receipts) == 1
    payload = provider.calls[0]["payload"]
    assert payload["question"] == "Assessment prompt for question-1"
    assert payload["rubric_id"] == "semantic-rubric"
    assert [item["evidence_id"] for item in payload["evidence"]] == ["chunk-1"]


def test_provider_timeout_is_fail_safe_and_session_can_be_resumed() -> None:
    provider = FakeSemanticProvider(raw_output=None, error=TimeoutError())
    runtime = _semantic_runtime(provider)
    runtime.start()
    receipt = asyncio.run(
        runtime.submit(
            _submission(answer="threshold", rubric_id="semantic-rubric")
        )
    )
    assert receipt.evaluation_result is not None
    assert receipt.evaluation_result.outcome is EvaluationOutcome.INSUFFICIENT_EVIDENCE
    assert receipt.evaluation_result.score is None
    assert receipt.provider_calls == 1
    assert runtime.snapshot.state is AssessmentSessionState.EVALUATED
    runtime.resume()
    assert runtime.snapshot.state is AssessmentSessionState.ACTIVE


def test_invalid_structured_output_is_fail_safe_without_score() -> None:
    provider = FakeSemanticProvider(raw_output="{not-json")
    runtime = _semantic_runtime(provider)
    runtime.start()
    receipt = asyncio.run(
        runtime.submit(
            _submission(answer="threshold", rubric_id="semantic-rubric")
        )
    )
    assert receipt.evaluation_result is not None
    assert receipt.evaluation_result.outcome is EvaluationOutcome.INVALID_ATTEMPT
    assert receipt.evaluation_result.score is None
    assert receipt.provider_calls == 1


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"question_id": "wrong-question"}, "wrong_question_binding"),
        ({"rubric_id": "wrong-rubric"}, "rubric_mismatch"),
        ({"evidence_ids": ("wrong-evidence",)}, "evidence_binding_mismatch"),
    ],
)
def test_wrong_question_rubric_or_evidence_binding_fails_closed(
    changes,
    reason,
) -> None:
    runtime = _runtime()
    runtime.start()
    receipt = asyncio.run(runtime.submit(_submission(**changes)))
    assert receipt.accepted is False
    assert receipt.reason == reason
    assert receipt.provider_calls == 0
    assert runtime.snapshot.state is AssessmentSessionState.FAILED


@pytest.mark.parametrize(
    ("provider_output", "expected_reason"),
    [
        (
            _valid_semantic_output(cited_evidence_ids=["invented"]),
            "fabricated_citation",
        ),
        (_valid_semantic_output(score=1.5), "semantic_provider_output_invalid"),
    ],
)
def test_fabricated_citation_and_score_overflow_remain_scoreless(
    provider_output,
    expected_reason,
) -> None:
    provider = FakeSemanticProvider(raw_output=provider_output)
    runtime = _semantic_runtime(provider)
    runtime.start()
    receipt = asyncio.run(
        runtime.submit(
            _submission(answer="threshold", rubric_id="semantic-rubric")
        )
    )
    assert receipt.evaluation_result is not None
    assert receipt.evaluation_result.outcome is EvaluationOutcome.INVALID_ATTEMPT
    assert receipt.evaluation_result.score is None
    assert receipt.evaluation_result.evidence_refs == ()
    assert receipt.evaluation_result.reason == expected_reason


def test_commit_plan_contract_rejects_duplicate_event_types() -> None:
    runtime = _runtime()
    runtime.start()
    receipt = asyncio.run(runtime.submit(_submission()))
    assert receipt.commit_plan is not None
    answer_event = next(
        event
        for event in receipt.commit_plan.proposed_events
        if event.event_type is ProposedLearningEventType.ANSWER_CORRECT
    )
    duplicate_type = answer_event.model_copy(update={"event_id": "different-id"})
    with pytest.raises(ValidationError):
        AssessmentCommitPlan(
            plan_id="duplicate-plan",
            session_id="session-1",
            submission_id="submission-1",
            verification=receipt.verification,
            proposed_events=(answer_event, duplicate_type),
            reason="invalid duplicate event proposal",
        )


def test_abort_while_evaluating_discards_result_and_commit_plan() -> None:
    async def scenario():
        provider = SlowSemanticProvider(raw_output=_valid_semantic_output())
        runtime = _semantic_runtime(provider)
        runtime.start()
        task = asyncio.create_task(
            runtime.submit(
                _submission(answer="threshold", rubric_id="semantic-rubric")
            )
        )
        await provider.started.wait()
        assert runtime.snapshot.state is AssessmentSessionState.EVALUATING
        await runtime.abort("user_cancelled")
        provider.release.set()
        receipt = await task
        return provider, runtime, receipt

    provider, runtime, receipt = asyncio.run(scenario())
    assert len(provider.calls) == 1
    assert runtime.snapshot.state is AssessmentSessionState.ABORTED
    assert receipt.accepted is False
    assert receipt.reason == "session_aborted_during_evaluation"
    assert receipt.commit_plan is None
    assert runtime.snapshot.commit_plans == ()


def test_abort_wins_over_late_evaluator_runtime_failure() -> None:
    async def scenario():
        evaluator = SlowFailingEvaluator()
        runtime = _runtime(evaluator=evaluator)
        runtime.start()
        task = asyncio.create_task(runtime.submit(_submission()))
        await evaluator.started.wait()
        await runtime.abort("user_cancelled")
        evaluator.release.set()
        receipt = await task
        return runtime, receipt

    runtime, receipt = asyncio.run(scenario())
    assert runtime.snapshot.state is AssessmentSessionState.ABORTED
    assert runtime.snapshot.failure_reason == "user_cancelled"
    assert receipt.accepted is False
    assert receipt.reason == "session_aborted_during_evaluation"
    assert receipt.commit_plan is None


def test_session_restart_dto_round_trips_and_can_complete() -> None:
    clock = FakeClock()
    runtime = _runtime(clock=clock)
    runtime.start()
    asyncio.run(runtime.submit(_submission()))
    dto = runtime.to_restart_dto()
    restored_dto = AssessmentSessionRestartDTO.model_validate_json(
        dto.model_dump_json()
    )
    restored = AssessmentSessionRuntime.from_restart_dto(
        restored_dto,
        evaluator=EvaluatorV2(),
        clock_ms=clock,
    )
    assert restored.snapshot == runtime.snapshot
    result = restored.complete()
    assert result.final_state is AssessmentSessionState.COMPLETED


def test_restart_of_inflight_snapshot_fails_closed_without_provider_replay() -> None:
    clock = FakeClock()
    runtime = _runtime(clock=clock)
    runtime.start()
    payload = runtime.snapshot.model_dump(mode="python")
    payload.update(
        {
            "state": AssessmentSessionState.EVALUATING,
            "state_history": (
                *runtime.snapshot.state_history,
                AssessmentSessionState.SUBMITTED,
                AssessmentSessionState.EVALUATING,
            ),
            "pending_submission_id": "submission-interrupted",
        }
    )
    dto = AssessmentSessionRestartDTO(
        session=runtime.snapshot.__class__.model_validate(payload),
        exported_at_ms=clock(),
    )
    restored = AssessmentSessionRuntime.from_restart_dto(
        dto,
        evaluator=EvaluatorV2(),
        clock_ms=clock,
    )
    assert restored.snapshot.state is AssessmentSessionState.FAILED
    assert restored.snapshot.pending_submission_id is None
    assert restored.snapshot.failure_reason == "interrupted_inflight_evaluation"


def test_same_objective_session_replays_deterministically() -> None:
    async def run_once():
        runtime = _runtime(clock=FakeClock())
        runtime.start()
        receipt = await runtime.submit(_submission())
        return receipt, runtime.snapshot

    first = asyncio.run(run_once())
    second = asyncio.run(run_once())
    assert first == second


def test_two_grounded_questions_are_required_before_verification_qualifies() -> None:
    runtime = _runtime(two_questions=True)
    runtime.start("question-1")
    first = asyncio.run(runtime.submit(_submission()))
    assert first.verification.question_qualified is True
    assert first.verification.status is VerificationStatus.NOT_QUALIFIED
    assert ProposedLearningEventType.INDEPENDENT_VERIFICATION_SIGNAL not in (
        _event_types(first)
    )
    runtime.resume("question-2")
    second = asyncio.run(
        runtime.submit(
            _submission(
                submission_id="submission-2",
                attempt_id="attempt-2",
                question_id="question-2",
                rubric_id="rubric-2",
                answer="threshold crossed",
                evidence_ids=("chunk-2",),
            )
        )
    )
    assert second.verification.status is VerificationStatus.QUALIFIED
    assert second.verification.qualified_question_ids == (
        "question-1",
        "question-2",
    )
    assert ProposedLearningEventType.INDEPENDENT_VERIFICATION_SIGNAL in (
        _event_types(second)
    )


def test_simple_practice_question_never_qualifies_concept_verification() -> None:
    runtime = _runtime(assessment_type=AssessmentType.PRACTICE)
    runtime.start()
    receipt = asyncio.run(runtime.submit(_submission()))
    assert receipt.evaluation_result is not None
    assert receipt.evaluation_result.outcome is EvaluationOutcome.CORRECT
    assert receipt.verification.question_qualified is False
    assert receipt.verification.status is VerificationStatus.NOT_QUALIFIED
    assert ProposedLearningEventType.INDEPENDENT_VERIFICATION_SIGNAL not in (
        _event_types(receipt)
    )


def test_self_attested_mastery_never_qualifies_verification() -> None:
    runtime = _runtime()
    runtime.start()
    receipt = asyncio.run(
        runtime.submit(
            _submission(
                student_move=StudentMove.LEARNER_SAYS_MASTERED,
                self_attested_mastery=True,
            )
        )
    )
    assert receipt.evaluation_result is not None
    assert receipt.evaluation_result.outcome is EvaluationOutcome.CORRECT
    assert receipt.verification.question_qualified is False
    assert ProposedLearningEventType.INDEPENDENT_VERIFICATION_SIGNAL not in (
        _event_types(receipt)
    )


def test_runtime_only_calls_evaluator_and_has_no_student_state_commit_dependency(
) -> None:
    source = inspect.getsource(runtime_module)
    tree = ast.parse(source)
    evaluator_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "evaluate"
    ]
    assert len(evaluator_calls) == 1
    assert all(
        forbidden not in source
        for forbidden in (
            "StudentModel",
            "SessionLocal",
            "active_teaching",
            "generic_knowledge_api",
            "commit_port.",
        )
    )
