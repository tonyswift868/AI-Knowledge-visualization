"""Incremental HTTP contract; PostgreSQL/RLS evidence is in the PG lab test."""
import pytest
from sqlalchemy import text

from test_slim_course_record_sync import sync, put, envelope, entity_id
from slim_sqlite_support import system, headers

PATH = '/api/enterprise/sync/v1/changes'


def test_pages_cursor_tombstone_idempotency_and_account_isolation(sync):
    client, engine, auth = sync
    payloads = [envelope(record_id=f'module:{i}') for i in range(3)]
    for i, payload in enumerate(payloads):
        payload['values']['entity_id'] = f'module:{i}'
        assert put(sync, payload, request_id=f'create-{i}').status_code == 200
    first = client.get(PATH, headers=auth, params={'limit': 2}).json()
    assert first['has_more'] and len(first['entities']) == 2 and first['since'] == '0'
    assert first['next_cursor'] == str(first['entities'][-1]['change_seq'])
    second = client.get(PATH, headers=auth, params={'since': first['next_cursor'], 'limit': 2}).json()
    assert not second['has_more'] and len(second['entities']) == 1
    cursor = second['next_cursor']
    # Exact replay never consumes another change sequence.
    assert put(sync, payloads[0], request_id='create-0').status_code == 200
    empty = client.get(PATH, headers=auth, params={'since': cursor}).json()
    assert empty['entities'] == [] and empty['next_cursor'] == cursor
    deleted = client.post('/api/enterprise/sync/v1/entities/learning_run/' + entity_id(payloads[0]) + '/delete',
                          headers=auth, json={'request_id': 'delete-0', 'expected_revision': 1})
    assert deleted.status_code == 200
    delta = client.get(PATH, headers=auth, params={'since': cursor})
    assert delta.headers['cache-control'] == 'no-store'
    rows = delta.json()['entities']
    assert len(rows) == 1 and rows[0]['deleted'] and rows[0]['payload'] == {} and rows[0]['revision'] == 2
    assert rows[0]['change_seq'] > int(cursor)
    workspace = client.post('/api/enterprise/account/workspaces/initialize', headers=headers('b'), json={}).json()['workspaces'][0]
    other = client.get(PATH, headers=headers('b', workspace)).json()
    assert other['entities'] == [] and other['next_cursor'] == '0'
    assert client.get(PATH, headers=headers('b') | {k: v for k, v in auth.items() if k != 'Authorization'}).status_code == 403
    with engine.connect() as c:
        assert c.execute(text('SELECT count(DISTINCT change_seq) FROM structured_sync_entities')).scalar_one() == 3


@pytest.mark.parametrize('query', [{'since': -1}, {'since': 2**63}, {'since': 'garbage'},
                                  {'kind': 'unknown'}, {'limit': 0}, {'limit': 501}])
def test_invalid_query_rejected(sync, query):
    assert sync[0].get(PATH, headers=sync[2], params=query).status_code == 422


def test_requires_authenticated_workspace(sync):
    assert sync[0].get(PATH).status_code == 401
    assert sync[0].get(PATH, headers=headers()).status_code in (401, 403, 422)
