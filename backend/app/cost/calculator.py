from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from app.cost.contracts import (
    CostAggregate,
    CostAggregationStatus,
    CostBreakdown,
    CostStatus,
    CostUnavailableReason,
    LocalCacheSavingsBaseline,
)
from app.providers.pricing import (
    InputTokenPricingMode,
    ProviderPricingCatalog,
    ProviderPricingSnapshot,
)
from app.providers.pricing_deepseek import DEEPSEEK_PRICING_CATALOG
from app.providers.telemetry import ProviderUsageReceipt


TOKENS_PER_MILLION = Decimal("1000000")


def round_money(
    amount: Decimal,
    *,
    quantum: Decimal = Decimal("0.000001"),
) -> Decimal:
    """Apply explicit half-up presentation rounding without binary floats."""

    if not isinstance(amount, Decimal) or not isinstance(quantum, Decimal):
        raise TypeError("amount and quantum must be Decimal")
    if not amount.is_finite() or not quantum.is_finite() or quantum <= 0:
        raise ValueError("amount must be finite and quantum must be positive")
    return amount.quantize(quantum, rounding=ROUND_HALF_UP)


def _token_cost(tokens: int, rate_per_1m: Decimal) -> Decimal:
    return Decimal(tokens) * rate_per_1m / TOKENS_PER_MILLION


def _valid_token_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _safe_token_count(value: object) -> int | None:
    return value if _valid_token_count(value) else None


def _parse_receipt_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _unavailable(
    receipt: ProviderUsageReceipt,
    reason: CostUnavailableReason,
    snapshot: ProviderPricingSnapshot | None = None,
) -> CostBreakdown:
    return CostBreakdown(
        provider=receipt.provider,
        model=receipt.model,
        status=CostStatus.COST_UNAVAILABLE,
        reason=reason,
        currency=snapshot.currency if snapshot else None,
        pricing_version=snapshot.pricing_version if snapshot else None,
        rate_tier=snapshot.rate_tier if snapshot else None,
        prompt_cache_hit_tokens=_safe_token_count(
            receipt.prompt_cache_hit_tokens
        ),
        prompt_cache_miss_tokens=_safe_token_count(
            receipt.prompt_cache_miss_tokens
        ),
        input_tokens=_safe_token_count(receipt.prompt_tokens),
        output_tokens=_safe_token_count(receipt.completion_tokens),
        input_cache_hit_cost=None,
        input_cache_miss_cost=None,
        input_total_cost=None,
        output_cost=None,
        total_cost=None,
        provider_cache_savings=None,
        local_cache_savings=None,
        savings_classification=None,
    )


def _resolve_snapshot_if_possible(
    receipt: ProviderUsageReceipt,
    catalog: ProviderPricingCatalog,
) -> ProviderPricingSnapshot | None:
    occurred_at = _parse_receipt_timestamp(receipt.started_at)
    if occurred_at is None:
        return None
    return catalog.resolve(
        provider=receipt.provider,
        model=receipt.model,
        occurred_at=occurred_at,
    )


def calculate_actual_cost(
    receipt: ProviderUsageReceipt,
    *,
    catalog: ProviderPricingCatalog = DEEPSEEK_PRICING_CATALOG,
    local_cache_baseline: LocalCacheSavingsBaseline | None = None,
) -> CostBreakdown:
    """Convert one frozen telemetry receipt into a fail-safe actual cost."""

    if not receipt.provider_called:
        snapshot = _resolve_snapshot_if_possible(receipt, catalog)
        is_local_cache = receipt.local_result_cache_hit is True
        local_savings: Decimal | None = None
        estimation_basis: str | None = None
        currency = snapshot.currency if snapshot else None
        if is_local_cache and local_cache_baseline is not None:
            if currency is None or currency == local_cache_baseline.currency:
                currency = local_cache_baseline.currency
                local_savings = local_cache_baseline.amount
                estimation_basis = local_cache_baseline.estimation_basis
        return CostBreakdown(
            provider=receipt.provider,
            model=receipt.model,
            status=(
                CostStatus.PROVIDER_CALL_AVOIDED_BY_LOCAL_CACHE
                if is_local_cache
                else CostStatus.PROVIDER_NOT_CALLED
            ),
            currency=currency,
            pricing_version=snapshot.pricing_version if snapshot else None,
            rate_tier=snapshot.rate_tier if snapshot else None,
            prompt_cache_hit_tokens=None,
            prompt_cache_miss_tokens=None,
            input_tokens=None,
            output_tokens=None,
            input_cache_hit_cost=Decimal("0"),
            input_cache_miss_cost=Decimal("0"),
            input_total_cost=Decimal("0"),
            output_cost=Decimal("0"),
            total_cost=Decimal("0"),
            provider_cache_savings=None,
            local_cache_savings=local_savings,
            savings_classification=(
                "estimated_savings" if local_savings is not None else None
            ),
            local_cache_estimation_basis=estimation_basis,
        )

    occurred_at = _parse_receipt_timestamp(receipt.started_at)
    if occurred_at is None:
        return _unavailable(
            receipt,
            CostUnavailableReason.INVALID_RECEIPT_TIMESTAMP,
        )
    snapshot = catalog.resolve(
        provider=receipt.provider,
        model=receipt.model,
        occurred_at=occurred_at,
    )
    if snapshot is None:
        return _unavailable(receipt, CostUnavailableReason.PRICING_NOT_FOUND)

    output_tokens = receipt.completion_tokens
    if not _valid_token_count(output_tokens):
        return _unavailable(
            receipt,
            CostUnavailableReason.USAGE_UNAVAILABLE,
            snapshot,
        )

    hit_tokens: int | None = None
    miss_tokens: int | None = None
    input_hit_cost: Decimal | None = None
    input_miss_cost: Decimal | None = None
    provider_cache_savings: Decimal | None = None

    if snapshot.input_pricing_mode == InputTokenPricingMode.CACHE_HIT_MISS_REQUIRED:
        if not _valid_token_count(
            receipt.prompt_cache_hit_tokens
        ) or not _valid_token_count(receipt.prompt_cache_miss_tokens):
            return _unavailable(
                receipt,
                CostUnavailableReason.CACHE_BREAKDOWN_UNAVAILABLE,
                snapshot,
            )
        hit_tokens = receipt.prompt_cache_hit_tokens
        miss_tokens = receipt.prompt_cache_miss_tokens
        input_tokens = hit_tokens + miss_tokens
        if (
            receipt.prompt_tokens is not None
            and (
                not _valid_token_count(receipt.prompt_tokens)
                or receipt.prompt_tokens != input_tokens
            )
        ):
            return _unavailable(
                receipt,
                CostUnavailableReason.USAGE_INCONSISTENT,
                snapshot,
            )
        input_hit_cost = _token_cost(
            hit_tokens,
            snapshot.input_cache_hit_per_1m,
        )
        input_miss_cost = _token_cost(
            miss_tokens,
            snapshot.input_cache_miss_per_1m,
        )
        input_total_cost = input_hit_cost + input_miss_cost
        provider_cache_savings = _token_cost(
            hit_tokens,
            snapshot.input_cache_miss_per_1m
            - snapshot.input_cache_hit_per_1m,
        )
    else:
        if not _valid_token_count(receipt.prompt_tokens):
            return _unavailable(
                receipt,
                CostUnavailableReason.USAGE_UNAVAILABLE,
                snapshot,
            )
        input_tokens = receipt.prompt_tokens
        input_total_cost = _token_cost(
            input_tokens,
            snapshot.input_cache_miss_per_1m,
        )

    if receipt.total_tokens is not None and (
        not _valid_token_count(receipt.total_tokens)
        or receipt.total_tokens != input_tokens + output_tokens
    ):
        return _unavailable(
            receipt,
            CostUnavailableReason.USAGE_INCONSISTENT,
            snapshot,
        )

    output_cost = _token_cost(output_tokens, snapshot.output_per_1m)
    total_cost = input_total_cost + output_cost
    return CostBreakdown(
        provider=receipt.provider,
        model=receipt.model,
        status=CostStatus.CALCULATED,
        currency=snapshot.currency,
        pricing_version=snapshot.pricing_version,
        rate_tier=snapshot.rate_tier,
        prompt_cache_hit_tokens=hit_tokens,
        prompt_cache_miss_tokens=miss_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_cache_hit_cost=input_hit_cost,
        input_cache_miss_cost=input_miss_cost,
        input_total_cost=input_total_cost,
        output_cost=output_cost,
        total_cost=total_cost,
        provider_cache_savings=provider_cache_savings,
        local_cache_savings=None,
        savings_classification=(
            "estimated_savings"
            if provider_cache_savings is not None
            else None
        ),
    )


def aggregate_costs(breakdowns: Iterable[CostBreakdown]) -> CostAggregate:
    items = tuple(breakdowns)
    currencies = {item.currency for item in items if item.currency}
    if len(currencies) > 1:
        raise ValueError("cannot aggregate costs with different currencies")
    currency = next(iter(currencies), None)
    unavailable_count = sum(
        item.status == CostStatus.COST_UNAVAILABLE for item in items
    )
    calculated_count = sum(item.status == CostStatus.CALCULATED for item in items)
    avoided_count = sum(
        item.status == CostStatus.PROVIDER_CALL_AVOIDED_BY_LOCAL_CACHE
        for item in items
    )
    known_subtotal = sum(
        (item.total_cost for item in items if item.total_cost is not None),
        start=Decimal("0"),
    )
    provider_savings = sum(
        (
            item.provider_cache_savings
            for item in items
            if item.provider_cache_savings is not None
        ),
        start=Decimal("0"),
    )
    local_savings = sum(
        (
            item.local_cache_savings
            for item in items
            if item.local_cache_savings is not None
        ),
        start=Decimal("0"),
    )
    if unavailable_count == len(items) and items:
        status = CostAggregationStatus.UNAVAILABLE
    elif unavailable_count:
        status = CostAggregationStatus.PARTIAL
    else:
        status = CostAggregationStatus.COMPLETE
    return CostAggregate(
        status=status,
        currency=currency,
        request_count=len(items),
        calculated_count=calculated_count,
        unavailable_count=unavailable_count,
        avoided_provider_call_count=avoided_count,
        known_cost_subtotal=known_subtotal,
        total_cost=None if unavailable_count else known_subtotal,
        provider_cache_savings=provider_savings,
        local_cache_savings=local_savings,
    )
