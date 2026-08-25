from __future__ import annotations

import asyncio

import pytest

from app.teaching.composer import ConversationTurn, FollowUpKind, TeacherEvidence
from app.teaching.contracts import (
    AssessmentState,
    LearnerContext,
    MasteryState,
    StudentMove,
)
from app.teaching.fastpath_v2 import (
    ContextBudgetPlanner,
    ContextSelectionReason,
    FakeStreamingTeacherProvider,
    TeacherContextBudget,
    TeacherContextBudgetError,
    TeacherLatencyBudget,
    TeacherResponseChunk,
    TeacherResponseLengthPolicy,
    TeacherResponseLengthReason,
    TeacherResponseMode,
    run_streaming_teacher_turn,
)
from app.teaching.pedagogy import plan_teaching
from app.teaching.runtime_v2 import TeacherRuntimeError


def _evidence(size: int = 512) -> TeacherEvidence:
    return TeacherEvidence(
        evidence_id="required-evidence",
        content="E" * size,
        source_reference="fixture://context-budget/required",
    )


def _context(**changes) -> LearnerContext:
    values = {
        "current_concept": "HashMap threshold",
        "learning_goal": "Distinguish threshold from capacity",
        "learner_level": "intermediate",
        "known_prerequisites": tuple(f"pre-{item}" for item in range(12)),
        "missing_prerequisites": ("amortized analysis",),
        "mastery_state": MasteryState.IN_PROGRESS,
        "student_move": StudentMove.ASK_WHY,
        "recent_mistakes": tuple(f"mistake-{item}" for item in range(8)),
        "known_misconceptions": ("threshold equals capacity",),
        "evidence_available": True,
        "relevant_evidence_ids": ("required-evidence",),
        "assessment_state": AssessmentState.READY,
        "consecutive_failures": 3,
    }
    values.update(changes)
    return LearnerContext(**values)


def _length_policy() -> TeacherResponseLengthPolicy:
    return TeacherResponseLengthPolicy(
        fast_clarification_target_tokens=80,
        fast_why_target_tokens=160,
        fast_example_target_tokens=220,
        fast_default_target_tokens=140,
        fast_maximum_output_tokens=320,
        deep_target_tokens=600,
        deep_maximum_output_tokens=1_200,
    )


def _latency_budget() -> TeacherLatencyBudget:
    return TeacherLatencyBudget(
        budget_id="context-runtime-fixture",
        fast_ttft_expected_ms=1_000,
        fast_total_expected_ms=5_000,
        deep_ttft_expected_ms=2_000,
        deep_total_expected_ms=10_000,
        timeout_risk_ms=20_000,
    )


def _plan(
    *,
    budget: TeacherContextBudget,
    context: LearnerContext | None = None,
    evidence: tuple[TeacherEvidence, ...] | None = None,
    history: tuple[ConversationTurn, ...] = (),
):
    learner_context = context or _context()
    selected_evidence = evidence if evidence is not None else (_evidence(),)
    return ContextBudgetPlanner().plan(
        budget=budget,
        teaching_brief=plan_teaching(learner_context),
        learner_context=learner_context,
        latest_user_message="为什么 threshold 不是 capacity？",
        evidence=selected_evidence,
        assessment_state=AssessmentState.READY,
        response_mode=TeacherResponseMode.FAST,
        response_length=_length_policy().resolve(
            response_mode=TeacherResponseMode.FAST,
            student_move=StudentMove.ASK_WHY,
        ),
        example_plan=None,
        recent_conversation=history,
    )


def test_budget_keeps_question_concept_and_required_evidence_before_optional_context() -> None:
    minimal_context = _context(
        learning_goal="",
        learner_level="",
        known_prerequisites=(),
        missing_prerequisites=(),
        recent_mistakes=(),
        known_misconceptions=(),
        consecutive_failures=0,
    )
    large_budget = TeacherContextBudget(
        budget_id="measure-minimum",
        maximum_prompt_tokens=20_000,
        maximum_evidence_tokens=10_000,
        maximum_history_tokens=5_000,
    )
    minimum = _plan(budget=large_budget, context=minimal_context)
    tight_maximum = minimum.receipt.input_estimated_tokens + 20
    tight_budget = TeacherContextBudget(
        budget_id="tight-priority-budget",
        maximum_prompt_tokens=tight_maximum,
        maximum_evidence_tokens=tight_maximum,
        maximum_history_tokens=tight_maximum,
    )
    history = tuple(
        ConversationTurn(role="user", content=f"optional history {item} " * 20)
        for item in range(8)
    )

    plan = _plan(budget=tight_budget, history=history)

    assert plan.receipt.input_estimated_tokens <= tight_budget.maximum_prompt_tokens
    assert plan.receipt.selected_evidence_ids == ("required-evidence",)
    assert plan.receipt.evidence_tokens > 0
    assert plan.receipt.stable_prefix_tokens + plan.receipt.dynamic_tokens == plan.receipt.input_estimated_tokens
    assert plan.receipt.reason is ContextSelectionReason.OPTIONAL_CONTEXT_DROPPED
    assert plan.receipt.dropped_optional_context
    assert "HashMap threshold" in plan.envelope.dynamic_tail.content
    assert "为什么 threshold" in plan.envelope.dynamic_tail.content
    assert _evidence().content in plan.envelope.dynamic_tail.content


def test_recent_history_is_selected_newest_first_with_its_own_budget() -> None:
    history = (
        ConversationTurn(role="user", content="oldest turn"),
        ConversationTurn(role="assistant", content="middle turn"),
        ConversationTurn(role="user", content="newest turn"),
    )
    budget = TeacherContextBudget(
        budget_id="history-priority",
        maximum_prompt_tokens=20_000,
        maximum_evidence_tokens=10_000,
        maximum_history_tokens=20,
    )

    plan = _plan(budget=budget, history=history)

    assert plan.selected_history
    assert plan.selected_history[-1].content == "newest turn"
    assert plan.receipt.selected_history_count == len(plan.selected_history)
    assert plan.receipt.history_tokens <= budget.maximum_history_tokens


def test_oversized_required_evidence_is_never_silently_dropped() -> None:
    budget = TeacherContextBudget(
        budget_id="required-evidence-overflow",
        maximum_prompt_tokens=2_000,
        maximum_evidence_tokens=10,
        maximum_history_tokens=0,
    )

    with pytest.raises(
        TeacherContextBudgetError,
        match="REQUIRED_EVIDENCE_EXCEEDS_CONTEXT_BUDGET",
    ):
        _plan(budget=budget, evidence=(_evidence(8_000),))


def test_minimum_prompt_overflow_fails_closed() -> None:
    budget = TeacherContextBudget(
        budget_id="minimum-overflow",
        maximum_prompt_tokens=50,
        maximum_evidence_tokens=50,
        maximum_history_tokens=0,
    )

    with pytest.raises(TeacherContextBudgetError, match="CONTEXT_BUDGET_OVERFLOW"):
        _plan(
            budget=budget,
            context=_context(
                evidence_available=False,
                relevant_evidence_ids=(),
                student_move=StudentMove.CONTINUE,
            ),
            evidence=(),
        )


def test_fast_response_length_policy_is_shorter_than_deep_without_summary_call() -> None:
    policy = _length_policy()
    clarification = policy.resolve(
        response_mode=TeacherResponseMode.FAST,
        student_move=StudentMove.REQUEST_SIMPLIFY,
    )
    why = policy.resolve(
        response_mode=TeacherResponseMode.FAST,
        student_move=StudentMove.ASK_WHY,
    )
    example = policy.resolve(
        response_mode=TeacherResponseMode.FAST,
        student_move=StudentMove.ASK_EXAMPLE,
    )
    deep = policy.resolve(
        response_mode=TeacherResponseMode.DEEP,
        student_move=StudentMove.ASK_DEEPER_EXPLANATION,
    )

    assert clarification.reason is TeacherResponseLengthReason.FAST_CLARIFICATION
    assert why.reason is TeacherResponseLengthReason.FAST_WHY
    assert example.reason is TeacherResponseLengthReason.FAST_EXAMPLE
    assert max(
        clarification.maximum_output_tokens,
        why.maximum_output_tokens,
        example.maximum_output_tokens,
    ) < deep.maximum_output_tokens


def test_oversized_fast_output_fails_without_a_second_provider_call() -> None:
    chunks = (
        TeacherResponseChunk(sequence_number=0, text_delta="X" * 4_000),
        TeacherResponseChunk(
            sequence_number=1,
            is_final=True,
            cited_evidence_ids=("required-evidence",),
            follow_up_kind=FollowUpKind.NONE,
        ),
    )
    provider = FakeStreamingTeacherProvider(chunks=chunks)
    policy = TeacherResponseLengthPolicy(
        fast_clarification_target_tokens=10,
        fast_why_target_tokens=10,
        fast_example_target_tokens=10,
        fast_default_target_tokens=10,
        fast_maximum_output_tokens=20,
        deep_target_tokens=40,
        deep_maximum_output_tokens=80,
    )

    with pytest.raises(TeacherRuntimeError, match="TEACHER_STREAM_INVALID"):
        asyncio.run(
            run_streaming_teacher_turn(
                latest_user_message="为什么 threshold 不是 capacity？",
                learner_context=_context(),
                provider=provider,
                latency_budget=_latency_budget(),
                context_budget=TeacherContextBudget(
                    budget_id="output-length-runtime",
                    maximum_prompt_tokens=8_000,
                    maximum_evidence_tokens=4_000,
                    maximum_history_tokens=1_000,
                ),
                response_length_policy=policy,
                evidence=(_evidence(),),
            )
        )

    assert provider.call_count == 1
