from __future__ import annotations

import sqlite3
from dataclasses import replace
from decimal import Decimal

import pytest
from app.cost.cache_observatory import (
    CacheEvent,
    CacheEventConflict,
    CacheIdentity,
    CacheLayer,
    CacheMissReason,
)
from app.cost.cache_observatory.persistence import (
    CacheMetricsStore,
    CacheMetricsStoreClosed,
    CacheMetricsStoreCorruption,
)


def _identity(layer: CacheLayer, artifact: str = "artifact-1") -> CacheIdentity:
    return CacheIdentity(
        layer=layer,
        namespace="store-test",
        artifact_id=artifact,
        provider="openai_compatible",
        model="deepseek-v4-flash",
        prompt_version="teacher-v1",
        route_identity="route-a",
        stable_prefix_hash="stable-a",
        dynamic_context_hash="learner-a",
        evidence_hash="evidence-a",
        parser_version="parser-v1",
    )


def _event(
    event_id: str,
    *,
    layer: CacheLayer = CacheLayer.PROVIDER_PROMPT_CACHE,
    hit: bool = True,
    occurred_at_ms: int | None = 100,
) -> CacheEvent:
    return CacheEvent(
        event_id=event_id,
        trace_id=f"trace-{event_id}",
        identity=_identity(layer),
        hit=hit,
        miss_reason=None if hit else CacheMissReason.DYNAMIC_CONTEXT_CHANGED,
        bytes_reused=1_024 if hit else 0,
        tokens_reused=700 if hit else 0,
        avoided_provider_calls=(
            1 if hit and layer == CacheLayer.LOCAL_RESULT_CACHE else 0
        ),
        estimated_cost_avoided=Decimal("0.02") if hit else Decimal(0),
        currency="USD",
        lookup_ms=Decimal("1.5"),
        occurred_at_ms=occurred_at_ms,
    )


def test_store_requires_caller_injected_absolute_local_path(tmp_path) -> None:
    with pytest.raises(ValueError):
        CacheMetricsStore("relative/cache.db")
    with pytest.raises(ValueError):
        CacheMetricsStore(":memory:")
    with pytest.raises(ValueError):
        CacheMetricsStore(r"\\server\share\cache.db")

    store = CacheMetricsStore(tmp_path / "metrics.db")
    assert store.database_path == (tmp_path / "metrics.db").resolve()
    assert store.journal_mode == "WAL"
    store.close()
    with pytest.raises(CacheMetricsStoreClosed):
        store.count()


def test_store_persists_restart_safe_separated_metrics_and_exact_columns(
    tmp_path,
) -> None:
    database = tmp_path / "metrics.db"
    clock_values = iter((1_000, 1_001))
    store = CacheMetricsStore(database, clock_ms=lambda: next(clock_values))
    provider = _event("provider-hit")
    local = _event(
        "local-miss",
        layer=CacheLayer.LOCAL_RESULT_CACHE,
        hit=False,
        occurred_at_ms=None,
    )
    assert store.extend((provider, local)) == 2
    store.close()

    restarted = CacheMetricsStore(database)
    assert restarted.count() == 2
    assert restarted.snapshot(CacheLayer.PROVIDER_PROMPT_CACHE).hit_count == 1
    assert restarted.snapshot(CacheLayer.LOCAL_RESULT_CACHE).miss_count == 1
    assert restarted.report().to_public_dict()["combined_hit_ratio"] is None
    rows = restarted.read_events()
    assert [item.event.event_id for item in rows] == ["provider-hit", "local-miss"]
    assert rows[1].effective_occurred_at_ms == 1_001
    assert rows[1].event.occurred_at_ms is None
    assert restarted.verify_integrity().ok

    connection = sqlite3.connect(database)
    persisted = connection.execute(
        "SELECT lookup, layer, identity_key, hit, miss_reason, tokens_reused, "
        "estimated_cost_avoided, effective_occurred_at_ms, route_identity, "
        "model, prompt_version FROM cache_events WHERE event_id='provider-hit'"
    ).fetchone()
    connection.close()
    assert persisted == (
        1,
        "PROVIDER_PROMPT_CACHE",
        provider.identity.cache_key,
        1,
        None,
        700,
        "0.02",
        100,
        "route-a",
        "deepseek-v4-flash",
        "teacher-v1",
    )


def test_duplicate_is_idempotent_and_conflicting_event_rolls_back_batch(
    tmp_path,
) -> None:
    store = CacheMetricsStore(tmp_path / "metrics.db")
    event = _event("same-id")
    assert store.ingest(event)
    assert not store.ingest(event)
    with pytest.raises(CacheEventConflict):
        store.extend(
            (
                _event("would-have-been-inserted"),
                replace(event, tokens_reused=701),
            )
        )
    assert store.count() == 1


def test_out_of_order_events_are_read_and_filtered_by_occurrence_time(tmp_path) -> None:
    store = CacheMetricsStore(tmp_path / "metrics.db")
    store.extend(
        (
            _event("late", occurred_at_ms=300),
            _event("early", occurred_at_ms=100),
            _event("middle", occurred_at_ms=200),
        )
    )
    assert [item.event.event_id for item in store.read_events()] == [
        "early",
        "middle",
        "late",
    ]
    assert [
        item.event.event_id
        for item in store.read_events(started_at_ms=150, ended_at_ms=250)
    ] == ["middle"]


def test_corrupt_database_fails_closed_without_reinitializing(tmp_path) -> None:
    database = tmp_path / "corrupt.db"
    database.write_bytes(b"not-a-sqlite-database")
    original = database.read_bytes()
    with pytest.raises(CacheMetricsStoreCorruption):
        CacheMetricsStore(database)
    assert database.read_bytes() == original
