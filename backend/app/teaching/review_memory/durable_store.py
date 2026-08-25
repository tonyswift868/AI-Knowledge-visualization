"""SQLite persistence for FSRS cards, observations, logs, and due state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    MacroRoundBoundary,
    ReviewMemoryItem,
    ReviewObservation,
    ReviewRatingDecision,
)
from .service_models import (
    DURABLE_REVIEW_SCHEMA_VERSION,
    DurableObservationStatus,
    DurableReviewLog,
    DurableReviewObservationRecord,
    DurableReviewReceipt,
    PersistedReviewMemoryItem,
    ReviewCheckpointReceipt,
    ReviewHistoryPage,
    ReviewMemoryAnalytics,
    SchedulerStateIdentity,
)


class DurableReviewStoreError(RuntimeError):
    pass


class DurableReviewStoreCorruptionError(DurableReviewStoreError):
    pass


class ReviewEventConflictError(DurableReviewStoreError):
    pass


class ReviewItemConflictError(DurableReviewStoreError):
    pass


class ReviewItemVersionConflictError(DurableReviewStoreError):
    pass


class ReviewObservationOrderError(DurableReviewStoreError):
    pass


class SchedulerVersionMismatchError(DurableReviewStoreError):
    pass


@dataclass(frozen=True, slots=True)
class DurableReviewCommit:
    observation: ReviewObservation
    boundary: MacroRoundBoundary
    expected_state_version: int
    item_after: ReviewMemoryItem
    failure_streak: int
    rating_decision: ReviewRatingDecision
    durable_receipt: DurableReviewReceipt
    review_log: DurableReviewLog


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS durable_review_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_items (
    student_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    question_id TEXT,
    item_json TEXT NOT NULL,
    item_sha256 TEXT NOT NULL,
    card_json TEXT NOT NULL,
    due_at TEXT,
    difficulty REAL,
    stability REAL,
    retrievability REAL,
    review_count INTEGER NOT NULL,
    failure_count INTEGER NOT NULL,
    failure_streak INTEGER NOT NULL,
    hint_dependency TEXT NOT NULL,
    last_observation TEXT,
    critical_weight REAL NOT NULL,
    state_version INTEGER NOT NULL,
    scheduler_package TEXT NOT NULL,
    scheduler_version TEXT NOT NULL,
    scheduler_adapter_id TEXT NOT NULL,
    scheduler_config_json TEXT NOT NULL,
    scheduler_config_sha256 TEXT NOT NULL,
    card_schema_version TEXT NOT NULL,
    stored_at TEXT NOT NULL,
    PRIMARY KEY(student_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_review_items_due
    ON review_items(student_id, due_at, critical_weight DESC);
CREATE INDEX IF NOT EXISTS idx_review_items_weak
    ON review_items(student_id, last_observation, failure_streak DESC);

CREATE TABLE IF NOT EXISTS review_observations (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    observation_json TEXT NOT NULL,
    boundary_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'applied')),
    rating_decision_json TEXT,
    receipt_id TEXT,
    receipt_json TEXT,
    created_at TEXT NOT NULL,
    applied_at TEXT,
    UNIQUE(student_id, observation_id),
    FOREIGN KEY(student_id, item_id)
        REFERENCES review_items(student_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_review_observations_pending
    ON review_observations(student_id, status, sequence);

CREATE TABLE IF NOT EXISTS review_logs (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    review_log_id TEXT NOT NULL UNIQUE,
    receipt_id TEXT NOT NULL UNIQUE,
    observation_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    rating TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    scheduler_version TEXT NOT NULL,
    log_json TEXT NOT NULL,
    log_sha256 TEXT NOT NULL,
    UNIQUE(student_id, observation_id),
    FOREIGN KEY(student_id, observation_id)
        REFERENCES review_observations(student_id, observation_id),
    FOREIGN KEY(student_id, item_id)
        REFERENCES review_items(student_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_review_logs_history
    ON review_logs(student_id, item_id, sequence);

CREATE TRIGGER IF NOT EXISTS review_items_no_delete
BEFORE DELETE ON review_items
BEGIN
    SELECT RAISE(ABORT, 'review items cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS review_observations_immutable_payload
BEFORE UPDATE ON review_observations
WHEN NEW.student_id != OLD.student_id
  OR NEW.item_id != OLD.item_id
  OR NEW.observation_id != OLD.observation_id
  OR NEW.observation_json != OLD.observation_json
  OR NEW.boundary_json != OLD.boundary_json
  OR NEW.payload_sha256 != OLD.payload_sha256
  OR NEW.created_at != OLD.created_at
BEGIN
    SELECT RAISE(ABORT, 'review observation payload is immutable');
END;

CREATE TRIGGER IF NOT EXISTS review_observations_no_delete
BEFORE DELETE ON review_observations
BEGIN
    SELECT RAISE(ABORT, 'review observations cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS review_observations_applied_immutable
BEFORE UPDATE ON review_observations
WHEN OLD.status = 'applied'
BEGIN
    SELECT RAISE(ABORT, 'applied review observations are immutable');
END;

CREATE TRIGGER IF NOT EXISTS review_logs_no_update
BEFORE UPDATE ON review_logs
BEGIN
    SELECT RAISE(ABORT, 'review logs are append-only');
END;

CREATE TRIGGER IF NOT EXISTS review_logs_no_delete
BEFORE DELETE ON review_logs
BEGIN
    SELECT RAISE(ABORT, 'review logs are append-only');
END;
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat()


def _parse_utc(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("stored timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _model_json(model: object) -> str:
    return _canonical_json(model.model_dump(mode="json"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class SqliteReviewMemoryStore:
    def __init__(
        self,
        root: str | Path,
        *,
        scheduler_identity: SchedulerStateIdentity,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        candidate = Path(root)
        if not candidate.is_absolute():
            raise ValueError("store root must be an absolute caller-provided path")
        self.root = candidate.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "review-memory.sqlite3"
        self.scheduler_identity = scheduler_identity
        self._clock = clock or _utc_now
        self._timeout_seconds = timeout_seconds
        self._initialize()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("store clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            isolation_level=None,
            timeout=self._timeout_seconds,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                check = connection.execute("PRAGMA quick_check").fetchone()[0]
                if check != "ok":
                    raise DurableReviewStoreCorruptionError(
                        f"SQLite quick_check failed: {check}"
                    )
                connection.executescript(_SCHEMA_SQL)
                row = connection.execute(
                    "SELECT value FROM durable_review_meta WHERE key='schema_version'"
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO durable_review_meta(key,value) VALUES('schema_version',?)",
                        (DURABLE_REVIEW_SCHEMA_VERSION,),
                    )
                elif row["value"] != DURABLE_REVIEW_SCHEMA_VERSION:
                    raise DurableReviewStoreCorruptionError(
                        f"unsupported durable review schema: {row['value']}"
                    )
                identity_row = connection.execute(
                    """
                    SELECT value FROM durable_review_meta
                    WHERE key='scheduler_identity_json'
                    """
                ).fetchone()
                if identity_row is None:
                    connection.execute(
                        """
                        INSERT INTO durable_review_meta(key,value)
                        VALUES('scheduler_identity_json',?)
                        """,
                        (_model_json(self.scheduler_identity),),
                    )
                else:
                    try:
                        stored_identity = SchedulerStateIdentity.model_validate_json(
                            identity_row["value"]
                        )
                    except Exception as exc:
                        raise DurableReviewStoreCorruptionError(
                            f"invalid scheduler identity metadata: {exc}"
                        ) from exc
                    self._assert_scheduler_identity(stored_identity)
        except (DurableReviewStoreCorruptionError, SchedulerVersionMismatchError):
            raise
        except sqlite3.DatabaseError as exc:
            raise DurableReviewStoreCorruptionError(
                f"cannot initialize review memory DB: {exc}"
            ) from exc

    def _assert_scheduler_identity(self, identity: SchedulerStateIdentity) -> None:
        expected = self.scheduler_identity
        if (
            identity.package_name != expected.package_name
            or identity.package_version != expected.package_version
            or identity.adapter_id != expected.adapter_id
            or identity.scheduler_config_sha256 != expected.scheduler_config_sha256
            or identity.card_schema_version != expected.card_schema_version
        ):
            raise SchedulerVersionMismatchError(
                "stored FSRS scheduler identity differs from the active adapter; "
                "an explicit migration is required"
            )

    @staticmethod
    def _identity_from_row(row: sqlite3.Row) -> SchedulerStateIdentity:
        return SchedulerStateIdentity(
            package_name=row["scheduler_package"],
            package_version=row["scheduler_version"],
            adapter_id=row["scheduler_adapter_id"],
            scheduler_config_json=row["scheduler_config_json"],
            scheduler_config_sha256=row["scheduler_config_sha256"],
            card_schema_version=row["card_schema_version"],
        )

    def _item_from_row(self, row: sqlite3.Row) -> PersistedReviewMemoryItem:
        item_json = row["item_json"]
        if _sha256(item_json) != row["item_sha256"]:
            raise DurableReviewStoreCorruptionError("review item checksum mismatch")
        try:
            item = ReviewMemoryItem.model_validate_json(item_json)
            stored_at = _parse_utc(row["stored_at"])
            identity = self._identity_from_row(row)
        except Exception as exc:
            raise DurableReviewStoreCorruptionError(
                f"invalid persisted review item: {exc}"
            ) from exc
        if stored_at is None:
            raise DurableReviewStoreCorruptionError("stored_at cannot be null")
        if (
            item.student_id != row["student_id"]
            or item.item_id != row["item_id"]
            or item.concept_id != row["concept_id"]
            or item.question_id != row["question_id"]
            or item.scheduler_card_json != row["card_json"]
            or _utc_text(item.due_at) != row["due_at"]
            or item.difficulty != row["difficulty"]
            or item.stability != row["stability"]
            or item.retrievability != row["retrievability"]
            or item.review_count != row["review_count"]
            or item.failure_count != row["failure_count"]
            or item.hint_dependency.value != row["hint_dependency"]
            or (
                None
                if item.last_observation is None
                else item.last_observation.value
            )
            != row["last_observation"]
            or item.critical_weight != row["critical_weight"]
        ):
            raise DurableReviewStoreCorruptionError(
                "review item columns disagree with immutable item JSON"
            )
        self._assert_scheduler_identity(identity)
        return PersistedReviewMemoryItem(
            item=item,
            state_version=row["state_version"],
            failure_streak=row["failure_streak"],
            scheduler_identity=identity,
            stored_at=stored_at,
        )

    @staticmethod
    def _item_values(
        item: ReviewMemoryItem,
        *,
        state_version: int,
        failure_streak: int,
        identity: SchedulerStateIdentity,
        stored_at: datetime,
    ) -> tuple[object, ...]:
        item_json = _model_json(item)
        return (
            item.student_id,
            item.item_id,
            item.concept_id,
            item.question_id,
            item_json,
            _sha256(item_json),
            item.scheduler_card_json,
            _utc_text(item.due_at),
            item.difficulty,
            item.stability,
            item.retrievability,
            item.review_count,
            item.failure_count,
            failure_streak,
            item.hint_dependency.value,
            None if item.last_observation is None else item.last_observation.value,
            item.critical_weight,
            state_version,
            identity.package_name,
            identity.package_version,
            identity.adapter_id,
            identity.scheduler_config_json,
            identity.scheduler_config_sha256,
            identity.card_schema_version,
            _utc_text(stored_at),
        )

    def register_items(
        self,
        items: Sequence[ReviewMemoryItem],
    ) -> tuple[PersistedReviewMemoryItem, ...]:
        if not items:
            return ()
        for item in items:
            if (
                item.review_count != 0
                or item.failure_count != 0
                or item.consecutive_recalls != 0
                or item.last_review_at is not None
                or item.last_observation is not None
                or item.last_review_log_id is not None
                or item.scheduler_card_json != "{}"
                or item.due_at is not None
                or item.difficulty is not None
                or item.stability is not None
                or item.retrievability is not None
            ):
                raise ValueError("new item registration cannot import scheduled state")
        now = self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            results: list[PersistedReviewMemoryItem] = []
            for item in items:
                row = connection.execute(
                    "SELECT * FROM review_items WHERE student_id=? AND item_id=?",
                    (item.student_id, item.item_id),
                ).fetchone()
                if row is not None:
                    existing = self._item_from_row(row)
                    if (
                        existing.item.student_id != item.student_id
                        or existing.item.item_id != item.item_id
                        or existing.item.concept_id != item.concept_id
                        or existing.item.question_id != item.question_id
                        or existing.item.critical_weight != item.critical_weight
                    ):
                        raise ReviewItemConflictError(
                            "review item already exists with a different immutable "
                            f"definition: {item.item_id}"
                        )
                    results.append(existing)
                    continue
                connection.execute(
                    """
                    INSERT INTO review_items(
                        student_id,item_id,concept_id,question_id,item_json,item_sha256,
                        card_json,due_at,difficulty,stability,retrievability,
                        review_count,failure_count,failure_streak,hint_dependency,
                        last_observation,critical_weight,state_version,
                        scheduler_package,scheduler_version,scheduler_adapter_id,
                        scheduler_config_json,scheduler_config_sha256,
                        card_schema_version,stored_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    self._item_values(
                        item,
                        state_version=1,
                        failure_streak=0,
                        identity=self.scheduler_identity,
                        stored_at=now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM review_items WHERE student_id=? AND item_id=?",
                    (item.student_id, item.item_id),
                ).fetchone()
                results.append(self._item_from_row(row))
            connection.execute("COMMIT")
            return tuple(results)
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def register_item(self, item: ReviewMemoryItem) -> PersistedReviewMemoryItem:
        return self.register_items((item,))[0]

    def load_item(self, student_id: str, item_id: str) -> PersistedReviewMemoryItem:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM review_items WHERE student_id=? AND item_id=?",
                (student_id.strip(), item_id.strip()),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown review item: {student_id}/{item_id}")
        return self._item_from_row(row)

    def load_items(
        self,
        keys: Sequence[tuple[str, str]],
    ) -> tuple[PersistedReviewMemoryItem, ...]:
        if not keys:
            return ()
        if len(keys) > 10_000:
            raise ValueError("item load batch cannot exceed 10000 keys")
        normalized = tuple((student.strip(), item.strip()) for student, item in keys)
        if any(not student or not item for student, item in normalized):
            raise ValueError("student_id and item_id must be non-empty")
        found: dict[tuple[str, str], PersistedReviewMemoryItem] = {}
        grouped: dict[str, list[str]] = {}
        for student_id, item_id in normalized:
            grouped.setdefault(student_id, []).append(item_id)
        with self._connect() as connection:
            for student_id, item_ids in grouped.items():
                unique_ids = tuple(dict.fromkeys(item_ids))
                for offset in range(0, len(unique_ids), 800):
                    chunk = unique_ids[offset : offset + 800]
                    placeholders = ",".join("?" for _ in chunk)
                    rows = connection.execute(
                        "SELECT * FROM review_items WHERE student_id=? "
                        f"AND item_id IN ({placeholders})",
                        (student_id, *chunk),
                    ).fetchall()
                    for row in rows:
                        persisted = self._item_from_row(row)
                        found[(persisted.item.student_id, persisted.item.item_id)] = persisted
        missing = tuple(key for key in normalized if key not in found)
        if missing:
            raise KeyError(f"unknown review items: {missing[:5]}")
        return tuple(found[key] for key in normalized)

    @staticmethod
    def _observation_payload(
        observation: ReviewObservation,
        boundary: MacroRoundBoundary,
    ) -> tuple[str, str, str]:
        observation_json = _model_json(observation)
        boundary_json = _model_json(boundary)
        envelope = _canonical_json(
            {
                "boundary": json.loads(boundary_json),
                "observation": json.loads(observation_json),
            }
        )
        return observation_json, boundary_json, _sha256(envelope)

    @staticmethod
    def _observation_from_row(
        row: sqlite3.Row,
    ) -> DurableReviewObservationRecord:
        payload = row["observation_json"]
        try:
            envelope = _canonical_json(
                {
                    "boundary": json.loads(row["boundary_json"]),
                    "observation": json.loads(payload),
                }
            )
            if _sha256(envelope) != row["payload_sha256"]:
                raise DurableReviewStoreCorruptionError(
                    "review observation checksum mismatch"
                )
            observation = ReviewObservation.model_validate_json(payload)
            boundary = MacroRoundBoundary.model_validate_json(row["boundary_json"])
            decision = (
                None
                if row["rating_decision_json"] is None
                else ReviewRatingDecision.model_validate_json(
                    row["rating_decision_json"]
                )
            )
            created_at = _parse_utc(row["created_at"])
            applied_at = _parse_utc(row["applied_at"])
        except DurableReviewStoreCorruptionError:
            raise
        except Exception as exc:
            raise DurableReviewStoreCorruptionError(
                f"invalid review observation: {exc}"
            ) from exc
        if created_at is None:
            raise DurableReviewStoreCorruptionError("created_at cannot be null")
        return DurableReviewObservationRecord(
            observation=observation,
            boundary=boundary,
            payload_sha256=row["payload_sha256"],
            status=DurableObservationStatus(row["status"]),
            rating_decision=decision,
            receipt_id=row["receipt_id"],
            created_at=created_at,
            applied_at=applied_at,
        )

    def begin_observation(
        self,
        observation: ReviewObservation,
        *,
        boundary: MacroRoundBoundary,
        _connection: sqlite3.Connection | None = None,
    ) -> DurableReviewObservationRecord:
        payload, boundary_json, payload_hash = self._observation_payload(
            observation,
            boundary,
        )
        owns_connection = _connection is None
        connection = _connection or self._connect()
        try:
            if owns_connection:
                connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM review_observations
                WHERE student_id=? AND observation_id=?
                """,
                (observation.student_id, observation.observation_id),
            ).fetchone()
            if row is not None:
                if row["payload_sha256"] != payload_hash:
                    raise ReviewEventConflictError(
                        f"observation id has conflicting payload: {observation.observation_id}"
                    )
                record = self._observation_from_row(row)
                if owns_connection:
                    connection.execute("COMMIT")
                return record
            connection.execute(
                """
                INSERT INTO review_observations(
                    student_id,item_id,observation_id,observation_json,boundary_json,
                    payload_sha256,status,created_at
                ) VALUES (?,?,?,?,?,?,'pending',?)
                """,
                (
                    observation.student_id,
                    observation.item_id,
                    observation.observation_id,
                    payload,
                    boundary_json,
                    payload_hash,
                    _utc_text(self._now()),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM review_observations
                WHERE student_id=? AND observation_id=?
                """,
                (observation.student_id, observation.observation_id),
            ).fetchone()
            if owns_connection:
                connection.execute("COMMIT")
            return self._observation_from_row(row)
        except Exception:
            if owns_connection and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            if owns_connection:
                connection.close()

    def begin_observations(
        self,
        entries: Sequence[tuple[ReviewObservation, MacroRoundBoundary]],
    ) -> tuple[DurableReviewObservationRecord, ...]:
        if not entries:
            return ()
        if len(entries) > 1_000:
            raise ValueError("observation batch cannot exceed 1000 entries")
        keys = tuple(
            (observation.student_id, observation.observation_id)
            for observation, _ in entries
        )
        if len(keys) != len(set(keys)):
            raise ValueError("observation batch contains duplicate event ids")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            records = tuple(
                self.begin_observation(
                    observation,
                    boundary=boundary,
                    _connection=connection,
                )
                for observation, boundary in entries
            )
            connection.execute("COMMIT")
            return records
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    @staticmethod
    def _receipt_from_row(row: sqlite3.Row) -> DurableReviewReceipt:
        payload = row["receipt_json"]
        if payload is None:
            raise DurableReviewStoreCorruptionError(
                "applied observation is missing receipt JSON"
            )
        try:
            receipt = DurableReviewReceipt.model_validate_json(payload)
            decision = ReviewRatingDecision.model_validate_json(
                row["rating_decision_json"]
            )
        except Exception as exc:
            raise DurableReviewStoreCorruptionError(
                f"invalid durable review receipt: {exc}"
            ) from exc
        observation = receipt.receipt.observation
        if (
            receipt.receipt.receipt_id != row["receipt_id"]
            or observation.observation_id != row["observation_id"]
            or observation.student_id != row["student_id"]
            or observation.item_id != row["item_id"]
            or receipt.receipt.rating_decision != decision
        ):
            raise DurableReviewStoreCorruptionError(
                "durable receipt columns disagree with receipt JSON"
            )
        return receipt

    def load_receipt(
        self,
        student_id: str,
        observation_id: str,
    ) -> DurableReviewReceipt | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM review_observations
                WHERE student_id=? AND observation_id=?
                """,
                (student_id.strip(), observation_id.strip()),
            ).fetchone()
        if row is None or row["status"] != DurableObservationStatus.APPLIED.value:
            return None
        receipt = self._receipt_from_row(row)
        return receipt.model_copy(update={"idempotent_replay": True})

    def load_receipts(
        self,
        keys: Sequence[tuple[str, str]],
    ) -> tuple[DurableReviewReceipt | None, ...]:
        if not keys:
            return ()
        if len(keys) > 1_000:
            raise ValueError("receipt load batch cannot exceed 1000 keys")
        normalized = tuple((student.strip(), event.strip()) for student, event in keys)
        found: dict[tuple[str, str], DurableReviewReceipt] = {}
        grouped: dict[str, list[str]] = {}
        for student_id, observation_id in normalized:
            grouped.setdefault(student_id, []).append(observation_id)
        with self._connect() as connection:
            for student_id, observation_ids in grouped.items():
                unique_ids = tuple(dict.fromkeys(observation_ids))
                for offset in range(0, len(unique_ids), 800):
                    chunk = unique_ids[offset : offset + 800]
                    placeholders = ",".join("?" for _ in chunk)
                    rows = connection.execute(
                        "SELECT * FROM review_observations WHERE student_id=? "
                        f"AND observation_id IN ({placeholders}) AND status='applied'",
                        (student_id, *chunk),
                    ).fetchall()
                    for row in rows:
                        found[(row["student_id"], row["observation_id"])] = (
                            self._receipt_from_row(row).model_copy(
                                update={"idempotent_replay": True}
                            )
                        )
        return tuple(found.get(key) for key in normalized)

    def _after_updates_before_commit(self, receipt: DurableReviewReceipt) -> None:
        """Crash injection seam: all SQL is still uncommitted."""

    def commit_review(
        self,
        *,
        observation: ReviewObservation,
        boundary: MacroRoundBoundary,
        expected_state_version: int,
        item_after: ReviewMemoryItem,
        failure_streak: int,
        rating_decision: ReviewRatingDecision,
        durable_receipt: DurableReviewReceipt,
        review_log: DurableReviewLog,
        _connection: sqlite3.Connection | None = None,
        _allow_replay: bool = True,
    ) -> DurableReviewReceipt:
        _, _, payload_hash = self._observation_payload(observation, boundary)
        owns_connection = _connection is None
        connection = _connection or self._connect()
        try:
            if owns_connection:
                connection.execute("BEGIN IMMEDIATE")
            observation_row = connection.execute(
                """
                SELECT * FROM review_observations
                WHERE student_id=? AND observation_id=?
                """,
                (observation.student_id, observation.observation_id),
            ).fetchone()
            if observation_row is None:
                raise DurableReviewStoreError("observation must be persisted first")
            if observation_row["payload_sha256"] != payload_hash:
                raise ReviewEventConflictError(
                    f"observation id has conflicting payload: {observation.observation_id}"
                )
            if observation_row["status"] == DurableObservationStatus.APPLIED.value:
                if not _allow_replay:
                    raise ReviewItemVersionConflictError(
                        "observation was applied concurrently; batch must recompute"
                    )
                existing = self._receipt_from_row(observation_row)
                if owns_connection:
                    connection.execute("COMMIT")
                return existing.model_copy(update={"idempotent_replay": True})

            earlier_pending = connection.execute(
                """
                SELECT observation_id FROM review_observations
                WHERE student_id=? AND item_id=? AND status='pending' AND sequence<?
                ORDER BY sequence LIMIT 1
                """,
                (
                    observation.student_id,
                    observation.item_id,
                    observation_row["sequence"],
                ),
            ).fetchone()
            if earlier_pending is not None:
                raise ReviewObservationOrderError(
                    "an earlier observation for this item is still pending: "
                    f"{earlier_pending['observation_id']}"
                )

            item_row = connection.execute(
                "SELECT * FROM review_items WHERE student_id=? AND item_id=?",
                (observation.student_id, observation.item_id),
            ).fetchone()
            if item_row is None:
                raise KeyError(f"unknown review item: {observation.item_id}")
            persisted_before = self._item_from_row(item_row)
            if persisted_before.state_version != expected_state_version:
                raise ReviewItemVersionConflictError(
                    f"expected item version {expected_state_version}, "
                    f"found {persisted_before.state_version}"
                )
            new_version = expected_state_version + 1
            if failure_streak < 0:
                raise ValueError("failure_streak cannot be negative")
            receipt = durable_receipt.receipt
            if receipt.observation != observation:
                raise ValueError("durable receipt observation differs from commit input")
            if receipt.item_before != persisted_before.item:
                raise ValueError("durable receipt item_before differs from stored item")
            if receipt.item_after != item_after:
                raise ValueError("durable receipt item_after differs from commit input")
            if rating_decision != receipt.rating_decision:
                raise ValueError("rating decision differs from durable receipt")
            if durable_receipt.persisted_state_version != new_version:
                raise ValueError("durable receipt has incorrect next state version")
            if review_log.state_version != new_version:
                raise ValueError("review log has incorrect next state version")
            if (
                review_log.observation_id != observation.observation_id
                or review_log.student_id != observation.student_id
                or review_log.item_id != observation.item_id
                or review_log.receipt_id != receipt.receipt_id
                or review_log.review_log_id != receipt.scheduler_result.review_log_id
                or review_log.rating != rating_decision.rating
                or review_log.card_before_json != persisted_before.item.scheduler_card_json
                or review_log.card_after_json != item_after.scheduler_card_json
                or review_log.due_at != item_after.due_at
                or review_log.difficulty != item_after.difficulty
                or review_log.stability != item_after.stability
                or review_log.retrievability != item_after.retrievability
                or review_log.reviewed_at != observation.observed_at
                or review_log.source_event_ids != observation.source_event_ids
            ):
                raise ValueError("review log differs from committed review result")
            self._assert_scheduler_identity(review_log.scheduler_identity)

            log_json = _model_json(review_log)
            connection.execute(
                """
                INSERT INTO review_logs(
                    review_log_id,receipt_id,observation_id,student_id,item_id,
                    rating,reviewed_at,due_at,state_version,scheduler_version,
                    log_json,log_sha256
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    review_log.review_log_id,
                    review_log.receipt_id,
                    review_log.observation_id,
                    review_log.student_id,
                    review_log.item_id,
                    review_log.rating.value,
                    _utc_text(review_log.reviewed_at),
                    _utc_text(review_log.due_at),
                    review_log.state_version,
                    review_log.scheduler_identity.package_version,
                    log_json,
                    _sha256(log_json),
                ),
            )
            values = self._item_values(
                item_after,
                state_version=new_version,
                failure_streak=failure_streak,
                identity=self.scheduler_identity,
                stored_at=self._now(),
            )
            connection.execute(
                """
                UPDATE review_items SET
                    concept_id=?,question_id=?,item_json=?,item_sha256=?,card_json=?,
                    due_at=?,difficulty=?,stability=?,retrievability=?,review_count=?,
                    failure_count=?,failure_streak=?,hint_dependency=?,last_observation=?,
                    critical_weight=?,state_version=?,scheduler_package=?,
                    scheduler_version=?,scheduler_adapter_id=?,scheduler_config_json=?,
                    scheduler_config_sha256=?,card_schema_version=?,stored_at=?
                WHERE student_id=? AND item_id=? AND state_version=?
                """,
                (
                    *values[2:],
                    observation.student_id,
                    observation.item_id,
                    expected_state_version,
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ReviewItemVersionConflictError(
                    "review item changed during durable commit"
                )
            receipt_json = _model_json(durable_receipt)
            connection.execute(
                """
                UPDATE review_observations SET
                    status='applied',rating_decision_json=?,receipt_id=?,
                    receipt_json=?,applied_at=?
                WHERE student_id=? AND observation_id=? AND status='pending'
                """,
                (
                    _model_json(rating_decision),
                    durable_receipt.receipt.receipt_id,
                    receipt_json,
                    _utc_text(self._now()),
                    observation.student_id,
                    observation.observation_id,
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ReviewItemVersionConflictError(
                    "observation changed during durable commit"
                )
            self._after_updates_before_commit(durable_receipt)
            if owns_connection:
                connection.execute("COMMIT")
            return durable_receipt
        except Exception:
            if owns_connection and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            if owns_connection:
                connection.close()

    def commit_review_batch(
        self,
        commits: Sequence[DurableReviewCommit],
    ) -> tuple[DurableReviewReceipt, ...]:
        if not commits:
            return ()
        if len(commits) > 1_000:
            raise ValueError("review commit batch cannot exceed 1000 entries")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            receipts = tuple(
                self.commit_review(
                    observation=commit.observation,
                    boundary=commit.boundary,
                    expected_state_version=commit.expected_state_version,
                    item_after=commit.item_after,
                    failure_streak=commit.failure_streak,
                    rating_decision=commit.rating_decision,
                    durable_receipt=commit.durable_receipt,
                    review_log=commit.review_log,
                    _connection=connection,
                    _allow_replay=False,
                )
                for commit in commits
            )
            connection.execute("COMMIT")
            return receipts
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def pending_observations(
        self,
        *,
        student_id: str | None = None,
        limit: int = 500,
    ) -> tuple[DurableReviewObservationRecord, ...]:
        if not 1 <= limit <= 10_000:
            raise ValueError("limit must be between 1 and 10000")
        clauses = ["status='pending'"]
        parameters: list[object] = []
        if student_id is not None:
            clauses.append("student_id=?")
            parameters.append(student_id.strip())
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM review_observations WHERE "
                + " AND ".join(clauses)
                + " ORDER BY sequence LIMIT ?",
                parameters,
            ).fetchall()
        return tuple(self._observation_from_row(row) for row in rows)

    def queue_candidates(
        self,
        *,
        student_id: str,
        as_of: datetime,
        limit: int = 10_000,
    ) -> tuple[PersistedReviewMemoryItem, ...]:
        if not 1 <= limit <= 10_000:
            raise ValueError("limit must be between 1 and 10000")
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        weak = (
            "failed",
            "partial",
            "hint_dependent",
            "recalled_with_difficulty",
        )
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM review_items
                WHERE student_id=? AND (
                    (due_at IS NOT NULL AND due_at <= ?)
                    OR last_observation IN (?,?,?,?)
                )
                ORDER BY
                    CASE WHEN due_at IS NOT NULL AND due_at <= ? THEN 0 ELSE 1 END,
                    due_at,
                    failure_streak DESC,
                    critical_weight DESC,
                    item_id
                LIMIT ?
                """,
                (
                    student_id.strip(),
                    _utc_text(as_of),
                    *weak,
                    _utc_text(as_of),
                    limit,
                ),
            ).fetchall()
        return tuple(self._item_from_row(row) for row in rows)

    @staticmethod
    def _log_from_row(row: sqlite3.Row) -> DurableReviewLog:
        payload = row["log_json"]
        if _sha256(payload) != row["log_sha256"]:
            raise DurableReviewStoreCorruptionError("review log checksum mismatch")
        try:
            log = DurableReviewLog.model_validate_json(payload)
        except Exception as exc:
            raise DurableReviewStoreCorruptionError(
                f"invalid review log: {exc}"
            ) from exc
        if (
            log.review_log_id != row["review_log_id"]
            or log.receipt_id != row["receipt_id"]
            or log.observation_id != row["observation_id"]
            or log.student_id != row["student_id"]
            or log.item_id != row["item_id"]
            or log.rating.value != row["rating"]
            or _utc_text(log.reviewed_at) != row["reviewed_at"]
            or _utc_text(log.due_at) != row["due_at"]
            or log.state_version != row["state_version"]
            or log.scheduler_identity.package_version != row["scheduler_version"]
        ):
            raise DurableReviewStoreCorruptionError(
                "review log columns disagree with append-only log JSON"
            )
        data = log.model_dump()
        data["sequence"] = row["sequence"]
        return DurableReviewLog.model_validate(data)

    def review_history(
        self,
        *,
        student_id: str,
        item_id: str | None = None,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> ReviewHistoryPage:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        clauses = ["student_id=?", "sequence>?"]
        parameters: list[object] = [student_id.strip(), after_sequence]
        if item_id is not None:
            clauses.append("item_id=?")
            parameters.append(item_id.strip())
        parameters.append(limit + 1)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM review_logs WHERE "
                + " AND ".join(clauses)
                + " ORDER BY sequence LIMIT ?",
                parameters,
            ).fetchall()
        has_more = len(rows) > limit
        records = tuple(self._log_from_row(row) for row in rows[:limit])
        return ReviewHistoryPage(
            records=records,
            next_after_sequence=(
                records[-1].sequence if has_more and records else None
            ),
        )

    def analytics(self, student_id: str, item_id: str) -> ReviewMemoryAnalytics:
        persisted = self.load_item(student_id, item_id)
        item = persisted.item
        return ReviewMemoryAnalytics(
            student_id=item.student_id,
            item_id=item.item_id,
            concept_id=item.concept_id,
            difficulty=item.difficulty,
            stability=item.stability,
            retrievability=item.retrievability,
            retrievability_calculated_at=item.last_review_at,
            due_at=item.due_at,
            failure_streak=persisted.failure_streak,
            hint_dependency=item.hint_dependency,
            review_count=item.review_count,
            state_version=persisted.state_version,
            scheduler_version=persisted.scheduler_identity.package_version,
        )

    def counts(self) -> tuple[int, int]:
        with self._connect() as connection:
            item_count = connection.execute(
                "SELECT COUNT(*) FROM review_items"
            ).fetchone()[0]
            event_count = connection.execute(
                "SELECT COUNT(*) FROM review_logs"
            ).fetchone()[0]
        return item_count, event_count

    def checkpoint(
        self,
        mode: str = "PASSIVE",
    ) -> ReviewCheckpointReceipt:
        normalized = mode.strip().upper()
        if normalized not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
            raise ValueError("unsupported WAL checkpoint mode")
        with self._connect() as connection:
            busy, wal_pages, checkpointed = connection.execute(
                f"PRAGMA wal_checkpoint({normalized})"
            ).fetchone()
        item_count, event_count = self.counts()
        return ReviewCheckpointReceipt(
            database_path=str(self.db_path),
            mode=normalized,
            busy=busy,
            wal_pages=wal_pages,
            checkpointed_pages=checkpointed,
            item_count=item_count,
            review_event_count=event_count,
            completed_at=self._now(),
        )


__all__ = [
    "DurableReviewCommit",
    "DurableReviewStoreCorruptionError",
    "DurableReviewStoreError",
    "ReviewEventConflictError",
    "ReviewItemConflictError",
    "ReviewItemVersionConflictError",
    "ReviewObservationOrderError",
    "SchedulerVersionMismatchError",
    "SqliteReviewMemoryStore",
]
