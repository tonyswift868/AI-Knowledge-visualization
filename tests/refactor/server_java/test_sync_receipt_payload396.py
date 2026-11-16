"""Real HTTP and PostgreSQL checks for content-free idempotency receipts."""
import importlib.util
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from contract_support145 import sync145_backends
from test_sync_http_contract145 import BASE, _entity_id, _envelope, _headers, _wire_json


PREVIEW_SQL = """SELECT COUNT(*) AS total_receipts,
 COUNT(*) FILTER (WHERE response::jsonb ? 'payload') AS receipts_to_change,
 COALESCE(SUM(pg_column_size(response)), 0) AS total_response_bytes,
 COALESCE(SUM(pg_column_size(response)) FILTER
   (WHERE response::jsonb ? 'payload'), 0) AS response_bytes_to_change
 FROM public.structured_sync_requests"""


def _migration(connection):
    path = Path(__file__).resolve().parents[3] / 'refactor/server/migrations/versions/0054_sync_receipts_without_payload.py'
    spec = importlib.util.spec_from_file_location('migration396', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = SimpleNamespace(get_bind=lambda: connection,
        execute=lambda sql: connection.execute(text(sql)),
        get_context=lambda: SimpleNamespace(config=SimpleNamespace(print_stdout=lambda _: None)))
    module.upgrade()


def _put(clients, token, record, payload, request, revision=0, status=200):
    result = clients['java'].put(f'{BASE}/entities/learning_run/{_entity_id(record)}',
        headers=_headers(token, request), content=_wire_json(dict(
            request_id=request, expected_revision=revision, payload=payload)))
    assert result.status_code == status, result.text
    return result.json()


def _payload(record, entity, data):
    value = _envelope(record)
    value['values'].update(schema=f'mentorflow.course.{entity}.v1', entity=entity, payload=data)
    return value


def _no_marker(connection, marker):
    for table in ('structured_sync_requests', 'structured_sync_entities'):
        assert connection.execute(text(f'SELECT count(*) FROM public.{table} t WHERE row_to_json(t)::text LIKE :marker'),
                                  dict(marker='%' + marker + '%')).scalar() == 0


@pytest.mark.parametrize('mode', ['new', 'migrated', 'legacy'])
def test_receipt396_replay_and_conflict(sync145_backends, mode):
    clients = sync145_backends
    token = clients['jwks'].token(subject='user-145-a')
    record, request = uuid.uuid4().hex, uuid.uuid4().hex
    payload = _payload(record, 'chat_messages', dict(text='replay396-' + uuid.uuid4().hex))
    first = _put(clients, token, record, payload, request)
    assert first == dict(kind='learning_run', entity_id=_entity_id(record), revision=1,
                         deleted=False, payload=payload)
    engine = create_engine(clients['java_admin_url'])
    try:
        with engine.begin() as c:
            stored = c.execute(text('SELECT response FROM structured_sync_requests WHERE request_id=:r'), dict(r=request)).scalar()
            assert 'payload' not in stored
            if mode != 'new':
                c.execute(text('UPDATE structured_sync_requests SET response=CAST(:value AS json) WHERE request_id=:r'),
                          dict(value=json.dumps(first), r=request))
            if mode == 'migrated':
                _migration(c)
        assert _put(clients, token, record, payload, request) == first
        changed = _payload(record, 'chat_messages', dict(text='different'))
        assert _put(clients, token, record, changed, request, status=409)['code'] == 'SYNC_IDEMPOTENCY_KEY_REUSED'
        # Replaying an earlier create after deletion must not resurrect it.
        deleted = clients['java'].post(f'{BASE}/entities/learning_run/{_entity_id(record)}/delete',
            headers=_headers(token, request+'d'), content=_wire_json(dict(request_id=request+'d', expected_revision=1)))
        assert deleted.status_code == 200, deleted.text
        assert _put(clients, token, record, payload, request) == first
        with engine.begin() as c:
            row = c.execute(text('SELECT deleted,payload FROM structured_sync_entities WHERE entity_id=:id'),dict(id=_entity_id(record))).one()
            assert row.deleted and row.payload == {}
    finally:
        engine.dispose()


def test_receipt396_deleted_chat_and_teacher_preset_content(sync145_backends):
    clients = sync145_backends
    token = clients['jwks'].token(subject='user-145-a')
    marker = 'deleted396-' + uuid.uuid4().hex
    for entity in ('chat_sessions', 'chat_messages'):
        record = uuid.uuid4().hex
        _put(clients, token, record, _payload(record, entity, dict(text=marker)), record)
        response = clients['java'].post(f'{BASE}/entities/learning_run/{_entity_id(record)}/delete',
            headers=_headers(token, record+'d'), content=_wire_json(dict(request_id=record+'d', expected_revision=1)))
        assert response.status_code == 200, response.text
    record = uuid.uuid4().hex
    live = 'live396-' + uuid.uuid4().hex
    _put(clients, token, record, _payload(record, 'account_settings', dict(
        teacher_presets=[dict(name=marker), dict(name=live)])), record)
    updated = _put(clients, token, record, _payload(record, 'account_settings', dict(
        teacher_presets=[dict(name=live)])), record+'u', revision=1)
    engine = create_engine(clients['java_admin_url'])
    try:
        with engine.connect() as c:
            _no_marker(c, marker)
            assert c.execute(text('SELECT payload FROM structured_sync_entities WHERE entity_id=:id'),
                             dict(id=_entity_id(record))).scalar() == updated['payload']
    finally:
        engine.dispose()


def test_receipt396_migration_idempotent_and_preserves_live_data(sync145_backends):
    clients = sync145_backends
    engine = create_engine(clients['java_admin_url'])
    try:
        with engine.begin() as c:
            c.execute(text("UPDATE structured_sync_requests SET response=(response::jsonb || jsonb_build_object('payload',jsonb_build_object('old','migration396')))::json"))
            before = c.execute(text("SELECT issuer,subject_id,organization_id,workspace_id,request_id,digest,(response::jsonb-'payload')::text FROM structured_sync_requests ORDER BY issuer,subject_id,request_id")).all()
            entities = c.execute(text('SELECT row_to_json(t)::text FROM structured_sync_entities t ORDER BY issuer,subject_id,kind,entity_id')).all()
            manifests = c.execute(text('SELECT row_to_json(t)::text FROM rehydration_manifests t ORDER BY issuer,subject_id')).all()
            preview = c.execute(text(PREVIEW_SQL)).one()
            assert preview.receipts_to_change > 0 and preview.total_response_bytes > 0
            _migration(c)
            once = c.execute(text('SELECT issuer,subject_id,organization_id,workspace_id,request_id,digest,response::jsonb::text FROM structured_sync_requests ORDER BY issuer,subject_id,request_id')).all()
            assert once == before
            _migration(c)
            assert c.execute(text('SELECT issuer,subject_id,organization_id,workspace_id,request_id,digest,response::jsonb::text FROM structured_sync_requests ORDER BY issuer,subject_id,request_id')).all() == once
            assert c.execute(text("SELECT count(*) FROM structured_sync_requests WHERE response::jsonb ? 'payload'")).scalar() == 0
            assert c.execute(text('SELECT row_to_json(t)::text FROM structured_sync_entities t ORDER BY issuer,subject_id,kind,entity_id')).all() == entities
            assert c.execute(text('SELECT row_to_json(t)::text FROM rehydration_manifests t ORDER BY issuer,subject_id')).all() == manifests
    finally:
        engine.dispose()


def test_receipt396_migration_owner_can_scrub_forced_rls(sync145_backends):
    clients = sync145_backends
    record = uuid.uuid4().hex
    token = clients['jwks'].token(subject='user-145-a')
    _put(clients, token, record, _envelope(record), record)
    admin = create_engine(clients['java_admin_url'])
    migrator = create_engine(clients['java_migration_url'])
    try:
        with admin.begin() as c:
            c.execute(text("UPDATE structured_sync_requests SET response=(response::jsonb || CAST(:old AS jsonb))::json"),
                      dict(old=json.dumps({'payload': {'old': True}})))
        with migrator.begin() as c:
            c.execute(text('SET TRANSACTION READ ONLY'))
            c.execute(text('SET LOCAL ROLE mentorflow_closure'))
            assert c.execute(text(PREVIEW_SQL)).one().receipts_to_change > 0
        with migrator.begin() as c:
            assert c.execute(text('SELECT current_user')).scalar() == 'mentorflow_migrator'
            _migration(c)
        with admin.connect() as c:
            assert c.execute(text("SELECT count(*) FROM structured_sync_requests WHERE response::jsonb ? 'payload'")).scalar() == 0
            assert c.execute(text("SELECT count(*) FROM pg_policies WHERE policyname='receipt_scrub_0054'")).scalar() == 0
    finally:
        admin.dispose()
        migrator.dispose()
