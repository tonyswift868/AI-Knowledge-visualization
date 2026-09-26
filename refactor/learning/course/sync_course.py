"""Course RecordStore adapter for the deployed structured-sync protocol.

Run sync() on a worker thread. Only one round per instance runs at a time.
Cancellation fences local completion; an in-flight HTTP request may finish on
the server, and its deterministic request ID makes the next round safe.

The current API has a complete, authenticated MVCC snapshot, not a delta feed.
The opaque RecordStore cursor retains per-entity revisions and tombstone
identities, so apply_remote receives only changes. Wire reads are still full
snapshots. No separate database, queue, or domain merge policy lives here.
RecordStore must preserve edits newer than mark_synced's local_revision and
make repeated apply_remote calls safe (including after a cursor-save failure).
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from threading import Event, Lock
from typing import TYPE_CHECKING

from ..plan_models import PlanError
from ..plan_source import validate_snapshot

if TYPE_CHECKING:
    from .sync_contract import RecordStore, SyncRecord

PREFIX = "refactor.v1:course_record:"
CURSOR_SCHEMA = "mentorflow.course-sync-cursor.v1"
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}\Z")


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _invalid():
    return PlanError("SYNC_COURSE_INVALID", "课程同步记录无法验证，本机记录仍保留。")


def _values(record):
    values = {name: getattr(record, name) for name in ("schema", "plan_key", "entity", "entity_id", "payload")}
    _validate(values)
    if type(record.deleted) is not bool or type(record.local_revision) is not int or record.local_revision < 0:
        raise _invalid()
    return deepcopy(values)


def _validate(values):
    if not isinstance(values, dict) or set(values) != {"schema", "plan_key", "entity", "entity_id", "payload"}:
        raise _invalid()
    schema = values["schema"]
    match = re.fullmatch(r"mentorflow\.course\.([a-z_]{1,40})\.v[0-9]{1,3}", schema) if isinstance(schema, str) else None
    if (not match or values["entity"] != match[1] or not isinstance(values["entity_id"], str)
            or not ID.fullmatch(values["entity_id"]) or not isinstance(values["plan_key"], str)
            or values["plan_key"] != "" and not ID.fullmatch(values["plan_key"])
            or not isinstance(values["payload"], dict)):
        raise _invalid()
    stack = [(values["payload"], 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, (dict, list)):
            if depth > 32 or isinstance(item, dict) and any(not isinstance(k, str) for k in item):
                raise _invalid()
            stack.extend((v, depth + 1) for v in (item.values() if isinstance(item, dict) else item))
        elif item is not None and type(item) not in (str, int, float, bool):
            raise _invalid()
    try:
        if len(_json(values["payload"]).encode("utf-8")) > 256 * 1024:
            raise PlanError("SYNC_RECORD_TOO_LARGE", "单条课程记录超过同步容量，本机记录仍保留。")
    except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
        raise _invalid() from exc


def _record_id(values):
    # RecordStore keys are (entity, entity_id), including account-level data.
    return _json([values["entity"], values["entity_id"]])


def _wire_id(values):
    return PREFIX + hashlib.sha256(_record_id(values).encode("utf-8")).hexdigest()


def _envelope(values):
    return {"schema": "mentorflow.refactor-sync.v1", "version": 1,
            "record_type": "course_record", "record_id": _record_id(values), "values": values}


def _metadata(values, revision, deleted):
    return {**{k: values[k] for k in ("schema", "plan_key", "entity", "entity_id")},
            "revision": revision, "deleted": deleted}


def _record(values, deleted):
    # MF-03 owns this type. Do not ship a second production definition or store.
    from .sync_contract import SyncRecord
    return SyncRecord(**deepcopy(values), deleted=deleted, local_revision=0)


class CourseSync:
    def __init__(self, record_store: RecordStore, transport):
        self.store, self.transport = record_store, transport
        self._lock, self._cancel, self._closed = Lock(), Event(), Event()
        self._scope = None

    def cancel(self):
        self._cancel.set()

    def close(self):
        self._closed.set()
        self.cancel()
        # The injected transport/session belongs to the caller.

    def _fence(self, cancel):
        if self._closed.is_set() or self._cancel.is_set() or cancel is not None and cancel.is_set():
            raise PlanError("CANCELLED", "课程同步已取消，本机记录仍保留。")
        scope = self.transport.require_current()
        if self._scope is None:
            self._scope = scope
        if scope != self._scope:
            raise PlanError("SESSION_UNAVAILABLE", "账号或空间已切换，课程同步已停止。")

    def _cursor(self):
        raw = self.store.sync_cursor()
        if raw == "":
            return {"schema": CURSOR_SCHEMA, "scope": self._scope.snapshot_scope(), "records": {}}
        try:
            cursor = json.loads(raw)
            if (set(cursor) != {"schema", "scope", "records"} or cursor["schema"] != CURSOR_SCHEMA
                    or cursor["scope"] != self._scope.snapshot_scope() or not isinstance(cursor["records"], dict)):
                raise ValueError()
            for wire_id, meta in cursor["records"].items():
                if (not re.fullmatch(re.escape(PREFIX) + r"[0-9a-f]{64}", wire_id)
                        or type(meta["revision"]) is not int or meta["revision"] < 1
                        or type(meta["deleted"]) is not bool):
                    raise ValueError()
                if "entity" in meta:
                    values = {k: meta[k] for k in ("schema", "plan_key", "entity", "entity_id")}
                    _validate(dict(values, payload={}))
                    if _wire_id(values) != wire_id:
                        raise ValueError()
                elif set(meta) != {"revision", "deleted"} or not meta["deleted"]:
                    raise ValueError()
            return cursor
        except (ValueError, TypeError, AttributeError, KeyError):
            raise PlanError("SYNC_CURSOR_INVALID", "课程同步游标无效，未跳过待同步记录。") from None

    def _save(self, cursor, cancel):
        self._fence(cancel)
        self.store.set_sync_cursor(_json(cursor))

    def _pull(self, cursor, pending, cancel):
        self._fence(cancel)
        snapshot = self.transport.read_snapshot()
        self._fence(cancel)
        validate_snapshot(snapshot, self._scope)
        local = {_wire_id(_values(r)): deepcopy(r) for r in pending}
        next_cursor = deepcopy(cursor)
        changes, acknowledgements = [], []
        for row in snapshot["entities"]:
            self._fence(cancel)
            wire_id = row["entity_id"]
            payload = row["payload"]
            if not wire_id.startswith(PREFIX):
                if payload.get("record_type") == "course_record":
                    raise _invalid()
                continue
            if row["kind"] != "learning_run" or not re.fullmatch(re.escape(PREFIX) + r"[0-9a-f]{64}", wire_id):
                raise _invalid()
            previous = cursor["records"].get(wire_id, {})
            revision, deleted = row["revision"], row["deleted"]
            if revision < previous.get("revision", 0):
                raise PlanError("SYNC_REVISION_REGRESSED", "云端课程修订倒退，已停止同步。")
            values = None
            if deleted:
                if payload != {}:
                    raise _invalid()
                identity = previous if "entity" in previous else None
                if identity is None and wire_id in local:
                    identity = _values(local[wire_id])
                if identity is not None:
                    values = {k: identity[k] for k in ("schema", "plan_key", "entity", "entity_id")}
                    values["payload"] = {}
            else:
                if (set(payload) != {"schema", "version", "record_type", "record_id", "values"}
                        or payload["schema"] != "mentorflow.refactor-sync.v1"
                        or type(payload["version"]) is not int or payload["version"] != 1
                        or payload["record_type"] != "course_record"):
                    raise _invalid()
                values = payload["values"]
                _validate(values)
                if _wire_id(values) != wire_id or _record_id(values) != payload["record_id"]:
                    raise _invalid()
            next_cursor["records"][wire_id] = (_metadata(values, revision, deleted) if values is not None
                                                else {"revision": revision, "deleted": True})
            if values is not None and (revision != previous.get("revision") or "entity" not in previous):
                changes.append((_record(values, deleted), revision))
            candidate = local.get(wire_id)
            if candidate is not None and candidate.deleted == deleted and (deleted or _values(candidate) == values):
                acknowledgements.append((candidate, revision))
        self._fence(cancel)
        for record, revision in acknowledgements:
            self.store.mark_synced(record.entity, record.entity_id, record.local_revision, revision)
        if changes:
            self.store.apply_remote(changes)
        self._save(next_cursor, cancel)
        return next_cursor, len(changes)

    def _send(self, record, cursor, cancel, *, deleting):
        values = _values(record)
        wire_id = _wire_id(values)
        expected = cursor["records"].get(wire_id, {}).get("revision", 0)
        payload = _envelope(values)
        # Survives process restart without extending the RecordStore protocol.
        fingerprint = [self._scope.snapshot_scope(), wire_id, record.local_revision, expected, deleting, payload]
        request_id = "course-" + hashlib.sha256(_json(fingerprint).encode("utf-8")).hexdigest()
        self._fence(cancel)
        args = dict(request_id=request_id, expected_revision=expected)
        result = (self.transport.delete("learning_run", wire_id, **args) if deleting
                  else self.transport.write("learning_run", wire_id, payload=payload, **args))
        self._fence(cancel)
        if (result.get("kind") != "learning_run" or result.get("entity_id") != wire_id
                or type(result.get("revision")) is not int or result["revision"] != expected + 1
                or result.get("deleted") is not deleting or result.get("payload") != ({} if deleting else payload)):
            raise PlanError("SYNC_RESPONSE_INVALID", "课程同步回执不匹配，未清除待同步记录。")
        if deleting == record.deleted:
            self.store.mark_synced(record.entity, record.entity_id, record.local_revision, result["revision"])
        cursor["records"][wire_id] = _metadata(values, result["revision"], deleting)
        self._save(cursor, cancel)

    def sync(self, *, limit=100, cancel=None):
        """One bounded pull/upload round; errors leave the store retryable."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if not self._lock.acquire(blocking=False):
            raise PlanError("SYNC_IN_PROGRESS", "课程同步正在进行。")
        try:
            self._cancel.clear()
            self._fence(cancel)
            cursor, pulled = self._pull(self._cursor(), self.store.pending_records(limit), cancel)
            summary = dict(uploaded=0, pulled=pulled, conflicts=0)
            pending = deepcopy(self.store.pending_records(limit))
            for record in pending:
                for attempt in range(2):
                    self._fence(cancel)
                    try:
                        if record.deleted and _wire_id(_values(record)) not in cursor["records"]:
                            # The existing delete endpoint requires an entity. A
                            # locally created then deleted row still needs a tombstone.
                            self._send(record, cursor, cancel, deleting=False)
                        self._send(record, cursor, cancel, deleting=record.deleted)
                        summary["uploaded"] += 1
                        break
                    except PlanError as exc:
                        if exc.code not in {"SYNC_CONFLICT", "SYNC_TOMBSTONED"}:
                            raise
                        summary["conflicts"] += 1
                        cursor, count = self._pull(cursor, self.store.pending_records(limit), cancel)
                        summary["pulled"] += count
                        # RecordStore alone decides whether the local edit remains
                        # pending after remote application. Never resend the stale copy.
                        record = next((deepcopy(r) for r in self.store.pending_records(limit)
                                       if (r.entity, r.entity_id) == (record.entity, record.entity_id)), None)
                        if record is None:
                            break
                        if attempt or exc.code == "SYNC_TOMBSTONED":
                            raise
            self._fence(cancel)
            summary["pending"] = len(self.store.pending_records(limit))
            summary["ok"] = summary["pending"] == 0
            return summary
        finally:
            self._lock.release()
