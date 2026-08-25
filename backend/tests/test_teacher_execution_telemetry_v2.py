from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.teaching.composer import TeacherEvidence
from app.teaching.contracts import AssessmentState, LearnerContext, MasteryState, StudentMove
from app.teaching.fastpath_v2 import (
    FakeStreamingTeacherProvider,
    TeacherCachedInputMetadata,
    TeacherCachedInputStatus,
    TeacherContextBudget,
    TeacherDegradationAction,
    TeacherDegradationPolicy,
    TeacherLatencyBudget,
    TeacherLatencyClassification,
    TeacherResponseChunk,
    TeacherResponseLengthPolicy,
    TeacherResponseMode,
    TeacherRuntimeCancelledError,
    TeacherStreamStatus,
    run_streaming_teacher_turn,
)
from app.teaching.runtime_v2 import TeacherRuntimeError


EVIDENCE = TeacherEvidence(
    evidence_id="telemetry-evidence",
    content="Threshold equals capacity multiplied by load factor.",
    source_reference="fixture://telemetry/threshold",
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
        budget_id="telemetry-latency-fixture",
        fast_ttft_expected_ms=1_000,
        fast_total_expected_ms=5_000,
        deep_ttft_expected_ms=2_000,
        deep_total_expected_ms=10_000,
        timeout_risk_ms=20_000,
    )


def _context_budget() -> TeacherContextBudget:
    return TeacherContextBudget(
        budget_id="telemetry-context-fixture",
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


async def _run(provider):
    return await run_streaming_teacher_turn(
        latest_user_message="为什么 threshold 不是 capacity？",
        learner_context=_context(),
        provider=provider,
        latency_budget=_latency_budget(),
        context_budget=_context_budget(),
        response_length_policy=_length_policy(),
        evidence=(EVIDENCE,),
    )


def test_execution_trace_carries_budget_timing_and_sanitized_cache_signal() -> None:
    provider = FakeStreamingTeacherProvider(
        cached_input_metadata=TeacherCachedInputMetadata(
            metadata_available=True,
            status=TeacherCachedInputStatus.HIT,
            cached_input_tokens=321,
        )
    )

    result = asyncio.run(_run(provider))
    receipt = result.runtime_trace.stream_receipt

    assert receipt is not None
    trace = receipt.execution_trace
    assert trace is not None
    assert trace.mode == TeacherResponseMode.FAST.value
    assert trace.route == "teacher-fastpath-v2-stream"
    assert trace.prompt_identity == receipt.prompt_identity
    assert trace.stable_prefix_hash == receipt.stable_prefix_hash
    assert trace.context_budget_id == _context_budget().budget_id
    assert trace.estimated_prompt_tokens == trace.stable_prefix_tokens + trace.dynamic_tokens
    assert trace.provider_start_offset_ms <= trace.total_latency_ms
    assert trace.first_chunk_offset_ms is not None
    assert trace.last_chunk_offset_ms is not None
    assert trace.first_chunk_offset_ms <= trace.last_chunk_offset_ms
    assert trace.time_to_first_chunk_ms == receipt.time_to_first_chunk_ms
    assert trace.provider_calls == provider.call_count == 1
    assert trace.cancelled is False
    assert trace.timed_out is False
    assert trace.cached_input_metadata.status is TeacherCachedInputStatus.HIT
    assert trace.cached_input_metadata.cached_input_tokens == 321
    assert "key" not in trace.model_dump_json().casefold()


def test_invalid_cache_metadata_is_dropped_without_leaking_raw_key() -> None:
    provider = FakeStreamingTeacherProvider(
        cached_input_metadata={
            "metadata_available": True,
            "status": "hit",
            "cached_input_tokens": 12,
            "raw_key": "must-not-appear",
        }
    )

    result = asyncio.run(_run(provider))
    trace = result.runtime_trace.stream_receipt.execution_trace

    assert trace is not None
    assert trace.cached_input_metadata == TeacherCachedInputMetadata()
    serialized = trace.model_dump_json()
    assert "must-not-appear" not in serialized
    assert "raw_key" not in serialized


def test_timeout_decision_requires_caller_retry_and_never_adds_a_call() -> None:
    provider = FakeStreamingTeacherProvider(error=TimeoutError("offline timeout"))

    with pytest.raises(TeacherRuntimeError, match="TEACHER_STREAM_TIMEOUT") as captured:
        asyncio.run(_run(provider))

    receipt = captured.value.trace.stream_receipt
    assert receipt is not None
    trace = receipt.execution_trace
    assert trace is not None
    assert trace.timed_out is True
    assert trace.cancelled is False
    assert trace.degradation_decision.action is TeacherDegradationAction.TIMEOUT
    assert trace.degradation_decision.retry_requires_caller_decision is True
    assert trace.degradation_decision.automatic_additional_provider_calls == 0
    assert provider.call_count == 1


def test_slow_fast_policy_continues_current_stream_without_fallback() -> None:
    decision = TeacherDegradationPolicy().decide(
        response_mode=TeacherResponseMode.FAST,
        status=TeacherStreamStatus.COMPLETED,
        latency_classification=TeacherLatencyClassification.FAST_SLOW,
    )

    assert decision.action is TeacherDegradationAction.CONTINUE_CURRENT_STREAM
    assert decision.continue_current_stream is True
    assert decision.retry_requires_caller_decision is False
    assert decision.automatic_additional_provider_calls == 0


class _BlockingProvider:
    provider_name = "offline-blocking-fixture"
    model = "fixture"

    def __init__(self) -> None:
        self.call_count = 0
        self.started = asyncio.Event()

    async def stream(
        self,
        envelope,
        *,
        response_mode: TeacherResponseMode,
        temperature: float = 0.2,
    ) -> AsyncIterator[TeacherResponseChunk]:
        self.call_count += 1
        self.started.set()
        await asyncio.Event().wait()
        yield TeacherResponseChunk(sequence_number=0, text_delta="unreachable")


def test_cancellation_preserves_trace_and_cancellation_semantics() -> None:
    async def scenario():
        provider = _BlockingProvider()
        task = asyncio.create_task(_run(provider))
        await provider.started.wait()
        task.cancel()
        with pytest.raises(TeacherRuntimeCancelledError) as captured:
            await task
        return provider, captured.value

    provider, error = asyncio.run(scenario())
    receipt = error.trace.stream_receipt

    assert receipt is not None
    assert receipt.status is TeacherStreamStatus.CANCELLED
    assert receipt.execution_trace is not None
    assert receipt.execution_trace.cancelled is True
    assert receipt.execution_trace.timed_out is False
    assert (
        receipt.execution_trace.degradation_decision.action
        is TeacherDegradationAction.CANCELLED
    )
    assert receipt.execution_trace.degradation_decision.retry_requires_caller_decision is True
    assert provider.call_count == 1
