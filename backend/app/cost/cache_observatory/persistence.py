from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .contracts import (
    CacheEvent,
    CacheEventConflict,
    CacheIdentity,
    CacheLayer,
    CacheMetricsSnapshot,
    CacheMissReason,
    CacheObservatoryReport,
    CurrencyAmount,
    MissReasonCount,
)

_SCHEMA_VERSION = 1


class CacheMetricsStoreError(RuntimeError):
    """Base error for durable cache observatory storage."""


class CacheMetricsStoreClosed(CacheMetricsStoreError):
    pass


class CacheMetricsStoreCorruption(CacheMetricsStoreError):
    def __init__(self, message: str, *, original: BaseException | None = None) -> None:
        super().__init__(message)
        self.original = original


class CacheMetricsSchemaMissing(CacheMetricsStoreError):
    pass


@dataclass(frozen=True, slots=True)
class StoredCacheEvent:
    sequence: int
    event: CacheEvent
    effective_occurred_at_ms: int
    recorded_at_ms: int


@dataclass(frozen=True, slots=True)
class CacheMetricsIntegrityReport:
    ok: bool
    messages: tuple[str, ...]
    event_count: int


@dataclass(frozen=True, slots=True)
class CacheMetricsCheckpoint:
    mode: str
    busy: int
    log_frames: int
    checkpointed_frames: int


def _remote_windows_drive(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        return int(ctypes.windll.kernel32.GetDriveTypeW(path.anchor)) == 4
    except (AttributeError, OSError, ValueError):
        return False


def _validated_database_path(value: str | os.PathLike[str]) -> Path:
    raw = os.fspath(value).strip()
    if not raw or raw == ":memory:" or raw.casefold().startswith("file:"):
        raise ValueError("database_path must be an injected local filesystem path")
    if raw.startswith(("\\\\", "//")):
        raise ValueError("network share SQLite paths are not supported")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("database_path must be absolute")
    resolved = path.resolve(strict=False)
    if str(resolved).startswith(("\\\\", "//")) or _remote_windows_drive(resolved):
        raise ValueError("network share SQLite paths are not supported")
    return resolved


def _event_payload(event: CacheEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "trace_id": event.trace_id,
        "identity": event.identity.canonical_payload(),
        "hit": event.hit,
        "miss_reason": event.miss_reason.value if event.miss_reason else None,
        "bytes_reused": event.bytes_reused,
        "tokens_reused": event.tokens_reused,
        "avoided_provider_calls": event.avoided_provider_calls,
        "estimated_cost_avoided": str(event.estimated_cost_avoided),
        "currency": event.currency,
        "lookup_ms": str(event.lookup_ms) if event.lookup_ms is not None else None,
        "occurred_at_ms": event.occurred_at_ms,
        "synthetic": event.synthetic,
        "savings_classification": event.savings_classification,
    }


def _canonical_payload(event: CacheEvent) -> tuple[str, str]:
    payload = json.dumps(
        _event_payload(event),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _event_from_payload(payload_json: str) -> CacheEvent:
    payload = json.loads(payload_json)
    identity = CacheIdentity(**payload["identity"])
    return CacheEvent(
        event_id=payload["event_id"],
        trace_id=payload["trace_id"],
        identity=identity,
        hit=bool(payload["hit"]),
        miss_reason=payload["miss_reason"],
        bytes_reused=int(payload["bytes_reused"]),
        tokens_reused=int(payload["tokens_reused"]),
        avoided_provider_calls=int(payload["avoided_provider_calls"]),
        estimated_cost_avoided=Decimal(payload["estimated_cost_avoided"]),
        currency=payload["currency"],
        lookup_ms=(
            Decimal(payload["lookup_ms"]) if payload["lookup_ms"] is not None else None
        ),
        occurred_at_ms=payload["occurred_at_ms"],
        synthetic=bool(payload["synthetic"]),
        savings_classification=payload["savings_classification"],
    )


class CacheMetricsStore:
    """Local-only SQLite event store for persistent cache metrics.

    The caller owns the database path. A connection is opened per operation;
    WAL allows readers during the deliberately serialized write transaction.
    """

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        busy_timeout_ms: int = 5_000,
        auto_bootstrap: bool = True,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if isinstance(busy_timeout_ms, bool) or int(busy_timeout_ms) < 1:
            raise ValueError("busy_timeout_ms must be a positive integer")
        self._path = _validated_database_path(database_path)
        self._busy_timeout_ms = int(busy_timeout_ms)
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._writer_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._closed = False
        self._journal_mode: str | None = None
        if auto_bootstrap:
            self.bootstrap_schema()

    @property
    def database_path(self) -> Path:
        return self._path

    @property
    def journal_mode(self) -> str | None:
        return self._journal_mode

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        with self._lifecycle_lock:
            self._closed = True

    def _check_open(self) -> None:
        if self._closed:
            raise CacheMetricsStoreClosed("cache metrics store is closed")

    def _connect(self) -> sqlite3.Connection:
        self._check_open()
        try:
            connection = sqlite3.connect(
                str(self._path),
                timeout=self._busy_timeout_ms / 1_000,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            connection.execute("PRAGMA synchronous=FULL")
            return connection
        except sqlite3.DatabaseError as exc:
            raise CacheMetricsStoreCorruption(
                "unable to open the cache metrics database", original=exc
            ) from exc

    @staticmethod
    def _require_schema(connection: sqlite3.Connection) -> None:
        try:
            row = connection.execute(
                "SELECT value FROM cache_observatory_metadata "
                "WHERE key='schema_version'"
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).casefold():
                raise CacheMetricsSchemaMissing(
                    "cache metrics schema is missing; bootstrap it explicitly"
                ) from exc
            raise
        if row is None:
            raise CacheMetricsSchemaMissing("cache metrics schema version is missing")
        if int(row["value"]) != _SCHEMA_VERSION:
            raise CacheMetricsStoreCorruption(
                f"unsupported cache metrics schema version: {row['value']}"
            )

    @contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            self._require_schema(connection)
            yield connection
        except sqlite3.DatabaseError as exc:
            raise CacheMetricsStoreCorruption(
                "cache metrics database read failed", original=exc
            ) from exc
        finally:
            connection.close()

    @contextmanager
    def _writer(self) -> Iterator[sqlite3.Connection]:
        with self._writer_lock:
            connection = self._connect()
            try:
                self._require_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except sqlite3.DatabaseError as exc:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise CacheMetricsStoreCorruption(
                    "cache metrics database transaction failed", original=exc
                ) from exc
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def bootstrap_schema(self) -> None:
        self._check_open()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        statements = (
            """
            CREATE TABLE IF NOT EXISTS cache_observatory_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS cache_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                trace_id TEXT NOT NULL,
                layer TEXT NOT NULL,
                identity_key TEXT NOT NULL,
                identity_json TEXT NOT NULL,
                lookup INTEGER NOT NULL DEFAULT 1 CHECK (lookup = 1),
                hit INTEGER NOT NULL CHECK (hit IN (0, 1)),
                miss_reason TEXT,
                bytes_reused INTEGER NOT NULL,
                tokens_reused INTEGER NOT NULL,
                avoided_provider_calls INTEGER NOT NULL,
                estimated_cost_avoided TEXT NOT NULL,
                currency TEXT,
                lookup_ms TEXT,
                effective_occurred_at_ms INTEGER NOT NULL,
                recorded_at_ms INTEGER NOT NULL,
                route_identity TEXT,
                provider TEXT,
                model TEXT,
                prompt_version TEXT,
                synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
                savings_classification TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS cache_events_layer_time_idx
            ON cache_events(layer, effective_occurred_at_ms, sequence)
            """,
            """
            CREATE INDEX IF NOT EXISTS cache_events_identity_time_idx
            ON cache_events(identity_key, effective_occurred_at_ms, sequence)
            """,
            """
            CREATE INDEX IF NOT EXISTS cache_events_trace_idx
            ON cache_events(trace_id, sequence)
            """,
        )
        with self._writer_lock:
            connection = self._connect()
            try:
                self._journal_mode = str(
                    connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                ).upper()
                if self._journal_mode != "WAL":
                    raise CacheMetricsStoreCorruption(
                        f"cache metrics store requires WAL, got {self._journal_mode}"
                    )
                connection.execute("BEGIN IMMEDIATE")
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO cache_observatory_metadata(key, value)
                    VALUES('schema_version', ?)
                    ON CONFLICT(key) DO NOTHING
                    """,
                    (str(_SCHEMA_VERSION),),
                )
                self._require_schema(connection)
                connection.execute("COMMIT")
            except CacheMetricsStoreError:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            except sqlite3.DatabaseError as exc:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise CacheMetricsStoreCorruption(
                    "cache metrics schema bootstrap failed", original=exc
                ) from exc
            finally:
                connection.close()

    def _insert(self, connection: sqlite3.Connection, event: CacheEvent) -> bool:
        if not isinstance(event, CacheEvent):
            raise TypeError("event must be CacheEvent")
        payload_json, payload_hash = _canonical_payload(event)
        recorded_at_ms = int(self._clock_ms())
        effective_at_ms = (
            event.occurred_at_ms if event.occurred_at_ms is not None else recorded_at_ms
        )
        identity_json = json.dumps(
            event.identity.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO cache_events(
                event_id, trace_id, layer, identity_key, identity_json, lookup,
                hit, miss_reason, bytes_reused, tokens_reused,
                avoided_provider_calls, estimated_cost_avoided, currency,
                lookup_ms, effective_occurred_at_ms, recorded_at_ms,
                route_identity, provider, model, prompt_version, synthetic,
                savings_classification, payload_json, payload_hash
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.trace_id,
                event.layer.value,
                event.identity.cache_key,
                identity_json,
                int(event.hit),
                event.miss_reason.value if event.miss_reason else None,
                event.bytes_reused,
                event.tokens_reused,
                event.avoided_provider_calls,
                str(event.estimated_cost_avoided),
                event.currency,
                str(event.lookup_ms) if event.lookup_ms is not None else None,
                effective_at_ms,
                recorded_at_ms,
                event.identity.route_identity,
                event.identity.provider,
                event.identity.model,
                event.identity.prompt_version,
                int(event.synthetic),
                event.savings_classification,
                payload_json,
                payload_hash,
            ),
        )
        if cursor.rowcount == 1:
            return True
        existing = connection.execute(
            "SELECT payload_hash, payload_json FROM cache_events WHERE event_id=?",
            (event.event_id,),
        ).fetchone()
        if (
            existing is not None
            and existing["payload_hash"] == payload_hash
            and existing["payload_json"] == payload_json
        ):
            return False
        raise CacheEventConflict(
            "event_id is already bound to different cache telemetry"
        )

    def ingest(self, event: CacheEvent) -> bool:
        with self._writer() as connection:
            return self._insert(connection, event)

    def extend(self, events: Iterable[CacheEvent]) -> int:
        immutable_events = tuple(events)
        with self._writer() as connection:
            return sum(self._insert(connection, event) for event in immutable_events)

    @staticmethod
    def _time_filter(
        *,
        started_at_ms: int | None,
        ended_at_ms: int | None,
    ) -> tuple[str, tuple[int, ...]]:
        for name, value in (
            ("started_at_ms", started_at_ms),
            ("ended_at_ms", ended_at_ms),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer")
        if (
            started_at_ms is not None
            and ended_at_ms is not None
            and started_at_ms >= ended_at_ms
        ):
            raise ValueError("time range must satisfy start < end")
        clauses: list[str] = []
        values: list[int] = []
        if started_at_ms is not None:
            clauses.append("effective_occurred_at_ms >= ?")
            values.append(started_at_ms)
        if ended_at_ms is not None:
            clauses.append("effective_occurred_at_ms < ?")
            values.append(ended_at_ms)
        return (" AND ".join(clauses), tuple(values))

    def read_events(
        self,
        *,
        layer: CacheLayer | None = None,
        started_at_ms: int | None = None,
        ended_at_ms: int | None = None,
    ) -> tuple[StoredCacheEvent, ...]:
        where, values = self._time_filter(
            started_at_ms=started_at_ms, ended_at_ms=ended_at_ms
        )
        clauses = [where] if where else []
        parameters: list[object] = list(values)
        if layer is not None:
            normalized = layer if isinstance(layer, CacheLayer) else CacheLayer(layer)
            clauses.append("layer = ?")
            parameters.append(normalized.value)
        sql = (
            "SELECT sequence, payload_json, effective_occurred_at_ms, recorded_at_ms "
            "FROM cache_events"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY effective_occurred_at_ms, sequence"
        with self._reader() as connection:
            rows = tuple(connection.execute(sql, tuple(parameters)).fetchall())
        return tuple(
            StoredCacheEvent(
                sequence=int(row["sequence"]),
                event=_event_from_payload(row["payload_json"]),
                effective_occurred_at_ms=int(row["effective_occurred_at_ms"]),
                recorded_at_ms=int(row["recorded_at_ms"]),
            )
            for row in rows
        )

    def snapshot(
        self,
        layer: CacheLayer,
        *,
        started_at_ms: int | None = None,
        ended_at_ms: int | None = None,
    ) -> CacheMetricsSnapshot:
        normalized = layer if isinstance(layer, CacheLayer) else CacheLayer(layer)
        where, values = self._time_filter(
            started_at_ms=started_at_ms, ended_at_ms=ended_at_ms
        )
        clauses = ["layer = ?"]
        parameters: list[object] = [normalized.value]
        if where:
            clauses.append(where)
            parameters.extend(values)
        with self._reader() as connection:
            rows = tuple(
                connection.execute(
                    "SELECT hit, miss_reason, bytes_reused, tokens_reused, "
                    "avoided_provider_calls, estimated_cost_avoided, currency, "
                    "synthetic FROM cache_events WHERE " + " AND ".join(clauses),
                    tuple(parameters),
                ).fetchall()
            )
        lookup_count = len(rows)
        hit_count = sum(int(row["hit"]) for row in rows)
        miss_counts = Counter(
            CacheMissReason(row["miss_reason"])
            for row in rows
            if row["miss_reason"] is not None
        )
        currencies: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
        for row in rows:
            if row["currency"] is not None:
                currencies[row["currency"]] += Decimal(row["estimated_cost_avoided"])
        totals = tuple(
            CurrencyAmount(currency=currency, amount=amount)
            for currency, amount in sorted(currencies.items())
        )
        return CacheMetricsSnapshot(
            layer=normalized,
            lookup_count=lookup_count,
            hit_count=hit_count,
            miss_count=lookup_count - hit_count,
            hit_ratio=(
                Decimal(hit_count) / Decimal(lookup_count) if lookup_count else None
            ),
            bytes_reused=sum(int(row["bytes_reused"]) for row in rows),
            tokens_reused=sum(int(row["tokens_reused"]) for row in rows),
            avoided_provider_calls=sum(
                int(row["avoided_provider_calls"]) for row in rows
            ),
            estimated_cost_avoided=(totals[0].amount if len(totals) == 1 else None),
            estimated_costs_by_currency=totals,
            miss_reasons=tuple(
                MissReasonCount(reason=reason, count=miss_counts[reason])
                for reason in CacheMissReason
                if miss_counts[reason]
            ),
            synthetic_event_count=sum(int(row["synthetic"]) for row in rows),
        )

    def report(
        self,
        *,
        started_at_ms: int | None = None,
        ended_at_ms: int | None = None,
    ) -> CacheObservatoryReport:
        return CacheObservatoryReport(
            layers=tuple(
                self.snapshot(
                    layer,
                    started_at_ms=started_at_ms,
                    ended_at_ms=ended_at_ms,
                )
                for layer in CacheLayer
            )
        )

    def count(self) -> int:
        with self._reader() as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM cache_events").fetchone()[0]
            )

    def verify_integrity(self) -> CacheMetricsIntegrityReport:
        with self._reader() as connection:
            messages = tuple(
                str(row[0]) for row in connection.execute("PRAGMA quick_check")
            )
            event_count = int(
                connection.execute("SELECT COUNT(*) FROM cache_events").fetchone()[0]
            )
        return CacheMetricsIntegrityReport(
            ok=messages == ("ok",), messages=messages, event_count=event_count
        )

    def checkpoint(self, mode: str = "PASSIVE") -> CacheMetricsCheckpoint:
        normalized = str(mode or "").strip().upper()
        if normalized not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
            raise ValueError("unsupported WAL checkpoint mode")
        with self._writer_lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    f"PRAGMA wal_checkpoint({normalized})"
                ).fetchone()
            except sqlite3.DatabaseError as exc:
                raise CacheMetricsStoreCorruption(
                    "cache metrics WAL checkpoint failed", original=exc
                ) from exc
            finally:
                connection.close()
        return CacheMetricsCheckpoint(
            mode=normalized,
            busy=int(row[0]),
            log_frames=int(row[1]),
            checkpointed_frames=int(row[2]),
        )
