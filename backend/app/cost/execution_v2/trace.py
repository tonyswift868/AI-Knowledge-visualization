from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from app.cost.budget import BudgetDecision
from app.cost.execution_v2.contracts import ExecutionSessionState


class BudgetTraceEventType(str, Enum):
    JOB_START_EVALUATED = "JOB_START_EVALUATED"
    OPERATION_PERMISSION_GRANTED = "OPERATION_PERMISSION_GRANTED"
    OPERATION_PERMISSION_WARNED = "OPERATION_PERMISSION_WARNED"
    OPERATION_PERMISSION_STOPPED = "OPERATION_PERMISSION_STOPPED"
    OPERATION_STARTED = "OPERATION_STARTED"
    LOCAL_CACHE_USED = "LOCAL_CACHE_USED"
    COST_RECEIPT_INGESTED = "COST_RECEIPT_INGESTED"
    COST_RECEIPT_DUPLICATE = "COST_RECEIPT_DUPLICATE"
    COST_RECEIPT_MISSING = "COST_RECEIPT_MISSING"
    OPERATION_SUCCEEDED = "OPERATION_SUCCEEDED"
    OPERATION_FAILED = "OPERATION_FAILED"
    OPERATION_CANCELLED = "OPERATION_CANCELLED"
    SESSION_WARNED = "SESSION_WARNED"
    SESSION_STOPPED = "SESSION_STOPPED"
    SESSION_CANCELLED = "SESSION_CANCELLED"
    SESSION_COMPLETED = "SESSION_COMPLETED"


@dataclass(frozen=True, slots=True)
class BudgetTraceEvent:
    sequence: int
    occurred_at: str
    event_type: BudgetTraceEventType
    session_state: ExecutionSessionState
    run_id: str
    operation_id: str | None = None
    attempt_id: str | None = None
    retry_of_attempt_id: str | None = None
    budget_decision: BudgetDecision | None = None
    budget_reasons: tuple[str, ...] = ()
    provider_called: bool | None = None
    ledger_accepted: bool | None = None
    ledger_duplicate: bool | None = None
    error_class: str | None = None


class BudgetExecutionTrace:
    """Thread-safe metadata-only trace; it never stores payloads or Provider output."""

    def __init__(self, *, run_id: str) -> None:
        normalized_run = str(run_id or "").strip()
        if not normalized_run:
            raise ValueError("run_id must not be empty")
        self._run_id = normalized_run
        self._events: list[BudgetTraceEvent] = []
        self._lock = threading.Lock()

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def last_sequence(self) -> int:
        with self._lock:
            return len(self._events)

    def record(
        self,
        event_type: BudgetTraceEventType,
        *,
        session_state: ExecutionSessionState,
        operation_id: str | None = None,
        attempt_id: str | None = None,
        retry_of_attempt_id: str | None = None,
        budget_decision: BudgetDecision | None = None,
        budget_reasons: tuple[str, ...] = (),
        provider_called: bool | None = None,
        ledger_accepted: bool | None = None,
        ledger_duplicate: bool | None = None,
        error_class: str | None = None,
    ) -> BudgetTraceEvent:
        with self._lock:
            event = BudgetTraceEvent(
                sequence=len(self._events) + 1,
                occurred_at=datetime.now(timezone.utc).isoformat(),
                event_type=event_type,
                session_state=session_state,
                run_id=self._run_id,
                operation_id=str(operation_id or "").strip() or None,
                attempt_id=str(attempt_id or "").strip() or None,
                retry_of_attempt_id=(
                    str(retry_of_attempt_id or "").strip() or None
                ),
                budget_decision=budget_decision,
                budget_reasons=tuple(budget_reasons),
                provider_called=provider_called,
                ledger_accepted=ledger_accepted,
                ledger_duplicate=ledger_duplicate,
                error_class=str(error_class or "").strip() or None,
            )
            self._events.append(event)
            return event

    def snapshot(self) -> tuple[BudgetTraceEvent, ...]:
        with self._lock:
            return tuple(self._events)
