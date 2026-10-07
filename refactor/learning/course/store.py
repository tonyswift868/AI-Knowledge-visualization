"""Account-owned SQLite course data and an atomic record outbox, schema v5.

Each business table stores a canonical, versioned JSON payload plus indexed FK
columns and a local revision. Payload fields (also validated below):
plans: intake, window/initial_window, total/initial_total, state, archived,
optional started_on/completed_on (explicit caller-supplied study dates);
sources: kind, url, sha256, title, site, tier, credibility, adopted;
source_chunks: extract chunk_id as id, source_id, position, text, pages, section;
stages/modules/points: title/summary, position, prerequisites, minute estimates,
and nonempty chunk_ids for every point; teacher_profile/reports: document;
messages/chat_messages: role, content, kind, at, compressed, summary_of.
question_bank: original-source question payload (question_bank.validate_question),
source/answer_source locators and append-only purpose/time uses.
question_answers: immutable answer ID, question/point IDs, context, attempt,
angle, verdict and time; composites count for detection without mastery events.
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
records use an empty plan_key. All wire schemas are mentorflow.course.<entity>.v1.

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
from .ledger import Ledger, PlanWindow, Credit
from .mastery import MasteryBook, PointRef, Judgement, AttemptClosed, event_to_dict, event_from_dict
from .sync_contract import SyncRecord

SCHEMA_VERSION = 5
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
    'question_bank': (('module_id', 'modules'),), 'practice_results': (),
    'question_answers': (),  # immutable answered-question facts, independent of point judgements
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
                if entity in ('plans', 'question_bank', 'practice_results', 'question_answers'): continue
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
        owner = self._db.execute("SELECT value FROM _meta WHERE key='account_id'").fetchone()
        if owner is None or owner[0] != self.account_id: raise StoreError('ACCOUNT_MISMATCH')

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

    def _validate(self, entity, plan, payload):
        _id(payload['id'])
        if entity == 'plans':
            if payload['id'] != plan or payload['state'] not in STATES or type(payload['archived']) is not bool: raise StoreError('INVALID_PLAN')
            for key in ('goal', 'final_goal', 'level', 'notes'):
                if not isinstance(payload[key], str): raise StoreError('INVALID_PLAN')
            for key in ('window', 'initial_window'):
                if payload[key] is not None: _window(payload[key])
            _number(payload['total_minutes']); _number(payload['initial_total_minutes'])
            _number(payload['module_count'])
            for key in ('started_on', 'completed_on'):
                if payload.get(key) is not None: _day(payload[key])
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
                if not isinstance(payload['chunk_ids'], list) or not payload['chunk_ids'] or len(set(payload['chunk_ids'])) != len(payload['chunk_ids']): raise StoreError('MISSING_PROVENANCE')
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
        elif entity == 'usage':
            for key in ('input_tokens', 'cache_hit_tokens', 'cache_miss_tokens', 'output_tokens', 'search_count'): _number(payload[key])
        elif entity == 'question_bank':
            from .question_bank import validate_question
            try:
                validate_question(payload)
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
        _json(payload)

    def _materialize(self, entity, plan, payload, revision):
        payload = {key: value for key, value in payload.items() if key != _FIELD_VERSIONS}
        identifier = payload['id']
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

    def _put(self, entity, plan, payload):
        payload = json.loads(_json(payload))
        self._validate(entity, plan, payload)
        identifier = _wire_id(entity, plan, payload['id'])
        raw = _json(payload)
        old = self._db.execute('SELECT * FROM _records WHERE entity=? AND entity_id=?', (entity, identifier)).fetchone()
        previous = json.loads(old['payload']) if old and not old['deleted'] else {}
        previous_data = {key: value for key, value in previous.items() if key != _FIELD_VERSIONS}
        if old and not old['deleted'] and previous_data == payload: return payload
        if old and entity in PROGRESS: raise StoreError('EVENT_CONFLICT', '同一进度事件不能被改写。')
        if entity not in PROGRESS and old and not old['deleted']:
            versions = dict(previous.get(_FIELD_VERSIONS, {}))
            stamp = [time.time_ns(), self._device_id()]
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
        self._db.execute('UPDATE _records SET payload=?,deleted=1,local_revision=?,pending=1 WHERE entity=? AND entity_id=?',
                         (_json({'id': identifier}), self._revision(), entity, wire))
        if entity == 'plans': self._db.execute('DELETE FROM plans WHERE id=?', (identifier,))
        else: self._db.execute(f'DELETE FROM {entity} WHERE plan_key=? AND id=?', (plan, identifier))
        self._changed('local', entity)

    @_atomic
    def create_plan(self, plan_id=None, *, goal='', final_goal='', level='', notes='', window=None, total_minutes=0):
        plan_id = _id(plan_id or uuid.uuid4().hex)
        wire = self._db.execute("SELECT 1 FROM _records WHERE entity='plans' AND entity_id=?", (plan_id,)).fetchone()
        if wire: raise StoreError('PLAN_EXISTS')
        configuration = _window_json(window) if window is not None else None
        self._put('plans', plan_id, dict(id=plan_id, goal=goal, final_goal=final_goal, level=level, notes=notes,
            window=configuration, initial_window=configuration, total_minutes=total_minutes,
            initial_total_minutes=total_minutes, module_count=0, state='creating', archived=False, material_version=3))
        return plan_id

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
        with self._lock: return self._plan_availability(self._get('plans', plan_id, plan_id))

    def list_plans(self, *, include_archived=False):
        with self._lock:
            values = [json.loads(r[0]) for r in self._db.execute('SELECT payload FROM plans ORDER BY id')]
            return [self._plan_availability(p) for p in values if include_archived or not p['archived']]

    @_atomic
    def save_module_preparation(self, plan_id, module_id, summary):
        """Persist the material component's per-module summary with normal sync."""
        row = self._get('modules', plan_id, module_id)
        row['material_preparation'] = dict(summary)
        return self._put('modules', plan_id, row)

    def records(self, entity, plan_id=''):
        """Read detached payloads; deterministic position/ID order for consumers."""
        if entity not in PARENTS or entity == 'plans': raise StoreError('UNKNOWN_ENTITY')
        with self._lock:
            return sorted(self._rows(entity, plan_id), key=lambda p: (p.get('position', 0), p['id']))

    def _plan_availability(self, plan):
        # A version-2 plan (material flow) saved before version 3 is old only if it
        # holds pre-batch-70 questions; questions prepared since then are
        # objective/recall (BUG-54). Version 1 or none is old.
        bank = self._rows('question_bank', plan['id'])
        version = plan.get('material_version')
        old = any('kind' not in q or q.get('origin')=='cloze' and q.get('role','bank')=='bank' for q in bank) \
            or version not in (2, 3) \
            or (version == 2 and any(q.get('kind') not in ('objective', 'recall') for q in bank))
        if old: plan.update(unusable=True, unusable_reason='旧版计划，已不可用')
        return plan

    def require_material_plan(self, plan_id):
        plan = self.get_plan(plan_id)
        if plan.get('unusable'): raise StoreError('OLD_PLAN', '旧版计划，已不可用')
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
        self._put('points', plan_id, point)

    @_atomic
    def add_bank_questions(self, plan_id, module_id, questions):
        from .question_bank import original_text
        self.require_material_plan(plan_id)
        self._get('modules', plan_id, module_id)
        valid_points = {p['id'] for p in self._rows('points', plan_id) if p['module_id'] == module_id}
        identifiers = []
        existing_rows = self._rows('question_bank', plan_id)
        stems = {}
        for q in existing_rows: stems.setdefault(original_text(q['stem']), []).append(q)
        skipped = 0
        for question in questions:
            row = dict(question)
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
        row['uses'].append(dict(purpose=purpose, at=_at(at)))
        self._put('question_bank', plan_id, row)

    @_atomic
    def append_question_answer(self, plan_id, answer_id, *, module_id, context, attempt_id,
                               question_id, point_ids, angle, correct, wrong_points=(), reason='', checks=(), at=None,
                               answer=None, ticked=None):
        """Persist a question answer and optional recall self-check exactly once.

        It is an append-only sync event, not an authoritative mastery state.
        Existing same-ID facts survive classroom replay without changing their time.
        """
        row = dict(id=_id(answer_id), module_id=_id(module_id), context=context, attempt_id=attempt_id,
                   question_id=question_id, point_ids=list(point_ids), angle=angle, correct=correct,
                   wrong_points=list(wrong_points), reason=str(reason)[:400], checks=list(checks), at=_at(at))
        if answer is not None:row['answer']=answer
        if ticked is not None:row['ticked']=list(ticked)
        existing = next((r for r in self._rows('question_answers', plan_id) if r['id'] == answer_id), None)
        if existing:
            if any(existing.get(k) != v for k, v in row.items() if k != 'at'):
                raise StoreError('EVENT_CONFLICT')
            return answer_id
        if context not in ('module_test', 'skip_test', 'retest', 'check', 'practice', 'quick_confirm') or type(correct) is not bool:
            raise StoreError('INVALID_ANSWER')
        self._get('modules', plan_id, module_id)
        valid = {p['id'] for p in self.records('points', plan_id) if p['module_id'] == module_id}
        if not point_ids or not set(point_ids) <= valid or not set(wrong_points) <= set(point_ids):
            raise StoreError('POINT_MODULE_MISMATCH')
        self._put('question_answers', plan_id, row)
        return answer_id

    @_atomic
    def append_practice_result(self, plan_id, question_id, module_id, point_ids, correct, *, reason='', at=None, result_id=None, wrong_points=None):
        self._get('plans', plan_id, plan_id)
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

    @_atomic
    def append_material_additions(self, plan_id, modules, supplements, module_order, *, today, new_window=None):
        """Atomic PLAN-10 addition, never a general started-plan edit API.

        modules contain NEW IDs, points/check_minutes and optional required_by.
        supplements only union chunk_ids onto existing points. Old wording,
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
            point['chunk_ids'] = list(dict.fromkeys(point['chunk_ids'] + item['chunk_ids']))
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

    def _replan(self, plan, today, new_window, total_minutes, *, cause, reason=None):
        ledger = self._load_ledger(plan)
        ledger.replan(today, new_window, total_minutes)  # Validate before changing any rows.
        event = dict(id=uuid.uuid4().hex, op='replan', operation_seq=self._op_seq(plan['id']),
                     today=today.isoformat(), window=_window_json(new_window), total_minutes=total_minutes,
                     cause=cause, reason=reason)
        self._put('ledger_events', plan['id'], event)
        plan['window'], plan['total_minutes'] = event['window'], total_minutes

    @_atomic
    def replan_plan(self, plan_id, today, new_window=None, new_total_minutes=None, *, reason=None):
        plan = self._get('plans', plan_id, plan_id)
        self._replan(plan, _day(today), _window(new_window or plan['window']),
                     plan['total_minutes'] if new_total_minutes is None else new_total_minutes, cause='explicit_replan', reason=reason)
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
            if entity in PROGRESS or entity in ('plans', 'chat_sessions', 'chat_messages'): continue
            for payload in self._rows(entity, plan_id): self._delete(entity, plan_id, payload['id'])
        self._delete('plans', plan_id, plan_id)

    def _load_ledger(self, plan):
        if plan['initial_window'] is None: raise StoreError('PLAN_NOT_READY')
        ledger = Ledger(_window(plan['initial_window']), plan['initial_total_minutes'])
        operations = [(p['operation_seq'], p['id'], 'credit', p) for p in self._rows('credits', plan['id'])]
        operations += [(p['operation_seq'], p['id'], p['op'], p) for p in self._rows('ledger_events', plan['id'])]
        for _, _, op, payload in sorted(operations):
            if op == 'credit':
                c = payload['credit']
                method = ledger.add_confirmed if c['state'] == 'confirmed' else ledger.add_tentative
                method(c['credit_id'], c['unit_id'], c['minutes'], _day(c['day']))
            elif op == 'replan': ledger.replan(_day(payload['today']), _window(payload['window']), payload['total_minutes'])
            elif op == 'confirm': ledger.confirm(payload['unit_id'])
            elif op == 'revoke': ledger.revoke(payload['unit_id'])
            elif op == 'release': ledger.release_unused(payload['minutes'])
        return ledger

    def load_ledger(self, plan_id) -> Ledger:
        with self.transaction(): return self._load_ledger(self._get('plans', plan_id, plan_id))

    def load_mastery(self, plan_id) -> MasteryBook:
        with self.transaction():
            self._get('plans', plan_id, plan_id)
            modules = sorted(self._rows('modules', plan_id), key=lambda m: (m['position'], m['id']))
            positions = {m['id']: i for i, m in enumerate(modules)}
            points = sorted(self._rows('points', plan_id), key=lambda p: (positions[p['module_id']], p['position'], p['id']))
            from .question_bank import assessment_questions
            taught = [r['credit']['unit_id'] for r in self._rows('credits',plan_id)
                      if r['id'].startswith(('learn:','relearn:'))]
            book = MasteryBook((PointRef(p['id'], p['module_id']) for p in points),
                               assessment_questions(self.records('question_bank',plan_id)), taught)
            events = [p['event'] for entity in ('judgements', 'attempt_closed') for p in self._rows(entity, plan_id)]
            for event in events: book.apply(event_from_dict(event))
            return book

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
            return any(study_day(datetime.fromisoformat(row['event']['at']), refresh, tz) == day
                       for row in self._rows('judgements', plan_id))

    def _append_mastery(self, plan_id, event, entity, details=None):
        self._get('plans', plan_id, plan_id)
        if not isinstance(event, Judgement if entity == 'judgements' else AttemptClosed): raise StoreError('INVALID_EVENT')
        book = self.load_mastery(plan_id); book.apply(event)
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
    def append_attempt_closed(self, plan_id, event: AttemptClosed):
        self._append_mastery(plan_id, event, 'attempt_closed')

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
        plan = self._get('plans', plan_id, plan_id)
        ledger = self._load_ledger(plan)
        if state not in ('tentative', 'confirmed'): raise StoreError('INVALID_CREDIT')
        method = ledger.add_confirmed if state == 'confirmed' else ledger.add_tentative
        method(credit_id, unit_id, minutes, _day(day))
        original = next(c for c in ledger.to_dict()['submissions'] if c['credit_id'] == credit_id)
        existing = next((p for p in self._rows('credits', plan_id) if p['id'] == credit_id), None)
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

    def messages(self, plan_id, module_id=None, *, include_compressed=False):
        values = self.records('messages', plan_id)
        return sorted([m for m in values if m['module_id'] == module_id and (include_compressed or not m['compressed'])], key=lambda m: (m['at'], m['id']))

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
            if _REPORT_STORAGE not in document: return document
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
                return result
            except (StoreError, ValueError, TypeError, KeyError, UnicodeError):
                raise StoreError('REPORT_INCOMPLETE', '报告分块尚未全部同步或校验失败。') from None

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
        payload = dict(id=identifier, title=title)
        if created_at is not None:
            payload.update(created_at=_at(created_at), updated_at=_at(created_at))
        self._put('chat_sessions', '', payload)
        return identifier

    def get_chat_session(self, session_id):
        with self._lock: return self._get('chat_sessions', '', session_id)

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
                if entity not in ('plans', 'chat_sessions', 'chat_messages'):
                    if not self._db.execute('SELECT 1 FROM plans WHERE id=?', (plan,)).fetchone():
                        deferred.append(row); continue
                missing = False
                for name, parent in PARENTS[entity]:
                    if payload.get(name) is not None and not self._db.execute(f'SELECT 1 FROM {parent} WHERE plan_key=? AND id=?', (plan, payload[name])).fetchone():
                        missing = True
                if entity == 'points':
                    missing |= any(not self._db.execute('SELECT 1 FROM source_chunks WHERE plan_key=? AND id=?', (plan, cid)).fetchone() for cid in payload['chunk_ids'])
                if missing: deferred.append(row); continue
                self._materialize(entity, plan, payload, row['local_revision'])
            if len(deferred) == len(pending): break
            pending = deferred

    @_atomic
    def apply_remote(self, records: list[tuple[SyncRecord, int]]) -> None:
        for record, server_revision in records:
            if not isinstance(record, SyncRecord) or record.entity not in PARENTS: raise StoreError('UNKNOWN_ENTITY')
            _number(server_revision); _number(record.local_revision)
            if record.schema != f'mentorflow.course.{record.entity}.v1': raise StoreError('UNSUPPORTED_RECORD_SCHEMA')
            if type(record.deleted) is not bool or not isinstance(record.plan_key, str) or not isinstance(record.payload, dict): raise StoreError('INVALID_RECORD')
            payload = json.loads(_json(record.payload))
            if record.entity_id != _wire_id(record.entity, record.plan_key, _id(payload['id'])): raise StoreError('INVALID_RECORD_ID')
            if record.entity in ('chat_sessions', 'chat_messages') and record.plan_key: raise StoreError('INVALID_RECORD_SCOPE')
            if record.entity not in ('chat_sessions', 'chat_messages'): _id(record.plan_key)
            old = self._db.execute('SELECT * FROM _records WHERE entity=? AND entity_id=?', (record.entity, record.entity_id)).fetchone()
            if record.entity in PROGRESS:
                if record.deleted: continue  # Never erase even an already-synced event.
                self._validate(record.entity, record.plan_key, payload)
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
            if not record.deleted and record.entity == 'question_bank' and 'kind' not in payload:
                try: plan = self._get('plans', record.plan_key, record.plan_key)
                except StoreError: plan = None
                if plan is not None:
                    plan.update(unusable=True, unusable_reason='旧版计划，已不可用', material_version=1)
                    self._put('plans', record.plan_key, plan)
                continue
            if not record.deleted:
                # Validate the actual remote record before choosing local fields;
                # otherwise a malformed remote edit could be silently hidden.
                self._validate(record.entity, record.plan_key, payload)
            repair_answered = False
            uses_union = None
            if record.entity == 'question_bank' and not record.deleted and old is not None and not old['deleted']:
                local_uses = json.loads(old['payload']).get('uses', [])
                uses_union = [dict(purpose=purpose, at=at) for purpose, at in sorted(
                    {(u['purpose'], u['at']) for u in local_uses + payload['uses']})]
            merged = False
            if old is not None and old['pending'] and record.entity not in PROGRESS and not record.deleted:
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
            if not deleted: self._validate(record.entity, record.plan_key, payload)
            if record.entity == 'plans' and payload['id'] != record.plan_key: raise StoreError('INVALID_RECORD_SCOPE')
            self._db.execute('''INSERT INTO _records VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(entity,entity_id)
                DO UPDATE SET payload=excluded.payload,deleted=excluded.deleted,local_revision=excluded.local_revision,
                server_revision=excluded.server_revision,pending=excluded.pending''',
                             (record.entity, record.entity_id, record.plan_key, _json(payload), int(deleted),
                              self._revision(), server_revision, int(repair_answered or merged)))
            if old is None or old['deleted'] != int(deleted) or old['payload'] != _json(payload):
                self._changed('remote', record.entity)
        self._project_records()
