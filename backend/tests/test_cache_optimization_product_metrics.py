from __future__ import annotations

from decimal import Decimal

from app.cost.cache_observatory import (
    CacheLayer,
    CacheOpportunityAnalyzer,
    OptimizationRecommendationCode,
    ProductMetricsProjector,
    WorkloadKind,
    analyze_review_optimization,
    analyze_teacher_optimization,
    build_synthetic_cache_workload,
)


def test_teacher_optimization_analyzes_10000_traces_without_changing_policy() -> None:
    workload = build_synthetic_cache_workload()
    teacher_traces = tuple(
        trace
        for trace in workload.opportunity_traces
        if trace.workload == WorkloadKind.TEACHER_TURN
    )
    report = analyze_teacher_optimization(
        teacher_traces, source_classification="synthetic_offline_estimate"
    )
    assert report.trace_count == 10_000
    assert report.current_provider_prefix_hit_count > 0
    assert report.current_provider_prefix_tokens_reused > 0
    assert report.potential_stable_prefix_reuse > 0
    assert report.potential_stable_prefix_tokens > 0
    assert report.dynamic_context_miss_count > 0
    assert report.evidence_churn_count > 0
    assert report.avoidable_duplicate_calls > 0
    assert report.non_cacheable_calls > 0
    assert not report.behavior_changed
    assert all(not item.automatic_action for item in report.recommendations)
    assert {item.code for item in report.recommendations} == {
        OptimizationRecommendationCode.STABILIZE_PREFIX_BOUNDARY,
        OptimizationRecommendationCode.REDUCE_DYNAMIC_PREFIX_INPUT,
        OptimizationRecommendationCode.VERSION_EVIDENCE_AT_SOURCE,
        OptimizationRecommendationCode.DEDUPE_IDENTICAL_REQUESTS,
        OptimizationRecommendationCode.REVIEW_NON_CACHEABLE_ROUTES,
    }


def test_review_optimization_separates_local_provider_and_zero_provider_share() -> None:
    workload = build_synthetic_cache_workload()
    report = analyze_review_optimization(
        workload.review_cost_traces,
        source_classification="synthetic_offline_estimate",
    )
    assert report.evaluation_count == 10_000
    assert report.question_lookup_local_count == 10_000
    assert report.question_bank_selection_local_count == 10_000
    assert report.objective_evaluation_local_count == 7_500
    assert report.semantic_evaluation_count == 2_500
    assert report.semantic_provider_call_count > 0
    assert report.teacher_explanation_count == 2_000
    assert report.teacher_explanation_provider_call_count == 2_000
    assert report.current_zero_provider_evaluations > 0
    assert report.current_zero_provider_share is not None
    assert report.achievable_local_path_evaluations == 6_000
    assert report.achievable_zero_provider_review_share == Decimal("0.6")
    assert not report.behavior_changed
    assert all(not item.automatic_action for item in report.recommendations)


def test_product_read_model_keeps_every_cache_percentage_separate() -> None:
    workload = build_synthetic_cache_workload()
    observatory = CacheOpportunityAnalyzer().analyze(
        workload.opportunity_traces,
        source_classification="synthetic_offline_estimate",
    )
    read_model = ProductMetricsProjector().project(
        observatory.current_cache_metrics,
        generated_at_ms=2_000,
        window_started_at_ms=1_000,
        window_ended_at_ms=2_000,
    )
    snapshots = observatory.current_cache_metrics
    assert read_model.provider_cache_hit_percent == (
        snapshots.for_layer(CacheLayer.PROVIDER_PROMPT_CACHE).hit_ratio * Decimal(100)
    )
    assert read_model.local_result_hit_percent == Decimal("2.3400")
    assert read_model.parse_hit_percent == Decimal("88.200")
    assert read_model.question_hit_percent == Decimal("92.500")
    assert read_model.cost_saved_estimate is not None
    assert read_model.calls_avoided == 412
    public = read_model.to_public_dict()
    assert public["combined_cache_hit_percent"] is None
    assert (
        "intentionally reported separately"
        in public["combined_cache_hit_percent_reason"]
    )
    assert public["cost_classification"] == "estimated"
