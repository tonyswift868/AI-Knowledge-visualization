from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from decimal import Decimal

from app.cost.budget import BudgetDecision
from app.cost.estimation import CostEstimate
from app.cost.execution_v2.contracts import (
    BudgetedOperation,
    ExecutionSessionState,
    OperationExecutionStatus,
)
from app.cost.execution_v2.ports import (
    CostReceiptPort,
    CostReceiptRecord,
    PortContractError,
    ProviderOperationOutcome,
    ProviderOperationPort,
    RouteMetadata,
    RouteMetadataPort,
)
from app.cost.ledger import ActualCostLedger
from app.cost.runtime_budget import (
    BudgetGatePhase,
    ExecutionBudgetEnvelope,
    ExecutionBudgetGate,
    UnknownCostPolicy,
)

from .audit import build_execution_audit_report
from .contracts import (
    DurableExecutionResult,
    ExecutionAuditReport,
    ExecutionSessionRecord,
    RecoveryDecision,
    RecoveryOutcome,
)
from .serialization import receipt_payload_hash
from .store import SQLiteExecutionStore

CrashSimulationHook = Callable[[str], None]


class DurableExecutionRuntime:
    """Crash-aware budget runtime over W3 ports and the W2 cost ledger.

    A ProviderRuntimeV2 integration supplies the same endpoint-neutral ports as
    BudgetedExecutionSession. The runtime writes the Provider-start claim before
    the chargeable call, then uses a durable receipt inbox before ledger commit.
    """

    def __init__(
        self,
        *,
        store: SQLiteExecutionStore,
        provider_port: ProviderOperationPort,
        route_port: RouteMetadataPort,
        cost_receipt_port: CostReceiptPort,
        crash_hook: CrashSimulationHook | None = None,
    ) -> None:
        self._store = store
        self._provider_port = provider_port
        self._route_port = route_port
        self._cost_receipt_port = cost_receipt_port
        self._crash_hook = crash_hook

    @property
    def store(self) -> SQLiteExecutionStore:
        return self._store

    def create_session(
        self,
        *,
        session_id: str,
        run_id: str,
        job_start_estimate: CostEstimate | None,
        currency: str,
        user_max_budget: Decimal,
        warning_threshold: Decimal,
        hard_stop_threshold: Decimal,
        unknown_cost_policy: UnknownCostPolicy = UnknownCostPolicy.STOP,
    ) -> ExecutionSessionRecord:
        envelope = ExecutionBudgetEnvelope(
            run_id=run_id,
            operation_id="__job_start__",
            currency=currency,
            user_max_budget=user_max_budget,
            warning_threshold=warning_threshold,
            hard_stop_threshold=hard_stop_threshold,
            estimate=job_start_estimate,
            provider_call_required=True,
            routing_available=True,
            unknown_cost_policy=unknown_cost_policy,
            phase=BudgetGatePhase.PRE_EXECUTION,
        )
        budget = ExecutionBudgetGate().evaluate(envelope, ActualCostLedger())
        if budget.decision == BudgetDecision.STOP:
            initial_state = ExecutionSessionState.STOPPED
            stop_reason = ";".join(budget.reasons)
        elif budget.decision == BudgetDecision.WARN:
            initial_state = ExecutionSessionState.WARN
            stop_reason = None
        else:
            initial_state = ExecutionSessionState.ACTIVE
            stop_reason = None
        return self._store.create_session(
            session_id=session_id,
            run_id=run_id,
            currency=currency,
            user_max_budget=user_max_budget,
            warning_threshold=warning_threshold,
            hard_stop_threshold=hard_stop_threshold,
            job_start_estimate=job_start_estimate,
            unknown_cost_policy=unknown_cost_policy,
            initial_state=initial_state,
            stop_reason=stop_reason,
            budget_anomalies=tuple(item.code.value for item in budget.anomalies),
        )

    async def execute(
        self,
        *,
        session_id: str,
        operation: BudgetedOperation,
        receipt_id: str | None = None,
    ) -> DurableExecutionResult:
        return await self._execute_locked(
            session_id=session_id,
            operation=operation,
            receipt_id=receipt_id,
        )

    async def _execute_locked(
        self,
        *,
        session_id: str,
        operation: BudgetedOperation,
        receipt_id: str | None,
    ) -> DurableExecutionResult:
        session = self._store.get_session(session_id)
        if session is None:
            raise KeyError(f"unknown session_id: {session_id}")
        existing = self._store.get_operation(
            session_id=session_id, attempt_id=operation.attempt_id
        )
        if existing is None and session.state in {
            ExecutionSessionState.STOPPED,
            ExecutionSessionState.CANCELLED,
            ExecutionSessionState.COMPLETED,
        }:
            decision = self._store.recover_operation(
                session_id=session_id,
                operation_id=operation.operation_id,
                attempt_id=operation.attempt_id,
            )
            return DurableExecutionResult(
                value=None,
                operation=None,
                receipt=None,
                recovery=decision,
                budget_receipt=None,
            )
        if existing is not None:
            decision = self._store.recover_operation(
                session_id=session_id,
                operation_id=operation.operation_id,
                attempt_id=operation.attempt_id,
            )
            if decision.outcome == RecoveryOutcome.RECEIPT_REPLAY_REQUIRED:
                return self.replay_receipt(decision.receipt_id or "")
            if decision.outcome != RecoveryOutcome.RETRY_SAFE:
                return DurableExecutionResult(
                    value=None,
                    operation=existing,
                    receipt=(
                        self._store.get_receipt(existing.receipt_id)
                        if existing.receipt_id is not None
                        else None
                    ),
                    recovery=decision,
                    budget_receipt=None,
                )
            route = RouteMetadata(
                routing_available=existing.routing_available,
                selected_provider=existing.selected_provider,
                selected_model=existing.selected_model,
                reason="durable authorized route",
            )
        else:
            route = await self._resolve_route(operation)

        permission = self._store.authorize_operation(
            session_id=session_id,
            operation=operation,
            route=route,
        )
        if not permission.allowed:
            decision = self._store.recover_operation(
                session_id=session_id,
                operation_id=operation.operation_id,
                attempt_id=operation.attempt_id,
            )
            return DurableExecutionResult(
                value=None,
                operation=permission.operation,
                receipt=None,
                recovery=decision,
                budget_receipt=permission.budget_receipt,
            )
        self._inject("AFTER_AUTHORIZATION")

        outcome: ProviderOperationOutcome | None = None
        provider_error: BaseException | None = None
        if operation.local_result_cache_hit:
            outcome = ProviderOperationOutcome(
                value=operation.cached_result,
                provider_called=False,
            )
        else:
            claim = self._store.claim_provider_start(
                session_id=session_id, attempt_id=operation.attempt_id
            )
            if not claim.claimed:
                decision = self._store.recover_operation(
                    session_id=session_id,
                    operation_id=operation.operation_id,
                    attempt_id=operation.attempt_id,
                )
                return DurableExecutionResult(
                    value=None,
                    operation=claim.operation,
                    receipt=None,
                    recovery=decision,
                    budget_receipt=permission.budget_receipt,
                )
            self._inject("AFTER_PROVIDER_START")
            try:
                candidate = await self._provider_port.execute(operation)
                if not isinstance(candidate, ProviderOperationOutcome):
                    raise PortContractError(
                        "ProviderOperationPort must return ProviderOperationOutcome"
                    )
                outcome = candidate
            except asyncio.CancelledError as exc:
                provider_error = exc
            except Exception as exc:  # noqa: BLE001 - Provider errors require receipts.
                provider_error = exc

        receipt_record: CostReceiptRecord | None = None
        receipt_error: BaseException | None = None
        try:
            candidate_receipt = self._cost_receipt_port.receipt_for(
                operation,
                outcome=outcome,
                error=provider_error,
            )
            if candidate_receipt is not None and not isinstance(
                candidate_receipt, CostReceiptRecord
            ):
                raise PortContractError(
                    "CostReceiptPort must return CostReceiptRecord or None"
                )
            receipt_record = candidate_receipt
            if (
                receipt_record is not None
                and outcome is not None
                and receipt_record.receipt.provider_called != outcome.provider_called
            ):
                raise PortContractError(
                    "Provider outcome and usage receipt disagree on provider_called"
                )
        except Exception as exc:  # noqa: BLE001 - port contract failures stop safely.
            receipt_error = exc

        if receipt_record is None:
            reason = (
                type(receipt_error).__name__
                if receipt_error is not None
                else type(provider_error).__name__
                if provider_error is not None
                else "COST_RECEIPT_MISSING"
            )
            operation_record = self._store.mark_unresolved_provider_attempt(
                session_id=session_id,
                attempt_id=operation.attempt_id,
                reason=reason,
            )
            decision = self._store.recover_operation(
                session_id=session_id,
                operation_id=operation.operation_id,
                attempt_id=operation.attempt_id,
            )
            return DurableExecutionResult(
                value=None,
                operation=operation_record,
                receipt=None,
                recovery=decision,
                budget_receipt=permission.budget_receipt,
            )

        resolved_receipt_id = receipt_id or self.receipt_identity(
            session_id=session_id,
            attempt_id=operation.attempt_id,
            request_id=receipt_record.receipt.request_id,
            payload_hash=receipt_payload_hash(receipt_record.receipt),
        )
        if isinstance(provider_error, asyncio.CancelledError):
            status = OperationExecutionStatus.CANCELLED
        elif provider_error is not None or not receipt_record.receipt.success:
            status = OperationExecutionStatus.FAILED
        elif outcome is not None and not outcome.provider_called:
            status = OperationExecutionStatus.LOCAL_CACHE_HIT
        else:
            status = OperationExecutionStatus.SUCCEEDED
        effective_error_class = (
            type(provider_error).__name__
            if provider_error is not None
            else receipt_record.receipt.error_class
        )
        self._store.stage_receipt(
            receipt_id=resolved_receipt_id,
            session_id=session_id,
            attempt_id=operation.attempt_id,
            receipt=receipt_record.receipt,
            idempotency_key=(
                receipt_record.idempotency_key or operation.idempotency_key
            ),
            local_cache_baseline=(
                receipt_record.local_cache_baseline or operation.local_cache_baseline
            ),
            execution_status=status,
            error_class=effective_error_class,
        )
        self._inject("AFTER_RECEIPT_STAGED")
        committed = self._store.commit_receipt(
            receipt_id=resolved_receipt_id,
            execution_status=status,
            error_class=effective_error_class,
        )
        self._inject("AFTER_LEDGER_COMMIT")
        decision = self._store.recover_operation(
            session_id=session_id,
            operation_id=operation.operation_id,
            attempt_id=operation.attempt_id,
        )
        return DurableExecutionResult(
            value=(
                outcome.value
                if outcome is not None and provider_error is None
                else None
            ),
            operation=committed.operation,
            receipt=committed.receipt,
            recovery=decision,
            budget_receipt=committed.budget_receipt,
        )

    def replay_receipt(self, receipt_id: str) -> DurableExecutionResult:
        receipt = self._store.get_receipt(receipt_id)
        if receipt is None:
            raise KeyError(f"unknown receipt_id: {receipt_id}")
        operation = self._store.get_operation(
            session_id=receipt.session_id, attempt_id=receipt.attempt_id
        )
        if operation is None:
            raise KeyError(f"unknown attempt_id: {receipt.attempt_id}")
        if operation.execution_status is not None:
            status = operation.execution_status
        elif receipt.receipt.success:
            status = (
                OperationExecutionStatus.SUCCEEDED
                if receipt.receipt.provider_called
                else OperationExecutionStatus.LOCAL_CACHE_HIT
            )
        else:
            status = OperationExecutionStatus.FAILED
        committed = self._store.commit_receipt(
            receipt_id=receipt_id,
            execution_status=status,
            error_class=operation.error_class or receipt.receipt.error_class,
        )
        decision = self._store.recover_operation(
            session_id=receipt.session_id,
            operation_id=receipt.operation_id,
            attempt_id=receipt.attempt_id,
        )
        return DurableExecutionResult(
            value=None,
            operation=committed.operation,
            receipt=committed.receipt,
            recovery=decision,
            budget_receipt=committed.budget_receipt,
        )

    def recover(
        self,
        *,
        session_id: str,
        attempt_id: str,
        operation_id: str | None = None,
    ) -> RecoveryDecision:
        return self._store.recover_operation(
            session_id=session_id,
            operation_id=operation_id,
            attempt_id=attempt_id,
        )

    def cancel_operation(
        self,
        *,
        session_id: str,
        attempt_id: str,
        reason: str = "OPERATION_CANCELLED",
    ) -> RecoveryDecision:
        operation = self._store.cancel_operation(
            session_id=session_id, attempt_id=attempt_id, reason=reason
        )
        return self._store.recover_operation(
            session_id=session_id,
            operation_id=operation.operation_id,
            attempt_id=attempt_id,
        )

    def stop_session(
        self, *, session_id: str, reason: str = "SESSION_STOPPED"
    ) -> ExecutionSessionRecord:
        return self._store.transition_session(
            session_id=session_id,
            state=ExecutionSessionState.STOPPED,
            reason=reason,
        )

    def cancel_session(
        self, *, session_id: str, reason: str = "SESSION_CANCELLED"
    ) -> ExecutionSessionRecord:
        return self._store.transition_session(
            session_id=session_id,
            state=ExecutionSessionState.CANCELLED,
            reason=reason,
        )

    def complete_session(self, *, session_id: str) -> ExecutionSessionRecord:
        return self._store.transition_session(
            session_id=session_id,
            state=ExecutionSessionState.COMPLETED,
            reason="SESSION_COMPLETED",
        )

    def audit_report(self, session_id: str) -> ExecutionAuditReport:
        return build_execution_audit_report(self._store, session_id=session_id)

    async def _resolve_route(self, operation: BudgetedOperation) -> RouteMetadata:
        try:
            route = await self._route_port.metadata_for(operation)
            if not isinstance(route, RouteMetadata):
                raise PortContractError("RouteMetadataPort must return RouteMetadata")
            return route
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - unavailable routing is fail-safe.
            return RouteMetadata(
                routing_available=False,
                selected_provider=None,
                selected_model=None,
                reason=type(exc).__name__,
            )

    def _inject(self, phase: str) -> None:
        if self._crash_hook is not None:
            self._crash_hook(phase)

    @staticmethod
    def receipt_identity(
        *, session_id: str, attempt_id: str, request_id: str, payload_hash: str
    ) -> str:
        identity = f"{session_id}\x1f{attempt_id}\x1f{request_id}\x1f{payload_hash}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return "receipt-" + digest
