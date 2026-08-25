from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.cost.budget import BudgetDecision
from app.cost.contracts import require_non_negative_decimal
from app.cost.estimation import CostEstimate
from app.cost.ledger import ActualCostLedger, LedgerSnapshot
from app.model_routing.router import RoutingDecision


class BudgetGatePhase(str, Enum):
    PRE_EXECUTION = "PRE_EXECUTION"
    IN_EXECUTION = "IN_EXECUTION"


class UnknownCostPolicy(str, Enum):
    STOP = "STOP"
    WARN = "WARN"
    ALLOW = "ALLOW"


class CostAnomalyCode(str, Enum):
    ACTUAL_EXCEEDS_EXPECTED_HIGH = "ACTUAL_EXCEEDS_EXPECTED_HIGH"
    CACHE_HIT_RATE_DROP = "CACHE_HIT_RATE_DROP"
    UNKNOWN_PRICE_PRESENT = "UNKNOWN_PRICE_PRESENT"


class CostAnomalySeverity(str, Enum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class CostAnomaly:
    code: CostAnomalyCode
    severity: CostAnomalySeverity
    message: str
    observed: Decimal | None = None
    expected: Decimal | None = None
    diagnostics_only: bool = True

    def __post_init__(self) -> None:
        require_non_negative_decimal("observed", self.observed)
        require_non_negative_decimal("expected", self.expected)
        if not str(self.message).strip():
            raise ValueError("anomaly message must not be empty")


@dataclass(frozen=True, slots=True)
class ExecutionBudgetEnvelope:
    run_id: str
    operation_id: str
    currency: str
    user_max_budget: Decimal
    warning_threshold: Decimal
    hard_stop_threshold: Decimal
    estimate: CostEstimate | None
    provider_call_required: bool
    routing_available: bool
    selected_provider: str | None = None
    selected_model: str | None = None
    local_result_cache_hit: bool = False
    unknown_cost_policy: UnknownCostPolicy = UnknownCostPolicy.STOP
    phase: BudgetGatePhase = BudgetGatePhase.PRE_EXECUTION
    minimum_expected_cache_hit_ratio: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("run_id", "operation_id", "currency"):
            normalized = str(getattr(self, name) or "").strip()
            if not normalized:
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, normalized)
        for name in (
            "user_max_budget",
            "warning_threshold",
            "hard_stop_threshold",
        ):
            require_non_negative_decimal(name, getattr(self, name))
        if not (
            self.warning_threshold
            <= self.hard_stop_threshold
            <= self.user_max_budget
        ):
            raise ValueError(
                "budget amounts must satisfy warning <= hard stop <= user max"
            )
        if self.estimate is not None and self.estimate.currency != self.currency:
            raise ValueError("estimate and envelope currencies must match")
        if self.local_result_cache_hit and self.provider_call_required:
            raise ValueError("a local result cache hit cannot require a Provider call")
        if self.minimum_expected_cache_hit_ratio is not None:
            value = self.minimum_expected_cache_hit_ratio
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value < 0
                or value > 1
            ):
                raise ValueError(
                    "minimum_expected_cache_hit_ratio must be a Decimal from 0 to 1"
                )

    @classmethod
    def from_routing_decision(
        cls,
        *,
        run_id: str,
        operation_id: str,
        routing_decision: RoutingDecision,
        currency: str,
        user_max_budget: Decimal,
        warning_threshold: Decimal,
        hard_stop_threshold: Decimal,
        estimate: CostEstimate | None,
        local_result_cache_hit: bool = False,
        unknown_cost_policy: UnknownCostPolicy = UnknownCostPolicy.STOP,
        phase: BudgetGatePhase = BudgetGatePhase.PRE_EXECUTION,
        minimum_expected_cache_hit_ratio: Decimal | None = None,
    ) -> "ExecutionBudgetEnvelope":
        return cls(
            run_id=run_id,
            operation_id=operation_id,
            currency=currency,
            user_max_budget=user_max_budget,
            warning_threshold=warning_threshold,
            hard_stop_threshold=hard_stop_threshold,
            estimate=estimate,
            provider_call_required=not local_result_cache_hit,
            routing_available=routing_decision.available,
            selected_provider=routing_decision.selected_provider,
            selected_model=routing_decision.selected_model,
            local_result_cache_hit=local_result_cache_hit,
            unknown_cost_policy=unknown_cost_policy,
            phase=phase,
            minimum_expected_cache_hit_ratio=minimum_expected_cache_hit_ratio,
        )


@dataclass(frozen=True, slots=True)
class BudgetReceipt:
    run_id: str
    operation_id: str
    phase: BudgetGatePhase
    decision: BudgetDecision
    reasons: tuple[str, ...]
    currency: str
    user_max_budget: Decimal
    warning_threshold: Decimal
    hard_stop_threshold: Decimal
    actual_accumulated_cost: Decimal | None
    known_actual_cost_subtotal: Decimal | None
    projected_minimum: Decimal | None
    projected_expected: Decimal | None
    projected_maximum: Decimal | None
    unknown_cost_present: bool
    provider_cache_savings: Decimal
    local_cache_savings: Decimal
    estimated_savings: Decimal
    savings_classification: str
    selected_provider: str | None
    selected_model: str | None
    anomalies: tuple[CostAnomaly, ...]

    def __post_init__(self) -> None:
        if not self.reasons:
            raise ValueError("budget receipt must contain at least one reason")
        for name in (
            "user_max_budget",
            "warning_threshold",
            "hard_stop_threshold",
            "actual_accumulated_cost",
            "known_actual_cost_subtotal",
            "projected_minimum",
            "projected_expected",
            "projected_maximum",
            "provider_cache_savings",
            "local_cache_savings",
            "estimated_savings",
        ):
            require_non_negative_decimal(name, getattr(self, name))
        if self.savings_classification != "estimated_savings":
            raise ValueError("budget receipt savings must be explicitly estimated")

    def to_public_dict(self) -> dict[str, object]:
        def money(value: Decimal | None) -> str | None:
            return str(value) if value is not None else None

        return {
            "run_id": self.run_id,
            "operation_id": self.operation_id,
            "phase": self.phase.value,
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "currency": self.currency,
            "user_max_budget": money(self.user_max_budget),
            "warning_threshold": money(self.warning_threshold),
            "hard_stop_threshold": money(self.hard_stop_threshold),
            "actual_accumulated_cost": money(self.actual_accumulated_cost),
            "known_actual_cost_subtotal": money(self.known_actual_cost_subtotal),
            "projected_minimum": money(self.projected_minimum),
            "projected_expected": money(self.projected_expected),
            "projected_maximum": money(self.projected_maximum),
            "unknown_cost_present": self.unknown_cost_present,
            "provider_cache_savings": money(self.provider_cache_savings),
            "local_cache_savings": money(self.local_cache_savings),
            "estimated_savings": money(self.estimated_savings),
            "savings_classification": self.savings_classification,
            "selected_provider": self.selected_provider,
            "selected_model": self.selected_model,
            "anomalies": [anomaly.code.value for anomaly in self.anomalies],
        }


def _diagnostics(
    envelope: ExecutionBudgetEnvelope,
    run_snapshot: LedgerSnapshot,
    operation_snapshot: LedgerSnapshot,
) -> tuple[CostAnomaly, ...]:
    anomalies: list[CostAnomaly] = []
    if (
        envelope.estimate is not None
        and operation_snapshot.actual_calculated_provider_cost is not None
        and operation_snapshot.actual_calculated_provider_cost
        > envelope.estimate.maximum
    ):
        anomalies.append(
            CostAnomaly(
                code=CostAnomalyCode.ACTUAL_EXCEEDS_EXPECTED_HIGH,
                severity=CostAnomalySeverity.CRITICAL,
                message="Operation actual cost exceeded its expected high estimate.",
                observed=operation_snapshot.actual_calculated_provider_cost,
                expected=envelope.estimate.maximum,
            )
        )
    if (
        envelope.minimum_expected_cache_hit_ratio is not None
        and run_snapshot.cache_hit_ratio is not None
        and run_snapshot.cache_hit_ratio
        < envelope.minimum_expected_cache_hit_ratio
    ):
        anomalies.append(
            CostAnomaly(
                code=CostAnomalyCode.CACHE_HIT_RATE_DROP,
                severity=CostAnomalySeverity.WARNING,
                message="Observed Provider cache hit ratio fell below the configured minimum.",
                observed=run_snapshot.cache_hit_ratio,
                expected=envelope.minimum_expected_cache_hit_ratio,
            )
        )
    if run_snapshot.unpriced_receipts:
        anomalies.append(
            CostAnomaly(
                code=CostAnomalyCode.UNKNOWN_PRICE_PRESENT,
                severity=CostAnomalySeverity.CRITICAL,
                message="The run contains one or more receipts without a pricing snapshot.",
                observed=Decimal(run_snapshot.unpriced_receipts),
            )
        )
    return tuple(anomalies)


def _threshold_reached(value: Decimal, threshold: Decimal) -> bool:
    """Zero spend remains executable even when a user configures a zero cap."""

    return value > 0 and value >= threshold


class ExecutionBudgetGate:
    """Evaluate pre/in-execution budget state without executing Provider work."""

    def evaluate(
        self,
        envelope: ExecutionBudgetEnvelope,
        ledger: ActualCostLedger,
    ) -> BudgetReceipt:
        run_snapshot, operation_snapshot = ledger.scoped_snapshots(
            run_id=envelope.run_id,
            operation_id=envelope.operation_id,
        )
        reasons: list[str] = []
        must_stop = False
        must_warn = False

        currency_mismatch = (
            run_snapshot.known_actual_cost_subtotal is None
            or (
                run_snapshot.currency is not None
                and run_snapshot.currency != envelope.currency
            )
        )
        unknown_actual = not run_snapshot.actual_cost_complete or currency_mismatch
        known_actual = (
            None
            if currency_mismatch
            else run_snapshot.known_actual_cost_subtotal
        )
        estimate_unknown = envelope.provider_call_required and envelope.estimate is None
        unknown_cost_present = unknown_actual or estimate_unknown

        if not envelope.routing_available and not envelope.local_result_cache_hit:
            must_stop = True
            reasons.append("ROUTING_UNAVAILABLE")

        if unknown_actual:
            reasons.append("ACTUAL_COST_INCOMPLETE")
        if estimate_unknown:
            reasons.append("REQUIRED_PROVIDER_COST_UNESTIMATED")
        if unknown_cost_present:
            if envelope.unknown_cost_policy == UnknownCostPolicy.STOP:
                must_stop = True
                reasons.append("UNKNOWN_COST_STOP_POLICY")
            elif envelope.unknown_cost_policy == UnknownCostPolicy.WARN:
                must_warn = True
                reasons.append("UNKNOWN_COST_WARNING_POLICY")
            else:
                reasons.append("UNKNOWN_COST_ALLOWED_POLICY")

        if known_actual is not None:
            if envelope.estimate is not None and envelope.provider_call_required:
                projected_minimum = known_actual + envelope.estimate.minimum
                projected_expected = known_actual + envelope.estimate.expected
                projected_maximum = known_actual + envelope.estimate.maximum
            elif not envelope.provider_call_required:
                projected_minimum = known_actual
                projected_expected = known_actual
                projected_maximum = known_actual
            else:
                projected_minimum = None
                projected_expected = None
                projected_maximum = None
        else:
            projected_minimum = None
            projected_expected = None
            projected_maximum = None

        stop_values = tuple(
            value
            for value in (known_actual, projected_maximum)
            if value is not None
        )
        if any(
            _threshold_reached(value, envelope.user_max_budget)
            for value in stop_values
        ):
            must_stop = True
            reasons.append("USER_MAX_BUDGET_REACHED")
        if any(
            _threshold_reached(value, envelope.hard_stop_threshold)
            for value in stop_values
        ):
            must_stop = True
            reasons.append("HARD_STOP_THRESHOLD_REACHED")
        elif any(
            _threshold_reached(value, envelope.warning_threshold)
            for value in stop_values
        ):
            must_warn = True
            reasons.append("WARNING_THRESHOLD_REACHED")

        if must_stop:
            decision = BudgetDecision.STOP
        elif must_warn:
            decision = BudgetDecision.WARN
        else:
            decision = BudgetDecision.ALLOW
            reasons.append("WITHIN_BUDGET")

        return BudgetReceipt(
            run_id=envelope.run_id,
            operation_id=envelope.operation_id,
            phase=envelope.phase,
            decision=decision,
            reasons=tuple(dict.fromkeys(reasons)),
            currency=envelope.currency,
            user_max_budget=envelope.user_max_budget,
            warning_threshold=envelope.warning_threshold,
            hard_stop_threshold=envelope.hard_stop_threshold,
            actual_accumulated_cost=(
                run_snapshot.actual_calculated_provider_cost
                if not currency_mismatch
                else None
            ),
            known_actual_cost_subtotal=known_actual,
            projected_minimum=projected_minimum,
            projected_expected=projected_expected,
            projected_maximum=projected_maximum,
            unknown_cost_present=unknown_cost_present,
            provider_cache_savings=run_snapshot.provider_cache_savings,
            local_cache_savings=run_snapshot.local_cache_savings,
            estimated_savings=run_snapshot.estimated_savings,
            savings_classification=run_snapshot.savings_classification,
            selected_provider=envelope.selected_provider,
            selected_model=envelope.selected_model,
            anomalies=_diagnostics(envelope, run_snapshot, operation_snapshot),
        )
