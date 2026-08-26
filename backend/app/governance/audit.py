from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.enterprise.contracts_v1 import (
    AccessDecisionKind,
    ActorContext,
    AuditEnvelope,
    AuditMetadataEntry,
)
from app.security_redaction import redact


AUDIT_STORAGE_SCHEMA = "mentorflow.enterprise.audit-chain.v1"
GENESIS_HASH = "0" * 64


def _required(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("audit timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class AuditEventAction(str, Enum):
    LOGIN = "login"
    INVITE = "membership.invite"
    DISABLE = "membership.disable"
    REVOKE = "credential.revoke"
    ROLE_CHANGE = "membership.role_change"
    UPLOAD = "document.upload"
    DELETE = "document.delete"
    PLAN_PUBLISH = "plan.publish"
    PROVIDER_CONFIG = "provider_config.change"
    EXPORT = "export.create"
    ACCESS_DENIED = "access.denied"


@dataclass(frozen=True, slots=True)
class GovernanceAuditEvent:
    """Domain event adapter serialized only as the frozen AuditEnvelope."""

    actor: ActorContext
    action: AuditEventAction
    resource_type: str
    resource_id: str
    outcome: AccessDecisionKind
    reason_code: str
    trace_id: str
    occurred_at: datetime
    metadata_redacted: tuple[AuditMetadataEntry, ...] = field(default_factory=tuple)
    event_id: str = field(default_factory=lambda: "audit_" + uuid4().hex)

    def __post_init__(self) -> None:
        for name in ("resource_type", "resource_id", "reason_code", "trace_id", "event_id"):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at))
        object.__setattr__(self, "metadata_redacted", tuple(self.metadata_redacted))

    def to_envelope(self) -> AuditEnvelope:
        return AuditEnvelope(
            event_id=self.event_id,
            actor_context=self.actor,
            action=self.action.value,
            resource_type=self.resource_type,
            resource_id=self.resource_id,
            outcome=self.outcome.value,
            reason_code=self.reason_code,
            occurred_at=self.occurred_at,
            request_id=self.actor.request_id,
            trace_id=self.trace_id,
            metadata_redacted=self.metadata_redacted,
        )


@dataclass(frozen=True, slots=True)
class AuditRecord:
    sequence: int
    previous_hash: str
    entry_hash: str
    envelope: AuditEnvelope

    @property
    def event_id(self) -> str:
        return self.envelope.event_id

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "previous_hash": self.previous_hash,
            "entry_hash": self.entry_hash,
            "envelope": self.envelope.model_dump(mode="json"),
        }


@dataclass(frozen=True, slots=True)
class AuditIntegrityReport:
    valid: bool
    entries_checked: int
    final_hash: str
    first_invalid_sequence: int | None = None
    expected_entries: int | None = None


class AuditIntegrityError(RuntimeError):
    pass


class SQLiteAuditLog:
    """Append-only hash chain storing only the canonical AuditEnvelope contract."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.executescript(
                f"""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS enterprise_audit_log (
                    sequence INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    envelope_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL UNIQUE
                );
                CREATE TRIGGER IF NOT EXISTS enterprise_audit_no_update
                BEFORE UPDATE ON enterprise_audit_log
                BEGIN
                    SELECT RAISE(ABORT, 'enterprise audit log is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS enterprise_audit_no_delete
                BEFORE DELETE ON enterprise_audit_log
                BEGIN
                    SELECT RAISE(ABORT, 'enterprise audit log is append-only');
                END;
                CREATE TABLE IF NOT EXISTS enterprise_audit_chain_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    entry_count INTEGER NOT NULL CHECK (entry_count >= 0),
                    final_hash TEXT NOT NULL
                );
                INSERT OR IGNORE INTO enterprise_audit_chain_state
                    (singleton, entry_count, final_hash)
                SELECT
                    1,
                    COUNT(*),
                    COALESCE(
                        (SELECT entry_hash FROM enterprise_audit_log
                         ORDER BY sequence DESC LIMIT 1),
                        '{GENESIS_HASH}'
                    )
                FROM enterprise_audit_log;
                """
            )

    @staticmethod
    def _canonical_envelope(envelope: AuditEnvelope) -> tuple[dict[str, Any], str]:
        payload = redact(envelope.model_dump(mode="json"))
        payload["storage_schema"] = AUDIT_STORAGE_SCHEMA
        payload_json = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return payload, payload_json

    @staticmethod
    def _entry_hash(
        *,
        sequence: int,
        previous_hash: str,
        envelope_json: str,
    ) -> str:
        material = f"{sequence}\n{previous_hash}\n{envelope_json}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def append(self, envelope: AuditEnvelope) -> AuditRecord:
        payload, envelope_json = self._canonical_envelope(envelope)
        sanitized = AuditEnvelope.model_validate(
            {key: value for key, value in payload.items() if key != "storage_schema"}
        )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                last = self._connection.execute(
                    "SELECT sequence, entry_hash FROM enterprise_audit_log "
                    "ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                state = self._connection.execute(
                    "SELECT entry_count, final_hash FROM enterprise_audit_chain_state "
                    "WHERE singleton = 1"
                ).fetchone()
                actual_count = 0 if last is None else int(last["sequence"])
                actual_hash = GENESIS_HASH if last is None else str(last["entry_hash"])
                if (
                    state is None
                    or int(state["entry_count"]) != actual_count
                    or str(state["final_hash"]) != actual_hash
                ):
                    raise AuditIntegrityError("AUDIT_CHAIN_STATE_MISMATCH")
                sequence = 1 if last is None else int(last["sequence"]) + 1
                previous_hash = GENESIS_HASH if last is None else str(last["entry_hash"])
                entry_hash = self._entry_hash(
                    sequence=sequence,
                    previous_hash=previous_hash,
                    envelope_json=envelope_json,
                )
                self._connection.execute(
                    "INSERT INTO enterprise_audit_log "
                    "(sequence, event_id, envelope_json, previous_hash, entry_hash) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        sequence,
                        sanitized.event_id,
                        envelope_json,
                        previous_hash,
                        entry_hash,
                    ),
                )
                self._connection.execute(
                    "UPDATE enterprise_audit_chain_state "
                    "SET entry_count = ?, final_hash = ? WHERE singleton = 1",
                    (sequence, entry_hash),
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        return AuditRecord(
            sequence=sequence,
            previous_hash=previous_hash,
            entry_hash=entry_hash,
            envelope=sanitized,
        )

    def record(self, event: GovernanceAuditEvent) -> AuditRecord:
        return self.append(event.to_envelope())

    def records(self, *, after_sequence: int = 0) -> tuple[AuditRecord, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT sequence, envelope_json, previous_hash, entry_hash "
                "FROM enterprise_audit_log WHERE sequence > ? ORDER BY sequence",
                (after_sequence,),
            ).fetchall()
        records: list[AuditRecord] = []
        for row in rows:
            stored = json.loads(str(row["envelope_json"]))
            stored.pop("storage_schema", None)
            records.append(
                AuditRecord(
                    sequence=int(row["sequence"]),
                    previous_hash=str(row["previous_hash"]),
                    entry_hash=str(row["entry_hash"]),
                    envelope=AuditEnvelope.model_validate(stored),
                )
            )
        return tuple(records)

    def verify_integrity(self) -> AuditIntegrityReport:
        with self._lock:
            rows = self._connection.execute(
                "SELECT sequence, envelope_json, previous_hash, entry_hash "
                "FROM enterprise_audit_log ORDER BY sequence"
            ).fetchall()
            state = self._connection.execute(
                "SELECT entry_count, final_hash FROM enterprise_audit_chain_state "
                "WHERE singleton = 1"
            ).fetchone()
        previous_hash = GENESIS_HASH
        for expected_sequence, row in enumerate(rows, start=1):
            sequence = int(row["sequence"])
            stored_previous = str(row["previous_hash"])
            expected_hash = self._entry_hash(
                sequence=expected_sequence,
                previous_hash=previous_hash,
                envelope_json=str(row["envelope_json"]),
            )
            if (
                sequence != expected_sequence
                or stored_previous != previous_hash
                or str(row["entry_hash"]) != expected_hash
            ):
                return AuditIntegrityReport(
                    valid=False,
                    entries_checked=expected_sequence - 1,
                    final_hash=previous_hash,
                    first_invalid_sequence=sequence,
                    expected_entries=(int(state["entry_count"]) if state is not None else None),
                )
            previous_hash = expected_hash
        expected_entries = int(state["entry_count"]) if state is not None else None
        expected_hash = str(state["final_hash"]) if state is not None else ""
        if (
            state is None
            or expected_entries != len(rows)
            or expected_hash != previous_hash
        ):
            return AuditIntegrityReport(
                valid=False,
                entries_checked=len(rows),
                final_hash=previous_hash,
                first_invalid_sequence=len(rows) + 1,
                expected_entries=expected_entries,
            )
        return AuditIntegrityReport(
            valid=True,
            entries_checked=len(rows),
            final_hash=previous_hash,
            expected_entries=expected_entries,
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "SQLiteAuditLog":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


__all__ = [
    "AuditIntegrityReport",
    "AuditIntegrityError",
    "AuditEventAction",
    "AuditRecord",
    "GENESIS_HASH",
    "GovernanceAuditEvent",
    "SQLiteAuditLog",
]
