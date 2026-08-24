from __future__ import annotations

import ast
import inspect
import json

import pytest
from pydantic import ValidationError

from app.teaching import (
    AssessmentState,
    CurrentTaskKind,
    DecisionReason,
    EvidenceRequirement,
    ExampleDepth,
    ExampleHistory,
    ExampleStrategy,
    HintDependency,
    LearnerContext,
    MasteryState,
    StudentMove,
    TeacherMove,
    TeachingBrief,
    plan_teaching,
)
import app.teaching.pedagogy as pedagogy_module


def _context(student_move: StudentMove = StudentMove.CONTINUE, **changes) -> LearnerContext:
    values = {
        "current_concept": "HashMap resize",
        "learning_goal": "Distinguish threshold from capacity",
        "learner_level": "intermediate",
        "known_prerequisites": ("hashing",),
        "mastery_state": MasteryState.IN_PROGRESS,
        "student_move": student_move,
        "evidence_available": True,
        "relevant_evidence_ids": ("chunk-1", "chunk-2"),
    }
    values.update(changes)
    return LearnerContext(**values)


def test_first_exposure_uses_explain() -> None:
    brief = plan_teaching(_context())
    assert brief.teaching_move is TeacherMove.EXPLAIN
    assert brief.reason is DecisionReason.FIRST_EXPOSURE


def test_confusion_after_explain_rotates_strategy_without_assessment() -> None:
    brief = plan_teaching(
        _context(
            StudentMove.CONFUSED,
            recent_teaching_moves=(TeacherMove.EXPLAIN,),
            assessment_state=AssessmentState.READY,
        )
    )
    assert brief.teaching_move is TeacherMove.SIMPLIFY
    assert brief.teaching_move is not TeacherMove.EXPLAIN
    assert brief.assessment_recommended is False


def test_explicit_example_request_selects_an_example_move() -> None:
    brief = plan_teaching(_context(StudentMove.ASK_EXAMPLE))
    assert brief.teaching_move is TeacherMove.CONCRETE_EXAMPLE
    assert brief.example_strategy is ExampleStrategy.REAL_WORLD
    assert brief.preferred_example_depth is ExampleDepth.LEVEL_1_REAL_WORLD


def test_failed_example_escalates_depth_and_avoids_old_example() -> None:
    brief = plan_teaching(
        _context(
            StudentMove.ASK_EXAMPLE,
            recent_examples=(
                ExampleHistory(
                    example_id="bank-transfer",
                    strategy=ExampleStrategy.REAL_WORLD,
                    depth=ExampleDepth.LEVEL_1_REAL_WORLD,
                    effective=False,
                ),
            ),
        )
    )
    assert brief.teaching_move is TeacherMove.WORKED_EXAMPLE
    assert brief.preferred_example_depth is ExampleDepth.LEVEL_2_CONCRETE_NUMERIC
    assert brief.avoid_example_ids == ("bank-transfer",)
    assert brief.reason is DecisionReason.EXAMPLE_DEPTH_ESCALATION


def test_ask_why_is_answered_directly_not_forced_back_to_student() -> None:
    brief = plan_teaching(_context(StudentMove.ASK_WHY))
    assert brief.teaching_move is TeacherMove.ANSWER_DIRECTLY
    assert brief.supporting_moves == (TeacherMove.DEEPEN,)
    assert TeacherMove.RETRIEVAL_PROMPT not in (brief.teaching_move, *brief.supporting_moves)


def test_known_misconception_is_targeted_instead_of_reteaching_everything() -> None:
    misconception = "resize only when size equals capacity"
    brief = plan_teaching(
        _context(
            StudentMove.INCORRECT_ANSWER_SIGNAL,
            known_misconceptions=(misconception,),
        )
    )
    assert brief.teaching_move is TeacherMove.CORRECT
    assert brief.supporting_moves == (TeacherMove.DIAGNOSE,)
    assert brief.target_misconception == misconception


def test_repeated_failure_with_gap_connects_prerequisite() -> None:
    brief = plan_teaching(
        _context(
            StudentMove.CONFUSED,
            missing_prerequisites=("load factor",),
            consecutive_failures=2,
        )
    )
    assert brief.teaching_move is TeacherMove.CONNECT_PREREQUISITE
    assert brief.reason is DecisionReason.PREREQUISITE_GAP


def test_correct_but_unverified_uses_practice_not_completion() -> None:
    context = _context(StudentMove.CORRECT_ANSWER_SIGNAL)
    brief = plan_teaching(context)
    assert brief.teaching_move in {
        TeacherMove.GUIDED_PRACTICE,
        TeacherMove.RETRIEVAL_PROMPT,
        TeacherMove.TRANSFER_CHALLENGE,
    }
    assert brief.transition_recommended is False
    assert context.mastery_state is MasteryState.IN_PROGRESS


@pytest.mark.parametrize(
    ("move", "reason"),
    [
        (StudentMove.LEARNER_SAYS_MASTERED, DecisionReason.LEARNER_MASTERED_FAST_LANE),
        (StudentMove.REQUEST_SKIP, DecisionReason.LEARNER_SKIP_FAST_LANE),
    ],
)
def test_mastered_and_skip_fast_lanes_recommend_transition_without_grading(
    move,
    reason,
) -> None:
    brief = plan_teaching(_context(move))
    assert brief.teaching_move is TeacherMove.TRANSITION
    assert brief.transition_recommended is True
    assert brief.assessment_recommended is False
    assert brief.reason is reason


def test_related_tangent_is_answered_and_learning_position_is_preserved() -> None:
    brief = plan_teaching(
        _context(
            StudentMove.RELATED_TANGENT,
            next_concept="ConcurrentHashMap",
            next_concept_relationship="concurrency limitation",
        )
    )
    assert brief.teaching_move is TeacherMove.ANSWER_DIRECTLY
    assert brief.preserve_current_learning_position is True
    assert brief.transition_recommended is False


def test_practical_project_request_uses_code_example() -> None:
    brief = plan_teaching(_context(StudentMove.REQUEST_PRACTICAL_USE))
    assert brief.teaching_move is TeacherMove.CODE_EXAMPLE
    assert brief.example_strategy is ExampleStrategy.CODE
    assert brief.preferred_example_depth is ExampleDepth.LEVEL_3_CODE


def test_complete_confusion_never_forces_assessment() -> None:
    brief = plan_teaching(
        _context(
            StudentMove.CONFUSED,
            consecutive_failures=5,
            assessment_state=AssessmentState.READY,
        )
    )
    assert brief.assessment_recommended is False
    assert brief.teaching_move in {
        TeacherMove.DIAGNOSE,
        TeacherMove.SIMPLIFY,
        TeacherMove.CONCRETE_EXAMPLE,
        TeacherMove.CODE_EXAMPLE,
        TeacherMove.ANALOGY,
    }


def test_explicit_test_me_request_recommends_assessment_but_has_no_grade() -> None:
    brief = plan_teaching(_context(StudentMove.REQUEST_ASSESSMENT))
    assert brief.teaching_move is TeacherMove.RETRIEVAL_PROMPT
    assert brief.assessment_recommended is True
    payload = brief.model_dump(mode="json")
    assert "score" not in payload
    assert "grade" not in payload
    assert "passed" not in payload


def test_three_repeated_moves_are_not_selected_again() -> None:
    brief = plan_teaching(
        _context(
            StudentMove.ASK_DEFINITION,
            recent_teaching_moves=(
                TeacherMove.EXPLAIN,
                TeacherMove.EXPLAIN,
                TeacherMove.EXPLAIN,
            ),
        )
    )
    assert brief.teaching_move is not TeacherMove.EXPLAIN
    assert brief.avoid_recent_moves == (TeacherMove.EXPLAIN,)


def test_transition_link_contains_only_supplied_relationship() -> None:
    brief = plan_teaching(
        _context(
            StudentMove.LEARNER_SAYS_MASTERED,
            next_concept="ConcurrentHashMap",
            next_concept_relationship="concurrency limitation and prerequisite progression",
        )
    )
    assert brief.transition_link is not None
    assert brief.transition_link.current_concept == "HashMap resize"
    assert brief.transition_link.next_concept == "ConcurrentHashMap"
    assert brief.transition_link.relationship == (
        "concurrency limitation and prerequisite progression"
    )


def test_output_contract_cannot_contain_composer_language_fields() -> None:
    forbidden = {
        "teacher_response",
        "full_explanation",
        "opening_sentence",
        "closing_sentence",
        "mandatory_phrase",
    }
    assert forbidden.isdisjoint(TeachingBrief.model_fields)
    with pytest.raises(ValidationError):
        TeachingBrief.model_validate(
            {
                **plan_teaching(_context()).model_dump(mode="json"),
                "teacher_response": "learner-visible response",
            }
        )


def test_planner_is_pure_and_does_not_mutate_mastery_task_plan_or_evidence() -> None:
    context = _context(
        StudentMove.INCORRECT_ANSWER_SIGNAL,
        current_task=CurrentTaskKind.PRACTICE,
        known_misconceptions=("threshold vs capacity",),
    )
    before = context.model_dump(mode="json")
    first = plan_teaching(context)
    second = plan_teaching(context)

    assert context.model_dump(mode="json") == before
    assert first == second
    payload = first.model_dump(mode="json")
    assert "mastery_state" not in payload
    assert "task_status" not in payload
    assert "plan" not in payload
    assert payload["relevant_evidence_ids"] == ["chunk-1", "chunk-2"]


def test_evidence_ids_must_be_caller_supplied_and_planner_never_invents_them() -> None:
    without_evidence = plan_teaching(
        _context(
            StudentMove.ASK_WHY,
            evidence_available=False,
            relevant_evidence_ids=(),
        )
    )
    assert without_evidence.evidence_requirement is EvidenceRequirement.REQUIRED
    assert without_evidence.relevant_evidence_ids == ()
    with pytest.raises(ValidationError):
        _context(evidence_available=False, relevant_evidence_ids=("invented",))


def test_independent_verification_and_self_attestation_remain_distinct() -> None:
    verified = plan_teaching(
        _context(
            StudentMove.CORRECT_ANSWER_SIGNAL,
            mastery_state=MasteryState.INDEPENDENTLY_VERIFIED,
        )
    )
    self_attested = plan_teaching(
        _context(
            StudentMove.LEARNER_SAYS_MASTERED,
            mastery_state=MasteryState.SELF_ATTESTED,
        )
    )
    assert verified.reason is DecisionReason.INDEPENDENTLY_VERIFIED
    assert self_attested.reason is DecisionReason.LEARNER_MASTERED_FAST_LANE
    assert verified.assessment_recommended is False
    assert self_attested.assessment_recommended is False


def test_planner_has_no_provider_database_or_composer_dependency() -> None:
    source = inspect.getsource(pedagogy_module)
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert imported_modules == {"__future__", "collections.abc", "contracts"}
    assert {"open", "print", "exec", "eval"}.isdisjoint(called_names)
    assert "provider_service" not in source
    assert "SessionLocal" not in source
    assert "TeacherTurnORM" not in source
    assert "teacher_response" not in source
    assert "free_chat" not in source
    assert "active_teaching" not in source
    json.dumps(plan_teaching(_context()).model_dump(mode="json"))


def test_all_declared_teacher_moves_are_bounded_and_serializable() -> None:
    expected = {
        "explain",
        "analogy",
        "worked_example",
        "concrete_example",
        "code_example",
        "compare",
        "counterexample",
        "hint",
        "diagnose",
        "correct",
        "simplify",
        "deepen",
        "connect_prerequisite",
        "connect_next_concept",
        "guided_practice",
        "retrieval_prompt",
        "transfer_challenge",
        "summarize",
        "transition",
        "answer_directly",
    }
    assert {item.value for item in TeacherMove} == expected


def test_only_decision_relevant_context_fields_change_the_brief() -> None:
    beginner = plan_teaching(_context(learner_level="beginner"))
    advanced = plan_teaching(_context(learner_level="advanced"))
    assert beginner.depth.value == "brief"
    assert advanced.depth.value == "deep"

    review = plan_teaching(_context(current_task=CurrentTaskKind.REVIEW))
    practice = plan_teaching(_context(current_task=CurrentTaskKind.PRACTICE))
    assert review.teaching_move is TeacherMove.RETRIEVAL_PROMPT
    assert practice.teaching_move is TeacherMove.GUIDED_PRACTICE


def test_repeated_mistakes_and_hint_dependency_change_remediation_strategy() -> None:
    repeated = plan_teaching(
        _context(
            StudentMove.INCORRECT_ANSWER_SIGNAL,
            recent_mistakes=("confused threshold with capacity",),
        )
    )
    hint_dependent = plan_teaching(
        _context(
            StudentMove.REQUEST_HINT,
            hint_dependency=HintDependency.PERSISTENT,
        )
    )
    assert repeated.reason is DecisionReason.REPEATED_MISTAKE_PATTERN
    assert repeated.supporting_moves == (TeacherMove.CORRECT,)
    assert hint_dependent.teaching_move is TeacherMove.DIAGNOSE
    assert hint_dependent.reason is DecisionReason.HINT_DEPENDENCY_GUARD


@pytest.mark.parametrize(
    "readiness",
    [
        {"mastery_state": MasteryState.READY_FOR_VERIFICATION},
        {"assessment_state": AssessmentState.READY},
        {"assessment_state": AssessmentState.REQUESTED},
        {"assessment_state": AssessmentState.IN_PROGRESS},
    ],
)
def test_ready_context_recommends_verification_instead_of_restarting_explanation(
    readiness,
) -> None:
    brief = plan_teaching(_context(**readiness))
    assert brief.teaching_move is TeacherMove.RETRIEVAL_PROMPT
    assert brief.assessment_recommended is True
    assert brief.reason is DecisionReason.CORRECT_BUT_UNVERIFIED


def test_maximum_example_depth_does_not_claim_a_further_escalation() -> None:
    brief = plan_teaching(
        _context(
            StudentMove.ASK_EXAMPLE,
            recent_examples=(
                ExampleHistory(
                    example_id="edge-case-transfer",
                    strategy=ExampleStrategy.EDGE_CASE_TRANSFER,
                    depth=ExampleDepth.LEVEL_5_EDGE_CASE_TRANSFER,
                    effective=False,
                ),
            ),
        )
    )
    assert brief.preferred_example_depth is ExampleDepth.LEVEL_5_EDGE_CASE_TRANSFER
    assert brief.reason is DecisionReason.EXPLICIT_EXAMPLE_REQUEST
