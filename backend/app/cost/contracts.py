from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class CostStatus(str, Enum):
    CALCULATED = "CALCULATED"
    PROVIDER_CALL_AVOIDED_BY_LOCAL_CACHE = (
        "PROVIDER_CALL_AVOIDED_BY_LOCAL_CACHE"
    )
    PROVIDER_NOT_CALLED = "PROVIDER_NOT_CALLED"
    COST_UNAVAILABLE = "COST_UNAVAILABLE"


class CostUnavailableReason(str, Enum):
    INVALID_RECEIPT_TIMESTAMP = "INVALID_RECEIPT_TIMESTAMP"
    PRICING_NOT_FOUND = "PRICING_NOT_FOUND"
    USAGE_UNAVAILABLE = "USAGE_UNAVAILABLE"
    CACHE_BREAKDOWN_UNAVAILABLE = "CACHE_BREAKDOWN_UNAVAILABLE"
    USAGE_INCONSISTENT = "USAGE_INCONSISTENT"


class CostAggregationStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


def require_non_negative_decimal(name: str, value: Decimal | None) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite non-negative Decimal")


@dataclass(frozen=True)
class LocalCacheSavingsBaseline:
    """Explicit counterfactual used to estimate a locally avoided call."""

    amount: Decimal
    currency: str
    estimation_basis: str

    def __post_init__(self) -> None:
        require_non_negative_decimal("amount", self.amount)
        if not str(self.currency).strip():
            raise ValueError("currency must not be empty")
        if not str(self.estimation_basis).strip():
            raise ValueError("estimation_basis must not be empty")


@dataclass(frozen=True)
class CostBreakdown:
    provider: str
    model: str
    status: CostStatus
    currency: str | None
    pricing_version: str | None
    rate_tier: str | None
    prompt_cache_hit_tokens: int | None
    prompt_cache_miss_tokens: int | None
    input_tokens: int | None
    output_tokens: int | None
    input_cache_hit_cost: Decimal | None
    input_cache_miss_cost: Decimal | None
    input_total_cost: Decimal | None
    output_cost: Decimal | None
    total_cost: Decimal | None
    provider_cache_savings: Decimal | None
    local_cache_savings: Decimal | None
    savings_classification: str | None
    reason: CostUnavailableReason | None = None
    local_cache_estimation_basis: str | None = None
    cost_kind: str = "actual"

    def __post_init__(self) -> None:
        for name in (
            "input_cache_hit_cost",
            "input_cache_miss_cost",
            "input_total_cost",
            "output_cost",
            "total_cost",
            "provider_cache_savings",
            "local_cache_savings",
        ):
            require_non_negative_decimal(name, getattr(self, name))
        for name in (
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
            "input_tokens",
            "output_tokens",
        ):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or None")
        if self.status == CostStatus.COST_UNAVAILABLE:
            if self.total_cost is not None or self.reason is None:
                raise ValueError("unavailable costs require a reason and no total")
        elif self.total_cost is None:
            raise ValueError("available costs require a total")
        if self.status == CostStatus.CALCULATED and not self.currency:
            raise ValueError("calculated costs require a currency")
        if self.savings_classification not in {None, "estimated_savings"}:
            raise ValueError("savings must be labelled estimated_savings")
        if (
            self.provider_cache_savings is not None
            or self.local_cache_savings is not None
        ) and self.savings_classification != "estimated_savings":
            raise ValueError("all savings must be explicitly marked estimated")

    @property
    def is_available(self) -> bool:
        return self.status != CostStatus.COST_UNAVAILABLE


@dataclass(frozen=True)
class CostAggregate:
    status: CostAggregationStatus
    currency: str | None
    request_count: int
    calculated_count: int
    unavailable_count: int
    avoided_provider_call_count: int
    known_cost_subtotal: Decimal
    total_cost: Decimal | None
    provider_cache_savings: Decimal
    local_cache_savings: Decimal
    savings_classification: str = "estimated_savings"

    def __post_init__(self) -> None:
        for name in (
            "known_cost_subtotal",
            "total_cost",
            "provider_cache_savings",
            "local_cache_savings",
        ):
            require_non_negative_decimal(name, getattr(self, name))
        for name in (
            "request_count",
            "calculated_count",
            "unavailable_count",
            "avoided_provider_call_count",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if self.unavailable_count and self.total_cost is not None:
            raise ValueError("aggregate total is unknown when any receipt is unknown")
        if not self.unavailable_count and self.total_cost != self.known_cost_subtotal:
            raise ValueError("complete aggregate total must equal its subtotal")
