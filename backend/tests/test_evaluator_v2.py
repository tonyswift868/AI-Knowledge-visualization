from __future__ import annotations

import ast
import asyncio
import inspect
from typing import Any

from app.teaching.contracts import StudentMove
from app.teaching.evaluator_v2 import (
    AssessmentAttempt,
    EvaluationEvidence,
    EvaluationOutcome,
    EvaluationPath,
    EvaluationResult,
    EvaluationRubric,
    EvaluatorDecision,
    EvaluatorRequest,
    EvaluatorV2,
    MisconceptionAnswer,
    ProposedLearningEventType,
    RubricCriterion,
    SemanticEvaluationDraft,
)
import app.teaching.evaluator_v2.engine as evaluator_module


EVIDENCE = EvaluationEvidence(
    evidence_id="chunk-1",
    content="HashMap resizes when size exceeds capacity multiplied by load factor.",
    source_reference="java-map-contract",
)
UNRELATED_EVIDENCE = EvaluationEvidence(
    evidence_id="chunk-unrelated",
    content="This evidence concerns a different collection type.",
)


def _attempt(**changes) -> AssessmentAttempt:
    values = {
        "assessment_id": "assessment-1",
        "attempt_id": "attempt-1",
        "question": "When does HashMap resize?",
        "answer": "12",
        "student_move": StudentMove.ANSWER_ATTEMPT,
    }
    values.update(changes)
    return AssessmentAttempt(**values)


def _objective_rubric(**changes) -> EvaluationRubric:
    values = {
        "rubric_id": "objective-rubric",
        "path": EvaluationPath.OBJECTIVE,
        "accepted_answers": ("12",),
        "partial_answers": ("about twelve",),
        "misconception_answers": (
            MisconceptionAnswer(
                answer="16",
                misconception="size must equal capacity before resize",
            ),
        ),
    }
    values.update(changes)
    return EvaluationRubric(**values)


def _semantic_rubric(**changes) -> EvaluationRubric:
    values = {
        "rubric_id": "semantic-rubric",
        "path": EvaluationPath.SEMANTIC,
        "criteria": (
            RubricCriterion(
                criterion_id="threshold",
                description="Explains threshold as capacity multiplied by load factor",
                weight=1.0,
            ),
        ),
        "required_evidence_ids": ("chunk-1",),
    }
    values.update(changes)
    return EvaluationRubric(**values)


def _request(
    *,
    attempt: AssessmentAttempt | None = None,
    rubric: EvaluationRubric | None = None,
    evidence: tuple[EvaluationEvidence, ...] = (),
    processed_assessment_ids: tuple[str, ...] = (),
) -> EvaluatorRequest:
    return EvaluatorRequest(
        attempt=attempt or _attempt(),
        rubric=rubric or _objective_rubric(),
        evidence=evidence,
        processed_assessment_ids=processed_assessment_ids,
    )


class FakeSemanticProvider:
    provider_name = "fake-semantic-evaluator"

    def __init__(self, *, raw_output: object, error: Exception | None = None) -> None:
        self.raw_output = raw_output
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def structured(self, payload, response_model):
        self.calls.append({"payload": payload, "response_model": response_model})
        if self.error is not None:
            raise self.error
        return self.raw_output


def _valid_semantic_output(**changes) -> dict[str, object]:
    values: dict[str, object] = {
        "outcome": "correct",
        "score": 0.9,
        "confidence": 0.84,
        "reason": "answer satisfies the threshold criterion using supplied evidence",
        "cited_evidence_ids": ["chunk-1"],
        "criterion_results": [
            {
                "criterion_id": "threshold",
                "satisfied": True,
                "confidence": 0.88,
                "reason": "threshold relationship is stated",
            }
        ],
    }
    values.update(changes)
    return values


def _event_types(decision: EvaluatorDecision) -> set[ProposedLearningEventType]:
    return {event.event_type for event in decision.proposed_learning_events}


def test_exact_correct_objective_answer_uses_zero_provider_calls() -> None:
    decision = EvaluatorV2().evaluate_objective(_request())
    assert decision.result.outcome is EvaluationOutcome.CORRECT
    assert decision.result.score == 1.0
    assert decision.result.confidence == 1.0
    assert decision.result.provider_calls == 0
    assert _event_types(decision) == {
        ProposedLearningEventType.ANSWER_CORRECT,
        ProposedLearningEventType.ASSESSMENT_RESULT,
        ProposedLearningEventType.INDEPENDENT_VERIFICATION_SIGNAL,
    }


def test_partial_answer_has_bounded_score_distinct_from_confidence() -> None:
    request = _request(attempt=_attempt(answer="about twelve"))
    decision = EvaluatorV2().evaluate_objective(request)
    assert decision.result.outcome is EvaluationOutcome.PARTIAL
    assert decision.result.score == 0.5
    assert decision.result.confidence == 0.95
    assert decision.result.score != decision.result.confidence
    assert ProposedLearningEventType.ANSWER_PARTIAL in _event_types(decision)
    assert ProposedLearningEventType.INDEPENDENT_VERIFICATION_SIGNAL not in (
        _event_types(decision)
    )


def test_known_misconception_answer_is_explainably_incorrect() -> None:
    decision = EvaluatorV2().evaluate_objective(
        _request(attempt=_attempt(answer="16"))
    )
    assert decision.result.outcome is EvaluationOutcome.INCORRECT
    assert decision.result.score == 0.0
    assert "size must equal capacity" in decision.result.reason
    assert decision.result.rubric_signals == ("objective_match:misconception",)
    assert ProposedLearningEventType.ANSWER_INCORRECT in _event_types(decision)


def test_ambiguous_rubric_fails_safe_without_score_or_provider() -> None:
    rubric = _objective_rubric(partial_answers=("12",))
    decision = EvaluatorV2().evaluate_objective(_request(rubric=rubric))
    assert decision.result.outcome is EvaluationOutcome.AMBIGUOUS
    assert decision.result.score is None
    assert decision.result.provider_calls == 0
    assert "overlap" in decision.result.reason


def test_missing_required_evidence_is_insufficient_before_evaluation() -> None:
    rubric = _objective_rubric(required_evidence_ids=("chunk-1",))
    decision = EvaluatorV2().evaluate_objective(_request(rubric=rubric))
    assert decision.result.outcome is EvaluationOutcome.INSUFFICIENT_EVIDENCE
    assert decision.result.score is None
    assert decision.result.provider_calls == 0


def test_challenge_teacher_is_not_graded_as_an_incorrect_answer() -> None:
    attempt = _attempt(
        answer="Your premise is wrong",
        student_move=StudentMove.CHALLENGE_TEACHER,
    )
    decision = EvaluatorV2().evaluate_objective(_request(attempt=attempt))
    assert decision.result.outcome is EvaluationOutcome.INVALID_ATTEMPT
    assert decision.result.score is None
    assert ProposedLearningEventType.ANSWER_INCORRECT not in _event_types(decision)


def test_invalid_provider_json_fails_safe_without_score() -> None:
    provider = FakeSemanticProvider(raw_output="{not-json")
    request = _request(
        attempt=_attempt(answer="It crosses capacity times load factor"),
        rubric=_semantic_rubric(),
        evidence=(EVIDENCE,),
    )
    decision = asyncio.run(EvaluatorV2(provider).evaluate(request))
    assert decision.result.outcome is EvaluationOutcome.INVALID_ATTEMPT
    assert decision.result.score is None
    assert decision.result.provider_calls == 1
    assert len(provider.calls) == 1


def test_provider_cannot_add_mastery_or_state_fields() -> None:
    provider = FakeSemanticProvider(
        raw_output=_valid_semantic_output(mastery_state="independently_verified")
    )
    request = _request(
        attempt=_attempt(answer="It crosses the threshold"),
        rubric=_semantic_rubric(),
        evidence=(EVIDENCE,),
    )
    decision = asyncio.run(EvaluatorV2(provider).evaluate(request))
    assert decision.result.outcome is EvaluationOutcome.INVALID_ATTEMPT
    assert decision.result.score is None
    assert decision.result.reason == "semantic_provider_output_invalid"


def test_provider_timeout_returns_insufficient_evidence_without_score() -> None:
    provider = FakeSemanticProvider(raw_output=None, error=TimeoutError())
    request = _request(
        attempt=_attempt(answer="It crosses the threshold"),
        rubric=_semantic_rubric(),
        evidence=(EVIDENCE,),
    )
    decision = asyncio.run(EvaluatorV2(provider).evaluate(request))
    assert decision.result.outcome is EvaluationOutcome.INSUFFICIENT_EVIDENCE
    assert decision.result.score is None
    assert decision.result.reason == "semantic_provider_timeout"
    assert len(provider.calls) == 1


def test_duplicate_assessment_id_is_rejected_without_duplicate_event() -> None:
    request = _request(processed_assessment_ids=("assessment-1",))
    decision = EvaluatorV2().evaluate_objective(request)
    assert decision.result.outcome is EvaluationOutcome.INVALID_ATTEMPT
    assert decision.result.reason == "duplicate_assessment_id"
    assert decision.proposed_learning_events == ()


def test_same_objective_answer_replays_deterministically() -> None:
    request = _request()
    evaluator = EvaluatorV2()
    assert evaluator.evaluate_objective(request) == evaluator.evaluate_objective(request)


def test_provider_cannot_fabricate_citations() -> None:
    provider = FakeSemanticProvider(
        raw_output=_valid_semantic_output(cited_evidence_ids=["invented"])
    )
    request = _request(
        attempt=_attempt(answer="It crosses the threshold"),
        rubric=_semantic_rubric(),
        evidence=(EVIDENCE,),
    )
    decision = asyncio.run(EvaluatorV2(provider).evaluate(request))
    assert decision.result.outcome is EvaluationOutcome.INVALID_ATTEMPT
    assert decision.result.score is None
    assert decision.result.evidence_refs == ()
    assert decision.result.reason == "fabricated_citation"


def test_provider_cannot_cite_request_evidence_omitted_from_its_payload() -> None:
    provider = FakeSemanticProvider(
        raw_output=_valid_semantic_output(
            cited_evidence_ids=["chunk-1", "chunk-unrelated"]
        )
    )
    request = _request(
        attempt=_attempt(answer="It crosses the threshold"),
        rubric=_semantic_rubric(),
        evidence=(UNRELATED_EVIDENCE, EVIDENCE),
    )
    decision = asyncio.run(EvaluatorV2(provider).evaluate(request))
    assert [
        item["evidence_id"] for item in provider.calls[0]["payload"]["evidence"]
    ] == ["chunk-1"]
    assert decision.result.outcome is EvaluationOutcome.INVALID_ATTEMPT
    assert decision.result.score is None
    assert decision.result.evidence_refs == ()
    assert decision.result.reason == "fabricated_citation"


def test_provider_score_outside_zero_one_is_rejected() -> None:
    provider = FakeSemanticProvider(raw_output=_valid_semantic_output(score=1.2))
    request = _request(
        attempt=_attempt(answer="It crosses the threshold"),
        rubric=_semantic_rubric(),
        evidence=(EVIDENCE,),
    )
    decision = asyncio.run(EvaluatorV2(provider).evaluate(request))
    assert decision.result.outcome is EvaluationOutcome.INVALID_ATTEMPT
    assert decision.result.score is None


def test_semantic_success_is_rubric_grounded_and_calls_provider_once() -> None:
    provider = FakeSemanticProvider(raw_output=_valid_semantic_output())
    request = _request(
        attempt=_attempt(answer="Resize occurs past capacity times load factor"),
        rubric=_semantic_rubric(),
        evidence=(UNRELATED_EVIDENCE, EVIDENCE),
    )
    decision = asyncio.run(EvaluatorV2(provider).evaluate(request))
    assert decision.result.outcome is EvaluationOutcome.CORRECT
    assert decision.result.score == 0.9
    assert decision.result.confidence == 0.84
    assert decision.result.evidence_refs == ("chunk-1",)
    assert decision.result.criterion_results[0].criterion_id == "threshold"
    assert decision.result.provider_calls == 1
    assert len(provider.calls) == 1
    payload = provider.calls[0]["payload"]
    assert payload["rubric_id"] == "semantic-rubric"
    assert [item["evidence_id"] for item in payload["evidence"]] == ["chunk-1"]
    assert provider.calls[0]["response_model"] is SemanticEvaluationDraft


def test_missing_semantic_port_returns_insufficient_evidence_without_a_call() -> None:
    request = _request(
        attempt=_attempt(answer="It crosses the threshold"),
        rubric=_semantic_rubric(),
        evidence=(EVIDENCE,),
    )
    decision = asyncio.run(EvaluatorV2().evaluate(request))
    assert decision.result.outcome is EvaluationOutcome.INSUFFICIENT_EVIDENCE
    assert decision.result.score is None
    assert decision.result.provider_calls == 0
    assert decision.result.reason == "semantic_provider_unavailable"


def test_provider_must_return_every_rubric_criterion() -> None:
    provider = FakeSemanticProvider(
        raw_output=_valid_semantic_output(criterion_results=[])
    )
    request = _request(
        attempt=_attempt(answer="It crosses the threshold"),
        rubric=_semantic_rubric(),
        evidence=(EVIDENCE,),
    )
    decision = asyncio.run(EvaluatorV2(provider).evaluate(request))
    assert decision.result.outcome is EvaluationOutcome.INVALID_ATTEMPT
    assert decision.result.score is None
    assert decision.result.reason == "provider_criterion_mismatch"


def test_provider_must_cite_required_grounding_evidence() -> None:
    provider = FakeSemanticProvider(
        raw_output=_valid_semantic_output(cited_evidence_ids=[])
    )
    request = _request(
        attempt=_attempt(answer="It crosses the threshold"),
        rubric=_semantic_rubric(),
        evidence=(EVIDENCE,),
    )
    decision = asyncio.run(EvaluatorV2(provider).evaluate(request))
    assert decision.result.outcome is EvaluationOutcome.INSUFFICIENT_EVIDENCE
    assert decision.result.score is None
    assert decision.result.reason == "provider_omitted_required_evidence"


def test_self_attested_mastery_never_proposes_independent_verification() -> None:
    attempt = _attempt(
        answer="12",
        student_move=StudentMove.LEARNER_SAYS_MASTERED,
        self_attested_mastery=True,
    )
    decision = EvaluatorV2().evaluate_objective(_request(attempt=attempt))
    assert decision.result.outcome is EvaluationOutcome.CORRECT
    assert ProposedLearningEventType.INDEPENDENT_VERIFICATION_SIGNAL not in (
        _event_types(decision)
    )


def test_result_is_explainable_and_has_no_state_write_fields() -> None:
    decision = EvaluatorV2().evaluate_objective(_request())
    assert decision.result.reason
    assert decision.result.rubric_signals
    forbidden = {
        "mastery",
        "mastery_state",
        "task",
        "task_status",
        "plan",
        "schedule",
        "state_update",
    }
    assert forbidden.isdisjoint(EvaluationResult.model_fields)
    assert forbidden.isdisjoint(EvaluatorDecision.model_fields)


def test_evaluator_has_one_semantic_port_call_site_and_no_state_dependency() -> None:
    source = inspect.getsource(evaluator_module)
    tree = ast.parse(source)
    provider_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "structured"
    ]
    assert len(provider_calls) == 1
    assert all(
        forbidden not in source
        for forbidden in (
            "active_teaching",
            "generic_knowledge_api",
            "provider_runtime",
            "SessionLocal",
            "StudentModel",
        )
    )
