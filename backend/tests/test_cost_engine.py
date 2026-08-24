from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.cost import (
    BudgetDecision,
    CostAggregationStatus,
    CostBudget,
    CostStatus,
    CostUnavailableReason,
    EstimationConfidence,
    LocalCacheSavingsBaseline,
    TokenRange,
    aggregate_costs,
    calculate_actual_cost,
    convert_estimate_currency,
    estimate_cost,
    evaluate_budget,
    round_money,
)
from app.providers.pricing import (
    InputTokenPricingMode,
    ProviderPricingCatalog,
    ProviderPricingSnapshot,
)
from app.providers.pricing_deepseek import (
    DEEPSEEK_V4_FLASH_OFF_PEAK,
)
from app.providers.telemetry import ProviderUsageReceipt


_UNSET = object()


def _receipt(
    *,
    provider: str = "openai_compatible",
    model: str = "deepseek-v4-flash",
    started_at: str = "2026-08-25T12:00:00+00:00",
    prompt_tokens: int | None = 1_000_000,
    completion_tokens: int | None = 100_000,
    cache_hit_tokens: int | None = 0,
    cache_miss_tokens: int | None = 1_000_000,
    total_tokens: int | None | object = _UNSET,
    provider_called: bool = True,
    local_result_cache_hit: bool | None = False,
) -> ProviderUsageReceipt:
    if total_tokens is _UNSET:
        total_tokens = (
            prompt_tokens + completion_tokens
            if prompt_tokens is not None and completion_tokens is not None
            else None
        )
    return ProviderUsageReceipt(
        provider=provider,
        model=model,
        task_type="test",
        request_id="req_cost_test",
        started_at=started_at,
        latency_ms=10,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_cache_hit_tokens=cache_hit_tokens,
        prompt_cache_miss_tokens=cache_miss_tokens,
        cache_hit_ratio=None,
        local_result_cache_hit=local_result_cache_hit,
        estimated_cost_usd=None,
        pricing_version=None,
        success=True,
        error_class=None,
        prompt_version="test-v1",
        provider_called=provider_called,
        local_cache_miss=False if local_result_cache_hit else True,
    )


def test_cache_hit_miss_and_output_are_priced_separately() -> None:
    result = calculate_actual_cost(
        _receipt(
            prompt_tokens=1_000_000,
            completion_tokens=200_000,
            cache_hit_tokens=700_000,
            cache_miss_tokens=300_000,
        )
    )

    assert result.status == CostStatus.CALCULATED
    assert result.input_cache_hit_cost == Decimal("0.0049")
    assert result.input_cache_miss_cost == Decimal("0.066")
    assert result.output_cost == Decimal("0.132")
    assert result.total_cost == Decimal("0.2029")
    assert result.provider_cache_savings == Decimal("0.1491")
    assert result.savings_classification == "estimated_savings"


def test_one_hundred_percent_provider_cache_hit() -> None:
    result = calculate_actual_cost(
        _receipt(cache_hit_tokens=1_000_000, cache_miss_tokens=0)
    )

    assert result.input_total_cost == Decimal("0.007")
    assert result.total_cost == Decimal("0.073")
    assert result.provider_cache_savings == Decimal("0.213")


def test_one_hundred_percent_provider_cache_miss() -> None:
    result = calculate_actual_cost(
        _receipt(cache_hit_tokens=0, cache_miss_tokens=1_000_000)
    )

    assert result.input_total_cost == Decimal("0.22")
    assert result.total_cost == Decimal("0.286")
    assert result.provider_cache_savings == Decimal("0")


def test_local_result_cache_avoids_call_without_fabricating_zero_tokens() -> None:
    receipt = _receipt(
        prompt_tokens=None,
        completion_tokens=None,
        cache_hit_tokens=None,
        cache_miss_tokens=None,
        provider_called=False,
        local_result_cache_hit=True,
    )
    result = calculate_actual_cost(
        receipt,
        local_cache_baseline=LocalCacheSavingsBaseline(
            amount=Decimal("0.18"),
            currency="USD",
            estimation_basis="same task's expected token range",
        ),
    )

    assert result.status == CostStatus.PROVIDER_CALL_AVOIDED_BY_LOCAL_CACHE
    assert result.total_cost == Decimal("0")
    assert result.input_tokens is None
    assert result.output_tokens is None
    assert result.local_cache_savings == Decimal("0.18")
    assert result.local_cache_estimation_basis == "same task's expected token range"
    assert result.savings_classification == "estimated_savings"


def test_provider_not_called_is_distinct_from_local_cache_hit() -> None:
    result = calculate_actual_cost(
        _receipt(
            prompt_tokens=None,
            completion_tokens=None,
            cache_hit_tokens=None,
            cache_miss_tokens=None,
            provider_called=False,
            local_result_cache_hit=False,
        )
    )

    assert result.status == CostStatus.PROVIDER_NOT_CALLED
    assert result.total_cost == Decimal("0")
    assert result.local_cache_savings is None


def test_usage_unavailable_is_unknown_instead_of_zero() -> None:
    result = calculate_actual_cost(
        _receipt(
            prompt_tokens=None,
            completion_tokens=None,
            cache_hit_tokens=None,
            cache_miss_tokens=None,
        )
    )

    assert result.status == CostStatus.COST_UNAVAILABLE
    assert result.reason == CostUnavailableReason.USAGE_UNAVAILABLE
    assert result.total_cost is None


def test_deepseek_missing_cache_breakdown_fails_safe() -> None:
    result = calculate_actual_cost(
        _receipt(cache_hit_tokens=None, cache_miss_tokens=None)
    )

    assert result.status == CostStatus.COST_UNAVAILABLE
    assert result.reason == CostUnavailableReason.CACHE_BREAKDOWN_UNAVAILABLE
    assert result.total_cost is None


def test_inconsistent_or_invalid_usage_fails_safe() -> None:
    inconsistent = calculate_actual_cost(_receipt(total_tokens=99))
    invalid = calculate_actual_cost(_receipt(completion_tokens=-1))

    assert inconsistent.status == CostStatus.COST_UNAVAILABLE
    assert inconsistent.reason == CostUnavailableReason.USAGE_INCONSISTENT
    assert inconsistent.total_cost is None
    assert invalid.status == CostStatus.COST_UNAVAILABLE
    assert invalid.reason == CostUnavailableReason.USAGE_UNAVAILABLE
    assert invalid.output_tokens is None


def test_unknown_model_pricing_fails_safe() -> None:
    result = calculate_actual_cost(_receipt(model="unpriced-model"))

    assert result.status == CostStatus.COST_UNAVAILABLE
    assert result.reason == CostUnavailableReason.PRICING_NOT_FOUND
    assert result.total_cost is None


def test_generic_contract_can_price_total_input_without_cache_fields() -> None:
    snapshot = ProviderPricingSnapshot(
        provider="generic_provider",
        provider_aliases=("openai_compatible",),
        model="generic-model",
        currency="USD",
        input_cache_hit_per_1m=Decimal("0.22"),
        input_cache_miss_per_1m=Decimal("0.22"),
        output_per_1m=Decimal("0.66"),
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source="https://provider.example/pricing",
        pricing_version="generic-v1",
        input_pricing_mode=InputTokenPricingMode.TOTAL_INPUT_AT_MISS_RATE,
    )
    result = calculate_actual_cost(
        _receipt(
            model="generic-model",
            cache_hit_tokens=None,
            cache_miss_tokens=None,
        ),
        catalog=ProviderPricingCatalog((snapshot,)),
    )

    assert result.status == CostStatus.CALCULATED
    assert result.input_cache_hit_cost is None
    assert result.input_cache_miss_cost is None
    assert result.input_total_cost == Decimal("0.22")
    assert result.total_cost == Decimal("0.286")


def test_pricing_snapshot_version_change_is_selected_by_effective_time() -> None:
    old = replace(
        DEEPSEEK_V4_FLASH_OFF_PEAK,
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        pricing_version="deepseek-test-v1",
        input_cache_miss_per_1m=Decimal("0.10"),
    )
    new = replace(
        old,
        effective_from=datetime(2026, 6, 1, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        pricing_version="deepseek-test-v2",
        input_cache_miss_per_1m=Decimal("0.20"),
    )
    catalog = ProviderPricingCatalog((old, new))

    assert catalog.resolve(
        provider="deepseek",
        model="deepseek-v4-flash",
        occurred_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    ) == old
    assert catalog.resolve(
        provider="deepseek",
        model="deepseek-v4-flash",
        occurred_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    ) == new


def test_decimal_rounding_is_explicit_and_binary_float_is_rejected() -> None:
    assert round_money(Decimal("0.0000005")) == Decimal("0.000001")
    assert round_money(Decimal("0.0000004")) == Decimal("0.000000")
    with pytest.raises(TypeError):
        round_money(0.1)  # type: ignore[arg-type]


def test_multiple_request_aggregation_preserves_savings() -> None:
    first = calculate_actual_cost(
        _receipt(cache_hit_tokens=1_000_000, cache_miss_tokens=0)
    )
    second = calculate_actual_cost(
        _receipt(cache_hit_tokens=0, cache_miss_tokens=1_000_000)
    )
    aggregate = aggregate_costs((first, second))

    assert aggregate.status == CostAggregationStatus.COMPLETE
    assert aggregate.request_count == 2
    assert aggregate.calculated_count == 2
    assert aggregate.total_cost == Decimal("0.359")
    assert aggregate.provider_cache_savings == Decimal("0.213")
    assert aggregate.savings_classification == "estimated_savings"


def test_peak_receipt_uses_peak_snapshot_prices() -> None:
    result = calculate_actual_cost(
        _receipt(started_at="2026-08-25T01:30:00+00:00")
    )

    assert result.rate_tier == "peak"
    assert result.input_total_cost == Decimal("0.44")
    assert result.output_cost == Decimal("0.132")
    assert result.total_cost == Decimal("0.572")


def _estimate():
    return estimate_cost(
        snapshot=DEEPSEEK_V4_FLASH_OFF_PEAK,
        input_tokens=TokenRange(100_000, 200_000, 300_000),
        output_tokens=TokenRange(10_000, 20_000, 30_000),
        expected_provider_calls=2,
        optimistic_cache_hit_ratio=Decimal("1"),
        expected_cache_hit_ratio=Decimal("0.5"),
        pessimistic_cache_hit_ratio=Decimal("0"),
        confidence=EstimationConfidence.MEDIUM,
        estimation_basis="historical task-family token ranges",
        expected_cache_assumption="50% Provider cache hit ratio",
    )


def test_estimate_is_an_interval_and_distinct_from_actual() -> None:
    estimate = _estimate()
    actual = calculate_actual_cost(_receipt())

    assert estimate.minimum == Decimal("0.0073")
    assert estimate.expected == Decimal("0.0359")
    assert estimate.maximum == Decimal("0.0858")
    assert estimate.expected_provider_calls == 2
    assert estimate.expected_input_tokens == 200_000
    assert estimate.expected_output_tokens == 20_000
    assert estimate.expected_cache_assumption == "50% Provider cache hit ratio"
    assert estimate.cost_kind == "estimated"
    assert actual.cost_kind == "actual"


def test_currency_conversion_requires_an_injected_time_aware_hook() -> None:
    observed: list[tuple[str, str, datetime]] = []

    def converter(
        amount: Decimal,
        source: str,
        target: str,
        as_of: datetime,
    ) -> Decimal:
        observed.append((source, target, as_of))
        return amount * Decimal("7")

    as_of = datetime(2026, 8, 25, tzinfo=timezone.utc)
    converted = convert_estimate_currency(
        _estimate(),
        target_currency="CNY",
        as_of=as_of,
        converter=converter,
    )

    assert converted.currency == "CNY"
    assert converted.expected == Decimal("0.2513")
    assert observed == [("USD", "CNY", as_of)] * 3


@pytest.mark.parametrize(
    ("maximum_estimated_cost", "expected_decision"),
    [
        (Decimal("0.20"), BudgetDecision.ALLOW),
        (Decimal("0.10"), BudgetDecision.WARN),
        (Decimal("0.08"), BudgetDecision.STOP),
    ],
)
def test_budget_allow_warn_stop(
    maximum_estimated_cost: Decimal,
    expected_decision: BudgetDecision,
) -> None:
    budget = CostBudget(
        currency="USD",
        maximum_estimated_cost=maximum_estimated_cost,
        maximum_actual_cost=Decimal("1.00"),
        warning_threshold=Decimal("0.80"),
        hard_stop_threshold=Decimal("1.00"),
    )

    result = evaluate_budget(budget, estimate=_estimate())

    assert result.decision == expected_decision


def test_budget_fails_closed_when_actual_cost_is_unavailable() -> None:
    budget = CostBudget(
        currency="USD",
        maximum_estimated_cost=Decimal("1"),
        maximum_actual_cost=Decimal("1"),
        warning_threshold=Decimal("0.8"),
        hard_stop_threshold=Decimal("1"),
    )
    unknown = calculate_actual_cost(_receipt(model="unpriced-model"))

    result = evaluate_budget(budget, actual=unknown)

    assert result.decision == BudgetDecision.STOP
    assert result.reason == "BUDGET_COST_UNAVAILABLE"
