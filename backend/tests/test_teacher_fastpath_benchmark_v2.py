from __future__ import annotations

import asyncio
import inspect

import app.teaching.fastpath_v2.benchmark as benchmark_module
from app.teaching.fastpath_v2.benchmark import (
    OFFLINE_BENCHMARK_DISCLAIMER,
    TeacherFastPathBenchmarkHarness,
)
from app.teaching.fastpath_v2 import (
    TeacherLatencyBudget,
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


def test_offline_benchmark_runs_1000_varied_fast_turns_and_reports_percentiles() -> None:
    report = asyncio.run(
        TeacherFastPathBenchmarkHarness(latency_budget=_budget()).run()
    )

    assert report.turn_count == 1_000
    assert report.fast_turn_count == 1_000
    assert report.deep_turn_count == 0
    assert report.primary_provider_calls == 1_000
    assert report.routing_provider_calls == 0
    assert report.stable_prefix_unique_count == 1
    assert report.dynamic_tail_unique_count > 1
    assert report.context_item_variations == (0, 4, 12, 24)
    assert report.evidence_size_variations == (64, 512, 2_048, 8_192)
    assert len(report.student_move_variations) >= 6
    assert report.time_to_first_chunk.p95_ms >= report.time_to_first_chunk.p50_ms
    assert report.total_response.p95_ms >= report.total_response.p50_ms
    assert report.runtime_overhead.p95_ms >= report.runtime_overhead.p50_ms
    assert report.disclaimer == OFFLINE_BENCHMARK_DISCLAIMER
    assert "does not represent real Provider latency" in report.disclaimer


def test_benchmark_contains_no_network_or_final_response_cache() -> None:
    source = inspect.getsource(benchmark_module).casefold()

    assert "http://" not in source
    assert "https://" not in source
    assert "api_key" not in source
    assert "response_cache" not in source
    assert "final_response_cache" not in source
    assert ".structured(" not in source
