from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.cost.budget import BudgetDecision
from app.cost.contracts import LocalCacheSavingsBaseline
from app.cost.estimation import CostEstimate
from app.cost.execution_v2.contracts import (
    ExecutionSessionState,
    OperationExecutionStatus,
)
from app.cost.ledger import CurrencyTotal, LedgerSnapshot
from app.cost.runtime_budget import BudgetReceipt, UnknownCostPolicy
from app.providers.telemetry import ProviderUsageReceipt


class DurableExecutionError(RuntimeError):
    """Base error for durable execution state failures."""


class ExecutionStoreClosed(DurableExecutionError):
    pass


class ExecutionStoreCorruption(DurableExecutionError):
    def __init__(self, message: str, *, original: BaseException | None = None) -> None:
        super().__init__(message)
        self.original = original


class ExecutionSchemaMissing(DurableExecutionError):
    pass


class DurableIdentityConflict(DurableExecutionError, ValueError):
    pass


class DurableExecutionStopped(DurableExecutionError):
    def __init__(self, reason: str, *, session_state: ExecutionSessionState) -> None:
        normalized = str(reason or "EXECUTION_STOPPED").strip() or "EXECUTION_STOPPED"
        super().__init__(normalized)
        self.reason = normalized
        self.session_state = session_state


class OperationState(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    PROVIDER_STARTED = "PROVIDER_STARTED"
    RECEIPT_STAGED = "RECEIPT_STAGED"
    ACCOUNTED = "ACCOUNTED"
    CANCELLED = "CANCELLED"
    STOPPED = "STOPPED"


class ReceiptAccountingState(str, Enum):
    STAGED = "STAGED"
    ACCOUNTED = "ACCOUNTED"


class RecoveryOutcome(str, Enum):
    RETRY_SAFE = "RETRY_SAFE"
    RECEIPT_REPLAY_REQUIRED = "RECEIPT_REPLAY_REQUIRED"
    ALREADY_ACCOUNTED = "ALREADY_ACCOUNTED"
    DO_NOT_RETRY_CHARGEABLE_CALL = "DO_NOT_RETRY_CHARGEABLE_CALL"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class ExecutionSessionRecord:
    session_id: str
    run_id: str
    state: ExecutionSessionState
    currency: str
    user_max_budget: Decimal
    warning_threshold: Decimal
    hard_stop_threshold: Decimal
    unknown_cost_policy: UnknownCostPolicy
    job_start_estimate: CostEstimate | None
    stop_reason: str | None
    budget_anomalies: tuple[str, ...]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class OperationRecord:
    session_id: str
    operation_id: str
    attempt_id: str
    idempotency_key: str
    retry_of_attempt_id: str | None
    state: OperationState
    chargeable: bool
    provider_called: bool | None
    routing_available: bool
    selected_provider: str | None
    selected_model: str | None
    estimate: CostEstimate | None
    local_cache_baseline: LocalCacheSavingsBaseline | None
    receipt_id: str | None
    execution_status: OperationExecutionStatus | None
    error_class: str | None
    budget_decision: BudgetDecision | None
    budget_reasons: tuple[str, ...]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class UsageReceiptRecord:
    receipt_id: str
    session_id: str
    operation_id: str
    attempt_id: str
    payload_hash: str
    idempotency_key: str
    receipt: ProviderUsageReceipt
    local_cache_baseline: LocalCacheSavingsBaseline | None
    accounting_state: ReceiptAccountingState
    actual_cost: Decimal | None
    currency: str | None
    cost_status: str | None
    unavailable_reason: str | None
    pricing_version: str | None
    staged_at: str
    accounted_at: str | None


@dataclass(frozen=True, slots=True)
class DurableOperationPermission:
    operation: OperationRecord
    budget_receipt: BudgetReceipt
    newly_authorized: bool

    @property
    def allowed(self) -> bool:
        return self.budget_receipt.decision != BudgetDecision.STOP


@dataclass(frozen=True, slots=True)
class ProviderStartClaim:
    operation: OperationRecord
    claimed: bool


@dataclass(frozen=True, slots=True)
class ReceiptStageResult:
    record: UsageReceiptRecord
    accepted: bool
    duplicate: bool


@dataclass(frozen=True, slots=True)
class ReceiptCommitResult:
    receipt: UsageReceiptRecord
    operation: OperationRecord
    session: ExecutionSessionRecord
    budget_receipt: BudgetReceipt
    ledger_snapshot: LedgerSnapshot
    newly_accounted: bool


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    session_id: str
    operation_id: str | None
    attempt_id: str
    receipt_id: str | None
    outcome: RecoveryOutcome
    reason: str


@dataclass(frozen=True, slots=True)
class IntegrityCheckReport:
    ok: bool
    messages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CheckpointReport:
    mode: str
    busy: int
    log_frames: int
    checkpointed_frames: int


@dataclass(frozen=True, slots=True)
class RetryLineageRecord:
    operation_id: str
    attempt_id: str
    retry_of_attempt_id: str | None
    state: OperationState
    execution_status: OperationExecutionStatus | None


@dataclass(frozen=True, slots=True)
class ExecutionAuditReport:
    session_id: str
    run_id: str
    session_state: ExecutionSessionState
    estimate_currency: str | None
    estimated_minimum: Decimal | None
    estimated_expected: Decimal | None
    estimated_high: Decimal | None
    actual_known_cost: Decimal | None
    actual_known_costs: tuple[CurrencyTotal, ...]
    actual_cost_complete: bool
    unknown_receipts: int
    unpriced_receipts: int
    staged_receipts: int
    provider_calls: int
    local_cache_avoided_calls: int
    provider_cache_hit_tokens: int
    provider_cache_miss_tokens: int
    output_tokens: int
    provider_cache_hit_ratio: Decimal | None
    provider_cache_savings: Decimal
    local_cache_savings: Decimal
    estimated_savings: Decimal
    savings_classification: str
    pricing_versions: tuple[str, ...]
    retry_lineage: tuple[RetryLineageRecord, ...]
    stop_reason: str | None
    budget_anomalies: tuple[str, ...]
    generated_at: str

    def to_public_dict(self) -> dict[str, object]:
        money = lambda value: str(value) if value is not None else None
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "session_state": self.session_state.value,
            "estimates": {
                "currency": self.estimate_currency,
                "minimum": money(self.estimated_minimum),
                "expected": money(self.estimated_expected),
                "high": money(self.estimated_high),
                "classification": "estimated",
            },
            "actual": {
                "known_cost": money(self.actual_known_cost),
                "known_costs": [
                    {"currency": item.currency, "amount": str(item.amount)}
                    for item in self.actual_known_costs
                ],
                "complete": self.actual_cost_complete,
                "unknown_receipts": self.unknown_receipts,
                "unpriced_receipts": self.unpriced_receipts,
                "staged_receipts": self.staged_receipts,
            },
            "cache": {
                "provider_calls": self.provider_calls,
                "local_cache_avoided_calls": self.local_cache_avoided_calls,
                "provider_cache_hit_tokens": self.provider_cache_hit_tokens,
                "provider_cache_miss_tokens": self.provider_cache_miss_tokens,
                "output_tokens": self.output_tokens,
                "provider_cache_hit_ratio": money(self.provider_cache_hit_ratio),
            },
            "savings": {
                "provider_cache": str(self.provider_cache_savings),
                "local_cache": str(self.local_cache_savings),
                "total": str(self.estimated_savings),
                "classification": self.savings_classification,
            },
            "pricing_versions": list(self.pricing_versions),
            "retry_lineage": [
                {
                    "operation_id": item.operation_id,
                    "attempt_id": item.attempt_id,
                    "retry_of_attempt_id": item.retry_of_attempt_id,
                    "state": item.state.value,
                    "execution_status": (
                        item.execution_status.value
                        if item.execution_status is not None
                        else None
                    ),
                }
                for item in self.retry_lineage
            ],
            "stop_reason": self.stop_reason,
            "budget_anomalies": list(self.budget_anomalies),
            "generated_at": self.generated_at,
        }


@dataclass(frozen=True, slots=True)
class DurableExecutionResult:
    value: object | None
    operation: OperationRecord | None
    receipt: UsageReceiptRecord | None
    recovery: RecoveryDecision
    budget_receipt: BudgetReceipt | None
