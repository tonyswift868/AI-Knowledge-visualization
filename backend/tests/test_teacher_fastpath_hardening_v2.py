from __future__ import annotations

import asyncio

import pytest

from app.teaching.composer import FollowUpKind, TeacherEvidence
from app.teaching.contracts import AssessmentState, LearnerContext, MasteryState, StudentMove
from app.teaching.fastpath_v2 import (
    FakeStreamingTeacherProvider,
    TeacherContextBudget,
    TeacherDegradationAction,
    TeacherLatencyBudget,
    TeacherLatencyClassification,
    TeacherResponseChunk,
    TeacherResponseLengthPolicy,
    run_streaming_teacher_turn,
)
from app.teaching.runtime_v2 import TeacherRuntimeError


EVIDENCE = TeacherEvidence(
    evidence_id="hardening-evidence",
    content="Threshold is capacity multiplied by load factor.",
    source_reference="fixture://hardening/threshold",
)


def _context() -> LearnerContext:
    return LearnerContext(
        current_concept="HashMap threshold",
        learning_goal="Understand resize triggers",
        learner_level="intermediate",
        mastery_state=MasteryState.IN_PROGRESS,
        student_move=StudentMove.ASK_WHY,
        evidence_available=True,
        relevant_evidence_ids=(EVIDENCE.evidence_id,),
        assessment_state=AssessmentState.READY,
    )


def _latency_budget() -> TeacherLatencyBudget:
    return TeacherLatencyBudget(
        budget_id="hardening-latency",
        fast_ttft_expected_ms=1,
        fast_total_expected_ms=5_000,
        deep_ttft_expected_ms=2_000,
        deep_total_expected_ms=10_000,
        timeout_risk_ms=20_000,
    )


def _context_budget() -> TeacherContextBudget:
    return TeacherContextBudget(
        budget_id="hardening-context",
        maximum_prompt_tokens=8_000,
        maximum_evidence_tokens=4_000,
        maximum_history_tokens=1_000,
    )


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


def _run(provider):
    return asyncio.run(
        run_streaming_teacher_turn(
            latest_user_message="为什么会扩容？",
            learner_context=_context(),
            provider=provider,
            latency_budget=_latency_budget(),
            context_budget=_context_budget(),
            response_length_policy=_length_policy(),
            evidence=(EVIDENCE,),
            explicit_action=StudentMove.ASK_WHY,
        )
    )


@pytest.mark.parametrize(
    "chunks",
    [
        ({"sequence_number": "malformed"},),
        (
            TeacherResponseChunk(sequence_number=0, text_delta="   "),
            TeacherResponseChunk(
                sequence_number=1,
                is_final=True,
                cited_evidence_ids=(EVIDENCE.evidence_id,),
                follow_up_kind=FollowUpKind.NONE,
            ),
        ),
        (
            TeacherResponseChunk(sequence_number=0, text_delta="uncited answer"),
            TeacherResponseChunk(
                sequence_number=1,
                is_final=True,
                follow_up_kind=FollowUpKind.NONE,
            ),
        ),
    ],
    ids=("malformed_chunk", "empty_output", "missing_required_citation"),
)
def test_bad_streams_fail_closed_without_retry(chunks) -> None:
    provider = FakeStreamingTeacherProvider(chunks=chunks)

    with pytest.raises(TeacherRuntimeError, match="TEACHER_STREAM_INVALID") as captured:
        _run(provider)

    assert provider.call_count == 1
    assert captured.value.trace is not None
    receipt = captured.value.trace.stream_receipt
    assert receipt is not None and receipt.execution_trace is not None
    assert (
        receipt.execution_trace.degradation_decision.action
        is TeacherDegradationAction.DISCARD_INVALID_STREAM
    )
    assert receipt.execution_trace.degradation_decision.automatic_additional_provider_calls == 0


def test_late_first_chunk_marks_fast_slow_and_continues_the_same_stream() -> None:
    provider = FakeStreamingTeacherProvider(first_chunk_delay_seconds=0.01)

    result = _run(provider)
    receipt = result.runtime_trace.stream_receipt

    assert receipt is not None and receipt.execution_trace is not None
    assert receipt.time_to_first_chunk_ms is not None
    assert receipt.time_to_first_chunk_ms > _latency_budget().fast_ttft_expected_ms
    assert receipt.latency_classification is TeacherLatencyClassification.FAST_SLOW
    assert (
        receipt.execution_trace.degradation_decision.action
        is TeacherDegradationAction.CONTINUE_CURRENT_STREAM
    )
    assert receipt.execution_trace.degradation_decision.continue_current_stream is True
    assert receipt.execution_trace.degradation_decision.automatic_additional_provider_calls == 0
    assert provider.call_count == 1
