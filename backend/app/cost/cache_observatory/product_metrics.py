from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from .contracts import (
    CacheLayer,
    CacheObservatoryError,
    CacheObservatoryReport,
    CurrencyAmount,
)


@dataclass(frozen=True, slots=True)
class ProductMetricsReadModel:
    provider_cache_hit_percent: Decimal | None
    local_result_hit_percent: Decimal | None
    evidence_hit_percent: Decimal | None
    parse_hit_percent: Decimal | None
    question_hit_percent: Decimal | None
    evaluation_artifact_hit_percent: Decimal | None
    cost_saved_estimate: Decimal | None
    cost_saved_estimates_by_currency: tuple[CurrencyAmount, ...]
    calls_avoided: int
    generated_at_ms: int
    window_started_at_ms: int | None = None
    window_ended_at_ms: int | None = None
    cost_classification: str = "estimated"

    def __post_init__(self) -> None:
        if (
            isinstance(self.generated_at_ms, bool)
            or not isinstance(self.generated_at_ms, int)
            or self.generated_at_ms < 0
        ):
            raise CacheObservatoryError(
                "generated_at_ms must be a non-negative integer"
            )
        if self.cost_classification != "estimated":
            raise CacheObservatoryError("cost saved must remain explicitly estimated")

    def to_public_dict(self) -> dict[str, object]:
        percent = lambda value: str(value) if value is not None else None
        return {
            "provider_cache_hit_percent": percent(self.provider_cache_hit_percent),
            "local_result_hit_percent": percent(self.local_result_hit_percent),
            "evidence_hit_percent": percent(self.evidence_hit_percent),
            "parse_hit_percent": percent(self.parse_hit_percent),
            "question_hit_percent": percent(self.question_hit_percent),
            "evaluation_artifact_hit_percent": percent(
                self.evaluation_artifact_hit_percent
            ),
            "combined_cache_hit_percent": None,
            "combined_cache_hit_percent_reason": (
                "Cache layers are intentionally reported separately."
            ),
            "cost_saved_estimate": (
                str(self.cost_saved_estimate)
                if self.cost_saved_estimate is not None
                else None
            ),
            "cost_saved_estimates_by_currency": [
                {"currency": item.currency, "amount": str(item.amount)}
                for item in self.cost_saved_estimates_by_currency
            ],
            "cost_classification": self.cost_classification,
            "calls_avoided": self.calls_avoided,
            "generated_at_ms": self.generated_at_ms,
            "window_started_at_ms": self.window_started_at_ms,
            "window_ended_at_ms": self.window_ended_at_ms,
        }


class ProductMetricsProjector:
    def project(
        self,
        report: CacheObservatoryReport,
        *,
        generated_at_ms: int,
        window_started_at_ms: int | None = None,
        window_ended_at_ms: int | None = None,
    ) -> ProductMetricsReadModel:
        if not isinstance(report, CacheObservatoryReport):
            raise TypeError("report must be CacheObservatoryReport")
        if (
            window_started_at_ms is not None
            and window_ended_at_ms is not None
            and window_started_at_ms >= window_ended_at_ms
        ):
            raise CacheObservatoryError("metrics window must satisfy start < end")
        savings: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
        for snapshot in report.layers:
            for amount in snapshot.estimated_costs_by_currency:
                savings[amount.currency] += amount.amount
        totals = tuple(
            CurrencyAmount(currency=currency, amount=amount)
            for currency, amount in sorted(savings.items())
        )

        def hit_percent(layer: CacheLayer) -> Decimal | None:
            ratio = report.for_layer(layer).hit_ratio
            return ratio * Decimal(100) if ratio is not None else None

        return ProductMetricsReadModel(
            provider_cache_hit_percent=hit_percent(CacheLayer.PROVIDER_PROMPT_CACHE),
            local_result_hit_percent=hit_percent(CacheLayer.LOCAL_RESULT_CACHE),
            evidence_hit_percent=hit_percent(CacheLayer.EVIDENCE_CACHE),
            parse_hit_percent=hit_percent(CacheLayer.PARSE_CACHE),
            question_hit_percent=hit_percent(CacheLayer.QUESTION_BANK_CACHE),
            evaluation_artifact_hit_percent=hit_percent(
                CacheLayer.EVALUATION_ARTIFACT_CACHE
            ),
            cost_saved_estimate=(totals[0].amount if len(totals) == 1 else None),
            cost_saved_estimates_by_currency=totals,
            calls_avoided=sum(
                snapshot.avoided_provider_calls for snapshot in report.layers
            ),
            generated_at_ms=generated_at_ms,
            window_started_at_ms=window_started_at_ms,
            window_ended_at_ms=window_ended_at_ms,
        )
