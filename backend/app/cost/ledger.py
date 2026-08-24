from __future__ import annotations

import hashlib
import json
import math
import threading
from dataclasses import dataclass
from decimal import Decimal

from app.cost.calculator import calculate_actual_cost
from app.cost.contracts import (
    CostBreakdown,
    CostStatus,
    CostUnavailableReason,
    LocalCacheSavingsBaseline,
    require_non_negative_decimal,
)
from app.providers.pricing import ProviderPricingCatalog
from app.providers.pricing_deepseek import DEEPSEEK_PRICING_CATALOG
from app.providers.telemetry import ProviderUsageReceipt


class LedgerIdempotencyConflict(ValueError):
    """The same idempotency identity was reused for different ledger data."""


@dataclass(frozen=True, slots=True)
class CurrencyTotal:
    currency: str
    amount: Decimal

    def __post_init__(self) -> None:
        if not str(self.currency).strip():
            raise ValueError("currency must not be empty")
        require_non_negative_decimal("amount", self.amount)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    run_id: str
    operation_id: str
    receipt_fingerprint: str
    provider: str
    model: str
    provider_called: bool
    local_cache_avoided: bool
    breakdown: CostBreakdown


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    run_id: str
    operation_id: str | None
    operation_count: int
    receipt_count: int
    duplicate_ingestions: int
    provider_calls: int
    reported_input_cache_hit_tokens: int
    reported_input_cache_miss_tokens: int
    reported_output_tokens: int
    local_cache_avoided_calls: int
    unknown_receipts: int
    unpriced_receipts: int
    currency: str | None
    known_costs: tuple[CurrencyTotal, ...]
    known_actual_cost_subtotal: Decimal | None
    actual_calculated_provider_cost: Decimal | None
    actual_cost_complete: bool
    provider_cache_savings: Decimal
    local_cache_savings: Decimal
    estimated_savings: Decimal
    savings_classification: str
    pricing_versions: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "known_actual_cost_subtotal",
            "actual_calculated_provider_cost",
            "provider_cache_savings",
            "local_cache_savings",
            "estimated_savings",
        ):
            require_non_negative_decimal(name, getattr(self, name))
        if self.savings_classification != "estimated_savings":
            raise ValueError("ledger savings must be explicitly estimated")
        if self.actual_cost_complete:
            if self.actual_calculated_provider_cost is None:
                raise ValueError("complete actual cost requires a total")
        elif self.actual_calculated_provider_cost is not None:
            raise ValueError("incomplete actual cost must remain unknown")

    @property
    def cache_hit_ratio(self) -> Decimal | None:
        input_tokens = (
            self.reported_input_cache_hit_tokens
            + self.reported_input_cache_miss_tokens
        )
        if input_tokens == 0:
            return None
        return Decimal(self.reported_input_cache_hit_tokens) / Decimal(input_tokens)


@dataclass(frozen=True, slots=True)
class LedgerIngestionResult:
    accepted: bool
    duplicate: bool
    receipt_fingerprint: str
    breakdown: CostBreakdown
    operation_snapshot: LedgerSnapshot
    run_snapshot: LedgerSnapshot


def _normalized_identifier(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _canonical_receipt_payload(receipt: ProviderUsageReceipt) -> bytes:
    payload = receipt.to_dict()
    for value in payload.values():
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("receipt contains a non-finite numeric value")
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class ActualCostLedger:
    """Thread-safe, idempotent in-memory actual cost ledger by run/operation."""

    def __init__(
        self,
        *,
        catalog: ProviderPricingCatalog = DEEPSEEK_PRICING_CATALOG,
    ) -> None:
        self._catalog = catalog
        self._entries: dict[str, dict[str, list[LedgerEntry]]] = {}
        self._idempotency_index: dict[str, tuple[str, str, str, LedgerEntry]] = {}
        self._payload_index: dict[str, tuple[str, str, LedgerEntry]] = {}
        self._duplicate_counts: dict[tuple[str, str], int] = {}
        self._lock = threading.RLock()

    def ingest(
        self,
        *,
        run_id: str,
        operation_id: str,
        receipt: ProviderUsageReceipt,
        idempotency_key: str | None = None,
        local_cache_baseline: LocalCacheSavingsBaseline | None = None,
    ) -> LedgerIngestionResult:
        normalized_run = _normalized_identifier("run_id", run_id)
        normalized_operation = _normalized_identifier("operation_id", operation_id)
        receipt_payload = _canonical_receipt_payload(receipt)
        payload_hash = _sha256(receipt_payload)
        if idempotency_key is None:
            idempotency_hash = payload_hash
        else:
            normalized_key = _normalized_identifier(
                "idempotency_key",
                idempotency_key,
            )
            idempotency_hash = _sha256(
                ("external:" + normalized_key).encode("utf-8")
            )
        breakdown = calculate_actual_cost(
            receipt,
            catalog=self._catalog,
            local_cache_baseline=local_cache_baseline,
        )

        with self._lock:
            existing_by_key = self._idempotency_index.get(idempotency_hash)
            if existing_by_key is not None:
                (
                    existing_payload_hash,
                    existing_run,
                    existing_operation,
                    existing_entry,
                ) = existing_by_key
                if (
                    existing_payload_hash != payload_hash
                    or existing_run != normalized_run
                    or existing_operation != normalized_operation
                ):
                    raise LedgerIdempotencyConflict(
                        "idempotency key is already bound to different ledger data"
                    )
                return self._duplicate_result(existing_entry)

            existing_by_payload = self._payload_index.get(payload_hash)
            if existing_by_payload is not None:
                existing_run, existing_operation, existing_entry = existing_by_payload
                if (
                    existing_run != normalized_run
                    or existing_operation != normalized_operation
                ):
                    raise LedgerIdempotencyConflict(
                        "the same Provider receipt cannot belong to multiple ledger scopes"
                    )
                self._idempotency_index[idempotency_hash] = (
                    payload_hash,
                    existing_run,
                    existing_operation,
                    existing_entry,
                )
                return self._duplicate_result(existing_entry)

            entry = LedgerEntry(
                run_id=normalized_run,
                operation_id=normalized_operation,
                receipt_fingerprint=payload_hash,
                provider=receipt.provider,
                model=receipt.model,
                provider_called=receipt.provider_called,
                local_cache_avoided=(
                    not receipt.provider_called
                    and receipt.local_result_cache_hit is True
                ),
                breakdown=breakdown,
            )
            operations = self._entries.setdefault(normalized_run, {})
            operations.setdefault(normalized_operation, []).append(entry)
            self._idempotency_index[idempotency_hash] = (
                payload_hash,
                normalized_run,
                normalized_operation,
                entry,
            )
            self._payload_index[payload_hash] = (
                normalized_run,
                normalized_operation,
                entry,
            )
            return LedgerIngestionResult(
                accepted=True,
                duplicate=False,
                receipt_fingerprint=payload_hash,
                breakdown=breakdown,
                operation_snapshot=self._snapshot_locked(
                    normalized_run,
                    normalized_operation,
                ),
                run_snapshot=self._snapshot_locked(normalized_run, None),
            )

    def _duplicate_result(self, entry: LedgerEntry) -> LedgerIngestionResult:
        scope = (entry.run_id, entry.operation_id)
        self._duplicate_counts[scope] = self._duplicate_counts.get(scope, 0) + 1
        return LedgerIngestionResult(
            accepted=False,
            duplicate=True,
            receipt_fingerprint=entry.receipt_fingerprint,
            breakdown=entry.breakdown,
            operation_snapshot=self._snapshot_locked(
                entry.run_id,
                entry.operation_id,
            ),
            run_snapshot=self._snapshot_locked(entry.run_id, None),
        )

    def snapshot(
        self,
        *,
        run_id: str,
        operation_id: str | None = None,
    ) -> LedgerSnapshot:
        normalized_run = _normalized_identifier("run_id", run_id)
        normalized_operation = (
            _normalized_identifier("operation_id", operation_id)
            if operation_id is not None
            else None
        )
        with self._lock:
            return self._snapshot_locked(normalized_run, normalized_operation)

    def scoped_snapshots(
        self,
        *,
        run_id: str,
        operation_id: str,
    ) -> tuple[LedgerSnapshot, LedgerSnapshot]:
        """Return run and operation views from one atomic ledger state."""

        normalized_run = _normalized_identifier("run_id", run_id)
        normalized_operation = _normalized_identifier(
            "operation_id",
            operation_id,
        )
        with self._lock:
            return (
                self._snapshot_locked(normalized_run, None),
                self._snapshot_locked(normalized_run, normalized_operation),
            )

    def _entries_for_scope(
        self,
        run_id: str,
        operation_id: str | None,
    ) -> tuple[LedgerEntry, ...]:
        operations = self._entries.get(run_id, {})
        if operation_id is not None:
            return tuple(operations.get(operation_id, ()))
        return tuple(
            entry
            for operation_entries in operations.values()
            for entry in operation_entries
        )

    def _duplicate_count(self, run_id: str, operation_id: str | None) -> int:
        if operation_id is not None:
            return self._duplicate_counts.get((run_id, operation_id), 0)
        return sum(
            count
            for (entry_run, _entry_operation), count in self._duplicate_counts.items()
            if entry_run == run_id
        )

    def _snapshot_locked(
        self,
        run_id: str,
        operation_id: str | None,
    ) -> LedgerSnapshot:
        entries = self._entries_for_scope(run_id, operation_id)
        breakdowns = tuple(entry.breakdown for entry in entries)
        currency_totals: dict[str, Decimal] = {}
        for breakdown in breakdowns:
            if breakdown.total_cost is not None and breakdown.currency:
                currency_totals[breakdown.currency] = (
                    currency_totals.get(breakdown.currency, Decimal("0"))
                    + breakdown.total_cost
                )
        known_costs = tuple(
            CurrencyTotal(currency=currency, amount=amount)
            for currency, amount in sorted(currency_totals.items())
        )
        currency = known_costs[0].currency if len(known_costs) == 1 else None
        if len(known_costs) == 0:
            known_subtotal: Decimal | None = Decimal("0")
        elif len(known_costs) == 1:
            known_subtotal = known_costs[0].amount
        else:
            known_subtotal = None
        unknown_receipts = sum(
            breakdown.status == CostStatus.COST_UNAVAILABLE
            for breakdown in breakdowns
        )
        unpriced_receipts = sum(
            breakdown.reason == CostUnavailableReason.PRICING_NOT_FOUND
            for breakdown in breakdowns
        )
        actual_cost_complete = unknown_receipts == 0 and known_subtotal is not None
        provider_savings = sum(
            (
                breakdown.provider_cache_savings
                for breakdown in breakdowns
                if breakdown.provider_cache_savings is not None
            ),
            start=Decimal("0"),
        )
        local_savings = sum(
            (
                breakdown.local_cache_savings
                for breakdown in breakdowns
                if breakdown.local_cache_savings is not None
            ),
            start=Decimal("0"),
        )
        operation_count = (
            1
            if operation_id is not None and entries
            else sum(bool(values) for values in self._entries.get(run_id, {}).values())
        )
        return LedgerSnapshot(
            run_id=run_id,
            operation_id=operation_id,
            operation_count=operation_count,
            receipt_count=len(entries),
            duplicate_ingestions=self._duplicate_count(run_id, operation_id),
            provider_calls=sum(entry.provider_called for entry in entries),
            reported_input_cache_hit_tokens=sum(
                breakdown.prompt_cache_hit_tokens or 0
                for breakdown in breakdowns
            ),
            reported_input_cache_miss_tokens=sum(
                breakdown.prompt_cache_miss_tokens or 0
                for breakdown in breakdowns
            ),
            reported_output_tokens=sum(
                breakdown.output_tokens or 0 for breakdown in breakdowns
            ),
            local_cache_avoided_calls=sum(
                entry.local_cache_avoided for entry in entries
            ),
            unknown_receipts=unknown_receipts,
            unpriced_receipts=unpriced_receipts,
            currency=currency,
            known_costs=known_costs,
            known_actual_cost_subtotal=known_subtotal,
            actual_calculated_provider_cost=(
                known_subtotal if actual_cost_complete else None
            ),
            actual_cost_complete=actual_cost_complete,
            provider_cache_savings=provider_savings,
            local_cache_savings=local_savings,
            estimated_savings=provider_savings + local_savings,
            savings_classification="estimated_savings",
            pricing_versions=tuple(
                sorted(
                    {
                        breakdown.pricing_version
                        for breakdown in breakdowns
                        if breakdown.pricing_version
                    }
                )
            ),
        )

    def entries(
        self,
        *,
        run_id: str,
        operation_id: str | None = None,
    ) -> tuple[LedgerEntry, ...]:
        normalized_run = _normalized_identifier("run_id", run_id)
        normalized_operation = (
            _normalized_identifier("operation_id", operation_id)
            if operation_id is not None
            else None
        )
        with self._lock:
            return self._entries_for_scope(normalized_run, normalized_operation)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._idempotency_index.clear()
            self._payload_index.clear()
            self._duplicate_counts.clear()
