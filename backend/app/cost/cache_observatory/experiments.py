from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .contracts import CacheObservatoryError

_TOKENS_PER_MILLION = Decimal(1_000_000)


def _ratio(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite() or not Decimal(0) <= value <= Decimal(1):
        raise CacheObservatoryError(f"{name} must be a Decimal from 0 to 1")


def _non_negative_decimal(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite() or value < 0:
        raise CacheObservatoryError(f"{name} must be finite and non-negative")


def _non_negative_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CacheObservatoryError(f"{name} must be a non-negative integer")


def _count_at_ratio(total: int, ratio: Decimal) -> int:
    return int((Decimal(total) * ratio).to_integral_value(rounding=ROUND_HALF_UP))


@dataclass(frozen=True, slots=True)
class CacheScenario:
    scenario_id: str
    provider_prompt_cache_hit_ratio: Decimal
    local_result_cache_hit_ratio: Decimal

    def __post_init__(self) -> None:
        normalized = str(self.scenario_id or "").strip()
        if not normalized:
            raise CacheObservatoryError("scenario_id must not be empty")
        object.__setattr__(self, "scenario_id", normalized)
        _ratio(
            "provider_prompt_cache_hit_ratio",
            self.provider_prompt_cache_hit_ratio,
        )
        _ratio("local_result_cache_hit_ratio", self.local_result_cache_hit_ratio)


@dataclass(frozen=True, slots=True)
class CacheExperiment:
    experiment_id: str
    request_count: int
    provider_input_tokens_per_request: int
    stable_prefix_tokens_per_request: int
    provider_output_tokens_per_request: int
    input_cache_hit_per_1m: Decimal
    input_cache_miss_per_1m: Decimal
    output_per_1m: Decimal
    currency: str
    scenarios: tuple[CacheScenario, ...]
    pricing_version: str
    source_classification: str = "synthetic_offline_estimate"

    def __post_init__(self) -> None:
        for name in ("experiment_id", "currency", "pricing_version"):
            normalized = str(getattr(self, name) or "").strip()
            if not normalized:
                raise CacheObservatoryError(f"{name} must not be empty")
            object.__setattr__(
                self, name, normalized.upper() if name == "currency" else normalized
            )
        for name in (
            "request_count",
            "provider_input_tokens_per_request",
            "stable_prefix_tokens_per_request",
            "provider_output_tokens_per_request",
        ):
            _non_negative_int(name, getattr(self, name))
        if self.request_count == 0:
            raise CacheObservatoryError("request_count must be positive")
        if (
            self.stable_prefix_tokens_per_request
            > self.provider_input_tokens_per_request
        ):
            raise CacheObservatoryError(
                "stable prefix cannot exceed Provider input tokens"
            )
        for name in (
            "input_cache_hit_per_1m",
            "input_cache_miss_per_1m",
            "output_per_1m",
        ):
            _non_negative_decimal(name, getattr(self, name))
        if self.input_cache_hit_per_1m > self.input_cache_miss_per_1m:
            raise CacheObservatoryError(
                "Provider cache-hit input price cannot exceed miss price"
            )
        if not self.scenarios:
            raise CacheObservatoryError("experiment requires at least one scenario")
        scenario_ids = tuple(item.scenario_id for item in self.scenarios)
        if len(scenario_ids) != len(set(scenario_ids)):
            raise CacheObservatoryError("scenario_id values must be unique")
        if self.source_classification != "synthetic_offline_estimate":
            raise CacheObservatoryError(
                "cache experiments must remain classified as synthetic estimates"
            )


@dataclass(frozen=True, slots=True)
class CacheScenarioResult:
    scenario: CacheScenario
    request_count: int
    provider_call_count: int
    calls_avoided: int
    provider_prompt_cache_hit_calls: int
    provider_prompt_cache_miss_calls: int
    provider_cache_tokens_reused: int
    local_input_tokens_avoided: int
    local_output_tokens_avoided: int
    tokens_avoided: int
    estimated_cost: Decimal
    estimated_cost_difference: Decimal
    estimated_provider_cache_savings: Decimal
    estimated_local_cache_savings: Decimal
    currency: str
    classification: str = "synthetic_estimate"

    def to_public_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario.scenario_id,
            "assumptions": {
                "provider_prompt_cache_hit_ratio": str(
                    self.scenario.provider_prompt_cache_hit_ratio
                ),
                "local_result_cache_hit_ratio": str(
                    self.scenario.local_result_cache_hit_ratio
                ),
            },
            "request_count": self.request_count,
            "provider_call_count": self.provider_call_count,
            "calls_avoided": self.calls_avoided,
            "provider_prompt_cache_hit_calls": (self.provider_prompt_cache_hit_calls),
            "provider_prompt_cache_miss_calls": (self.provider_prompt_cache_miss_calls),
            "provider_cache_tokens_reused": self.provider_cache_tokens_reused,
            "local_input_tokens_avoided": self.local_input_tokens_avoided,
            "local_output_tokens_avoided": self.local_output_tokens_avoided,
            "tokens_avoided": self.tokens_avoided,
            "estimated_cost": str(self.estimated_cost),
            "estimated_cost_difference": str(self.estimated_cost_difference),
            "estimated_provider_cache_savings": str(
                self.estimated_provider_cache_savings
            ),
            "estimated_local_cache_savings": str(self.estimated_local_cache_savings),
            "currency": self.currency,
            "classification": self.classification,
        }


@dataclass(frozen=True, slots=True)
class CacheExperimentReport:
    experiment_id: str
    request_count: int
    baseline_estimated_cost: Decimal
    currency: str
    pricing_version: str
    scenarios: tuple[CacheScenarioResult, ...]
    most_cost_efficient_scenario_id: str
    real_provider_calls: int = 0
    behavior_changed: bool = False
    classification: str = "synthetic_offline_estimate"
    disclaimer: str = (
        "Synthetic workload only; cost differences are estimates, not an official "
        "Provider bill or a real cache hit rate."
    )

    def for_scenario(self, scenario_id: str) -> CacheScenarioResult:
        return next(
            item for item in self.scenarios if item.scenario.scenario_id == scenario_id
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "request_count": self.request_count,
            "baseline_estimated_cost": str(self.baseline_estimated_cost),
            "currency": self.currency,
            "pricing_version": self.pricing_version,
            "scenarios": [item.to_public_dict() for item in self.scenarios],
            "most_cost_efficient_scenario_id": (self.most_cost_efficient_scenario_id),
            "real_provider_calls": self.real_provider_calls,
            "behavior_changed": self.behavior_changed,
            "classification": self.classification,
            "disclaimer": self.disclaimer,
        }


def standard_cache_scenarios(
    *,
    local_result_cache_hit_ratios: tuple[Decimal, ...] = (
        Decimal(0),
        Decimal("0.20"),
    ),
) -> tuple[CacheScenario, ...]:
    for ratio in local_result_cache_hit_ratios:
        _ratio("local_result_cache_hit_ratio", ratio)
    return tuple(
        CacheScenario(
            scenario_id=f"provider-{provider_ratio}-local-{local_ratio}",
            provider_prompt_cache_hit_ratio=provider_ratio,
            local_result_cache_hit_ratio=local_ratio,
        )
        for local_ratio in local_result_cache_hit_ratios
        for provider_ratio in (
            Decimal(0),
            Decimal("0.30"),
            Decimal("0.60"),
            Decimal("0.90"),
        )
    )


def _token_cost(tokens: int, rate: Decimal) -> Decimal:
    return Decimal(tokens) * rate / _TOKENS_PER_MILLION


def run_cache_experiment(experiment: CacheExperiment) -> CacheExperimentReport:
    if not isinstance(experiment, CacheExperiment):
        raise TypeError("experiment must be CacheExperiment")
    baseline_request_cost = _token_cost(
        experiment.provider_input_tokens_per_request,
        experiment.input_cache_miss_per_1m,
    ) + _token_cost(
        experiment.provider_output_tokens_per_request,
        experiment.output_per_1m,
    )
    baseline_cost = baseline_request_cost * experiment.request_count
    scenario_results: list[CacheScenarioResult] = []
    for scenario in experiment.scenarios:
        calls_avoided = _count_at_ratio(
            experiment.request_count, scenario.local_result_cache_hit_ratio
        )
        provider_calls = experiment.request_count - calls_avoided
        provider_hit_calls = _count_at_ratio(
            provider_calls, scenario.provider_prompt_cache_hit_ratio
        )
        provider_miss_calls = provider_calls - provider_hit_calls
        provider_hit_tokens = (
            provider_hit_calls * experiment.stable_prefix_tokens_per_request
        )
        provider_total_input_tokens = (
            provider_calls * experiment.provider_input_tokens_per_request
        )
        provider_miss_priced_tokens = provider_total_input_tokens - provider_hit_tokens
        local_input_tokens = (
            calls_avoided * experiment.provider_input_tokens_per_request
        )
        local_output_tokens = (
            calls_avoided * experiment.provider_output_tokens_per_request
        )
        estimated_provider_savings = _token_cost(
            provider_hit_tokens,
            experiment.input_cache_miss_per_1m - experiment.input_cache_hit_per_1m,
        )
        estimated_local_savings = baseline_request_cost * calls_avoided
        estimated_cost = (
            _token_cost(provider_hit_tokens, experiment.input_cache_hit_per_1m)
            + _token_cost(
                provider_miss_priced_tokens,
                experiment.input_cache_miss_per_1m,
            )
            + _token_cost(
                provider_calls * experiment.provider_output_tokens_per_request,
                experiment.output_per_1m,
            )
        )
        cost_difference = baseline_cost - estimated_cost
        if cost_difference != estimated_provider_savings + estimated_local_savings:
            raise CacheObservatoryError(
                "experiment cost difference does not reconcile to cache savings"
            )
        scenario_results.append(
            CacheScenarioResult(
                scenario=scenario,
                request_count=experiment.request_count,
                provider_call_count=provider_calls,
                calls_avoided=calls_avoided,
                provider_prompt_cache_hit_calls=provider_hit_calls,
                provider_prompt_cache_miss_calls=provider_miss_calls,
                provider_cache_tokens_reused=provider_hit_tokens,
                local_input_tokens_avoided=local_input_tokens,
                local_output_tokens_avoided=local_output_tokens,
                tokens_avoided=(
                    provider_hit_tokens + local_input_tokens + local_output_tokens
                ),
                estimated_cost=estimated_cost,
                estimated_cost_difference=cost_difference,
                estimated_provider_cache_savings=estimated_provider_savings,
                estimated_local_cache_savings=estimated_local_savings,
                currency=experiment.currency,
            )
        )
    most_efficient = min(
        scenario_results,
        key=lambda item: (item.estimated_cost, item.scenario.scenario_id),
    )
    return CacheExperimentReport(
        experiment_id=experiment.experiment_id,
        request_count=experiment.request_count,
        baseline_estimated_cost=baseline_cost,
        currency=experiment.currency,
        pricing_version=experiment.pricing_version,
        scenarios=tuple(scenario_results),
        most_cost_efficient_scenario_id=most_efficient.scenario.scenario_id,
    )
