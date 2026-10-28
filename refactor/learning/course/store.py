"""Account-owned SQLite course data and an atomic record outbox, schema v10.

Each business table stores a canonical, versioned JSON payload plus indexed FK
columns and a local revision. Payload fields (also validated below):
plans: intake, window/initial_window, total/initial_total, state, archived,
optional started_on/completed_on (explicit caller-supplied study dates);
sources: kind, url, sha256, title, site, tier, credibility, adopted;
source_chunks: extract chunk_id as id, source_id, position, text, pages, section;
stages/modules/points: title/summary, position, prerequisites, minute estimates,
and chunk_ids (legacy missing provenance is explicitly queued for repair);
teacher_profile/reports: document;
messages/chat_messages: role, content, kind, at, compressed, summary_of.
question_bank: original-source question payload (question_bank.validate_question),
source/answer_source locators and append-only purpose/time uses.
question_answers: immutable answer ID, question/point IDs, context, attempt,
angle, verdict and time; v9 adds literal answer, question snapshot, ticked/missed,
optional self_correct and dialogue anchor. Missing self_correct means historical
verdict, never an inferred student choice. All fields travel in the same sync fact.
classroom_positions: one row/id per plan, atomic scene state, originating
device_id and saved_at. State uses the existing field timestamp for last-edit
merge; the local legacy-import marker stays in _meta and is not synced.
Module positions are global within a plan; point positions are within a module.
Plan module_count and module point_count prevent a partially downloaded course
from becoming runnable/complete before all its structural records arrive.

credits stores immutable original credit submissions. The additional
ledger_events table stores immutable confirm/revoke/replan commands in the same
operation sequence: neither a cached Ledger nor mutable credit states are an
authority. Plans retain the initial window/total as well as the current ones.
New replans also retain their cause and optional caller reason; confirm/revoke
may carry an explicit study date. Legacy records without these remain readable.

Sync entity names are table names. For plan-owned records, wire entity_id is
length-prefixed '<len(plan_key)>:<plan_key><id>'; payload.id is the original ID
(in particular extract's chunk_id). plans use their plan ID directly. Chat
records use an empty plan_key. Chat sessions carry additive `archived` metadata
since schema v11; all wire schemas remain mentorflow.course.<entity>.v1.

_records is the durable revision/outbox journal. Hard deletion removes business
rows and report payloads; unsent progress stays intact in this journal. Remote
children received before parents remain in the journal until they can be
projected with valid foreign keys. Old plans.sqlite3 files are never opened.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from functools import wraps
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time
import uuid

from ..preferences.storage import account_directory
from .ledger import (Ledger, PlanWindow, Credit, replan_preserves_past,
                     learning_day_slices, order_journal_operations)
from .mastery import MasteryBook, Judgement, AttemptClosed, event_to_dict, event_from_dict
from .sync_contract import SyncRecord

SCHEMA_VERSION = 11
POINT_MINUTES_VERSION = 2  # Content totals, replacing v1's fixed-budget allocation.
MAX_PAYLOAD = 256 * 1024
_REPORT_STORAGE = '_course_report_storage'
_REPORT_PART_PREFIX = 'report-part:'
PROGRESS = frozenset(('judgements', 'attempt_closed', 'credits', 'ledger_events', 'usage', 'practice_results', 'question_answers'))
_FIELD_VERSIONS = '_field_versions'
# One entity maps to exactly one table. Tuple entries are FK field -> parent.
PARENTS = {
    'plans': (), 'sources': (), 'source_chunks': (('source_id', 'sources'),),
    'stages': (), 'modules': (('stage_id', 'stages'),), 'points': (('module_id', 'modules'),),
    'judgements': (), 'attempt_closed': (), 'credits': (), 'ledger_events': (),
    'teacher_profile': (), 'messages': (('module_id', 'modules'),), 'reports': (), 'usage': (),
    'chat_sessions': (), 'chat_messages': (('session_id', 'chat_sessions'),),
    'account_settings': (),
    'question_bank': (('module_id', 'modules'),), 'practice_results': (),
    'question_answers': (),  # immutable answered-question facts, independent of point judgements
    'point_links': (('module_id', 'modules'),),
    'classroom_positions': (),  # one atomic scene (state field) per plan, last edit wins
}
STATES = ('creating', 'ready', 'active', 'checking', 'completed')


class StoreError(ValueError):
    def __init__(self, code, message='课程数据不符合约定，操作未保存。'):
        self.code, self.message = code, message
        super().__init__(message)


def _json(value, *, bounded=True):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if bounded and len(raw.encode('utf-8')) > MAX_PAYLOAD:
            raise StoreError('PAYLOAD_TOO_LARGE', '单条同步记录超过 256 KiB，请按正文块或消息拆分。')
        return raw
    except (TypeError, ValueError, UnicodeError) as exc:
        if isinstance(exc, StoreError): raise
        raise StoreError('INVALID_JSON') from None


def _id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 240:
        raise StoreError('INVALID_ID')
    return value


def _number(value, positive=False):
    if type(value) is not int or value < int(positive):
        raise StoreError('INVALID_NUMBER')
    return value


def _day(value):
    if isinstance(value, str): value = date.fromisoformat(value)
    if type(value) is not date: raise StoreError('INVALID_DATE')
    return value


def _at(value=None):
    if value is None: value = datetime.now(timezone.utc)
    if isinstance(value, str): value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StoreError('NAIVE_DATETIME')
    return value.isoformat()


def _window(value):
    if isinstance(value, PlanWindow): return value
    return PlanWindow(_day(value['start']), _day(value['end']), value['daily_cap_minutes'],
                      tuple(value['weekday_weights']), {_day(k): v for k, v in value['date_weights'].items()})


def _window_json(value):
    w = _window(value)
    return dict(start=w.start.isoformat(), end=w.end.isoformat(), daily_cap_minutes=w.daily_cap_minutes,
                weekday_weights=list(w.weekday_weights), date_weights={d.isoformat(): v for d, v in w.date_weights.items()})


def _wire_id(entity, plan, identifier):
    return identifier if entity == 'plans' else f'{len(plan)}:{plan}{identifier}'


def _atomic(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.transaction():
            return method(self, *args, **kwargs)
    return wrapped


def _legacy_learning_day_projection(operations):
    """Project old aggregate restore credits onto revoked source study days.

    This is a read-only interpretation of immutable journal rows. New restore
    credits carry a ``source-day`` suffix and already have the right
    ``credit.day``. Old restore IDs are matched, in event order, only to earlier
    revoked learn/subpoint credits for the same point. The historical direct
    skip path that reused ``learn:<point>`` is recognized only immediately
    after its explicit ``confirm-direct`` event. Untraceable portions remain
    on the recorded recovery day.
    """
    active = {}
    revoked_sources = {}
    projection = {}
    source_attribution = {}
    direct_pending = None

    def is_recovery(credit_id):
        parts = credit_id.split(':')
        marked = len(parts) >= 5 and parts[-2] == 'source-day'
        return credit_id.startswith(('relearn:concept:', 'relearn:direct:')) and not marked

    def is_learning_source(credit_id):
        return credit_id.startswith(('learn:', 'subpoint:'))

    def consume(pool, amount):
        source_amount = min(amount, sum(item['minutes'] for item in pool))
        slices = learning_day_slices(
            [(item['day'], item['minutes']) for item in pool], source_amount
        ) if source_amount else []
        left = source_amount
        while left:
            used = min(left, pool[0]['minutes'])
            pool[0]['minutes'] -= used
            left -= used
            if pool[0]['minutes'] == 0:
                pool.pop(0)
        return source_amount, slices

    def consume_day(pool, day, amount):
        """Consume an explicitly source-attributed row from that source day."""
        left = amount
        consumed = []
        for item in list(pool):
            item_day = (date.fromisoformat(item['day'])
                        if isinstance(item['day'], str) else item['day'])
            if item_day != day or left <= 0:
                continue
            used = min(left, item['minutes'])
            item['minutes'] -= used
            left -= used
            consumed.append((item_day, used))
            if item['minutes'] == 0:
                pool.remove(item)
        return consumed

    ordered = order_journal_operations(operations)
    for index, (_, identifier, op, payload) in enumerate(ordered):
        seq = ordered[index][0]
        if direct_pending and not (op == 'credit' and seq == direct_pending[0] + 1):
            direct_pending = None
        if op == 'credit':
            credit = payload['credit']
            credit_id = credit['credit_id']
            active[credit_id] = dict(credit)
            unit_id = credit['unit_id']
            legacy_recovery = is_recovery(credit_id)
            if (credit_id.startswith(('relearn:concept:', 'relearn:direct:'))
                    and not legacy_recovery):
                # New source-day rows already carry their attribution in their
                # recorded day, but still consume the revoked pool so a later
                # legacy aggregate cannot claim those minutes again.
                consumed = consume_day(revoked_sources.setdefault(unit_id, []),
                                       date.fromisoformat(credit['day']), credit['minutes'])
                if consumed:
                    source_attribution[credit_id] = consumed
                direct_pending = None
                continue
            legacy_direct_learn = (direct_pending is not None and credit_id == f'learn:{unit_id}'
                                   and direct_pending[1] == unit_id)
            if not legacy_recovery and not legacy_direct_learn:
                direct_pending = None
                continue
            pool = revoked_sources.setdefault(unit_id, [])
            source_amount, slices = consume(pool, credit['minutes'])
            if source_amount:
                source_attribution[credit_id] = slices
            by_day = dict(slices)
            remainder = credit['minutes'] - source_amount
            if remainder:
                recovery_day = date.fromisoformat(credit['day'])
                by_day[recovery_day] = by_day.get(recovery_day, 0) + remainder
            if source_amount:
                projection[credit_id] = sorted(by_day.items())
            direct_pending = None
        elif op == 'confirm' and identifier.startswith('confirm-direct:'):
            direct_pending = (seq, payload['unit_id'])
            for credit in active.values():
                if credit['unit_id'] == payload['unit_id'] and credit['state'] == 'tentative':
                    credit['state'] = 'confirmed'
        elif op == 'confirm':
            for credit in active.values():
                if credit['unit_id'] == payload['unit_id'] and credit['state'] == 'tentative':
                    credit['state'] = 'confirmed'
        elif op == 'revoke':
            unit_id = payload['unit_id']
            pool = revoked_sources.setdefault(unit_id, [])
            for credit_id, credit in list(active.items()):
                if credit['unit_id'] != unit_id or credit['state'] != 'tentative':
                    continue
                attributed_source = source_attribution.pop(credit_id, None)
                if attributed_source:
                    pool.extend(dict(day=day.isoformat(), minutes=amount)
                                for day, amount in attributed_source)
                    projection.pop(credit_id, None)
                elif is_learning_source(credit_id):
                    pool.append(dict(day=credit['day'], minutes=credit['minutes']))
                del active[credit_id]
    return projection


class CourseStore:
    def __init__(self, account_id, data_root):
        self.account_id = account_id
        self.root = account_directory(account_id, data_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'course.sqlite3'
        for name in ('course.sqlite3', 'course.sqlite3-wal', 'course.sqlite3-shm', 'files'):
            self._safe_path(self.root / name)
        self._lock = threading.RLock()
        self._closed = False
        self._depth = 0
        self._changes = []
        self._listeners = set()
        self._db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA foreign_keys=ON')
        self._db.execute('PRAGMA busy_timeout=10000')
        self._db.execute('PRAGMA journal_mode=WAL')
        try:
            with self.transaction(): self._migrate()
        except BaseException:
            self._db.close(); self._closed = True
            raise

    @staticmethod
    def _safe_path(path):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise StoreError('UNSAFE_PATH')
        if path.is_file() and path.stat().st_nlink != 1:
            raise StoreError('UNSAFE_PATH')

    @contextmanager
    def transaction(self):
        """Group writes; notify observers only after the outer commit/unlock."""
        notifications = []
        with self._lock:
            if self._closed: raise StoreError('STORE_CLOSED')
            depth = self._depth
            savepoint = f'course_{depth}'
            self._db.execute('BEGIN IMMEDIATE' if depth == 0 else f'SAVEPOINT {savepoint}')
            self._depth += 1
            changes = {'local': set(), 'remote': set()}
            self._changes.append(changes)
            try:
                yield self
                self._db.execute('COMMIT' if depth == 0 else f'RELEASE {savepoint}')
                if depth:
                    for origin, entities in changes.items():
                        self._changes[-2][origin].update(entities)
                else:
                    notifications = [(listener, origin, frozenset(entities))
                                     for origin, entities in changes.items() if entities
                                     for listener in tuple(self._listeners)]
            except BaseException:
                if depth == 0:
                    if self._db.in_transaction: self._db.execute('ROLLBACK')
                else:
                    self._db.execute(f'ROLLBACK TO {savepoint}'); self._db.execute(f'RELEASE {savepoint}')
                raise
            finally:
                self._changes.pop()
                self._depth -= 1
        for listener, origin, entities in notifications:
            try:
                listener(origin, entities)
            except Exception:
                logging.getLogger(__name__).exception('course store observer failed')

    def subscribe(self, listener):
        """Observe committed entity changes on the writing thread; returns unsubscribe."""
        with self._lock:
            if self._closed: raise StoreError('STORE_CLOSED')
            self._listeners.add(listener)
        def unsubscribe():
            with self._lock: self._listeners.discard(listener)
        return unsubscribe

    def _changed(self, origin, entity):
        self._changes[-1][origin].add(entity)

    def close(self):
        with self._lock:
            if self._depth: raise StoreError('TRANSACTION_ACTIVE')
            if not self._closed:
                self._listeners.clear()
                self._db.close(); self._closed = True

    def __enter__(self): return self
    def __exit__(self, *args): self.close()

    def _migrate(self):
        tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'schema_version' not in tables:
            if tables: raise StoreError('UNKNOWN_DATABASE', '文件含有未知数据库，未覆盖。')
            self._db.execute('CREATE TABLE schema_version(singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL)')
            self._db.execute('INSERT INTO schema_version VALUES(1,0)')
        version = self._db.execute('SELECT version FROM schema_version WHERE singleton=1').fetchone()[0]
        if version > SCHEMA_VERSION or version < 0: raise StoreError('UNSUPPORTED_SCHEMA')
        if version == 0:
            self._db.execute('CREATE TABLE _meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            self._db.executemany('INSERT INTO _meta VALUES(?,?)', [('account_id', self.account_id), ('revision', '0'), ('cursor', '')])
            self._db.execute('''CREATE TABLE _records(entity TEXT NOT NULL, entity_id TEXT NOT NULL, plan_key TEXT NOT NULL,
                payload TEXT NOT NULL CHECK(json_valid(payload)), deleted INTEGER NOT NULL CHECK(deleted IN (0,1)),
                local_revision INTEGER NOT NULL, server_revision INTEGER NOT NULL DEFAULT 0, pending INTEGER NOT NULL,
                PRIMARY KEY(entity,entity_id))''')
            self._db.execute('CREATE TABLE plans(id TEXT PRIMARY KEY, payload TEXT NOT NULL CHECK(json_valid(payload)), revision INTEGER NOT NULL)')
            for entity, parents in PARENTS.items():
                if entity in ('plans', 'question_bank', 'practice_results', 'question_answers', 'account_settings', 'classroom_positions'): continue
                fields = [f'{name} TEXT' for name, _ in parents]
                constraints = [f'FOREIGN KEY(plan_key,{name}) REFERENCES {parent}(plan_key,id) DEFERRABLE INITIALLY DEFERRED'
                               for name, parent in parents]
                if entity not in ('chat_sessions', 'chat_messages'):
                    constraints.append('FOREIGN KEY(plan_key) REFERENCES plans(id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED')
                middle = ''.join(',' + value for value in fields + constraints)
                self._db.execute(f'''CREATE TABLE {entity}(plan_key TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL CHECK(json_valid(payload)),
                    revision INTEGER NOT NULL{middle},PRIMARY KEY(plan_key,id))''')
            # Provenance is a real relational FK, not just an unchecked JSON list.
            self._db.execute('''CREATE TABLE point_sources(plan_key TEXT,point_id TEXT,chunk_id TEXT,
                PRIMARY KEY(plan_key,point_id,chunk_id),
                FOREIGN KEY(plan_key,point_id) REFERENCES points(plan_key,id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
                FOREIGN KEY(plan_key,chunk_id) REFERENCES source_chunks(plan_key,id) DEFERRABLE INITIALLY DEFERRED)''')
            self._db.execute('UPDATE schema_version SET version=1'); version = 1
        if version == 1:
            # v2 adds ordered/outbox lookup indexes without changing any payload.
            self._db.execute('CREATE INDEX IF NOT EXISTS records_pending ON _records(pending,local_revision,entity,entity_id)')
            self._db.execute('CREATE INDEX IF NOT EXISTS records_plan ON _records(plan_key,entity)')
            self._db.execute("CREATE INDEX IF NOT EXISTS messages_order ON messages(plan_key,module_id,json_extract(payload,'$.at'),id)")
            self._db.execute('UPDATE schema_version SET version=2'); version = 2
        if version == 2:
            self._db.execute("CREATE INDEX IF NOT EXISTS chat_messages_order ON chat_messages(plan_key,session_id,json_extract(payload,'$.at'),id)")
            self._db.execute('UPDATE schema_version SET version=3'); version = 3
        if version == 3:
            for entity, parent in (('question_bank', 'module_id TEXT NOT NULL'), ('practice_results', '')):
                extra = ',' + parent if parent else ''
                fk = ',FOREIGN KEY(plan_key,module_id) REFERENCES modules(plan_key,id) DEFERRABLE INITIALLY DEFERRED' if parent else ''
                self._db.execute(f'''CREATE TABLE IF NOT EXISTS {entity}(plan_key TEXT NOT NULL,id TEXT NOT NULL,
                    payload TEXT NOT NULL CHECK(json_valid(payload)),revision INTEGER NOT NULL{extra},
                    FOREIGN KEY(plan_key) REFERENCES plans(id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED{fk},
                    PRIMARY KEY(plan_key,id))''')
            self._db.execute('UPDATE schema_version SET version=4'); version = 4
        if version == 4:
            self._db.execute('''CREATE TABLE IF NOT EXISTS question_answers(plan_key TEXT NOT NULL,id TEXT NOT NULL,
                payload TEXT NOT NULL CHECK(json_valid(payload)),revision INTEGER NOT NULL,
                FOREIGN KEY(plan_key) REFERENCES plans(id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
                PRIMARY KEY(plan_key,id))''')
            self._db.execute('UPDATE schema_version SET version=5')
            version = 5
        if version == 5:
            self._db.execute('''CREATE TABLE IF NOT EXISTS account_settings(plan_key TEXT NOT NULL,id TEXT NOT NULL,
                payload TEXT NOT NULL CHECK(json_valid(payload)),revision INTEGER NOT NULL,PRIMARY KEY(plan_key,id))''')
            self._db.execute('UPDATE schema_version SET version=6')
            version = 6
        if version == 6:
            self._db.execute('''CREATE TABLE IF NOT EXISTS point_links(plan_key TEXT NOT NULL,id TEXT NOT NULL,
                payload TEXT NOT NULL CHECK(json_valid(payload)),revision INTEGER NOT NULL,module_id TEXT,
                FOREIGN KEY(plan_key) REFERENCES plans(id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
                FOREIGN KEY(plan_key,module_id) REFERENCES modules(plan_key,id) DEFERRABLE INITIALLY DEFERRED,
                PRIMARY KEY(plan_key,id))''')
            self._db.execute('UPDATE schema_version SET version=7')
            version = 7
        owner = self._db.execute("SELECT value FROM _meta WHERE key='account_id'").fetchone()
        if owner is None or owner[0] != self.account_id: raise StoreError('ACCOUNT_MISMATCH')
        if version == 7:
            for plan_id, in self._db.execute('SELECT id FROM plans').fetchall():
                self._upgrade_material_plan(plan_id)
            self._db.execute('UPDATE schema_version SET version=8')
            version = 8
        if version == 8:
            # v9 extends append-only JSON answer facts. Keep old events byte-for-byte
            # (including their sync IDs/verdict); missing fields project as historical.
            self._db.execute('UPDATE schema_version SET version=9')
            version = 9
        if version == 9:
            self._db.execute('''CREATE TABLE IF NOT EXISTS classroom_positions(plan_key TEXT NOT NULL,id TEXT NOT NULL,
                payload TEXT NOT NULL CHECK(json_valid(payload)),revision INTEGER NOT NULL,
                FOREIGN KEY(plan_key) REFERENCES plans(id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
                PRIMARY KEY(plan_key,id))''')
            self._db.execute('UPDATE schema_version SET version=10')
            version = 10
        if version == 10:
            # Archive is additive session metadata. Keep messages untouched and
            # do not enqueue a mass sync just to backfill the default value.
            rows = self._db.execute("SELECT entity_id,payload FROM _records WHERE entity='chat_sessions' AND deleted=0").fetchall()
            for row in rows:
                payload = json.loads(row['payload'])
                if 'archived' in payload:
                    continue
                payload['archived'] = False
                raw = _json(payload)
                self._db.execute("UPDATE _records SET payload=? WHERE entity='chat_sessions' AND entity_id=?", (raw, row['entity_id']))
                projected = _json({key: value for key, value in payload.items() if key != _FIELD_VERSIONS})
                self._db.execute('UPDATE chat_sessions SET payload=? WHERE plan_key=? AND id=?', (projected, '', payload['id']))
            self._db.execute('UPDATE schema_version SET version=11')

    def _revision(self):
        self._db.execute("UPDATE _meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
        return int(self._db.execute("SELECT value FROM _meta WHERE key='revision'").fetchone()[0])

    def _get(self, entity, plan, identifier):
        if entity not in PARENTS: raise StoreError('UNKNOWN_ENTITY')
        row = self._db.execute('SELECT payload FROM plans WHERE id=?', (identifier,)).fetchone() if entity == 'plans' else self._db.execute(
            f'SELECT payload FROM {entity} WHERE plan_key=? AND id=?', (plan, identifier)).fetchone()
        if row is None: raise StoreError('NOT_FOUND', '找不到所需课程记录。')
        return json.loads(row[0])

    def _rows(self, entity, plan):
        return [json.loads(row[0]) for row in self._db.execute(f'SELECT payload FROM {entity} WHERE plan_key=? ORDER BY id', (plan,))]

    def _validate(self, entity, plan, payload, *, references=True):
        _id(payload['id'])
        if entity == 'classroom_positions':
            if payload['id'] != plan or not isinstance(payload.get('state'), dict) or not isinstance(payload.get('device_id'), str):
                raise StoreError('INVALID_CLASSROOM_POSITION')
            _at(payload['saved_at'])
        elif entity == 'point_links':
            if (payload['id'] != payload.get('module_id') or payload.get('status') not in ('ready', 'failed', 'pending')
                    or not isinstance(payload.get('fingerprint'), str) or not isinstance(payload.get('links'), list)
                    or not isinstance(payload.get('dropped', {}), dict)):
                raise StoreError('INVALID_POINT_LINKS')
            for edge in payload['links']:
                if not isinstance(edge, dict) or edge.get('kind') not in ('prerequisite', 'related'):
                    raise StoreError('INVALID_POINT_LINKS')
                _id(edge.get('from')); _id(edge.get('to'))
        elif entity == 'plans':
            if payload['id'] != plan or payload['state'] not in STATES or type(payload['archived']) is not bool: raise StoreError('INVALID_PLAN')
            for key in ('goal', 'final_goal', 'level', 'notes'):
                if not isinstance(payload[key], str): raise StoreError('INVALID_PLAN')
            for key in ('window', 'initial_window'):
                if payload[key] is not None: _window(payload[key])
            _number(payload['total_minutes']); _number(payload['initial_total_minutes'])
            _number(payload['module_count'])
            if payload.get('teacher_preset_id') is not None: _id(payload['teacher_preset_id'])
            for key in ('started_on', 'completed_on'):
                if payload.get(key) is not None: _day(payload[key])
        elif entity == 'account_settings':
            if plan or payload['id'] != 'teacher_presets': raise StoreError('INVALID_SETTING')
            from ..app.settings import validate_presets
            from ..app.bridge import BridgeError
            try: validate_presets(payload['value']['presets'], payload['value']['default_id'])
            except (BridgeError, KeyError, TypeError): raise StoreError('INVALID_SETTING') from None
        elif entity == 'sources':
            if payload['kind'] not in ('url', 'file') or type(payload['adopted']) is not bool: raise StoreError('INVALID_SOURCE')
            if payload['kind'] == 'file' and not re.fullmatch('[0-9a-f]{64}', payload['sha256'] or ''): raise StoreError('INVALID_SHA')
            if payload['kind'] == 'url' and not isinstance(payload['url'], str): raise StoreError('INVALID_SOURCE')
        elif entity == 'source_chunks':
            _id(payload['source_id']); _number(payload['position'])
            if not isinstance(payload['text'], str) or not payload['text'].strip(): raise StoreError('EMPTY_CHUNK')
            for key in ('page_start', 'page_end'):
                if payload[key] is not None: _number(payload[key], True)
            if payload['page_start'] is not None and payload['page_end'] is not None and payload['page_start'] > payload['page_end']:
                raise StoreError('INVALID_PAGES')
        elif entity in ('stages', 'modules', 'points'):
            _number(payload['position'])
            if entity == 'modules':
                _id(payload['stage_id']); _number(payload['learning_minutes']); _number(payload['check_minutes'])
                _number(payload['point_count'])
                # Optional together: old modules retain their combined estimate.
                if 'final_check_minutes' in payload or 'check_question_minutes' in payload:
                    _number(payload.get('final_check_minutes'))
                    _number(payload.get('check_question_minutes'), True)
                if not isinstance(payload['prerequisites'], list) or len(set(payload['prerequisites'])) != len(payload['prerequisites']): raise StoreError('INVALID_PREREQUISITES')
            if entity == 'points':
                _id(payload['module_id']); _number(payload['minutes'], True)
                if not isinstance(payload['summary'], str) or not payload['summary'].strip(): raise StoreError('INVALID_POINT')
                ids = payload['chunk_ids']
                if (not isinstance(ids, list) or len(set(ids)) != len(ids)
                        or not ids and payload.get('material_repair') != 'missing_provenance'):
                    raise StoreError('MISSING_PROVENANCE')
        elif entity in ('judgements', 'attempt_closed'):
            event = event_from_dict(payload['event'])
            expected = Judgement if entity == 'judgements' else AttemptClosed
            if not isinstance(event, expected) or event.event_id != payload['id']: raise StoreError('INVALID_EVENT')
        elif entity == 'credits':
            raw = dict(payload['credit']); raw['day'] = _day(raw['day']); credit = Credit(**raw)
            if credit.credit_id != payload['id']: raise StoreError('INVALID_EVENT')
            _number(payload['operation_seq'], True)
        elif entity == 'ledger_events':
            _number(payload['operation_seq'], True)
            if payload.get('today') is not None: _day(payload['today'])
            for key in ('reason', 'cause'):
                if payload.get(key) is not None and not isinstance(payload[key], str): raise StoreError('INVALID_EVENT')
            if payload['op'] == 'replan':
                _day(payload['today']); _window(payload['window']); _number(payload['total_minutes'])
            elif payload['op'] == 'release':
                _day(payload['today']); _number(payload['minutes'])
            elif payload['op'] in ('confirm', 'revoke'): _id(payload['unit_id'])
            else: raise StoreError('INVALID_EVENT')
        elif entity in ('messages', 'chat_messages'):
            if payload['role'] not in ('system', 'user', 'assistant', 'tool'): raise StoreError('INVALID_ROLE')
            if not isinstance(payload['content'], str) or not isinstance(payload['kind'], str): raise StoreError('INVALID_MESSAGE')
            _at(payload['at'])
            if type(payload['compressed']) is not bool or not isinstance(payload['summary_of'], list): raise StoreError('INVALID_MESSAGE')
        elif entity in ('teacher_profile', 'reports'):
            if not isinstance(payload['document'], dict): raise StoreError('INVALID_DOCUMENT')
        elif entity == 'chat_sessions':
            if not isinstance(payload['title'], str): raise StoreError('INVALID_SESSION')
            if 'archived' in payload and type(payload['archived']) is not bool: raise StoreError('INVALID_SESSION')
        elif entity == 'usage':
            for key in ('input_tokens', 'cache_hit_tokens', 'cache_miss_tokens', 'output_tokens', 'search_count'): _number(payload[key])
        elif entity == 'question_bank':
            from .question_bank import validate_question
            try:
                # subpoint_id is stored here; the bank's legacy shape validator
                # need not know this optional structural reference.
                validate_question({k: v for k, v in payload.items() if k != 'subpoint_id'})
                _id(payload['module_id'])
                for point_id in payload['point_ids']: _id(point_id)
            except (ValueError, KeyError, TypeError):
                raise StoreError('INVALID_QUESTION', '题库必须使用有原文与答案出处的新版格式。') from None
        elif entity == 'question_answers':
            _id(payload['module_id']); _at(payload['at'])
            if (payload['context'] not in ('module_test', 'skip_test', 'retest', 'check', 'practice', 'quick_confirm')
                    or type(payload['correct']) is not bool or not isinstance(payload['point_ids'], list)
                    or not payload['point_ids'] or not isinstance(payload['wrong_points'], list)
                    or not set(payload['wrong_points']) <= set(payload['point_ids'])):
                raise StoreError('INVALID_ANSWER')
        elif entity == 'practice_results':
            _id(payload['question_id']); _id(payload['module_id']); _at(payload['at'])
            if type(payload['correct']) is not bool or not isinstance(payload['point_ids'], list) or not payload['point_ids']:
                raise StoreError('INVALID_PRACTICE')
            if payload.get('sequence') is not None: _number(payload['sequence'], True)
            if 'wrong_points' in payload and (not isinstance(payload['wrong_points'], list)
                    or not set(payload['wrong_points']) <= set(payload['point_ids'])
                    or bool(payload['wrong_points']) == payload['correct']):
                raise StoreError('INVALID_PRACTICE')
        self._validate_concept_fields(entity, plan, payload, references=references)
        _json(payload)

    def _validate_concept_fields(self, entity, plan, payload, *, references=True):
        """Optional concept_v1 JSON fields; old rows retain their old contract.

        Remote children can precede parents. Shape is checked on ingestion;
        cross-record references are checked before projection, like point_sources.
        """
        def text(value):
            if not isinstance(value, str) or not value.strip(): raise StoreError('INVALID_CONCEPT')
        def chunks(value):
            if (not isinstance(value, list) or any(not isinstance(cid, str) for cid in value)
                    or len(set(value)) != len(value)):
                raise StoreError('INVALID_CONCEPT_CHUNKS')
            for cid in value:
                _id(cid)
                if references: self._get('source_chunks', plan, cid)
        if entity == 'points':
            if 'replaced_by' in payload and (not isinstance(payload['replaced_by'], list)
                    or any(not isinstance(pid, str) or not pid for pid in payload['replaced_by'])
                    or len(set(payload['replaced_by'])) != len(payload['replaced_by'])
                    or not isinstance(payload.get('replacement_operation'), str)):
                raise StoreError('INVALID_REPLACEMENT')
            if 'structure' in payload and payload['structure'] != 'concept_v1':
                raise StoreError('INVALID_CONCEPT')
            if 'structure' in payload: text(payload.get('title'))
            if 'goal' in payload:
                text(payload['goal'])
                if payload['goal'] != payload['summary']: raise StoreError('INVALID_CONCEPT_GOAL')
            if 'subpoints' in payload:
                subs = payload['subpoints']
                if not isinstance(subs, list): raise StoreError('INVALID_SUBPOINTS')
                ids, union, minutes = set(), [], 0
                for sub in subs:
                    if not isinstance(sub, dict): raise StoreError('INVALID_SUBPOINTS')
                    sid = sub.get('id')
                    if (not isinstance(sid, str) or not re.fullmatch(re.escape(payload['id']) + r'-s[1-9][0-9]*', sid)
                            or sid in ids): raise StoreError('INVALID_SUBPOINT_ID')
                    ids.add(sid); text(sub.get('title')); _number(sub.get('minutes'), True)
                    minutes += sub['minutes']; chunks(sub.get('chunk_ids'))
                    union.extend(cid for cid in sub['chunk_ids'] if cid not in union)
                    if not isinstance(sub.get('notes'), list): raise StoreError('INVALID_NOTES')
                    for note in sub['notes']:
                        if not isinstance(note, dict): raise StoreError('INVALID_NOTES')
                        text(note.get('text'))
                        if note.get('chunk_id') not in sub['chunk_ids']: raise StoreError('INVALID_NOTE_CHUNK')
                if minutes != payload['minutes'] or union != payload['chunk_ids']:
                    raise StoreError('INVALID_CONCEPT_TOTAL')
        elif entity == 'modules' and 'navigation' in payload:
            if not isinstance(payload['navigation'], list): raise StoreError('INVALID_NAVIGATION')
            seen = set()
            for nav in payload['navigation']:
                if not isinstance(nav, dict): raise StoreError('INVALID_NAVIGATION')
                _id(nav.get('id')); text(nav.get('title')); text(nav.get('reason'))
                if nav['id'] in seen: raise StoreError('INVALID_NAVIGATION')
                seen.add(nav['id']); chunks(nav.get('chunk_ids'))
        elif entity == 'question_bank' and 'subpoint_id' in payload:
            _id(payload['subpoint_id'])
            if references and payload.get('role') != 'retired':
                parents = [self._get('points', plan, pid) for pid in payload['point_ids']]
                if not any(p['module_id'] == payload['module_id'] and any(
                        s['id'] == payload['subpoint_id'] for s in p.get('subpoints', [])) for p in parents):
                    raise StoreError('INVALID_QUESTION_SUBPOINT')

    def _materialize(self, entity, plan, payload, revision):
        payload = {key: value for key, value in payload.items() if key != _FIELD_VERSIONS}
        identifier = payload['id']
        if entity == 'points' and 'replaced_by' in payload:
            # Reversible inactivity, not a server tombstone (which cannot be
            # resurrected). Keep the original payload in _records for rollback.
            self._db.execute('DELETE FROM point_sources WHERE plan_key=? AND point_id=?', (plan, identifier))
            self._db.execute('DELETE FROM points WHERE plan_key=? AND id=?', (plan, identifier))
            return
        if entity == 'plans':
            self._db.execute('INSERT INTO plans VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,revision=excluded.revision',
                             (identifier, _json(payload), revision))
            return
        parents = PARENTS[entity]
        columns = ['plan_key', 'id', 'payload', 'revision'] + [name for name, _ in parents]
        values = [plan, identifier, _json(payload), revision] + [payload.get(name) for name, _ in parents]
        update = ','.join(f'{name}=excluded.{name}' for name in columns[2:])
        self._db.execute(f"INSERT INTO {entity}({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) ON CONFLICT(plan_key,id) DO UPDATE SET {update}", values)
        if entity == 'points':
            self._db.execute('DELETE FROM point_sources WHERE plan_key=? AND point_id=?', (plan, identifier))
            self._db.executemany('INSERT INTO point_sources VALUES(?,?,?)', [(plan, identifier, cid) for cid in payload['chunk_ids']])

    def _put(self, entity, plan, payload, *, modified_ns=None, _inside_module_migration=False):
        # Module prep writers may hand us the legacy aggregate before its
        # detailed rejection rows are folded back onto their current points.
        # It is only an in-memory input here; the normalized wire row below is
        # still checked against MAX_PAYLOAD before it can be persisted.
        payload = json.loads(_json(payload, bounded=entity != 'modules'))
        if entity == 'modules':
            extraction = payload.get('question_extraction')
            if (not _inside_module_migration and isinstance(extraction, dict)
                    and isinstance(extraction.get('rejections'), list) and extraction['rejections']):
                with self.transaction():
                    return self._put(entity, plan, payload, modified_ns=modified_ns,
                                     _inside_module_migration=True)
            payload = self._compact_module_preparation_rejections(plan, payload)
        self._validate(entity, plan, payload)
        identifier = _wire_id(entity, plan, payload['id'])
        raw = _json(payload)
        old = self._db.execute('SELECT * FROM _records WHERE entity=? AND entity_id=?', (entity, identifier)).fetchone()
        previous = json.loads(old['payload']) if old and not old['deleted'] else {}
        previous_data = {key: value for key, value in previous.items() if key != _FIELD_VERSIONS}
        if old and not old['deleted'] and previous_data == payload: return payload
        if old and entity in PROGRESS: raise StoreError('EVENT_CONFLICT', '同一进度事件不能被改写。')
        if entity not in PROGRESS and (old and not old['deleted'] or entity == 'classroom_positions'):
            versions = dict(previous.get(_FIELD_VERSIONS, {}))
            stamp = [time.time_ns() if modified_ns is None else modified_ns, self._device_id()]
            for field in set(previous_data) | set(payload):
                if field != 'id' and previous_data.get(field) != payload.get(field):
                    versions[field] = stamp
            payload[_FIELD_VERSIONS] = versions
            raw = _json(payload)
        revision = self._revision()
        self._db.execute('''INSERT INTO _records VALUES(?,?,?,?,?,?,0,1) ON CONFLICT(entity,entity_id)
            DO UPDATE SET payload=excluded.payload,deleted=0,local_revision=excluded.local_revision,pending=1''',
                         (entity, identifier, plan, raw, 0, revision))
        self._materialize(entity, plan, payload, revision)
        self._changed('local', entity)
        return {key: value for key, value in payload.items() if key != _FIELD_VERSIONS}

    def _compact_module_preparation_rejections(self, plan, payload):
        """Keep detailed prep rejections with their point, not duplicated on a module.

        Module summaries were accumulating the same detailed evidence on every
        preparation pass. A growing module record then exceeded the sync
        record limit even though each point's checkpoint was small. Move rows
        that still identify a current subpoint to its existing point marker;
        stale/unknown rows are represented by a bounded count only.
        """
        extraction = payload.get('question_extraction')
        if not isinstance(extraction, dict):
            return payload
        rejected = extraction.get('rejections')
        if not isinstance(rejected, list) or not rejected:
            return payload

        point_rows = self._db.execute(
            'SELECT payload FROM points WHERE plan_key=? AND module_id=?',
            (plan, payload['id'])).fetchall()
        points = [json.loads(row[0]) for row in point_rows]
        by_subpoint = {
            sub.get('id'): point for point in points
            for sub in point.get('subpoints', [])
            if isinstance(sub, dict) and isinstance(sub.get('id'), str)
        }
        point_updates = {}
        for item in rejected:
            sid = item.get('subpoint_id') if isinstance(item, dict) else None
            point = by_subpoint.get(sid)
            if point is None:
                continue
            target = point_updates.get(point['id'], point)
            marker = target.get('subpoint_preparation')
            if not isinstance(marker, dict):
                marker = {}
            existing = marker.get('rejections', [])
            if not isinstance(existing, list):
                # Don't overwrite an invalid checkpoint that could contain
                # useful state. The module still retains its aggregate count.
                continue
            fingerprint = _json(item, bounded=False)
            if any(_json(saved, bounded=False) == fingerprint for saved in existing):
                continue
            if point['id'] not in point_updates:
                target = dict(point)
                target_marker = dict(marker)
                target_marker['rejections'] = list(existing)
                target['subpoint_preparation'] = target_marker
                point_updates[point['id']] = target
            target_marker = point_updates[point['id']]['subpoint_preparation']
            target_marker['rejections'].append(item)

        result = extraction.get('result')
        result_count = result.get('rejected', 0) if isinstance(result, dict) else 0
        previous_count = extraction.get('rejection_count', 0)
        previous_count = previous_count if type(previous_count) is int and previous_count >= 0 else 0
        extraction = dict(extraction)
        # Detail rows that cannot be tied to the current structure are stale
        # diagnostics. The count remains, while current-point detail survives
        # on its own point record.
        extraction['rejections'] = []
        extraction['rejection_count'] = max(previous_count, len(rejected),
                                             result_count if type(result_count) is int and result_count >= 0 else 0)

        # All related point writes and the compact module write participate in
        # the caller's transaction, so a too-large point record rolls back the
        # migration instead of losing its source evidence.
        for point in point_updates.values():
            self._put('points', plan, point)
        payload = dict(payload)
        payload['question_extraction'] = extraction
        return payload

    def classroom_position(self, plan_id):
        with self._lock:
            rows = self._rows('classroom_positions', plan_id)
            return rows[0] if rows else None

    def classroom_position_imported(self, plan_id):
        with self._lock:
            return self._db.execute('SELECT 1 FROM _meta WHERE key=?', ('classroom-import:'+plan_id,)).fetchone() is not None

    def classroom_gaps_imported(self, plan_id):
        with self._lock:
            return self._db.execute('SELECT 1 FROM _meta WHERE key=?', ('classroom-gaps-import:'+plan_id,)).fetchone() is not None

    @_atomic
    def import_classroom_gaps(self, plan_id, legacy_gaps):
        """Import legacy missing-material rows once, without syncing a marker field."""
        marker = 'classroom-gaps-import:'+plan_id
        if self._db.execute('SELECT 1 FROM _meta WHERE key=?', (marker,)).fetchone():
            record = self.classroom_position(plan_id)
            return list((record or {}).get('state', {}).get('question_gaps') or [])
        record = self.classroom_position(plan_id)
        state = dict(record['state']) if record else {}
        original_gaps = list(state.get('question_gaps') or [])
        gaps = []
        for gap in original_gaps + list(legacy_gaps):
            if gap not in gaps:
                gaps.append(gap)
        if gaps != state.get('question_gaps', []):
            if not record:
                raise StoreError('CLASSROOM_POSITION_MISSING')
            state['question_gaps'] = gaps
            self.save_classroom_position(plan_id, state)
        self._db.execute('INSERT INTO _meta VALUES(?,?)', (marker, '1'))
        return gaps

    @_atomic
    def import_classroom_position(self, plan_id, state, modified_ns):
        """Once per device. A stale legacy file never replaces a newer synced scene."""
        if self.classroom_position_imported(plan_id): return
        if state:
            identifier = _wire_id('classroom_positions', plan_id, plan_id)
            old = self._db.execute('SELECT payload FROM _records WHERE entity=? AND entity_id=?', ('classroom_positions',identifier)).fetchone()
            stamp = json.loads(old[0]).get(_FIELD_VERSIONS,{}).get('state',[0,''])[0] if old else 0
            if modified_ns > stamp:
                self.save_classroom_position(plan_id,state,modified_ns=modified_ns)
        self._db.execute('INSERT INTO _meta VALUES(?,?)', ('classroom-import:'+plan_id,'1'))

    @_atomic
    def save_classroom_position(self, plan_id, state, *, modified_ns=None):
        self._get('plans',plan_id,plan_id)
        modified_ns = time.time_ns() if modified_ns is None else modified_ns
        return self._put('classroom_positions',plan_id,dict(id=plan_id,state=state,device_id=self._device_id(),
            saved_at=datetime.fromtimestamp(modified_ns/1_000_000_000,timezone.utc).isoformat()),modified_ns=modified_ns)

    def classroom_device_id(self):
        with self.transaction(): return self._device_id()

    def _device_id(self):
        row = self._db.execute("SELECT value FROM _meta WHERE key='device_id'").fetchone()
        if row: return row[0]
        identifier = uuid.uuid4().hex
        self._db.execute("INSERT INTO _meta(key,value) VALUES('device_id',?)", (identifier,))
        return identifier

    @_atomic
    def take_merge_notices(self):
        row = self._db.execute("SELECT value FROM _meta WHERE key='merge_notices'").fetchone()
        count = int(row[0]) if row else 0
        self._db.execute("INSERT INTO _meta(key,value) VALUES('merge_notices','0') ON CONFLICT(key) DO UPDATE SET value='0'")
        return count

    def _delete(self, entity, plan, identifier):
        if entity in PROGRESS: raise StoreError('IMMUTABLE_EVENT')
        wire = _wire_id(entity, plan, identifier)
        old = self._db.execute('SELECT deleted FROM _records WHERE entity=? AND entity_id=?', (entity, wire)).fetchone()
        if not old or old[0]: return
        if entity == 'modules':
            self._delete('point_links', plan, identifier)
        if entity == 'points':
            for row in self._rows('point_links', plan):
                kept = [e for e in row['links'] if identifier not in (e['from'], e['to'])]
                if kept != row['links']:
                    row.update(links=kept, status='pending')
                    self._put('point_links', plan, row)
        self._db.execute('UPDATE _records SET payload=?,deleted=1,local_revision=?,pending=1 WHERE entity=? AND entity_id=?',
                         (_json({'id': identifier}), self._revision(), entity, wire))
        if entity == 'plans': self._db.execute('DELETE FROM plans WHERE id=?', (identifier,))
        else: self._db.execute(f'DELETE FROM {entity} WHERE plan_key=? AND id=?', (plan, identifier))
        self._changed('local', entity)

    @_atomic
    def create_plan(self, plan_id=None, *, goal='', final_goal='', level='', notes='', window=None, total_minutes=0, teacher_preset_id=None):
        plan_id = _id(plan_id or uuid.uuid4().hex)
        wire = self._db.execute("SELECT 1 FROM _records WHERE entity='plans' AND entity_id=?", (plan_id,)).fetchone()
        if wire: raise StoreError('PLAN_EXISTS')
        configuration = _window_json(window) if window is not None else None
        self._put('plans', plan_id, dict(id=plan_id, goal=goal, final_goal=final_goal, level=level, notes=notes,
            window=configuration, initial_window=configuration, total_minutes=total_minutes,
            initial_total_minutes=total_minutes, module_count=0, state='creating', archived=False, material_version=3,
            **({'teacher_preset_id': teacher_preset_id} if teacher_preset_id is not None else {})))
        return plan_id

    @_atomic
    def set_teacher_preset(self, plan_id, preset_id):
        plan = self._get('plans', plan_id, plan_id)
        plan['teacher_preset_id'] = _id(preset_id)
        return self._put('plans', plan_id, plan)

    @_atomic
    def save_account_setting(self, identifier, value):
        return self._put('account_settings', '', dict(id=identifier, value=value))

    def load_account_setting(self, identifier):
        with self._lock:
            try: return self._get('account_settings', '', identifier)['value']
            except StoreError as exc:
                if exc.code != 'NOT_FOUND': raise
                return None

    @_atomic
    def save_intake(self, plan_id, *, goal=None, final_goal=None, level=None, notes=None, window=None, today=None, reason=None):
        plan = self._get('plans', plan_id, plan_id)
        if plan['state'] not in ('creating', 'ready') and any(v is not None for v in (goal, final_goal, level, notes)):
            raise StoreError('PLAN_STARTED', '开始后的计划只能改日期或添加资料。')
        for key, value in dict(goal=goal, final_goal=final_goal, level=level, notes=notes).items():
            if value is not None: plan[key] = value
        if window is not None:
            configuration = _window_json(window)
            if plan['initial_window'] is None:
                plan['initial_window'] = plan['window'] = configuration
            elif plan['window'] != configuration:
                if today is None: raise StoreError('TODAY_REQUIRED', '修改日期须提供当前学习日。')
                self._replan(plan, _day(today), _window(configuration), plan['total_minutes'], cause='window_changed', reason=reason)
                plan['window'] = configuration
        return self._put('plans', plan_id, plan)

    def get_plan(self, plan_id):
        with self._lock: return self._get('plans', plan_id, plan_id)

    def list_plans(self, *, include_archived=False):
        with self._lock:
            values = [json.loads(r[0]) for r in self._db.execute('SELECT payload FROM plans ORDER BY id')]
            return [p for p in values if include_archived or not p['archived']]

    @_atomic
    def save_module_preparation(self, plan_id, module_id, summary, *, merge=False, question_count=None):
        """Persist the material summary; optionally merge output counts atomically."""
        row = self._get('modules', plan_id, module_id)
        value = dict(row.get('material_preparation') or {}) if merge else {}
        value.update(dict(summary))
        row['material_preparation'] = value
        if question_count is not None:
            _number(question_count)
            row['question_count'] = question_count
        return self._put('modules', plan_id, row)

    @_atomic
    def update_module_preparation_state(self, plan_id, module_id, state, **details):
        """Update durable queue state without replacing preparation outputs."""
        if state not in {'pending', 'queued', 'running', 'cancelled', 'stopped', 'failed', 'done'}:
            raise StoreError('PREPARATION_STATE_INVALID', '备题状态无效。')
        row = self._get('modules', plan_id, module_id)
        summary = dict(row.get('material_preparation') or {})
        unless_stopped = bool(details.pop('unless_stopped', False))
        resume_stopped = bool(details.pop('resume_stopped', False))
        if summary.get('state') == 'stopped' and unless_stopped:
            return summary
        if summary.get('state') == 'stopped' and state != 'stopped' and not resume_stopped:
            return summary
        summary['state'] = state
        for key in ('error', 'error_code', 'attempts', 'fingerprint', 'retry_at'):
            if key in details:
                summary[key] = details[key]
        if state == 'running' and 'error' not in details:
            summary['error'] = summary['error_code'] = None
        if state != 'running' or 'retry_at' not in details:
            summary['retry_at'] = None
        if state == 'done':
            summary['error'] = summary['error_code'] = None
            summary['retry_at'] = None
        row['material_preparation'] = summary
        self._put('modules', plan_id, row)
        return summary

    def records(self, entity, plan_id=''):
        """Read detached payloads; deterministic position/ID order for consumers."""
        if entity not in PARENTS or entity == 'plans': raise StoreError('UNKNOWN_ENTITY')
        with self._lock:
            return sorted(self._rows(entity, plan_id), key=lambda p: (p.get('position', 0), p['id']))

    def point_links_state(self, plan_id):
        """Read-only projection and invalidation, including edits received by sync.

        Fingerprints exclude preparation/ledger fields. Adding a predecessor's
        point invalidates its dependent modules as well as its own module.
        """
        from .planner_agents import validate_point_links
        with self._lock:
            self._get('plans', plan_id, plan_id)
            points = self._rows('points', plan_id)
            modules = sorted(self._rows('modules', plan_id), key=lambda m: (m['position'], m['id']))
            modules = [m for m in modules if any(p['module_id'] == m['id'] for p in points)]
            rows = {r['module_id']: r for r in self._rows('point_links', plan_id)}
            fingerprints, dirty, failed, links, ready = {}, [], [], [], 0
            for module in modules:
                content = dict(prerequisites=sorted(module['prerequisites']), points=[
                    {k: p.get(k) for k in ('id', 'title', 'summary') if k != 'summary' or p['module_id'] == module['id']}
                    for p in sorted(points, key=lambda p: p['id'])
                    if p['module_id'] in [module['id'], *module['prerequisites']]])
                fingerprint = hashlib.sha256(_json(content).encode('utf-8')).hexdigest()
                fingerprints[module['id']] = fingerprint
                row = rows.get(module['id'])
                if row is None or row['fingerprint'] != fingerprint or row['status'] == 'pending':
                    dirty.append(module['id'])
                elif row['status'] == 'failed':
                    failed.append(module['id'])
                else:
                    ready += 1
                if row:
                    clean, _ = validate_point_links(row['links'], {p['id']: p['id'] for p in points}, module, points, links)
                    links.extend(clean)
            status = ('none' if dirty or not modules else 'partial' if failed and ready
                      else 'failed' if failed else 'ready')
            return dict(modules=modules, points=points, fingerprints=fingerprints, dirty=dirty, failed=failed,
                        links=links, status=status)

    @_atomic
    def save_point_links(self, plan_id, module_id, fingerprint, links, *, dropped=None, error=None):
        state = self.point_links_state(plan_id)
        # A model reply about an older map may not overwrite a newer map.
        if state['fingerprints'].get(module_id) != fingerprint:
            return False
        from .planner_agents import validate_point_links
        module = next(m for m in state['modules'] if m['id'] == module_id)
        others = []
        for row in self._rows('point_links', plan_id):
            if row['module_id'] != module_id: others.extend(row['links'])
        clean, removed = validate_point_links(links, {p['id']: p['id'] for p in state['points']}, module, state['points'], others)
        counts = {k: removed[k] + (dropped or {}).get(k, 0) for k in removed}
        self._put('point_links', plan_id, dict(id=module_id, module_id=module_id, fingerprint=fingerprint,
            status='failed' if error else 'ready', links=clean, dropped=counts, error=error))
        logging.getLogger(__name__).info('point_links plan=%s module=%s kept=%d dropped=%s error=%s', plan_id, module_id, len(clean), counts, error)
        return True

    def _prune_point_links(self):
        """Repair stale remote endpoint references in the same sync transaction."""
        # An orphan does not appear in the FK projection; tombstone its journal
        # too, so a delayed link result cannot resurrect a remotely deleted module.
        for row in self._db.execute("SELECT plan_key,payload FROM _records WHERE entity='modules' AND deleted=1").fetchall():
            self._delete('point_links', row['plan_key'], json.loads(row['payload'])['id'])
        for plan_id, in self._db.execute('SELECT id FROM plans').fetchall():
            points = {p['id'] for p in self._rows('points', plan_id)}
            for row in self._rows('point_links', plan_id):
                kept = [e for e in row['links'] if e['from'] in points and e['to'] in points]
                if kept != row['links']:
                    row.update(links=kept, status='pending')
                    self._put('point_links', plan_id, row)

    @staticmethod
    def _retire_legacy_question(question):
        """Preserve the complete historical payload; never upgrade an invented Q."""
        from .question_bank import retire_legacy_question
        return retire_legacy_question(question)

    def _upgrade_material_plan(self, plan_id):
        """v8 material-format upgrade, also used after old-device sync ingestion.

        Called inside the existing store transaction. All changes go through the
        normal journal/outbox, so a subsequent projection cannot undo migration.
        Immutable progress/events and the plan state are never rewritten.
        """
        plan = self._get('plans', plan_id, plan_id)
        legacy = plan.get('material_version') not in (2, 3)
        upgrade_fields = plan.get('material_version') != 3 or plan.get('material_migration') == 8
        changed_modules, changed_points = set(), set()
        for question in self._rows('question_bank', plan_id):
            retired = self._retire_legacy_question(question)
            if retired.get('role') == 'retired' and not retired['retirement'].get('preparation_reset'):
                retired = dict(retired, retirement=dict(retired['retirement'], preparation_reset=True))
            if retired != question:
                self._put('question_bank', plan_id, retired)
                changed_modules.add(question['module_id'])
                changed_points.update(question.get('point_ids', ()))
        chunks = {c['id']: c for c in self._rows('source_chunks', plan_id)}
        sources = {s['id']: s for s in self._rows('sources', plan_id)}
        modules = self._rows('modules', plan_id)
        points = self._rows('points', plan_id)
        for point in points:
            if not upgrade_fields and point['id'] not in changed_points: continue
            original = dict(point)
            # Use only recorded point/relational provenance or literal citation
            # references; never guess a point's source from its neighbouring text.
            ids = list(point.get('chunk_ids') or ())
            if not ids:
                ids = [r[0] for r in self._db.execute('SELECT chunk_id FROM point_sources WHERE plan_key=? AND point_id=?', (plan_id, point['id']))]
                ids += [c['chunk_id'] for c in point.get('citations', ()) if c.get('chunk_id')]
                ids = list(dict.fromkeys(cid for cid in ids if cid in chunks))
                if ids: point['chunk_ids'] = ids
            if not point.get('citations') and ids:
                point['citations'] = [dict(chunk_id=cid, title=sources.get(chunks[cid]['source_id'], {}).get('title', '资料'),
                    quote=chunks[cid]['text'], locator=' / '.join(chunks[cid].get('section') or [])) for cid in ids if cid in chunks]
            if not point.get('summary') and point.get('title'): point['summary'] = point['title']
            if not point.get('title') and point.get('summary'): point['title'] = point['summary'][:80]
            if legacy or point['id'] in changed_points:
                point['question_preparation'] = 'pending'
            if not ids:
                # Keep a source-less old point readable; the existing classroom
                # prepare_one path asks for/relinks material before teaching it.
                point['material_repair'] = 'missing_provenance'
                point['chunk_ids'] = []
                changed_modules.add(point['module_id'])
            if point != original:
                self._put('points', plan_id, point)
        for module in modules:
            before = dict(module)
            if upgrade_fields:
                owned = [p for p in points if p['module_id'] == module['id']]
                module.setdefault('point_count', len(owned))
                module.setdefault('learning_minutes', sum(p['minutes'] for p in owned))
                module.setdefault('check_minutes', 0)
                module.setdefault('prerequisites', [])
                module.setdefault('chunk_ids', list(dict.fromkeys(cid for p in owned for cid in p.get('chunk_ids', ()))))
            if (legacy or module['id'] in changed_modules
                    or upgrade_fields and not (module.get('question_extraction') or {}).get('state')):
                module['question_extraction'] = dict(state='pending', reason='material_format_upgrade')
                module['material_preparation'] = dict(module.get('material_preparation') or {}, state='pending',
                    error=None, message='这个模块的题需要按资料重新准备，学习进度已保留。')
            if module != before: self._put('modules', plan_id, module)
        original = dict(plan)
        plan.pop('unusable', None); plan.pop('unusable_reason', None)
        plan['material_version'] = 3
        if legacy or changed_modules:
            plan['material_migration'] = 8
        if plan != original: self._put('plans', plan_id, plan)
        return plan

    @_atomic
    def require_material_plan(self, plan_id):
        plan = self.get_plan(plan_id)
        # Also accept an old locally restored plan without requiring app restart.
        if plan.get('material_version') != 3 or plan.get('unusable'):
            return self._upgrade_material_plan(plan_id)
        return plan

    @_atomic
    def link_point_sources(self, plan_id, point_id, chunk_ids):
        """Change provenance only, never content/mastery/schedule of a learnt point."""
        self.require_material_plan(plan_id)
        point = self._get('points', plan_id, point_id)
        ids = list(dict.fromkeys(chunk_ids))
        if not ids: raise StoreError('MISSING_PROVENANCE')
        for cid in ids:
            chunk = self._get('source_chunks', plan_id, cid)
            source = self._get('sources', plan_id, chunk['source_id'])
            if not source['adopted']: raise StoreError('MISSING_PROVENANCE')
        point['chunk_ids'] = ids
        point.pop('material_repair', None)
        self._put('points', plan_id, point)

    @_atomic
    def add_bank_questions(self, plan_id, module_id, questions):
        from .question_bank import original_text
        self.require_material_plan(plan_id)
        self._get('modules', plan_id, module_id)
        valid_points = {p['id'] for p in self._rows('points', plan_id) if p['module_id'] == module_id}
        identifiers = []
        bank = self._rows('question_bank', plan_id)
        retired_ids = {q['id'] for q in bank if q.get('role')=='retired'}
        existing_rows = [q for q in bank if q.get('role') != 'retired']
        stems = {}
        for q in existing_rows: stems.setdefault(original_text(q['stem']), []).append(q)
        skipped = 0
        for question in questions:
            row = dict(question)
            if row['id'] in retired_ids:
                skipped += 1
                continue  # a reused ID must never overwrite historical evidence
            if row['module_id'] != module_id or not set(row['point_ids']) <= valid_points:
                raise StoreError('POINT_MODULE_MISMATCH')
            # Content-addressed IDs make concurrent/background repeats idempotent.
            existing = next((q for q in existing_rows if q['id'] == row['id']), None)
            if existing is None:
                stem = original_text(row['stem'])
                # One point may retain the existing quick/bank roles of the
                # same gap. That never permits another point/module to count it.
                if any(not (
                        q['point_ids'] == row['point_ids'] and q.get('role', 'bank') != row.get('role', 'bank'))
                        for q in stems.get(stem, [])):
                    skipped += 1
                    continue  # Not admitted and not counted for this point.
                self._put('question_bank', plan_id, row)
                stems.setdefault(stem, []).append(row)
                existing_rows.append(row)
            identifiers.append(row['id'])
        if skipped: logging.getLogger(__name__).info('question admission skipped %s duplicate stem(s)', skipped)
        return identifiers

    @_atomic
    def record_question_use(self, plan_id, question_id, purpose, *, at=None):
        from .question_bank import PURPOSES
        self.require_material_plan(plan_id)
        if purpose not in PURPOSES: raise StoreError('INVALID_QUESTION_USE')
        row = self._get('question_bank', plan_id, question_id)
        if row.get('role') == 'retired': raise StoreError('QUESTION_RETIRED', '这道旧题已退出考核，历史作答仍保留。')
        row['uses'].append(dict(purpose=purpose, at=_at(at)))
        self._put('question_bank', plan_id, row)

    @_atomic
    def append_question_answer(self, plan_id, answer_id, *, module_id, context, attempt_id,
                               question_id, point_ids, angle, correct, wrong_points=(), reason='', checks=(), at=None,
                               answer=None, ticked=None, missed=None, self_correct=None, question=None,
                               dialogue_channel=None, question_message_id=None):
        """Persist a question answer and optional recall self-check exactly once.

        It is an append-only sync event, not an authoritative mastery state.
        Existing same-ID facts survive classroom replay without changing their time.
        """
        row = dict(id=_id(answer_id), module_id=_id(module_id), context=context, attempt_id=attempt_id,
                   question_id=question_id, point_ids=list(point_ids), angle=angle, correct=correct,
                   wrong_points=list(wrong_points), reason=str(reason)[:400], checks=list(checks), at=_at(at))
        if answer is not None:row['answer']=answer
        if ticked is not None:row['ticked']=list(ticked)
        if missed is not None:row['missed']=list(missed)
        if self_correct is not None:
            if type(self_correct) is not bool or self_correct != correct:raise StoreError('INVALID_ANSWER')
            row['self_correct']=self_correct
        if question is not None:
            row.update(question=question,dialogue_channel=dialogue_channel,question_message_id=question_message_id)
        existing = next((r for r in self._rows('question_answers', plan_id) if r['id'] == answer_id), None)
        if existing:
            if any(existing.get(k) != v for k, v in row.items() if k != 'at'):
                raise StoreError('EVENT_CONFLICT')
            return answer_id
        if any(q['id']==question_id and q.get('role')=='retired' for q in self._rows('question_bank',plan_id)):
            raise StoreError('QUESTION_RETIRED','这道旧题已退出考核，历史作答仍保留。')
        if context not in ('module_test', 'skip_test', 'retest', 'check', 'practice', 'quick_confirm') or type(correct) is not bool:
            raise StoreError('INVALID_ANSWER')
        self._get('modules', plan_id, module_id)
        valid = {p['id'] for p in self.records('points', plan_id) if p['module_id'] == module_id}
        if not point_ids or not set(point_ids) <= valid or not set(wrong_points) <= set(point_ids):
            raise StoreError('POINT_MODULE_MISMATCH')
        self._put('question_answers', plan_id, row)
        return answer_id

    def wrong_answers(self, plan_id):
        """Read-only wrong-ever groups; immutable snapshots, all attempts and dialogue.

        v8 facts retain their original verdict. No self-verdict is inferred. Bank
        and judgement details only supply missing display fields of old records.
        Messages remain separate sync entities, linked by question message ID.
        """
        with self._lock:
            self._get('plans',plan_id,plan_id)
            bank={q['id']:q for q in self.records('question_bank',plan_id)}
            judgements=self.records('judgements',plan_id)
            details={j['id']:j.get('details',{}) for j in judgements}
            messages=sorted(self.records('messages',plan_id),key=lambda m:(m['at'],m['id']))
            groups={}
            facts=self.records('question_answers',plan_id)
            known={f['id'] for f in facts}
            # Pre-answer-fact versions may only have the judgement event. Project
            # those too without rewriting or re-uploading immutable history.
            for j in judgements:
                e=j['event'];d=j.get('details') or {};qid=e.get('question_id') or d.get('question_id')
                if d.get('user_override') is True:
                    continue  # An authorized mastery grant is not a learner answer fact.
                if j['id'] in known or not qid:continue
                facts.append(dict(id=j['id'],question_id=qid,module_id=e['module_id'],context=e['context'],
                    attempt_id=e.get('attempt_id'),at=e['at'],correct=all(good for _,good in e['results'])))
            for fact in sorted(facts,key=lambda a:(a['at'],a['id'])):
                row=dict(fact);qid=row['question_id'];old=details.get(row['id'],{})
                q=dict(row.get('question') or bank.get(qid) or {})
                for key in ('reference','key_points','source','answer_source'):
                    if key not in q and key in old:q[key]=old[key]
                q.setdefault('stem',old.get('question','历史题目（题面未保存）'))
                q.setdefault('kind','recall' if 'key_points' in q else old.get('question_type','objective'))
                row['question']=q
                row.setdefault('answer',old.get('answer'))
                row.setdefault('ticked',old.get('ticked',[]))
                row.setdefault('missed',[i for i in range(len(q.get('key_points',[]))) if i not in row['ticked']])
                row.setdefault('self_correct',None)
                channel=row.get('dialogue_channel',None if row['context']=='check' else row['module_id'])
                dialogue=[m for m in messages if m['module_id']==channel]
                anchor=next((i for i,m in enumerate(dialogue) if m['id']==row.get('question_message_id')),None)
                if anchor is None:
                    anchor=next((i for i,m in reversed(list(enumerate(dialogue))) if m['at']<=row['at']),None)
                if anchor is not None:
                    end=next((i for i in range(anchor+1,len(dialogue)) if dialogue[i]['kind']=='question'),len(dialogue))
                    row['dialogue']=dialogue[max(0,anchor-2):end]
                else:row['dialogue']=[]
                group=groups.setdefault(qid,dict(question_id=qid,question=q,wrong_count=0,attempts=[]))
                group['attempts'].append(row)
                group['wrong_count']+=int(not row['correct'])
            return sorted((g for g in groups.values() if g['wrong_count']),key=lambda g:(-g['wrong_count'],g['question_id']))

    @_atomic
    def append_practice_result(self, plan_id, question_id, module_id, point_ids, correct, *, reason='', at=None, result_id=None, wrong_points=None):
        self._get('plans', plan_id, plan_id)
        if any(q['id']==question_id and q.get('role')=='retired' for q in self._rows('question_bank',plan_id)):
            raise StoreError('QUESTION_RETIRED','这道旧题已退出考核，历史作答仍保留。')
        identifier = _id(result_id or uuid.uuid4().hex)
        sequence = int(self._db.execute("SELECT value FROM _meta WHERE key='revision'").fetchone()[0]) + 1
        wrong = list(wrong_points) if wrong_points is not None else ([] if correct else list(point_ids))
        if not set(wrong) <= set(point_ids) or bool(wrong) == bool(correct): raise StoreError('INVALID_PRACTICE')
        self._put('practice_results', plan_id, dict(id=identifier, question_id=_id(question_id), module_id=_id(module_id),
                  point_ids=list(point_ids), wrong_points=wrong, correct=bool(correct), reason=str(reason)[:400], at=_at(at), sequence=sequence))
        return identifier

    @_atomic
    def add_sources(self, plan_id, sources):
        self._get('plans', plan_id, plan_id)
        identifiers = []
        for source in sources:
            source = dict(source)
            chunks = source.pop('chunks', [])
            identifier = source.pop('id', None) or uuid.uuid4().hex
            payload = dict(id=identifier, kind='url', url=None, sha256=None, title='', site='', tier='', credibility=None, adopted=False)
            payload.update(source)
            self._put('sources', plan_id, payload)
            for position, chunk in enumerate(chunks):
                data = asdict(chunk) if is_dataclass(chunk) else dict(chunk)
                chunk_id = data.pop('chunk_id', None) or data.pop('id', None)
                item = dict(id=_id(chunk_id), source_id=identifier, position=position, text='', page_start=None, page_end=None, section=[])
                item.update(data)
                try: existing = self._get('source_chunks', plan_id, chunk_id)
                except StoreError: existing = None
                if existing and existing['source_id'] != identifier: raise StoreError('CHUNK_CONFLICT')
                self._put('source_chunks', plan_id, item)
            identifiers.append(identifier)
        return identifiers

    @_atomic
    def set_source_adopted(self, plan_id, source_id, adopted):
        source = self._get('sources', plan_id, source_id)
        source['adopted'] = adopted
        self._put('sources', plan_id, source)

    @_atomic
    def set_source_publisher_evidence(self, plan_id, source_id, evidence):
        from .source_filter import classify_source
        source = self._get('sources', plan_id, source_id)
        body = '\n'.join(c['text'] for c in self._rows('source_chunks',plan_id) if c['source_id']==source_id)
        identity = classify_source(source.get('final_url') or source.get('url') or '',
                                   publisher_evidence=evidence, page_text=body)
        if identity['level'] not in (1,2,3): raise StoreError('UNKNOWN_PUBLISHER')
        source['publisher_evidence'] = dict(evidence)
        self._put('sources', plan_id, source)

    def _structure_editable(self, plan):
        if plan['state'] not in ('creating', 'ready'):
            raise StoreError('PLAN_STARTED', '开始后的计划不能删除模块或重新排序。')

    def studied_content(self, plan_id):
        """IDs with persisted study activity, including failed/tentative work.

        Messages lock their module's placement, but are not evidence that every
        point was learnt. Revoked credits still count as historical activity.
        """
        with self.transaction():
            self.get_plan(plan_id)
            points = {p['id']: p for p in self._rows('points', plan_id)}
            modules, learnt = set(), set()
            for entity in ('judgements', 'attempt_closed'):
                for row in self._rows(entity, plan_id):
                    event = row['event']
                    modules.add(event['module_id'])
                    learnt.update(pair[0] for pair in event.get('results', []))
            modules.update(row['module_id'] for row in self._rows('messages', plan_id) if row.get('module_id'))
            all_modules = {m['id'] for m in self._rows('modules', plan_id)}
            for row in self._rows('credits', plan_id):
                unit = row['credit']['unit_id']
                if unit in points: learnt.add(unit)
                if unit in all_modules: modules.add(unit)
                if unit.startswith('test:') and unit[5:] in all_modules: modules.add(unit[5:])
            modules.update(points[pid]['module_id'] for pid in learnt)
            return {'module_ids': sorted(modules), 'point_ids': sorted(learnt)}

    def _supplement_point(self, plan_id, point, chunk_ids):
        """Attach point-level source additions without changing learned units or time."""
        added = [cid for cid in dict.fromkeys(chunk_ids) if cid not in point['chunk_ids']]
        if not added:
            return point
        if point.get('structure') != 'concept_v1':
            return dict(point, chunk_ids=point['chunk_ids'] + added)
        from .planner_agents import material_paragraphs
        chunks = {c['id']: c for c in self._rows('source_chunks', plan_id)}
        subs = point['subpoints']
        for cid in added:
            chunk = chunks[cid]
            path = chunk.get('section') or []
            scores = []
            for sub in subs:
                title_depth = max((i + 1 for i, title in enumerate(path) if title == sub['title']), default=0)
                shared = 0
                for old_id in sub['chunk_ids']:
                    common = 0
                    for left, right in zip(path, chunks[old_id].get('section') or []):
                        if left != right: break
                        common += 1
                    shared = max(shared, common)
                scores.append((title_depth, shared))
            best = max(scores)
            matches = [i for i, score in enumerate(scores) if score == best]
            # shortcut: PLAN-10 supplies only a point ID. Until its analyzer
            # supplies a child mapping, ambiguous point-level supplements use
            # the first child as a stable display attachment, not a semantic guess.
            owner = subs[matches[0] if len(matches) == 1 and best != (0, 0) else 0]
            owner['chunk_ids'].append(cid)
            # Notes are output: retain existing excerpts and add bounded literal
            # prose. Full text remains available through this child's source map.
            prose = next((p['text'].strip() for p in material_paragraphs([chunk])
                          if p['kind'] != 'heading' and p['text'].strip()), '')
            if prose:
                sentence = re.split(r'(?<=[。！？.!?])\s+', prose, maxsplit=1)[0]
                owner['notes'].append(dict(text=sentence[:300], chunk_id=cid))
        point['chunk_ids'] = list(dict.fromkeys(cid for sub in subs for cid in sub['chunk_ids']))
        point['question_preparation'] = 'pending'
        return point

    @_atomic
    def append_material_additions(self, plan_id, modules, supplements, module_order, *, today, new_window=None):
        """Atomic PLAN-10 addition, never a general started-plan edit API.

        modules contain NEW IDs, points/check_minutes and optional required_by.
        supplements attach new chunks to their owning subpoints. Old wording,
        minutes and progress are immutable; old module relative order remains.
        A studied prefix cannot move. All writes/outbox/replan roll back together.
        """
        plan = self.get_plan(plan_id)
        if plan['archived'] or plan['state'] != 'active': raise StoreError('PLAN_NOT_ACTIVE')
        old = self.records('modules', plan_id)
        old_ids = [m['id'] for m in old]
        incoming = [dict(m) for m in modules]
        new_ids = [m['id'] for m in incoming]
        if len(set(new_ids)) != len(new_ids) or set(old_ids) & set(new_ids): raise StoreError('MODULE_CONFLICT')
        if (len(module_order) != len(old_ids) + len(new_ids) or len(set(module_order)) != len(module_order)
                or set(module_order) != set(old_ids + new_ids)
                or [mid for mid in module_order if mid in old_ids] != old_ids):
            raise StoreError('INVALID_ADDITION_ORDER')
        studied = set(self.studied_content(plan_id)['module_ids'])
        frontier = max((i + 1 for i, m in enumerate(old) if m['id'] in studied), default=0)
        if module_order[:frontier] != old_ids[:frontier]: raise StoreError('STUDIED_ORDER_IMMUTABLE')
        positions = {mid: i for i, mid in enumerate(module_order)}
        point_ids = {p['id'] for p in self._rows('points', plan_id)}
        if any(item['point_id'] not in point_ids for item in supplements):
            raise StoreError('INVALID_SUPPLEMENT_TARGET')
        dependencies = {}
        for item in incoming:
            points = item.pop('points')
            required_by = item.pop('required_by', [])
            self._get('stages', plan_id, item['stage_id'])
            if not points: raise StoreError('EMPTY_MODULE')
            if any(mid not in old_ids or mid in old_ids[:frontier] for mid in required_by):
                raise StoreError('INVALID_ADDITION_DEPENDENT')
            for mid in required_by: dependencies.setdefault(mid, []).append(item['id'])
            payload = dict(item, learning_minutes=sum(p['minutes'] for p in points), point_count=len(points),
                           position=positions[item['id']])
            self._put('modules', plan_id, payload)
            for index, point in enumerate(points):
                if point['id'] in point_ids: raise StoreError('POINT_CONFLICT')
                point_ids.add(point['id'])
                for cid in point['chunk_ids']: self._get('source_chunks', plan_id, cid)
                self._put('points', plan_id, dict(point, module_id=item['id'], position=index))
        # Keep every record in the frozen prefix byte-for-byte identical. The
        # suffix gets integer positions after it without changing old order.
        base = max((m['position'] for m in old[:frontier]), default=-1) + 1
        for i, mid in enumerate(module_order[frontier:] if incoming else []):
            module = self._get('modules', plan_id, mid)
            module['position'] = base + i
            module['prerequisites'] = list(dict.fromkeys(module['prerequisites'] + dependencies.get(mid, [])))
            self._put('modules', plan_id, module)
        self._check_graph(plan_id)
        for module in self._rows('modules', plan_id):
            if module['id'] in new_ids or module['id'] in dependencies:
                if any(positions[p] >= positions[module['id']] for p in module['prerequisites']):
                    raise StoreError('INVALID_ADDITION_ORDER')
        for item in supplements:
            point = self._get('points', plan_id, item['point_id'])
            if not item['chunk_ids']: raise StoreError('MISSING_PROVENANCE')
            for cid in item['chunk_ids']: self._get('source_chunks', plan_id, cid)
            point = self._supplement_point(plan_id, point, item['chunk_ids'])
            self._put('points', plan_id, point)
        if incoming:
            plan['module_count'] = len(module_order)
            added_minutes = sum(m['learning_minutes'] + m['check_minutes'] for m in self._rows('modules', plan_id)
                                if m['id'] in new_ids)
            self._replan(plan, _day(today), _window(new_window or plan['window']), plan['total_minutes'] + added_minutes,
                         cause='material_added')
            self._put('plans', plan_id, plan)
        elif new_window is not None and _window_json(new_window) != plan['window']:
            raise StoreError('SUPPLEMENT_TIME_IMMUTABLE')
        return self.get_plan(plan_id)

    def _check_graph(self, plan_id):
        modules = self._rows('modules', plan_id)
        graph = {m['id']: m['prerequisites'] for m in modules}
        visiting, visited = set(), set()
        def walk(key):
            if key not in graph: raise StoreError('MISSING_PREREQUISITE')
            if key in visiting: raise StoreError('CYCLIC_PREREQUISITES')
            if key in visited: return
            visiting.add(key)
            for parent in graph[key]: walk(parent)
            visiting.remove(key); visited.add(key)
        for key in graph: walk(key)

    @_atomic
    def save_outline(self, plan_id, stages, modules):
        """Upsert an outline; after start only new stages/modules may be added."""
        plan = self._get('plans', plan_id, plan_id)
        for position, data in enumerate(stages):
            stages_now = self._rows('stages', plan_id)
            existing = next((s for s in stages_now if s['id'] == data.get('id')), None)
            payload = dict(position=max((s['position'] for s in stages_now), default=-1) + 1, title='')
            if existing: payload.update(existing)
            payload.update(dict(data)); _id(payload['id'])
            if plan['state'] not in ('creating', 'ready'):
                if existing and existing != payload: raise StoreError('PLAN_STARTED')
            self._put('stages', plan_id, payload)
        for position, data in enumerate(modules):
            modules_now = self._rows('modules', plan_id)
            payload = dict(position=max((m['position'] for m in modules_now), default=-1) + 1, title='', prerequisites=[], learning_minutes=0, check_minutes=0, point_count=0)
            existing = next((m for m in modules_now if m['id'] == data.get('id')), None)
            if existing: payload.update(existing)
            payload.update(dict(data)); _id(payload['id'])
            if 'points' in payload: raise StoreError('OUTLINE_HAS_POINTS')
            self._get('stages', plan_id, payload['stage_id'])
            if existing and plan['state'] not in ('creating', 'ready') and existing != payload: raise StoreError('PLAN_STARTED')
            if existing and any(p['module_id'] == payload['id'] for p in self._rows('points', plan_id)):
                if any(payload.get(key) != existing.get(key) for key in ('learning_minutes', 'check_minutes', 'point_count',
                        'final_check_minutes', 'check_question_minutes')): raise StoreError('GENERATED_ESTIMATE_IMMUTABLE')
            self._put('modules', plan_id, payload)
        self._check_graph(plan_id)
        if plan['state'] == 'ready':
            generated = {p['module_id'] for p in self._rows('points', plan_id)}
            if any(m['id'] not in generated for m in self._rows('modules', plan_id)):
                plan['state'] = 'creating'
        count = len(self._rows('modules', plan_id))
        if plan['state'] == 'completed' and count > plan['module_count']:
            # New learning content starts a new completion cycle. Retain the
            # existing saved report, but do not reuse its finish date next time.
            plan['state'] = 'active'
            plan.pop('completed_on', None)
        plan['module_count'] = count
        self._put('plans', plan_id, plan)

    def _total(self, plan_id):
        return sum(m['learning_minutes'] + m['check_minutes'] + m.get('final_check_minutes', 0)
                   for m in self._rows('modules', plan_id))

    def _op_seq(self, plan_id):
        return max([p['operation_seq'] for table in ('credits', 'ledger_events') for p in self._rows(table, plan_id)], default=0) + 1

    def _replan(self, plan, today, new_window, total_minutes, *, cause, reason=None, point_minutes_key=None):
        if point_minutes_key:
            existing = [e for e in self._rows('ledger_events', plan['id'])
                        if e.get('point_minutes_key') == point_minutes_key]
            if existing:
                event = next((e for e in existing if e['id'] == plan.get('point_minutes_event_id')),
                             min(existing, key=lambda e: (e['operation_seq'], e['id'])))
                plan['window'], plan['total_minutes'] = event['window'], event['total_minutes']
                return event['id']
        ledger = self._load_ledger(plan)
        ledger.replan(today, new_window, total_minutes,
                      preserve_past=replan_preserves_past(cause))
        event = dict(op='replan', operation_seq=self._op_seq(plan['id']),
                     today=today.isoformat(), window=_window_json(new_window), total_minutes=total_minutes,
                     cause=cause, reason=reason)
        if point_minutes_key:
            event['point_minutes_key'] = point_minutes_key
            # Identical commands produced independently have identical IDs AND
            # payloads. Different clocks/sequences retain immutable facts and
            # are selected once during replay, rather than conflicting on sync.
            event['id'] = 'point-minutes:' + hashlib.sha256(_json(event).encode()).hexdigest()
        else:
            event['id'] = uuid.uuid4().hex
        self._put('ledger_events', plan['id'], event)
        plan['window'], plan['total_minutes'] = event['window'], total_minutes
        return event['id']

    @_atomic
    def replan_plan(self, plan_id, today, new_window=None, new_total_minutes=None, *, reason=None, cause='explicit_replan'):
        plan = self._get('plans', plan_id, plan_id)
        self._replan(plan, _day(today), _window(new_window or plan['window']),
                     plan['total_minutes'] if new_total_minutes is None else new_total_minutes, cause=cause, reason=reason)
        self._put('plans', plan_id, plan)

    @_atomic
    def save_generated_module(self, plan_id, module_id, points, *, check_minutes, today,
                              final_check_minutes=None, check_question_minutes=None):
        """Save separate module/final estimates, or retain legacy combined time.

        New estimates use whole minutes, like Ledger credits. Both optional
        fields must be supplied together. No old events or estimates are rewritten.
        """
        plan = self._get('plans', plan_id, plan_id)
        module = self._get('modules', plan_id, module_id)
        existing = [p for p in self._rows('points', plan_id) if p['module_id'] == module_id]
        if existing and plan['state'] not in ('creating', 'ready'): raise StoreError('PLAN_STARTED')
        points = list(points)
        if not points: raise StoreError('EMPTY_MODULE')
        new_ids = set()
        for position, point in enumerate(points):
            payload = dict(point, module_id=module_id, position=position)
            if payload['id'] in new_ids: raise StoreError('DUPLICATE_POINT')
            new_ids.add(payload['id'])
            old = next((p for p in self._rows('points', plan_id) if p['id'] == payload['id']), None)
            if old and old['module_id'] != module_id: raise StoreError('POINT_CONFLICT')
            for chunk in payload.get('chunk_ids', []): self._get('source_chunks', plan_id, chunk)
            self._put('points', plan_id, payload)
        for point in existing:
            if point['id'] not in new_ids: self._delete('points', plan_id, point['id'])
        module['learning_minutes'] = sum(p['minutes'] for p in points)
        module['check_minutes'] = check_minutes
        if final_check_minutes is not None or check_question_minutes is not None:
            _number(final_check_minutes); _number(check_question_minutes, True)
            module.update(final_check_minutes=final_check_minutes, check_question_minutes=check_question_minutes)
        else:
            module.pop('final_check_minutes', None)
            module.pop('check_question_minutes', None)
        module['point_count'] = len(points)
        self._put('modules', plan_id, module)
        self._replan(plan, _day(today), _window(plan['window']), self._total(plan_id), cause='module_generated')
        generated = {p['module_id'] for p in self._rows('points', plan_id)}
        if plan['state'] in ('creating', 'ready'):
            plan['state'] = 'ready' if all(m['id'] in generated for m in self._rows('modules', plan_id)) else 'creating'
        self._put('plans', plan_id, plan)

    @_atomic
    def reorder_modules(self, plan_id, module_ids, *, today):
        plan = self._get('plans', plan_id, plan_id); self._structure_editable(plan)
        modules = {m['id']: m for m in self._rows('modules', plan_id)}
        if len(module_ids) != len(modules) or set(module_ids) != set(modules): raise StoreError('INVALID_ORDER')
        for index, key in enumerate(module_ids):
            modules[key]['position'] = index
            self._put('modules', plan_id, modules[key])
        self._check_graph(plan_id)
        self._replan(plan, _day(today), _window(plan['window']), self._total(plan_id), cause='modules_reordered')
        self._put('plans', plan_id, plan)

    @_atomic
    def delete_module(self, plan_id, module_id, *, today):
        plan = self._get('plans', plan_id, plan_id); self._structure_editable(plan)
        self._get('modules', plan_id, module_id)
        for question in self._rows('question_bank', plan_id):
            if question['module_id'] == module_id: self._delete('question_bank', plan_id, question['id'])
        for point in self._rows('points', plan_id):
            if point['module_id'] == module_id: self._delete('points', plan_id, point['id'])
        for message in self._rows('messages', plan_id):
            if message.get('module_id') == module_id: self._delete('messages', plan_id, message['id'])
        self._delete('modules', plan_id, module_id)
        for module in self._rows('modules', plan_id):
            if module_id in module['prerequisites']:
                module['prerequisites'].remove(module_id); self._put('modules', plan_id, module)
        plan['module_count'] = len(self._rows('modules', plan_id))
        self._replan(plan, _day(today), _window(plan['window']), self._total(plan_id), cause='module_deleted')
        self._put('plans', plan_id, plan)

    @_atomic
    def start_plan(self, plan_id, *, today=None):
        """Pass the current study day to record an exact start; no clock is read."""
        self.require_material_plan(plan_id)
        plan = self._get('plans', plan_id, plan_id)
        if plan['state'] != 'ready' or plan['archived']: raise StoreError('PLAN_NOT_READY')
        self._load_ledger(plan)
        if not self._fully_generated(plan_id): raise StoreError('PLAN_NOT_READY')
        if today is not None: plan['started_on'] = _day(today).isoformat()
        plan['state'] = 'active'; self._put('plans', plan_id, plan)

    @_atomic
    def start_checking(self, plan_id):
        plan = self._get('plans', plan_id, plan_id)
        if plan['state'] != 'active' or not self._fully_generated(plan_id) or not self.load_mastery(plan_id).check_ready(): raise StoreError('CHECK_NOT_READY')
        plan['state'] = 'checking'; self._put('plans', plan_id, plan)

    def _fully_generated(self, plan_id):
        modules = self._rows('modules', plan_id)
        plan = self._get('plans', plan_id, plan_id)
        points = self._rows('points', plan_id)
        return (bool(modules) and len(modules) == plan['module_count']
                and all(m['point_count'] > 0 and sum(p['module_id'] == m['id'] for p in points) == m['point_count'] for m in modules))

    @_atomic
    def complete_plan(self, plan_id, *, today):
        plan = self._get('plans', plan_id, plan_id)
        if not self._fully_generated(plan_id) or not self.load_mastery(plan_id).check_complete():
            raise StoreError('PLAN_NOT_COMPLETE')
        # Completion is mastery, not consumption of an estimate (CHK-01).
        # A release is an immutable ledger operation, never a fake credit.
        unused = max(0, self._load_ledger(plan).status(_day(today)).remaining_confirmed_minutes)
        if unused:
            self._release_estimate(plan_id, f'complete:{plan["module_count"]}', unused, _day(today))
        # Repeating completion must not move a previously recorded finish date.
        if plan['state'] != 'completed' or not plan.get('completed_on'):
            plan['completed_on'] = _day(today).isoformat()
        plan['state'] = 'completed'; self._put('plans', plan_id, plan)

    @_atomic
    def archive_plan(self, plan_id):
        plan = self._get('plans', plan_id, plan_id); plan['archived'] = True
        self._put('plans', plan_id, plan)

    @_atomic
    def rename_plan(self, plan_id, title):
        """A display name only; the learning goal the plan was built for stays as it was."""
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80: raise StoreError('INVALID_TITLE')
        plan = self._get('plans', plan_id, plan_id); plan['title'] = title.strip()
        self._put('plans', plan_id, plan)

    @_atomic
    def restore_plan(self, plan_id):
        plan = self._get('plans', plan_id, plan_id); plan['archived'] = False
        self._put('plans', plan_id, plan)

    @_atomic
    def delete_plan(self, plan_id):
        self._get('plans', plan_id, plan_id)
        # Tombstone every structural row; progress journal entries survive even
        # though SQLite cascades their business rows when the plan is removed.
        for entity in reversed(tuple(PARENTS)):
            if entity in PROGRESS or entity in ('plans', 'chat_sessions', 'chat_messages', 'account_settings'): continue
            for payload in self._rows(entity, plan_id): self._delete(entity, plan_id, payload['id'])
        self._delete('plans', plan_id, plan_id)

    def _load_ledger(self, plan):
        if plan['initial_window'] is None: raise StoreError('PLAN_NOT_READY')
        ledger = Ledger(_window(plan['initial_window']), plan['initial_total_minutes'])
        operations = [(p['operation_seq'], p['id'], 'credit', p) for p in self._rows('credits', plan['id'])]
        events = self._rows('ledger_events', plan['id'])
        operations += [(p['operation_seq'], p['id'], p['op'], p) for p in events]
        estimate_events = {}
        for e in sorted(events, key=lambda e: (e['operation_seq'], e['id'])):
            if e.get('cause') == 'point_minutes' and e.get('point_minutes_key'):
                key = e['point_minutes_key']
                if key not in estimate_events or e['id'] == plan.get('point_minutes_event_id'):
                    estimate_events[key] = e['id']
        for _, _, op, payload in order_journal_operations(operations):
            if op == 'credit':
                c = payload['credit']
                ledger._add_journal_credit(c['credit_id'], c['unit_id'], c['minutes'],
                                           _day(c['day']), c['state'])
            elif op == 'replan':
                key = payload.get('point_minutes_key')
                if key and payload['id'] != estimate_events.get(key): continue
                ledger.replan(_day(payload['today']), _window(payload['window']), payload['total_minutes'],
                              preserve_past=replan_preserves_past(payload.get('cause')))
            elif op == 'confirm': ledger.confirm(payload['unit_id'])
            elif op == 'revoke': ledger.revoke(payload['unit_id'])
            elif op == 'release': ledger.release_unused(payload['minutes'])
        ledger.set_learning_day_projection(_legacy_learning_day_projection(operations))
        return ledger

    def point_minutes_snapshot(self, plan_id):
        """One coherent input snapshot, excluding minutes/credits from content identity."""
        from .planner_agents import point_minute_metrics
        import hashlib
        with self.transaction():
            plan = self._get('plans', plan_id, plan_id)
            points, modules = self.records('points', plan_id), self.records('modules', plan_id)
            metrics = point_minute_metrics(points, self.records('source_chunks', plan_id),
                                           self.records('question_bank', plan_id))
            content = []
            for p in points:
                # Preserve the v2 identity shape for legacy points so an
                # existing plan does not re-request difficulty merely because
                # this version learned about concept children.  Concept
                # parents include their child IDs/minutes: regrouping must
                # invalidate their signature even though they have no parent
                # citations.
                row = (p['id'], p['module_id'], p['title'], p.get('summary'), p.get('citations'),
                       metrics[p['id']]['characters'])
                if p.get('structure') == 'concept_v1':
                    row += (p.get('structure'),
                            tuple((s.get('id'), s.get('minutes')) for s in p.get('subpoints', [])))
                content.append(row)
            fingerprint = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            point_fingerprints = {row[0]: hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                                  for row in content}
            signature = hashlib.sha256(json.dumps([fingerprint, metrics], sort_keys=True).encode()).hexdigest()
            ready = (plan.get('point_minutes_version') == POINT_MINUTES_VERSION and
                     plan.get('point_minutes_signature') == signature)
            return dict(plan=plan, points=points, modules=modules, metrics=metrics, fingerprint=fingerprint,
                        signature=signature, point_fingerprints=point_fingerprints, ready=ready)

    @_atomic
    def save_point_minutes(self, plan_id, fingerprint, difficulties, *, today=None):
        """Commit content estimates and a future-only replan; preserve earned time."""
        from .planner_agents import estimate_point_minutes
        state = self.point_minutes_snapshot(plan_id)
        if state['ready']: return True
        if state['fingerprint'] != fingerprint: return False
        points, modules, plan = state['points'], state['modules'], state['plan']
        # Directory based concept points already have their estimate in the
        # subpoints.  They must not be sent through the legacy citation based
        # estimator (their parent citations are intentionally absent).  Keep
        # accepting the historical all-points label map for old plans, while
        # mixed plans only require labels for legacy points.
        legacy = {p['id'] for p in points if p.get('structure') != 'concept_v1'}
        supplied = set(difficulties)
        if supplied not in (legacy, {p['id'] for p in points}):
            raise StoreError('MINUTE_POINTS_CHANGED')
        # Any completed concept subpoint freezes its parent estimate: a later
        # metadata refresh must not move the goal beneath already-earned work.
        frozen = self.learning_credit_projection(plan_id)['recorded']
        weights = {}
        for p in points:
            if p.get('structure') == 'concept_v1':
                # The parent is a projection of its children.  Keeping this
                # invariant makes regrouped plans stable across devices and
                # prevents a second difficulty call from shrinking them.
                weights[p['id']] = sum(int(s['minutes']) for s in p.get('subpoints', []))
            else:
                weights[p['id']] = estimate_point_minutes(
                    **state['metrics'][p['id']], difficulty=difficulties[p['id']])
        for p in points:
            original = dict(p)
            if p.get('structure') != 'concept_v1':
                p.update(difficulty=difficulties[p['id']],
                         minute_estimate=dict(**state['metrics'][p['id']], unconstrained=weights[p['id']]))
                p.update(point_minutes_version=POINT_MINUTES_VERSION,
                         difficulty_fingerprint=state['point_fingerprints'][p['id']])
            if p['id'] not in frozen: p['minutes'] = weights[p['id']]
            if p != original: self._put('points', plan_id, p)
        for m in modules:
            minutes = sum(p['minutes'] for p in points if p['module_id'] == m['id'])
            if minutes != m['learning_minutes']:
                m['learning_minutes'] = minutes
                self._put('modules', plan_id, m)
        plan.pop('point_minutes_provisional', None)
        plan.update(point_minutes_version=POINT_MINUTES_VERSION, point_minutes_signature=state['signature'],
                    point_minutes_unconstrained_total=sum(weights.values()))
        total = self._total(plan_id)
        if total != plan['total_minutes']:
            plan['point_minutes_event_id'] = self._replan(
                plan, _day(today or date.today()), _window(plan['window']), total,
                cause='point_minutes', reason='按原文分量、题量与难度重算',
                point_minutes_key=f'{POINT_MINUTES_VERSION}:{state["signature"]}')
        self._put('plans', plan_id, plan)
        return True

    def load_ledger(self, plan_id) -> Ledger:
        with self.transaction(): return self._load_ledger(self._get('plans', plan_id, plan_id))

    def load_mastery(self, plan_id) -> MasteryBook:
        with self.transaction():
            self._get('plans', plan_id, plan_id)
            modules = sorted(self._rows('modules', plan_id), key=lambda m: (m['position'], m['id']))
            points = self._rows('points', plan_id)
            taught = self.learning_credit_projection(plan_id)['taught']
            bank = self.records('question_bank',plan_id)
            event_rows = [p for entity in ('judgements', 'attempt_closed') for p in self._rows(entity, plan_id)]
            return MasteryBook.from_course_records(points,modules,bank,taught,event_rows,
                                                    self._rows('question_answers',plan_id))

    def learning_credit_projection(self, plan_id):
        """Return immutable learning-credit markers used by mastery and estimates.

        Old whole-point learn/relearn rows remain authoritative. Concept points
        become taught only after their original subpoint-credit total reaches
        the point estimate; any recorded subpoint freezes that estimate while
        the point is in progress.
        """
        points = self.records('points', plan_id)
        rows = self._rows('credits', plan_id)
        taught = {row['credit']['unit_id'] for row in rows
                  if row['id'].startswith(('learn:', 'relearn:'))}
        recorded = set(taught)
        subpoint_minutes = {}
        for row in rows:
            credit = row['credit']
            unit = credit['unit_id']
            if row['id'].startswith('subpoint:'):
                recorded.add(unit)
                subpoint_minutes[unit] = subpoint_minutes.get(unit, 0) + credit['minutes']
        for point in points:
            if (point.get('structure') == 'concept_v1' and point.get('subpoints') and
                    subpoint_minutes.get(point['id'], 0) >= point['minutes']):
                taught.add(point['id'])
        return {'taught': taught, 'recorded': recorded}

    def today_status(self, plan_id, *, now, refresh, tz, classroom_day=None) -> dict:
        """Read one coherent course snapshot; no clock, writes or notifications.

        See course.today.build_today_status for the shared home/classroom wire
        fields. The caller supplies account time settings and, when applicable,
        the classroom's pinned entry study day.
        """
        from .today import build_today_status
        with self.transaction():
            plan = self._get('plans', plan_id, plan_id)
            ledger = self._load_ledger(plan)
            checked = self._fully_generated(plan_id) and self.load_mastery(plan_id).check_complete()
            return build_today_status(ledger, now=now, refresh=refresh, tz=tz,
                                      plan_state=plan['state'], check_complete=checked,
                                      classroom_day=classroom_day)

    def has_study_activity(self, plan_id, *, day, refresh, tz) -> bool:
        """Whether learning actually started on a study day (SYS-04).

        FIFO daily fills are not activity: today's work can pay yesterday's
        debt, and a failed test can remove all its tentative minutes. Original
        credit submissions survive revocation; judgements count even if wrong.
        Credits retain their pinned classroom day; judgement instants use the
        caller's current account refresh/zone. No clock or state is written.
        """
        from .ledger import study_day
        day = _day(day)
        with self.transaction():
            self._get('plans', plan_id, plan_id)
            if any(_day(row['credit']['day']) == day for row in self._rows('credits', plan_id)):
                return True
            return any((row.get('details') or {}).get('user_override') is not True and
                       study_day(datetime.fromisoformat(row['event']['at']), refresh, tz) == day
                       for row in self._rows('judgements', plan_id))

    def _append_mastery(self, plan_id, event, entity, details=None):
        self._get('plans', plan_id, plan_id)
        if not isinstance(event, Judgement if entity == 'judgements' else AttemptClosed): raise StoreError('INVALID_EVENT')
        book = self.load_mastery(plan_id)
        projection_details=details if isinstance(event,AttemptClosed) or (details or {}).get('user_override') is True else None
        book.apply(event,details=projection_details)
        payload = dict(id=event.event_id, event=event_to_dict(event), details=details or {})
        self._put(entity, plan_id, payload)

    @_atomic
    def append_judgement(self, plan_id, event: Judgement, *, details=None, credit_day=None):
        """Atomically record a judgement and earned final-check time.

        Classroom supplies its pinned study day. Each actual question answer
        earns two minutes, whether correct or wrong, including separator
        questions. Old events without a question ID earn no new final time.
        Retries of the same event cannot earn twice. Remote sync merges the
        resulting immutable judgement and credit events without regenerating them.
        """
        if not isinstance(event, Judgement): raise StoreError('INVALID_EVENT')
        existing = any(row['id'] == event.event_id for row in self._rows('judgements', plan_id))
        earned = []
        if credit_day is not None and event.context == 'check' and not existing:
            credit_day = _day(credit_day)
            prior = self.load_mastery(plan_id).to_dict()
            prior['events'] = [row for row in prior['events']
                               if (row['seq'], row['event_id']) < (event.seq, event.event_id)]
            book = MasteryBook.from_dict(prior)
            if event.question_id and book.check_ready():
                # Every answered question consumes time, including incorrect answers.
                earned.append((event.question_id,2))
        self._append_mastery(plan_id, event, 'judgements', details)
        for pid, minutes in earned:
            self.add_credit(plan_id, f'check:{event.event_id}:{pid}', f'check:{pid}', minutes, credit_day, 'confirmed')

    @_atomic
    def append_attempt_closed(self, plan_id, event: AttemptClosed, *, details=None):
        self._append_mastery(plan_id, event, 'attempt_closed', details)

    def _override_judgement(self, plan_id, question, grant_id, kind, *, extra_details=None):
        seq = max((row['event']['seq'] for entity in ('judgements','attempt_closed')
                   for row in self._rows(entity,plan_id)), default=0) + 1
        event_id=f'manual:{grant_id}:{question["id"]}'
        event=Judgement(event_id,seq,datetime.now(timezone.utc),'check',question['module_id'],
                        results=tuple((pid,True) for pid in question['point_ids']),question_id=question['id'])
        details=dict(user_override=True,override_kind=kind,grant_id=grant_id,module_id=question['module_id'])
        if extra_details:
            details.update(extra_details)
        self._append_mastery(plan_id,event,'judgements',details)
        return event_id

    @_atomic
    def grant_questions_as_passed(self, plan_id, module_id, question_ids, *, grant_id):
        """Record an explicit administrator mastery override, not an answer.

        The immutable Judgement envelope is tagged and projected separately;
        no learner answer fact, error history, check statistic, or credit is made.
        """
        _id(grant_id); self._get('modules',plan_id,module_id)
        ids=list(question_ids)
        if not ids or any(not isinstance(qid,str) or not qid for qid in ids) or len(ids)!=len(set(ids)):
            raise StoreError('INVALID_OVERRIDE')
        existing=[row for row in self._rows('judgements',plan_id)
                  if (row.get('details') or {}).get('user_override') is True and
                  (row.get('details') or {}).get('grant_id')==grant_id]
        if any((row.get('details') or {}).get('override_kind')!='question_passed' or
               (row.get('details') or {}).get('module_id')!=module_id or
               row['event'].get('module_id')!=module_id for row in existing):
            raise StoreError('OVERRIDE_CONFLICT')
        existing_ids={row.get('details',{}).get('question_id') or row['event'].get('question_id') for row in existing}
        if existing and existing_ids!=set(ids): raise StoreError('OVERRIDE_CONFLICT')
        from .question_bank import assessment_questions
        admitted={q['id']:q for q in assessment_questions(self.records('question_bank',plan_id),module_id)}
        questions=[]
        for qid in ids:
            q=admitted.get(qid)
            if q is None: raise StoreError('UNKNOWN_QUESTION')
            questions.append(q)
        prior=set(existing_ids)
        for q in questions:
            if q['id'] in prior:continue
            self._override_judgement(plan_id,q,grant_id,'question_passed')
        book=self.load_mastery(plan_id)
        return dict(id=grant_id,module_id=module_id,question_ids=ids,
                    newly_granted=[q['id'] for q in questions if q['id'] not in prior],
                    already_granted=[q['id'] for q in questions if q['id'] in prior],
                    module_state=book.module(module_id).state)

    def preview_questions_as_passed(self, plan_id, module_id, question_ids):
        """Read-only in-memory preview helper for the administrator utility."""
        self._get('modules',plan_id,module_id)
        from .question_bank import assessment_questions
        admitted={q['id']:q for q in assessment_questions(self.records('question_bank',plan_id),module_id)}
        ids=list(question_ids)
        if not ids or any(qid not in admitted for qid in ids):raise StoreError('UNKNOWN_QUESTION')
        return self.load_mastery(plan_id).preview_question_override(ids,kind='question_passed')

    @_atomic
    def grant_final_check_passed(self, plan_id, *, grant_id, today):
        """Explicitly grant every admitted final-check item, then use normal completion."""
        _id(grant_id)
        plan=self._get('plans',plan_id,plan_id)
        book=self.load_mastery(plan_id)
        if not book.check_ready():raise StoreError('CHECK_NOT_READY')
        from .question_bank import assessment_questions
        questions=assessment_questions(self.records('question_bank',plan_id))
        if not questions:raise StoreError('CHECK_NOT_READY')
        existing=[row for row in self._rows('judgements',plan_id)
                  if (row.get('details') or {}).get('user_override') is True and
                  (row.get('details') or {}).get('grant_id')==grant_id]
        if any((row.get('details') or {}).get('override_kind')!='final_check_passed' or
               (row.get('details') or {}).get('module_id')!=row['event'].get('module_id') for row in existing):
            raise StoreError('OVERRIDE_CONFLICT')
        existing_ids={row['event'].get('question_id') for row in existing}
        if existing and existing_ids!={q['id'] for q in questions}:raise StoreError('OVERRIDE_CONFLICT')
        for q in questions:
            if q['id'] not in existing_ids:self._override_judgement(plan_id,q,grant_id,'final_check_passed')
        self.complete_plan(plan_id,today=today)
        return dict(id=grant_id,state=self._get('plans',plan_id,plan_id)['state'],
                    completed_on=self._get('plans',plan_id,plan_id).get('completed_on'))

    @_atomic
    def grant_final_check_questions_except(self, plan_id, keep_question_id, *, grant_id):
        """Grant all currently unpassed final-check items except one preserved item.

        This is mastery metadata only: it appends the same immutable
        ``final_check_passed`` judgement override used by the operator grant,
        but deliberately does not complete the plan, save a report, add answer
        facts, or award study credits. The kept question must still be
        unpassed so the learner can finish the check normally.
        """
        _id(grant_id)
        plan = self._get('plans', plan_id, plan_id)
        from .question_bank import assessment_questions
        questions = assessment_questions(self.records('question_bank', plan_id))
        by_id = {question['id']: question for question in questions}
        keep = by_id.get(keep_question_id)
        if keep is None:
            raise StoreError('UNKNOWN_QUESTION', '保留题不属于当前最终检验题库。')
        existing = [row for row in self._rows('judgements', plan_id)
                    if (row.get('details') or {}).get('user_override') is True and
                    (row.get('details') or {}).get('grant_id') == grant_id]
        for row in existing:
            details = row.get('details') or {}
            event = row.get('event') or {}
            question = by_id.get(event.get('question_id'))
            if (details.get('override_kind') != 'final_check_passed' or
                    details.get('grant_scope') != 'except_question' or
                    details.get('keep_question_id') != keep_question_id or
                    question is None or event.get('module_id') != question.get('module_id') or
                    details.get('module_id') != event.get('module_id')):
                raise StoreError('OVERRIDE_CONFLICT', '授权编号已用于其他操作。')
        already_granted = {row['event'].get('question_id') for row in existing}
        eligible_ids = set(by_id) - {keep_question_id}
        if not already_granted <= eligible_ids:
            raise StoreError('OVERRIDE_CONFLICT', '已有授权与本次保留题不一致。')

        book = self.load_mastery(plan_id)
        if existing:
            changed_targets = [qid for qid in eligible_ids
                               if not book.question(qid).passed]
            if changed_targets:
                raise StoreError('GRANT_SCOPE_CHANGED',
                    '授权后出现了新的未通过题，或已有授权题再次变成未通过；请先核对进度，不能静默重复或扩大原授权。')
            # A repeat invocation remains a no-op after the kept question was
            # answered or the plan completed. Never undo that learner result.
            return dict(id=grant_id, keep_question_id=keep_question_id,
                        newly_granted=[], already_granted=sorted(already_granted),
                        preserved_passed=sorted(eligible_ids - already_granted),
                        check_ready=book.check_ready(), keep_passed=book.question(keep_question_id).passed,
                        already_applied=True)
        if plan.get('state') == 'completed':
            raise StoreError('PLAN_ALREADY_COMPLETED', '计划已经完成，拒绝改变既有成绩。')
        if not book.check_ready():
            raise StoreError('CHECK_NOT_READY', '仍有模块未通过或跳过。')
        if book.question(keep_question_id).passed:
            raise StoreError('KEEP_QUESTION_ALREADY_PASSED', '保留题已经通过；不会撤销或降级既有成绩。')

        # A repeat run sees earlier grants as passed; retain those event IDs
        # and only append items that remain unpassed for other reasons.
        newly_granted = []
        for question in questions:
            qid = question['id']
            if qid == keep_question_id or book.question(qid).passed or qid in already_granted:
                continue
            self._override_judgement(plan_id, question, grant_id, 'final_check_passed',
                extra_details=dict(grant_scope='except_question', keep_question_id=keep_question_id))
            newly_granted.append(qid)

        # Ensure this operation actually covers every other item; passed
        # learner results are preserved and need no synthetic override.
        after = self.load_mastery(plan_id)
        outstanding = [qid for qid in eligible_ids if not after.question(qid).passed]
        if outstanding:
            raise StoreError('GRANT_INCOMPLETE', '仍有其他最终检验题未通过，未完成授权。')
        return dict(id=grant_id, keep_question_id=keep_question_id,
                    newly_granted=newly_granted,
                    already_granted=sorted(already_granted),
                    preserved_passed=sorted(qid for qid in eligible_ids - already_granted
                                             if book.question(qid).passed),
                    check_ready=after.check_ready(), keep_passed=after.question(keep_question_id).passed,
                    already_applied=False)

    @_atomic
    def set_question_estimate(self, plan_id, module_id, count, *, today):
        """Replace the module's question reserve without changing generated content."""
        from .ledger import estimate_check_minutes
        _number(count)
        module=self._get('modules',plan_id,module_id)
        module.update(check_minutes=count*2,final_check_minutes=estimate_check_minutes(count,minutes_per_question=2),
                      check_question_minutes=2,question_count=count)
        self._put('modules',plan_id,module)
        plan=self._get('plans',plan_id,plan_id)
        if plan['window'] and plan['state']!='completed':
            self._replan(plan,_day(today),_window(plan['window']),self._total(plan_id),cause='question_estimate')
            self._put('plans',plan_id,plan)

    def _release_estimate(self, plan_id, key, minutes, today):
        identifier = f'release:{key}'
        if any(row['id'] == identifier for row in self._rows('ledger_events', plan_id)):
            return
        self._put('ledger_events', plan_id, dict(id=identifier, op='release', minutes=minutes,
            today=_day(today).isoformat(), operation_seq=self._op_seq(plan_id)))

    @_atomic
    def settle_module_test(self, plan_id, module_id, attempt_id, *, today):
        """Settle a closed detection once; old modules keep their old estimate.

        A detection question counts whether right or wrong. Final checks have
        their separate per-question rule. Only the first closed attempt can
        release the module's reserve; additional attempts record actual work.
        """
        module = self._get('modules', plan_id, module_id)
        closed = [row['event'] for row in self._rows('attempt_closed', plan_id)
                  if row['event']['module_id'] == module_id and row['event']['attempt_id'] == attempt_id]
        if not closed: raise StoreError('ATTEMPT_NOT_CLOSED')
        answered = {row['id'] for row in self._rows('question_answers', plan_id)
                    if row['module_id'] == module_id and row['attempt_id'] == attempt_id
                    and row['context'] == closed[0]['kind']}
        # Saved plans before v5 only have judgements. Union by answer/event ID
        # avoids double counting the new fact and its accompanying judgement.
        answered.update(row['id'] for row in self._rows('judgements', plan_id)
                        if row['event']['module_id'] == module_id and row['event']['attempt_id'] == attempt_id
                        and row['event']['context'] == closed[0]['kind'] and row['event']['seq'] < closed[0]['seq'])
        question_count = len(answered)
        _number(question_count, True)
        minutes = (question_count * module['check_question_minutes'] if 'final_check_minutes' in module
                   else max(1, module['check_minutes']))
        self.add_credit(plan_id, f'test:{attempt_id}', f'test:{module_id}', minutes, today, 'confirmed')
        if 'final_check_minutes' in module:
            unused = max(0, module['check_minutes'] - minutes)
            self._release_estimate(plan_id, f'test:{module_id}', unused, today)

    @_atomic
    def add_credit(self, plan_id, credit_id, unit_id=None, minutes=None, day=None, state='tentative'):
        if isinstance(credit_id, Credit):
            c = credit_id; credit_id, unit_id, minutes, day, state = c.credit_id, c.unit_id, c.minutes, c.day, c.state
        return self._add_credit_record(plan_id, credit_id, unit_id, minutes, day, state,
                                       allow_recovery=False)

    @_atomic
    def add_restored_learning(self, plan_id, credit_prefix, unit_id, minutes, recovery_day,
                              state='confirmed', *, allow_current_remainder=False):
        """Append source-day recovery credits derived by the ledger itself.

        Concept-test recovery must be fully backed by revoked learning sources.
        Direct module completion may additionally earn the point's genuinely
        unlearned remainder on ``recovery_day``.
        """
        plan = self._get('plans', plan_id, plan_id)
        ledger = self._load_ledger(plan)
        restored = ledger.add_restored_learning(
            credit_prefix, unit_id, minutes, _day(recovery_day), state,
            allow_current_remainder=allow_current_remainder)
        for credit in restored:
            self._add_credit_record(plan_id, credit.credit_id, credit.unit_id,
                                    credit.minutes, credit.day, credit.state,
                                    allow_recovery=True)
        return [dict(credit_id=c.credit_id, day=c.day.isoformat(), minutes=c.minutes)
                for c in restored]

    def _add_credit_record(self, plan_id, credit_id, unit_id, minutes, day, state,
                           *, allow_recovery):
        plan = self._get('plans', plan_id, plan_id)
        if state not in ('tentative', 'confirmed'): raise StoreError('INVALID_CREDIT')
        existing = next((p for p in self._rows('credits', plan_id) if p['id'] == credit_id), None)
        if credit_id.startswith(('relearn:concept:', 'relearn:direct:')) and not allow_recovery:
            if existing is None:
                raise StoreError('LEARNING_SOURCE_REQUIRED', '恢复学习时间必须由账本按原来源日生成。')
            requested = dict(credit_id=credit_id, unit_id=unit_id, minutes=minutes,
                             day=_day(day).isoformat(), state=state)
            if existing['credit'] != requested:
                raise StoreError('CREDIT_CONFLICT')
            return self._put('credits', plan_id, existing)
        ledger = self._load_ledger(plan)
        if allow_recovery:
            ledger._add_unchecked(credit_id, unit_id, minutes, _day(day), state)
        else:
            method = ledger.add_confirmed if state == 'confirmed' else ledger.add_tentative
            method(credit_id, unit_id, minutes, _day(day))
        original = next(c for c in ledger.to_dict()['submissions'] if c['credit_id'] == credit_id)
        if existing:
            return self._put('credits', plan_id, existing)
        sequence = existing['operation_seq'] if existing else self._op_seq(plan_id)
        self._put('credits', plan_id, dict(id=credit_id, credit=original, operation_seq=sequence))

    def _credit_change(self, plan_id, unit_id, event_id, op, today):
        identifier = _id(event_id or uuid.uuid4().hex)
        existing = next((p for p in self._rows('ledger_events', plan_id) if p['id'] == identifier), None)
        if existing:
            if existing['op'] != op or existing.get('unit_id') != unit_id: raise StoreError('EVENT_CONFLICT')
            if today is not None and existing.get('today') != _day(today).isoformat(): raise StoreError('EVENT_CONFLICT')
            return 0
        ledger = self._load_ledger(self._get('plans', plan_id, plan_id))
        minutes = getattr(ledger, op)(unit_id)
        payload = dict(id=identifier, op=op, unit_id=unit_id, operation_seq=self._op_seq(plan_id))
        if today is not None: payload['today'] = _day(today).isoformat()
        self._put('ledger_events', plan_id, payload)
        return minutes

    @_atomic
    def confirm_credits(self, plan_id, unit_id, *, event_id=None, today=None):
        return self._credit_change(plan_id, unit_id, event_id, 'confirm', today)

    @_atomic
    def revoke_credits(self, plan_id, unit_id, *, event_id=None, today=None):
        return self._credit_change(plan_id, unit_id, event_id, 'revoke', today)

    @_atomic
    def save_teacher_profile(self, plan_id, document):
        self._get('plans', plan_id, plan_id)
        # Background profile and preparation finish independently. Preserve the
        # synced lesson cache even when a profile request began before prep saved.
        try:
            previous = self._get('teacher_profile', plan_id, plan_id)['document']
        except StoreError as exc:
            if exc.code != 'NOT_FOUND': raise
            previous = {}
        for key in ('lesson_preps', 'prepared_tests'):
            if key in previous:
                document = dict(document, **{key: previous[key]})
        self._put('teacher_profile', plan_id, dict(id=plan_id, document=document))

    def load_lesson_cache(self, plan_id):
        with self.transaction():
            try:
                document = self._get('teacher_profile', plan_id, plan_id)['document']
            except StoreError as exc:
                if exc.code != 'NOT_FOUND': raise
                return {'lesson_preps': {}, 'prepared_tests': {}}
            return {key: dict(document.get(key) or {}) for key in ('lesson_preps', 'prepared_tests')}

    @_atomic
    def save_lesson_cache_item(self, plan_id, kind, item_id, value, *, keep_ids=None):
        if kind not in ('lesson_preps', 'prepared_tests'):
            raise StoreError('INVALID_LESSON_CACHE')
        self._get('plans', plan_id, plan_id)
        try:
            document = self._get('teacher_profile', plan_id, plan_id)['document']
        except StoreError as exc:
            if exc.code != 'NOT_FOUND': raise
            document = {}
        document = dict(document)
        if keep_ids is not None:
            document[kind] = {key: row for key, row in (document.get(kind) or {}).items() if key in keep_ids}
        cache = dict(document.get(kind) or {})
        cache[_id(item_id)] = value
        document[kind] = cache
        self._put('teacher_profile', plan_id, dict(id=plan_id, document=document))

    @_atomic
    def prune_lesson_cache(self, plan_id, *, point_ids, module_ids):
        try:
            document = self._get('teacher_profile', plan_id, plan_id)['document']
        except StoreError as exc:
            if exc.code == 'NOT_FOUND': return
            raise
        document = dict(document)
        changed = False
        for kind, keep in (('lesson_preps', set(point_ids)), ('prepared_tests', set(module_ids))):
            old = document.get(kind) or {}
            retained = {key: row for key, row in old.items() if key in keep}
            if retained != old:
                document[kind] = retained
                changed = True
        if changed:
            self._put('teacher_profile', plan_id, dict(id=plan_id, document=document))

    @_atomic
    def append_message(self, plan_id, module_id, role, content, *, message_id=None, kind='text', at=None):
        self._get('plans', plan_id, plan_id)
        if module_id is not None: self._get('modules', plan_id, module_id)
        identifier = _id(message_id or uuid.uuid4().hex)
        payload = dict(id=identifier, module_id=module_id, role=role, content=content, kind=kind,
                       at=_at(at), compressed=False, summary_of=[])
        self._put('messages', plan_id, payload)
        return identifier

    @_atomic
    def compress_messages(self, plan_id, module_id, message_ids, summary, *, summary_id=None, at=None):
        if not message_ids or len(set(message_ids)) != len(message_ids): raise StoreError('INVALID_COMPRESSION')
        if not isinstance(summary, str) or not summary.strip(): raise StoreError('EMPTY_SUMMARY')
        messages = [self._get('messages', plan_id, identifier) for identifier in message_ids]
        if any(m['module_id'] != module_id or m['compressed'] for m in messages): raise StoreError('INVALID_COMPRESSION')
        identifier = _id(summary_id or uuid.uuid4().hex)
        if any(m['id'] == identifier for m in self._rows('messages', plan_id)): raise StoreError('MESSAGE_EXISTS')
        for message in messages:
            message['compressed'] = True; self._put('messages', plan_id, message)
        self._put('messages', plan_id, dict(id=identifier, module_id=module_id, role='system', content=summary,
                                          kind='summary', at=_at(at), compressed=False, summary_of=list(message_ids)))
        return identifier

    def get_message(self, plan_id, message_id):
        with self._lock:
            return self._get('messages', plan_id, message_id)

    def messages(self, plan_id, module_id=None, *, include_compressed=False, limit=None):
        """Read one channel using messages_order; a limit returns its latest rows.

        The default retains all rows of that channel for dialogue page-boundary
        replay. JSON is decoded only after SQL has filtered the channel/window.
        """
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError('limit must be a non-negative integer')
        query = 'SELECT payload FROM messages WHERE plan_key=? AND module_id IS ?'
        params = [plan_id, module_id]
        if not include_compressed: query += " AND json_extract(payload, '$.compressed')=0"
        direction = 'DESC' if limit is not None else 'ASC'
        query += f" ORDER BY json_extract(payload, '$.at') {direction}, id {direction}"
        if limit is not None:
            query += ' LIMIT ?'; params.append(limit)
        with self._lock:
            values = [json.loads(row[0]) for row in self._db.execute(query, params)]
        if limit is not None: values.reverse()
        return values

    @_atomic
    def save_report(self, plan_id, document, *, report_id=None):
        """Save a report atomically, splitting large JSON across reports rows.

        Small reports retain the original document envelope. Large reports use
        a manifest and content-addressed base64 parts (128 KiB raw each), still
        one reports entity per row and <=256 KiB per sync record. Use load_report
        to read either form; records('reports') remains the raw row-level API.
        """
        plan = self._get('plans', plan_id, plan_id)
        if plan['state'] != 'completed': raise StoreError('PLAN_NOT_COMPLETE')
        identifier = _id(report_id or plan_id)
        if identifier.startswith(_REPORT_PART_PREFIX): raise StoreError('INVALID_REPORT_ID')
        if not isinstance(document, dict) or _REPORT_STORAGE in document: raise StoreError('INVALID_DOCUMENT')
        raw = _json(document, bounded=False).encode('utf-8')
        try:
            previous = self._get('reports', plan_id, identifier)['document']
        except StoreError as exc:
            if exc.code != 'NOT_FOUND': raise
            previous = {}
        old_parts = previous.get('parts', []) if previous.get(_REPORT_STORAGE) == 'chunks.v1' else []
        parts = []
        envelope = dict(id=identifier, document=document)
        # Include the row envelope in the single-record limit, not just document.
        if len(_json(envelope, bounded=False).encode('utf-8')) > MAX_PAYLOAD:
            digest = hashlib.sha256(raw).hexdigest()
            owner_digest = hashlib.sha256(identifier.encode('utf-8') + b'\0' + raw).hexdigest()
            for index, offset in enumerate(range(0, len(raw), 128 * 1024)):
                part_id = f'{_REPORT_PART_PREFIX}{owner_digest}:{index}'
                parts.append(part_id)
                self._put('reports', plan_id, dict(id=part_id, document={
                    _REPORT_STORAGE: 'part.v1', 'report_id': identifier, 'index': index,
                    'data': base64.b64encode(raw[offset:offset + 128 * 1024]).decode('ascii')}))
            envelope['document'] = {_REPORT_STORAGE: 'chunks.v1', 'sha256': digest, 'parts': parts}
        self._put('reports', plan_id, envelope)
        for old_id in old_parts:
            if old_id in parts: continue
            try: old = self._get('reports', plan_id, old_id)['document']
            except StoreError as exc:
                if exc.code != 'NOT_FOUND': raise
                continue
            if old.get(_REPORT_STORAGE) == 'part.v1' and old.get('report_id') == identifier:
                self._delete('reports', plan_id, old_id)
        return identifier

    def load_report(self, plan_id, *, report_id=None):
        """Read a complete detached report; fail explicitly during partial sync."""
        with self.transaction():
            identifier = _id(report_id or plan_id)
            document = self._get('reports', plan_id, identifier)['document']
            if _REPORT_STORAGE not in document:
                result = document
            else:
                if document[_REPORT_STORAGE] != 'chunks.v1': raise StoreError('INVALID_REPORT')
                try:
                    parts = []
                    ids = document['parts']
                    if (not isinstance(ids, list) or not ids
                            or any(not isinstance(key, str) or not key.startswith(_REPORT_PART_PREFIX) for key in ids)
                            or len(ids) != len(set(ids))): raise ValueError()
                    for index, part_id in enumerate(ids):
                        part = self._get('reports', plan_id, part_id)['document']
                        if (part.get(_REPORT_STORAGE) != 'part.v1' or part.get('report_id') != identifier
                                or type(part.get('index')) is not int or part['index'] != index): raise ValueError()
                        parts.append(base64.b64decode(part['data'], validate=True))
                    raw = b''.join(parts)
                    if hashlib.sha256(raw).hexdigest() != document['sha256']: raise ValueError()
                    result = json.loads(raw.decode('utf-8'))
                    if not isinstance(result, dict) or _REPORT_STORAGE in result: raise ValueError()
                    if _json(result, bounded=False).encode('utf-8') != raw: raise ValueError()
                except (StoreError, ValueError, TypeError, KeyError, UnicodeError):
                    raise StoreError('REPORT_INCOMPLETE', '报告分块尚未全部同步或校验失败。') from None
            return self._project_report_learning_days(plan_id, result)

    def _project_report_learning_days(self, plan_id, document):
        """Apply legacy source-day display compatibility without rewriting reports."""
        timing = document.get('time') if isinstance(document, dict) else None
        if not isinstance(timing, dict) or not isinstance(timing.get('days'), list):
            return document
        try:
            ledger = self._load_ledger(self._get('plans', plan_id, plan_id))
        except StoreError as exc:
            if exc.code in ('NOT_FOUND', 'PLAN_NOT_READY'): return document
            raise
        if not ledger.to_dict().get('learning_day_projection'):
            return document
        from .report import reproject_cached_time_series
        result = dict(document)
        result['time'] = reproject_cached_time_series(timing, ledger)
        return result

    @_atomic
    def append_usage(self, plan_id, usage_id, *, provider, model, purpose, input_tokens=0, cache_hit_tokens=0,
                     cache_miss_tokens=0, output_tokens=0, search_count=0):
        self._get('plans', plan_id, plan_id)
        self._put('usage', plan_id, dict(id=usage_id, provider=provider, model=model, purpose=purpose,
            input_tokens=input_tokens, cache_hit_tokens=cache_hit_tokens, cache_miss_tokens=cache_miss_tokens,
            output_tokens=output_tokens, search_count=search_count))

    @_atomic
    def create_chat_session(self, title='', *, session_id=None, created_at=None):
        identifier = _id(session_id or uuid.uuid4().hex)
        if self._db.execute("SELECT 1 FROM _records WHERE entity='chat_sessions' AND entity_id=?",
                            (_wire_id('chat_sessions', '', identifier),)).fetchone():
            raise StoreError('CHAT_EXISTS')
        payload = dict(id=identifier, title=title, archived=False)
        if created_at is not None:
            payload.update(created_at=_at(created_at), updated_at=_at(created_at))
        self._put('chat_sessions', '', payload)
        return identifier

    def get_chat_session(self, session_id):
        with self._lock: return self._get('chat_sessions', '', session_id)

    @_atomic
    def set_chat_session_archived(self, session_id, archived):
        """Archive or restore one account-scoped chat session."""
        if type(archived) is not bool: raise StoreError('INVALID_SESSION')
        payload = self._get('chat_sessions', '', session_id)
        payload['archived'] = archived
        return self._put('chat_sessions', '', payload)

    @_atomic
    def touch_chat_session(self, session_id, *, title, updated_at):
        payload = self._get('chat_sessions', '', session_id)
        payload.update(title=title, updated_at=_at(updated_at))
        self._put('chat_sessions', '', payload)

    @_atomic
    def rename_chat_session(self, session_id, title):
        payload = self._get('chat_sessions', '', session_id); payload['title'] = title
        self._put('chat_sessions', '', payload)

    @_atomic
    def append_chat_message(self, session_id, role, content, *, message_id=None, kind='text', at=None):
        self._get('chat_sessions', '', session_id)
        identifier = _id(message_id or uuid.uuid4().hex)
        self._put('chat_messages', '', dict(id=identifier, session_id=session_id, role=role, content=content,
            kind=kind, at=_at(at), compressed=False, summary_of=[]))
        return identifier

    def chat_messages(self, session_id):
        with self._lock:
            return [json.loads(row[0]) for row in self._db.execute("""SELECT payload FROM chat_messages
                WHERE plan_key='' AND session_id=? ORDER BY json_extract(payload,'$.at'),id""", (session_id,))]

    def legacy_chat_imported(self, key='complete'):
        with self._lock:
            return self._db.execute('SELECT 1 FROM _meta WHERE key=?', ('legacy_chat_v1:' + key,)).fetchone() is not None

    @_atomic
    def mark_legacy_chat_imported(self, key='complete'):
        self._db.execute('INSERT OR IGNORE INTO _meta VALUES(?,?)', ('legacy_chat_v1:' + key, '1'))

    @_atomic
    def import_chat_session(self, source_key, session, messages):
        """Import one legacy file with its local marker in the same transaction.

        Never overwrite an already synced ID, especially a remote tombstone.
        Deterministic IDs are provided by the legacy reader, not allocated here.
        """
        if self.legacy_chat_imported(source_key): return
        existing = self._db.execute("SELECT deleted FROM _records WHERE entity='chat_sessions' AND entity_id=?",
                                    (_wire_id('chat_sessions', '', _id(session['id'])),)).fetchone()
        if existing and existing['deleted']:
            self.mark_legacy_chat_imported(source_key)
            return
        session = dict(session)
        session.setdefault('archived', False)
        for entity, payload in [('chat_sessions', session), *[('chat_messages', m) for m in messages]]:
            wire = _wire_id(entity, '', _id(payload['id']))
            if not self._db.execute('SELECT 1 FROM _records WHERE entity=? AND entity_id=?', (entity, wire)).fetchone():
                self._put(entity, '', payload)
        self.mark_legacy_chat_imported(source_key)

    @_atomic
    def delete_chat_session(self, session_id):
        self._get('chat_sessions', '', session_id)
        for message in self.chat_messages(session_id): self._delete('chat_messages', '', message['id'])
        self._delete('chat_sessions', '', session_id)

    def put_original(self, data: bytes, sha256=None) -> str:
        """Atomic content-addressed original bytes. Never added to the outbox."""
        if not isinstance(data, bytes): raise StoreError('INVALID_FILE')
        digest = hashlib.sha256(data).hexdigest()
        if sha256 is not None and sha256 != digest: raise StoreError('SHA_MISMATCH')
        directory = self.root / 'files'; self._safe_path(directory)
        directory.mkdir(exist_ok=True)
        target = directory / digest; self._safe_path(target)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest: raise StoreError('CORRUPT_ORIGINAL')
            return digest
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix='.original-', delete=False) as stream:
                temporary = Path(stream.name); stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
        return digest

    def get_original(self, sha256) -> bytes | None:
        if not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256): raise StoreError('INVALID_SHA')
        directory = self.root / 'files'; self._safe_path(directory)
        target = directory / sha256; self._safe_path(target)
        if not target.exists(): return None
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256: raise StoreError('CORRUPT_ORIGINAL')
        return data

    def pending_count(self) -> int:
        """Count the live outbox without loading student record payloads."""
        with self._lock:
            return self._db.execute('SELECT count(*) FROM _records WHERE pending=1').fetchone()[0]

    def get_sync_success_at(self):
        with self._lock:
            row = self._db.execute("SELECT value FROM _meta WHERE key='sync_success_at'").fetchone()
            if row is None:
                return None
            try:
                return _at(row[0])
            except (ValueError, StoreError):
                return None

    @_atomic
    def set_sync_success_at(self, value):
        # Device-local status only: no schema change, record revision or upload.
        self._db.execute("INSERT INTO _meta(key,value) VALUES('sync_success_at',?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_at(value),))

    def pending_records(self, limit) -> list[SyncRecord]:
        _number(limit)
        with self._lock:
            return [SyncRecord(row['entity'], row['entity_id'], row['plan_key'], f"mentorflow.course.{row['entity']}.v1",
                               json.loads(row['payload']), bool(row['deleted']), row['local_revision'])
                    for row in self._db.execute('SELECT * FROM _records WHERE pending=1 ORDER BY local_revision,entity,entity_id LIMIT ?', (limit,))]

    @_atomic
    def mark_synced(self, entity, entity_id, local_revision, server_revision) -> None:
        _number(local_revision); _number(server_revision)
        # Acknowledgement can advance server knowledge even if local editing
        # raced it. Only the exact submitted local revision loses its pending bit.
        self._db.execute('''UPDATE _records SET server_revision=MAX(server_revision,?),
            pending=CASE WHEN local_revision=? THEN 0 ELSE pending END WHERE entity=? AND entity_id=?''',
                         (server_revision, local_revision, entity, entity_id))

    @_atomic
    def mark_synced_many(self, acknowledgements) -> None:
        """Acknowledge one sync round in a single outbox transaction.

        This is an optional CourseStore acceleration for CourseSync.  The
        four-argument ``mark_synced`` method remains the RecordStore contract,
        and each row still compares the submitted local revision so an edit
        made while uploading remains pending.
        """
        rows = list(acknowledgements)
        if not rows:
            return
        for row in rows:
            if not isinstance(row, (tuple, list)) or len(row) != 4:
                raise StoreError('INVALID_ACKNOWLEDGEMENT')
            entity, entity_id, local_revision, server_revision = row
            _number(local_revision); _number(server_revision)
            if not isinstance(entity, str) or not isinstance(entity_id, str):
                raise StoreError('INVALID_ACKNOWLEDGEMENT')
        self._db.executemany('''UPDATE _records SET server_revision=MAX(server_revision,?),
            pending=CASE WHEN local_revision=? THEN 0 ELSE pending END WHERE entity=? AND entity_id=?''',
                         [(server_revision, local_revision, entity, entity_id)
                          for entity, entity_id, local_revision, server_revision in rows])

    def sync_cursor(self) -> str:
        with self._lock: return self._db.execute("SELECT value FROM _meta WHERE key='cursor'").fetchone()[0]

    @_atomic
    def set_sync_cursor(self, cursor: str) -> None:
        if not isinstance(cursor, str): raise StoreError('INVALID_CURSOR')
        self._db.execute("UPDATE _meta SET value=? WHERE key='cursor'", (cursor,))

    def _project_records(self):
        # Journal ingestion and projection happen in the same transaction. FK
        # deferral permits rebuilding while preserving final referential checks.
        self._db.execute('DELETE FROM point_sources')
        for entity in reversed(tuple(PARENTS)):
            self._db.execute(f'DELETE FROM {entity}')
        pending = list(self._db.execute('SELECT * FROM _records WHERE deleted=0 ORDER BY entity,entity_id'))
        while pending:
            deferred = []
            for row in pending:
                entity, plan = row['entity'], row['plan_key']
                payload = json.loads(row['payload'])
                if entity not in ('plans', 'chat_sessions', 'chat_messages', 'account_settings'):
                    if not self._db.execute('SELECT 1 FROM plans WHERE id=?', (plan,)).fetchone():
                        deferred.append(row); continue
                missing = False
                for name, parent in PARENTS[entity]:
                    if payload.get(name) is not None and not self._db.execute(f'SELECT 1 FROM {parent} WHERE plan_key=? AND id=?', (plan, payload[name])).fetchone():
                        missing = True
                if entity == 'points':
                    missing |= any(not self._db.execute('SELECT 1 FROM source_chunks WHERE plan_key=? AND id=?', (plan, cid)).fetchone() for cid in payload['chunk_ids'])
                if missing: deferred.append(row); continue
                try:
                    self._validate_concept_fields(entity, plan, payload)
                except StoreError as exc:
                    if exc.code == 'NOT_FOUND':
                        deferred.append(row); continue
                    raise
                self._materialize(entity, plan, payload, row['local_revision'])
            if len(deferred) == len(pending): break
            pending = deferred

    @_atomic
    def apply_remote(self, records: list[tuple[SyncRecord, int]]) -> None:
        for record, server_revision in records:
            if not isinstance(record, SyncRecord): raise StoreError('INVALID_RECORD')
            _number(server_revision); _number(record.local_revision)
            if type(record.deleted) is not bool or not isinstance(record.plan_key, str) or not isinstance(record.payload, dict): raise StoreError('INVALID_RECORD')
            if record.entity not in PARENTS:
                # An unknown but coherent future record is harmless. Keep the
                # existing rejection of a known schema with a forged entity.
                if not isinstance(record.schema,str) or not re.fullmatch(r'mentorflow\.course\.'+re.escape(record.entity)+r'\.v[0-9]{1,3}',record.schema):
                    raise StoreError('UNSUPPORTED_RECORD_SCHEMA')
                _json(record.payload)
                logging.getLogger(__name__).warning('course sync skipped unknown entity: %s', record.entity)
                continue
            if record.schema != f'mentorflow.course.{record.entity}.v1': raise StoreError('UNSUPPORTED_RECORD_SCHEMA')
            payload = json.loads(_json(record.payload))
            if record.entity_id != _wire_id(record.entity, record.plan_key, _id(payload['id'])): raise StoreError('INVALID_RECORD_ID')
            if record.entity in ('chat_sessions', 'chat_messages', 'account_settings') and record.plan_key: raise StoreError('INVALID_RECORD_SCOPE')
            if record.entity not in ('chat_sessions', 'chat_messages', 'account_settings'): _id(record.plan_key)
            old = self._db.execute('SELECT * FROM _records WHERE entity=? AND entity_id=?', (record.entity, record.entity_id)).fetchone()
            repair_archive = False
            if record.entity == 'chat_sessions' and not record.deleted and 'archived' not in payload:
                # Older clients omit this field. Absence is not a restore:
                # preserve local archive state whether or not this row is pending.
                local_session = json.loads(old['payload']) if old and not old['deleted'] else {}
                payload['archived'] = local_session.get('archived', False)
                repair_archive = bool(local_session.get('archived', False))
                local_versions = local_session.get(_FIELD_VERSIONS, {})
                if 'archived' in local_versions:
                    remote_versions = dict(payload.get(_FIELD_VERSIONS, {}))
                    remote_versions['archived'] = local_versions['archived']
                    payload[_FIELD_VERSIONS] = remote_versions
            if record.entity in PROGRESS:
                if record.deleted: continue  # Never erase even an already-synced event.
                self._validate(record.entity, record.plan_key, payload, references=False)
                if record.entity in ('judgements', 'attempt_closed'):
                    other = 'attempt_closed' if record.entity == 'judgements' else 'judgements'
                    if self._db.execute('SELECT 1 FROM _records WHERE entity=? AND entity_id=?', (other, record.entity_id)).fetchone():
                        raise StoreError('EVENT_CONFLICT')
                if old:
                    if old['payload'] != _json(payload): raise StoreError('EVENT_CONFLICT')
                    self._db.execute('UPDATE _records SET server_revision=MAX(server_revision,?) WHERE entity=? AND entity_id=?',
                                     (server_revision, record.entity, record.entity_id))
                    continue  # A remote echo is not an upload acknowledgement.
            elif old and server_revision <= old['server_revision']:
                continue
            if not record.deleted and record.entity == 'question_bank':
                payload = self._retire_legacy_question(payload)
            if not record.deleted:
                # Validate the actual remote record before choosing local fields;
                # otherwise a malformed remote edit could be silently hidden.
                self._validate(record.entity, record.plan_key, payload, references=False)
            repair_answered = False
            uses_union = None
            if (record.entity == 'question_bank' and not record.deleted and old is not None and not old['deleted']
                    and payload.get('role')!='retired' and json.loads(old['payload']).get('role')!='retired'):
                local_uses = json.loads(old['payload']).get('uses', [])
                uses_union = [dict(purpose=purpose, at=at) for purpose, at in sorted(
                    {(u['purpose'], u['at']) for u in local_uses + payload.get('uses', [])})]
            merged = False
            if record.entity == 'classroom_positions' and old is not None and not old['deleted'] and not record.deleted:
                # The whole scene is one versioned field; do not splice a new
                # module onto an old quiz. Compare even acknowledged scenes:
                # another device may upload an older legacy-file import later.
                local = json.loads(old['payload'])
                if local.get(_FIELD_VERSIONS,{}).get('state',[0,'']) > payload.get(_FIELD_VERSIONS,{}).get('state',[0,'']):
                    payload = local
                    merged = old['payload'] != _json(record.payload) or bool(old['pending'])
                deleted = False
            elif old is not None and old['pending'] and record.entity not in PROGRESS and not record.deleted:
                local = json.loads(old['payload'])
                if old['deleted']:
                    # A pending local removal cannot be undone by a remote edit.
                    payload, deleted = local, True
                elif old['payload'] != _json(payload):
                    local_versions = local.get(_FIELD_VERSIONS, {})
                    remote_versions = payload.get(_FIELD_VERSIONS, {})
                    combined = {}
                    absent = object()
                    for field in (set(local) | set(payload)) - {_FIELD_VERSIONS}:
                        local_value, remote_value = local.get(field, absent), payload.get(field, absent)
                        if local_value == remote_value:
                            chosen = local_value
                        elif field == 'id' or (local_versions.get(field, [0, '']) > remote_versions.get(field, [0, ''])):
                            chosen = local_value
                        else:
                            chosen = remote_value
                        if chosen is not absent: combined[field] = chosen
                    combined[_FIELD_VERSIONS] = {**remote_versions, **{
                        field: max(local_versions.get(field, [0, '']), remote_versions.get(field, [0, '']))
                        for field in set(local_versions) | set(remote_versions)}}
                    if record.entity == 'points' and (
                            local.get('structure') == 'concept_v1' or payload.get('structure') == 'concept_v1'):
                        # Source union, child minutes and goal equality describe
                        # one structure. Field-wise splicing can make two valid
                        # snapshots invalid (e.g. new parent refs plus old notes).
                        fields = ('structure', 'summary', 'goal', 'minutes', 'chunk_ids', 'subpoints')
                        def versioned_structure(row, versions):
                            data = {field: row[field] for field in fields if field in row}
                            stamp = max((versions.get(field, [0, '']) for field in fields))
                            return stamp, _json(data), data
                        stamp, _, structure = max(versioned_structure(local, local_versions),
                                                   versioned_structure(payload, remote_versions), key=lambda v: v[:2])
                        for field in fields:
                            if field in structure: combined[field] = structure[field]
                            else: combined.pop(field, None)
                            combined[_FIELD_VERSIONS][field] = stamp
                    payload = combined
                    deleted = False
                else:
                    deleted = False
                merged = True
                if old['payload'] != _json(record.payload):
                    self._db.execute("INSERT INTO _meta(key,value) VALUES('merge_notices','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
            else:
                deleted = record.deleted
            if uses_union is not None and uses_union != payload.get('uses'):
                payload['uses'] = uses_union
                repair_answered = True
            if not deleted and record.entity == 'question_bank':
                payload = self._retire_legacy_question(payload)
            if not deleted: self._validate(record.entity, record.plan_key, payload, references=False)
            if record.entity == 'plans' and payload['id'] != record.plan_key: raise StoreError('INVALID_RECORD_SCOPE')
            self._db.execute('''INSERT INTO _records VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(entity,entity_id)
                DO UPDATE SET payload=excluded.payload,deleted=excluded.deleted,local_revision=excluded.local_revision,
                server_revision=excluded.server_revision,pending=excluded.pending''',
                             (record.entity, record.entity_id, record.plan_key, _json(payload), int(deleted),
                              self._revision(), server_revision, int(repair_answered or merged or repair_archive)))
            if old is None or old['deleted'] != int(deleted) or old['payload'] != _json(payload):
                self._changed('remote', record.entity)
        self._project_records()
        self._prune_point_links()
        for plan_id, in self._db.execute('SELECT id FROM plans').fetchall():
            self._upgrade_material_plan(plan_id)

    def needs_sync_replay(self, entity: str, entity_id: str) -> bool:
        """True when this version understands a journal identity it never stored."""
        if entity not in PARENTS:
            return False
        with self._lock:
            return self._db.execute(
                'SELECT 1 FROM _records WHERE entity=? AND entity_id=?',
                (entity, entity_id),
            ).fetchone() is None
