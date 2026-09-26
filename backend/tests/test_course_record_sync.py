"""Real HTTP writes and local SQLite persistence; no remote DSN or RLS claim."""
from copy import deepcopy
import hashlib
import json
import uuid

import pytest
from sqlalchemy import select

from app import account_core_mvp
from app.enterprise_tenancy.structured_sync import (
    StructuredSyncService, _refactor_sync_payload, entities,
)
from app.enterprise_tenancy.models import EnterprisePersistenceRequestError
from test_account_workspace_bootstrap import system, headers
from test_enterprise_workspace_persistence import _SQLiteTransactions


def envelope(values=None, record_type='course_record', record_id='module:one'):
    values = values if values is not None else {
        'schema': 'mentorflow.course.module.v1', 'plan_key': 'plan:one',
        'entity': 'module', 'entity_id': 'module:one',
        'payload': {'title': '课程模块', 'nodes': [{'id': 'knowledge-1', 'mastered': False}]},
    }
    return {'schema': 'mentorflow.refactor-sync.v1', 'version': 1,
            'record_type': record_type, 'record_id': record_id, 'values': values}


def entity_id(payload):
    return 'refactor.v1:' + payload['record_type'] + ':' + hashlib.sha256(payload['record_id'].encode()).hexdigest()


@pytest.fixture
def sync(system, monkeypatch):
    client, _, engine = system
    workspace = client.post('/api/enterprise/account/workspaces/initialize', headers=headers(), json={}).json()['workspaces'][0]
    monkeypatch.setattr(account_core_mvp.app.state, 'structured_sync_service',
                        StructuredSyncService(_SQLiteTransactions(engine)), raising=False)
    return client, engine, headers(workspace=workspace)


def put(sync, payload, *, revision=0, request_id=None, kind='learning_run'):
    client, _, auth = sync
    return client.put('/api/enterprise/sync/v1/entities/' + kind + '/' + entity_id(payload), headers=auth,
                      json={'request_id': request_id or uuid.uuid4().hex, 'expected_revision': revision, 'payload': payload})


@pytest.mark.parametrize('plan_key', ['plan:one', ''])
def test_valid_course_round_trip_persistence_and_cas(sync, plan_key):
    payload = envelope()
    payload['values']['plan_key'] = plan_key
    first = put(sync, payload, request_id='same-operation')
    assert first.status_code == 200, first.text
    assert first.json()['payload'] == payload
    assert first.json()['revision'] == 1
    assert put(sync, payload, request_id='same-operation').json() == first.json()
    # Read through a fresh DB connection, independently of the HTTP response.
    with sync[1].connect() as connection:
        stored = connection.execute(select(entities.c.payload).where(entities.c.entity_id == entity_id(payload))).scalar_one()
    assert json.loads(json.dumps(stored, ensure_ascii=False)) == payload
    changed = deepcopy(payload)
    changed['values']['payload']['nodes'][0]['mastered'] = True
    assert put(sync, changed).json()['code'] == 'SYNC_STALE_REVISION'
    assert put(sync, changed, request_id='same-operation').json()['code'] == 'SYNC_IDEMPOTENCY_KEY_REUSED'
    assert put(sync, changed, revision=1).json()['revision'] == 2


@pytest.mark.parametrize('field,value', [
    ('schema', 'mentorflow.course.Module.v1'), ('schema', 'mentorflow.course.module.v1000'),
    ('schema', 'mentorflow.course.' + 'a' * 41 + '.v1'), ('schema', None),
    ('entity', 'other'), ('entity', 'module1'), ('entity', None),
    ('entity_id', '../invalid'), ('entity_id', ''), ('entity_id', 7),
    ('plan_key', '/invalid'), ('plan_key', None),
    ('payload', []), ('payload', None),
])
def test_invalid_each_field_rejected(sync, field, value):
    payload = envelope()
    payload['values'][field] = value
    response = put(sync, payload)
    assert response.status_code == 422, response.text
    assert response.json()['code'] == 'SYNC_PAYLOAD_INVALID'


@pytest.mark.parametrize('shape', ['missing', 'extra', 'blob'])
def test_exact_field_set_no_blob_manifest_bypass(sync, shape):
    payload = envelope()
    if shape == 'missing':
        del payload['values']['entity']
    elif shape == 'extra':
        payload['values']['extra'] = True
    else:
        payload['values'] = {'blob_ref': {'schema': 'mentorflow.history-blob.v1', 'sha256': 'a' * 64,
                                        'size': 5, 'content_type': 'application/json'},
                             'record_type': 'course_record', 'record_id': payload['record_id'], 'summary': {}}
    assert put(sync, payload).status_code == 422


@pytest.mark.parametrize('extra', [0, 1])
def test_normalized_utf8_payload_size_boundary(sync, extra):
    payload = envelope()
    budget = 256 * 1024 - len(b'{"text":""}')
    payload['values']['payload'] = {'text': '中' * (budget // 3) + 'x' * (budget % 3 + extra)}
    encoded = json.dumps(payload['values']['payload'], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    assert len(encoded) == 256 * 1024 + extra
    response = put(sync, payload)
    assert response.status_code == (200 if extra == 0 else 422), response.text
    if extra:
        assert response.json()['code'] == 'SYNC_PAYLOAD_TOO_LARGE'


@pytest.mark.parametrize('depth', [32, 33])
def test_payload_depth_boundary(sync, depth):
    payload = envelope()
    nested = {}
    for _ in range(depth - 1):
        nested = {'child': nested}
    payload['values']['payload'] = nested
    assert put(sync, payload).status_code == (200 if depth == 32 else 422)


def test_extreme_depth_rejected_before_recursive_secret_scan():
    payload = envelope()
    nested = {}
    for _ in range(1500):
        nested = {'child': nested}
    payload['values']['payload'] = nested
    with pytest.raises(EnterprisePersistenceRequestError) as exc:
        _refactor_sync_payload('learning_run', entity_id(payload), payload)
    assert exc.value.code == 'SYNC_PAYLOAD_INVALID'


@pytest.mark.parametrize('value', [float('nan'), float('inf'), {1: 'non-string-key'}, ('not', 'json')])
def test_payload_is_strict_json(value):
    payload = envelope()
    payload['values']['payload'] = {'value': value}
    with pytest.raises(EnterprisePersistenceRequestError):
        _refactor_sync_payload('learning_run', entity_id(payload), payload)


def test_existing_note_normalization_size_rule_and_secret_rule_unchanged(sync):
    note = envelope({'plan_key': 'plan:one', 'task_id': 'task:one', 'text': 'existing'}, 'note', 'note:one')
    response = put(sync, note, kind='history')
    assert response.status_code == 200
    assert response.json()['payload']['values']['revision'] == 1
    note['values']['extra'] = True
    assert put(sync, note, revision=1, kind='history').status_code == 422
    del note['values']['extra']
    note['values']['text'] = 'x' * (256 * 1024)
    assert put(sync, note, revision=1, kind='history').json()['code'] == 'SYNC_PAYLOAD_TOO_LARGE'
    course = envelope()
    course['values']['payload'] = {'password': 'must-not-persist'}
    assert put(sync, course).json()['code'] == 'SYNC_SECRET_FIELD_REJECTED'


def test_kind_identity_and_account_scope_still_enforced(sync):
    payload = envelope()
    assert put(sync, payload, kind='history').status_code == 422
    client, engine, auth = sync
    assert put(sync, payload).status_code == 200
    workspace_b = client.post('/api/enterprise/account/workspaces/initialize', headers=headers('b'), json={}).json()['workspaces'][0]
    changed = deepcopy(payload)
    changed['values']['payload'] = {'title': 'B only'}
    response = client.put('/api/enterprise/sync/v1/entities/learning_run/' + entity_id(payload),
                          headers=headers('b', workspace_b),
                          json={'request_id': 'b-create', 'expected_revision': 0, 'payload': changed})
    assert response.status_code == 200
    with engine.connect() as connection:
        rows = connection.execute(select(entities.c.subject_id, entities.c.payload)).all()
    assert {r[0]: r[1] for r in rows} == {'user-a': payload, 'user-b': changed}
    assert client.put('/api/enterprise/sync/v1/entities/learning_run/' + entity_id(payload),
                      json={'request_id': 'anonymous', 'expected_revision': 0, 'payload': payload}).status_code == 401
