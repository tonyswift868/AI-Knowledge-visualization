from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from app.cost.cache_observatory import (
    CacheExperiment,
    CacheObservatoryError,
    CacheScenario,
    run_cache_experiment,
    standard_cache_scenarios,
)


def _experiment() -> CacheExperiment:
    return CacheExperiment(
        experiment_id="teacher-cost-lab",
        request_count=1_000,
        provider_input_tokens_per_request=1_000,
        stable_prefix_tokens_per_request=600,
        provider_output_tokens_per_request=200,
        input_cache_hit_per_1m=Decimal("0.014"),
        input_cache_miss_per_1m=Decimal("0.44"),
        output_per_1m=Decimal("1.32"),
        currency="USD",
        scenarios=standard_cache_scenarios(),
        pricing_version="synthetic-pricing-v1",
    )


def test_standard_scenarios_cover_0_30_60_90_provider_and_different_local_hits() -> (
    None
):
    scenarios = standard_cache_scenarios()
    assert len(scenarios) == 8
    assert {item.provider_prompt_cache_hit_ratio for item in scenarios} == {
        Decimal(0),
        Decimal("0.30"),
        Decimal("0.60"),
        Decimal("0.90"),
    }
    assert {item.local_result_cache_hit_ratio for item in scenarios} == {
        Decimal(0),
        Decimal("0.20"),
    }


def test_experiment_reconciles_token_call_and_cost_savings() -> None:
    report = run_cache_experiment(_experiment())
    baseline = report.for_scenario("provider-0-local-0")
    optimized = report.for_scenario("provider-0.90-local-0.20")

    assert report.baseline_estimated_cost == Decimal("0.70400")
    assert baseline.provider_call_count == 1_000
    assert baseline.calls_avoided == 0
    assert baseline.provider_cache_tokens_reused == 0
    assert baseline.estimated_cost_difference == 0

    assert optimized.calls_avoided == 200
    assert optimized.provider_call_count == 800
    assert optimized.provider_prompt_cache_hit_calls == 720
    assert optimized.provider_prompt_cache_miss_calls == 80
    assert optimized.provider_cache_tokens_reused == 432_000
    assert optimized.local_input_tokens_avoided == 200_000
    assert optimized.local_output_tokens_avoided == 40_000
    assert optimized.tokens_avoided == 672_000
    assert optimized.estimated_cost_difference == (
        optimized.estimated_provider_cache_savings
        + optimized.estimated_local_cache_savings
    )
    assert optimized.estimated_cost < baseline.estimated_cost
    assert report.most_cost_efficient_scenario_id == optimized.scenario.scenario_id


def test_experiment_is_offline_estimate_and_never_changes_execution() -> None:
    report = run_cache_experiment(_experiment())
    public = report.to_public_dict()
    assert report.real_provider_calls == 0
    assert not report.behavior_changed
    assert report.classification == "synthetic_offline_estimate"
    assert "not an official Provider bill" in report.disclaimer
    assert public["real_provider_calls"] == 0
    assert public["behavior_changed"] is False
    assert all(
        item["classification"] == "synthetic_estimate" for item in public["scenarios"]
    )


def test_experiment_contract_rejects_false_precision_or_unsafe_assumptions() -> None:
    experiment = _experiment()
    with pytest.raises(CacheObservatoryError):
        replace(experiment, stable_prefix_tokens_per_request=1_001)
    with pytest.raises(CacheObservatoryError):
        replace(experiment, input_cache_hit_per_1m=Decimal("0.45"))
    with pytest.raises(CacheObservatoryError):
        replace(
            experiment,
            scenarios=(
                CacheScenario("duplicate", Decimal(0), Decimal(0)),
                CacheScenario("duplicate", Decimal("0.30"), Decimal(0)),
            ),
        )
    with pytest.raises(CacheObservatoryError):
        CacheScenario("invalid", Decimal("1.01"), Decimal(0))
    with pytest.raises(TypeError):
        CacheScenario("float", 0.3, Decimal(0))  # type: ignore[arg-type]
