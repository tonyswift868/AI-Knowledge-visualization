"""106: real local API, CAS batches, replay and course field merge."""
from copy import deepcopy
import json
import logging
from types import SimpleNamespace as NS

import certifi
import httpx
import pytest
from sqlalchemy import text

from test_slim_course_record_sync import sync, put, envelope, entity_id
from slim_sqlite_support import system
from refactor.learning.course.store import CourseStore
from refactor.learning.course.sync_course import CourseSync
from refactor.learning.sync_transport import SyncTransport
from refactor.learning.sync_identity import PlanError

PATH = '/api/enterprise/sync/v1/entities/bulk'


def operation(payload, revision=0, request_id='request106'):
    return dict(kind='learning_run', entity_id=entity_id(payload), payload=payload,
                expected_revision=revision, request_id=request_id)


def test_sync106_bulk_partial_conflict_rejection_replay_and_legacy(sync):
    client, engine, auth = sync
    first, second = envelope(), envelope(record_id='two')
    second['values']['entity_id'] = 'two'
    assert put(sync, first, request_id='legacy106').status_code == 200
    invalid = operation(second, request_id='invalid106')
    invalid['payload'] = {'password': 'never-store-this'}
    rows = [operation(first), operation(second, request_id='valid106'), invalid]
    result = client.post(PATH, headers=auth, json={'records': rows})
    assert result.status_code == 200
    assert [r['status'] for r in result.json()['results']] == [409, 200, 422]
    replay = client.post(PATH, headers=auth, json={'records': rows}).json()
    assert replay == result.json()
    assert put(sync, second, revision=1).json()['revision'] == 2
    assert client.post(PATH, json={'records': rows}).status_code == 401
    with engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM structured_sync_entities')).scalar_one() == 2


def connection106(sync):
    client, _, auth = sync
    # Identity from the real local authenticated workspace fixture, no vault.
    snapshot = client.get('/api/enterprise/sync/v1/changes', headers=auth).json()
    scope = snapshot['scope']
    from refactor.desktop.account_identity import AccountIdentity
    identity = AccountIdentity(scope['issuer'], scope['subject_id'], 'Offline fixture')
    login = NS(identity=NS(account_id=identity.account_id, issuer=scope['issuer'], subject=scope['subject_id'],
                          organization_id=scope['organization_id'], workspace_id=scope['workspace_id']),
               access_token='token-a', server_state=dict(authenticated=True,
                   account=dict(account_id=identity.account_id, subject_id=scope['subject_id']),
                   context={k:scope[k] for k in ('organization_id','workspace_id')}))
    def handle(request):
        return client.request(request.method, request.url.raw_path.decode(),
                              headers=dict(request.headers), content=request.content)
    transport = SyncTransport(account_id=identity.account_id, session_provider=lambda:login,
                              api_base_url='https://local.invalid', ca_bundle=certifi.where(),
                              http_transport=httpx.MockTransport(handle))
    return identity.account_id, transport, handle


def test_sync106_partial_conflict_uses_store_field_merge(sync, tmp_path):
    account, transport, handle = connection106(sync)
    with CourseStore(account, tmp_path/'a') as a, CourseStore(account, tmp_path/'b') as b:
        a.create_plan('p', goal='initial')
        assert CourseSync(a, transport).sync()['ok']
        assert CourseSync(b, transport).sync()['ok']
        a.save_intake('p', final_goal='local goal')
        b.save_intake('p', notes='remote notes')
        assert CourseSync(b, transport).sync()['ok']
        original = transport.write_many
        def race(rows):
            # A remote edit after the initial pull, before batch CAS.
            transport.write_many = original
            b.save_intake('p', notes='racing remote')
            assert CourseSync(b, transport).sync()['ok']
            return original(rows)
        transport.write_many = race
        result = CourseSync(a, transport).sync()
        assert result['ok'] and result['conflicts'] == 1
        assert a.get_plan('p')['final_goal'] == 'local goal'
        assert a.get_plan('p')['notes'] == 'racing remote'
    transport.close()


def test_sync106_lost_receipt_restart_acknowledges_committed_rows(sync, tmp_path):
    account, transport, handle = connection106(sync)
    lost = [True]
    batches = [0]
    def disconnect(request):
        response = handle(request)
        if request.url.path == PATH:
            batches[0] += 1
            if lost[0] and batches[0] >= 2:
                raise httpx.ReadError('synthetic lost receipt')
        return response
    transport._client.close()
    transport._client = httpx.Client(transport=httpx.MockTransport(disconnect))
    with CourseStore(account, tmp_path) as store:
        for i in range(205):
            store.create_plan(f'p{i}')
        result=CourseSync(store, transport).sync()
        assert result['uploaded']==100 and result['failed']==100 and not result['ok']
        # The first batch is already durable locally. The lost second batch
        # and five unsent rows remain queued, even though the server committed.
        assert len(store.pending_records(500)) == 105
        lost[0] = False
        assert CourseSync(store, transport).sync()['ok']
        assert not store.pending_records(500)
    with sync[1].connect() as c:
        assert c.execute(text('SELECT count(*) FROM structured_sync_entities')).scalar_one() == 205
        assert c.execute(text('SELECT max(revision) FROM structured_sync_entities')).scalar_one() == 1
    transport.close()


def test_sync106_batch_ack_progress_visible_before_round_finishes(sync, tmp_path, caplog):
    account, transport, _ = connection106(sync)
    with CourseStore(account, tmp_path) as store, caplog.at_level(logging.INFO, logger='refactor.runtime'):
        for i in range(205): store.create_plan(f'p{i}')
        pending = []
        runner = CourseSync(store, transport)
        runner.on_progress = lambda value, cancel: pending.append((value, len(store.pending_records(500))))
        assert runner.sync()['ok']
        assert any(p['phase']=='upload' and p['completed']==100 and n==105 for p,n in pending)
        fields = [r.mf_fields for r in caplog.records if hasattr(r,'mf_fields')]
        assert sum(f.get('state')=='UPLOAD_BATCH_END' for f in fields) == 3
        assert any(f.get('state')=='ROUND_RESULT' and f['completed']==205 for f in fields)
    transport.close()
