from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from app.cost.cache_observatory import (
    LOCAL_REVIEW_ACTIONS,
    CacheEvent,
    CacheEventConflict,
    CacheIdentity,
    CacheLayer,
    CacheMetricsAggregator,
    CacheMissReason,
    CacheObservatoryError,
    CacheOpportunityAnalyzer,
    CacheOpportunityTrace,
    ReviewAction,
    ReviewActionCostTrace,
    ReviewExecutionPlane,
    TeacherCostComponent,
    TeacherCostMode,
    TeacherTurnCostTrace,
    WorkloadKind,
    build_review_cost_profile,
    build_teacher_cost_profile_report,
    cache_events_from_usage_receipt,
    classify_cache_miss,
    local_cache_miss_reason,
    review_execution_plane,
    review_trace_from_evaluation_result,
    teacher_trace_from_usage_receipt,
)
from app.providers.telemetry import ProviderUsageReceipt
from app.teaching.contracts import TeachingDepth
from app.teaching.evaluator_v2 import (
    EvaluationOutcome,
    EvaluationPath,
    EvaluationResult,
)


def _identity(
    layer: CacheLayer,
    *,
    artifact: str = "artifact-1",
    model: str = "deepseek-v4-flash",
    prompt: str = "teacher-v1",
    route: str = "route-1",
    dynamic: str = "learner-1",
    evidence: str = "evidence-1",
    expires: int | None = None,
) -> CacheIdentity:
    return CacheIdentity(
        layer=layer,
        namespace="test",
        artifact_id=artifact,
        provider="openai_compatible",
        model=model,
        prompt_version=prompt,
        route_identity=route,
        stable_prefix_hash="stable-1",
        dynamic_context_hash=dynamic,
        evidence_hash=evidence,
        question_hash="question-1",
        expires_at_ms=expires,
    )


def _event(
    event_id: str,
    layer: CacheLayer,
    *,
    trace_id: str = "trace-1",
    hit: bool,
    reason: CacheMissReason | None = None,
    tokens: int = 0,
    calls: int = 0,
    savings: str = "0",
    currency: str = "USD",
) -> CacheEvent:
    return CacheEvent(
        event_id=event_id,
        trace_id=trace_id,
        identity=_identity(layer, artifact=event_id),
        hit=hit,
        miss_reason=None if hit else reason or CacheMissReason.UNKNOWN,
        bytes_reused=128 if hit else 0,
        tokens_reused=tokens,
        avoided_provider_calls=calls,
        estimated_cost_avoided=Decimal(savings),
        currency=currency,
        lookup_ms=Decimal("0.5"),
    )


def test_cache_taxonomy_and_miss_reason_contract_are_complete() -> None:
    assert {item.value for item in CacheLayer} == {
        "PROVIDER_PROMPT_CACHE",
        "LOCAL_RESULT_CACHE",
        "EVIDENCE_CACHE",
        "PARSE_CACHE",
        "QUESTION_BANK_CACHE",
        "EVALUATION_ARTIFACT_CACHE",
    }
    assert {item.value for item in CacheMissReason} == {
        "COLD",
        "DYNAMIC_CONTEXT_CHANGED",
        "EVIDENCE_CHANGED",
        "MODEL_CHANGED",
        "PROMPT_VERSION_CHANGED",
        "ROUTE_CHANGED",
        "EXPIRED",
        "NOT_CACHEABLE",
        "UNKNOWN",
    }


def test_cache_identity_is_canonical_and_miss_classifier_explains_all_reasons() -> None:
    requested = _identity(CacheLayer.PROVIDER_PROMPT_CACHE)
    assert requested.cache_key == _identity(CacheLayer.PROVIDER_PROMPT_CACHE).cache_key
    assert classify_cache_miss(requested, None) == CacheMissReason.COLD
    assert (
        classify_cache_miss(requested, requested, cacheable=False)
        == CacheMissReason.NOT_CACHEABLE
    )
    assert (
        classify_cache_miss(
            requested,
            replace(requested, expires_at_ms=10),
            observed_at_ms=10,
        )
        == CacheMissReason.EXPIRED
    )
    cases = (
        (
            replace(requested, prompt_version="v2"),
            CacheMissReason.PROMPT_VERSION_CHANGED,
        ),
        (replace(requested, model="another-model"), CacheMissReason.MODEL_CHANGED),
        (replace(requested, route_identity="route-2"), CacheMissReason.ROUTE_CHANGED),
        (
            replace(requested, evidence_hash="evidence-2"),
            CacheMissReason.EVIDENCE_CHANGED,
        ),
        (
            replace(requested, dynamic_context_hash="learner-2"),
            CacheMissReason.DYNAMIC_CONTEXT_CHANGED,
        ),
        (requested, CacheMissReason.UNKNOWN),
    )
    for candidate, expected in cases:
        assert classify_cache_miss(requested, candidate) == expected


def test_cache_event_rejects_false_savings_and_ambiguous_outcomes() -> None:
    with pytest.raises(CacheObservatoryError):
        _event(
            "miss-with-savings", CacheLayer.LOCAL_RESULT_CACHE, hit=False, savings="1"
        )
    with pytest.raises(CacheObservatoryError):
        CacheEvent(
            event_id="hit-with-reason",
            trace_id="trace-1",
            identity=_identity(CacheLayer.PROVIDER_PROMPT_CACHE),
            hit=True,
            miss_reason=CacheMissReason.COLD,
        )
    with pytest.raises(CacheObservatoryError):
        CacheEvent(
            event_id="unexplained-miss",
            trace_id="trace-1",
            identity=_identity(CacheLayer.PROVIDER_PROMPT_CACHE),
            hit=False,
        )
    with pytest.raises(CacheObservatoryError):
        _event(
            "unclassified",
            CacheLayer.LOCAL_RESULT_CACHE,
            hit=True,
            savings="1",
            currency="",
        )


def test_per_layer_metrics_never_mix_provider_and_local_hit_ratios() -> None:
    aggregator = CacheMetricsAggregator()
    provider_hit = _event(
        "provider-hit",
        CacheLayer.PROVIDER_PROMPT_CACHE,
        hit=True,
        tokens=900,
        savings="0.01",
    )
    events = (
        provider_hit,
        _event(
            "provider-miss",
            CacheLayer.PROVIDER_PROMPT_CACHE,
            hit=False,
            reason=CacheMissReason.DYNAMIC_CONTEXT_CHANGED,
        ),
        _event(
            "local-hit",
            CacheLayer.LOCAL_RESULT_CACHE,
            hit=True,
            calls=1,
            savings="0.20",
        ),
        _event(
            "local-miss-1",
            CacheLayer.LOCAL_RESULT_CACHE,
            hit=False,
            reason=CacheMissReason.COLD,
        ),
        _event(
            "local-miss-2",
            CacheLayer.LOCAL_RESULT_CACHE,
            hit=False,
            reason=CacheMissReason.MODEL_CHANGED,
        ),
    )
    assert aggregator.extend(events) == len(events)
    assert not aggregator.ingest(provider_hit)
    with pytest.raises(CacheEventConflict):
        aggregator.ingest(replace(provider_hit, tokens_reused=901))

    report = aggregator.report()
    provider = report.for_layer(CacheLayer.PROVIDER_PROMPT_CACHE)
    local = report.for_layer(CacheLayer.LOCAL_RESULT_CACHE)
    assert provider.lookup_count == 2
    assert provider.hit_ratio == Decimal("0.5")
    assert provider.tokens_reused == 900
    assert provider.avoided_provider_calls == 0
    assert provider.estimated_cost_avoided == Decimal("0.01")
    assert provider.miss_reason_counts == {CacheMissReason.DYNAMIC_CONTEXT_CHANGED: 1}
    assert local.lookup_count == 3
    assert local.hit_ratio == Decimal(1) / Decimal(3)
    assert local.avoided_provider_calls == 1
    assert local.estimated_cost_avoided == Decimal("0.20")
    public = report.to_public_dict()
    assert public["combined_hit_ratio"] is None
    assert "intentionally separate" in public["combined_hit_ratio_reason"]


def test_cache_metrics_preserve_mixed_currency_estimates_without_conversion() -> None:
    aggregator = CacheMetricsAggregator()
    aggregator.extend(
        [
            _event(
                "usd-hit",
                CacheLayer.LOCAL_RESULT_CACHE,
                hit=True,
                savings="1.00",
                currency="USD",
            ),
            _event(
                "cny-hit",
                CacheLayer.LOCAL_RESULT_CACHE,
                hit=True,
                savings="2.00",
                currency="CNY",
            ),
        ]
    )
    snapshot = aggregator.snapshot(CacheLayer.LOCAL_RESULT_CACHE)
    assert snapshot.estimated_cost_avoided is None
    assert [
        (item.currency, item.amount) for item in snapshot.estimated_costs_by_currency
    ] == [
        ("CNY", Decimal("2.00")),
        ("USD", Decimal("1.00")),
    ]
    assert snapshot.savings_classification == "estimated"


def test_provider_usage_adapter_emits_separate_provider_and_local_cache_events() -> (
    None
):
    receipt = ProviderUsageReceipt(
        provider="openai_compatible",
        model="deepseek-v4-flash",
        task_type="teacher",
        request_id="request-cache",
        started_at="2026-08-25T12:00:00+00:00",
        latency_ms=100,
        prompt_tokens=1_000,
        completion_tokens=200,
        total_tokens=1_200,
        prompt_cache_hit_tokens=700,
        prompt_cache_miss_tokens=300,
        cache_hit_ratio=0.7,
        local_result_cache_hit=False,
        estimated_cost_usd=None,
        pricing_version=None,
        success=True,
        error_class=None,
        prompt_version="teacher-v1",
        provider_called=True,
        local_cache_miss=True,
        cache_lookup_ms=3,
    )
    events = cache_events_from_usage_receipt(
        trace_id="usage-trace",
        receipt=receipt,
        provider_prompt_identity=_identity(CacheLayer.PROVIDER_PROMPT_CACHE),
        local_result_identity=_identity(CacheLayer.LOCAL_RESULT_CACHE),
        local_miss_reason=CacheMissReason.DYNAMIC_CONTEXT_CHANGED,
        provider_cache_estimated_cost_avoided=Decimal("0.02"),
    )
    assert len(events) == 2
    provider = next(
        event for event in events if event.layer == CacheLayer.PROVIDER_PROMPT_CACHE
    )
    local = next(
        event for event in events if event.layer == CacheLayer.LOCAL_RESULT_CACHE
    )
    assert provider.hit
    assert provider.tokens_reused == 700
    assert provider.estimated_cost_avoided == Decimal("0.02")
    assert not local.hit
    assert local.miss_reason == CacheMissReason.DYNAMIC_CONTEXT_CHANGED
    assert local.lookup_ms == Decimal(3)
    report = CacheMetricsAggregator()
    report.extend(events)
    assert report.snapshot(CacheLayer.PROVIDER_PROMPT_CACHE).hit_ratio == Decimal(1)
    assert report.snapshot(CacheLayer.LOCAL_RESULT_CACHE).hit_ratio == Decimal(0)


def test_local_result_cache_hit_never_creates_a_fake_provider_prompt_hit() -> None:
    receipt = ProviderUsageReceipt(
        provider="openai_compatible",
        model="deepseek-v4-flash",
        task_type="teacher",
        request_id="request-local",
        started_at="2026-08-25T12:00:00+00:00",
        latency_ms=0,
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
        prompt_cache_hit_tokens=None,
        prompt_cache_miss_tokens=None,
        cache_hit_ratio=None,
        local_result_cache_hit=True,
        estimated_cost_usd=None,
        pricing_version=None,
        success=True,
        error_class=None,
        prompt_version="teacher-v1",
        provider_called=False,
        local_cache_miss=False,
        cache_lookup_ms=1,
    )
    events = cache_events_from_usage_receipt(
        trace_id="local-only",
        receipt=receipt,
        provider_prompt_identity=_identity(CacheLayer.PROVIDER_PROMPT_CACHE),
        local_result_identity=_identity(CacheLayer.LOCAL_RESULT_CACHE),
        local_result_estimated_cost_avoided=Decimal("0.10"),
        local_result_bytes_reused=2_048,
    )
    assert len(events) == 1
    assert events[0].layer == CacheLayer.LOCAL_RESULT_CACHE
    assert events[0].hit
    assert events[0].avoided_provider_calls == 1
    assert events[0].estimated_cost_avoided == Decimal("0.10")
    assert local_cache_miss_reason("expired") == CacheMissReason.EXPIRED
    assert local_cache_miss_reason("unrecognized") == CacheMissReason.UNKNOWN


def _teacher_trace(
    trace_id: str,
    mode: TeacherCostMode,
    *,
    stable_cost: str,
    dynamic_cost: str,
    output_cost: str,
    ttft: str | None,
    cached: int | None = 700,
) -> TeacherTurnCostTrace:
    components = tuple(
        Decimal(value) for value in (stable_cost, dynamic_cost, output_cost)
    )
    return TeacherTurnCostTrace(
        trace_id=trace_id,
        mode=mode,
        prompt_tokens=1_000,
        stable_prefix_tokens=700,
        dynamic_tokens=300,
        cached_input_tokens=cached,
        output_tokens=200,
        provider_call_count=1,
        estimated_actual_cost=sum(components, Decimal(0)),
        currency="USD",
        stable_prefix_estimated_cost=components[0],
        dynamic_input_estimated_cost=components[1],
        output_estimated_cost=components[2],
        ttft_ms=Decimal(ttft) if ttft is not None else None,
    )


def test_teacher_cost_profiles_are_separate_for_fast_and_deep_with_ttft_seam() -> None:
    traces = (
        _teacher_trace(
            "fast-1",
            TeacherCostMode.FAST,
            stable_cost="0.01",
            dynamic_cost="0.03",
            output_cost="0.08",
            ttft="100",
        ),
        _teacher_trace(
            "fast-2",
            TeacherCostMode.FAST,
            stable_cost="0.01",
            dynamic_cost="0.03",
            output_cost="0.08",
            ttft=None,
            cached=None,
        ),
        _teacher_trace(
            "deep-1",
            TeacherCostMode.DEEP,
            stable_cost="0.03",
            dynamic_cost="0.09",
            output_cost="0.20",
            ttft="300",
        ),
    )
    report = build_teacher_cost_profile_report(traces)
    assert report.fast.turn_count == 2
    assert report.deep.turn_count == 1
    assert report.fast.prompt_tokens == 2_000
    assert report.fast.stable_prefix_tokens == 1_400
    assert report.fast.dynamic_tokens == 600
    assert report.fast.cached_input_tokens == 700
    assert report.fast.cache_usage_unknown_turns == 1
    assert report.fast.output_tokens == 400
    assert report.fast.provider_call_count == 2
    assert report.fast.estimated_actual_cost == Decimal("0.24")
    assert report.fast.most_expensive_component == TeacherCostComponent.OUTPUT
    assert report.fast.ttft.observed_count == 1
    assert report.fast.ttft.missing_count == 1
    assert report.fast.ttft.measurement_seam == "provider_stream_first_token"
    assert report.deep.estimated_actual_cost == Decimal("0.32")
    assert report.most_expensive_mode == TeacherCostMode.DEEP
    assert report.fast.cost_classification == "estimated_actual_cost"


def test_teacher_usage_adapter_preserves_unknown_provider_cache_and_depth() -> None:
    receipt = ProviderUsageReceipt(
        provider="openai_compatible",
        model="deepseek-v4-flash",
        task_type="teacher",
        request_id="request-1",
        started_at="2026-08-25T12:00:00+00:00",
        latency_ms=100,
        prompt_tokens=1_000,
        completion_tokens=200,
        total_tokens=1_200,
        prompt_cache_hit_tokens=None,
        prompt_cache_miss_tokens=None,
        cache_hit_ratio=None,
        local_result_cache_hit=False,
        estimated_cost_usd=None,
        pricing_version=None,
        success=True,
        error_class=None,
        prompt_version="teacher-v1",
    )
    trace = teacher_trace_from_usage_receipt(
        trace_id="teacher-adapter",
        mode=TeachingDepth.DEEP,
        receipt=receipt,
        stable_prefix_tokens=700,
        dynamic_tokens=300,
        estimated_actual_cost=Decimal("0.12"),
        currency="USD",
        stable_prefix_estimated_cost=Decimal("0.01"),
        dynamic_input_estimated_cost=Decimal("0.03"),
        output_estimated_cost=Decimal("0.08"),
        ttft_ms=Decimal(90),
    )
    assert trace.mode == TeacherCostMode.DEEP
    assert trace.cached_input_tokens is None
    assert (
        build_teacher_cost_profile_report((trace,)).deep.cache_usage_unknown_turns == 1
    )


def test_teacher_trace_rejects_inconsistent_split_or_component_cost() -> None:
    with pytest.raises(CacheObservatoryError):
        replace(
            _teacher_trace(
                "bad",
                TeacherCostMode.FAST,
                stable_cost="0.01",
                dynamic_cost="0.03",
                output_cost="0.08",
                ttft="100",
            ),
            dynamic_tokens=301,
        )
    with pytest.raises(CacheObservatoryError):
        replace(
            _teacher_trace(
                "bad-cost",
                TeacherCostMode.FAST,
                stable_cost="0.01",
                dynamic_cost="0.03",
                output_cost="0.08",
                ttft="100",
            ),
            estimated_actual_cost=Decimal(9),
        )


def test_review_cost_profile_exposes_local_and_provider_actions() -> None:
    evaluation_id = "evaluation-1"
    traces = [
        ReviewActionCostTrace(
            trace_id=f"local-{action.value}",
            evaluation_id=evaluation_id,
            action=action,
        )
        for action in LOCAL_REVIEW_ACTIONS
    ]
    traces.extend(
        (
            ReviewActionCostTrace(
                trace_id="semantic",
                evaluation_id=evaluation_id,
                action=ReviewAction.SEMANTIC_EVALUATION,
                provider_call_count=1,
                input_tokens=500,
                output_tokens=100,
                estimated_cost=Decimal("0.10"),
                currency="USD",
            ),
            ReviewActionCostTrace(
                trace_id="explanation",
                evaluation_id=evaluation_id,
                action=ReviewAction.TEACHER_EXPLANATION,
                provider_call_count=1,
                input_tokens=800,
                output_tokens=200,
                estimated_cost=Decimal("0.30"),
                currency="USD",
            ),
        )
    )
    profile = build_review_cost_profile(traces)
    assert profile.evaluation_count == 1
    assert profile.provider_call_count == 2
    assert profile.estimated_cost == Decimal("0.40")
    assert profile.most_expensive_action == ReviewAction.TEACHER_EXPLANATION
    assert set(profile.zero_provider_capable_actions) == set(LOCAL_REVIEW_ACTIONS)
    assert profile.zero_provider_evaluation_count == 0
    assert (
        review_execution_plane(ReviewAction.QUESTION_READ) == ReviewExecutionPlane.LOCAL
    )
    assert (
        review_execution_plane(ReviewAction.SEMANTIC_EVALUATION)
        == ReviewExecutionPlane.PROVIDER
    )
    with pytest.raises(CacheObservatoryError):
        ReviewActionCostTrace(
            trace_id="invalid-local",
            evaluation_id=evaluation_id,
            action=ReviewAction.FSRS_UPDATE,
            provider_call_count=1,
        )


def test_review_objective_path_can_be_entirely_zero_provider() -> None:
    traces = tuple(
        ReviewActionCostTrace(
            trace_id=action.value,
            evaluation_id="objective-evaluation",
            action=action,
        )
        for action in (
            ReviewAction.QUESTION_READ,
            ReviewAction.QUESTION_BANK_SELECTION,
            ReviewAction.OBJECTIVE_EVALUATION,
            ReviewAction.WEAK_QUEUE_UPDATE,
            ReviewAction.FSRS_UPDATE,
        )
    )
    profile = build_review_cost_profile(traces)
    assert profile.provider_call_count == 0
    assert profile.zero_provider_evaluation_count == 1
    assert profile.zero_provider_path == tuple(trace.action for trace in traces)


def test_evaluator_v2_adapter_keeps_objective_local_and_semantic_provider() -> None:
    objective = EvaluationResult(
        assessment_id="assessment-1",
        attempt_id="attempt-objective",
        rubric_id="rubric-1",
        path=EvaluationPath.OBJECTIVE,
        outcome=EvaluationOutcome.CORRECT,
        score=1.0,
        confidence=1.0,
        reason="objective_exact_match",
        provider_calls=0,
    )
    semantic = EvaluationResult(
        assessment_id="assessment-1",
        attempt_id="attempt-semantic",
        rubric_id="rubric-2",
        path=EvaluationPath.SEMANTIC,
        outcome=EvaluationOutcome.PARTIAL,
        score=0.5,
        confidence=0.8,
        reason="semantic_provider_partial",
        provider_calls=1,
    )
    objective_trace = review_trace_from_evaluation_result(
        trace_id="objective", result=objective
    )
    semantic_trace = review_trace_from_evaluation_result(
        trace_id="semantic",
        result=semantic,
        input_tokens=600,
        output_tokens=80,
        estimated_cost=Decimal("0.12"),
    )
    assert objective_trace.action == ReviewAction.OBJECTIVE_EVALUATION
    assert objective_trace.provider_call_count == 0
    assert semantic_trace.action == ReviewAction.SEMANTIC_EVALUATION
    assert semantic_trace.provider_call_count == 1


def test_cache_opportunity_analyzer_is_read_only_and_separates_current_hits() -> None:
    first_event = _event(
        "provider-first",
        CacheLayer.PROVIDER_PROMPT_CACHE,
        trace_id="opportunity-1",
        hit=False,
        reason=CacheMissReason.COLD,
    )
    second_events = (
        _event(
            "provider-second",
            CacheLayer.PROVIDER_PROMPT_CACHE,
            trace_id="opportunity-2",
            hit=False,
            reason=CacheMissReason.DYNAMIC_CONTEXT_CHANGED,
        ),
        _event(
            "local-second",
            CacheLayer.LOCAL_RESULT_CACHE,
            trace_id="opportunity-2",
            hit=False,
            reason=CacheMissReason.COLD,
        ),
    )
    traces = (
        CacheOpportunityTrace(
            trace_id="opportunity-1",
            workload=WorkloadKind.TEACHER_TURN,
            cache_events=(first_event,),
            stable_prefix_key="prefix-a",
            full_request_key="request-a",
            stable_prefix_tokens=700,
            provider_input_tokens=1_000,
            provider_call_count=1,
            cacheable=True,
            stable_prefix_reuse_estimated_savings=Decimal("0.02"),
            duplicate_call_estimated_cost=Decimal("0.10"),
            currency="USD",
        ),
        CacheOpportunityTrace(
            trace_id="opportunity-2",
            workload=WorkloadKind.TEACHER_TURN,
            cache_events=second_events,
            stable_prefix_key="prefix-a",
            full_request_key="request-a",
            stable_prefix_tokens=700,
            provider_input_tokens=1_000,
            provider_call_count=1,
            cacheable=True,
            stable_prefix_reuse_estimated_savings=Decimal("0.02"),
            duplicate_call_estimated_cost=Decimal("0.10"),
            currency="USD",
        ),
        CacheOpportunityTrace(
            trace_id="opportunity-3",
            workload=WorkloadKind.REVIEW_EVALUATION,
            cache_events=(
                _event(
                    "provider-third",
                    CacheLayer.PROVIDER_PROMPT_CACHE,
                    trace_id="opportunity-3",
                    hit=False,
                    reason=CacheMissReason.NOT_CACHEABLE,
                ),
            ),
            stable_prefix_key="prefix-b",
            full_request_key="request-b",
            stable_prefix_tokens=400,
            provider_input_tokens=800,
            provider_call_count=1,
            cacheable=False,
            stable_prefix_reuse_estimated_savings=Decimal("0.01"),
            duplicate_call_estimated_cost=Decimal("0.08"),
            currency="USD",
        ),
    )
    original = tuple(traces)
    report = CacheOpportunityAnalyzer().analyze(traces)
    assert traces == original
    assert report.trace_count == 3
    assert report.teacher_turn_traces == 2
    assert report.review_evaluation_traces == 1
    assert report.potential_stable_prefix_reuse == 1
    assert report.potential_stable_prefix_tokens == 700
    assert report.potential_stable_prefix_estimated_savings == Decimal("0.02")
    assert report.avoidable_duplicate_calls == 1
    assert report.avoidable_duplicate_input_tokens == 1_000
    assert report.avoidable_duplicate_estimated_cost == Decimal("0.10")
    assert report.non_cacheable_dynamic_calls == 1
    assert not report.behavior_changed
    provider = report.current_cache_metrics.for_layer(CacheLayer.PROVIDER_PROMPT_CACHE)
    local = report.current_cache_metrics.for_layer(CacheLayer.LOCAL_RESULT_CACHE)
    assert provider.lookup_count == 3
    assert local.lookup_count == 1
    assert report.current_cache_metrics.to_public_dict()["combined_hit_ratio"] is None


def test_cache_opportunity_requires_explicit_provider_miss_telemetry() -> None:
    first = CacheOpportunityTrace(
        trace_id="review-first",
        workload=WorkloadKind.REVIEW_EVALUATION,
        cache_events=(),
        stable_prefix_key="shared-prefix",
        full_request_key="first-request",
        stable_prefix_tokens=400,
        provider_input_tokens=800,
        provider_call_count=1,
        cacheable=True,
        stable_prefix_reuse_estimated_savings=Decimal("0.01"),
        duplicate_call_estimated_cost=Decimal("0.08"),
        currency="USD",
    )
    explicit_events = (
        _event(
            "review-hit",
            CacheLayer.PROVIDER_PROMPT_CACHE,
            trace_id="review-second",
            hit=True,
        ),
        _event(
            "review-miss",
            CacheLayer.PROVIDER_PROMPT_CACHE,
            trace_id="review-second",
            hit=False,
            reason=CacheMissReason.DYNAMIC_CONTEXT_CHANGED,
        ),
    )
    second = CacheOpportunityTrace(
        trace_id="review-second",
        workload=WorkloadKind.REVIEW_EVALUATION,
        cache_events=explicit_events,
        stable_prefix_key="shared-prefix",
        full_request_key="second-request",
        stable_prefix_tokens=400,
        provider_input_tokens=800,
        provider_call_count=2,
        cacheable=True,
        stable_prefix_reuse_estimated_savings=Decimal("0.01"),
        duplicate_call_estimated_cost=Decimal("0.08"),
        currency="USD",
    )
    unknown = CacheOpportunityTrace(
        trace_id="review-unknown",
        workload=WorkloadKind.REVIEW_EVALUATION,
        cache_events=(),
        stable_prefix_key="shared-prefix",
        full_request_key="third-request",
        stable_prefix_tokens=400,
        provider_input_tokens=800,
        provider_call_count=1,
        cacheable=True,
        stable_prefix_reuse_estimated_savings=Decimal("0.01"),
        duplicate_call_estimated_cost=Decimal("0.08"),
        currency="USD",
    )

    report = CacheOpportunityAnalyzer().analyze((first, second, unknown))

    assert report.potential_stable_prefix_reuse == 1
    assert report.potential_stable_prefix_tokens == 400
    assert report.potential_stable_prefix_estimated_savings == Decimal("0.01")
