"""Local uvicorn startup and isolation of the new runtime from backend/desktop."""
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import partial

import httpx
from sqlalchemy import create_engine, event, text
import uvicorn

from contract_support import principal, runtime
from slim_sqlite_support import _SQLiteTransactions

ROOT = Path(__file__).resolve().parents[3]


def test_slim_import_never_loads_backend_or_legacy_entry(tmp_path):
    script=tmp_path/'isolated_import.py'
    script.write_text('''import sys
class NoLegacy:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'app' or fullname.startswith(('app.', 'backend.', 'desktop.')):
            raise AssertionError('Old runtime imported: ' + fullname)
sys.meta_path.insert(0, NoLegacy())
from refactor.server.app import app
assert app.title == 'MentorFlow Account and Sync'
assert not any(name.startswith('refactor.server.migrations') for name in sys.modules)
print('standalone import passed')
''', encoding='utf-8')
    result=subprocess.run([sys.executable,'-B',str(script)],cwd=ROOT,env=dict(os.environ,PYTHONPATH=str(ROOT)),
                          capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_slim_only_selected_routes_and_no_original_storage():
    from refactor.server.app import app
    paths=app.openapi()['paths']
    assert len(paths)==38
    assert set(paths['/api/enterprise/sync/v1/entities/bulk']) == {'post'}
    assert set(p for p in paths if not p.startswith('/api/enterprise/')) == {
        '/health/live','/health/startup','/health/ready'}
    assert not any(part in path for path in paths for part in (
        '/objects/', '/metadata', '/imports', '/active-teaching', '/documents', '/jobs', '/config', '/callback'))


def test_slim_existing_secret_adapter_and_dockerfile(tmp_path):
    from refactor.server.entrypoint import runtime_environment
    (tmp_path/'postgres_app_password').write_text('test:p@ss',encoding='utf-8')
    source={'MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS':'1','MENTORFLOW_ACCOUNT_LIFECYCLE_ENABLED':'1',
            'MENTORFLOW_MIGRATION_DATABASE_URL':'must-not-reach-runtime'}
    env=runtime_environment(source,tmp_path)
    assert 'test%3Ap%40ss@postgres:5432/mentorflow' in env['MENTORFLOW_DATABASE_URL']
    assert env['MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS']=='1'
    assert 'MENTORFLOW_MIGRATION_DATABASE_URL' not in env
    assert runtime_environment({'MENTORFLOW_DATABASE_URL':'explicit'},tmp_path)['MENTORFLOW_DATABASE_URL']=='explicit'
    docker=(ROOT/'refactor/server/Dockerfile').read_text()
    assert 'COPY refactor/server /app/refactor/server' in docker
    assert 'COPY backend' not in docker and 'COPY . ' not in docker


def test_slim_real_uvicorn_health_and_local_business_requests(schema_template, tmp_path, monkeypatch):
    """SQLite is test-only; production configure/verify still require PostgreSQL 0042."""
    import shutil
    from refactor.server import app as entry
    from refactor.server.enterprise.identity.native import NativeSessions
    from refactor.server.enterprise.identity import runtime as identity_runtime
    from refactor.server.enterprise.identity.oidc import IdentityError
    from test_slim_course_record_sync import envelope, entity_id
    database=tmp_path/'uvicorn.db'
    shutil.copyfile(schema_template,database)
    engine=create_engine(f'sqlite:///{database.as_posix()}')
    statements=[]
    event.listen(engine,'before_cursor_execute',lambda conn,cursor,statement,*args:statements.append(statement))
    identity=runtime('refactor.server',engine)
    # The fake verifier and the real session validation share one clock.
    # datetime.now().timestamp() can round slightly ahead of time.time() on
    # Windows, spuriously rejecting the fixture as a future-issued principal.
    now=datetime.fromtimestamp(1_700_000_000,timezone.utc)
    verified=principal('refactor.server').model_copy(update={
        'issued_at':now,'authentication_time':now,'expires_at':now+timedelta(minutes=5)})
    class Verifier:
        async def authenticate(self, username, password):
            assert username=='user-a' and password=='test-only'
            return verified
        async def aclose(self): pass
    identity.native_sessions=NativeSessions(Verifier(),verified.issuer,clock=lambda:now.timestamp())
    # Tenant context rechecks expiry too; pass its existing `now` argument
    # through to the original validator, preserving all membership checks.
    monkeypatch.setattr(identity_runtime,'enterprise_request_context',
        partial(identity_runtime.enterprise_request_context,now=now))
    def verify():
        with engine.connect() as conn:
            assert conn.scalar(text('SELECT version_num FROM alembic_version'))=='0044_rag_documents'
    monkeypatch.setattr(entry.EnterpriseIdentityConfig,'from_env',lambda:identity.config)
    monkeypatch.setattr(entry.orm_runtime,'configure_enterprise_orm',lambda:engine)
    monkeypatch.setattr(entry.orm_runtime,'verify_enterprise_runtime_schema',verify)
    monkeypatch.setattr(entry,'build_enterprise_identity_runtime',lambda **kwargs:identity)
    monkeypatch.setattr(entry,'PostgresTenantTransactionManager',_SQLiteTransactions)
    monkeypatch.delenv('MENTORFLOW_ADMIN_OVERVIEW_CONFIG',raising=False)
    registry=entry.account_core_health_registry
    monkeypatch.setattr(registry,'_probes',{
        'postgres':(lambda:(True,'SQLITE_TEST_FIXTURE'),True),
        'keycloak_discovery':(lambda:(True,'KEYCLOAK_TEST_DOUBLE'),True)})
    catalog=tmp_path/'catalog';catalog.mkdir()
    (catalog/'model-prices.json').write_text('{"version":"fixture"}',encoding='utf-8')
    monkeypatch.setenv('MENTORFLOW_CATALOG_DIR',str(catalog))
    sock=socket.socket();sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(entry.app,host='127.0.0.1',port=port,log_level='error',
        access_log=False,proxy_headers=True,forwarded_allow_ips='127.0.0.1'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
    thread.start()
    evidence={}
    try:
        deadline=time.monotonic()+10
        while not server.started and thread.is_alive() and time.monotonic()<deadline:
            time.sleep(.02)
        assert server.started
        # The actual lifespan checked the existing schema and did not write DDL.
        assert statements==['SELECT version_num FROM alembic_version']
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=5,
                          headers={'X-Forwarded-Proto':'https'}) as http:
            for name in ('live','startup','ready'):
                response=http.get('/health/'+name)
                evidence['health/'+name]=response.status_code
                assert response.status_code==200,response.text
            pair=http.post('/api/enterprise/auth/native/login',json={'username':'user-a','password':'test-only'})
            assert pair.status_code==200,pair.text
            http.headers['Authorization']='Bearer '+pair.json()['access_token']
            evidence['native/session']=http.get('/api/enterprise/auth/native/session').status_code
            space=http.post('/api/enterprise/account/workspaces/initialize',json={}).json()['workspaces'][0]
            http.headers.update({'X-MentorFlow-Organization-ID':space['organization_id'],
                                 'X-MentorFlow-Workspace-ID':space['id']})
            payload=envelope()
            response=http.put('/api/enterprise/sync/v1/entities/learning_run/'+entity_id(payload),
                json={'request_id':'uvicorn-course','expected_revision':0,'payload':payload})
            evidence['course_record']=response.status_code
            assert response.status_code==200,response.text
            changes=http.get('/api/enterprise/sync/v1/changes')
            assert changes.json()['entities'][0]['payload']==payload
            evidence['changes']=changes.status_code
            result=http.get('/api/enterprise/catalog/model-prices')
            evidence['catalog']=result.status_code
            evidence['catalog_etag']=result.headers['etag']
            assert all(status==200 for key,status in evidence.items() if key!='catalog_etag')
        assert not any(s.lstrip().upper().startswith(('CREATE ','ALTER ','DROP ')) for s in statements)
        (Path(os.environ['TEMP'])/'uvicorn-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    finally:
        server.should_exit=True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()
        engine.dispose()
