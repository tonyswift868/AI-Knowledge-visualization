"""Actual MF-U04 admission and new bundle rehearsal on owned loopback PG."""
import json
import subprocess
import sys
import hashlib

import pytest

from alembic import command

from mfu10_local_lab import assemble, environment, server, PACKAGE
from test_sync_changes_postgres import lab, BASE, HEAD
from test_course_record_sync import envelope, entity_id


def gate(root, lab):
    code = '''
import json
from app import orm
orm.configure_enterprise_orm()
try:
    orm.verify_enterprise_runtime_schema()
except orm.DatabaseRuntimeConfigurationError as error:
    print(json.dumps({'accepted': False, 'reason': str(error)}))
else:
    print(json.dumps({'accepted': True}))
finally:
    orm.engine.dispose()
'''
    result = subprocess.run([sys.executable, '-B', '-c', code], cwd=root,
        env=environment(root, lab), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_actual_mfu04_rejects_0042_and_accepts_restored_0041(lab, tmp_path):
    root = assemble(tmp_path / 'mfu04')
    assert gate(root, lab) == {'accepted': True}
    command.upgrade(lab.config, HEAD)
    env = environment(root, lab)
    env.update(MFU10_ACTOR=lab.contexts[0][0].model_dump_json(),
               MFU10_TENANT=lab.contexts[0][1].model_dump_json())
    code = '''
import json,os
from app import orm
from app.enterprise.contracts_v1 import ActorContext,TenantContext
from app.enterprise_tenancy.context import PostgresTenantTransactionManager
from app.enterprise_tenancy.structured_sync import StructuredSyncService,Write,Delete
engine=orm.configure_enterprise_orm()
service=StructuredSyncService(PostgresTenantTransactionManager(engine))
actor=ActorContext.model_validate_json(os.environ['MFU10_ACTOR'])
tenant=TenantContext.model_validate_json(os.environ['MFU10_TENANT'])
first=service.write(actor,tenant,'plan','legacy-0042',Write(
    request_id='legacy-insert',expected_revision=0,payload={'title':'legacy','resource_ids':[]}))
second=service.write(actor,tenant,'plan','legacy-0042',Write(
    request_id='legacy-update',expected_revision=1,payload={'title':'changed','resource_ids':[]}))
dead=service.write(actor,tenant,'plan','legacy-0042',Delete(
    request_id='legacy-delete',expected_revision=2),deleting=True)
assert first['revision']==1 and second['revision']==2 and dead['revision']==3
assert any(row['entity_id']=='legacy-0042' and row['deleted']
           for row in service.snapshot(actor,tenant)['entities'])
engine.dispose()
print('MF-U04 service INSERT UPDATE DELETE snapshot on 0042: PASS (startup is separately rejected)')
'''
    result = subprocess.run([sys.executable, '-B', '-c', code], cwd=root, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    with lab.connect() as connection:
        row = connection.execute("SELECT change_seq,revision,deleted FROM structured_sync_entities "
                                 "WHERE entity_id='legacy-0042'").fetchone()
        assert row[0] >= 3 and tuple(row[1:]) == (3, True)
    result = gate(root, lab)
    assert result == {'accepted': False,
                      'reason': 'enterprise PostgreSQL is not at the canonical migration head'}
    command.downgrade(lab.config, BASE)
    assert gate(root, lab) == {'accepted': True}


def test_candidate_real_startup_https_flows_and_legacy_rollback(lab, tmp_path):
    command.upgrade(lab.config, HEAD)
    root = assemble(tmp_path / 'candidate', overlay=True)
    assert gate(root, lab) == {'accepted': True}
    catalog = root / 'catalog'
    catalog.mkdir()
    with server(root, lab, catalog=catalog) as client:
        assert client.get('/health/live').status_code == 200
        assert client.get('/health/startup').status_code == 200
        login = client.post('/api/enterprise/auth/native/login', json={
            'username': 'synthetic-user-01', 'password': 'local-synthetic-password'})
        assert login.status_code == 200, login.text
        first = login.json()
        headers = {'Authorization': 'Bearer ' + first['access_token'],
                   'X-MentorFlow-Organization-ID': 'synthetic-org-1',
                   'X-MentorFlow-Workspace-ID': 'synthetic-org-1-workspace-1'}
        payload = envelope()
        url = '/api/enterprise/sync/v1/entities/learning_run/' + entity_id(payload)
        response = client.put(url, headers=headers, json={
            'request_id': 'rehearsal-course', 'expected_revision': 0, 'payload': payload})
        assert response.status_code == 200, response.text
        assert response.json()['payload'] == payload
        changes = client.get('/api/enterprise/sync/v1/changes?kind=learning_run&limit=1', headers=headers)
        assert changes.status_code == 200, changes.text
        assert changes.json()['entities'][0]['payload'] == payload
        cursor = changes.json()['next_cursor']
        assert client.get('/api/enterprise/sync/v1/changes?since=' + cursor,
                          headers=headers).json()['entities'] == []
        catalog_url = '/api/enterprise/catalog/model-prices'
        assert client.get(catalog_url).status_code == 401
        assert client.get(catalog_url, headers=headers).status_code == 404
        raw = b'{"version":"synthetic-rehearsal","prices":[]}'
        (catalog / 'model-prices.json').write_bytes(raw)
        response = client.get(catalog_url, headers=headers)
        assert response.status_code == 200 and response.content == raw
        etag = '"' + hashlib.sha256(raw).hexdigest() + '"'
        assert response.headers['etag'] == etag
        assert client.get(catalog_url, headers=dict(headers, **{'If-None-Match': etag})).status_code == 304
        session_url = '/api/enterprise/auth/native/session'
        bearer = {'Authorization': headers['Authorization']}
        assert client.get(session_url, headers=bearer).json() == {'state': 'active'}
        assert client.get(session_url).status_code == 401
        second = client.post('/api/enterprise/auth/native/login', json={
            'username': 'synthetic-user-01', 'password': 'local-synthetic-password'}).json()
        superseded = client.get(session_url, headers=bearer)
        assert superseded.status_code == 401 and superseded.json()['code'] == 'NATIVE_SESSION_SUPERSEDED'
        refreshed = client.post('/api/enterprise/auth/native/refresh', json={'refresh_token': first['refresh_token']})
        assert refreshed.status_code == 401 and refreshed.json()['code'] == 'NATIVE_SESSION_SUPERSEDED'
        headers['Authorization'] = 'Bearer ' + second['access_token']
        assert client.get(session_url, headers={'Authorization': headers['Authorization']}).status_code == 200
        assert client.get('/api/enterprise/sync/v1/objects/' + 'a' * 64, headers=headers).status_code == 404
        assert client.put('/api/enterprise/sync/v1/objects/' + 'a' * 64, headers=headers, content=b'absent').status_code == 404
    # Unmodified old write/snapshot implementation still works on the new
    # structure, but cannot launch: its independent admission rejects 0042.
    old = assemble(tmp_path / 'rollback')
    assert gate(old, lab)['accepted'] is False
    with pytest.raises(AssertionError, match='canonical migration head'):
        with server(old, lab):
            pytest.fail('MF-U04 must fail actual startup on 0042')
    command.downgrade(lab.config, BASE)
    with server(old, lab) as client:
        assert client.get('/health/startup').status_code == 200
        token = client.post('/api/enterprise/auth/native/login', json={
            'username': 'synthetic-user-01', 'password': 'local-synthetic-password'}).json()['access_token']
        headers['Authorization'] = 'Bearer ' + token
        snapshot = client.get('/api/enterprise/sync/v1/snapshot', headers=headers)
        assert snapshot.status_code == 200
        assert any(row['payload'] == payload for row in snapshot.json()['entities'])
        legacy = client.put('/api/enterprise/sync/v1/entities/plan/old-after-rollback', headers=headers,
            json={'request_id': 'old-after-rollback', 'expected_revision': 0,
                  'payload': {'title': 'legacy HTTP write after rollback', 'resource_ids': []}})
        assert legacy.status_code == 200, legacy.text
    command.upgrade(lab.config, HEAD)
    with server(root, lab, catalog=catalog) as client:
        assert client.get('/health/startup').status_code == 200
        token = client.post('/api/enterprise/auth/native/login', json={
            'username': 'synthetic-user-01', 'password': 'local-synthetic-password'}).json()['access_token']
        headers['Authorization'] = 'Bearer ' + token
        returned = client.get('/api/enterprise/sync/v1/changes?since=' + cursor, headers=headers).json()
        assert returned['entities'][0]['payload'] == payload  # Re-upgrade never reuses a cursor.
    trace = json.loads((root / 'runtime-trace.json').read_text(encoding='utf-8'))
    audit = json.loads((PACKAGE / 'runtime-audit.json').read_text(encoding='utf-8'))
    assert set(trace) <= {entry['file'] for entry in audit}
    assert not any('account_object_storage' in path for path in trace)
    for assembled, mode in ((old, 'base'), (root, 'candidate')):
        code = '''
import importlib.util
from pathlib import Path
import sys
package=Path(sys.argv[1])
spec=importlib.util.spec_from_file_location('mfu10_image_check',package/'image_check.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.check(sys.argv[2],Path.cwd(),package)
'''
        result = subprocess.run([sys.executable, '-B', '-c', code, str(PACKAGE), mode], cwd=assembled,
            env=environment(assembled, lab), capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
    # Recreate the incident class: hashes alone can pass for broken imports.
    bad_package = tmp_path / 'bad-import-bundle'
    bad_package.mkdir()
    for name in ('image_check.py', 'runtime-audit.json', 'manifest.json'):
        (bad_package / name).write_bytes((PACKAGE / name).read_bytes())
    source = root / 'backend/app/enterprise/catalog_api.py'
    original = source.read_bytes()
    try:
        source.write_bytes(original + b'\nimport refactor.server.never_deployed_dependency\n')
        manifest = json.loads((bad_package / 'manifest.json').read_text(encoding='utf-8'))
        manifest['sha256']['backend/app/enterprise/catalog_api.py'] = hashlib.sha256(source.read_bytes()).hexdigest()
        (bad_package / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        result = subprocess.run([sys.executable, '-B', '-c', code, str(bad_package), 'candidate'], cwd=root,
            env=environment(root, lab), capture_output=True, text=True, timeout=30)
        assert result.returncode != 0 and 'ModuleNotFoundError' in result.stderr
        assert 'never_deployed_dependency' in result.stderr
    finally:
        source.write_bytes(original)


def test_0042_admission_rejects_broken_trigger_sequence_and_policy(lab):
    from app.enterprise_tenancy.sync_schema import require_structured_sync_schema
    command.upgrade(lab.config, HEAD)
    with lab.engine.connect() as connection:
        require_structured_sync_schema(connection)
    # Deliberate drift is restored after each check in this owned isolated DB.
    for change in (
        'ALTER TABLE structured_sync_entities DISABLE TRIGGER structured_sync_change_stamp',
        'ALTER SEQUENCE structured_sync_change_seq CACHE 2',
        'GRANT UPDATE ON SEQUENCE structured_sync_change_seq TO mentorflow_app',
        'DROP INDEX ix_structured_sync_changes_scope',
        'ALTER TABLE structured_sync_entities NO FORCE ROW LEVEL SECURITY',
    ):
        with lab.connect() as admin:
            admin.execute(change)
            # The checking connection must see the drift; restore explicitly.
            admin.commit()
            with lab.engine.connect() as connection:
                with pytest.raises(ValueError):
                    require_structured_sync_schema(connection)
            reverse = {
                'ALTER TABLE structured_sync_entities DISABLE TRIGGER structured_sync_change_stamp':
                    'ALTER TABLE structured_sync_entities ENABLE TRIGGER structured_sync_change_stamp',
                'ALTER SEQUENCE structured_sync_change_seq CACHE 2': 'ALTER SEQUENCE structured_sync_change_seq CACHE 1',
                'GRANT UPDATE ON SEQUENCE structured_sync_change_seq TO mentorflow_app':
                    'REVOKE UPDATE ON SEQUENCE structured_sync_change_seq FROM mentorflow_app',
                'DROP INDEX ix_structured_sync_changes_scope':
                    'CREATE INDEX ix_structured_sync_changes_scope ON structured_sync_entities '
                    '(issuer,subject_id,organization_id,workspace_id,kind,change_seq)',
                'ALTER TABLE structured_sync_entities NO FORCE ROW LEVEL SECURITY':
                    'ALTER TABLE structured_sync_entities FORCE ROW LEVEL SECURITY',
            }
            admin.execute(reverse[change])
            admin.commit()
