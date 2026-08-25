"""Append-only SQLite repository and auditable snapshot checkpoints."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from ..learning_state import LearningStateReducer, ReducerResult
from ..student_model import (
    DuplicateEventConflictError,
    StudentLearningEvent,
    StudentLearningEventType,
)
from .codec import (
    canonical_json,
    event_from_data,
    event_from_json,
    event_to_data,
    event_to_json,
    parse_utc,
    result_from_json,
    result_to_json,
    sha256_text,
    utc_text,
)
from .models import (
    CheckpointPolicy,
    CheckpointValidation,
    StudentEventCursor,
    StudentEventPage,
    StudentEventRecord,
    StudentSnapshotCheckpoint,
)


REPOSITORY_SCHEMA_VERSION = "student-event-repository.v1"


class StudentRepositoryError(RuntimeError):
    pass


class StudentRepositoryCorruptionError(StudentRepositoryError):
    pass


class StudentRepositorySchemaError(StudentRepositoryError):
    pass


class CheckpointValidationError(StudentRepositoryError):
    pass


_LOCK_REGISTRY_GUARD = threading.Lock()
_STUDENT_LOCKS: dict[tuple[str, str], threading.RLock] = {}


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS repository_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS student_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    UNIQUE(student_id, session_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_student_events_scope_sequence
    ON student_events(student_id, session_id, sequence);
CREATE INDEX IF NOT EXISTS idx_student_events_concept_sequence
    ON student_events(student_id, session_id, concept_id, sequence);
CREATE INDEX IF NOT EXISTS idx_student_events_type_sequence
    ON student_events(student_id, session_id, event_type, sequence);
CREATE INDEX IF NOT EXISTS idx_student_events_occurred
    ON student_events(student_id, session_id, occurred_at, event_id);

CREATE TABLE IF NOT EXISTS student_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    through_sequence INTEGER NOT NULL,
    event_count INTEGER NOT NULL,
    event_digest TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    replay_events_json TEXT NOT NULL,
    replay_events_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(student_id, session_id, through_sequence)
);

CREATE INDEX IF NOT EXISTS idx_student_checkpoints_scope_sequence
    ON student_checkpoints(student_id, session_id, through_sequence DESC);

CREATE TRIGGER IF NOT EXISTS student_events_no_update
BEFORE UPDATE ON student_events
BEGIN
    SELECT RAISE(ABORT, 'student_events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS student_events_no_delete
BEFORE DELETE ON student_events
BEGIN
    SELECT RAISE(ABORT, 'student_events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS student_checkpoints_no_update
BEFORE UPDATE ON student_checkpoints
BEGIN
    SELECT RAISE(ABORT, 'student_checkpoints are append-only');
END;

CREATE TRIGGER IF NOT EXISTS student_checkpoints_no_delete
BEFORE DELETE ON student_checkpoints
BEGIN
    SELECT RAISE(ABORT, 'student_checkpoints are append-only');
END;
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_identity(student_id: str, session_id: str) -> tuple[str, str]:
    normalized_student_id = student_id.strip()
    normalized_session_id = session_id.strip()
    if not normalized_student_id or not normalized_session_id:
        raise ValueError("student_id and session_id must be non-empty")
    return normalized_student_id, normalized_session_id


class StudentEventRepository:
    """SQLite-backed, append-only source of student learning events."""

    def __init__(
        self,
        root: str | Path,
        *,
        reducer: LearningStateReducer | None = None,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        candidate = Path(root)
        if not candidate.is_absolute():
            raise ValueError("repository root must be an absolute caller-provided path")
        self.root = candidate.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "student-events.sqlite3"
        self.reducer = reducer or LearningStateReducer()
        self._clock = clock or _utc_now
        self._timeout_seconds = timeout_seconds
        self._initialize()

    def _student_lock(self, student_id: str) -> threading.RLock:
        student_key = hashlib.sha256(student_id.strip().encode("utf-8")).hexdigest()
        key = (str(self.db_path).casefold(), student_key)
        with _LOCK_REGISTRY_GUARD:
            return _STUDENT_LOCKS.setdefault(key, threading.RLock())

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=self._timeout_seconds,
            isolation_level=None,
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
                quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
                if quick_check != "ok":
                    raise StudentRepositoryCorruptionError(
                        f"SQLite quick_check failed: {quick_check}"
                    )
                connection.executescript(_SCHEMA_SQL)
                row = connection.execute(
                    "SELECT value FROM repository_meta WHERE key = 'schema_version'"
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO repository_meta(key, value) VALUES('schema_version', ?)",
                        (REPOSITORY_SCHEMA_VERSION,),
                    )
                elif row["value"] != REPOSITORY_SCHEMA_VERSION:
                    raise StudentRepositorySchemaError(
                        f"unsupported repository schema: {row['value']}"
                    )
        except (StudentRepositoryCorruptionError, StudentRepositorySchemaError):
            raise
        except sqlite3.DatabaseError as exc:
            raise StudentRepositoryCorruptionError(
                f"cannot initialize SQLite repository {self.db_path}: {exc}"
            ) from exc

    def validate_storage(self) -> bool:
        try:
            with self._connect() as connection:
                quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
                if quick_check != "ok":
                    raise StudentRepositoryCorruptionError(
                        f"SQLite quick_check failed: {quick_check}"
                    )
                for row in connection.execute(
                    "SELECT * FROM student_events ORDER BY sequence"
                ):
                    self._record_from_row(row)
                for row in connection.execute(
                    "SELECT * FROM student_checkpoints ORDER BY through_sequence"
                ):
                    self._checkpoint_from_row(row)
                return True
        except StudentRepositoryCorruptionError:
            raise
        except sqlite3.DatabaseError as exc:
            raise StudentRepositoryCorruptionError(
                f"cannot validate SQLite repository: {exc}"
            ) from exc

    def _ingested_at(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("repository clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> StudentEventRecord:
        payload_json = row["payload_json"]
        if sha256_text(payload_json) != row["payload_sha256"]:
            raise StudentRepositoryCorruptionError(
                f"event payload checksum mismatch at sequence {row['sequence']}"
            )
        try:
            event = event_from_json(payload_json)
            occurred_at = parse_utc(row["occurred_at"])
            ingested_at = parse_utc(row["ingested_at"])
        except Exception as exc:
            raise StudentRepositoryCorruptionError(
                f"invalid event row at sequence {row['sequence']}: {exc}"
            ) from exc
        if occurred_at is None or ingested_at is None:
            raise StudentRepositoryCorruptionError("event timestamps cannot be null")
        columns = (
            row["event_id"],
            row["student_id"],
            row["session_id"],
            row["concept_id"],
            row["event_type"],
            row["occurred_at"],
        )
        expected = (
            event.event_id,
            event.student_id,
            event.session_id,
            event.concept_id,
            event.event_type.value,
            utc_text(event.occurred_at),
        )
        if columns != expected:
            raise StudentRepositoryCorruptionError(
                f"event columns disagree with payload at sequence {row['sequence']}"
            )
        return StudentEventRecord(
            sequence=row["sequence"],
            event_id=event.event_id,
            student_id=event.student_id,
            session_id=event.session_id,
            concept_id=event.concept_id,
            event_type=event.event_type,
            occurred_at=occurred_at,
            ingested_at=ingested_at,
            event=event,
        )

    def append_events(
        self,
        events: Sequence[StudentLearningEvent],
    ) -> tuple[StudentEventRecord, ...]:
        if not events:
            return ()
        students = sorted({event.student_id for event in events})
        with ExitStack() as locks:
            for student_id in students:
                locks.enter_context(self._student_lock(student_id))
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                records: list[StudentEventRecord] = []
                for event in events:
                    existing = connection.execute(
                        """
                        SELECT * FROM student_events
                        WHERE student_id = ? AND session_id = ? AND event_id = ?
                        """,
                        (event.student_id, event.session_id, event.event_id),
                    ).fetchone()
                    if existing is not None:
                        record = self._record_from_row(existing)
                        if record.event != event:
                            raise DuplicateEventConflictError(
                                f"event_id {event.event_id!r} has conflicting content"
                            )
                        records.append(record)
                        continue
                    payload_json = event_to_json(event)
                    ingested_at = self._ingested_at()
                    cursor = connection.execute(
                        """
                        INSERT INTO student_events(
                            student_id, session_id, concept_id, event_type,
                            event_id, occurred_at, ingested_at,
                            payload_json, payload_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.student_id,
                            event.session_id,
                            event.concept_id,
                            event.event_type.value,
                            event.event_id,
                            utc_text(event.occurred_at),
                            utc_text(ingested_at),
                            payload_json,
                            sha256_text(payload_json),
                        ),
                    )
                    row = connection.execute(
                        "SELECT * FROM student_events WHERE sequence = ?",
                        (cursor.lastrowid,),
                    ).fetchone()
                    records.append(self._record_from_row(row))
                connection.execute("COMMIT")
                return tuple(records)
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def append_event(self, event: StudentLearningEvent) -> StudentEventRecord:
        """Append one event, returning its stable persisted identity."""

        return self.append_events((event,))[0]

    def get_event(
        self,
        *,
        student_id: str,
        session_id: str,
        event_id: str,
    ) -> StudentEventRecord | None:
        normalized_student_id, normalized_session_id = _normalize_identity(
            student_id, session_id
        )
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be non-empty")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM student_events
                    WHERE student_id = ? AND session_id = ? AND event_id = ?
                    """,
                    (
                        normalized_student_id,
                        normalized_session_id,
                        normalized_event_id,
                    ),
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise StudentRepositoryCorruptionError(
                f"event identity query failed: {exc}"
            ) from exc
        return None if row is None else self._record_from_row(row)

    def get_events(
        self,
        *,
        student_id: str,
        session_id: str,
        event_ids: Sequence[str],
    ) -> tuple[StudentEventRecord, ...]:
        """Resolve a bounded identity set in caller order with one DB query."""

        normalized_student_id, normalized_session_id = _normalize_identity(
            student_id, session_id
        )
        normalized_event_ids = tuple(
            dict.fromkeys(event_id.strip() for event_id in event_ids)
        )
        if any(not event_id for event_id in normalized_event_ids):
            raise ValueError("event_ids cannot contain blank values")
        if len(normalized_event_ids) > 256:
            raise ValueError("event_ids cannot contain more than 256 identities")
        if not normalized_event_ids:
            return ()
        placeholders = ",".join("?" for _ in normalized_event_ids)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM student_events "
                    "WHERE student_id = ? AND session_id = ? "
                    f"AND event_id IN ({placeholders})",
                    (
                        normalized_student_id,
                        normalized_session_id,
                        *normalized_event_ids,
                    ),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise StudentRepositoryCorruptionError(
                f"event identity query failed: {exc}"
            ) from exc
        records_by_id = {
            record.event_id: record for record in map(self._record_from_row, rows)
        }
        return tuple(
            records_by_id[event_id]
            for event_id in normalized_event_ids
            if event_id in records_by_id
        )

    def load_events(
        self,
        *,
        student_id: str,
        session_id: str,
    ) -> tuple[StudentLearningEvent, ...]:
        """Load one replay scope for compatibility with StudentModelStore."""

        normalized_student_id, normalized_session_id = _normalize_identity(
            student_id, session_id
        )
        try:
            with self._connect() as connection:
                records = self._scope_records(
                    connection,
                    student_id=normalized_student_id,
                    session_id=normalized_session_id,
                )
        except sqlite3.DatabaseError as exc:
            raise StudentRepositoryCorruptionError(
                f"event replay query failed: {exc}"
            ) from exc
        return tuple(record.event for record in records)

    @staticmethod
    def _query_records(
        connection: sqlite3.Connection,
        *,
        student_id: str,
        session_id: str | None = None,
        concept_id: str | None = None,
        event_type: StudentLearningEventType | None = None,
        after_sequence: int = 0,
        through_sequence: int | None = None,
        limit: int | None = None,
        descending: bool = False,
    ) -> tuple[StudentEventRecord, ...]:
        clauses = ["student_id = ?", "sequence > ?"]
        parameters: list[object] = [student_id, after_sequence]
        if session_id is not None:
            clauses.append("session_id = ?")
            parameters.append(session_id)
        if concept_id is not None:
            clauses.append("concept_id = ?")
            parameters.append(concept_id)
        if event_type is not None:
            clauses.append("event_type = ?")
            parameters.append(event_type.value)
        if through_sequence is not None:
            clauses.append("sequence <= ?")
            parameters.append(through_sequence)
        order = "DESC" if descending else "ASC"
        sql = "SELECT * FROM student_events WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY sequence {order}"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        rows = connection.execute(sql, parameters).fetchall()
        return tuple(StudentEventRepository._record_from_row(row) for row in rows)

    def list_events(
        self,
        *,
        student_id: str,
        session_id: str | None = None,
        concept_id: str | None = None,
        event_type: StudentLearningEventType | None = None,
        cursor: StudentEventCursor | None = None,
        limit: int = 100,
    ) -> StudentEventPage:
        normalized_student_id = student_id.strip()
        if not normalized_student_id:
            raise ValueError("student_id must be non-empty")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        normalized_session_id = None if session_id is None else session_id.strip()
        normalized_concept_id = None if concept_id is None else concept_id.strip()
        after_sequence = 0 if cursor is None else cursor.after_sequence
        try:
            with self._connect() as connection:
                records = self._query_records(
                    connection,
                    student_id=normalized_student_id,
                    session_id=normalized_session_id,
                    concept_id=normalized_concept_id,
                    event_type=event_type,
                    after_sequence=after_sequence,
                    limit=limit + 1,
                )
        except sqlite3.DatabaseError as exc:
            raise StudentRepositoryCorruptionError(f"event query failed: {exc}") from exc
        has_more = len(records) > limit
        page_records = records[:limit]
        next_cursor = (
            StudentEventCursor(page_records[-1].sequence)
            if has_more and page_records
            else None
        )
        return StudentEventPage(records=page_records, next_cursor=next_cursor)

    def list_recent_events(
        self,
        *,
        student_id: str,
        session_id: str,
        concept_id: str | None = None,
        event_type: StudentLearningEventType | None = None,
        limit: int = 50,
    ) -> tuple[StudentEventRecord, ...]:
        normalized_student_id, normalized_session_id = _normalize_identity(
            student_id, session_id
        )
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._connect() as connection:
            descending = self._query_records(
                connection,
                student_id=normalized_student_id,
                session_id=normalized_session_id,
                concept_id=None if concept_id is None else concept_id.strip(),
                event_type=event_type,
                limit=limit,
                descending=True,
            )
        return tuple(reversed(descending))

    def list_sessions(self, *, student_id: str) -> tuple[str, ...]:
        normalized_student_id = student_id.strip()
        if not normalized_student_id:
            raise ValueError("student_id must be non-empty")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, MIN(sequence) AS first_sequence
                FROM student_events WHERE student_id = ?
                GROUP BY session_id ORDER BY first_sequence
                """,
                (normalized_student_id,),
            ).fetchall()
        return tuple(row["session_id"] for row in rows)

    def _scope_records(
        self,
        connection: sqlite3.Connection,
        *,
        student_id: str,
        session_id: str,
        through_sequence: int | None = None,
        after_sequence: int = 0,
    ) -> tuple[StudentEventRecord, ...]:
        return self._query_records(
            connection,
            student_id=student_id,
            session_id=session_id,
            after_sequence=after_sequence,
            through_sequence=through_sequence,
        )

    @staticmethod
    def _event_digest(records: Sequence[StudentEventRecord]) -> str:
        manifest = [
            {
                "sequence": record.sequence,
                "event_id": record.event_id,
                "payload_sha256": sha256_text(event_to_json(record.event)),
            }
            for record in records
        ]
        return sha256_text(canonical_json(manifest))

    @staticmethod
    def _checkpoint_from_row(row: sqlite3.Row) -> StudentSnapshotCheckpoint:
        snapshot_json = row["snapshot_json"]
        replay_events_json = row["replay_events_json"]
        if sha256_text(snapshot_json) != row["snapshot_sha256"]:
            raise StudentRepositoryCorruptionError(
                f"checkpoint snapshot checksum mismatch: {row['checkpoint_id']}"
            )
        if sha256_text(replay_events_json) != row["replay_events_sha256"]:
            raise StudentRepositoryCorruptionError(
                f"checkpoint replay capsule checksum mismatch: {row['checkpoint_id']}"
            )
        try:
            snapshot = result_from_json(snapshot_json)
            replay_payload = json.loads(replay_events_json)
            if not isinstance(replay_payload, list):
                raise TypeError("checkpoint replay capsule must be a list")
            replay_events = tuple(event_from_data(item) for item in replay_payload)
            created_at = parse_utc(row["created_at"])
        except Exception as exc:
            raise StudentRepositoryCorruptionError(
                f"invalid checkpoint {row['checkpoint_id']}: {exc}"
            ) from exc
        if created_at is None:
            raise StudentRepositoryCorruptionError("checkpoint created_at cannot be null")
        if (
            snapshot.student_id != row["student_id"]
            or snapshot.session_id != row["session_id"]
        ):
            raise StudentRepositoryCorruptionError(
                f"checkpoint identity mismatch: {row['checkpoint_id']}"
            )
        if len(replay_events) != row["event_count"] or any(
            event.student_id != row["student_id"]
            or event.session_id != row["session_id"]
            for event in replay_events
        ):
            raise StudentRepositoryCorruptionError(
                f"checkpoint replay capsule identity/count mismatch: {row['checkpoint_id']}"
            )
        return StudentSnapshotCheckpoint(
            checkpoint_id=row["checkpoint_id"],
            student_id=row["student_id"],
            session_id=row["session_id"],
            through_sequence=row["through_sequence"],
            event_count=row["event_count"],
            event_digest=row["event_digest"],
            snapshot_digest=row["snapshot_sha256"],
            snapshot=snapshot,
            created_at=created_at,
            replay_events=replay_events,
        )

    def load_latest_checkpoint(
        self,
        *,
        student_id: str,
        session_id: str,
    ) -> StudentSnapshotCheckpoint | None:
        normalized_student_id, normalized_session_id = _normalize_identity(
            student_id, session_id
        )
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM student_checkpoints
                WHERE student_id = ? AND session_id = ?
                ORDER BY through_sequence DESC LIMIT 1
                """,
                (normalized_student_id, normalized_session_id),
            ).fetchone()
        return None if row is None else self._checkpoint_from_row(row)

    def create_checkpoint(
        self,
        *,
        student_id: str,
        session_id: str,
        policy: CheckpointPolicy | None = None,
        session_boundary: bool = False,
        force: bool = False,
    ) -> StudentSnapshotCheckpoint | None:
        normalized_student_id, normalized_session_id = _normalize_identity(
            student_id, session_id
        )
        checkpoint_policy = policy or CheckpointPolicy()
        with self._student_lock(normalized_student_id):
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                records = self._scope_records(
                    connection,
                    student_id=normalized_student_id,
                    session_id=normalized_session_id,
                )
                if not records:
                    connection.execute("COMMIT")
                    return None
                previous_row = connection.execute(
                    """
                    SELECT * FROM student_checkpoints
                    WHERE student_id = ? AND session_id = ?
                    ORDER BY through_sequence DESC LIMIT 1
                    """,
                    (normalized_student_id, normalized_session_id),
                ).fetchone()
                previous_count = 0 if previous_row is None else previous_row["event_count"]
                if not force and not checkpoint_policy.should_checkpoint(
                    current_event_count=len(records),
                    previous_event_count=previous_count,
                    session_boundary=session_boundary,
                ):
                    connection.execute("COMMIT")
                    return (
                        None
                        if previous_row is None
                        else self._checkpoint_from_row(previous_row)
                    )
                snapshot = self.reducer.reduce(
                    (record.event for record in records),
                    student_id=normalized_student_id,
                    session_id=normalized_session_id,
                )
                snapshot_json = result_to_json(snapshot)
                replay_events_json = canonical_json(
                    [event_to_data(record.event) for record in records]
                )
                event_digest = self._event_digest(records)
                through_sequence = records[-1].sequence
                created_at = self._ingested_at()
                checkpoint_id = sha256_text(
                    canonical_json(
                        {
                            "student_id": normalized_student_id,
                            "session_id": normalized_session_id,
                            "through_sequence": through_sequence,
                            "event_digest": event_digest,
                            "snapshot_sha256": sha256_text(snapshot_json),
                        }
                    )
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO student_checkpoints(
                        checkpoint_id, student_id, session_id,
                        through_sequence, event_count, event_digest,
                        snapshot_json, snapshot_sha256,
                        replay_events_json, replay_events_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        checkpoint_id,
                        normalized_student_id,
                        normalized_session_id,
                        through_sequence,
                        len(records),
                        event_digest,
                        snapshot_json,
                        sha256_text(snapshot_json),
                        replay_events_json,
                        sha256_text(replay_events_json),
                        utc_text(created_at),
                    ),
                )
                row = connection.execute(
                    """
                    SELECT * FROM student_checkpoints
                    WHERE student_id = ? AND session_id = ? AND through_sequence = ?
                    """,
                    (
                        normalized_student_id,
                        normalized_session_id,
                        through_sequence,
                    ),
                ).fetchone()
                stored = self._checkpoint_from_row(row)
                if stored.checkpoint_id != checkpoint_id:
                    raise CheckpointValidationError(
                        "a checkpoint already exists at this event boundary "
                        "with a different reducer result"
                    )
                connection.execute("COMMIT")
                return stored
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def validate_checkpoint(
        self,
        checkpoint: StudentSnapshotCheckpoint,
    ) -> CheckpointValidation:
        with self._connect() as connection:
            prefix = self._scope_records(
                connection,
                student_id=checkpoint.student_id,
                session_id=checkpoint.session_id,
                through_sequence=checkpoint.through_sequence,
            )
            subsequent = self._scope_records(
                connection,
                student_id=checkpoint.student_id,
                session_id=checkpoint.session_id,
                after_sequence=checkpoint.through_sequence,
            )
        actual_event_digest = self._event_digest(prefix)
        actual_snapshot_digest = sha256_text(result_to_json(checkpoint.snapshot))
        prefix_replay = self.reducer.reduce(
            checkpoint.replay_events,
            student_id=checkpoint.student_id,
            session_id=checkpoint.session_id,
        )
        checkpoint_plus_subsequent = self.reducer.reduce(
            (*checkpoint.replay_events, *(record.event for record in subsequent)),
            student_id=checkpoint.student_id,
            session_id=checkpoint.session_id,
        )
        full_replay = self.reducer.reduce(
            (record.event for record in (*prefix, *subsequent)),
            student_id=checkpoint.student_id,
            session_id=checkpoint.session_id,
        )
        replay_capsule_match = (
            tuple(record.event for record in prefix) == checkpoint.replay_events
        )
        checkpoint_replay_match = (
            len(prefix) == checkpoint.event_count
            and replay_capsule_match
            and prefix_replay == checkpoint.snapshot
        )
        event_digest_match = actual_event_digest == checkpoint.event_digest
        snapshot_digest_match = actual_snapshot_digest == checkpoint.snapshot_digest
        full_replay_equivalent = (
            checkpoint_replay_match and checkpoint_plus_subsequent == full_replay
        )
        reasons: list[str] = []
        if not event_digest_match:
            reasons.append("event_digest_mismatch")
        if not snapshot_digest_match:
            reasons.append("snapshot_digest_mismatch")
        if len(prefix) != checkpoint.event_count:
            reasons.append("event_count_mismatch")
        if not replay_capsule_match:
            reasons.append("replay_capsule_mismatch")
        if prefix_replay != checkpoint.snapshot:
            reasons.append("checkpoint_replay_mismatch")
        if not full_replay_equivalent:
            reasons.append("full_replay_mismatch")
        return CheckpointValidation(
            checkpoint_id=checkpoint.checkpoint_id,
            valid=not reasons,
            reasons=tuple(reasons),
            event_digest_match=event_digest_match,
            snapshot_digest_match=snapshot_digest_match,
            checkpoint_replay_match=checkpoint_replay_match,
            full_replay_equivalent=full_replay_equivalent,
            subsequent_event_count=len(subsequent),
        )

    def rebuild_snapshot(
        self,
        *,
        student_id: str,
        session_id: str,
    ) -> ReducerResult:
        normalized_student_id, normalized_session_id = _normalize_identity(
            student_id, session_id
        )
        with self._student_lock(normalized_student_id):
            checkpoint = self.load_latest_checkpoint(
                student_id=normalized_student_id,
                session_id=normalized_session_id,
            )
            if checkpoint is not None:
                validation = self.validate_checkpoint(checkpoint)
                if not validation.valid:
                    raise CheckpointValidationError(
                        "checkpoint validation failed: " + ", ".join(validation.reasons)
                    )
                if validation.subsequent_event_count == 0:
                    return checkpoint.snapshot
                with self._connect() as connection:
                    subsequent = self._scope_records(
                        connection,
                        student_id=normalized_student_id,
                        session_id=normalized_session_id,
                        after_sequence=checkpoint.through_sequence,
                    )
                return self.reducer.reduce(
                    (
                        *checkpoint.replay_events,
                        *(record.event for record in subsequent),
                    ),
                    student_id=normalized_student_id,
                    session_id=normalized_session_id,
                )
            with self._connect() as connection:
                records = self._scope_records(
                    connection,
                    student_id=normalized_student_id,
                    session_id=normalized_session_id,
                )
            return self.reducer.reduce(
                (record.event for record in records),
                student_id=normalized_student_id,
                session_id=normalized_session_id,
            )


__all__ = [
    "REPOSITORY_SCHEMA_VERSION",
    "CheckpointValidationError",
    "StudentEventRepository",
    "StudentRepositoryCorruptionError",
    "StudentRepositoryError",
    "StudentRepositorySchemaError",
]
