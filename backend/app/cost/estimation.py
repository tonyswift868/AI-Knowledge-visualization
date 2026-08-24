from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Protocol

from app.cost.contracts import (
    LocalCacheSavingsBaseline,
    require_non_negative_decimal,
)
from app.providers.pricing import (
    InputTokenPricingMode,
    ProviderPricingSnapshot,
)


_TOKENS_PER_MILLION = Decimal("1000000")


class EstimationConfidence(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class TokenRange:
    minimum: int
    expected: int
    maximum: int

    def __post_init__(self) -> None:
        values = (self.minimum, self.expected, self.maximum)
        if any(isinstance(value, bool) or value < 0 for value in values):
            raise ValueError("token estimates must be non-negative integers")
        if not self.minimum <= self.expected <= self.maximum:
            raise ValueError("token estimates must satisfy minimum <= expected <= maximum")


@dataclass(frozen=True)
class CostEstimate:
    minimum: Decimal
    expected: Decimal
    maximum: Decimal
    currency: str
    confidence: EstimationConfidence
    estimation_basis: str
    expected_provider_calls: int
    expected_input_tokens: int
    expected_output_tokens: int
    expected_cache_assumption: str
    pricing_version: str
    cost_kind: str = "estimated"

    def __post_init__(self) -> None:
        for name in ("minimum", "expected", "maximum"):
            require_non_negative_decimal(name, getattr(self, name))
        if not self.minimum <= self.expected <= self.maximum:
            raise ValueError("cost estimate must satisfy minimum <= expected <= maximum")
        if not str(self.currency).strip():
            raise ValueError("currency must not be empty")
        if not str(self.estimation_basis).strip():
            raise ValueError("estimation_basis must not be empty")
        if not str(self.expected_cache_assumption).strip():
            raise ValueError("expected_cache_assumption must not be empty")
        for name in (
            "expected_provider_calls",
            "expected_input_tokens",
            "expected_output_tokens",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def as_local_cache_savings_baseline(self) -> LocalCacheSavingsBaseline:
        return LocalCacheSavingsBaseline(
            amount=self.expected,
            currency=self.currency,
            estimation_basis=self.estimation_basis,
        )


def _require_ratio(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite() or value < 0 or value > 1:
        raise ValueError(f"{name} must be between Decimal('0') and Decimal('1')")


def _estimated_cost(
    *,
    snapshot: ProviderPricingSnapshot,
    input_tokens: int,
    output_tokens: int,
    cache_hit_ratio: Decimal,
) -> Decimal:
    if snapshot.input_pricing_mode == InputTokenPricingMode.CACHE_HIT_MISS_REQUIRED:
        weighted_input_rate = (
            cache_hit_ratio * snapshot.input_cache_hit_per_1m
            + (Decimal("1") - cache_hit_ratio)
            * snapshot.input_cache_miss_per_1m
        )
    else:
        weighted_input_rate = snapshot.input_cache_miss_per_1m
    return (
        Decimal(input_tokens) * weighted_input_rate
        + Decimal(output_tokens) * snapshot.output_per_1m
    ) / _TOKENS_PER_MILLION


def estimate_cost(
    *,
    snapshot: ProviderPricingSnapshot,
    input_tokens: TokenRange,
    output_tokens: TokenRange,
    expected_provider_calls: int,
    optimistic_cache_hit_ratio: Decimal,
    expected_cache_hit_ratio: Decimal,
    pessimistic_cache_hit_ratio: Decimal,
    confidence: EstimationConfidence,
    estimation_basis: str,
    expected_cache_assumption: str,
) -> CostEstimate:
    """Estimate an interval from aggregate token ranges and explicit assumptions."""

    if isinstance(expected_provider_calls, bool) or expected_provider_calls < 0:
        raise ValueError("expected_provider_calls must be a non-negative integer")
    for name, value in (
        ("optimistic_cache_hit_ratio", optimistic_cache_hit_ratio),
        ("expected_cache_hit_ratio", expected_cache_hit_ratio),
        ("pessimistic_cache_hit_ratio", pessimistic_cache_hit_ratio),
    ):
        _require_ratio(name, value)
    if not (
        optimistic_cache_hit_ratio
        >= expected_cache_hit_ratio
        >= pessimistic_cache_hit_ratio
    ):
        raise ValueError(
            "cache assumptions must satisfy optimistic >= expected >= pessimistic"
        )
    return CostEstimate(
        minimum=_estimated_cost(
            snapshot=snapshot,
            input_tokens=input_tokens.minimum,
            output_tokens=output_tokens.minimum,
            cache_hit_ratio=optimistic_cache_hit_ratio,
        ),
        expected=_estimated_cost(
            snapshot=snapshot,
            input_tokens=input_tokens.expected,
            output_tokens=output_tokens.expected,
            cache_hit_ratio=expected_cache_hit_ratio,
        ),
        maximum=_estimated_cost(
            snapshot=snapshot,
            input_tokens=input_tokens.maximum,
            output_tokens=output_tokens.maximum,
            cache_hit_ratio=pessimistic_cache_hit_ratio,
        ),
        currency=snapshot.currency,
        confidence=confidence,
        estimation_basis=estimation_basis,
        expected_provider_calls=expected_provider_calls,
        expected_input_tokens=input_tokens.expected,
        expected_output_tokens=output_tokens.expected,
        expected_cache_assumption=expected_cache_assumption,
        pricing_version=snapshot.pricing_version,
    )


class CurrencyConversionHook(Protocol):
    def __call__(
        self,
        amount: Decimal,
        source_currency: str,
        target_currency: str,
        as_of: datetime,
    ) -> Decimal: ...


def convert_estimate_currency(
    estimate: CostEstimate,
    *,
    target_currency: str,
    as_of: datetime,
    converter: CurrencyConversionHook,
) -> CostEstimate:
    """Use an injected, time-aware FX source; no exchange rate is hard-coded."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    normalized_target = str(target_currency or "").strip().upper()
    if not normalized_target:
        raise ValueError("target_currency must not be empty")
    converted = tuple(
        converter(amount, estimate.currency, normalized_target, as_of)
        for amount in (estimate.minimum, estimate.expected, estimate.maximum)
    )
    for name, value in zip(("minimum", "expected", "maximum"), converted):
        require_non_negative_decimal(name, value)
    return replace(
        estimate,
        minimum=converted[0],
        expected=converted[1],
        maximum=converted[2],
        currency=normalized_target,
        estimation_basis=(
            f"{estimate.estimation_basis}; FX supplied by injected converter "
            f"as of {as_of.isoformat()}"
        ),
    )
