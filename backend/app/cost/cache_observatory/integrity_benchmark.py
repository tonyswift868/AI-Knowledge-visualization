from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from time import perf_counter

from .contracts import (
    CacheEvent,
    CacheEventConflict,
    CacheIdentity,
    CacheLayer,
    CacheMissReason,
)
from .persistence import CacheMetricsStore
from .product_metrics import ProductMetricsProjector, ProductMetricsReadModel


@dataclass(frozen=True, slots=True)
class CacheIntegrityBenchmarkResult:
    event_count: int
    accepted_event_count: int
    persisted_event_count_after_restart: int
    duplicate_replay_idempotent: bool
    conflicting_event_rejected: bool
    out_of_order_preserved: bool
    integrity_ok: bool
    journal_mode: str
    checkpoint_busy: int
    elapsed_ms: Decimal
    events_per_second: Decimal
    database_bytes: int
    product_metrics: ProductMetricsReadModel
    real_provider_calls: int = 0
    data_classification: str = "synthetic_offline_integrity_benchmark"
    disclaimer: str = (
        "Synthetic local SQLite benchmark; it is not a real Provider hit rate "
        "or an official Provider bill."
    )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "event_count": self.event_count,
            "accepted_event_count": self.accepted_event_count,
            "persisted_event_count_after_restart": (
                self.persisted_event_count_after_restart
            ),
            "duplicate_replay_idempotent": self.duplicate_replay_idempotent,
            "conflicting_event_rejected": self.conflicting_event_rejected,
            "out_of_order_preserved": self.out_of_order_preserved,
            "integrity_ok": self.integrity_ok,
            "journal_mode": self.journal_mode,
            "checkpoint_busy": self.checkpoint_busy,
            "elapsed_ms": str(self.elapsed_ms),
            "events_per_second": str(self.events_per_second),
            "database_bytes": self.database_bytes,
            "product_metrics": self.product_metrics.to_public_dict(),
            "real_provider_calls": self.real_provider_calls,
            "data_classification": self.data_classification,
            "disclaimer": self.disclaimer,
        }


def _identity(layer: CacheLayer) -> CacheIdentity:
    return CacheIdentity(
        layer=layer,
        namespace="synthetic-100k-integrity",
        artifact_id=f"artifact-{layer.value.casefold()}",
        provider="openai_compatible",
        model="synthetic-model-v1",
        prompt_version="synthetic-prompt-v1",
        route_identity="synthetic-route-v1",
        stable_prefix_hash="stable-prefix-v1",
        dynamic_context_hash="learner-context-v1",
        source_hash="source-v1",
        evidence_hash="evidence-v1",
        question_hash="question-v1",
        question_bank_version="question-bank-v1",
        parser_version="parser-v1",
        evaluation_version="evaluation-v1",
        identity_policy_version="cache-identity-policy-v1",
    )


def _event(index: int, event_count: int) -> CacheEvent:
    layer = tuple(CacheLayer)[index % len(CacheLayer)]
    workload_index = index // len(CacheLayer)
    hit = workload_index % 4 != 0
    miss_reasons = tuple(CacheMissReason)
    return CacheEvent(
        event_id=f"integrity-{index:06d}",
        trace_id=f"trace-{index // len(CacheLayer):06d}",
        identity=_identity(layer),
        hit=hit,
        miss_reason=(None if hit else miss_reasons[workload_index % len(miss_reasons)]),
        bytes_reused=2_048 if hit and layer != CacheLayer.PROVIDER_PROMPT_CACHE else 0,
        tokens_reused=700 if hit else 0,
        avoided_provider_calls=(
            1
            if hit
            and layer
            in {
                CacheLayer.LOCAL_RESULT_CACHE,
                CacheLayer.EVALUATION_ARTIFACT_CACHE,
            }
            else 0
        ),
        estimated_cost_avoided=Decimal("0.0001") if hit else Decimal(0),
        currency="USD",
        lookup_ms=Decimal("0.1"),
        occurred_at_ms=event_count - index,
        synthetic=True,
    )


def run_cache_integrity_benchmark(
    database_path: str | Path,
    *,
    event_count: int = 100_000,
    batch_size: int = 5_000,
) -> CacheIntegrityBenchmarkResult:
    if (
        isinstance(event_count, bool)
        or not isinstance(event_count, int)
        or event_count < 1
    ):
        raise ValueError("event_count must be a positive integer")
    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or batch_size < 1
    ):
        raise ValueError("batch_size must be a positive integer")
    path = Path(database_path)
    if path.exists():
        raise FileExistsError("integrity benchmark database path must not exist")
    started = perf_counter()
    store = CacheMetricsStore(path)
    journal_mode = str(store.journal_mode)
    accepted = 0
    first_event: CacheEvent | None = None
    last_event: CacheEvent | None = None
    for batch_start in range(0, event_count, batch_size):
        batch_end = min(batch_start + batch_size, event_count)
        events = tuple(
            _event(index, event_count) for index in range(batch_start, batch_end)
        )
        if first_event is None:
            first_event = events[0]
        last_event = events[-1]
        accepted += store.extend(events)
    assert first_event is not None and last_event is not None
    duplicate_idempotent = not store.ingest(first_event)
    conflict_rejected = False
    try:
        store.ingest(
            replace(
                first_event,
                identity=replace(
                    first_event.identity,
                    model="conflicting-model-version",
                ),
            )
        )
    except CacheEventConflict:
        conflict_rejected = True
    checkpoint = store.checkpoint("TRUNCATE")
    store.close()

    restarted = CacheMetricsStore(path)
    persisted_count = restarted.count()
    earliest = restarted.read_events(ended_at_ms=2)
    latest = restarted.read_events(started_at_ms=event_count)
    out_of_order_preserved = (
        len(earliest) == 1
        and earliest[0].event.event_id == last_event.event_id
        and len(latest) == 1
        and latest[0].event.event_id == first_event.event_id
    )
    integrity = restarted.verify_integrity()
    observatory = restarted.report()
    product_metrics = ProductMetricsProjector().project(
        observatory,
        generated_at_ms=event_count + 1,
        window_started_at_ms=1,
        window_ended_at_ms=event_count + 1,
    )
    restarted.close()
    elapsed_seconds = Decimal(str(perf_counter() - started))
    elapsed_ms = elapsed_seconds * Decimal(1_000)
    return CacheIntegrityBenchmarkResult(
        event_count=event_count,
        accepted_event_count=accepted,
        persisted_event_count_after_restart=persisted_count,
        duplicate_replay_idempotent=duplicate_idempotent,
        conflicting_event_rejected=conflict_rejected,
        out_of_order_preserved=out_of_order_preserved,
        integrity_ok=integrity.ok,
        journal_mode=journal_mode,
        checkpoint_busy=checkpoint.busy,
        elapsed_ms=elapsed_ms,
        events_per_second=Decimal(event_count) / elapsed_seconds,
        database_bytes=path.stat().st_size,
        product_metrics=product_metrics,
    )
