from __future__ import annotations

import asyncio
import inspect

import pytest
from pydantic import ValidationError

import app.teaching.fastpath_v2.runtime as runtime_module
from app.teaching.composer import FollowUpKind, TeacherEvidence
from app.teaching.contracts import (
    AssessmentState,
    LearnerContext,
    MasteryState,
    StudentMove,
)
from app.teaching.fastpath_v2 import (
    FakeStreamingTeacherProvider,
    RecordingTeacherChunkObserver,
    TeacherLatencyBudget,
    TeacherLatencyClassification,
    TeacherLatencyClassifier,
    TeacherResponseChunk,
    TeacherResponseMode,
    TeacherStreamStatus,
    run_streaming_teacher_turn,
)
from app.teaching.runtime_v2 import TeacherRuntimeError
from app.teaching.runtime_v2 import run_teacher_turn


EVIDENCE = TeacherEvidence(
    evidence_id="chunk-1",
    content="HashMap threshold equals capacity multiplied by load factor.",
    source_reference="fixture://hashmap/threshold",
)


def _budget() -> TeacherLatencyBudget:
    return TeacherLatencyBudget(
        budget_id="offline-unit-test-budget",
        fast_ttft_expected_ms=1_000,
        fast_total_expected_ms=5_000,
        deep_ttft_expected_ms=2_000,
        deep_total_expected_ms=10_000,
        timeout_risk_ms=20_000,
    )


def _context(**changes) -> LearnerContext:
    values = {
        "current_concept": "HashMap threshold",
        "learning_goal": "Distinguish threshold from capacity",
        "learner_level": "intermediate",
        "mastery_state": MasteryState.IN_PROGRESS,
        "student_move": StudentMove.CONTINUE,
        "evidence_available": True,
        "relevant_evidence_ids": ("chunk-1",),
        "assessment_state": AssessmentState.READY,
    }
    values.update(changes)
    return LearnerContext(**values)


def _run(
    *,
    message: str = "为什么 threshold 不是 capacity？",
    provider: FakeStreamingTeacherProvider | None = None,
    observer: RecordingTeacherChunkObserver | None = None,
    evidence: tuple[TeacherEvidence, ...] = (EVIDENCE,),
):
    selected_provider = provider or FakeStreamingTeacherProvider()
    result = asyncio.run(
        run_streaming_teacher_turn(
            latest_user_message=message,
            learner_context=_context(),
            provider=selected_provider,
            latency_budget=_budget(),
            evidence=evidence,
            chunk_observer=observer,
        )
    )
    return selected_provider, result


def test_fast_stream_emits_validated_chunks_and_complete_ttft_receipt() -> None:
    observer = RecordingTeacherChunkObserver()
    provider = FakeStreamingTeacherProvider(first_chunk_delay_seconds=0.005)

    provider, result = _run(provider=provider, observer=observer)
    receipt = result.runtime_trace.stream_receipt

    assert receipt is not None
    assert receipt.response_mode == TeacherResponseMode.FAST.value
    assert receipt.status is TeacherStreamStatus.COMPLETED
    assert receipt.time_to_first_chunk_ms is not None
    assert receipt.time_to_first_chunk_ms >= 0.0
    assert receipt.total_response_ms >= receipt.time_to_first_chunk_ms
    assert receipt.chunk_count == 2
    assert receipt.provider_wait_ms <= receipt.total_response_ms
    assert receipt.composer_overhead_ms <= receipt.total_response_ms
    assert receipt.provider_call_count == 1
    assert receipt.latency_classification is TeacherLatencyClassification.FAST_EXPECTED
    assert receipt.stable_prefix_hash == provider.envelopes[0].stable_prefix.stable_prefix_hash
    assert receipt.dynamic_tail_hash == provider.envelopes[0].dynamic_tail.dynamic_tail_hash
    assert receipt.prompt_identity == provider.envelopes[0].identity.prompt_identity
    assert observer.chunks[0].text_delta
    assert observer.chunks[-1].is_final is True
    assert len(provider.envelopes) == 1
    assert provider.call_count == 1
    assert result.response_draft.text == "threshold 是扩容触发线，它由 capacity 与 load factor 共同决定。"


def test_explicit_deep_request_routes_the_same_single_stream_call_as_deep() -> None:
    provider, result = _run(message="请严格证明并逐步推导摊还复杂度")

    receipt = result.runtime_trace.stream_receipt
    assert receipt is not None
    assert provider.response_modes == [TeacherResponseMode.DEEP]
    assert provider.call_count == 1
    assert receipt.response_mode == TeacherResponseMode.DEEP.value
    assert receipt.latency_classification is TeacherLatencyClassification.DEEP_EXPECTED


def test_fast_timeout_has_degradation_recommendation_and_no_fallback_call() -> None:
    provider = FakeStreamingTeacherProvider(error=TimeoutError("offline timeout"))

    with pytest.raises(TeacherRuntimeError, match="TEACHER_STREAM_TIMEOUT") as captured:
        _run(provider=provider)

    error = captured.value
    assert provider.call_count == 1
    assert error.trace is not None
    receipt = error.trace.stream_receipt
    assert receipt is not None
    assert receipt.status is TeacherStreamStatus.TIMED_OUT
    assert receipt.provider_call_count == 1
    assert receipt.latency_classification is TeacherLatencyClassification.TIMEOUT_RISK
    assert "without a second Provider call" in (receipt.degradation_recommendation or "")


@pytest.mark.parametrize(
    "chunks",
    [
        (TeacherResponseChunk(sequence_number=0, text_delta="partial"),),
        (
            TeacherResponseChunk(sequence_number=1, text_delta="out of order"),
            TeacherResponseChunk(
                sequence_number=2,
                is_final=True,
                follow_up_kind=FollowUpKind.NONE,
            ),
        ),
        (
            TeacherResponseChunk(
                sequence_number=0,
                text_delta="done",
                is_final=True,
                follow_up_kind=FollowUpKind.NONE,
            ),
            TeacherResponseChunk(sequence_number=1, text_delta="after final"),
        ),
        (
            TeacherResponseChunk(sequence_number=0, text_delta="uncited"),
            TeacherResponseChunk(
                sequence_number=1,
                is_final=True,
                cited_evidence_ids=("unknown-evidence",),
                follow_up_kind=FollowUpKind.NONE,
            ),
        ),
        (
            TeacherResponseChunk(
                sequence_number=0,
                is_final=True,
                cited_evidence_ids=("chunk-1",),
                follow_up_kind=FollowUpKind.NONE,
            ),
        ),
    ],
)
def test_invalid_stream_shapes_fail_safe_without_retry(chunks) -> None:
    provider = FakeStreamingTeacherProvider(chunks=chunks)

    with pytest.raises(TeacherRuntimeError, match="TEACHER_STREAM_INVALID") as captured:
        _run(provider=provider)

    assert provider.call_count == 1
    assert captured.value.trace is not None
    assert captured.value.trace.stream_receipt is not None
    assert captured.value.trace.stream_receipt.status is TeacherStreamStatus.INVALID


def test_required_evidence_missing_fails_before_stream_call() -> None:
    provider = FakeStreamingTeacherProvider()

    with pytest.raises(
        TeacherRuntimeError,
        match="EVIDENCE_REQUIRED_BUT_MISSING",
    ) as captured:
        _run(provider=provider, evidence=())

    assert provider.call_count == 0
    assert captured.value.trace is not None
    assert captured.value.trace.provider_call_count == 0


@pytest.mark.parametrize(
    ("mode", "ttft", "total", "expected"),
    [
        (TeacherResponseMode.FAST, 10.0, 40.0, TeacherLatencyClassification.FAST_EXPECTED),
        (TeacherResponseMode.FAST, 60.0, 200.0, TeacherLatencyClassification.FAST_SLOW),
        (TeacherResponseMode.DEEP, 100.0, 400.0, TeacherLatencyClassification.DEEP_EXPECTED),
        (TeacherResponseMode.FAST, 100.0, 1_001.0, TeacherLatencyClassification.TIMEOUT_RISK),
    ],
)
def test_latency_classification_uses_caller_supplied_budget(
    mode: TeacherResponseMode,
    ttft: float,
    total: float,
    expected: TeacherLatencyClassification,
) -> None:
    budget = TeacherLatencyBudget(
        budget_id="classification-fixture",
        fast_ttft_expected_ms=50,
        fast_total_expected_ms=150,
        deep_ttft_expected_ms=200,
        deep_total_expected_ms=500,
        timeout_risk_ms=1_000,
    )

    actual = TeacherLatencyClassifier().classify(
        response_mode=mode,
        time_to_first_chunk_ms=ttft,
        total_response_ms=total,
        budget=budget,
    )

    assert actual is expected


def test_latency_budget_has_no_implicit_product_marketing_defaults() -> None:
    with pytest.raises(ValidationError):
        TeacherLatencyBudget()  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="timeout risk threshold"):
        TeacherLatencyBudget(
            budget_id="invalid",
            fast_ttft_expected_ms=10,
            fast_total_expected_ms=100,
            deep_ttft_expected_ms=20,
            deep_total_expected_ms=200,
            timeout_risk_ms=150,
        )


def test_fastpath_runtime_has_no_final_response_cache_or_second_provider_stage() -> None:
    source = inspect.getsource(runtime_module).casefold()

    assert "response_cache" not in source
    assert "final_response_cache" not in source
    assert ".structured(" not in source
    assert source.count("provider.stream(") == 1


def test_legacy_non_streaming_result_identity_does_not_gain_a_null_stream_field() -> None:
    provider = FakeStreamingTeacherProvider()

    class StructuredFixture:
        provider_name = "legacy-structured-fixture"

        async def structured(self, messages, response_model, temperature=0.2):
            return response_model(
                text="threshold 是扩容触发线。",
                cited_evidence_ids=("chunk-1",),
                follow_up_kind=FollowUpKind.NONE,
            )

    legacy = asyncio.run(
        run_teacher_turn(
            latest_user_message="为什么 threshold 不是 capacity？",
            learner_context=_context(),
            provider=StructuredFixture(),
            evidence=(EVIDENCE,),
        )
    )

    assert "stream_receipt" not in legacy.runtime_trace.model_dump(mode="json")
    assert provider.call_count == 0
