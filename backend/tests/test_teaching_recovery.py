from __future__ import annotations

import ast
import inspect

import pytest

from app.teaching.contracts import ExampleDepth, HintDependency, StudentMove, TeacherMove
from app.teaching.examples import ExamplePlan, ExamplePlanReason, ExampleType
from app.teaching.recovery import (
    MAX_RECOVERY_ATTEMPTS,
    AnswerOutcome,
    AnswerResult,
    DiagnosisCategory,
    ErrorKindHint,
    MisconceptionDiagnosis,
    RecoveryPlan,
    RecoveryStrategy,
    TeachingRecoveryEngine,
    TeachingRecoveryRequest,
    plan_teaching_recovery,
)
import app.teaching.recovery.engine as recovery_module


def _request(**changes) -> TeachingRecoveryRequest:
    values = {
        "student_move": StudentMove.INCORRECT_ANSWER_SIGNAL,
        "current_concept": "HashMap resize",
        "learner_level": "intermediate",
    }
    values.update(changes)
    return TeachingRecoveryRequest(**values)


def _outcome(
    result: AnswerResult,
    signature: str = "",
    *,
    kind: ErrorKindHint | None = None,
    evidence_refs: tuple[str, ...] = (),
) -> AnswerOutcome:
    return AnswerOutcome(
        result=result,
        error_signature=signature,
        error_kind_hint=kind,
        evidence_refs=evidence_refs,
    )


def _example_plan(example_type: ExampleType, depth: ExampleDepth) -> ExamplePlan:
    return ExamplePlan(
        example_type=example_type,
        depth=depth,
        learning_objective="Distinguish threshold from capacity",
        focus_concept="HashMap resize",
        constraints=("composer_intent_only:no_teacher_script",),
        semantic_family=f"{example_type.value}-family",
        reason=ExamplePlanReason.LEARNER_LEVEL,
    )


def test_one_incorrect_answer_stays_low_evidence_instead_of_misdiagnosing() -> None:
    decision = plan_teaching_recovery(
        _request(
            recent_answer_outcomes=(
                _outcome(
                    AnswerResult.INCORRECT,
                    "threshold-equals-capacity",
                    evidence_refs=("answer-1",),
                ),
            )
        )
    )
    assert decision.diagnosis.category is DiagnosisCategory.LOW_EVIDENCE
    assert decision.diagnosis.focus_concept == "HashMap resize"
    assert decision.diagnosis.confidence <= 0.3
    assert decision.diagnosis.evidence_refs == ("answer-1",)
    assert decision.recovery.strategy is RecoveryStrategy.DELAY_ASSESSMENT
    assert decision.recovery.assessment_deferred is True


def test_incorrect_signal_without_outcome_detail_remains_low_evidence() -> None:
    decision = plan_teaching_recovery(_request(recent_answer_outcomes=()))
    assert decision.diagnosis.category is DiagnosisCategory.LOW_EVIDENCE
    assert decision.diagnosis.confidence == 0.3


def test_known_misconception_hint_alone_cannot_force_a_diagnosis() -> None:
    decision = plan_teaching_recovery(
        _request(
            student_move=StudentMove.CONTINUE,
            recent_answer_outcomes=(),
            known_misconception_hints=("threshold equals capacity",),
        )
    )
    assert decision.diagnosis.category is DiagnosisCategory.UNKNOWN
    assert decision.diagnosis.confidence == 0.0


def test_repeated_same_error_becomes_a_specific_misconception() -> None:
    misconception = "resize happens only when size equals capacity"
    decision = plan_teaching_recovery(
        _request(
            recent_answer_outcomes=(
                _outcome(AnswerResult.INCORRECT, "threshold-equals-capacity"),
                _outcome(AnswerResult.INCORRECT, "threshold-equals-capacity"),
            ),
            known_misconception_hints=(misconception,),
        )
    )
    assert decision.diagnosis.category is DiagnosisCategory.SPECIFIC_MISCONCEPTION
    assert decision.diagnosis.target_misconception == misconception
    assert decision.diagnosis.confidence >= 0.8
    assert decision.recovery.strategy is RecoveryStrategy.TARGETED_CORRECTION
    assert decision.recovery.recommended_teaching_move is TeacherMove.CORRECT
    assert decision.recovery.recommended_example_type is ExampleType.COUNTEREXAMPLE


def test_confusion_after_same_strategy_repeats_escalates_to_a_new_route() -> None:
    analogy = _example_plan(ExampleType.ANALOGY, ExampleDepth.LEVEL_1_REAL_WORLD)
    decision = plan_teaching_recovery(
        _request(
            student_move=StudentMove.CONFUSED,
            recent_teaching_moves=(TeacherMove.ANALOGY, TeacherMove.ANALOGY),
            example_plan_history=(analogy, analogy),
        )
    )
    assert decision.diagnosis.category is DiagnosisCategory.CONFUSION_UNRESOLVED
    assert decision.recovery.strategy is RecoveryStrategy.CONCRETE_EXAMPLE
    assert decision.recovery.recommended_example_type is ExampleType.CONCRETE
    assert decision.recovery.recommended_example_depth is (
        ExampleDepth.LEVEL_2_CONCRETE_NUMERIC
    )
    assert decision.recovery.trace.strategy_escalation_count == 1


def test_prerequisite_hint_with_failure_uses_prerequisite_fallback() -> None:
    decision = plan_teaching_recovery(
        _request(
            student_move=StudentMove.CONFUSED,
            recent_answer_outcomes=(_outcome(AnswerResult.INCORRECT, "load-factor"),),
            prerequisite_hints=("load factor",),
        )
    )
    assert decision.diagnosis.category is DiagnosisCategory.PREREQUISITE_GAP
    assert decision.recovery.strategy is RecoveryStrategy.CONNECT_PREREQUISITE
    assert decision.recovery.prerequisite_fallback == "load factor"
    assert decision.recovery.recommended_teaching_move is (
        TeacherMove.CONNECT_PREREQUISITE
    )


def test_persistent_hint_dependency_switches_to_guided_practice() -> None:
    decision = plan_teaching_recovery(
        _request(
            recent_answer_outcomes=(_outcome(AnswerResult.PARTIAL, "needs-hint"),),
            hint_dependency=HintDependency.PERSISTENT,
        )
    )
    assert decision.diagnosis.category is DiagnosisCategory.KNOWLEDGE_GAP
    assert decision.recovery.strategy is RecoveryStrategy.GUIDED_PRACTICE
    assert decision.recovery.recommended_teaching_move is TeacherMove.GUIDED_PRACTICE
    assert decision.recovery.assessment_deferred is True


def test_challenging_the_teacher_is_not_treated_as_a_wrong_answer() -> None:
    decision = plan_teaching_recovery(
        _request(
            student_move=StudentMove.CHALLENGE_TEACHER,
            recent_answer_outcomes=(),
        )
    )
    assert decision.diagnosis.category is DiagnosisCategory.UNKNOWN
    assert decision.diagnosis.confidence == 0.05
    assert decision.recovery.recommended_teaching_move is TeacherMove.ANSWER_DIRECTLY
    assert decision.recovery.assessment_deferred is False


def test_latest_correct_answer_clears_prior_high_confidence_hypothesis() -> None:
    request = _request(
        student_move=StudentMove.CORRECT_ANSWER_SIGNAL,
        recent_answer_outcomes=(
            _outcome(AnswerResult.INCORRECT, "threshold-equals-capacity"),
            _outcome(AnswerResult.INCORRECT, "threshold-equals-capacity"),
            _outcome(AnswerResult.CORRECT),
        ),
        known_misconception_hints=("threshold equals capacity",),
    )
    before = request.model_dump(mode="json")
    decision = plan_teaching_recovery(request)
    assert decision.diagnosis.category is DiagnosisCategory.UNKNOWN
    assert decision.diagnosis.confidence == 0.1
    assert decision.diagnosis.target_misconception is None
    assert request.model_dump(mode="json") == before
    assert {"mastery_state", "task_status", "plan"}.isdisjoint(
        MisconceptionDiagnosis.model_fields
    )
    assert {"mastery_state", "task_status"}.isdisjoint(RecoveryPlan.model_fields)


def test_assessment_is_recommended_only_after_two_consecutive_correct_outcomes() -> None:
    decision = plan_teaching_recovery(
        _request(
            student_move=StudentMove.CORRECT_ANSWER_SIGNAL,
            recent_answer_outcomes=(
                _outcome(AnswerResult.CORRECT),
                _outcome(AnswerResult.CORRECT),
            ),
        )
    )
    assert decision.recovery.assessment_recommended is True
    assert decision.recovery.assessment_deferred is False
    assert decision.recovery.recommended_teaching_move is TeacherMove.RETRIEVAL_PROMPT


def test_recovery_attempts_stop_at_the_bounded_maximum() -> None:
    decision = plan_teaching_recovery(
        _request(
            student_move=StudentMove.CONFUSED,
            recovery_attempts=MAX_RECOVERY_ATTEMPTS,
            recent_teaching_moves=(TeacherMove.CODE_EXAMPLE, TeacherMove.CODE_EXAMPLE),
        )
    )
    assert decision.recovery.attempt_number == MAX_RECOVERY_ATTEMPTS
    assert decision.recovery.attempts_exhausted is True
    assert decision.recovery.strategy is RecoveryStrategy.REVIEW
    assert decision.recovery.recommended_example_type is None
    assert decision.recovery.assessment_deferred is True


def test_same_input_produces_the_same_diagnosis_recovery_and_trace() -> None:
    request = _request(
        recent_answer_outcomes=(
            _outcome(AnswerResult.INCORRECT, "wrong-step"),
            _outcome(AnswerResult.INCORRECT, "wrong-step"),
        )
    )
    engine = TeachingRecoveryEngine()
    assert engine.evaluate(request) == engine.evaluate(request)


@pytest.mark.parametrize(
    ("hint", "category"),
    [
        (ErrorKindHint.PROCEDURAL, DiagnosisCategory.PROCEDURAL_ERROR),
        (ErrorKindHint.CARELESS, DiagnosisCategory.CARELESS_ERROR),
    ],
)
def test_procedural_and_careless_error_hints_remain_distinct(hint, category) -> None:
    decision = plan_teaching_recovery(
        _request(
            recent_answer_outcomes=(
                _outcome(AnswerResult.INCORRECT, "first", kind=hint),
                _outcome(AnswerResult.INCORRECT, "second", kind=hint),
            )
        )
    )
    assert decision.diagnosis.category is category


def test_different_repeated_failures_indicate_a_general_knowledge_gap() -> None:
    decision = plan_teaching_recovery(
        _request(
            recent_answer_outcomes=(
                _outcome(AnswerResult.INCORRECT, "wrong-threshold"),
                _outcome(AnswerResult.PARTIAL, "wrong-load-factor"),
            )
        )
    )
    assert decision.diagnosis.category is DiagnosisCategory.KNOWLEDGE_GAP
    assert decision.recovery.strategy is RecoveryStrategy.CONCRETE_EXAMPLE


def test_trace_records_signals_rejected_alternatives_and_recovery_reason() -> None:
    decision = plan_teaching_recovery(
        _request(
            recent_answer_outcomes=(
                _outcome(AnswerResult.INCORRECT, "threshold-capacity"),
                _outcome(AnswerResult.INCORRECT, "threshold-capacity"),
            )
        )
    )
    trace = decision.recovery.trace
    assert trace.selected_diagnosis is DiagnosisCategory.SPECIFIC_MISCONCEPTION
    assert trace.observed_signals
    assert trace.rejected_alternatives
    assert trace.recovery_reason == decision.recovery.reason


def test_engine_has_no_provider_io_database_or_composer_dependency() -> None:
    source = inspect.getsource(recovery_module)
    tree = ast.parse(source)
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"open", "print", "exec", "eval"}.isdisjoint(called_names)
    assert "provider_service" not in source
    assert "SessionLocal" not in source
    assert "active_teaching" not in source
    assert "composer" not in source.casefold()
    assert TeachingRecoveryEngine().provider_calls == 0


def test_output_contracts_cannot_contain_teacher_language_or_state_commands() -> None:
    forbidden = {
        "teacher_response",
        "full_explanation",
        "opening_sentence",
        "mastery_state",
        "task_status",
        "learning_plan",
    }
    assert forbidden.isdisjoint(MisconceptionDiagnosis.model_fields)
    assert forbidden.isdisjoint(RecoveryPlan.model_fields)
