"""Owned native PostgreSQL + real Account Core HTTP candidate acceptance.

Run from an exact extracted Git archive. No external DSN, resident server,
real user, Keycloak mutation, password login bypass or public deployment.
OIDC smoke uses an ephemeral RSA signing key and a loopback JWKS test issuer;
the unmodified candidate validates JWTs and real DB workspace membership.
"""
import argparse
import base64
from contextlib import redirect_stdout, redirect_stderr
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'backend'), str(ROOT/'scripts'), str(ROOT/'tests/server_sync')]
from schema_0036_native import NativePostgres, HIDDEN


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--source-manifest', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.source_manifest.read_text())
    archive = Path(manifest['artifact'])
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest['artifact_sha256']
    # Verify every archived file before importing the candidate application.
    import zipfile
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            if not name.endswith('/'):
                assert (ROOT/name).read_bytes() == z.read(name), 'SOURCE_ARTIFACT_MISMATCH'
    args.scratch.mkdir(parents=True, exist_ok=True)
    lab = NativePostgres(args.pg_bin, args.scratch)
    report = dict(source=manifest, db_identity=lab.identity, db_endpoint=f'127.0.0.1:{lab.port}',
                  checks={}, real_user_data_migration=False, cleaned_up=False)
    process = issuer_server = engine = None
    code = 1
    try:
        lab.start()
        report['postgres_version'] = lab.version
        os.environ.update(MENTORFLOW_DEPLOYMENT_MODE='PERSONAL_LOCAL',
            MENTORFLOW_USER_DATA_ROOT=str(lab.home/'local'), MENTORFLOW_DATA_DIR=str(lab.home/'local'),
            MENTORFLOW_DB=str(lab.home/'app.db'), MENTORFLOW_ORM_DB=str(lab.home/'orm.db'),
            PYTHONDONTWRITEBYTECODE='1')
        from sqlalchemy import create_engine, text
        from psycopg import sql
        from alembic import command
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from app import orm
        from app.enterprise_tenancy.sync_schema import require_structured_sync_schema
        from app.enterprise_tenancy.context import create_enterprise_postgres_engine
        config = Config(str(ROOT/'backend/alembic.ini'))
        config.set_main_option('script_location', str(ROOT/'backend/alembic'))
        config.attributes['configure_logger'] = False
        from app.enterprise_tenancy.schema import ENTERPRISE_MIGRATION_REVISION
        head=ENTERPRISE_MIGRATION_REVISION
        assert ScriptDirectory.from_config(config).get_heads() == [head]
        os.environ['MENTORFLOW_DEPLOYMENT_MODE'] = 'ENTERPRISE_SERVER'
        def database(name):
            with lab.admin() as c:
                c.execute(sql.SQL('CREATE DATABASE {} OWNER mentorflow_migrator').format(sql.Identifier(name)))
            os.environ['MENTORFLOW_DATABASE_URL'] = lab.url('mentorflow_app', name).update_query_dict(
                {'connect_timeout': '3'}).render_as_string(hide_password=False)
            os.environ['MENTORFLOW_MIGRATION_DATABASE_URL'] = lab.url('mentorflow_migrator', name).render_as_string(hide_password=False)
        for name, start in [('fresh', None), ('from0036', '0036_account_import_ledger'), ('from0037', '0037_structured_sync')]:
            database(name)
            if start: command.upgrade(config, start)
            command.upgrade(config, 'head')
            engine = create_enterprise_postgres_engine(os.environ['MENTORFLOW_DATABASE_URL'])
            orm.engine = engine
            orm.verify_enterprise_runtime_schema()
            report['checks']['FRESH_TO_HEAD' if start is None else start[:4]+'_TO_HEAD'] = 'PASS'
            engine.dispose()
        # Startup/upgrade at head must work even with both roles forced read-only.
        with lab.admin() as c:
            for role in ('mentorflow_app', 'mentorflow_migrator'):
                c.execute(sql.SQL('ALTER ROLE {} IN DATABASE from0037 SET default_transaction_read_only=on').format(sql.Identifier(role)))
        command.upgrade(config, 'head')
        orm.verify_enterprise_runtime_schema()
        report['checks']['ALREADY_CURRENT'] = 'PASS_READ_ONLY'
        engine.dispose()
        with lab.admin() as c:
            for role in ('mentorflow_app', 'mentorflow_migrator'):
                c.execute(sql.SQL('ALTER ROLE {} IN DATABASE from0037 RESET default_transaction_read_only').format(sql.Identifier(role)))
        admin = create_engine(lab.url('postgres', 'from0037'), hide_parameters=True)
        with admin.begin() as c: c.execute(text("UPDATE alembic_version SET version_num='9999_unknown'"))
        try: orm.verify_enterprise_runtime_schema()
        except orm.DatabaseRuntimeConfigurationError: report['checks']['FUTURE_REJECTED'] = 'PASS'
        else: raise AssertionError('FUTURE_NOT_REJECTED')
        with admin.begin() as c: c.execute(text('UPDATE alembic_version SET version_num=:head'),{'head':head})
        drift = [
            'ALTER TABLE structured_sync_entities DROP COLUMN revision',
            'ALTER TABLE structured_sync_requests DROP COLUMN response',
            'ALTER TABLE structured_sync_entities DROP CONSTRAINT ck_sync_revision',
            'ALTER TABLE structured_sync_entities NO FORCE ROW LEVEL SECURITY',
            'ALTER POLICY tenant_isolation_structured_sync_entities ON structured_sync_entities USING (true)',
            'GRANT DELETE ON structured_sync_requests TO mentorflow_app',
            'ALTER TABLE rehydration_manifests DROP COLUMN initialized',
            'ALTER TABLE rehydration_manifests DROP COLUMN scope_version',
            'ALTER TABLE rehydration_manifests DROP CONSTRAINT ck_rehydration_generation',
            'ALTER TABLE rehydration_manifests NO FORCE ROW LEVEL SECURITY',
            'ALTER POLICY tenant_isolation_rehydration_manifests ON rehydration_manifests WITH CHECK (true)',
            'GRANT DELETE ON rehydration_manifests TO mentorflow_app',
        ]
        for statement in drift:
            with admin.connect() as c:
                tx = c.begin()
                c.execute(text(statement))
                c.execute(text('SET LOCAL ROLE mentorflow_app'))
                try: require_structured_sync_schema(c)
                except ValueError:
                    pass
                else: raise AssertionError('DRIFT_NOT_REJECTED')
                finally: tx.rollback()
        admin.dispose()
        report['checks']['DRIFT_REJECTED'] = f'PASS_{len(drift)}_CASES'
        # Reuse all real PG protocol tests against the full migrated schema.
        import test_vertical_slice
        import test_producer_contract
        import test_empty_authority
        def setup(cls):
            cls.admin = create_engine(lab.url('postgres', 'from0037'), hide_parameters=True)
            cls.url = os.environ['MENTORFLOW_DATABASE_URL']
        test_vertical_slice.SyncTests.setUpClass = classmethod(setup)
        test_producer_contract.ProducerTests.setUpClass = classmethod(setup)
        test_empty_authority.EmptyAuthorityTests.setUpClass = classmethod(setup)
        suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(t) for t in
            (test_vertical_slice.SyncTests, test_producer_contract.ProducerTests, test_empty_authority.EmptyAuthorityTests)])
        with (lab.home/'private-suite.log').open('w') as log:
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        report['protocol_tests'] = dict(run=result.testsRun, failures=len(result.failures), errors=len(result.errors), skips=len(result.skipped))
        assert not result.skipped, 'POSTGRES_INTEGRATION_MUST_NOT_SKIP'
        if not result.wasSuccessful():
            report['test_failures'] = [(str(t), err.replace(lab.password, '[REDACTED]')) for t,err in result.failures+result.errors]
            raise AssertionError('PROTOCOL_TESTS_FAILED')
        # Isolated loopback JWKS issuer. Private key and bearer values stay in memory.
        from cryptography.hazmat.primitives.asymmetric import rsa, padding
        from cryptography.hazmat.primitives import hashes
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        def b64(value): return base64.urlsafe_b64encode(value).rstrip(b'=').decode()
        numbers = key.public_key().public_numbers()
        jwk = dict(kty='RSA', kid='owned-fixture', alg='RS256', use='sig',
            n=b64(numbers.n.to_bytes((numbers.n.bit_length()+7)//8,'big')),
            e=b64(numbers.e.to_bytes((numbers.e.bit_length()+7)//8,'big')))
        class Issuer(BaseHTTPRequestHandler):
            def do_GET(self):
                if not self.path.endswith('/protocol/openid-connect/certs'):
                    self.send_error(404); return
                body=json.dumps({'keys':[jwk]}).encode()
                self.send_response(200); self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *args): pass
        issuer_server=ThreadingHTTPServer(('127.0.0.1',0),Issuer)
        threading.Thread(target=issuer_server.serve_forever,daemon=True).start()
        issuer=f'http://127.0.0.1:{issuer_server.server_port}/realms/disposable'
        def bearer(subject):
            now=int(time.time())
            claims=dict(iss=issuer,sub=subject,aud='mentorflow-api',azp='sync-candidate',
                        jti=subject+'-disposable',iat=now,exp=now+600)
            message=b64(json.dumps({'alg':'RS256','kid':'owned-fixture','typ':'JWT'}).encode())+'.'+b64(json.dumps(claims).encode())
            return message+'.'+b64(key.sign(message.encode(),padding.PKCS1v15(),hashes.SHA256()))
        with socket.socket() as s: s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
        endpoint=f'http://127.0.0.1:{port}'
        env={k:v for k,v in os.environ.items() if not k.startswith(('MENTORFLOW_', 'MF_SYNC_'))}
        env.update(PYTHONPATH=str(ROOT/'backend'), PYTHONDONTWRITEBYTECODE='1',
            MENTORFLOW_DEPLOYMENT_MODE='ENTERPRISE_SERVER', MENTORFLOW_DATABASE_URL=os.environ['MENTORFLOW_DATABASE_URL'],
            MENTORFLOW_KEYCLOAK_ISSUER=issuer, MENTORFLOW_KEYCLOAK_CLIENT_ID='sync-candidate',
            MENTORFLOW_KEYCLOAK_AUDIENCE='mentorflow-api', MENTORFLOW_KEYCLOAK_REDIRECT_URI=endpoint+'/callback',
            MENTORFLOW_KEYCLOAK_ALLOW_LOOPBACK_HTTP='1',
            MENTORFLOW_USER_DATA_ROOT=str(lab.home/'runtime'), MENTORFLOW_DATA_DIR=str(lab.home/'runtime'))
        # The owned loopback issuer must not travel through a workstation proxy.
        env['NO_PROXY'] = env['no_proxy'] = '127.0.0.1,localhost'
        with (lab.home/'private-backend.log').open('wb') as log:
            process=subprocess.Popen([sys.executable,'-B','-m','uvicorn','app.account_core_mvp:app',
                '--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=ROOT,env=env,
                stdout=log,stderr=log,creationflags=HIDDEN)
        import httpx
        from desktop.structured_sync import StructuredSyncClient
        report['phase']='BACKEND_STARTUP'
        with httpx.Client(base_url=endpoint,trust_env=False,timeout=15) as a:
            for _ in range(50):
                if process.poll() is not None: raise AssertionError('BACKEND_EXITED')
                try:
                    if a.get('/health/startup').status_code==200: break
                except httpx.TransportError: pass
                time.sleep(.2)
            else: raise AssertionError('BACKEND_STARTUP_TIMEOUT')
            report['phase']='UNAUTHENTICATED_SNAPSHOT'
            r=a.get('/api/enterprise/sync/v1/snapshot')
            assert r.status_code==401 and r.json()['sync_status']=='NOT_AUTHORIZED'
            a.headers['Authorization']='Bearer '+bearer('disposable-account')
            report['phase']='AUTHENTICATED_WORKSPACE_BOOTSTRAP'
            r=a.post('/api/enterprise/account/workspaces/initialize',json={});r.raise_for_status()
            ws=r.json()['workspaces'][0]
            a.headers.update({'X-MentorFlow-Organization-ID':ws['organization_id'],'X-MentorFlow-Workspace-ID':ws['id']})
            c=StructuredSyncClient(a)
            report['phase']='EMPTY_SNAPSHOT'
            empty=c.rehydrate()
            assert empty['learning_state']['status']=='TRUE_EMPTY_SUPPORTED_SCOPE'
            report['empty_observations']={'new_account':empty['observation']}
            partial=a.get(c.PREFIX+'/snapshot',params={'required_scope':'plans,review'}).json()
            assert partial['learning_state']['status']=='PARTIAL_UNSUPPORTED'
            report['empty_observations']['partial_unsupported']=partial['observation']
            report['phase']='RESOURCE_WRITE'
            c.write('resource','shared-resource',request_id='r',expected_revision=0,payload=dict(
                filename='fixture.bin',media_type='application/octet-stream',sha256='a'*64,size_bytes=10,product_classification='MODEL'))
            report['phase']='PLAN_PROGRESS_WRITE'
            for i in (1,2): c.write('plan',f'plan-{i}',request_id=f'p{i}',expected_revision=0,
                payload=dict(title=f'Disposable plan {i}',resource_ids=['shared-resource']))
            c.write('progress','progress-1',request_id='g',expected_revision=0,
                payload=dict(plan_id='plan-1',mastery=.5,completed_tasks=2))
            headers=dict(a.headers)
        # Device A transport is closed before a second independent socket client.
        with httpx.Client(base_url=endpoint,headers=headers,trust_env=False,timeout=15) as b:
            report['phase']='INDEPENDENT_CLIENT_REHYDRATE'
            state=StructuredSyncClient(b).rehydrate()
            assert state['resources'][0]['plan_ids']==['plan-1','plan-2']
            assert state['resources'][0]['product_classification']=='MODEL'
            assert state['resources'][0]['device_availability']=='MISSING_ON_THIS_DEVICE'
            assert state['observation']['counts']['plans']==2 and len(state['progress'])==1
            assert state['progress_provenance'][0]['snapshot_revision']==state['snapshot_revision']
            from app.enterprise_tenancy.sync_snapshot import SUPPORTED
            assert state['observation']['supported_entity_types']==list(SUPPORTED)
            (args.scratch/'observation.json').write_text(json.dumps(state['observation'],indent=2))
            report['snapshot_result']=state
            report['empty_observations']['nonempty']=state['observation']
            for kind,id in [('progress','progress-1'),('plan','plan-1'),('plan','plan-2'),('resource','shared-resource')]:
                StructuredSyncClient(b).delete(kind,id,request_id='delete-'+id,expected_revision=1)
            empty_after=StructuredSyncClient(b).rehydrate()
            assert empty_after['learning_state']['status']=='TRUE_EMPTY_SUPPORTED_SCOPE'
            assert len(empty_after['tombstones'])==4
            report['empty_observations']['delete_to_empty']=empty_after['observation']
            (args.scratch/'empty-observations.json').write_text(json.dumps(report['empty_observations'],indent=2))
        from sync_device_continuity import prove_continuity
        report['device_continuity'] = prove_continuity(ROOT, endpoint, bearer, args.scratch)
        report.update(endpoint=endpoint,backend_runtime_pass=True,independent_client_rehydrate=True,
            auth_evidence='REAL_RS256_VALIDATION_AND_DB_BOOTSTRAP; EPHEMERAL_LOOPBACK_JWKS_ISSUER; NO_NATIVE_LOGIN_CLAIM')
        # A real database outage must not produce an empty or partially successful snapshot.
        lab.command('pg_ctl', '-D', lab.data, '-m', 'fast', '-w', 'stop')
        lab.started = False
        report['phase']='DATABASE_OUTAGE'
        with httpx.Client(base_url=endpoint,headers=headers,trust_env=False,timeout=15) as b:
            failed=b.get('/api/enterprise/sync/v1/snapshot')
            assert failed.status_code==503 and failed.json()==dict(code='SYNC_QUERY_FAILED',sync_status='QUERY_FAILED')
        report['checks']['QUERY_FAILED'] = 'PASS_REAL_DATABASE_OUTAGE'
        report['phase']='COMPLETE'
        code=0
    except Exception as exc:
        report['failure_type']=type(exc).__name__
        report['failure_reason']=str(exc).replace(lab.password,'[REDACTED]')[:500]
        # Retain only sanitized diagnostic text, never raw runtime logs.
        log=lab.home/'private-backend.log'
        if log.exists(): report['backend_log']=log.read_text(errors='replace').replace(lab.password,'[REDACTED]')[-5000:]
    finally:
        if process and process.poll() is None:
            process.terminate();process.wait(timeout=15)
        if issuer_server:
            issuer_server.shutdown();issuer_server.server_close()
        if engine:engine.dispose()
        try:lab.close();report['cleaned_up']=True
        except Exception as exc:report['cleanup_failure']=type(exc).__name__;code=1
        (args.scratch/'candidate-receipt.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(dict(exit_code=code,checks=report['checks'],protocol_tests=report.get('protocol_tests'),
        cleaned_up=report['cleaned_up'],receipt=str(args.scratch/'candidate-receipt.json'))))
    return code


if __name__=='__main__':raise SystemExit(main())
