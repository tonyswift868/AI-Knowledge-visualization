from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum

from app.cost.contracts import (
    LocalCacheSavingsBaseline,
    require_non_negative_decimal,
)
from app.cost.estimation import CostEstimate
from app.cost.runtime_budget import BudgetReceipt
from app.cost.runtime_budget import CostAnomaly


class ExecutionSessionState(str, Enum):
    ACTIVE = "ACTIVE"
    WARN = "WARN"
    STOPPED = "STOPPED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class OperationExecutionStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    LOCAL_CACHE_HIT = "LOCAL_CACHE_HIT"


@dataclass(frozen=True, slots=True)
class BudgetedOperation:
    operation_id: str
    attempt_id: str
    idempotency_key: str
    payload: object
    estimate: CostEstimate | None
    retry_of_attempt_id: str | None = None
    local_result_cache_hit: bool = False
    cached_result: object | None = None
    local_cache_baseline: LocalCacheSavingsBaseline | None = None

    def __post_init__(self) -> None:
        for name in ("operation_id", "attempt_id", "idempotency_key"):
            normalized = str(getattr(self, name) or "").strip()
            if not normalized:
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, normalized)
        retry = str(self.retry_of_attempt_id or "").strip() or None
        if retry == self.attempt_id:
            raise ValueError("a retry cannot reference its own attempt")
        object.__setattr__(self, "retry_of_attempt_id", retry)

    @property
    def chargeable(self) -> bool:
        return not self.local_result_cache_hit

    @property
    def identity_signature(self) -> tuple[object, ...]:
        return (
            self.operation_id,
            self.attempt_id,
            self.idempotency_key,
            self.retry_of_attempt_id,
            self.local_result_cache_hit,
            self.estimate,
        )


@dataclass(frozen=True, slots=True)
class OperationPermission:
    operation_id: str
    attempt_id: str
    chargeable: bool
    budget_receipt: BudgetReceipt
    reserved_maximum: Decimal

    def __post_init__(self) -> None:
        require_non_negative_decimal("reserved_maximum", self.reserved_maximum)


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    session_id: str
    run_id: str
    operation_id: str
    attempt_id: str
    retry_of_attempt_id: str | None
    status: OperationExecutionStatus
    provider_called: bool | None
    budget_before: BudgetReceipt
    budget_after: BudgetReceipt
    ledger_accepted: bool | None
    ledger_duplicate: bool | None
    operation_actual_cost: Decimal | None
    running_actual_cost: Decimal | None
    unknown_cost: bool
    error_class: str | None
    anomalies: tuple[CostAnomaly, ...]
    session_state: ExecutionSessionState
    trace_sequence_start: int
    trace_sequence_end: int
    idempotent_replay: bool = False

    def __post_init__(self) -> None:
        require_non_negative_decimal("operation_actual_cost", self.operation_actual_cost)
        require_non_negative_decimal("running_actual_cost", self.running_actual_cost)
        if self.trace_sequence_start < 1:
            raise ValueError("trace_sequence_start must be positive")
        if self.trace_sequence_end < self.trace_sequence_start:
            raise ValueError("trace sequence range is invalid")

    def as_idempotent_replay(self) -> "ExecutionReceipt":
        return replace(self, idempotent_replay=True)


@dataclass(frozen=True, slots=True)
class BudgetedExecutionResult:
    value: object | None
    receipt: ExecutionReceipt

    def as_idempotent_replay(self) -> "BudgetedExecutionResult":
        return replace(self, receipt=self.receipt.as_idempotent_replay())


class ExecutionStop(RuntimeError):
    """Raised before execution when no further operation permission is safe."""

    def __init__(
        self,
        reason: str,
        *,
        budget_receipt: BudgetReceipt | None,
        session_state: ExecutionSessionState,
    ) -> None:
        normalized = str(reason or "EXECUTION_STOPPED").strip() or "EXECUTION_STOPPED"
        super().__init__(normalized)
        self.reason = normalized
        self.budget_receipt = budget_receipt
        self.session_state = session_state
