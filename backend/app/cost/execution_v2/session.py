from __future__ import annotations

import asyncio
from decimal import Decimal

from app.cost.budget import BudgetDecision
from app.cost.estimation import CostEstimate
from app.cost.execution_v2.contracts import (
    BudgetedExecutionResult,
    BudgetedOperation,
    ExecutionReceipt,
    ExecutionSessionState,
    ExecutionStop,
    OperationExecutionStatus,
)
from app.cost.execution_v2.guard import OperationBudgetGuard
from app.cost.execution_v2.ports import (
    CostReceiptPort,
    CostReceiptRecord,
    PortContractError,
    ProviderOperationOutcome,
    ProviderOperationPort,
    RouteMetadata,
    RouteMetadataPort,
)
from app.cost.execution_v2.trace import (
    BudgetExecutionTrace,
    BudgetTraceEventType,
)
from app.cost.ledger import ActualCostLedger, LedgerIdempotencyConflict
from app.cost.runtime_budget import (
    BudgetReceipt,
    CostAnomaly,
    CostAnomalyCode,
    CostAnomalySeverity,
    UnknownCostPolicy,
)


class BudgetedExecutionSession:
    """Budget-aware async wrapper around future Provider operation adapters."""

    def __init__(
        self,
        *,
        session_id: str,
        run_id: str,
        job_start_estimate: CostEstimate | None,
        currency: str,
        user_max_budget: Decimal,
        warning_threshold: Decimal,
        hard_stop_threshold: Decimal,
        provider_port: ProviderOperationPort,
        route_port: RouteMetadataPort,
        cost_receipt_port: CostReceiptPort,
        ledger: ActualCostLedger | None = None,
        unknown_cost_policy: UnknownCostPolicy = UnknownCostPolicy.STOP,
        minimum_expected_cache_hit_ratio: Decimal | None = None,
    ) -> None:
        self._session_id = str(session_id or "").strip()
        self._run_id = str(run_id or "").strip()
        if not self._session_id or not self._run_id:
            raise ValueError("session_id and run_id must not be empty")
        self._provider_port = provider_port
        self._route_port = route_port
        self._cost_receipt_port = cost_receipt_port
        self._ledger = ledger or ActualCostLedger()
        self._trace = BudgetExecutionTrace(run_id=self._run_id)
        self._guard = OperationBudgetGuard(
            run_id=self._run_id,
            currency=currency,
            user_max_budget=user_max_budget,
            warning_threshold=warning_threshold,
            hard_stop_threshold=hard_stop_threshold,
            ledger=self._ledger,
            unknown_cost_policy=unknown_cost_policy,
            minimum_expected_cache_hit_ratio=minimum_expected_cache_hit_ratio,
        )
        self._state_lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Task[BudgetedExecutionResult]] = {}
        self._completed: dict[str, BudgetedExecutionResult] = {}
        self._attempt_signatures: dict[str, tuple[object, ...]] = {}
        self._cancelled_attempts: dict[str, str] = {}
        self._state = ExecutionSessionState.ACTIVE
        self._start_budget_receipt = self._guard.evaluate_job_start(
            job_start_estimate
        )
        if self._start_budget_receipt.decision == BudgetDecision.STOP:
            self._state = ExecutionSessionState.STOPPED
        elif self._start_budget_receipt.decision == BudgetDecision.WARN:
            self._state = ExecutionSessionState.WARN
        self._trace.record(
            BudgetTraceEventType.JOB_START_EVALUATED,
            session_state=self._state,
            operation_id="__job_start__",
            budget_decision=self._start_budget_receipt.decision,
            budget_reasons=self._start_budget_receipt.reasons,
        )
        if self._state == ExecutionSessionState.WARN:
            self._trace.record(
                BudgetTraceEventType.SESSION_WARNED,
                session_state=self._state,
                operation_id="__job_start__",
                budget_decision=self._start_budget_receipt.decision,
                budget_reasons=self._start_budget_receipt.reasons,
            )
        elif self._state == ExecutionSessionState.STOPPED:
            self._trace.record(
                BudgetTraceEventType.SESSION_STOPPED,
                session_state=self._state,
                operation_id="__job_start__",
                budget_decision=self._start_budget_receipt.decision,
                budget_reasons=self._start_budget_receipt.reasons,
            )

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def state(self) -> ExecutionSessionState:
        return self._state

    @property
    def start_budget_receipt(self) -> BudgetReceipt:
        return self._start_budget_receipt

    @property
    def ledger(self) -> ActualCostLedger:
        return self._ledger

    @property
    def trace(self) -> BudgetExecutionTrace:
        return self._trace

    @property
    def running_actual_cost(self) -> Decimal | None:
        return self._ledger.snapshot(
            run_id=self._run_id
        ).actual_calculated_provider_cost

    async def execute(
        self,
        operation: BudgetedOperation,
    ) -> BudgetedExecutionResult:
        async with self._state_lock:
            existing_signature = self._attempt_signatures.get(operation.attempt_id)
            if (
                existing_signature is not None
                and existing_signature != operation.identity_signature
            ):
                raise ValueError("attempt_id was reused with different operation data")
            completed = self._completed.get(operation.attempt_id)
            if completed is not None:
                return completed.as_idempotent_replay()
            in_flight = self._inflight.get(operation.attempt_id)
            if in_flight is not None:
                task = in_flight
                joined_existing = True
            else:
                self._raise_if_terminal()
                self._validate_retry_lineage(operation)
                self._attempt_signatures[operation.attempt_id] = (
                    operation.identity_signature
                )
                task = asyncio.create_task(
                    self._execute_once(operation),
                    name=f"budgeted:{self._run_id}:{operation.attempt_id}",
                )
                self._inflight[operation.attempt_id] = task
                joined_existing = False
        result = await asyncio.shield(task)
        return result.as_idempotent_replay() if joined_existing else result

    async def cancel_operation(
        self,
        attempt_id: str,
        *,
        reason: str = "OPERATION_CANCELLED",
    ) -> ExecutionReceipt | None:
        normalized = str(attempt_id or "").strip()
        if not normalized:
            raise ValueError("attempt_id must not be empty")
        async with self._state_lock:
            completed = self._completed.get(normalized)
            if completed is not None:
                return completed.receipt
            task = self._inflight.get(normalized)
            if task is None:
                return None
            self._cancelled_attempts[normalized] = (
                str(reason or "OPERATION_CANCELLED").strip()
                or "OPERATION_CANCELLED"
            )
            task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        value = result[0]
        if isinstance(value, BudgetedExecutionResult):
            return value.receipt
        completed = self._completed.get(normalized)
        return completed.receipt if completed is not None else None

    async def cancel(
        self,
        *,
        reason: str = "SESSION_CANCELLED",
    ) -> tuple[ExecutionReceipt, ...]:
        normalized_reason = (
            str(reason or "SESSION_CANCELLED").strip() or "SESSION_CANCELLED"
        )
        async with self._state_lock:
            if self._state in {
                ExecutionSessionState.STOPPED,
                ExecutionSessionState.COMPLETED,
            }:
                return ()
            if self._state != ExecutionSessionState.CANCELLED:
                self._state = ExecutionSessionState.CANCELLED
                self._guard.cancel(normalized_reason)
                self._trace.record(
                    BudgetTraceEventType.SESSION_CANCELLED,
                    session_state=self._state,
                    error_class=normalized_reason,
                )
            tasks = tuple(self._inflight.items())
            for attempt_id, task in tasks:
                self._cancelled_attempts[attempt_id] = normalized_reason
                task.cancel()
        if tasks:
            await asyncio.gather(
                *(task for _attempt_id, task in tasks),
                return_exceptions=True,
            )
        return tuple(
            self._completed[attempt_id].receipt
            for attempt_id, _task in tasks
            if attempt_id in self._completed
        )

    async def complete(self) -> ExecutionSessionState:
        async with self._state_lock:
            if self._inflight:
                raise RuntimeError("cannot complete a session with operations in flight")
            if self._state in {
                ExecutionSessionState.STOPPED,
                ExecutionSessionState.CANCELLED,
                ExecutionSessionState.COMPLETED,
            }:
                return self._state
            self._state = ExecutionSessionState.COMPLETED
            self._guard.cancel("SESSION_COMPLETED")
            self._trace.record(
                BudgetTraceEventType.SESSION_COMPLETED,
                session_state=self._state,
            )
            return self._state

    def receipt(self, attempt_id: str) -> ExecutionReceipt | None:
        completed = self._completed.get(str(attempt_id or "").strip())
        return completed.receipt if completed is not None else None

    def _validate_retry_lineage(self, operation: BudgetedOperation) -> None:
        parent_id = operation.retry_of_attempt_id
        if parent_id is None:
            return
        parent = self._completed.get(parent_id)
        if parent is None:
            raise ValueError("retry parent must be a completed attempt")
        if parent.receipt.operation_id != operation.operation_id:
            raise ValueError("retry parent belongs to a different operation")
        if parent.receipt.status in {
            OperationExecutionStatus.SUCCEEDED,
            OperationExecutionStatus.LOCAL_CACHE_HIT,
        }:
            raise ValueError("a successful operation cannot be retried")

    async def _execute_once(
        self,
        operation: BudgetedOperation,
    ) -> BudgetedExecutionResult:
        route = RouteMetadata(
            routing_available=False,
            selected_provider=None,
            selected_model=None,
            reason="route metadata unavailable",
        )
        budget_before = self._start_budget_receipt
        trace_start = max(1, self._trace.last_sequence + 1)
        outcome: ProviderOperationOutcome | None = None
        provider_started = False
        try:
            try:
                resolved_route = await self._route_port.metadata_for(operation)
                if not isinstance(resolved_route, RouteMetadata):
                    raise PortContractError(
                        "RouteMetadataPort must return RouteMetadata"
                    )
                route = resolved_route
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                route = RouteMetadata(
                    routing_available=False,
                    selected_provider=None,
                    selected_model=None,
                    reason=type(exc).__name__,
                )

            async with self._state_lock:
                self._raise_if_terminal()
                try:
                    permission = self._guard.before_operation(operation, route)
                except ExecutionStop as stop:
                    self._state = ExecutionSessionState.STOPPED
                    self._trace.record(
                        BudgetTraceEventType.OPERATION_PERMISSION_STOPPED,
                        session_state=self._state,
                        operation_id=operation.operation_id,
                        attempt_id=operation.attempt_id,
                        retry_of_attempt_id=operation.retry_of_attempt_id,
                        budget_decision=(
                            stop.budget_receipt.decision
                            if stop.budget_receipt is not None
                            else BudgetDecision.STOP
                        ),
                        budget_reasons=(
                            stop.budget_receipt.reasons
                            if stop.budget_receipt is not None
                            else (stop.reason,)
                        ),
                    )
                    self._trace.record(
                        BudgetTraceEventType.SESSION_STOPPED,
                        session_state=self._state,
                        operation_id=operation.operation_id,
                        attempt_id=operation.attempt_id,
                    )
                    self._inflight.pop(operation.attempt_id, None)
                    raise
                budget_before = permission.budget_receipt
                if budget_before.decision == BudgetDecision.WARN:
                    if self._state == ExecutionSessionState.ACTIVE:
                        self._state = ExecutionSessionState.WARN
                        self._trace.record(
                            BudgetTraceEventType.SESSION_WARNED,
                            session_state=self._state,
                            operation_id=operation.operation_id,
                            attempt_id=operation.attempt_id,
                            budget_decision=budget_before.decision,
                            budget_reasons=budget_before.reasons,
                        )
                    permission_event = BudgetTraceEventType.OPERATION_PERMISSION_WARNED
                else:
                    permission_event = BudgetTraceEventType.OPERATION_PERMISSION_GRANTED
                permission_trace = self._trace.record(
                    permission_event,
                    session_state=self._state,
                    operation_id=operation.operation_id,
                    attempt_id=operation.attempt_id,
                    retry_of_attempt_id=operation.retry_of_attempt_id,
                    budget_decision=budget_before.decision,
                    budget_reasons=budget_before.reasons,
                )
                trace_start = permission_trace.sequence

            if operation.local_result_cache_hit:
                outcome = ProviderOperationOutcome(
                    value=operation.cached_result,
                    provider_called=False,
                )
                self._trace.record(
                    BudgetTraceEventType.LOCAL_CACHE_USED,
                    session_state=self._state,
                    operation_id=operation.operation_id,
                    attempt_id=operation.attempt_id,
                    retry_of_attempt_id=operation.retry_of_attempt_id,
                    provider_called=False,
                )
            else:
                provider_started = True
                self._trace.record(
                    BudgetTraceEventType.OPERATION_STARTED,
                    session_state=self._state,
                    operation_id=operation.operation_id,
                    attempt_id=operation.attempt_id,
                    retry_of_attempt_id=operation.retry_of_attempt_id,
                    provider_called=None,
                )
                outcome = await self._provider_port.execute(operation)
                if not isinstance(outcome, ProviderOperationOutcome):
                    raise PortContractError(
                        "ProviderOperationPort must return ProviderOperationOutcome"
                    )
            return await self._finalize_operation(
                operation=operation,
                route=route,
                budget_before=budget_before,
                trace_start=trace_start,
                outcome=outcome,
                error=None,
                provider_started=provider_started,
            )
        except ExecutionStop:
            async with self._state_lock:
                self._inflight.pop(operation.attempt_id, None)
            raise
        except asyncio.CancelledError as exc:
            return await self._finalize_operation(
                operation=operation,
                route=route,
                budget_before=budget_before,
                trace_start=trace_start,
                outcome=outcome,
                error=exc,
                provider_started=provider_started,
            )
        except Exception as exc:
            return await self._finalize_operation(
                operation=operation,
                route=route,
                budget_before=budget_before,
                trace_start=trace_start,
                outcome=outcome,
                error=exc,
                provider_started=provider_started,
            )

    async def _finalize_operation(
        self,
        *,
        operation: BudgetedOperation,
        route: RouteMetadata,
        budget_before: BudgetReceipt,
        trace_start: int,
        outcome: ProviderOperationOutcome | None,
        error: BaseException | None,
        provider_started: bool,
    ) -> BudgetedExecutionResult:
        receipt_record: CostReceiptRecord | None = None
        receipt_error: BaseException | None = None
        try:
            candidate = self._cost_receipt_port.receipt_for(
                operation,
                outcome=outcome,
                error=error,
            )
            if candidate is not None and not isinstance(candidate, CostReceiptRecord):
                raise PortContractError(
                    "CostReceiptPort must return CostReceiptRecord or None"
                )
            receipt_record = candidate
            self._validate_receipt_contract(operation, outcome, receipt_record)
        except Exception as exc:
            receipt_error = exc
            receipt_record = None

        async with self._state_lock:
            cancelled = (
                operation.attempt_id in self._cancelled_attempts
                or isinstance(error, asyncio.CancelledError)
                or self._state == ExecutionSessionState.CANCELLED
            )
            ingestion = None
            if receipt_record is not None:
                try:
                    ingestion = self._ledger.ingest(
                        run_id=self._run_id,
                        operation_id=operation.operation_id,
                        receipt=receipt_record.receipt,
                        idempotency_key=(
                            receipt_record.idempotency_key
                            or operation.idempotency_key
                        ),
                        local_cache_baseline=(
                            receipt_record.local_cache_baseline
                            or operation.local_cache_baseline
                        ),
                    )
                except (LedgerIdempotencyConflict, ValueError) as exc:
                    receipt_error = exc
                    ingestion = None

            if ingestion is not None:
                budget_after = self._guard.after_operation(operation, route)
                receipt_event = (
                    BudgetTraceEventType.COST_RECEIPT_INGESTED
                    if ingestion.accepted
                    else BudgetTraceEventType.COST_RECEIPT_DUPLICATE
                )
                self._trace.record(
                    receipt_event,
                    session_state=self._state,
                    operation_id=operation.operation_id,
                    attempt_id=operation.attempt_id,
                    retry_of_attempt_id=operation.retry_of_attempt_id,
                    provider_called=receipt_record.receipt.provider_called,
                    ledger_accepted=ingestion.accepted,
                    ledger_duplicate=ingestion.duplicate,
                )
            elif cancelled and not provider_started:
                budget_after = self._guard.after_operation(operation, route)
            else:
                reason = (
                    "COST_RECEIPT_INVALID"
                    if receipt_error is not None
                    else "COST_RECEIPT_MISSING"
                )
                if receipt_error is None:
                    receipt_error = PortContractError(reason)
                budget_after = self._guard.force_stop(
                    reason=reason,
                    operation=operation,
                    route=route,
                )
                self._trace.record(
                    BudgetTraceEventType.COST_RECEIPT_MISSING,
                    session_state=self._state,
                    operation_id=operation.operation_id,
                    attempt_id=operation.attempt_id,
                    retry_of_attempt_id=operation.retry_of_attempt_id,
                    provider_called=(
                        outcome.provider_called if outcome is not None else None
                    ),
                    error_class=(
                        type(receipt_error).__name__
                        if receipt_error is not None
                        else reason
                    ),
                )

            if (
                budget_after.decision == BudgetDecision.STOP
                and self._state != ExecutionSessionState.CANCELLED
            ):
                self._state = ExecutionSessionState.STOPPED
                self._trace.record(
                    BudgetTraceEventType.SESSION_STOPPED,
                    session_state=self._state,
                    operation_id=operation.operation_id,
                    attempt_id=operation.attempt_id,
                    budget_decision=budget_after.decision,
                    budget_reasons=budget_after.reasons,
                )
            elif (
                budget_after.decision == BudgetDecision.WARN
                and self._state == ExecutionSessionState.ACTIVE
            ):
                self._state = ExecutionSessionState.WARN
                self._trace.record(
                    BudgetTraceEventType.SESSION_WARNED,
                    session_state=self._state,
                    operation_id=operation.operation_id,
                    attempt_id=operation.attempt_id,
                    budget_decision=budget_after.decision,
                    budget_reasons=budget_after.reasons,
                )

            operation_snapshot = self._ledger.snapshot(
                run_id=self._run_id,
                operation_id=operation.operation_id,
            )
            run_snapshot = self._ledger.snapshot(run_id=self._run_id)
            effective_error = receipt_error or error
            if cancelled:
                status = OperationExecutionStatus.CANCELLED
                completed_event = BudgetTraceEventType.OPERATION_CANCELLED
            elif effective_error is not None:
                status = OperationExecutionStatus.FAILED
                completed_event = BudgetTraceEventType.OPERATION_FAILED
            elif outcome is not None and not outcome.provider_called:
                status = OperationExecutionStatus.LOCAL_CACHE_HIT
                completed_event = BudgetTraceEventType.OPERATION_SUCCEEDED
            else:
                status = OperationExecutionStatus.SUCCEEDED
                completed_event = BudgetTraceEventType.OPERATION_SUCCEEDED

            provider_called = (
                receipt_record.receipt.provider_called
                if receipt_record is not None
                else outcome.provider_called
                if outcome is not None
                else True
                if provider_started
                else False
            )
            anomalies = list(budget_after.anomalies)
            attempt_actual_cost = (
                ingestion.breakdown.total_cost if ingestion is not None else None
            )
            if (
                operation.estimate is not None
                and attempt_actual_cost is not None
                and attempt_actual_cost > operation.estimate.maximum
                and CostAnomalyCode.ACTUAL_EXCEEDS_EXPECTED_HIGH
                not in {item.code for item in anomalies}
            ):
                anomalies.append(
                    CostAnomaly(
                        code=CostAnomalyCode.ACTUAL_EXCEEDS_EXPECTED_HIGH,
                        severity=CostAnomalySeverity.CRITICAL,
                        message=(
                            "Operation actual cost exceeded its expected high estimate."
                        ),
                        observed=attempt_actual_cost,
                        expected=operation.estimate.maximum,
                    )
                )
            final_event = self._trace.record(
                completed_event,
                session_state=self._state,
                operation_id=operation.operation_id,
                attempt_id=operation.attempt_id,
                retry_of_attempt_id=operation.retry_of_attempt_id,
                budget_decision=budget_after.decision,
                budget_reasons=budget_after.reasons,
                provider_called=provider_called,
                ledger_accepted=(
                    ingestion.accepted if ingestion is not None else None
                ),
                ledger_duplicate=(
                    ingestion.duplicate if ingestion is not None else None
                ),
                error_class=(
                    type(effective_error).__name__
                    if effective_error is not None
                    else None
                ),
            )
            execution_receipt = ExecutionReceipt(
                session_id=self._session_id,
                run_id=self._run_id,
                operation_id=operation.operation_id,
                attempt_id=operation.attempt_id,
                retry_of_attempt_id=operation.retry_of_attempt_id,
                status=status,
                provider_called=provider_called,
                budget_before=budget_before,
                budget_after=budget_after,
                ledger_accepted=(
                    ingestion.accepted if ingestion is not None else None
                ),
                ledger_duplicate=(
                    ingestion.duplicate if ingestion is not None else None
                ),
                operation_actual_cost=(
                    ingestion.breakdown.total_cost
                    if ingestion is not None
                    else None
                ),
                running_actual_cost=run_snapshot.actual_calculated_provider_cost,
                unknown_cost=(
                    ingestion is None
                    or not operation_snapshot.actual_cost_complete
                ),
                error_class=(
                    type(effective_error).__name__
                    if effective_error is not None
                    else None
                ),
                anomalies=tuple(anomalies),
                session_state=self._state,
                trace_sequence_start=trace_start,
                trace_sequence_end=final_event.sequence,
            )
            result = BudgetedExecutionResult(
                value=(
                    outcome.value
                    if outcome is not None and effective_error is None
                    else None
                ),
                receipt=execution_receipt,
            )
            self._completed[operation.attempt_id] = result
            self._inflight.pop(operation.attempt_id, None)
            self._cancelled_attempts.pop(operation.attempt_id, None)
            return result

    @staticmethod
    def _validate_receipt_contract(
        operation: BudgetedOperation,
        outcome: ProviderOperationOutcome | None,
        receipt_record: CostReceiptRecord | None,
    ) -> None:
        if receipt_record is None:
            return
        receipt = receipt_record.receipt
        if operation.local_result_cache_hit:
            if receipt.provider_called or receipt.local_result_cache_hit is not True:
                raise PortContractError(
                    "local cache receipt must report provider_called=false and cache hit"
                )
        if outcome is not None and receipt.provider_called != outcome.provider_called:
            raise PortContractError(
                "Provider outcome and usage receipt disagree on provider_called"
            )

    def _raise_if_terminal(self) -> None:
        if self._state in {
            ExecutionSessionState.STOPPED,
            ExecutionSessionState.CANCELLED,
            ExecutionSessionState.COMPLETED,
        }:
            raise ExecutionStop(
                {
                    ExecutionSessionState.STOPPED: (
                        self._guard.stop_reason or "EXECUTION_STOPPED"
                    ),
                    ExecutionSessionState.CANCELLED: "SESSION_CANCELLED",
                    ExecutionSessionState.COMPLETED: "SESSION_COMPLETED",
                }[self._state],
                budget_receipt=(
                    self._guard.stop_receipt or self._start_budget_receipt
                ),
                session_state=self._state,
            )
