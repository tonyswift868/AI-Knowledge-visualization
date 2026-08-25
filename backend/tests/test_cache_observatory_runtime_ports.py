from __future__ import annotations

import inspect
from dataclasses import dataclass
from decimal import Decimal

import pytest
from app.cost.cache_observatory import (
    CacheEvent,
    CacheEventSourcePort,
    CacheIdentity,
    CacheLayer,
    CacheMetricsAggregator,
    CacheMetricsStore,
    CacheMissReason,
    CacheObservationSource,
    CacheObservatoryError,
    ExecutionObservatoryAdapter,
    runtime_ports,
)


@dataclass(frozen=True)
class _RuntimeTrace:
    cache_trace_id: str
    events: tuple[CacheEvent, ...]

    def iter_cache_events(self):
        return iter(self.events)


def _identity(layer: CacheLayer, artifact: str) -> CacheIdentity:
    return CacheIdentity(
        layer=layer,
        namespace="port-test",
        artifact_id=artifact,
        provider="openai_compatible",
        model="deepseek-v4-flash",
        prompt_version="prompt-v1",
        route_identity="route-a",
        stable_prefix_hash="stable-a",
        dynamic_context_hash="learner-a",
        source_hash="source-a",
        evidence_hash="evidence-a",
        question_hash="question-a",
        question_bank_version="question-bank-v1",
        parser_version="parser-v1",
        evaluation_version="evaluation-v1",
        identity_policy_version="cache-identity-policy-v1",
    )


def _events(trace_id: str) -> tuple[CacheEvent, ...]:
    return (
        CacheEvent(
            event_id=f"{trace_id}:provider",
            trace_id=trace_id,
            identity=_identity(CacheLayer.PROVIDER_PROMPT_CACHE, "provider"),
            hit=True,
            tokens_reused=700,
            estimated_cost_avoided=Decimal("0.02"),
            currency="USD",
        ),
        CacheEvent(
            event_id=f"{trace_id}:local",
            trace_id=trace_id,
            identity=_identity(CacheLayer.LOCAL_RESULT_CACHE, "local"),
            hit=False,
            miss_reason=CacheMissReason.DYNAMIC_CONTEXT_CHANGED,
        ),
    )


def test_runtime_source_is_structural_and_adapter_has_no_runtime_imports() -> None:
    source = _RuntimeTrace("trace-1", _events("trace-1"))
    assert isinstance(source, CacheEventSourcePort)
    module_source = inspect.getsource(runtime_ports)
    for forbidden in (
        "app.cost.execution_v2",
        "app.cost.durable_execution",
        "app.teaching.runtime_v2",
        "app.teaching.evaluator_v2",
        ".profiles",
    ):
        assert forbidden not in module_source


def test_budgeted_teacher_and_review_entrypoints_preserve_source_and_idempotency(
    tmp_path,
) -> None:
    store = CacheMetricsStore(tmp_path / "metrics.db")
    adapter = ExecutionObservatoryAdapter(store)
    sources = (
        (
            adapter.observe_budgeted_execution,
            CacheObservationSource.BUDGETED_EXECUTION,
            "budgeted",
        ),
        (
            adapter.observe_teacher_trace,
            CacheObservationSource.TEACHER_TRACE,
            "teacher",
        ),
        (adapter.observe_review_trace, CacheObservationSource.REVIEW_TRACE, "review"),
    )
    for observe, expected_source, trace_id in sources:
        source = _RuntimeTrace(trace_id, _events(trace_id))
        first = observe(source)
        duplicate = observe(source)
        assert first.source == expected_source
        assert first.received_event_count == 2
        assert first.accepted_event_count == 2
        assert first.duplicate_event_count == 0
        assert duplicate.accepted_event_count == 0
        assert duplicate.duplicate_event_count == 2
    assert store.count() == 6


def test_adapter_rejects_cross_trace_events_before_writing() -> None:
    aggregator = CacheMetricsAggregator()
    adapter = ExecutionObservatoryAdapter(aggregator)
    source = _RuntimeTrace("declared", _events("different"))
    with pytest.raises(CacheObservatoryError):
        adapter.observe_teacher_trace(source)
    assert aggregator.events() == ()


def test_adapter_accepts_empty_observation_without_fabricating_events() -> None:
    aggregator = CacheMetricsAggregator()
    receipt = ExecutionObservatoryAdapter(aggregator).observe_review_trace(
        _RuntimeTrace("empty-review", ())
    )
    assert receipt.received_event_count == 0
    assert receipt.accepted_event_count == 0
    assert receipt.duplicate_event_count == 0
    assert aggregator.events() == ()
