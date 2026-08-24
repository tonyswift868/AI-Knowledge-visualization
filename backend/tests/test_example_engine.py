from __future__ import annotations

import ast
import inspect

from app.teaching.contracts import (
    ExampleDepth,
    ExampleHistory,
    ExampleStrategy,
    LearnerContext,
    MasteryState,
    StudentMove,
    TeacherMove,
)
from app.teaching.examples import (
    ExamplePlan,
    ExamplePlanReason,
    ExampleRequest,
    ExampleType,
    example_fingerprint,
    plan_example,
)
import app.teaching.examples.engine as engine_module
from app.teaching.pedagogy import plan_teaching


def _request(
    learner_move: StudentMove = StudentMove.CONTINUE,
    *,
    desired_depth: ExampleDepth | None = None,
    requested_example_type: ExampleType | None = None,
    **changes,
) -> ExampleRequest:
    values = {
        "current_concept": "HashMap resize",
        "learning_goal": "Distinguish threshold from capacity",
        "learner_level": "intermediate",
        "known_prerequisites": ("hashing",),
        "mastery_state": MasteryState.IN_PROGRESS,
        "student_move": learner_move,
        "evidence_available": True,
        "relevant_evidence_ids": ("chunk-1", "chunk-2"),
    }
    values.update(changes)
    context = LearnerContext(**values)
    brief = plan_teaching(context)
    return ExampleRequest.from_teaching(
        context,
        brief,
        desired_depth=desired_depth,
        requested_example_type=requested_example_type,
    )


def test_beginner_gets_an_intuitive_level_one_example() -> None:
    plan = plan_example(_request(learner_level="beginner"))
    assert plan.example_type is ExampleType.ANALOGY
    assert plan.depth is ExampleDepth.LEVEL_1_REAL_WORLD


def test_intermediate_learner_gets_a_technical_code_example() -> None:
    plan = plan_example(_request(learner_level="intermediate"))
    assert plan.example_type is ExampleType.CODE
    assert plan.depth is ExampleDepth.LEVEL_3_CODE
    assert "assume_known_prerequisites:hashing" in plan.constraints


def test_failed_analogy_during_confusion_escalates_instead_of_repeating() -> None:
    plan = plan_example(
        _request(
            StudentMove.CONFUSED,
            recent_teaching_moves=(TeacherMove.ANALOGY,),
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
    assert plan.example_type is ExampleType.CONCRETE
    assert plan.reason is ExamplePlanReason.FAILED_STRATEGY_ESCALATION


def test_core_scenario_renames_share_a_fingerprint_and_are_avoided() -> None:
    assert example_fingerprint("工商银行转账") == example_fingerprint("建设银行转账")
    plan = plan_example(
        _request(
            recent_examples=(
                ExampleHistory(
                    example_id="工商银行转账",
                    strategy=ExampleStrategy.REAL_WORLD,
                    depth=ExampleDepth.LEVEL_1_REAL_WORLD,
                    effective=True,
                ),
            )
        )
    )
    assert "bank-transfer" in plan.avoid_semantic_families
    assert plan.semantic_family != "bank-transfer"


def test_known_misconception_is_the_only_target() -> None:
    misconception = "HashMap size == capacity 才 resize"
    plan = plan_example(
        _request(
            StudentMove.INCORRECT_ANSWER_SIGNAL,
            known_misconceptions=(misconception,),
        )
    )
    assert plan.example_type is ExampleType.COUNTEREXAMPLE
    assert plan.target_misconception == misconception
    assert plan.learning_objective == f"Correct misconception: {misconception}"
    assert f"target_only:{misconception}" in plan.constraints


def test_explicit_code_request_is_honored() -> None:
    plan = plan_example(
        _request(requested_example_type=ExampleType.CODE, learner_level="beginner")
    )
    assert plan.example_type is ExampleType.CODE
    assert plan.depth is ExampleDepth.LEVEL_3_CODE
    assert plan.reason is ExamplePlanReason.EXPLICIT_TYPE_REQUEST


def test_simplify_request_moves_to_level_one() -> None:
    plan = plan_example(
        _request(
            StudentMove.REQUEST_SIMPLIFY,
            learner_level="advanced",
            recent_teaching_moves=(TeacherMove.CODE_EXAMPLE,),
        )
    )
    assert plan.example_type is ExampleType.ANALOGY
    assert plan.depth is ExampleDepth.LEVEL_1_REAL_WORLD
    assert plan.reason is ExamplePlanReason.SIMPLIFY_REQUEST


def test_deeper_request_moves_beyond_the_previous_depth() -> None:
    plan = plan_example(
        _request(
            StudentMove.ASK_DEEPER_EXPLANATION,
            recent_examples=(
                ExampleHistory(
                    example_id="numeric-trace",
                    strategy=ExampleStrategy.CONCRETE_NUMERIC,
                    depth=ExampleDepth.LEVEL_2_CONCRETE_NUMERIC,
                    effective=True,
                ),
            ),
        )
    )
    assert plan.depth is ExampleDepth.LEVEL_3_CODE
    assert plan.reason is ExamplePlanReason.DEEPEN_REQUEST


def test_explicit_counterexample_request_selects_level_four() -> None:
    plan = plan_example(
        _request(requested_example_type=ExampleType.COUNTEREXAMPLE)
    )
    assert plan.example_type is ExampleType.COUNTEREXAMPLE
    assert plan.depth is ExampleDepth.LEVEL_4_COUNTEREXAMPLE


def test_explicit_transfer_request_selects_level_five() -> None:
    plan = plan_example(_request(requested_example_type=ExampleType.TRANSFER))
    assert plan.example_type is ExampleType.TRANSFER
    assert plan.depth is ExampleDepth.LEVEL_5_EDGE_CASE_TRANSFER


def test_prerequisite_gap_selects_a_bridge_before_more_complex_examples() -> None:
    plan = plan_example(
        _request(
            StudentMove.CONFUSED,
            missing_prerequisites=("load factor",),
            consecutive_failures=2,
        )
    )
    assert plan.example_type is ExampleType.PREREQUISITE
    assert plan.reason is ExamplePlanReason.PREREQUISITE_GAP
    assert "bridge_prerequisite:load factor" in plan.constraints


def test_evidence_refs_are_copied_and_never_invented() -> None:
    with_evidence = plan_example(_request())
    without_evidence = plan_example(
        _request(
            evidence_available=False,
            relevant_evidence_ids=(),
        )
    )
    assert with_evidence.evidence_refs == ("chunk-1", "chunk-2")
    assert without_evidence.evidence_refs == ()


def test_same_input_produces_a_stable_plan() -> None:
    request = _request(desired_depth=ExampleDepth.LEVEL_4_COUNTEREXAMPLE)
    assert plan_example(request) == plan_example(request)


def test_plan_has_no_complete_teacher_language_fields() -> None:
    forbidden = {
        "teacher_response",
        "full_explanation",
        "opening_sentence",
        "closing_sentence",
        "mandatory_phrase",
    }
    assert forbidden.isdisjoint(ExamplePlan.model_fields)
    assert "composer_intent_only:no_teacher_script" in plan_example(_request()).constraints


def test_engine_is_pure_and_does_not_modify_mastery_task_or_plan() -> None:
    request = _request()
    before = request.model_dump(mode="json")
    result = plan_example(request)
    assert request.model_dump(mode="json") == before
    assert {"mastery_state", "task_status", "plan"}.isdisjoint(ExamplePlan.model_fields)


def test_engine_has_no_provider_database_or_runtime_dependency() -> None:
    source = inspect.getsource(engine_module)
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
    assert imported_modules == {
        "__future__",
        "collections.abc",
        "contracts",
        "models",
    }
    assert {"open", "print", "exec", "eval"}.isdisjoint(called_names)
    assert "provider_service" not in source
    assert "instructor" not in source
    assert "SessionLocal" not in source
    assert "active_teaching" not in source
