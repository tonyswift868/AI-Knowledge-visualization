"""Small account-scoped sync coordinator with a durable CAS ledger."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import json
import hashlib
from pathlib import Path
import sqlite3
from threading import Event, RLock
from typing import Any, Protocol
from uuid import uuid4

from ..plan_models import PlanError
from ..plan_source import validate_snapshot
from ..file_io import _native_path, assert_no_reparse_ancestors, canonical_io_path
from .contract import (
    RefactorRecord,
    SyncRecordError,
    entity_id_for,
    envelope_for,
    is_refactor_entity,
    kind_for,
    parse_envelope,
    parse_entity_identity,
)


class SyncAdapter(Protocol):
    def scan(self) -> dict[tuple[str, str], dict[str, Any]]: ...

    def apply(self, record_type: str, record_id: str, values: dict[str, Any],
              expected_local: dict[str, Any] | None) -> bool: ...

    def preserve_conflict(self, record_type: str, record_id: str,
                          local: dict[str, Any] | None,
                          remote: dict[str, Any] | None) -> None: ...

    def original_bytes(self, material_id: str) -> bytes: ...
    def restore_original_bytes(self, material_id: str, data: bytes) -> None: ...


class SyncFailure(RuntimeError):
    def __init__(self, code: str, message: str = "学习记录同步未完成。"):
        self.code = code
        super().__init__(message)


_HISTORY_BLOB_TYPES = frozenset({"chat_session", "tool_activity", "draft_plan", "research_source",
                                 "turn", "note", "receipt", "evidence", "error", "verification"})
_HISTORY_BLOB_MAX = 8 * 1024 * 1024


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class _Local:
    record: RefactorRecord


@dataclass(frozen=True)
class _Remote:
    entity_id: str
    kind: str
    record_type: str
    record_id: str | None
    revision: int
    deleted: bool
    values: dict[str, Any] | None


class _Ledger:
    def __init__(self, data_root: Path, scope):
        self.scope = scope
        account_root = canonical_io_path(Path(data_root).resolve()) / "learning" / "accounts" / scope.account_id
        assert_no_reparse_ancestors(account_root, Path(account_root.anchor))
        _native_path(account_root).mkdir(parents=True, exist_ok=True)
        self.path = account_root / "sync.sqlite3"
        self._lock = RLock()
        self._initialize()

    @contextmanager
    def _connection(self):
        assert_no_reparse_ancestors(self.path, Path(self.path.anchor))
        if _native_path(self.path).exists() and _native_path(self.path).stat().st_nlink != 1:
            raise SyncFailure("SYNC_PATH_INVALID", "同步记录文件不是独立文件。")
        connection = sqlite3.connect(str(_native_path(self.path)), timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self._lock, self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS owner (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    account_id TEXT NOT NULL, issuer TEXT NOT NULL,
                    subject_id TEXT NOT NULL, organization_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS records (
                    record_type TEXT NOT NULL, record_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
                    server_revision INTEGER NOT NULL DEFAULT 0,
                    base_values TEXT,
                    pending_request_id TEXT,
                    pending_expected_revision INTEGER,
                    pending_payload TEXT,
                    tombstoned INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(record_type, record_id)
                );
                CREATE TABLE IF NOT EXISTS remote_tombstones (
                    entity_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    revision INTEGER NOT NULL
                );
            """)
            owner = connection.execute("SELECT account_id,issuer,subject_id,organization_id,workspace_id FROM owner WHERE singleton=1").fetchone()
            expected = (self.scope.account_id, self.scope.issuer, self.scope.subject_id,
                        self.scope.organization_id, self.scope.workspace_id)
            if owner is None:
                connection.execute("INSERT INTO owner VALUES(1,?,?,?,?,?)", expected)
            elif tuple(owner) != expected:
                raise SyncFailure("SYNC_SCOPE_MISMATCH", "同步记录归属与当前账号空间不一致。")

    @staticmethod
    def _json(value):
        return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _load(value):
        return None if value is None else json.loads(value)

    def all_rows(self):
        with self._lock, self._connection() as connection:
            rows = connection.execute("SELECT * FROM records ORDER BY entity_id").fetchall()
            return [dict(row) for row in rows]

    def row(self, entity_id):
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT * FROM records WHERE entity_id=?", (entity_id,)).fetchone()
            return dict(row) if row else None

    def ensure(self, record: RefactorRecord):
        with self._lock, self._connection() as connection:
            connection.execute("""INSERT OR IGNORE INTO records
                (record_type,record_id,entity_id,kind) VALUES(?,?,?,?)""",
                (record.record_type, record.record_id, record.entity_id, record.kind))

    def set_pending(self, record: RefactorRecord, request_id: str, expected: int):
        payload = self._json(record.payload)
        with self._lock, self._connection() as connection:
            connection.execute("""INSERT INTO records
                (record_type,record_id,entity_id,kind,pending_request_id,
                 pending_expected_revision,pending_payload)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(record_type,record_id) DO UPDATE SET
                 pending_request_id=excluded.pending_request_id,
                 pending_expected_revision=excluded.pending_expected_revision,
                 pending_payload=excluded.pending_payload""",
                (record.record_type, record.record_id, record.entity_id, record.kind,
                 request_id, expected, payload))

    def ack(self, record: RefactorRecord, revision: int):
        values = self._json(record.values)
        with self._lock, self._connection() as connection:
            connection.execute("""INSERT INTO records
                (record_type,record_id,entity_id,kind,server_revision,base_values,
                 pending_request_id,pending_expected_revision,pending_payload,tombstoned)
                VALUES(?,?,?,?,?,?,NULL,NULL,NULL,0)
                ON CONFLICT(record_type,record_id) DO UPDATE SET
                 server_revision=excluded.server_revision,
                 base_values=excluded.base_values,
                 pending_request_id=NULL,pending_expected_revision=NULL,
                 pending_payload=NULL,tombstoned=0""",
                (record.record_type, record.record_id, record.entity_id, record.kind,
                 revision, values))

    def remote_base(self, record_type: str, record_id: str, entity_id: str,
                    kind: str, values: dict[str, Any] | None, revision: int,
                    *, tombstoned: bool = False):
        encoded = self._json(values)
        with self._lock, self._connection() as connection:
            connection.execute("""INSERT INTO records
                (record_type,record_id,entity_id,kind,server_revision,base_values,tombstoned)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(record_type,record_id) DO UPDATE SET
                 server_revision=excluded.server_revision,
                 base_values=excluded.base_values,
                 pending_request_id=NULL,pending_expected_revision=NULL,
                 pending_payload=NULL,tombstoned=excluded.tombstoned""",
                (record_type, record_id, entity_id, kind, revision, encoded, int(tombstoned)))

    def mark_unknown_tombstone(self, entity_id: str, kind: str, revision: int):
        with self._lock, self._connection() as connection:
            connection.execute("""INSERT INTO remote_tombstones(entity_id,kind,revision)
                VALUES(?,?,?) ON CONFLICT(entity_id) DO UPDATE SET
                kind=excluded.kind,revision=MAX(remote_tombstones.revision,excluded.revision)""",
                (entity_id, kind, revision))

    def unknown_tombstone(self, entity_id: str):
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT * FROM remote_tombstones WHERE entity_id=?", (entity_id,)).fetchone()
            return dict(row) if row else None


def _same(left, right):
    return left == right


_MISSING = object()


def _three_way(base, local, remote, path=""):
    if _same(local, base):
        return remote, []
    if _same(remote, base):
        return local, []
    if _same(local, remote):
        return local, []
    if isinstance(base, dict) and isinstance(local, dict) and isinstance(remote, dict):
        merged, conflicts = {}, []
        for key in sorted(set(base) | set(local) | set(remote)):
            b = base.get(key, _MISSING)
            l = local.get(key, _MISSING)
            r = remote.get(key, _MISSING)
            value, nested = _three_way(b, l, r, f"{path}.{key}" if path else key)
            if value is not _MISSING:
                merged[key] = value
            conflicts.extend(nested)
        return merged, conflicts
    return local, [path or "<record>"]


def _new_request_id():
    return "refactor-v1-" + uuid4().hex


class SyncEngine:
    """Synchronize adapter records without making local files cloud-owned."""

    def __init__(self, data_root, scope, transport, adapter):
        self.scope = scope
        self.transport = transport
        self.adapter = adapter
        self.ledger = _Ledger(data_root, scope)
        self._closed = Event()
        self._cancelled = Event()
        self._lock = RLock()
        self._invalid_remote = set()

    def close(self):
        self._closed.set()

    def cancel(self):
        self._cancelled.set()

    def _fence(self):
        if self._closed.is_set() or self._cancelled.is_set():
            raise SyncFailure("CANCELLED", "同步会话已关闭。")
        current = self.transport.require_current()
        if current != self.scope:
            raise SyncFailure("SYNC_SCOPE_CHANGED", "账号或学习空间已变化；本机记录未修改。")
        return current

    @staticmethod
    def _summary(scope):
        return {"uploaded": 0, "downloaded": 0, "replayed": 0,
                "conflicts": 0, "pending": 0, "tombstones": 0,
                "pending_blob": 0, "errors": [], "scope": scope.snapshot_scope(), "ok": True}

    def _scan(self, summary):
        try:
            raw = self.adapter.scan()
            if not isinstance(raw, dict):
                raise SyncRecordError("SYNC_SCAN_INVALID", "本机学习记录扫描结果无效。")
        except SyncRecordError as exc:
            summary["errors"].append(exc.code)
            summary["pending"] += 1
            return {}
        except Exception:
            summary["errors"].append("SYNC_SCAN_FAILED")
            summary["pending"] += 1
            return {}
        result = {}
        for key, values in raw.items():
            try:
                if not isinstance(key, tuple) or len(key) != 2:
                    raise SyncRecordError("SYNC_SCAN_INVALID", "本机学习记录标识无效。")
                record_type, record_id = key
                try:
                    payload = envelope_for(record_type, record_id, values)
                    record = parse_envelope(payload, expected_type=record_type, expected_id=record_id)
                except SyncRecordError as exc:
                    if exc.code != "SYNC_RECORD_TOO_LARGE" or record_type not in _HISTORY_BLOB_TYPES:
                        raise
                    # Keep the full local value available to the adapter while
                    # _send replaces only the wire envelope with a blob manifest.
                    record = RefactorRecord(record_type, record_id, values,
                                            entity_id_for(record_type, record_id),
                                            kind_for(record_type), {
                                                "schema": "mentorflow.refactor-sync.v1",
                                                "version": 1,
                                                "record_type": record_type,
                                                "record_id": record_id,
                                                "values": values,
                                            })
                result[record.entity_id] = _Local(record)
            except SyncRecordError as exc:
                summary["errors"].append(exc.code)
                summary["pending"] += 1
        return result

    def _expand_blob(self, record_type, record_id, values):
        if not isinstance(values, dict) or not isinstance(values.get("blob_ref"), dict):
            return values, None
        ref = values["blob_ref"]
        digest, size = ref.get("sha256"), ref.get("size")
        if (not isinstance(digest, str) or not isinstance(size, int)
                or size < 0 or size > _HISTORY_BLOB_MAX
                or not hasattr(self.transport, "download_history_blob")):
            raise SyncFailure("SYNC_BLOB_PENDING", "同步记录原文尚未准备好。")
        raw = self.transport.download_history_blob(digest, max_bytes=_HISTORY_BLOB_MAX)
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            raise SyncFailure("SYNC_BLOB_INTEGRITY", "同步记录原文完整性校验失败。")
        try:
            envelope = json.loads(raw)
            if (envelope.get("schema") != "mentorflow.refactor-sync.v1"
                    or envelope.get("record_type") != record_type
                    or envelope.get("record_id") != record_id
                    or not isinstance(envelope.get("values"), dict)):
                raise ValueError()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SyncFailure("SYNC_BLOB_INVALID", "同步记录原文格式无效。") from exc
        expanded = envelope["values"]
        return expanded, RefactorRecord(record_type, record_id, expanded,
                                        entity_id_for(record_type, record_id),
                                        kind_for(record_type), envelope)

    def _remote(self, snapshot, summary):
        result = {}
        self._invalid_remote = set()
        for entity in snapshot.get("entities", []):
            entity_id = entity.get("entity_id")
            if not is_refactor_entity(entity_id):
                continue
            try:
                record_type, _ = parse_entity_identity(entity_id)
                expected_kind = kind_for(record_type)
                if entity.get("kind") != expected_kind:
                    raise SyncRecordError("SYNC_ENTITY_INVALID", "云端学习实体类型不一致。")
                if entity.get("deleted"):
                    result[entity_id] = _Remote(entity_id, expected_kind, record_type, None,
                                                entity["revision"], True, None)
                    continue
                wire_payload = entity.get("payload")
                # The server stores strict canonical fields for refactor
                # resources so the regular plan projector can consume the
                # same snapshot. Rebuild the refactor envelope before it
                # reaches the local record adapter.
                if (record_type == "resource" and isinstance(wire_payload, dict)
                        and isinstance(wire_payload.get("refactor_record_id"), str)):
                    record_id = wire_payload["refactor_record_id"]
                    wire_payload = envelope_for("resource", record_id, {
                        "material_id": record_id,
                        "filename": wire_payload.get("filename"),
                        "media_type": wire_payload.get("media_type"),
                        "size": wire_payload.get("size_bytes"),
                        "sha256": wire_payload.get("sha256"),
                        "text": wire_payload.get("text", ""),
                        "product_classification": wire_payload.get("product_classification", "UNKNOWN"),
                    })
                record = parse_envelope(wire_payload, expected_type=record_type)
                if record.entity_id != entity_id:
                    raise SyncRecordError("SYNC_ENVELOPE_INVALID", "云端学习实体标识不一致。")
                remote_values = record.values
                # Oversized history records are represented in the server
                # snapshot by a small manifest.  Expand it before entering
                # reconciliation so the local adapter compares the original
                # values rather than the transport-only blob reference.
                if isinstance(remote_values.get("blob_ref"), dict):
                    try:
                        remote_values, _ = self._expand_blob(
                            record.record_type, record.record_id, remote_values)
                    except (SyncFailure, PlanError, KeyError, TypeError, ValueError) as exc:
                        if isinstance(entity_id, str):
                            self._invalid_remote.add(entity_id)
                        summary["errors"].append(getattr(exc, "code", "SYNC_BLOB_PENDING"))
                        continue
                result[entity_id] = _Remote(entity_id, expected_kind, record_type,
                                            record.record_id, entity["revision"], False, remote_values)
            except (KeyError, TypeError, SyncRecordError):
                summary["errors"].append("SYNC_REMOTE_INVALID")
                if isinstance(entity_id, str):
                    self._invalid_remote.add(entity_id)
        return result

    def _ack_remote(self, remote: _Remote, summary):
        if remote.deleted or remote.record_id is None or remote.values is None:
            return
        try:
            expanded, blob_record = self._expand_blob(remote.record_type, remote.record_id, remote.values)
            record = blob_record or parse_envelope(envelope_for(remote.record_type, remote.record_id, expanded))
            self.ledger.ack(record, remote.revision)
            summary["replayed"] += 1
        except SyncFailure as exc:
            summary["pending"] += 1
            if exc.code == "SYNC_BLOB_PENDING":
                summary["pending_blob"] += 1
            summary["errors"].append(exc.code)

    def _pending_matches(self, row, remote: _Remote | None):
        pending = row.get("pending_payload")
        if not pending or remote is None or remote.deleted or remote.values is None:
            return False
        try:
            record = parse_envelope(json.loads(pending))
        except (ValueError, TypeError, SyncRecordError):
            return False
        return (remote.revision >= int(row["pending_expected_revision"]) + 1
                and remote.values == record.values)

    def _record_from_local(self, local: _Local):
        return local.record

    def _wire_record(self, record: RefactorRecord):
        """Return (small wire record, blob bytes or None) for history records."""
        if record.record_type not in _HISTORY_BLOB_TYPES:
            return record, None
        try:
            raw = _canonical_json_bytes(record.payload)
        except (TypeError, ValueError, OverflowError) as exc:
            raise SyncFailure("SYNC_RECORD_INVALID", "学习记录不能安全序列化。") from exc
        if len(raw) <= 256 * 1024:
            return record, None
        if len(raw) > _HISTORY_BLOB_MAX:
            raise SyncFailure("SYNC_RECORD_TOO_LARGE", "学习记录原文超过允许大小。")
        digest = hashlib.sha256(raw).hexdigest()
        manifest = envelope_for(record.record_type, record.record_id, {
            "blob_ref": {"sha256": digest, "size": len(raw),
                         "content_type": "application/json",
                         "schema": "mentorflow.history-blob.v1"},
            "record_type": record.record_type,
            "record_id": record.record_id,
            "summary": {"bytes": len(raw)},
        })
        return parse_envelope(manifest), raw

    def _send(self, record: RefactorRecord, row, summary):
        pending_payload = row.get("pending_payload")
        pending_request = row.get("pending_request_id")
        pending_expected = row.get("pending_expected_revision")
        from_pending = bool(pending_payload and pending_request and pending_expected is not None)
        blob_data = None
        if from_pending:
            payload = json.loads(pending_payload)
            expected = int(pending_expected)
            request_id = pending_request
            pending_record = parse_envelope(payload)
            # A previous attempt may have written the manifest locally but
            # failed before uploading its blob.  Reconstruct bytes from the
            # current adapter value and retry the object upload first.
            if isinstance(pending_record.values.get("blob_ref"), dict):
                try:
                    current = self.adapter.scan().get((pending_record.record_type, pending_record.record_id))
                    if isinstance(current, dict):
                        full_payload = {
                            "schema": "mentorflow.refactor-sync.v1", "version": 1,
                            "record_type": pending_record.record_type,
                            "record_id": pending_record.record_id, "values": current,
                        }
                        raw = _canonical_json_bytes(full_payload)
                        ref = pending_record.values["blob_ref"]
                        if len(raw) == ref.get("size") and hashlib.sha256(raw).hexdigest() == ref.get("sha256"):
                            blob_data = raw
                except Exception:
                    blob_data = None
        else:
            expected = int(row.get("server_revision") or 0)
            pending_record = record
            request_id = _new_request_id()
            self._fence()
            pending_record, blob_data = self._wire_record(record)
            self.ledger.set_pending(pending_record, request_id, expected)
        if from_pending:
            pending_record, generated_blob = self._wire_record(pending_record)
            if blob_data is None:
                blob_data = generated_blob
        try:
            self._fence()
            if (from_pending and isinstance(pending_record.values.get("blob_ref"), dict)
                    and blob_data is None):
                raise SyncFailure("SYNC_BLOB_UNAVAILABLE", "同步记录原文无法从本机恢复。")
            if blob_data is not None and not hasattr(self.transport, "upload_history_blob"):
                raise SyncFailure("SYNC_BLOB_UNAVAILABLE", "同步记录原文暂时无法上传。")
            if blob_data is not None:
                ref = pending_record.values.get("blob_ref", {})
                self.transport.upload_history_blob(ref["sha256"], blob_data)
            if pending_record.record_type == "resource" and hasattr(self.transport, "upload_original"):
                digest = pending_record.values.get("sha256")
                if isinstance(digest, str):
                    self.transport.upload_original(digest, self.adapter.original_bytes(pending_record.record_id))
            result = self.transport.write(kind_for(pending_record.record_type),
                                          pending_record.entity_id,
                                          request_id=request_id,
                                          expected_revision=expected,
                                          payload=pending_record.payload)
            self._fence()
            response_payload = result.get("payload") if isinstance(result, dict) else None
            if (pending_record.record_type == "resource" and isinstance(response_payload, dict)
                    and isinstance(response_payload.get("refactor_record_id"), str)):
                response_payload = envelope_for("resource", response_payload["refactor_record_id"], {
                    "material_id": response_payload["refactor_record_id"],
                    "filename": response_payload.get("filename"),
                    "media_type": response_payload.get("media_type"),
                    "size": response_payload.get("size_bytes"),
                    "sha256": response_payload.get("sha256"),
                    "text": response_payload.get("text", ""),
                    "product_classification": response_payload.get("product_classification", "UNKNOWN"),
                })
            if (not isinstance(result, dict) or result.get("kind") != pending_record.kind
                    or result.get("entity_id") != pending_record.entity_id
                    or type(result.get("revision")) is not int
                    or result["revision"] != expected + 1
                    or result.get("deleted") is not False
                    or response_payload != pending_record.payload):
                raise SyncFailure("SYNC_RESPONSE_INVALID", "同步回执与本机记录不一致。")
            # Keep the full local values as the ledger base after a blob
            # retry.  A manifest is only the wire representation; recording
            # it as the base makes the final scan look like a local edit and
            # schedules an unnecessary extra upload on the next pass.
            ack_record = pending_record
            if from_pending and blob_data is not None:
                try:
                    full_envelope = json.loads(blob_data)
                    # parse_envelope intentionally rejects payloads above the
                    # wire limit; blob content is allowed to exceed that
                    # limit, so rebuild the local record directly after the
                    # digest and envelope checks performed during upload.
                    if (isinstance(full_envelope, dict)
                            and full_envelope.get("record_type") == pending_record.record_type
                            and full_envelope.get("record_id") == pending_record.record_id
                            and isinstance(full_envelope.get("values"), dict)):
                        ack_record = RefactorRecord(
                            pending_record.record_type, pending_record.record_id,
                            full_envelope["values"], pending_record.entity_id,
                            pending_record.kind, full_envelope)
                except (TypeError, ValueError, json.JSONDecodeError):
                    ack_record = pending_record
            self.ledger.ack(record if not from_pending else ack_record, result["revision"])
            summary["uploaded"] += 1
            return True
        except (SyncFailure, PlanError) as exc:
            summary["pending"] += 1
            summary["errors"].append(getattr(exc, "code", "SYNC_UNAVAILABLE"))
            return False
        except Exception:
            summary["pending"] += 1
            summary["errors"].append("SYNC_UNAVAILABLE")
            return False

    def _confirm_applied(self, record_type, record_id, values, summary):
        """Confirm the adapter exposed the value it just accepted.

        Adapters may stage a complete plan before publishing it.  Their scan
        contract therefore includes those staged records; this check keeps
        the ledger from acknowledging a half-applied or merely queued value.
        """
        try:
            self._fence()
            scanned = self.adapter.scan()
            self._fence()
            actual = scanned.get((record_type, record_id)) if isinstance(scanned, dict) else None
            if actual != values:
                summary["pending"] += 1
                summary["errors"].append("SYNC_APPLY_NOT_VISIBLE")
                return False
            return True
        except Exception:
            summary["pending"] += 1
            summary["errors"].append("SYNC_APPLY_SCAN_FAILED")
            return False

    def _apply_remote(self, remote: _Remote, local: _Local | None,
                      summary, *, conflict=False):
        if remote.deleted or remote.values is None or remote.record_id is None:
            return False
        expected = local.record.values if local is not None else None
        try:
            expanded_values, blob_record = self._expand_blob(remote.record_type, remote.record_id, remote.values)
            self._fence()
            applied = bool(self.adapter.apply(remote.record_type, remote.record_id,
                                              expanded_values, expected))
            self._fence()
            # Metadata must exist before a restored object can be published:
            # MaterialStore validates the downloaded bytes against that row.
            # If the object request fails, the metadata remains visible as a
            # pending resource and the next sync retries it; the ledger is
            # deliberately not ACKed below.
            if applied and remote.record_type == "resource" and hasattr(self.transport, "download_original"):
                digest = remote.values.get("sha256") if isinstance(remote.values, dict) else None
                if isinstance(digest, str):
                    try:
                        self.adapter.original_bytes(remote.record_id)
                    except Exception:
                        self.adapter.restore_original_bytes(
                            remote.record_id, self.transport.download_original(digest))
            self._fence()
        except SyncFailure as exc:
            summary["pending"] += 1
            if exc.code == "SYNC_BLOB_PENDING":
                summary["pending_blob"] += 1
            summary["errors"].append(exc.code)
            return False
        except Exception:
            summary["pending"] += 1
            summary["errors"].append("SYNC_APPLY_FAILED")
            return False
        if not applied:
            summary["pending"] += 1
            summary["errors"].append("SYNC_LOCAL_CHANGED")
            return False
        if not self._confirm_applied(remote.record_type, remote.record_id,
                                     expanded_values, summary):
            return False
        record = blob_record or parse_envelope(envelope_for(remote.record_type, remote.record_id, expanded_values))
        self.ledger.ack(record, remote.revision)
        summary["downloaded"] += 1
        return True

    def _conflict(self, record_type, record_id, local, remote, summary):
        try:
            self._fence()
            self.adapter.preserve_conflict(record_type, record_id, local, remote)
            self._fence()
            summary["conflicts"] += 1
            return True
        except Exception:
            summary["pending"] += 1
            summary["errors"].append("SYNC_CONFLICT_PRESERVE_FAILED")
            return False

    def _reconcile(self, entity_id, local, remote, summary):
        if entity_id in self._invalid_remote:
            summary["pending"] += 1
            return
        row = self.ledger.row(entity_id)
        if local is not None:
            self.ledger.ensure(local.record)
            row = self.ledger.row(entity_id)
        if row is None and local is None and remote is not None and remote.record_id is not None:
            self.ledger.remote_base(remote.record_type, remote.record_id, entity_id,
                                    remote.kind, remote.values, remote.revision,
                                    tombstoned=remote.deleted)
            row = self.ledger.row(entity_id)
        if remote is not None and remote.deleted:
            summary["tombstones"] += 1
            if row is None:
                if local is not None:
                    self._conflict(local.record.record_type, local.record.record_id,
                                   local.record.values, None, summary)
                    self.ledger.remote_base(local.record.record_type, local.record.record_id,
                                            entity_id, remote.kind, None,
                                            remote.revision, tombstoned=True)
                else:
                    self.ledger.mark_unknown_tombstone(entity_id, remote.kind, remote.revision)
                return
            if int(row.get("tombstoned") or 0):
                return
            base = _Ledger._load(row.get("base_values"))
            pending = _Ledger._load(row.get("pending_payload"))
            if local is not None and (base is None or local.record.values != base):
                if not self._conflict(local.record.record_type, local.record.record_id,
                                      local.record.values, None, summary):
                    return
            with self.ledger._lock, self.ledger._connection() as connection:
                connection.execute("""UPDATE records SET server_revision=?,
                    pending_request_id=NULL,pending_expected_revision=NULL,
                    pending_payload=NULL,tombstoned=1 WHERE entity_id=?""",
                    (remote.revision, entity_id))
            return
        if row is not None and int(row.get("tombstoned") or 0):
            # A structured-sync tombstone is terminal for this entity ID.
            summary["errors"].append("SYNC_TOMBSTONED")
            summary["pending"] += 1
            return
        if remote is not None and self._pending_matches(row or {}, remote):
            self._ack_remote(remote, summary)
            row = self.ledger.row(entity_id)
        if row is not None and row.get("pending_request_id"):
            expected = int(row.get("pending_expected_revision") or 0)
            if remote is not None and remote.revision > expected and not self._pending_matches(row, remote):
                pending = _Ledger._load(row.get("pending_payload"))
                parsed = parse_envelope(pending) if pending else None
                if parsed:
                    if not self._conflict(parsed.record_type, parsed.record_id,
                                          parsed.values, remote.values, summary):
                        return
                if remote.record_id is not None and remote.values is not None:
                    self.ledger.remote_base(remote.record_type, remote.record_id,
                                            entity_id, remote.kind, remote.values,
                                            remote.revision)
                return
            pending_record = parse_envelope(json.loads(row["pending_payload"]))
            self._send(pending_record, row, summary)
            return
        if local is None and remote is None:
            return
        if local is None and remote is not None:
            self._apply_remote(remote, None, summary)
            return
        if local is not None and remote is None:
            base = _Ledger._load(row.get("base_values")) if row else None
            revision = int(row.get("server_revision") or 0) if row else 0
            if base is None and revision == 0:
                self._send(local.record, row or {"server_revision": 0}, summary)
            else:
                summary["pending"] += 1
                summary["errors"].append("SYNC_REMOTE_MISSING")
            return
        assert local is not None and remote is not None and remote.values is not None
        base = _Ledger._load(row.get("base_values")) if row else None
        if local.record.values == remote.values:
            if local.record.record_type == "resource" and hasattr(self.transport, "download_original"):
                try:
                    self.adapter.original_bytes(local.record.record_id)
                except Exception:
                    try:
                        digest = remote.values.get("sha256")
                        if not isinstance(digest, str):
                            raise ValueError("resource digest missing")
                        self.adapter.restore_original_bytes(
                            local.record.record_id, self.transport.download_original(digest))
                    except Exception:
                        summary["pending"] += 1
                        summary["errors"].append("SYNC_OBJECT_UNAVAILABLE")
                        return
            self.ledger.ack(local.record, remote.revision)
            return
        if base is None:
            if not self._conflict(local.record.record_type, local.record.record_id,
                                  local.record.values, remote.values, summary):
                return
            self._apply_remote(remote, local, summary, conflict=True)
            return
        merged, conflicts = _three_way(base, local.record.values, remote.values)
        if conflicts:
            if not self._conflict(local.record.record_type, local.record.record_id,
                                  local.record.values, remote.values, summary):
                return
            self._apply_remote(remote, local, summary, conflict=True)
            return
        if merged == remote.values:
            self._apply_remote(remote, local, summary)
            return
        merged_record = parse_envelope(envelope_for(local.record.record_type,
                                                     local.record.record_id, merged))
        if not self._send(merged_record, {"server_revision": remote.revision}, summary):
            return
        # The server now owns the merged value; apply it only if this exact
        # local value is still present.  A concurrent edit remains for the
        # next scan instead of being silently overwritten.
        if merged != local.record.values:
            applied = self._apply_remote(_Remote(remote.entity_id, remote.kind,
                                       merged_record.record_type,
                                       merged_record.record_id,
                                       remote.revision + 1, False, merged),
                              local, summary)
            if not applied:
                # The local editor never saw the merge. Retain its previous
                # last value actually seen by the local editor as its base;
                # otherwise a concurrent edit could erase the remote fields
                # merely because they were never applied on this device.
                self.ledger.remote_base(merged_record.record_type,
                    merged_record.record_id, remote.entity_id, remote.kind,
                    local.record.values, remote.revision + 1)

    def sync(self):
        with self._lock:
            summary = self._summary(self.scope)
            try:
                self._fence()
                local = self._scan(summary)
                self._fence()
                snapshot = self.transport.read_snapshot()
                self._fence()
                validate_snapshot(snapshot, self.scope)
                remote = self._remote(snapshot, summary)
                ledger_rows = self.ledger.all_rows()
                by_entity = {row["entity_id"]: row for row in ledger_rows}
                keys = set(local) | set(remote) | set(by_entity)
                # Hydrate the complete plan/task dependency before its notes
                # and progress. Incomplete plans stay staged for a later pull.
                # Resource metadata must arrive before a plan that references
                # it.  The account object is downloaded after this metadata
                # row is applied, so MaterialStore can verify its size/hash
                # before publishing the restored original locally.
                order = {"resource": 0, "plan": 1, "task": 2, "progress": 3,
                         "plan_revision": 4, "learning_contract": 5,
                         "daily_mission": 6, "draft_plan": 7,
                         "setting": 8, "tool_activity": 9,
                         "chat_session": 10, "research_source": 11,
                         "turn": 12, "note": 13, "receipt": 14}
                for entity_id in sorted(keys, key=lambda eid: (order.get(parse_entity_identity(eid)[0], 9), eid)):
                    if self._closed.is_set() or self._cancelled.is_set():
                        summary["pending"] += 1
                        break
                    self._reconcile(entity_id, local.get(entity_id), remote.get(entity_id), summary)
                # Refresh the adapter-visible state after all remote applies;
                # staged records remain visible through the same scan contract.
                self._fence()
                current = self._scan(summary)
                acknowledged = {row["entity_id"]: row for row in self.ledger.all_rows()}
                for entity_id, item in current.items():
                    row = acknowledged.get(entity_id)
                    if row and row.get("tombstoned"):
                        continue
                    if (not row or row.get("pending_request_id")
                            or _Ledger._load(row.get("base_values")) != item.record.values):
                        # A local edit arriving during upload has not yet
                        # reached the server. Keep automatic retry armed.
                        summary["pending"] += 1
                self._fence()
            except (SyncFailure, PlanError) as exc:
                summary["pending"] += 1
                summary["errors"].append(getattr(exc, "code", "SYNC_UNAVAILABLE"))
            except Exception:
                summary["pending"] += 1
                summary["errors"].append("SYNC_UNAVAILABLE")
            summary["ok"] = not summary["errors"] and not summary["conflicts"] and not summary["pending"]
            return summary
