from __future__ import annotations

import threading
from collections import Counter
from decimal import Decimal

from .contracts import (
    CacheEvent,
    CacheEventConflict,
    CacheLayer,
    CacheMetricsSnapshot,
    CacheMissReason,
    CacheObservatoryReport,
    CurrencyAmount,
    MissReasonCount,
)


class CacheMetricsAggregator:
    """Thread-safe, idempotent aggregation without a cross-layer hit ratio."""

    def __init__(self) -> None:
        self._events: dict[str, CacheEvent] = {}
        self._lock = threading.RLock()

    def ingest(self, event: CacheEvent) -> bool:
        if not isinstance(event, CacheEvent):
            raise TypeError("event must be CacheEvent")
        with self._lock:
            existing = self._events.get(event.event_id)
            if existing is not None:
                if existing != event:
                    raise CacheEventConflict(
                        "event_id is already bound to different cache telemetry"
                    )
                return False
            self._events[event.event_id] = event
            return True

    def extend(self, events: tuple[CacheEvent, ...] | list[CacheEvent]) -> int:
        return sum(self.ingest(event) for event in events)

    def snapshot(self, layer: CacheLayer) -> CacheMetricsSnapshot:
        normalized = layer if isinstance(layer, CacheLayer) else CacheLayer(layer)
        with self._lock:
            events = tuple(
                event for event in self._events.values() if event.layer == normalized
            )
        hit_count = sum(event.hit for event in events)
        lookup_count = len(events)
        miss_count = lookup_count - hit_count
        currencies: dict[str, Decimal] = {}
        for event in events:
            if event.currency is not None:
                currencies[event.currency] = (
                    currencies.get(event.currency, Decimal(0))
                    + event.estimated_cost_avoided
                )
        currency_totals = tuple(
            CurrencyAmount(currency=currency, amount=amount)
            for currency, amount in sorted(currencies.items())
        )
        miss_counts = Counter(
            event.miss_reason for event in events if event.miss_reason is not None
        )
        return CacheMetricsSnapshot(
            layer=normalized,
            lookup_count=lookup_count,
            hit_count=hit_count,
            miss_count=miss_count,
            hit_ratio=(
                Decimal(hit_count) / Decimal(lookup_count) if lookup_count else None
            ),
            bytes_reused=sum(event.bytes_reused for event in events),
            tokens_reused=sum(event.tokens_reused for event in events),
            avoided_provider_calls=sum(
                event.avoided_provider_calls for event in events
            ),
            estimated_cost_avoided=(
                currency_totals[0].amount if len(currency_totals) == 1 else None
            ),
            estimated_costs_by_currency=currency_totals,
            miss_reasons=tuple(
                MissReasonCount(reason=reason, count=miss_counts.get(reason, 0))
                for reason in CacheMissReason
                if miss_counts.get(reason, 0)
            ),
            synthetic_event_count=sum(event.synthetic for event in events),
        )

    def report(self) -> CacheObservatoryReport:
        return CacheObservatoryReport(
            layers=tuple(self.snapshot(layer) for layer in CacheLayer)
        )

    def events(self) -> tuple[CacheEvent, ...]:
        with self._lock:
            return tuple(self._events.values())
