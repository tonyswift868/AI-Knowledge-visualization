from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from decimal import Decimal

from app.cost.budget import BudgetDecision
from app.cost.estimation import CostEstimate, EstimationConfidence
from app.cost.execution_v2.contracts import (
    BudgetedOperation,
    ExecutionSessionState,
    ExecutionStop,
    OperationPermission,
)
from app.cost.execution_v2.ports import RouteMetadata
from app.cost.ledger import ActualCostLedger
from app.cost.runtime_budget import (
    BudgetGatePhase,
    BudgetReceipt,
    ExecutionBudgetEnvelope,
    ExecutionBudgetGate,
    UnknownCostPolicy,
)


@dataclass(frozen=True, slots=True)
class _Reservation:
    attempt_id: str
    estimate: CostEstimate | None


_CONFIDENCE_ORDER = {
    EstimationConfidence.LOW: 0,
    EstimationConfidence.MEDIUM: 1,
    EstimationConfidence.HIGH: 2,
}


def _combine_estimates(
    reservations: tuple[_Reservation, ...],
) -> CostEstimate | None:
    if not reservations or any(item.estimate is None for item in reservations):
        return None
    estimates = tuple(item.estimate for item in reservations if item.estimate is not None)
    currencies = {estimate.currency for estimate in estimates}
    if len(currencies) != 1:
        return None
    versions = tuple(sorted({estimate.pricing_version for estimate in estimates}))
    confidence = min(
        (estimate.confidence for estimate in estimates),
        key=_CONFIDENCE_ORDER.__getitem__,
    )
    return CostEstimate(
        minimum=sum((item.minimum for item in estimates), start=Decimal("0")),
        expected=sum((item.expected for item in estimates), start=Decimal("0")),
        maximum=sum((item.maximum for item in estimates), start=Decimal("0")),
        currency=estimates[0].currency,
        confidence=confidence,
        estimation_basis="aggregate of in-flight operation reservations",
        expected_provider_calls=sum(
            item.expected_provider_calls for item in estimates
        ),
        expected_input_tokens=sum(
            item.expected_input_tokens for item in estimates
        ),
        expected_output_tokens=sum(
            item.expected_output_tokens for item in estimates
        ),
        expected_cache_assumption="per-operation cache assumptions retained",
        pricing_version="+".join(versions),
    )


class OperationBudgetGuard:
    """Adds in-flight reservations and a sticky STOP latch to the W2 gate."""

    def __init__(
        self,
        *,
        run_id: str,
        currency: str,
        user_max_budget: Decimal,
        warning_threshold: Decimal,
        hard_stop_threshold: Decimal,
        ledger: ActualCostLedger,
        unknown_cost_policy: UnknownCostPolicy = UnknownCostPolicy.STOP,
        minimum_expected_cache_hit_ratio: Decimal | None = None,
        gate: ExecutionBudgetGate | None = None,
    ) -> None:
        self._run_id = str(run_id or "").strip()
        if not self._run_id:
            raise ValueError("run_id must not be empty")
        self._currency = str(currency or "").strip()
        self._user_max_budget = user_max_budget
        self._warning_threshold = warning_threshold
        self._hard_stop_threshold = hard_stop_threshold
        self._ledger = ledger
        self._unknown_cost_policy = unknown_cost_policy
        self._minimum_expected_cache_hit_ratio = minimum_expected_cache_hit_ratio
        self._gate = gate or ExecutionBudgetGate()
        self._reservations: dict[str, _Reservation] = {}
        self._stopped = False
        self._stop_reason: str | None = None
        self._stop_receipt: BudgetReceipt | None = None
        self._lock = threading.RLock()
        # Delegate all amount/range validation to the established W2 contract.
        self._make_envelope(
            operation_id="__guard_validation__",
            estimate=CostEstimate(
                minimum=Decimal("0"),
                expected=Decimal("0"),
                maximum=Decimal("0"),
                currency=self._currency,
                confidence=EstimationConfidence.HIGH,
                estimation_basis="guard validation",
                expected_provider_calls=0,
                expected_input_tokens=0,
                expected_output_tokens=0,
                expected_cache_assumption="no call",
                pricing_version="guard-validation",
            ),
            provider_call_required=False,
            routing_available=True,
            selected_provider=None,
            selected_model=None,
            local_result_cache_hit=False,
            phase=BudgetGatePhase.PRE_EXECUTION,
        )

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    @property
    def stop_reason(self) -> str | None:
        with self._lock:
            return self._stop_reason

    @property
    def stop_receipt(self) -> BudgetReceipt | None:
        with self._lock:
            return self._stop_receipt

    @property
    def reservation_count(self) -> int:
        with self._lock:
            return len(self._reservations)

    def evaluate_job_start(self, estimate: CostEstimate | None) -> BudgetReceipt:
        with self._lock:
            receipt = self._gate.evaluate(
                self._make_envelope(
                    operation_id="__job_start__",
                    estimate=estimate,
                    provider_call_required=True,
                    routing_available=True,
                    selected_provider=None,
                    selected_model=None,
                    local_result_cache_hit=False,
                    phase=BudgetGatePhase.PRE_EXECUTION,
                ),
                self._ledger,
            )
            if receipt.decision == BudgetDecision.STOP:
                self._latch_stop("JOB_START_BUDGET_STOP", receipt)
            return receipt

    def before_operation(
        self,
        operation: BudgetedOperation,
        route: RouteMetadata,
    ) -> OperationPermission:
        with self._lock:
            self._raise_if_stopped()
            if operation.attempt_id in self._reservations:
                raise ValueError("attempt already has an in-flight reservation")
            reservations = tuple(self._reservations.values())
            if operation.chargeable:
                reservations += (
                    _Reservation(operation.attempt_id, operation.estimate),
                )
            combined = _combine_estimates(reservations)
            provider_call_required = bool(reservations)
            receipt = self._gate.evaluate(
                self._make_envelope(
                    operation_id=operation.operation_id,
                    estimate=combined,
                    provider_call_required=provider_call_required,
                    routing_available=(
                        route.routing_available if operation.chargeable else True
                    ),
                    selected_provider=route.selected_provider,
                    selected_model=route.selected_model,
                    local_result_cache_hit=(
                        operation.local_result_cache_hit
                        and not provider_call_required
                    ),
                    phase=BudgetGatePhase.PRE_EXECUTION,
                ),
                self._ledger,
            )
            if receipt.decision == BudgetDecision.STOP:
                self._latch_stop("OPERATION_BUDGET_STOP", receipt)
                self._raise_if_stopped()
            if operation.chargeable:
                self._reservations[operation.attempt_id] = _Reservation(
                    operation.attempt_id,
                    operation.estimate,
                )
            return OperationPermission(
                operation_id=operation.operation_id,
                attempt_id=operation.attempt_id,
                chargeable=operation.chargeable,
                budget_receipt=receipt,
                reserved_maximum=(
                    operation.estimate.maximum
                    if operation.chargeable and operation.estimate is not None
                    else Decimal("0")
                ),
            )

    def after_operation(
        self,
        operation: BudgetedOperation,
        route: RouteMetadata,
    ) -> BudgetReceipt:
        with self._lock:
            self._reservations.pop(operation.attempt_id, None)
            remaining = tuple(self._reservations.values())
            receipt = self._gate.evaluate(
                self._make_envelope(
                    operation_id=operation.operation_id,
                    estimate=_combine_estimates(remaining),
                    provider_call_required=bool(remaining),
                    routing_available=True,
                    selected_provider=route.selected_provider,
                    selected_model=route.selected_model,
                    local_result_cache_hit=False,
                    phase=BudgetGatePhase.IN_EXECUTION,
                ),
                self._ledger,
            )
            if receipt.decision == BudgetDecision.STOP:
                self._latch_stop("RUNNING_BUDGET_STOP", receipt)
            return receipt

    def force_stop(
        self,
        *,
        reason: str,
        operation: BudgetedOperation,
        route: RouteMetadata,
    ) -> BudgetReceipt:
        with self._lock:
            self._reservations.pop(operation.attempt_id, None)
            remaining = tuple(self._reservations.values())
            evaluated = self._gate.evaluate(
                self._make_envelope(
                    operation_id=operation.operation_id,
                    estimate=_combine_estimates(remaining),
                    provider_call_required=bool(remaining),
                    routing_available=True,
                    selected_provider=route.selected_provider,
                    selected_model=route.selected_model,
                    local_result_cache_hit=False,
                    phase=BudgetGatePhase.IN_EXECUTION,
                ),
                self._ledger,
            )
            forced = replace(
                evaluated,
                decision=BudgetDecision.STOP,
                reasons=tuple(dict.fromkeys((reason, *evaluated.reasons))),
                unknown_cost_present=True,
            )
            self._latch_stop(reason, forced)
            return forced

    def cancel(self, reason: str = "SESSION_CANCELLED") -> None:
        with self._lock:
            self._stopped = True
            self._stop_reason = str(reason or "SESSION_CANCELLED").strip()
            self._reservations.clear()

    def _make_envelope(
        self,
        *,
        operation_id: str,
        estimate: CostEstimate | None,
        provider_call_required: bool,
        routing_available: bool,
        selected_provider: str | None,
        selected_model: str | None,
        local_result_cache_hit: bool,
        phase: BudgetGatePhase,
    ) -> ExecutionBudgetEnvelope:
        return ExecutionBudgetEnvelope(
            run_id=self._run_id,
            operation_id=operation_id,
            currency=self._currency,
            user_max_budget=self._user_max_budget,
            warning_threshold=self._warning_threshold,
            hard_stop_threshold=self._hard_stop_threshold,
            estimate=estimate,
            provider_call_required=provider_call_required,
            routing_available=routing_available,
            selected_provider=selected_provider,
            selected_model=selected_model,
            local_result_cache_hit=local_result_cache_hit,
            unknown_cost_policy=self._unknown_cost_policy,
            phase=phase,
            minimum_expected_cache_hit_ratio=(
                self._minimum_expected_cache_hit_ratio
            ),
        )

    def _latch_stop(self, reason: str, receipt: BudgetReceipt) -> None:
        self._stopped = True
        self._stop_reason = reason
        self._stop_receipt = receipt
        self._reservations.clear()

    def _raise_if_stopped(self) -> None:
        if self._stopped:
            raise ExecutionStop(
                self._stop_reason or "EXECUTION_STOPPED",
                budget_receipt=self._stop_receipt,
                session_state=ExecutionSessionState.STOPPED,
            )
