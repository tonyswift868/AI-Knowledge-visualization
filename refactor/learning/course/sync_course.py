"""Course RecordStore adapter for the deployed structured-sync protocol.

Run sync() on a worker thread. Only one round per instance runs at a time.
Cancellation fences local completion; an in-flight HTTP request may finish on
the server, and its deterministic request ID makes the next round safe.

Use the incremental feed when available; a 404 falls back to the existing
authenticated MVCC snapshot. The opaque cursor retains the server sequence,
per-entity revisions and tombstone identities. No separate database, queue,
or domain merge policy lives here.
RecordStore must preserve edits newer than mark_synced's local_revision and
make repeated apply_remote calls safe (including after a cursor-save failure).
"""
from __future__ import annotations

from copy import deepcopy
from collections import deque
import hashlib
import json
import re
import time
from threading import Event, Lock
from typing import TYPE_CHECKING

from refactor.learning.sync_identity import PlanError
from refactor.learning.sync_source import validate_snapshot
from refactor.runtime_logging import event
from .store import PROGRESS

if TYPE_CHECKING:
    from .sync_contract import RecordStore, SyncRecord

PREFIX = "refactor.v1:course_record:"
CURSOR_SCHEMA = "mentorflow.course-sync-cursor.v1"
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}\Z")
# The deployed changes endpoint accepts at most 500 records per page.  Keeping
# this value here makes the client use that limit for both pulls and the local
# outbox round. HTTP uploads are smaller, bounded by count and encoded bytes.
DEFAULT_BATCH_SIZE = 500
MAX_BATCH_SIZE = 1000
UPLOAD_BATCH_SIZE = 100
UPLOAD_BATCH_BYTES = 2 * 1024 * 1024


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
            "revision": revision, "deleted": deleted,
            **({"payload_id": values["payload"]["id"]} if "id" in values["payload"] else {})}


def _deleted_payload(identity):
    # CourseStore needs the business ID as well as the scoped wire ID. Cursors
    # from before this release can recover it from MF-03's stable ID encoding.
    identifier = identity.get("payload_id", identity.get("payload", {}).get("id"))
    if identifier is None:
        eid, plan = identity["entity_id"], identity["plan_key"]
        prefix = f"{len(plan)}:{plan}"
        if identity["entity"] == "plans":
            identifier = eid
        elif eid.startswith(prefix):
            identifier = eid[len(prefix):]
    return {"id": identifier} if identifier is not None else {}


def _record(values, deleted):
    # MF-03 owns this type. Do not ship a second production definition or store.
    from .sync_contract import SyncRecord
    return SyncRecord(**deepcopy(values), deleted=deleted, local_revision=0)


class CourseSync:
    def __init__(self, record_store: RecordStore, transport):
        self.store, self.transport = record_store, transport
        self._lock, self._cancel, self._closed = Lock(), Event(), Event()
        self._scope = None
        self.on_progress = None
        self._merge_messages = []

    def _progress(self, cancel, **values):
        self._fence(cancel)
        if self.on_progress is not None:
            self.on_progress(values, cancel)

    def _merge_message(self, record, remote):
        """Describe field winners using the store's existing timestamp rule.

        Never include field values (messages and learning content can be private).
        This is a receipt, not a second merge implementation.
        """
        if record.payload == remote:
            return
        local = record.payload
        fields = (set(local) | set(remote)) - {'id', '_field_versions'}
        absent = object()
        changed = sorted(f for f in fields if local.get(f, absent) != remote.get(f, absent))
        if not changed and not record.deleted:
            return
        versions = local.get('_field_versions', {})
        theirs = remote.get('_field_versions', {})
        here = [f for f in changed if versions.get(f, [0, '']) > theirs.get(f, [0, ''])]
        there = [f for f in changed if f not in here]
        title = record.plan_key
        get_plan = getattr(self.store, 'get_plan', None)
        if callable(get_plan) and record.plan_key:
            try:
                plan = get_plan(record.plan_key)
                title = plan.get('title') or plan.get('goal') or title
            except ValueError:
                pass  # A local plan deletion can precede its dependent rows.
        label = {'plans': '计划信息', 'points': '知识点', 'modules': '模块',
                 'teacher_profile': '老师记录', 'question_bank': '题目'}.get(record.entity, '学习记录')
        owner = f'计划「{title[:80]}」' if title else '账号设置'
        if record.deleted:
            self._merge_messages.append(f'{owner}的{label}已在本机删除：保留本机删除，没有用云端副本恢复。')
            return
        names = {'title': '标题', 'goal': '学习目标', 'final_goal': '验收目标', 'state': '进度状态',
                 'archived': '归档状态', 'window': '学习日期与时长', 'notes': '备注',
                 'summary': '掌握目标', 'minutes': '预计时长', 'uses': '用题记录', 'value': '设置'}
        def describe(side, selected):
            if not selected:
                return f'{side} 0 项'
            text = '、'.join(names.get(f, label) for f in selected[:3])
            suffix = f'等 {len(selected)} 项' if len(selected) > 3 else ''
            return f'{side}的{text}{suffix}'
        self._merge_messages.append(f'{owner}的{label}有两端修改：按字段更新时间保留{describe("本机", here)}、{describe("云端", there)}。')

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
            return {"schema": CURSOR_SCHEMA, "scope": self._scope.snapshot_scope(), "records": {}, "since": "0"}
        try:
            cursor = json.loads(raw)
            if (set(cursor) not in ({"schema", "scope", "records"}, {"schema", "scope", "records", "since"})
                    or cursor["schema"] != CURSOR_SCHEMA
                    or cursor["scope"] != self._scope.snapshot_scope() or not isinstance(cursor["records"], dict)):
                raise ValueError()
            since = cursor.setdefault("since", "0")
            if not isinstance(since, str) or not re.fullmatch(r"0|[1-9][0-9]{0,18}", since) or int(since) > 2**63 - 1:
                raise ValueError()
            for wire_id, meta in cursor["records"].items():
                if (not re.fullmatch(re.escape(PREFIX) + r"[0-9a-f]{64}", wire_id)
                        or type(meta["revision"]) is not int or meta["revision"] < 1
                        or type(meta["deleted"]) is not bool
                        or "unapplied" in meta and type(meta["unapplied"]) is not bool):
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

    def _pending_delete(self, values):
        """Only the matching current local deletion may consume an old placeholder."""
        lookup = getattr(self.store, 'pending_record', None)
        if callable(lookup):
            candidate = lookup(values['entity'], values['entity_id'])
        else:
            candidate = next((record for record in self.store.pending_records(2**31-1)
                              if (record.entity, record.entity_id) == (values['entity'], values['entity_id'])), None)
        if candidate is None or not candidate.deleted:
            return False
        local = _values(candidate)
        return (all(local[key] == values[key] for key in ('schema', 'plan_key', 'entity', 'entity_id'))
                and local['payload'].get('id') == values['payload']['id'])

    def _pull(self, cursor, pending, cancel):
        self._fence(cancel)
        total = 0
        replay_since = cursor["since"]
        needs_replay = getattr(self.store, "needs_sync_replay", None)
        if callable(needs_replay) and any(
                not meta.get("deleted") and "entity" in meta
                and needs_replay(meta["entity"], meta["entity_id"])
                for meta in cursor["records"].values()):
            # Old clients kept identity metadata but advanced past an unknown
            # record. Re-read the feed only while a now-supported identity is
            # still absent locally; this also repairs cursors written before
            # the unapplied marker existed.
            replay_since = "0"
        while True:
            started = time.monotonic()
            event('sync', state='PULL_PAGE_START', completed=total)
            try:
                page = self.transport.read_changes(since=replay_since, limit=DEFAULT_BATCH_SIZE)
            except PlanError as exc:
                if exc.code != "SYNC_CHANGES_UNSUPPORTED":
                    raise
                snapshot = self.transport.read_snapshot()
                self._fence(cancel)
                validate_snapshot(snapshot, self._scope)
                # A rollback may serve writes without a sequence. On the next
                # upgrade, re-read all sequenced rows before resuming deltas.
                cursor, count = self._apply_page(cursor, pending, snapshot["entities"], "0", cancel)
                return cursor, total + count
            self._fence(cancel)
            cursor, count = self._apply_page(cursor, pending, page["entities"], page["next_cursor"], cancel)
            replay_since = cursor["since"]
            total += count
            event('sync', state='PULL_PAGE_END', count=len(page['entities']), completed=total,
                  elapsed_seconds=time.monotonic()-started)
            self._progress(cancel, phase='pull', completed=total)
            if not page["has_more"]:
                return cursor, total

    def _apply_page(self, cursor, pending, rows, since, cancel):
        local = {_wire_id(_values(r)): deepcopy(r) for r in pending}
        next_cursor = deepcopy(cursor)
        identities = deepcopy(cursor)
        new_identity = False
        changes, acknowledgements = [], []
        for row in rows:
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
                if identity is None:
                    # A plan tombstone contains only its opaque server ID. Old
                    # cursors may have lost its identity while stale progress
                    # is still pending; recover only from that progress scope.
                    for pending_record in pending:
                        if pending_record.entity not in PROGRESS or not pending_record.plan_key:
                            continue
                        plan_id = pending_record.plan_key
                        candidate = dict(schema='mentorflow.course.plans.v1', plan_key=plan_id,
                                         entity='plans', entity_id=plan_id, payload={'id': plan_id})
                        if _wire_id(candidate) == wire_id:
                            identity = candidate
                            break
                if identity is not None:
                    values = {k: identity[k] for k in ("schema", "plan_key", "entity", "entity_id")}
                    values["payload"] = _deleted_payload(identity)
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
            pending_delete = (not deleted and set(values['payload']) == {'id'}
                              and self._pending_delete(values))
            next_cursor["records"][wire_id] = (_metadata(values, revision, deleted) if values is not None
                                                else {"revision": revision, "deleted": True})
            if pending_delete:
                next_cursor['records'][wire_id]['unapplied'] = True
            if values is not None and "entity" not in previous:
                identities["records"][wire_id] = dict(next_cursor["records"][wire_id], unapplied=True)
                new_identity = True
            if pending_delete:
                # Old clients published the deletion's {id} as live data. Keep
                # only its CAS identity; the user's pending removal owns it.
                continue
            needs_replay = getattr(self.store, "needs_sync_replay", None)
            missing_local = (values is not None and not deleted and callable(needs_replay)
                             and needs_replay(values["entity"], values["entity_id"]))
            if values is not None and (revision != previous.get("revision") or "entity" not in previous
                                       or previous.get("unapplied") or missing_local):
                changes.append((_record(values, deleted), revision))
            candidate = local.get(wire_id)
            if candidate is not None and candidate.deleted == deleted and (deleted or _values(candidate) == values):
                acknowledgements.append((candidate, revision))
        self._fence(cancel)
        if new_identity:
            # Persist reversible tombstone identity BEFORE applying a new row,
            # without advancing since or claiming it applied. Otherwise a
            # cursor-save crash followed by remote deletion can orphan a row
            # locally: the server tombstone carries only an irreversible hash.
            self._save(identities, cancel)
        self._mark_synced([(record.entity, record.entity_id, record.local_revision, revision)
                           for record, revision in acknowledgements])
        if changes:
            for incoming, _ in changes:
                candidate = local.get(_wire_id(_values(incoming)))
                if candidate is not None and not incoming.deleted:
                    self._merge_message(candidate, incoming.payload)
            self.store.apply_remote(changes)
        next_cursor["since"] = since
        self._save(next_cursor, cancel)
        return next_cursor, len(changes)

    def _send(self, record, cursor, cancel, *, deleting, persist_cursor=True, acknowledgements=None):
        values = _values(record)
        wire_id = _wire_id(values)
        expected = cursor["records"].get(wire_id, {}).get("revision", 0)
        payload = _envelope(values)
        # Survives process restart without extending the RecordStore protocol.
        fingerprint = [self._scope.snapshot_scope(), wire_id, record.local_revision, expected, deleting, payload]
        request_id = "course-" + hashlib.sha256(_json(fingerprint).encode("utf-8")).hexdigest()
        self._fence(cancel)
        args = dict(request_id=request_id, expected_revision=expected)
        started = time.monotonic()
        event('sync', state='SINGLE_REQUEST_START', count=1)
        try:
            if deleting and wire_id not in cursor['records']:
                tombstone = getattr(self.transport, 'tombstone', None)
                if not callable(tombstone):
                    raise PlanError('SYNC_TOMBSTONE_UNSUPPORTED',
                                    '服务器暂不支持原子删除；本机删除仍保留，稍后可重试。')
                result = tombstone('learning_run', wire_id, **args)
            else:
                result = (self.transport.delete("learning_run", wire_id, **args) if deleting
                          else self.transport.write("learning_run", wire_id, payload=payload, **args))
        finally:
            event('sync', state='SINGLE_REQUEST_END', elapsed_seconds=time.monotonic()-started)
        self._fence(cancel)
        if (result.get("kind") != "learning_run" or result.get("entity_id") != wire_id
                or type(result.get("revision")) is not int or result["revision"] != expected + 1
                or result.get("deleted") is not deleting or result.get("payload") != ({} if deleting else payload)):
            raise PlanError("SYNC_RESPONSE_INVALID", "课程同步回执不匹配，未清除待同步记录。")
        cursor["records"][wire_id] = _metadata(values, result["revision"], deleting)
        if persist_cursor:
            self._save(cursor, cancel)
        # The batched caller persists the same cursor before its bulk
        # acknowledgement; the single-send path has already done so above.
        # Keeping this ordering means a cursor failure never clears the queue.
        self._fence(cancel)
        if deleting == record.deleted:
            acknowledgement = (record.entity, record.entity_id, record.local_revision, result["revision"])
            if acknowledgements is None:
                self.store.mark_synced(*acknowledgement)
            else:
                acknowledgements.append(acknowledgement)

    def _mark_synced(self, acknowledgements):
        """Commit one round's local acknowledgements with an optional fast path.

        The RecordStore contract deliberately remains the four-argument
        ``mark_synced`` method.  CourseStore supplies an optional bulk method
        so its SQLite outbox pays for one transaction per round; test doubles
        and older stores continue to use the contract method.
        """
        if not acknowledgements:
            return
        bulk = getattr(self.store, "mark_synced_many", None)
        if callable(bulk):
            bulk(acknowledgements)
            return
        for acknowledgement in acknowledgements:
            self.store.mark_synced(*acknowledgement)

    def _flush_acknowledgements(self, cursor, cancel, acknowledgements):
        """Durably save a partial round before clearing confirmed rows."""
        if not acknowledgements:
            return
        self._save(cursor, cancel)
        self._fence(cancel)
        self._mark_synced(acknowledgements)
        acknowledgements.clear()

    def _send_bulk(self, pending, cursor, cancel, summary, failures, total):
        """Return only rows needing the legacy/deletion/conflict path.

        Request IDs are the same as single PUT. Lost whole-batch receipts can
        be replayed after restart; acknowledgement fences keep concurrent edits.
        Conflicts are re-read together, then the store's field merge is reused.
        """
        if not callable(getattr(self.transport, 'write_many', None)):
            return pending
        operations, singles = [], []
        for record in pending:
            try:
                values = _values(record)
                wire_id = _wire_id(values)
                expected = cursor['records'].get(wire_id, {}).get('revision', 0)
                if record.deleted and not expected:
                    singles.append(record)
                    continue
                payload = _envelope(values)
                fingerprint = [self._scope.snapshot_scope(), wire_id, record.local_revision,
                               expected, record.deleted, payload]
                row = dict(kind='learning_run', entity_id=wire_id, expected_revision=expected,
                           request_id='course-'+hashlib.sha256(_json(fingerprint).encode()).hexdigest(),
                           deleted=record.deleted)
                if not record.deleted:
                    row['payload'] = payload
                operations.append((record, values, row))
            except PlanError as exc:
                failures.append(exc)
        index, batch_number, conflicted = 0, 0, set()
        split_batches = deque()
        while index < len(operations) or split_batches:
            if split_batches:
                batch = split_batches.popleft()
            else:
                batch, size = [], 0
                while index < len(operations) and len(batch) < UPLOAD_BATCH_SIZE:
                    item = operations[index]
                    amount = len(_json(item[2]).encode())
                    if batch and size + amount > UPLOAD_BATCH_BYTES:
                        break
                    batch.append(item); size += amount; index += 1
            batch_number += 1
            started = time.monotonic()
            event('sync', state='UPLOAD_BATCH_START', batch=batch_number, count=len(batch),
                  completed=summary['uploaded'], total=total)
            receipts = None
            for attempt in range(2):
                self._fence(cancel)
                try:
                    receipts = self.transport.write_many([item[2] for item in batch])
                    break
                except PlanError as exc:
                    if exc.code == 'SYNC_BULK_UNSUPPORTED':
                        remaining = batch + [item for queued in split_batches for item in queued] + operations[index:]
                        return singles + [item[0] for item in remaining]
                    if exc.code == 'SYNC_BATCH_TOO_LARGE':
                        if len(batch) > 1:
                            middle = len(batch) // 2
                            # Reuse the original operations, including CAS IDs.
                            split_batches.appendleft(batch[middle:])
                            split_batches.appendleft(batch[:middle])
                            event('sync', state='UPLOAD_BATCH_SPLIT', batch=batch_number, count=len(batch))
                        else:
                            # Same deterministic request ID via the legacy path.
                            singles.append(batch[0][0])
                            event('sync', state='UPLOAD_SINGLE_FALLBACK', batch=batch_number, count=1)
                        break
                    if exc.code == 'SYNC_UNAVAILABLE' and attempt == 0:
                        event('sync', state='UPLOAD_BATCH_RETRY', batch=batch_number, code=exc.code)
                        continue
                    if exc.code in {'SYNC_UNAVAILABLE', 'SYNC_RESPONSE_INVALID'}:
                        failures.extend([exc] * len(batch))
                        event('sync', state='UPLOAD_BATCH_END', batch=batch_number, failed=len(batch),
                              code=exc.code, elapsed_seconds=time.monotonic()-started)
                        # Leave unsent rows pending; a disconnected link must not
                        # consume a timeout for every row in the remaining queue.
                        return singles
                    raise
            if receipts is None:  # Size rejection is retry work, not a failed round.
                continue
            acknowledgements = []
            for (record, values, sent), receipt in zip(batch, receipts):
                self._fence(cancel)
                status = receipt['status']
                if status == 200:
                    result = receipt.get('result', {})
                    if (result.get('kind') != sent['kind'] or result.get('entity_id') != sent['entity_id']
                            or type(result.get('revision')) is not int
                            or result['revision'] != sent['expected_revision']+1
                            or result.get('deleted') is not record.deleted
                            or result.get('payload') != sent.get('payload', {})):
                        failures.append(PlanError('SYNC_RESPONSE_INVALID', '批量同步回执不匹配。'))
                        continue
                    cursor['records'][sent['entity_id']] = _metadata(values, result['revision'], record.deleted)
                    acknowledgements.append((record.entity, record.entity_id, record.local_revision, result['revision']))
                    summary['uploaded'] += 1
                elif status in (409, 410):
                    summary['conflicts'] += 1
                    conflicted.add((record.entity, record.entity_id))
                else:
                    failures.append(PlanError('SYNC_RECORD_TOO_LARGE' if status == 413 else
                                              'SYNC_UNAVAILABLE' if status >= 500 else 'SYNC_RECORD_INVALID',
                                              '部分学习记录尚未同步，本机记录已保留。'))
            self._flush_acknowledgements(cursor, cancel, acknowledgements)
            self._progress(cancel, phase='upload', completed=summary['uploaded'], total=total, failed=len(failures))
            event('sync', state='UPLOAD_BATCH_END', batch=batch_number, count=len(batch),
                  completed=summary['uploaded'], failed=len(failures), elapsed_seconds=time.monotonic()-started)
        if conflicted:
            next_cursor, count = self._pull(cursor, self.store.pending_records(MAX_BATCH_SIZE), cancel)
            cursor.clear(); cursor.update(next_cursor)
            summary['pulled'] += count
            singles.extend(record for record in self.store.pending_records(MAX_BATCH_SIZE)
                           if (record.entity, record.entity_id) in conflicted)
        return singles

    def sync(self, *, limit=DEFAULT_BATCH_SIZE, cancel=None):
        """One bounded pull/upload round; errors leave the store retryable."""
        if type(limit) is not int or not 1 <= limit <= MAX_BATCH_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_BATCH_SIZE}")
        if not self._lock.acquire(blocking=False):
            raise PlanError("SYNC_IN_PROGRESS", "课程同步正在进行。")
        started = time.monotonic()
        event('sync', state='ROUND_START')
        try:
            self._cancel.clear()
            self._fence(cancel)
            self._merge_messages = []
            # For the UI count and merge receipt, include the whole outbox;
            # network pages and upload batches remain bounded at 500 records.
            count_limit = 2**31-1 if self.on_progress is not None else limit
            self._progress(cancel, phase='start', completed=0)
            cursor, pulled = self._pull(self._cursor(), self.store.pending_records(count_limit), cancel)
            summary = dict(uploaded=0, pulled=pulled, conflicts=0)
            pending = deepcopy(self.store.pending_records(limit))
            acknowledgements = []
            total = len(self.store.pending_records(count_limit))
            failures = []
            network_failures = 0
            self._progress(cancel, phase='upload', completed=0, total=total, failed=0)
            pending = self._send_bulk(pending, cursor, cancel, summary, failures, total)
            for record in pending:
                for attempt in range(2):
                    try:
                        self._fence(cancel)
                        self._send(record, cursor, cancel, deleting=record.deleted,
                                   persist_cursor=False, acknowledgements=acknowledgements)
                        summary["uploaded"] += 1
                        network_failures = 0
                        break
                    except PlanError as exc:
                        if exc.code not in {"SYNC_CONFLICT", "SYNC_TOMBSTONED"}:
                            # Retry the same request ID once. A lost response
                            # must not create another remote revision.
                            retryable = exc.code == 'SYNC_UNAVAILABLE'
                            isolated = exc.code in {'SYNC_UNAVAILABLE', 'SYNC_RECORD_TOO_LARGE',
                                'SYNC_RECORD_INVALID', 'SYNC_RESPONSE_INVALID', 'SYNC_COURSE_INVALID'}
                            if retryable and attempt == 0:
                                continue
                            if isolated:
                                failures.append(exc)
                                network_failures = network_failures + 1 if getattr(exc, 'network_unavailable', False) else 0
                                break
                            if exc.code != "CANCELLED":
                                self._flush_acknowledgements(cursor, cancel, acknowledgements)
                            raise
                        summary["conflicts"] += 1
                        try:
                            cursor, count = self._pull(cursor, self.store.pending_records(limit), cancel)
                        except BaseException:
                            self._flush_acknowledgements(cursor, cancel, acknowledgements)
                            raise
                        summary["pulled"] += count
                        # RecordStore alone decides whether the local edit remains
                        # pending after remote application. Never resend the stale copy.
                        record = next((deepcopy(r) for r in self.store.pending_records(limit)
                                       if (r.entity, r.entity_id) == (record.entity, record.entity_id)), None)
                        if record is None:
                            break
                        if attempt or exc.code == "SYNC_TOMBSTONED":
                            failures.append(exc)
                            break
                    except BaseException as exc:
                        if not (isinstance(exc, PlanError) and exc.code == "CANCELLED"):
                            self._flush_acknowledgements(cursor, cancel, acknowledgements)
                        raise
                self._progress(cancel, phase='upload', completed=summary['uploaded'], total=total, failed=len(failures))
                if network_failures >= 2:
                    # Two consecutive records each failed twice at transport
                    # level: leave the rest queued instead of spending hundreds
                    # of request timeouts while the connection is unavailable.
                    break
            self._fence(cancel)
            # The cursor is durable before the queue is acknowledged.  If the
            # process stops between these two calls, the deterministic request
            # IDs and the remote change feed make the next round safe.
            self._save(cursor, cancel)
            self._fence(cancel)
            self._mark_synced(acknowledgements)
            notices = getattr(self.store, 'take_merge_notices', None)
            merged_conflicts = notices() if callable(notices) else 0
            if merged_conflicts:
                summary['merged_conflicts'] = merged_conflicts
                summary['merge_messages'] = list(dict.fromkeys(self._merge_messages))
            summary["pending"] = len(self.store.pending_records(limit))
            if failures:
                summary['failed'] = len(failures)
                summary['total'] = total
                summary['failure_codes'] = sorted({exc.code for exc in failures})
                # Persist/acknowledge successes first; only an entirely failed
                # upload batch is a round failure. Pull failure still fails fast.
                if not summary['uploaded']:
                    raise failures[0]
            summary["ok"] = summary["pending"] == 0
            event('sync', state='ROUND_RESULT', completed=summary['uploaded'], count=summary['pulled'],
                  total=total, failed=len(failures), elapsed_seconds=time.monotonic()-started)
            return summary
        except BaseException as exc:
            event('sync', state='ROUND_ERROR', code=getattr(exc, 'code', 'INTERNAL'),
                  elapsed_seconds=time.monotonic()-started)
            raise
        finally:
            event('sync', state='ROUND_END', elapsed_seconds=time.monotonic()-started)
            self._lock.release()
