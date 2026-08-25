from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.cost.ledger import CurrencyTotal

from .contracts import (
    ExecutionAuditReport,
    ReceiptAccountingState,
    RetryLineageRecord,
)
from .store import SQLiteExecutionStore


def build_execution_audit_report(
    store: SQLiteExecutionStore,
    *,
    session_id: str,
) -> ExecutionAuditReport:
    """Build a telemetry-only audit view from durable facts.

    Savings remain explicitly estimated; they are never represented as a
    Provider invoice or official billed amount.
    """

    session = store.get_session(session_id)
    if session is None:
        raise KeyError(f"unknown session_id: {session_id}")
    operations = store.list_operations(session_id)
    receipts = store.list_receipts(session_id)
    ledger_snapshot = store.restore_ledger(session_id).snapshot(run_id=session.run_id)
    staged_receipts = sum(
        item.accounting_state == ReceiptAccountingState.STAGED for item in receipts
    )

    estimate = session.job_start_estimate
    if estimate is not None:
        estimate_currency = estimate.currency
        estimated_minimum = estimate.minimum
        estimated_expected = estimate.expected
        estimated_high = estimate.maximum
    else:
        estimates = tuple(
            item.estimate for item in operations if item.estimate is not None
        )
        currencies = {item.currency for item in estimates}
        estimate_currency = next(iter(currencies)) if len(currencies) == 1 else None
        if estimates and estimate_currency is not None:
            estimated_minimum = sum(
                (item.minimum for item in estimates), start=Decimal(0)
            )
            estimated_expected = sum(
                (item.expected for item in estimates), start=Decimal(0)
            )
            estimated_high = sum((item.maximum for item in estimates), start=Decimal(0))
        else:
            estimated_minimum = None
            estimated_expected = None
            estimated_high = None

    cache_hit_tokens = sum(
        item.receipt.prompt_cache_hit_tokens or 0 for item in receipts
    )
    cache_miss_tokens = sum(
        item.receipt.prompt_cache_miss_tokens or 0 for item in receipts
    )
    total_input = cache_hit_tokens + cache_miss_tokens
    cache_hit_ratio = (
        Decimal(cache_hit_tokens) / Decimal(total_input) if total_input else None
    )
    known_costs = tuple(
        CurrencyTotal(currency=item.currency, amount=item.amount)
        for item in ledger_snapshot.known_costs
    )
    actual_known_cost = (
        Decimal(0)
        if not known_costs
        else known_costs[0].amount
        if len(known_costs) == 1
        else None
    )
    return ExecutionAuditReport(
        session_id=session.session_id,
        run_id=session.run_id,
        session_state=session.state,
        estimate_currency=estimate_currency,
        estimated_minimum=estimated_minimum,
        estimated_expected=estimated_expected,
        estimated_high=estimated_high,
        actual_known_cost=actual_known_cost,
        actual_known_costs=known_costs,
        actual_cost_complete=(
            ledger_snapshot.actual_cost_complete and staged_receipts == 0
        ),
        unknown_receipts=ledger_snapshot.unknown_receipts,
        unpriced_receipts=ledger_snapshot.unpriced_receipts,
        staged_receipts=staged_receipts,
        provider_calls=sum(item.receipt.provider_called for item in receipts),
        local_cache_avoided_calls=sum(
            not item.receipt.provider_called
            and item.receipt.local_result_cache_hit is True
            for item in receipts
        ),
        provider_cache_hit_tokens=cache_hit_tokens,
        provider_cache_miss_tokens=cache_miss_tokens,
        output_tokens=sum(item.receipt.completion_tokens or 0 for item in receipts),
        provider_cache_hit_ratio=cache_hit_ratio,
        provider_cache_savings=ledger_snapshot.provider_cache_savings,
        local_cache_savings=ledger_snapshot.local_cache_savings,
        estimated_savings=ledger_snapshot.estimated_savings,
        savings_classification="estimated_savings",
        pricing_versions=ledger_snapshot.pricing_versions,
        retry_lineage=tuple(
            RetryLineageRecord(
                operation_id=item.operation_id,
                attempt_id=item.attempt_id,
                retry_of_attempt_id=item.retry_of_attempt_id,
                state=item.state,
                execution_status=item.execution_status,
            )
            for item in operations
        ),
        stop_reason=session.stop_reason,
        budget_anomalies=session.budget_anomalies,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
