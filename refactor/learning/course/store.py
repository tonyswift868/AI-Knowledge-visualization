"""Account-owned SQLite course data and an atomic record outbox, schema v2.

Each business table stores a canonical, versioned JSON payload plus indexed FK
columns and a local revision. Payload fields (also validated below):
plans: intake, window/initial_window, total/initial_total, state, archived;
sources: kind, url, sha256, title, site, tier, credibility, adopted;
source_chunks: extract chunk_id as id, source_id, position, text, pages, section;
stages/modules/points: title/summary, position, prerequisites, minute estimates,
and nonempty chunk_ids for every point; teacher_profile/reports: document;
messages/chat_messages: role, content, kind, at, compressed, summary_of.
Module positions are global within a plan; point positions are within a module.
Plan module_count and module point_count prevent a partially downloaded course
from becoming runnable/complete before all its structural records arrive.

credits stores immutable original credit submissions. The additional
ledger_events table stores immutable confirm/revoke/replan commands in the same
operation sequence: neither a cached Ledger nor mutable credit states are an
authority. Plans retain the initial window/total as well as the current ones.

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
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import uuid

from ..preferences.storage import account_directory
from .ledger import Ledger, PlanWindow, Credit
from .mastery import MasteryBook, PointRef, Judgement, AttemptClosed, event_to_dict, event_from_dict
from .sync_contract import SyncRecord

SCHEMA_VERSION = 2
MAX_PAYLOAD = 256 * 1024
PROGRESS = frozenset(('judgements', 'attempt_closed', 'credits', 'ledger_events', 'usage'))
# One entity maps to exactly one table. Tuple entries are FK field -> parent.
PARENTS = {
    'plans': (), 'sources': (), 'source_chunks': (('source_id', 'sources'),),
    'stages': (), 'modules': (('stage_id', 'stages'),), 'points': (('module_id', 'modules'),),
    'judgements': (), 'attempt_closed': (), 'credits': (), 'ledger_events': (),
    'teacher_profile': (), 'messages': (('module_id', 'modules'),), 'reports': (), 'usage': (),
    'chat_sessions': (), 'chat_messages': (('session_id', 'chat_sessions'),),
}
STATES = ('creating', 'ready', 'active', 'checking', 'completed')


class StoreError(ValueError):
    def __init__(self, code, message='课程数据不符合约定，操作未保存。'):
        self.code, self.message = code, message
        super().__init__(message)


def _json(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(raw.encode('utf-8')) > MAX_PAYLOAD:
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
        """Public grouped transaction; nested purpose-methods use savepoints."""
        with self._lock:
            if self._closed: raise StoreError('STORE_CLOSED')
            depth = self._depth
            savepoint = f'course_{depth}'
            self._db.execute('BEGIN IMMEDIATE' if depth == 0 else f'SAVEPOINT {savepoint}')
            self._depth += 1
            try:
                yield self
                self._db.execute('COMMIT' if depth == 0 else f'RELEASE {savepoint}')
            except BaseException:
                if depth == 0:
                    if self._db.in_transaction: self._db.execute('ROLLBACK')
                else:
                    self._db.execute(f'ROLLBACK TO {savepoint}'); self._db.execute(f'RELEASE {savepoint}')
                raise
            finally:
                self._depth -= 1

    def close(self):
        with self._lock:
            if self._depth: raise StoreError('TRANSACTION_ACTIVE')
            if not self._closed:
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
                if entity == 'plans': continue
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
            self._db.execute('UPDATE schema_version SET version=2')
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
            if payload['op'] == 'replan':
                _day(payload['today']); _window(payload['window']); _number(payload['total_minutes'])
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
        _json(payload)

    def _materialize(self, entity, plan, payload, revision):
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
        if old and not old['deleted'] and old['payload'] == raw: return payload
        if old and entity in PROGRESS: raise StoreError('EVENT_CONFLICT', '同一进度事件不能被改写。')
        revision = self._revision()
        self._db.execute('''INSERT INTO _records VALUES(?,?,?,?,?,?,0,1) ON CONFLICT(entity,entity_id)
            DO UPDATE SET payload=excluded.payload,deleted=0,local_revision=excluded.local_revision,pending=1''',
                         (entity, identifier, plan, raw, 0, revision))
        self._materialize(entity, plan, payload, revision)
        return payload

    def _delete(self, entity, plan, identifier):
        if entity in PROGRESS: raise StoreError('IMMUTABLE_EVENT')
        wire = _wire_id(entity, plan, identifier)
        old = self._db.execute('SELECT deleted FROM _records WHERE entity=? AND entity_id=?', (entity, wire)).fetchone()
        if not old or old[0]: return
        self._db.execute('UPDATE _records SET payload=?,deleted=1,local_revision=?,pending=1 WHERE entity=? AND entity_id=?',
                         (_json({'id': identifier}), self._revision(), entity, wire))
        if entity == 'plans': self._db.execute('DELETE FROM plans WHERE id=?', (identifier,))
        else: self._db.execute(f'DELETE FROM {entity} WHERE plan_key=? AND id=?', (plan, identifier))

    @_atomic
    def create_plan(self, plan_id=None, *, goal='', final_goal='', level='', notes='', window=None, total_minutes=0):
        plan_id = _id(plan_id or uuid.uuid4().hex)
        wire = self._db.execute("SELECT 1 FROM _records WHERE entity='plans' AND entity_id=?", (plan_id,)).fetchone()
        if wire: raise StoreError('PLAN_EXISTS')
        configuration = _window_json(window) if window is not None else None
        self._put('plans', plan_id, dict(id=plan_id, goal=goal, final_goal=final_goal, level=level, notes=notes,
            window=configuration, initial_window=configuration, total_minutes=total_minutes,
            initial_total_minutes=total_minutes, module_count=0, state='creating', archived=False))
        return plan_id

    @_atomic
    def save_intake(self, plan_id, *, goal=None, final_goal=None, level=None, notes=None, window=None, today=None):
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
                self._replan(plan, _day(today), _window(configuration), plan['total_minutes'])
                plan['window'] = configuration
        return self._put('plans', plan_id, plan)

    def get_plan(self, plan_id):
        with self._lock: return self._get('plans', plan_id, plan_id)

    def list_plans(self, *, include_archived=False):
        with self._lock:
            values = [json.loads(r[0]) for r in self._db.execute('SELECT payload FROM plans ORDER BY id')]
            return [p for p in values if include_archived or not p['archived']]

    def records(self, entity, plan_id=''):
        """Read detached payloads; deterministic position/ID order for consumers."""
        if entity not in PARENTS or entity == 'plans': raise StoreError('UNKNOWN_ENTITY')
        with self._lock:
            return sorted(self._rows(entity, plan_id), key=lambda p: (p.get('position', 0), p['id']))

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

    def _structure_editable(self, plan):
        if plan['state'] not in ('creating', 'ready'):
            raise StoreError('PLAN_STARTED', '开始后的计划不能删除模块或重新排序。')

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
                if any(payload[key] != existing[key] for key in ('learning_minutes', 'check_minutes', 'point_count')): raise StoreError('GENERATED_ESTIMATE_IMMUTABLE')
            self._put('modules', plan_id, payload)
        self._check_graph(plan_id)
        if plan['state'] == 'ready':
            generated = {p['module_id'] for p in self._rows('points', plan_id)}
            if any(m['id'] not in generated for m in self._rows('modules', plan_id)):
                plan['state'] = 'creating'
        plan['module_count'] = len(self._rows('modules', plan_id))
        self._put('plans', plan_id, plan)

    def _total(self, plan_id):
        return sum(m['learning_minutes'] + m['check_minutes'] for m in self._rows('modules', plan_id))

    def _op_seq(self, plan_id):
        return max([p['operation_seq'] for table in ('credits', 'ledger_events') for p in self._rows(table, plan_id)], default=0) + 1

    def _replan(self, plan, today, new_window, total_minutes):
        ledger = self._load_ledger(plan)
        ledger.replan(today, new_window, total_minutes)  # Validate before changing any rows.
        event = dict(id=uuid.uuid4().hex, op='replan', operation_seq=self._op_seq(plan['id']),
                     today=today.isoformat(), window=_window_json(new_window), total_minutes=total_minutes)
        self._put('ledger_events', plan['id'], event)
        plan['window'], plan['total_minutes'] = event['window'], total_minutes

    @_atomic
    def replan_plan(self, plan_id, today, new_window=None, new_total_minutes=None):
        plan = self._get('plans', plan_id, plan_id)
        self._replan(plan, _day(today), _window(new_window or plan['window']),
                     plan['total_minutes'] if new_total_minutes is None else new_total_minutes)
        self._put('plans', plan_id, plan)

    @_atomic
    def save_generated_module(self, plan_id, module_id, points, *, check_minutes, today):
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
        module['point_count'] = len(points)
        self._put('modules', plan_id, module)
        self._replan(plan, _day(today), _window(plan['window']), self._total(plan_id))
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
        self._replan(plan, _day(today), _window(plan['window']), self._total(plan_id))
        self._put('plans', plan_id, plan)

    @_atomic
    def delete_module(self, plan_id, module_id, *, today):
        plan = self._get('plans', plan_id, plan_id); self._structure_editable(plan)
        self._get('modules', plan_id, module_id)
        for point in self._rows('points', plan_id):
            if point['module_id'] == module_id: self._delete('points', plan_id, point['id'])
        for message in self._rows('messages', plan_id):
            if message.get('module_id') == module_id: self._delete('messages', plan_id, message['id'])
        self._delete('modules', plan_id, module_id)
        for module in self._rows('modules', plan_id):
            if module_id in module['prerequisites']:
                module['prerequisites'].remove(module_id); self._put('modules', plan_id, module)
        plan['module_count'] = len(self._rows('modules', plan_id))
        self._replan(plan, _day(today), _window(plan['window']), self._total(plan_id))
        self._put('plans', plan_id, plan)

    @_atomic
    def start_plan(self, plan_id):
        plan = self._get('plans', plan_id, plan_id)
        if plan['state'] != 'ready' or plan['archived']: raise StoreError('PLAN_NOT_READY')
        self._load_ledger(plan)
        if not self._fully_generated(plan_id): raise StoreError('PLAN_NOT_READY')
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
        if not self._fully_generated(plan_id) or not self.load_mastery(plan_id).check_complete() or not self._load_ledger(plan).status(_day(today)).completed:
            raise StoreError('PLAN_NOT_COMPLETE')
        plan['state'] = 'completed'; self._put('plans', plan_id, plan)

    @_atomic
    def archive_plan(self, plan_id):
        plan = self._get('plans', plan_id, plan_id); plan['archived'] = True
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
        return ledger

    def load_ledger(self, plan_id) -> Ledger:
        with self.transaction(): return self._load_ledger(self._get('plans', plan_id, plan_id))

    def load_mastery(self, plan_id) -> MasteryBook:
        with self.transaction():
            self._get('plans', plan_id, plan_id)
            modules = sorted(self._rows('modules', plan_id), key=lambda m: (m['position'], m['id']))
            positions = {m['id']: i for i, m in enumerate(modules)}
            points = sorted(self._rows('points', plan_id), key=lambda p: (positions[p['module_id']], p['position'], p['id']))
            book = MasteryBook(PointRef(p['id'], p['module_id']) for p in points)
            events = [p['event'] for entity in ('judgements', 'attempt_closed') for p in self._rows(entity, plan_id)]
            for event in events: book.apply(event_from_dict(event))
            return book

    def _append_mastery(self, plan_id, event, entity, details=None):
        self._get('plans', plan_id, plan_id)
        if not isinstance(event, Judgement if entity == 'judgements' else AttemptClosed): raise StoreError('INVALID_EVENT')
        book = self.load_mastery(plan_id); book.apply(event)
        payload = dict(id=event.event_id, event=event_to_dict(event), details=details or {})
        self._put(entity, plan_id, payload)

    @_atomic
    def append_judgement(self, plan_id, event: Judgement, *, details=None):
        """details may hold question, answer and source chunk IDs, unchanged."""
        self._append_mastery(plan_id, event, 'judgements', details)

    @_atomic
    def append_attempt_closed(self, plan_id, event: AttemptClosed):
        self._append_mastery(plan_id, event, 'attempt_closed')

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

    def _credit_change(self, plan_id, unit_id, event_id, op):
        identifier = _id(event_id or uuid.uuid4().hex)
        existing = next((p for p in self._rows('ledger_events', plan_id) if p['id'] == identifier), None)
        if existing:
            if existing['op'] != op or existing.get('unit_id') != unit_id: raise StoreError('EVENT_CONFLICT')
            return 0
        ledger = self._load_ledger(self._get('plans', plan_id, plan_id))
        minutes = getattr(ledger, op)(unit_id)
        self._put('ledger_events', plan_id, dict(id=identifier, op=op, unit_id=unit_id, operation_seq=self._op_seq(plan_id)))
        return minutes

    @_atomic
    def confirm_credits(self, plan_id, unit_id, *, event_id=None):
        return self._credit_change(plan_id, unit_id, event_id, 'confirm')

    @_atomic
    def revoke_credits(self, plan_id, unit_id, *, event_id=None):
        return self._credit_change(plan_id, unit_id, event_id, 'revoke')

    @_atomic
    def save_teacher_profile(self, plan_id, document):
        self._get('plans', plan_id, plan_id)
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
        plan = self._get('plans', plan_id, plan_id)
        if plan['state'] != 'completed': raise StoreError('PLAN_NOT_COMPLETE')
        identifier = _id(report_id or plan_id)
        self._put('reports', plan_id, dict(id=identifier, document=document))
        return identifier

    @_atomic
    def append_usage(self, plan_id, usage_id, *, provider, model, purpose, input_tokens=0, cache_hit_tokens=0,
                     cache_miss_tokens=0, output_tokens=0, search_count=0):
        self._get('plans', plan_id, plan_id)
        self._put('usage', plan_id, dict(id=usage_id, provider=provider, model=model, purpose=purpose,
            input_tokens=input_tokens, cache_hit_tokens=cache_hit_tokens, cache_miss_tokens=cache_miss_tokens,
            output_tokens=output_tokens, search_count=search_count))

    @_atomic
    def create_chat_session(self, title='', *, session_id=None):
        identifier = _id(session_id or uuid.uuid4().hex)
        self._put('chat_sessions', '', dict(id=identifier, title=title))
        return identifier

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
        return sorted([m for m in self.records('chat_messages') if m['session_id'] == session_id], key=lambda m: (m['at'], m['id']))

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
            if not record.deleted: self._validate(record.entity, record.plan_key, payload)
            if record.entity == 'plans' and payload['id'] != record.plan_key: raise StoreError('INVALID_RECORD_SCOPE')
            self._db.execute('''INSERT INTO _records VALUES(?,?,?,?,?,?,?,0) ON CONFLICT(entity,entity_id)
                DO UPDATE SET payload=excluded.payload,deleted=excluded.deleted,local_revision=excluded.local_revision,
                server_revision=excluded.server_revision,pending=0''',
                             (record.entity, record.entity_id, record.plan_key, _json(payload), int(record.deleted), self._revision(), server_revision))
        self._project_records()
