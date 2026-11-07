"""BUG-222: first-contact deletion has its own receipt intent."""
import hashlib
import json

from sqlalchemy import select
import pytest

from test_slim_course_record_sync import entity_id, envelope
from refactor.server import app as account_core_mvp
from refactor.server.enterprise_tenancy.structured_sync import StructuredSyncService, entities, requests
from slim_sqlite_support import system, headers, _SQLiteTransactions


@pytest.fixture
def sync(system, monkeypatch):
    client, _, engine = system
    workspace = client.post('/api/enterprise/account/workspaces/initialize',
                            headers=headers(), json={}).json()['workspaces'][0]
    monkeypatch.setattr(account_core_mvp.app.state, 'structured_sync_service',
                        StructuredSyncService(_SQLiteTransactions(engine)), raising=False)
    return client, engine, headers(workspace=workspace)


def test_missing_tombstone_is_idempotent_and_legacy_delete_stays_distinct(sync):
    client, engine, auth = sync
    missing = envelope(record_id='module:missing')
    path = '/api/enterprise/sync/v1/entities/learning_run/' + entity_id(missing)

    legacy_missing = client.post(path + '/delete', headers=auth,
                                 json={'request_id': 'old-delete-missing', 'expected_revision': 1})
    assert legacy_missing.status_code == 404

    body = {'request_id': 'first-contact-tombstone', 'expected_revision': 0}
    tombstone = client.post(path + '/tombstone', headers=auth, json=body)
    assert tombstone.status_code == 200
    result = tombstone.json()
    assert result == {'kind': 'learning_run', 'entity_id': entity_id(missing),
                      'revision': 1, 'deleted': True, 'payload': {}}
    assert client.post(path + '/tombstone', headers=auth, json=body).json() == result

    # The legacy route still rejects revision zero at the request boundary.
    assert client.post(path + '/delete', headers=auth, json=body).status_code == 422

    live = envelope(record_id='module:legacy-delete')
    live_path = '/api/enterprise/sync/v1/entities/learning_run/' + entity_id(live)
    created = client.put(live_path, headers=auth, json={'request_id': 'legacy-create',
        'expected_revision': 0, 'payload': live})
    assert created.status_code == 200
    delete_body = {'request_id': 'legacy-delete', 'expected_revision': 1}
    deleted = client.post(live_path + '/delete', headers=auth, json=delete_body)
    assert deleted.status_code == 200
    assert client.post(live_path + '/delete', headers=auth, json=delete_body).json() == deleted.json()
    assert client.post(live_path + '/tombstone', headers=auth, json=delete_body).json()['code'] == 'SYNC_IDEMPOTENCY_KEY_REUSED'

    with engine.connect() as connection:
        stored = connection.execute(select(entities.c.revision, entities.c.deleted, entities.c.payload)
                                    .where(entities.c.entity_id == entity_id(missing))).one()
        digests = dict(connection.execute(select(requests.c.request_id, requests.c.digest)).all())
    assert tuple(stored[:2]) == (1, True) and stored.payload == {}

    def legacy_digest(kind, identity, revision, deleting, payload):
        raw = json.dumps([kind, identity, revision, deleting, payload], sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode()
        return hashlib.sha256(raw).hexdigest()

    def tombstone_digest(identity):
        raw = json.dumps(['tombstone-if-missing-v1', 'learning_run', identity, 0, True, {}],
                         sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        return hashlib.sha256(raw).hexdigest()

    assert digests['legacy-create'] == legacy_digest('learning_run', entity_id(live), 0, False, live)
    assert digests['legacy-delete'] == legacy_digest('learning_run', entity_id(live), 1, True, {})
    assert digests['first-contact-tombstone'] == tombstone_digest(entity_id(missing))


def test_missing_tombstone_requires_revision_zero(sync):
    client, _, auth = sync
    payload = envelope(record_id='module:wrong-revision')
    path = '/api/enterprise/sync/v1/entities/learning_run/' + entity_id(payload) + '/tombstone'
    response = client.post(path, headers=auth, json={'request_id': 'nonzero-tombstone',
                            'expected_revision': 1})
    assert response.status_code == 409
    assert response.json()['code'] == 'SYNC_STALE_REVISION'
