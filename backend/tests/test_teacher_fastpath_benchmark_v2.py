from __future__ import annotations

import asyncio
import inspect

import app.teaching.fastpath_v2.benchmark as benchmark_module
from app.teaching.fastpath_v2.benchmark import (
    OFFLINE_BENCHMARK_DISCLAIMER,
    TeacherFastPathBenchmarkHarness,
)
from app.teaching.fastpath_v2 import (
    TeacherContextBudget,
    TeacherLatencyBudget,
    TeacherResponseLengthPolicy,
)


def _budget() -> TeacherLatencyBudget:
    return TeacherLatencyBudget(
        budget_id="offline-1000-turn-test-budget",
        fast_ttft_expected_ms=1_000,
        fast_total_expected_ms=5_000,
        deep_ttft_expected_ms=2_000,
        deep_total_expected_ms=10_000,
        timeout_risk_ms=20_000,
    )


def _context_budget() -> TeacherContextBudget:
    return TeacherContextBudget(
        budget_id="offline-10000-turn-context-budget",
        maximum_prompt_tokens=8_000,
        maximum_evidence_tokens=4_000,
        maximum_history_tokens=2_000,
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


def test_offline_benchmark_runs_10000_varied_turns_and_reports_distributions() -> None:
    report = asyncio.run(
        TeacherFastPathBenchmarkHarness(
            latency_budget=_budget(),
            context_budget=_context_budget(),
            response_length_policy=_length_policy(),
        ).run()
    )

    assert report.turn_count == 10_000
    assert report.completed_stream_count == 9_800
    assert report.duplicate_replay_count == 100
    assert report.cancelled_turn_count == 100
    assert report.concurrent_session_count == 100
    assert report.resumed_session_count == 1
    assert report.fast_turn_count + report.deep_turn_count == 10_000
    assert report.fast_turn_count > report.deep_turn_count > 0
    assert report.fast_ratio + report.deep_ratio == 1.0
    assert report.primary_provider_calls == 9_900
    assert report.expected_primary_provider_calls == 9_900
    assert report.provider_call_invariant_passed is True
    assert report.routing_provider_calls == 0
    assert report.real_provider_calls == 0
    assert report.stable_prefix_unique_count == 1
    assert report.dynamic_tail_unique_count > 1
    assert report.prompt_token_budget == 8_000
    assert report.context_item_variations == (0, 4, 12, 24)
    assert report.history_turn_variations == (0, 2, 8, 24)
    assert report.evidence_size_variations == (64, 512, 2_048, 8_192)
    assert len(report.student_move_variations) >= 9
    assert report.time_to_first_chunk_ms.sample_count == 9_800
    assert report.time_to_first_chunk_ms.p95 >= report.time_to_first_chunk_ms.p50
    assert report.orchestration_latency_ms.sample_count == 9_900
    assert report.orchestration_latency_ms.p95 >= report.orchestration_latency_ms.p50
    assert report.runtime_overhead_ms.p95 >= report.runtime_overhead_ms.p50
    assert report.context_item_distribution.maximum > report.context_item_distribution.minimum
    assert (
        report.estimated_prompt_token_distribution.maximum
        > report.estimated_prompt_token_distribution.minimum
    )
    assert report.disclaimer == OFFLINE_BENCHMARK_DISCLAIMER
    assert "does not represent real Provider latency" in report.disclaimer
    assert "billable token usage" in report.disclaimer


def test_benchmark_contains_no_network_or_final_response_cache() -> None:
    source = inspect.getsource(benchmark_module).casefold()

    assert "http://" not in source
    assert "https://" not in source
    assert "api_key" not in source
    assert "response_cache" not in source
    assert "final_response_cache" not in source
    assert ".structured(" not in source
