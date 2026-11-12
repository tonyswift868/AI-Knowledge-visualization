"""Same requests/real SQLite storage on both entries, plus unchanged migration chain."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from contract_support import ISSUER, module, runtime
from test_slim_http_contract import observe

ROOT = Path(__file__).resolve().parents[3]
PREFIX = '/api/enterprise'


def trace(root, database, catalog, monkeypatch):
    entry = module(root, 'account_core_mvp' if root == 'app' else 'app')
    sync = module(root, 'enterprise_tenancy.structured_sync')
    bootstrap = module(root, 'enterprise_tenancy.workspace_bootstrap')
    engine = create_engine(f'sqlite:///{database.as_posix()}')
    identity = runtime(root, engine)
    class Transactions:
        @contextmanager
        def transaction(self, context):
            with engine.begin() as connection:
                yield SimpleNamespace(connection=connection)
    monkeypatch.setattr(entry.app.state, 'enterprise_identity_runtime', identity, raising=False)
    monkeypatch.setattr(entry.app.state, 'account_workspace_bootstrap', bootstrap.AccountWorkspaceBootstrap(engine, issuer=ISSUER), raising=False)
    monkeypatch.setattr(entry.app.state, 'structured_sync_service', sync.StructuredSyncService(Transactions()), raising=False)
    monkeypatch.setenv('MENTORFLOW_CATALOG_DIR', str(catalog))
    catalog.mkdir(exist_ok=True)
    (catalog/'model-prices.json').write_bytes(b'{"version":"test", "models":[]}\n')
    client = TestClient(entry.app, base_url='https://fixture.invalid')
    auth = {'Authorization': 'Bearer token-a', 'X-Request-ID': 'slim-contract'}
    results = []
    def request(method, path, **kwargs):
        response = client.request(method, PREFIX+path, headers=auth, **kwargs)
        results.append(observe(response))
        return response
    try:
        assert request('GET', '/account/workspaces').json()['initialization_required']
        space = request('POST', '/account/workspaces/initialize', json={}).json()['workspaces'][0]
        assert not request('GET', '/account/workspaces').json()['initialization_required']
        assert request('POST', '/account/workspaces/initialize', json={}).json()['workspaces'][0] == space
        auth.update({'X-MentorFlow-Organization-ID': space['organization_id'], 'X-MentorFlow-Workspace-ID': space['id']})
        assert request('GET', '/auth/me').status_code == 200
        payload = {'schema':'mentorflow.refactor-sync.v1','version':1,'record_type':'course_record',
                   'record_id':'message:one','values':{'schema':'mentorflow.course.message.v1',
                   'entity':'message','entity_id':'message:one','plan_key':'','payload':{'text':'你好'}}}
        entity = 'refactor.v1:course_record:' + hashlib.sha256(b'message:one').hexdigest()
        path = '/sync/v1/entities/learning_run/' + entity
        body = {'request_id':'create-message', 'expected_revision':0, 'payload':payload}
        assert request('PUT', path, json=body).json()['revision'] == 1
        assert request('PUT', path, json=body).json()['revision'] == 1
        assert request('PUT', path, json={**body, 'request_id':'stale'}).status_code == 409
        delta = request('GET', '/sync/v1/changes?limit=1').json()
        assert delta['entities'][0]['payload'] == payload
        cursor = delta['next_cursor']
        assert request('GET', '/sync/v1/changes?since='+cursor).json()['entities'] == []
        assert request('POST', path+'/delete', json={'request_id':'remove-message','expected_revision':1}).json()['deleted']
        assert request('GET', '/sync/v1/changes?since='+cursor).json()['entities'][0]['deleted']
        # Snapshot explicitly requires PostgreSQL; SQLite must never report empty/success.
        assert request('GET', '/sync/v1/snapshot').status_code == 503
        catalog_response = request('GET', '/catalog/model-prices')
        assert catalog_response.status_code == 200
        auth['If-None-Match'] = catalog_response.headers['etag']
        assert request('GET', '/catalog/model-prices').status_code == 304
        del auth['If-None-Match']
        auth['Authorization'] = 'Bearer token-b'
        assert request('GET', '/sync/v1/changes').status_code == 403
        return results
    finally:
        client.close()
        engine.dispose()


def test_slim_successful_sqlite_http_roundtrip_matches_old(schema_template, tmp_path, monkeypatch):
    traces=[]
    for label, root in [('old','app'),('new','refactor.server')]:
        database=tmp_path/(label+'.db')
        shutil.copyfile(schema_template,database)
        traces.append(trace(root, database, tmp_path/'catalog', monkeypatch))
    assert traces[0] == traces[1]


def signature(path):
    engine=create_engine(f'sqlite:///{path.as_posix()}')
    try:
        schema=inspect(engine)
        value = {table: {
            'columns': [(c['name'],str(c['type']),c['nullable'],c['default'],c['primary_key']) for c in schema.get_columns(table)],
            'indexes': schema.get_indexes(table), 'foreign_keys':schema.get_foreign_keys(table),
            'unique':schema.get_unique_constraints(table), 'checks':schema.get_check_constraints(table),
        } for table in schema.get_table_names()}
        # SQLAlchemy reflects partial-index predicates as TextClause objects.
        return json.loads(json.dumps(value, default=str))
    finally:
        engine.dispose()


def test_slim_original_migration_chain_same_head_and_schema(schema_template, tmp_path, monkeypatch):
    monkeypatch.delenv('MENTORFLOW_MIGRATION_DATABASE_URL', raising=False)
    monkeypatch.delenv('MENTORFLOW_DEPLOYMENT_MODE', raising=False)
    old_database=tmp_path/'old-migrations.db'
    config=Config(str(ROOT/'backend/alembic.ini'))
    config.set_main_option('script_location',str(ROOT/'backend/alembic'))
    config.set_main_option('sqlalchemy.url',f'sqlite:///{old_database.as_posix()}')
    config.attributes['configure_logger']=False
    command.upgrade(config,'head')
    assert signature(old_database) == signature(schema_template)
    for source in (ROOT/'backend/alembic/versions').glob('*.py'):
        expected = source.read_text(encoding='utf-8-sig').replace('from app.orm import',
            'from refactor.server.migrations.legacy.orm import').replace('from app import expedition_orm',
            'from refactor.server.migrations.legacy import expedition_orm')
        assert (ROOT/'refactor/server/migrations/versions'/source.name).read_text(encoding='utf-8') == expected
    engine=create_engine(f'sqlite:///{schema_template.as_posix()}')
    try:
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0051_sync_closure_fence'
    finally:
        engine.dispose()


def test_slim_postgres_snapshot_projection_and_sql_contract():
    from datetime import datetime, timezone
    source={'issuer':ISSUER,'subject_id':'user-a','organization_id':'org','workspace_id':'ws'}
    row={'revision':'1:2:', 'observed_at':datetime(2026,9,29,tzinfo=timezone.utc),
         'item_count':2,'byte_count':1200,'manifest':{'scope_version':'SUPPORTED_REHYDRATION_SCOPE_V1',
         'initialized':True,'generation':2,'created_at':'2026-09-29','updated_at':'2026-09-29'},
         'entities':[{'kind':'learning_run','entity_id':'message','revision':1,'deleted':False,'payload':{'text':'课堂记录'}},
                     {'kind':'history','entity_id':'removed','revision':2,'deleted':True,'payload':{}}]}
    statements=[]
    class Connection:
        dialect=SimpleNamespace(name='postgresql')
        def execute(self, query, scope):
            assert scope == source
            statements.append(str(query))
            return SimpleNamespace(mappings=lambda:SimpleNamespace(one=lambda:row))
    results=[module(root,'enterprise_tenancy.sync_snapshot').observe(Connection(),source)
             for root in ('app','refactor.server')]
    assert results[0] == results[1]
    assert statements[0] == statements[1]
    assert results[1]['complete'] and results[1]['observation']['counts']['tombstones'] == 1
