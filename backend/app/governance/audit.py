from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.enterprise.contracts_v1 import AuditEnvelope
from app.security_redaction import redact


AUDIT_STORAGE_SCHEMA = "mentorflow.enterprise.audit-chain.v1"
GENESIS_HASH = "0" * 64


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
                """
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
                )
            previous_hash = expected_hash
        return AuditIntegrityReport(
            valid=True,
            entries_checked=len(rows),
            final_hash=previous_hash,
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
    "AuditRecord",
    "GENESIS_HASH",
    "SQLiteAuditLog",
]
