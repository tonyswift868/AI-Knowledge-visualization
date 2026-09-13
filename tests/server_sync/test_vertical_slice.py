"""Disposable PostgreSQL + independent device transports, no real user data.

Run with MF_SYNC_TEST_ADMIN_URL and MF_SYNC_TEST_APP_URL pointing to an owned,
fully migrated dedicated test database. Auth fixtures supply sessions; the real canonical
enterprise_request_context validates every membership/scope. No real login claimed.
"""
import concurrent.futures
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'backend')]
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, select
from app.enterprise.structured_sync_api import router
from app.enterprise.identity.context import enterprise_request_context, IdentityContextError
from app.enterprise.identity.models import AuthenticatedPrincipal, EnterpriseSession, MembershipGrant
from app.enterprise_tenancy.context import PostgresTenantTransactionManager
from app.enterprise_tenancy.structured_sync import StructuredSyncService, entities, requests
from desktop.structured_sync import StructuredSyncClient

class Directory:
    def __init__(self, grants): self.grants = grants
    def resolve_membership(self, **scope):
        return self.grants.get(tuple(scope[k] for k in ('issuer','subject','organization_id','workspace_id')))

class SyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.getenv('MF_SYNC_TEST_ADMIN_URL'):
            raise unittest.SkipTest('Dedicated disposable PostgreSQL URLs required')
        cls.admin = create_engine(os.environ['MF_SYNC_TEST_ADMIN_URL'])
        cls.url = os.environ['MF_SYNC_TEST_APP_URL']
        with cls.admin.begin() as c:
            assert c.execute(text("SELECT to_regclass('rehydration_manifests')")).scalar() is not None, 'MIGRATED_OWNED_FIXTURE_REQUIRED'
            role=c.execute(text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname='mentorflow_app'")).one()
            assert tuple(role)==(False,False), 'UNSAFE_FIXTURE_APP_ROLE'

    @classmethod
    def tearDownClass(cls):
        cls.admin.dispose()

    def setUp(self):
        self.scope = uuid.uuid4().hex
        self.issuer = 'https://fixture.invalid/realms/mentorflow'
        self.grants = {}; self.sessions = {}
        for who,org,ws in [('A','orgA','wsA'),('B','orgB','wsB'),('A2','orgA','wsA2')]:
            subject = ('A' if who=='A2' else who)+self.scope
            org += self.scope; ws += self.scope
            now = datetime.now(timezone.utc); end = now+timedelta(hours=1)
            principal = AuthenticatedPrincipal(issuer=self.issuer,subject=subject,audiences=('fixture',),
                token_id='fixture-id',issued_at=now,expires_at=end,authentication_time=now,authentication_strength='fixture')
            self.sessions[who] = EnterpriseSession(session_id=uuid.uuid4().hex,principal=principal,
                created_at=now,refreshed_at=now,session_expires_at=end,access_token_expires_at=end,
                selected_organization_id=org,selected_workspace_id=ws)
            grant = MembershipGrant(membership_id='m'+uuid.uuid4().hex,issuer=self.issuer,subject=subject,
                organization_id=org,workspace_id=ws,status='ACTIVE',roles=('LEARNER',))
            self.grants[(self.issuer,subject,org,ws)] = grant
        self.directory = Directory(self.grants)
        self.engines=[]; self.clients=[]

    def tearDown(self):
        for client in self.clients:client.close()
        for engine in self.engines:engine.dispose()

    def server(self):
        engine=create_engine(self.url);self.engines.append(engine)
        service=StructuredSyncService(PostgresTenantTransactionManager(engine))
        app=FastAPI();app.state.structured_sync_service=service;app.include_router(router)
        @app.middleware('http')
        async def authenticate(request, next):
            who=request.headers.get('Authorization','').removeprefix('Bearer ')
            if who not in self.sessions:return JSONResponse({},status_code=401)
            session=self.sessions[who]
            if request.headers.get('X-Test-Workspace'):
                session=session.model_copy(update={'selected_workspace_id':request.headers['X-Test-Workspace']})
            try:context=enterprise_request_context(session=session,directory=self.directory,request_id=uuid.uuid4().hex)
            except IdentityContextError as e:return JSONResponse({'code':e.code},status_code=e.status)
            request.state.actor_context=context.actor;request.state.tenant_context=context.tenant
            return await next(request)
        return app,engine

    def device(self, who='A'):
        app,engine=self.server();transport=TestClient(app,headers={'Authorization':'Bearer '+who})
        self.clients.append(transport)
        return StructuredSyncClient(transport),transport,engine

    def seed(self, client):
        r=client.write('resource','resource-1',request_id='r1',expected_revision=0,
            payload={'filename':'fixture.pdf','media_type':'application/pdf','sha256':'a'*64,'size_bytes':12})
        p=client.write('plan','plan-1',request_id='p1',expected_revision=0,payload={'title':'Fixture plan','resource_ids':['resource-1']})
        g=client.write('progress','progress-1',request_id='g1',expected_revision=0,payload={'plan_id':'plan-1','mastery':0.5,'completed_tasks':2})
        return r,p,g

    def test_two_devices_server_restart_missing_file(self):
        a,transport,engine=self.device();expected=self.seed(a)
        transport.close();engine.dispose();del a,transport,engine
        b,transport,engine=self.device()
        state=b.rehydrate()
        self.assertEqual(state['plans'][0],expected[1]);self.assertEqual(state['progress'][0],expected[2])
        self.assertEqual(state['resources'][0]['entity_id'],'resource-1')
        self.assertEqual(state['resources'][0]['device_availability'],'MISSING_ON_THIS_DEVICE')
        self.assertEqual(len(state['plans']),1);self.assertEqual(len(state['progress']),1)

    def test_timeout_after_commit_retry_and_duplicate(self):
        a,t,e=self.device();self.seed(a)
        args=dict(request_id='p1',expected_revision=0,payload={'title':'Fixture plan','resource_ids':['resource-1']})
        # Discard original successful response: retry via a new server instance.
        b,_,_=self.device();r=b.write('plan','plan-1',**args)
        self.assertEqual(r['revision'],1);self.assertEqual(len(b.rehydrate()['plans']),1)
        r2=b.write('plan','plan-1',**args);self.assertEqual(r,r2)
        response=t.put(a.PREFIX+'/entities/plan/plan-2',json={'request_id':'p1','expected_revision':0,'payload':{'title':'changed'}})
        self.assertEqual(response.status_code,409)

    def test_revision_5_stale_4_and_tombstone(self):
        a,t,e=self.device();self.seed(a)
        for n in range(1,5):a.write('plan','plan-1',request_id='update'+str(n),expected_revision=n,payload={'title':'Revision '+str(n+1)})
        stale=t.put(a.PREFIX+'/entities/plan/plan-1',json={'request_id':'stale','expected_revision':4,'payload':{'title':'lost update'}})
        self.assertEqual(stale.status_code,409)
        deleted=a.delete('plan','plan-1',request_id='del',expected_revision=5)
        self.assertEqual(deleted['revision'],6)
        b,_,_=self.device();state=b.rehydrate();self.assertEqual(state['plans'],[])
        self.assertEqual(state['tombstones'][0],deleted)
        resurrection=t.put(a.PREFIX+'/entities/plan/plan-1',json={'request_id':'resurrect','expected_revision':6,'payload':{'title':'resurrect'}})
        self.assertEqual(resurrection.status_code,410)
        self.assertEqual(a.delete('plan','plan-1',request_id='del',expected_revision=5),deleted)

    def test_cross_account_workspace_and_reference_denial(self):
        a,t,e=self.device();self.seed(a)
        b,bt,_=self.device('B');self.assertEqual(b.rehydrate()['plans'],[])
        self.assertEqual(bt.get(a.PREFIX+'/snapshot',headers={'X-Test-Workspace':self.sessions['A'].selected_workspace_id}).status_code,403)
        a2,t2,_=self.device('A2');self.assertEqual(a2.rehydrate()['resources'],[])
        r=t2.put(a.PREFIX+'/entities/plan/cross',json={'request_id':'cross','expected_revision':0,'payload':{'title':'cross','resource_ids':['resource-1']}})
        self.assertEqual(r.status_code,422)
        self.assertEqual(TestClient(self.server()[0]).get(a.PREFIX+'/snapshot').status_code,401)
        forbidden=bt.put(a.PREFIX+'/entities/resource/intrusion',headers={'X-Test-Workspace':self.sessions['A'].selected_workspace_id},
            json={'request_id':'intrusion','expected_revision':0,'payload':{'filename':'x.pdf','media_type':'application/pdf','size_bytes':1,'sha256':'a'*64}})
        self.assertEqual(forbidden.status_code,403)
        bad=t.put(a.PREFIX+'/entities/resource/path',json={'request_id':'bad','expected_revision':0,'payload':{'filename':'C:/private.pdf','media_type':'application/pdf','size_bytes':1,'sha256':'a'*64}})
        self.assertEqual(bad.status_code,422)

    def test_rls_without_application_where_and_wrong_scope_insert(self):
        a,t,e=self.device();self.seed(a)
        context=enterprise_request_context(session=self.sessions['B'],directory=self.directory,request_id='rls')
        manager=PostgresTenantTransactionManager(e)
        from app.enterprise_tenancy.context import TenantTransactionContext
        with manager.transaction(TenantTransactionContext(tenant=context.tenant,actor=context.actor)) as tx:
            tx.connection.execute(text("SELECT set_config('mentorflow.actor_issuer',:i,true)"),{'i':self.issuer})
            self.assertEqual(tx.connection.execute(select(entities)).all(),[])
        from sqlalchemy.exc import DBAPIError
        with self.assertRaises(DBAPIError):
            with manager.transaction(TenantTransactionContext(tenant=context.tenant,actor=context.actor)) as tx:
                tx.connection.execute(text("SELECT set_config('mentorflow.actor_issuer',:i,true)"),{'i':self.issuer})
                tx.connection.execute(entities.insert().values(issuer=self.issuer,subject_id=context.actor.subject_id,
                    organization_id=self.sessions['A'].selected_organization_id,workspace_id=self.sessions['A'].selected_workspace_id,
                    kind='resource',entity_id='forbidden',revision=1,deleted=False,payload={}))

    def test_concurrent_duplicate_writes(self):
        a,t,e=self.device()
        def write(_):
            return a.write('plan','parallel',request_id='parallel',expected_revision=0,payload={'title':'same'})
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(write,range(4)))
        self.assertTrue(all(r==results[0] for r in results));self.assertEqual(len(a.rehydrate()['plans']),1)

    def test_lost_response_after_commit_and_concurrent_stale_writers(self):
        import httpx
        a,t,e=self.device()
        class LostResponse:
            def put(self,*args,**kwargs):
                response=t.put(*args,**kwargs)
                assert response.status_code==200
                raise httpx.ReadTimeout('Injected lost acknowledgement AFTER commit')
        with self.assertRaises(httpx.ReadTimeout):
            StructuredSyncClient(LostResponse()).write('plan','lost',request_id='lost',expected_revision=0,payload={'title':'persisted'})
        b,bt,_=self.device()
        result=b.write('plan','lost',request_id='lost',expected_revision=0,payload={'title':'persisted'})
        self.assertEqual(result['revision'],1);self.assertEqual(len(b.rehydrate()['plans']),1)
        def change(n):
            return bt.put(a.PREFIX+'/entities/plan/lost',json={'request_id':'race'+str(n),'expected_revision':1,'payload':{'title':'winner'+str(n)}}).status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(change,range(2)))
        self.assertEqual(sorted(statuses),[200,409])

    def test_commit_failure_no_receipt_no_entity(self):
        a,t,e=self.device();manager=PostgresTenantTransactionManager(e)
        class FailBeforeCommit:
            @contextmanager
            def transaction(self, context):
                with manager.transaction(context) as tx:
                    yield tx
                    raise RuntimeError('injected commit failure')
        svc=StructuredSyncService(FailBeforeCommit())
        ctx=enterprise_request_context(session=self.sessions['A'],directory=self.directory,request_id='failure')
        from app.enterprise_tenancy.structured_sync import Write
        with self.assertRaises(RuntimeError):svc.write(ctx.actor,ctx.tenant,'plan','failed',Write(request_id='failed',expected_revision=0,payload={'title':'fail'}))
        self.assertEqual(a.rehydrate()['plans'],[])
        a.write('plan','failed',request_id='failed',expected_revision=0,payload={'title':'fail'})
        self.assertEqual(len(a.rehydrate()['plans']),1)

    def test_snapshot_limit_and_missing_delete(self):
        a,t,e=self.device()
        missing=t.post(a.PREFIX+'/entities/plan/absent/delete',json={'request_id':'absent','expected_revision':1})
        self.assertEqual(missing.status_code,404)
        session=self.sessions['A']
        with self.admin.begin() as c:
            c.execute(entities.insert(),[dict(issuer=self.issuer,subject_id=session.principal.subject,
                organization_id=session.selected_organization_id,workspace_id=session.selected_workspace_id,
                kind='plan',entity_id='bounded-'+str(n),revision=1,deleted=False,payload={'title':'fixture'}) for n in range(1001)])
        response=t.get(a.PREFIX+'/snapshot')
        self.assertEqual(response.status_code,413)
        self.assertNotIn('entities',response.json())

if __name__=='__main__':unittest.main(verbosity=2)
