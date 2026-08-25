from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.cost import (
    BudgetDecision,
    CostEstimate,
    EstimationConfidence,
    LocalCacheSavingsBaseline,
)
from app.cost.ledger import ActualCostLedger, LedgerIdempotencyConflict
from app.cost.runtime_budget import (
    CostAnomalyCode,
    ExecutionBudgetEnvelope,
    ExecutionBudgetGate,
    UnknownCostPolicy,
)
from app.model_routing import ProviderCapabilityProfile, route_provider
from app.providers.pricing import (
    InputTokenPricingMode,
    ProviderPricingCatalog,
    ProviderPricingSnapshot,
)
from app.providers.telemetry import ProviderUsageReceipt


def _routing_decision():
    return route_provider(
        (
            ProviderCapabilityProfile(
                provider="openai_compatible",
                model="deepseek-v4-flash",
                text_generation=True,
            ),
        ),
        "free_chat",
    )


def _estimate(
    minimum: str = "0.10",
    expected: str = "0.15",
    maximum: str = "0.20",
) -> CostEstimate:
    return CostEstimate(
        minimum=Decimal(minimum),
        expected=Decimal(expected),
        maximum=Decimal(maximum),
        currency="USD",
        confidence=EstimationConfidence.MEDIUM,
        estimation_basis="operation token range",
        expected_provider_calls=1,
        expected_input_tokens=1_000_000,
        expected_output_tokens=100_000,
        expected_cache_assumption="conservative cache range",
        pricing_version="deepseek-v4-flash-0731-usd-2026-08-24",
    )


def _receipt(
    *,
    request_id: str = "req-budget-1",
    started_at: str = "2026-08-25T12:00:00+00:00",
    provider: str = "openai_compatible",
    model: str = "deepseek-v4-flash",
    prompt_tokens: int | None = 1_000_000,
    completion_tokens: int | None = 100_000,
    cache_hit_tokens: int | None = 0,
    cache_miss_tokens: int | None = 1_000_000,
    provider_called: bool = True,
    local_result_cache_hit: bool | None = False,
) -> ProviderUsageReceipt:
    total_tokens = (
        prompt_tokens + completion_tokens
        if prompt_tokens is not None and completion_tokens is not None
        else None
    )
    return ProviderUsageReceipt(
        provider=provider,
        model=model,
        task_type="budget-test",
        request_id=request_id,
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
        prompt_version="budget-test-v1",
        provider_called=provider_called,
        local_cache_miss=False if local_result_cache_hit else True,
    )


def _envelope(
    *,
    run_id: str = "run-1",
    operation_id: str = "op-1",
    estimate: CostEstimate | None = None,
    warning: str = "0.70",
    hard_stop: str = "0.90",
    user_max: str = "1.00",
    local_cache_hit: bool = False,
    unknown_policy: UnknownCostPolicy = UnknownCostPolicy.STOP,
    minimum_cache_hit_ratio: Decimal | None = None,
) -> ExecutionBudgetEnvelope:
    return ExecutionBudgetEnvelope.from_routing_decision(
        run_id=run_id,
        operation_id=operation_id,
        routing_decision=_routing_decision(),
        currency="USD",
        user_max_budget=Decimal(user_max),
        warning_threshold=Decimal(warning),
        hard_stop_threshold=Decimal(hard_stop),
        estimate=estimate,
        local_result_cache_hit=local_cache_hit,
        unknown_cost_policy=unknown_policy,
        minimum_expected_cache_hit_ratio=minimum_cache_hit_ratio,
    )


def test_pre_execution_gate_consumes_router_decision_read_only() -> None:
    decision = _routing_decision()
    before = decision.to_public_dict()
    envelope = ExecutionBudgetEnvelope.from_routing_decision(
        run_id="run-route",
        operation_id="op-route",
        routing_decision=decision,
        currency="USD",
        user_max_budget=Decimal("1"),
        warning_threshold=Decimal("0.7"),
        hard_stop_threshold=Decimal("0.9"),
        estimate=_estimate(),
    )

    result = ExecutionBudgetGate().evaluate(envelope, ActualCostLedger())

    assert result.decision == BudgetDecision.ALLOW
    assert result.projected_minimum == Decimal("0.10")
    assert result.projected_expected == Decimal("0.15")
    assert result.projected_maximum == Decimal("0.20")
    assert result.selected_provider == "openai_compatible"
    assert result.selected_model == "deepseek-v4-flash"
    assert decision.to_public_dict() == before


def test_unavailable_router_decision_stops_without_mutating_router() -> None:
    decision = route_provider((), "free_chat")
    before = decision.to_public_dict()
    envelope = ExecutionBudgetEnvelope.from_routing_decision(
        run_id="run-route-unavailable",
        operation_id="op-route-unavailable",
        routing_decision=decision,
        currency="USD",
        user_max_budget=Decimal("1"),
        warning_threshold=Decimal("0.7"),
        hard_stop_threshold=Decimal("0.9"),
        estimate=_estimate(),
    )

    result = ExecutionBudgetGate().evaluate(envelope, ActualCostLedger())

    assert result.decision == BudgetDecision.STOP
    assert "ROUTING_UNAVAILABLE" in result.reasons
    assert decision.to_public_dict() == before


def test_paid_execution_without_estimate_stops_by_default() -> None:
    result = ExecutionBudgetGate().evaluate(
        _envelope(
            run_id="run-no-estimate",
            operation_id="op-no-estimate",
            estimate=None,
        ),
        ActualCostLedger(),
    )

    assert result.decision == BudgetDecision.STOP
    assert result.projected_maximum is None
    assert "REQUIRED_PROVIDER_COST_UNESTIMATED" in result.reasons
    assert "UNKNOWN_COST_STOP_POLICY" in result.reasons


def test_multi_step_job_accumulates_actual_cost_before_next_decision() -> None:
    ledger = ActualCostLedger()
    gate = ExecutionBudgetGate()
    ledger.ingest(
        run_id="run-multi",
        operation_id="op-1",
        receipt=_receipt(request_id="req-multi-1"),
    )

    after_first = gate.evaluate(
        _envelope(
            run_id="run-multi",
            operation_id="op-2",
            estimate=_estimate("0.20", "0.25", "0.286"),
            warning="0.50",
            hard_stop="0.80",
        ),
        ledger,
    )
    ledger.ingest(
        run_id="run-multi",
        operation_id="op-2",
        receipt=_receipt(
            request_id="req-multi-2",
            started_at="2026-08-25T12:00:01+00:00",
        ),
    )
    before_third = gate.evaluate(
        _envelope(
            run_id="run-multi",
            operation_id="op-3",
            estimate=_estimate("0.20", "0.25", "0.286"),
            warning="0.50",
            hard_stop="0.80",
        ),
        ledger,
    )

    assert after_first.decision == BudgetDecision.WARN
    assert after_first.known_actual_cost_subtotal == Decimal("0.286")
    assert after_first.projected_maximum == Decimal("0.572")
    assert before_third.decision == BudgetDecision.STOP
    assert before_third.known_actual_cost_subtotal == Decimal("0.572")
    assert before_third.projected_maximum == Decimal("0.858")
    run_snapshot = ledger.snapshot(run_id="run-multi")
    assert run_snapshot.operation_count == 2
    assert run_snapshot.provider_calls == 2


def test_local_cache_avoided_call_is_zero_actual_with_estimated_savings() -> None:
    ledger = ActualCostLedger()
    receipt = _receipt(
        prompt_tokens=None,
        completion_tokens=None,
        cache_hit_tokens=None,
        cache_miss_tokens=None,
        provider_called=False,
        local_result_cache_hit=True,
    )
    ledger.ingest(
        run_id="run-cache",
        operation_id="op-cache",
        receipt=receipt,
        local_cache_baseline=LocalCacheSavingsBaseline(
            amount=Decimal("0.286"),
            currency="USD",
            estimation_basis="equivalent uncached operation",
        ),
    )

    snapshot = ledger.snapshot(run_id="run-cache")
    result = ExecutionBudgetGate().evaluate(
        _envelope(
            run_id="run-cache",
            operation_id="op-cache",
            estimate=None,
            local_cache_hit=True,
        ),
        ledger,
    )

    assert snapshot.provider_calls == 0
    assert snapshot.local_cache_avoided_calls == 1
    assert snapshot.actual_calculated_provider_cost == Decimal("0")
    assert snapshot.local_cache_savings == Decimal("0.286")
    assert snapshot.estimated_savings == Decimal("0.286")
    assert snapshot.savings_classification == "estimated_savings"
    assert result.decision == BudgetDecision.ALLOW
    assert result.projected_maximum == Decimal("0")


def test_zero_cost_local_cache_operation_is_allowed_under_zero_budget() -> None:
    ledger = ActualCostLedger()
    ledger.ingest(
        run_id="run-zero-budget",
        operation_id="op-zero-budget",
        receipt=_receipt(
            prompt_tokens=None,
            completion_tokens=None,
            cache_hit_tokens=None,
            cache_miss_tokens=None,
            provider_called=False,
            local_result_cache_hit=True,
        ),
    )

    result = ExecutionBudgetGate().evaluate(
        _envelope(
            run_id="run-zero-budget",
            operation_id="op-zero-budget",
            estimate=None,
            warning="0",
            hard_stop="0",
            user_max="0",
            local_cache_hit=True,
        ),
        ledger,
    )

    assert result.decision == BudgetDecision.ALLOW
    assert result.actual_accumulated_cost == Decimal("0")


def test_one_hundred_percent_provider_cache_hit_is_recorded() -> None:
    ledger = ActualCostLedger()
    ledger.ingest(
        run_id="run-hit",
        operation_id="op-hit",
        receipt=_receipt(cache_hit_tokens=1_000_000, cache_miss_tokens=0),
    )

    snapshot = ledger.snapshot(run_id="run-hit")

    assert snapshot.provider_calls == 1
    assert snapshot.reported_input_cache_hit_tokens == 1_000_000
    assert snapshot.reported_input_cache_miss_tokens == 0
    assert snapshot.reported_output_tokens == 100_000
    assert snapshot.actual_calculated_provider_cost == Decimal("0.073")
    assert snapshot.provider_cache_savings == Decimal("0.213")
    assert snapshot.cache_hit_ratio == Decimal("1")


def test_unknown_model_remains_unknown_and_default_gate_stops() -> None:
    ledger = ActualCostLedger()
    ledger.ingest(
        run_id="run-unknown",
        operation_id="op-unknown",
        receipt=_receipt(model="unknown-priced-model"),
    )

    snapshot = ledger.snapshot(run_id="run-unknown")
    result = ExecutionBudgetGate().evaluate(
        _envelope(
            run_id="run-unknown",
            operation_id="op-next",
            estimate=_estimate(),
        ),
        ledger,
    )

    assert snapshot.known_actual_cost_subtotal == Decimal("0")
    assert snapshot.actual_calculated_provider_cost is None
    assert snapshot.unknown_receipts == 1
    assert snapshot.unpriced_receipts == 1
    assert result.decision == BudgetDecision.STOP
    assert result.unknown_cost_present is True
    assert "UNKNOWN_COST_STOP_POLICY" in result.reasons
    assert CostAnomalyCode.UNKNOWN_PRICE_PRESENT in {
        anomaly.code for anomaly in result.anomalies
    }


def test_unknown_cost_policy_can_warn_without_fabricating_actual_total() -> None:
    ledger = ActualCostLedger()
    ledger.ingest(
        run_id="run-unknown-warn",
        operation_id="op-unknown",
        receipt=_receipt(model="unknown-priced-model"),
    )

    result = ExecutionBudgetGate().evaluate(
        _envelope(
            run_id="run-unknown-warn",
            operation_id="op-next",
            estimate=_estimate(),
            unknown_policy=UnknownCostPolicy.WARN,
        ),
        ledger,
    )

    assert result.decision == BudgetDecision.WARN
    assert result.actual_accumulated_cost is None
    assert result.known_actual_cost_subtotal == Decimal("0")


def test_price_snapshot_version_switch_is_preserved_in_ledger() -> None:
    old = ProviderPricingSnapshot(
        provider="generic",
        provider_aliases=("openai_compatible",),
        model="versioned-model",
        currency="USD",
        input_cache_hit_per_1m=Decimal("0.10"),
        input_cache_miss_per_1m=Decimal("0.10"),
        output_per_1m=Decimal("0"),
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source="https://provider.example/pricing",
        pricing_version="generic-v1",
        input_pricing_mode=InputTokenPricingMode.TOTAL_INPUT_AT_MISS_RATE,
    )
    new = replace(
        old,
        effective_from=datetime(2026, 6, 1, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        pricing_version="generic-v2",
        input_cache_hit_per_1m=Decimal("0.20"),
        input_cache_miss_per_1m=Decimal("0.20"),
    )
    ledger = ActualCostLedger(catalog=ProviderPricingCatalog((old, new)))
    ledger.ingest(
        run_id="run-version",
        operation_id="op-old",
        receipt=_receipt(
            request_id="req-version-old",
            started_at="2026-03-01T12:00:00+00:00",
            model="versioned-model",
            completion_tokens=0,
            cache_hit_tokens=None,
            cache_miss_tokens=None,
        ),
    )
    ledger.ingest(
        run_id="run-version",
        operation_id="op-new",
        receipt=_receipt(
            request_id="req-version-new",
            started_at="2026-07-01T12:00:00+00:00",
            model="versioned-model",
            completion_tokens=0,
            cache_hit_tokens=None,
            cache_miss_tokens=None,
        ),
    )

    snapshot = ledger.snapshot(run_id="run-version")

    assert snapshot.actual_calculated_provider_cost == Decimal("0.30")
    assert snapshot.pricing_versions == ("generic-v1", "generic-v2")


def test_actual_above_expected_high_generates_diagnostic_only_anomaly() -> None:
    ledger = ActualCostLedger()
    ledger.ingest(
        run_id="run-anomaly",
        operation_id="op-anomaly",
        receipt=_receipt(),
    )

    result = ExecutionBudgetGate().evaluate(
        _envelope(
            run_id="run-anomaly",
            operation_id="op-anomaly",
            estimate=_estimate("0.10", "0.15", "0.20"),
        ),
        ledger,
    )

    anomaly = next(
        item
        for item in result.anomalies
        if item.code == CostAnomalyCode.ACTUAL_EXCEEDS_EXPECTED_HIGH
    )
    assert anomaly.observed == Decimal("0.286")
    assert anomaly.expected == Decimal("0.20")
    assert anomaly.diagnostics_only is True


@pytest.mark.parametrize(
    ("maximum", "expected_decision"),
    [
        ("0.499999", BudgetDecision.ALLOW),
        ("0.50", BudgetDecision.WARN),
        ("0.80", BudgetDecision.STOP),
    ],
)
def test_warn_to_stop_boundaries(
    maximum: str,
    expected_decision: BudgetDecision,
) -> None:
    result = ExecutionBudgetGate().evaluate(
        _envelope(
            run_id="run-boundary-" + maximum,
            operation_id="op-boundary",
            estimate=_estimate("0.10", "0.20", maximum),
            warning="0.50",
            hard_stop="0.80",
        ),
        ActualCostLedger(),
    )

    assert result.decision == expected_decision


def test_retry_receipts_and_redelivery_are_each_counted_exactly_once() -> None:
    ledger = ActualCostLedger()
    first_receipt = _receipt(request_id="req-retry")
    first = ledger.ingest(
        run_id="run-retry",
        operation_id="op-retry",
        receipt=first_receipt,
        idempotency_key="event-retry-1",
    )
    duplicate_first = ledger.ingest(
        run_id="run-retry",
        operation_id="op-retry",
        receipt=first_receipt,
        idempotency_key="event-retry-1",
    )
    retry_receipt = replace(
        first_receipt,
        started_at="2026-08-25T12:00:01+00:00",
        latency_ms=11,
    )
    retry = ledger.ingest(
        run_id="run-retry",
        operation_id="op-retry",
        receipt=retry_receipt,
        idempotency_key="event-retry-2",
    )
    duplicate_retry = ledger.ingest(
        run_id="run-retry",
        operation_id="op-retry",
        receipt=retry_receipt,
        idempotency_key="event-retry-2",
    )

    snapshot = ledger.snapshot(run_id="run-retry")

    assert first.accepted is True
    assert retry.accepted is True
    assert duplicate_first.duplicate is True
    assert duplicate_retry.duplicate is True
    assert snapshot.receipt_count == 2
    assert snapshot.provider_calls == 2
    assert snapshot.duplicate_ingestions == 2
    assert snapshot.actual_calculated_provider_cost == Decimal("0.572")


def test_idempotency_key_reuse_with_different_receipt_is_rejected() -> None:
    ledger = ActualCostLedger()
    ledger.ingest(
        run_id="run-idempotent",
        operation_id="op-idempotent",
        receipt=_receipt(request_id="req-idempotent-1"),
        idempotency_key="event-idempotent",
    )

    with pytest.raises(LedgerIdempotencyConflict):
        ledger.ingest(
            run_id="run-idempotent",
            operation_id="op-idempotent",
            receipt=_receipt(
                request_id="req-idempotent-2",
                started_at="2026-08-25T12:00:02+00:00",
            ),
            idempotency_key="event-idempotent",
        )


def test_concurrent_receipt_redelivery_is_counted_once() -> None:
    ledger = ActualCostLedger()
    receipt = _receipt(request_id="req-concurrent")

    def ingest_once(_index: int):
        return ledger.ingest(
            run_id="run-concurrent",
            operation_id="op-concurrent",
            receipt=receipt,
            idempotency_key="event-concurrent",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(ingest_once, range(8)))

    snapshot = ledger.snapshot(run_id="run-concurrent")

    assert sum(result.accepted for result in results) == 1
    assert sum(result.duplicate for result in results) == 7
    assert snapshot.provider_calls == 1
    assert snapshot.receipt_count == 1
    assert snapshot.duplicate_ingestions == 7
    assert snapshot.actual_calculated_provider_cost == Decimal("0.286")


def test_decimal_safe_aggregation_of_tiny_costs() -> None:
    snapshot = ProviderPricingSnapshot(
        provider="tiny",
        model="tiny-model",
        currency="USD",
        input_cache_hit_per_1m=Decimal("0.10"),
        input_cache_miss_per_1m=Decimal("0.10"),
        output_per_1m=Decimal("0"),
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source="https://provider.example/pricing",
        pricing_version="tiny-v1",
        input_pricing_mode=InputTokenPricingMode.TOTAL_INPUT_AT_MISS_RATE,
    )
    ledger = ActualCostLedger(catalog=ProviderPricingCatalog((snapshot,)))
    for index in range(10):
        ledger.ingest(
            run_id="run-decimal",
            operation_id="op-decimal",
            receipt=_receipt(
                request_id=f"req-decimal-{index}",
                started_at=f"2026-08-25T12:00:{index:02d}+00:00",
                provider="tiny",
                model="tiny-model",
                prompt_tokens=1,
                completion_tokens=0,
                cache_hit_tokens=None,
                cache_miss_tokens=None,
            ),
        )

    snapshot_result = ledger.snapshot(run_id="run-decimal")

    assert snapshot_result.actual_calculated_provider_cost == Decimal("0.000001")
    assert isinstance(snapshot_result.actual_calculated_provider_cost, Decimal)


def test_cache_hit_drop_and_budget_receipt_are_diagnostics_only() -> None:
    ledger = ActualCostLedger()
    ledger.ingest(
        run_id="run-cache-drop",
        operation_id="op-cache-drop",
        receipt=_receipt(cache_hit_tokens=0, cache_miss_tokens=1_000_000),
    )

    result = ExecutionBudgetGate().evaluate(
        _envelope(
            run_id="run-cache-drop",
            operation_id="op-next",
            estimate=_estimate(),
            minimum_cache_hit_ratio=Decimal("0.50"),
        ),
        ledger,
    )
    public = result.to_public_dict()

    assert CostAnomalyCode.CACHE_HIT_RATE_DROP in {
        anomaly.code for anomaly in result.anomalies
    }
    assert all(anomaly.diagnostics_only for anomaly in result.anomalies)
    assert public["savings_classification"] == "estimated_savings"
    assert "official" not in str(public).casefold()
