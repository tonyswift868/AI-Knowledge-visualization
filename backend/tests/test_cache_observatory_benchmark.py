from __future__ import annotations

import json
from decimal import Decimal

from app.cost.cache_observatory import (
    CacheLayer,
    ReviewAction,
    SyntheticBenchmarkConfig,
    TeacherCostMode,
    run_synthetic_cache_benchmark,
)
from app.cost.cache_observatory.benchmark import main


def test_default_benchmark_runs_10000_teacher_and_review_workloads_offline() -> None:
    result = run_synthetic_cache_benchmark()
    report = result.opportunity_report

    assert result.config.teacher_turns == 10_000
    assert result.config.review_evaluations == 10_000
    assert report.trace_count == 20_000
    assert report.teacher_turn_traces == 10_000
    assert report.review_evaluation_traces == 10_000
    assert result.real_provider_calls == 0
    assert result.data_classification == "synthetic_offline_estimate"
    assert "not a real Provider hit rate" in result.disclaimer
    assert "official Provider bill" in result.disclaimer
    assert result.elapsed_ms >= Decimal(0)

    snapshots = report.current_cache_metrics.layers
    assert {item.layer for item in snapshots} == set(CacheLayer)
    for snapshot in snapshots:
        assert snapshot.lookup_count > 0
        assert snapshot.hit_count > 0
        assert snapshot.miss_count > 0
        assert snapshot.synthetic_event_count == snapshot.lookup_count
        assert snapshot.savings_classification == "estimated"
    assert report.current_cache_metrics.to_public_dict()["combined_hit_ratio"] is None
    assert report.potential_stable_prefix_reuse > 0
    assert report.potential_stable_prefix_tokens > 0
    assert report.potential_stable_prefix_estimated_savings is not None
    assert report.avoidable_duplicate_calls > 0
    assert report.avoidable_duplicate_input_tokens > 0
    assert report.avoidable_duplicate_estimated_cost is not None
    assert report.non_cacheable_dynamic_calls > 0
    assert report.estimates_classification == "estimated"
    assert not report.behavior_changed


def test_benchmark_profiles_fast_deep_and_every_review_cost_plane() -> None:
    result = run_synthetic_cache_benchmark()
    teacher = result.teacher_profile
    review = result.review_profile

    assert teacher.fast.mode == TeacherCostMode.FAST
    assert teacher.deep.mode == TeacherCostMode.DEEP
    assert teacher.fast.turn_count == 7_500
    assert teacher.deep.turn_count == 2_500
    assert teacher.fast.turn_count + teacher.deep.turn_count == 10_000
    assert teacher.fast.provider_call_count < teacher.fast.turn_count
    assert teacher.deep.provider_call_count < teacher.deep.turn_count
    assert teacher.fast.cached_input_tokens > 0
    assert teacher.deep.cached_input_tokens > 0
    assert teacher.fast.most_expensive_component is not None
    assert teacher.deep.most_expensive_component is not None
    assert teacher.fast.ttft.observed_count == teacher.fast.provider_call_count
    assert teacher.deep.ttft.observed_count == teacher.deep.provider_call_count
    assert teacher.fast.synthetic_turn_count == teacher.fast.turn_count
    assert teacher.deep.synthetic_turn_count == teacher.deep.turn_count

    assert review.evaluation_count == 10_000
    assert {item.action for item in review.actions} == set(ReviewAction)
    assert review.for_action(ReviewAction.QUESTION_READ).provider_call_count == 0
    assert (
        review.for_action(ReviewAction.QUESTION_BANK_SELECTION).provider_call_count == 0
    )
    assert review.for_action(ReviewAction.OBJECTIVE_EVALUATION).provider_call_count == 0
    assert review.for_action(ReviewAction.WEAK_QUEUE_UPDATE).provider_call_count == 0
    assert review.for_action(ReviewAction.FSRS_UPDATE).provider_call_count == 0
    assert review.for_action(ReviewAction.SEMANTIC_EVALUATION).provider_call_count > 0
    assert review.for_action(ReviewAction.TEACHER_EXPLANATION).provider_call_count > 0
    assert review.zero_provider_evaluation_count > 0
    assert review.most_expensive_action in {
        ReviewAction.SEMANTIC_EVALUATION,
        ReviewAction.TEACHER_EXPLANATION,
    }
    assert review.cost_classification == "estimated"


def test_benchmark_variants_drive_reuse_and_miss_explanations() -> None:
    result = run_synthetic_cache_benchmark(
        SyntheticBenchmarkConfig(
            teacher_turns=2_000,
            review_evaluations=2_000,
            stable_prefix_variants=8,
            learner_context_variants=37,
            evidence_variants=19,
            question_variants=41,
        )
    )
    report = result.opportunity_report
    provider = report.current_cache_metrics.for_layer(CacheLayer.PROVIDER_PROMPT_CACHE)
    evidence = report.current_cache_metrics.for_layer(CacheLayer.EVIDENCE_CACHE)
    questions = report.current_cache_metrics.for_layer(CacheLayer.QUESTION_BANK_CACHE)
    evaluations = report.current_cache_metrics.for_layer(
        CacheLayer.EVALUATION_ARTIFACT_CACHE
    )
    assert provider.hit_count > provider.miss_count
    assert evidence.hit_count > 0
    assert questions.hit_count > 0
    assert evaluations.hit_count > 0
    assert provider.miss_reasons
    assert evidence.miss_reasons
    assert report.potential_stable_prefix_reuse > 0
    assert report.avoidable_duplicate_calls > 0


def test_benchmark_cli_emits_json_with_synthetic_warning(capsys) -> None:
    assert main(["--teacher-turns", "20", "--review-evaluations", "20"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["configuration"]["teacher_turns"] == 20
    assert payload["configuration"]["review_evaluations"] == 20
    assert payload["real_provider_calls"] == 0
    assert payload["data_classification"] == "synthetic_offline_estimate"
    assert "not a real Provider hit rate" in payload["disclaimer"]
    assert payload["cache_opportunity"]["behavior_changed"] is False
