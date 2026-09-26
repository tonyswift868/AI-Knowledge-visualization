"""Mock HTTP protocol + a test-only implementation of MF-03's agreed store."""
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import importlib
import json
import sys
from threading import Event, Thread
from types import SimpleNamespace as NS, ModuleType

import certifi
import httpx
import pytest

from refactor.learning.course.sync_course import CourseSync
from refactor.learning.plan_models import PlanError, PlanScope
from refactor.learning.sync_transport import SyncTransport


@dataclass(frozen=True)
class Record:
    entity: str
    entity_id: str
    plan_key: str
    schema: str
    payload: dict
    deleted: bool
    local_revision: int


@pytest.fixture(autouse=True)
def agreed_contract(monkeypatch):
    # No production sync_contract.py is created. Once MF-03 is integrated,
    # deserialization uses their real SyncRecord rather than this test type.
    name = 'refactor.learning.course.sync_contract'
    try:
        importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        module = ModuleType(name)
        module.SyncRecord = Record
        monkeypatch.setitem(sys.modules, name, module)


def record(key='one', **changes):
    return replace(Record('modules', key, 'plan-one', 'mentorflow.course.modules.v1',
                          {'title': '模块'}, False, 1), **changes)


def wire(record):
    values = {k: deepcopy(getattr(record, k)) for k in ('schema', 'plan_key', 'entity', 'entity_id', 'payload')}
    rid = json.dumps([record.entity, record.entity_id], ensure_ascii=False, separators=(',', ':'))
    eid = 'refactor.v1:course_record:' + hashlib.sha256(rid.encode()).hexdigest()
    return eid, {'schema': 'mentorflow.refactor-sync.v1', 'version': 1,
                 'record_type': 'course_record', 'record_id': rid, 'values': values}


class MemoryStore:
    def __init__(self, *records, remote_wins=False):
        self.rows = {(r.entity, r.entity_id): deepcopy(r) for r in records}
        self.pending = dict.fromkeys(self.rows)
        self.revisions = {}
        self.cursor = ''
        self.applied, self.acks = [], []
        self.remote_wins = remote_wins
        self.fail_apply = self.fail_cursor = False

    def edit(self, record):
        key = (record.entity, record.entity_id)
        self.rows[key] = deepcopy(record)
        self.pending[key] = None

    def pending_records(self, limit):
        return [deepcopy(self.rows[k]) for k in list(self.pending)[:limit]]

    def mark_synced(self, entity, entity_id, local_revision, server_revision):
        key = (entity, entity_id)
        self.acks.append((key, local_revision, server_revision))
        self.revisions[key] = max(server_revision, self.revisions.get(key, 0))
        if self.rows[key].local_revision == local_revision:
            self.pending.pop(key, None)

    def apply_remote(self, records):
        if self.fail_apply:
            raise OSError('synthetic disk failure')
        self.applied.extend(deepcopy(records))
        for remote, revision in records:
            key = (remote.entity, remote.entity_id)
            if revision <= self.revisions.get(key, 0):
                continue
            self.revisions[key] = revision
            if key in self.pending and not self.remote_wins and not remote.deleted:
                continue
            self.rows[key] = Record(remote.entity, remote.entity_id, remote.plan_key, remote.schema,
                                    deepcopy(remote.payload), remote.deleted, revision)
            self.pending.pop(key, None)

    def sync_cursor(self):
        return self.cursor

    def set_sync_cursor(self, cursor):
        if self.fail_cursor:
            raise OSError('synthetic cursor failure')
        self.cursor = cursor


class Server:
    def __init__(self, scope):
        self.scope, self.rows, self.requests = scope, {}, {}
        self.calls = []
        self.before = self.after = None
        self.incremental = False
        self.sequence = 0
        self.sequences = {}
        self.pages = []
        self.page_limit = 100

    def stamp(self, eid):
        self.sequence += 1
        self.sequences[eid] = self.sequence

    def put_remote(self, record, revision=1):
        eid, payload = wire(record)
        self.rows[eid] = dict(kind='learning_run', entity_id=eid, revision=revision,
                              deleted=record.deleted, payload={} if record.deleted else payload)
        self.stamp(eid)

    def handle(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, deepcopy(body), request.headers['Authorization']))
        assert request.headers['X-MentorFlow-Organization-ID'] == self.scope.organization_id
        assert request.headers['X-MentorFlow-Workspace-ID'] == self.scope.workspace_id
        if self.before:
            response = self.before(request)
            if response is not None:
                return response
        if request.method == 'GET':
            if request.url.path == '/api/enterprise/sync/v1/changes':
                if not self.incremental:
                    return httpx.Response(404)
                since = int(request.url.params['since'])
                limit = min(int(request.url.params['limit']), self.page_limit)
                rows = [dict(self.rows[eid], change_seq=seq) for eid, seq in sorted(self.sequences.items(), key=lambda x: x[1])
                        if seq > since and self.rows[eid]['kind'] == request.url.params['kind']]
                page = dict(schema='mentorflow.structured-sync-changes.v1', contract_version=1,
                            scope=self.scope.snapshot_scope(), kind=request.url.params['kind'], since=str(since),
                            next_cursor=str(rows[:limit][-1]['change_seq'] if rows else since),
                            has_more=len(rows) > limit, entities=rows[:limit])
                self.pages.append(deepcopy(page))
                return httpx.Response(200, json=page)
            assert request.url.path == '/api/enterprise/sync/v1/snapshot'
            return httpx.Response(200, json=dict(schema='mentorflow.structured-sync.v1', contract_version=3,
                complete=True, snapshot_authority='POSTGRESQL_SINGLE_STATEMENT_MVCC',
                snapshot_scope=self.scope.snapshot_scope(), snapshot_revision='pg-snapshot:1:5:',
                observation={'source': 'SERVER_CONFIRMED', 'snapshot_revision': 'pg-snapshot:1:5:'},
                entities=list(self.rows.values())))
        deleting = request.method == 'POST'
        parts = request.url.path.split('/')
        eid = parts[-2] if deleting else parts[-1]
        key = body['request_id']
        if key in self.requests:
            original, response = self.requests[key]
            assert original == (request.method, body)
            return httpx.Response(200, json=response)
        previous = self.rows.get(eid)
        revision = previous['revision'] if previous else 0
        if revision != body['expected_revision']:
            return httpx.Response(409, json={'code': 'SYNC_STALE_REVISION'})
        if previous and previous['deleted']:
            return httpx.Response(410, json={'code': 'SYNC_ENTITY_TOMBSTONED'})
        if deleting:
            assert previous is not None
        else:
            assert set(body['payload']['values']) == {'schema', 'plan_key', 'entity', 'entity_id', 'payload'}
            assert body['payload']['record_type'] == 'course_record'
        response = dict(kind='learning_run', entity_id=eid, revision=revision + 1,
                        deleted=deleting, payload={} if deleting else body['payload'])
        self.rows[eid] = deepcopy(response)
        self.stamp(eid)
        self.requests[key] = (deepcopy((request.method, body)), deepcopy(response))
        if self.after:
            self.after(request)
        return httpx.Response(200, json=response)


@pytest.fixture
def connection():
    issuer, subject = 'https://identity.invalid/realm', 'test-user'
    hashes = ':'.join(hashlib.sha256(s.encode()).hexdigest() for s in (issuer, subject))
    account = 'mfa1_' + hashlib.sha256(('mentorflow.account.v1:' + hashes).encode()).hexdigest()
    scope = PlanScope(account, issuer, subject, 'org-one', 'workspace-one')
    login = NS(identity=NS(account_id=account, issuer=issuer, subject=subject,
                          organization_id=scope.organization_id, workspace_id=scope.workspace_id),
               server_state={'authenticated': True, 'account': {'account_id': account, 'subject_id': subject},
                             'context': {'organization_id': scope.organization_id, 'workspace_id': scope.workspace_id}},
               access_token='synthetic-access')
    server = Server(scope)
    transport = SyncTransport(account_id=account, session_provider=lambda: login,
                              api_base_url='https://api.invalid', ca_bundle=certifi.where(),
                              http_transport=httpx.MockTransport(server.handle))
    yield server, transport, login
    transport.close()


def test_upload_and_reopen_cursor_uses_revision_and_current_token(connection):
    server, transport, login = connection
    store = MemoryStore(record())
    assert CourseSync(store, transport).sync()['uploaded'] == 1
    assert not store.pending
    assert store.acks[-1][1:] == (1, 1)
    store.edit(record(payload={'title': '修改'}, local_revision=2))
    login.access_token = 'rotated-access'
    CourseSync(store, transport).sync()
    assert server.calls[-1][2]['expected_revision'] == 1
    assert server.calls[-1][3] == 'Bearer rotated-access'
    assert len(server.requests) == 2


def test_incremental_apply_skips_unchanged_and_other_record_types(connection):
    server, transport, _ = connection
    server.put_remote(record())
    server.rows['legacy'] = dict(kind='history', entity_id='legacy', revision=1, deleted=False, payload={'old': True})
    store = MemoryStore()
    sync = CourseSync(store, transport)
    assert sync.sync()['pulled'] == 1
    cursor = store.cursor
    assert sync.sync()['pulled'] == 0
    assert store.cursor == cursor
    server.put_remote(record(payload={'title': '远端更新'}), revision=2)
    assert sync.sync()['pulled'] == 1
    assert len(store.applied) == 2
    assert store.rows['modules', 'one'].payload == {'title': '远端更新'}


@pytest.mark.parametrize('remote_wins', [False, True])
def test_conflict_fetches_remote_and_delegates_resolution(connection, remote_wins):
    server, transport, _ = connection
    store = MemoryStore(record(), remote_wins=remote_wins)
    def concurrent(request):
        if request.method == 'PUT':
            server.before = None
            server.put_remote(record(payload={'title': '另一设备'}), 1)
    server.before = concurrent
    result = CourseSync(store, transport).sync()
    assert result['conflicts'] == 1
    assert len(store.applied) == 1
    assert not store.pending
    assert list(server.rows.values())[0]['payload']['values']['payload']['title'] == ('另一设备' if remote_wins else '模块')


@pytest.mark.parametrize('after_commit', [False, True])
def test_network_retry_survives_engine_restart_without_duplicate_write(connection, after_commit):
    server, transport, _ = connection
    store = MemoryStore(record())
    def lose(request):
        if request.method == 'PUT':
            raise httpx.ReadError('synthetic disconnect')
    if after_commit:
        server.after = lose
    else:
        server.before = lose
    with pytest.raises(PlanError, match='暂未同步'):
        CourseSync(store, transport).sync()
    failed_id = server.calls[-1][2]['request_id']
    assert store.pending
    server.before = server.after = None
    CourseSync(store, transport).sync()
    assert not store.pending
    assert len(server.requests) == 1
    assert failed_id in server.requests


def test_edit_during_upload_is_not_acknowledged_as_synced(connection):
    server, transport, _ = connection
    store = MemoryStore(record())
    def edit(request):
        store.edit(record(payload={'title': '上传期间修改'}, local_revision=2))
    server.after = edit
    assert CourseSync(store, transport).sync()['pending'] == 1
    assert store.acks[-1][1:] == (1, 1)
    assert store.rows['modules', 'one'].payload['title'] == '上传期间修改'
    server.after = None
    CourseSync(store, transport).sync()
    assert not store.pending
    assert list(server.rows.values())[0]['revision'] == 2


def test_cancel_inflight_preserves_queue_and_retries_committed_result(connection):
    server, transport, _ = connection
    store = MemoryStore(record())
    sync = CourseSync(store, transport)
    server.after = lambda request: sync.cancel()
    with pytest.raises(PlanError) as exc:
        sync.sync()
    assert exc.value.code == 'CANCELLED'
    assert store.pending and not store.acks
    server.after = None
    assert sync.sync()['ok']
    assert len(server.requests) == 1
    sync.close()
    assert transport.require_current()


def test_cancel_before_round_and_single_flight(connection):
    server, transport, _ = connection
    store = MemoryStore(record())
    sync = CourseSync(store, transport)
    cancel = Event(); cancel.set()
    with pytest.raises(PlanError) as exc:
        sync.sync(cancel=cancel)
    assert exc.value.code == 'CANCELLED' and not server.calls
    entered, release = Event(), Event()
    def pause(request):
        entered.set()
        assert release.wait(5)
    server.before = pause
    errors = []
    def run():
        try:
            sync.sync()
        except Exception as e:
            errors.append(e)
    thread = Thread(target=run); thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(PlanError) as exc:
            sync.sync()
        assert exc.value.code == 'SYNC_IN_PROGRESS'
    finally:
        release.set(); thread.join(5)
    assert not thread.is_alive() and not errors


@pytest.mark.parametrize('stage', ['apply', 'cursor'])
def test_cursor_never_advances_past_failed_persistence(connection, stage):
    server, transport, _ = connection
    server.put_remote(record())
    store = MemoryStore()
    setattr(store, 'fail_' + stage, True)
    with pytest.raises(OSError):
        CourseSync(store, transport).sync()
    assert store.cursor == '' or json.loads(store.cursor)['since'] == '0'
    setattr(store, 'fail_' + stage, False)
    CourseSync(store, transport).sync()
    assert store.cursor and len(store.rows) == 1


def test_tombstone_upload_and_incremental_pull(connection):
    server, transport, _ = connection
    store = MemoryStore(record(deleted=True))
    CourseSync(store, transport).sync()
    assert not store.pending
    assert list(server.rows.values())[0]['deleted']
    assert list(server.rows.values())[0]['revision'] == 2
    # A fresh device has no local row to delete and cannot reverse a hashed ID.
    fresh = MemoryStore()
    assert CourseSync(fresh, transport).sync()['ok']
    assert not fresh.rows


def test_remote_tombstone_uses_cursor_identity(connection):
    server, transport, _ = connection
    server.put_remote(record())
    store = MemoryStore()
    CourseSync(store, transport).sync()
    server.put_remote(record(deleted=True), 2)
    assert CourseSync(store, transport).sync()['pulled'] == 1
    assert store.rows['modules', 'one'].deleted


def test_scope_switch_during_request_cannot_ack(connection):
    server, transport, login = connection
    store = MemoryStore(record())
    server.after = lambda request: setattr(login.identity, 'subject', 'different-user')
    with pytest.raises(PlanError) as exc:
        CourseSync(store, transport).sync()
    assert exc.value.code == 'SESSION_UNAVAILABLE'
    assert store.pending and not store.acks


@pytest.mark.parametrize('during', ['GET', 'PUT'])
def test_superseded_code_survives_transport(connection, during):
    server, transport, _ = connection
    server.before = lambda request: (httpx.Response(401, json={'error': {'code': 'NATIVE_SESSION_SUPERSEDED'}})
                                     if request.method == during else None)
    store = MemoryStore(record())
    with pytest.raises(PlanError) as exc:
        CourseSync(store, transport).sync()
    assert exc.value.code == 'NATIVE_SESSION_SUPERSEDED'
    assert store.pending


def test_maximum_course_payload_not_rejected_for_envelope_overhead(connection):
    server, transport, _ = connection
    payload = {'text': 'x' * (256 * 1024 - len('{"text":""}'))}
    assert CourseSync(MemoryStore(record(payload=payload)), transport).sync()['ok']
    store = MemoryStore(record('two', payload={'text': payload['text'] + 'x'}))
    with pytest.raises(PlanError) as exc:
        CourseSync(store, transport).sync()
    assert exc.value.code == 'SYNC_RECORD_TOO_LARGE' and store.pending


def test_invalid_remote_does_not_advance_cursor(connection):
    server, transport, _ = connection
    server.put_remote(record())
    list(server.rows.values())[0]['payload']['values']['entity_id'] = 'wrong'
    store = MemoryStore()
    with pytest.raises(PlanError):
        CourseSync(store, transport).sync()
    assert store.cursor == '' and not store.rows


def test_cursor_from_another_account_is_rejected_before_http(connection):
    server, transport, _ = connection
    store = MemoryStore(record())
    CourseSync(store, transport).sync()
    cursor = json.loads(store.cursor)
    cursor['scope']['subject_id'] = 'another-account'
    store.cursor = json.dumps(cursor)
    server.calls.clear()
    with pytest.raises(PlanError) as exc:
        CourseSync(store, transport).sync()
    assert exc.value.code == 'SYNC_CURSOR_INVALID' and not server.calls


def test_network_error_during_pull_does_not_advance_cursor_or_upload(connection):
    server, transport, _ = connection
    store = MemoryStore(record())
    server.before = lambda request: httpx.Response(503)
    with pytest.raises(PlanError):
        CourseSync(store, transport).sync()
    assert not server.requests and store.cursor == '' and store.pending
    server.before = None
    assert CourseSync(store, transport).sync()['ok']


def test_http_failure_after_first_record_keeps_remaining_queue(connection):
    server, transport, _ = connection
    store = MemoryStore(record('one'), record('two'))
    def fail_second(request):
        if request.method == 'PUT' and len(server.requests) == 1:
            return httpx.Response(503)
    server.before = fail_second
    with pytest.raises(PlanError):
        CourseSync(store, transport).sync()
    assert list(store.pending) == [('modules', 'two')]
    server.before = None
    assert CourseSync(store, transport).sync()['ok']
    assert len(server.requests) == 2


def test_cancelled_pull_does_not_apply_remote_or_advance_cursor(connection):
    server, transport, _ = connection
    server.put_remote(record())
    store = MemoryStore()
    sync = CourseSync(store, transport)
    server.before = lambda request: sync.cancel()
    with pytest.raises(PlanError) as exc:
        sync.sync()
    assert exc.value.code == 'CANCELLED' and not store.rows and store.cursor == ''


def test_account_level_records_and_distinct_entities_with_same_id(connection):
    server, transport, _ = connection
    store = MemoryStore(record('same', plan_key=''), record('same', entity='usage', schema='mentorflow.course.usage.v1', plan_key=''))
    assert CourseSync(store, transport).sync()['ok']
    assert len(server.rows) == 2
    fresh = MemoryStore()
    assert CourseSync(fresh, transport).sync()['pulled'] == 2
    assert set(fresh.rows) == {('modules', 'same'), ('usage', 'same')}


def test_existing_transport_write_and_size_limit_are_preserved(connection):
    server, transport, _ = connection
    payload = {'text': '已有历史记录'}
    def history(request):
        body = json.loads(request.content)
        assert request.method == 'PUT' and body == {
            'request_id': 'existing-request', 'expected_revision': 2, 'payload': payload}
        return httpx.Response(200, json=dict(kind='history', entity_id='existing-record',
                                            revision=3, deleted=False, payload=payload))
    server.before = history
    assert transport.write('history', 'existing-record', request_id='existing-request',
                           expected_revision=2, payload=payload)['revision'] == 3
    before = len(server.calls)
    with pytest.raises(PlanError) as exc:
        transport.write('history', 'existing-record', request_id='too-large', expected_revision=3,
                        payload={'text': 'x' * (256 * 1024)})
    assert exc.value.code == 'SYNC_RECORD_TOO_LARGE' and len(server.calls) == before
