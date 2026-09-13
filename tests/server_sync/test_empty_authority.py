"""True empty is a committed bootstrap fact plus a single scoped observation."""
import concurrent.futures
import threading
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine, text, event
from sqlalchemy.exc import DBAPIError
from test_vertical_slice import SyncTests
from app.enterprise.identity.directory import PostgresIdentityDirectory
from app.enterprise.identity.context import enterprise_request_context
from app.enterprise_tenancy.context import PostgresTenantTransactionManager
from app.enterprise_tenancy.structured_sync import StructuredSyncService
from app.enterprise_tenancy.workspace_bootstrap import AccountWorkspaceBootstrap
from app.enterprise_tenancy.rehydration_manifest import SCOPE_VERSION


class EmptyAuthorityTests(unittest.TestCase):
    setUpClass = classmethod(SyncTests.setUpClass.__func__)
    tearDownClass = classmethod(SyncTests.tearDownClass.__func__)
    tearDown = SyncTests.tearDown
    server = SyncTests.server
    device = SyncTests.device
    seed = SyncTests.seed

    def setUp(self):
        SyncTests.setUp(self)
        self.bootstrap_engine = create_engine(self.url)
        self.engines.append(self.bootstrap_engine)
        self.bootstrap = AccountWorkspaceBootstrap(self.bootstrap_engine, issuer=self.issuer)
        for who in ('A','B'):
            session=self.sessions[who]
            ws=self.bootstrap.initialize(session.principal)['workspaces'][0]
            self.sessions[who]=session.model_copy(update=dict(selected_organization_id=ws['organization_id'],selected_workspace_id=ws['id']))
        self.directory=PostgresIdentityDirectory(self.bootstrap_engine,issuer=self.issuer)

    def test_normal_bootstrap_empty_nonempty_and_idempotent_generation(self):
        c,_,_=self.device()
        empty=c.rehydrate()
        state=empty['learning_state']
        self.assertEqual(state['status'],'TRUE_EMPTY_SUPPORTED_SCOPE')
        self.assertTrue(state['initialized']);self.assertTrue(state['known_empty'])
        self.assertEqual(state['global_account_empty'],'UNKNOWN')
        self.assertEqual(state['scope_version'],SCOPE_VERSION)
        self.assertEqual(state['state_generation'],1)
        self.bootstrap.initialize(self.sessions['A'].principal)
        self.assertEqual(c.rehydrate()['learning_state']['state_generation'],1)
        self.seed(c)
        observed=c.rehydrate()
        self.assertEqual(observed['learning_state']['status'],'NONEMPTY')
        self.assertEqual(observed['learning_state']['state_generation'],4)
        self.seed(c)  # Exact idempotency replay cannot advance the manifest.
        self.assertEqual(c.rehydrate()['learning_state']['state_generation'],4)

    def test_existing_workspace_without_learning_manifest_not_initialized(self):
        c,_,_=self.device()
        scope=c.rehydrate()['snapshot_scope']
        with self.admin.begin() as db:
            db.execute(text('DELETE FROM rehydration_manifests WHERE subject_id=:subject_id'),scope)
        state=c.rehydrate()['learning_state']
        self.assertEqual(state['status'],'NOT_INITIALIZED')
        self.assertFalse(state['known_empty']);self.assertIsNone(state['supported_scope_empty'])
        discovered=self.bootstrap.discover(self.sessions['A'].principal)
        self.assertTrue(discovered['initialization_required'])
        self.assertEqual(discovered['workspaces'][0]['initialization_status'],'READY')
        self.bootstrap.initialize(self.sessions['A'].principal)
        self.assertEqual(c.rehydrate()['learning_state']['status'],'TRUE_EMPTY_SUPPORTED_SCOPE')

    def test_unsupported_scope_and_version_are_not_empty(self):
        c,t,_=self.device()
        for params in ({'required_scope':'plans,review'}, {'scope_version':'SUPPORTED_REHYDRATION_SCOPE_V2'}):
            r=t.get(c.PREFIX+'/snapshot',params=params)
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.json()['learning_state']['status'],'PARTIAL_UNSUPPORTED')
            self.assertFalse(r.json()['learning_state']['known_empty'])
            self.assertIsNone(r.json()['observation']['unsupported_counts']['messages'])
        with self.admin.begin() as db:
            db.execute(text("UPDATE rehydration_manifests SET scope_version='FUTURE_SCOPE' WHERE subject_id=:subject"),
                       {'subject':self.sessions['A'].principal.subject})
        self.assertEqual(c.rehydrate()['learning_state']['status'],'PARTIAL_UNSUPPORTED')

    def test_delete_all_is_empty_with_visible_tombstones_and_bound_generation(self):
        c,_,_=self.device();self.seed(c)
        before=c.rehydrate()
        for kind,id in (('progress','progress-1'),('plan','plan-1'),('resource','resource-1')):
            c.delete(kind,id,request_id='delete-'+id,expected_revision=1)
        b,_,_=self.device();after=b.rehydrate()
        self.assertEqual(after['learning_state']['status'],'TRUE_EMPTY_SUPPORTED_SCOPE')
        self.assertEqual(len(after['tombstones']),3)
        self.assertEqual(after['learning_state']['state_generation'],7)
        self.assertNotEqual(before['snapshot_revision'],after['snapshot_revision'])
        self.assertEqual(after['observation']['snapshot_revision'],after['learning_state']['snapshot_revision'])
        self.assertEqual(after['observation']['empty_state'],'TRUE_EMPTY_SUPPORTED_SCOPE')

    def test_pending_insert_during_observation_then_committed_next_snapshot(self):
        c,_,writer_engine=self.device();b,_,_=self.device()
        inserted=threading.Event();release=threading.Event();xid=[]
        def hold(connection,cursor,statement,parameters,context,many):
            if statement.startswith('INSERT INTO structured_sync_entities'):
                xid.append(str(connection.execute(text('SELECT pg_current_xact_id()')).scalar_one()))
                inserted.set()
                if not release.wait(10):raise AssertionError('CONCURRENT_TEST_RELEASE_TIMEOUT')
        event.listen(writer_engine,'after_cursor_execute',hold)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(c.write,'plan','concurrent',request_id='concurrent',expected_revision=0,payload={'title':'Concurrent'})
                try:
                    self.assertTrue(inserted.wait(10))
                    old=b.rehydrate()
                    self.assertEqual(old['learning_state']['status'],'TRUE_EMPTY_SUPPORTED_SCOPE')
                    self.assertEqual(old['learning_state']['state_generation'],1)
                finally:release.set()
                future.result(timeout=10)
            new=b.rehydrate()
            self.assertEqual(new['learning_state']['status'],'NONEMPTY')
            self.assertEqual(new['learning_state']['state_generation'],2)
            self.assertNotEqual(old['snapshot_revision'],new['snapshot_revision'])
            with self.admin.connect() as db:
                for observed,visible in ((old,False),(new,True)):
                    self.assertEqual(db.execute(text('SELECT pg_visible_in_snapshot(CAST(:x AS xid8),CAST(:s AS pg_snapshot))'),
                        dict(x=xid[0],s=observed['snapshot_revision'].removeprefix('pg-snapshot:'))).scalar_one(),visible)
        finally:event.remove(writer_engine,'after_cursor_execute',hold);release.set()

    def test_manifest_cross_account_workspace_rls_denial(self):
        c,t,e=self.device();scope=c.rehydrate()['snapshot_scope']
        b,_,_=self.device('B');other=b.rehydrate()['snapshot_scope']
        r=t.get(c.PREFIX+'/snapshot',headers={'X-Test-Workspace':other['workspace_id']})
        self.assertEqual(r.status_code,403)
        self.assertEqual(t.get(c.PREFIX+'/snapshot',headers={'Authorization':'invalid'}).status_code,401)
        ctx=enterprise_request_context(session=self.sessions['A'],directory=self.directory,request_id='rls')
        from app.enterprise_tenancy.context import TenantTransactionContext
        manager=PostgresTenantTransactionManager(e)
        with manager.transaction(TenantTransactionContext(ctx.tenant,ctx.actor)) as tx:
            StructuredSyncService._bind(tx,scope)
            self.assertEqual(tx.connection.execute(text('SELECT subject_id FROM rehydration_manifests')).scalars().all(),[scope['subject_id']])
        with self.assertRaises(DBAPIError) as denied:
            with manager.transaction(TenantTransactionContext(ctx.tenant,ctx.actor)) as tx:
                StructuredSyncService._bind(tx,scope)
                tx.connection.execute(text('''INSERT INTO rehydration_manifests
                    (issuer,subject_id,organization_id,workspace_id,scope_version,initialized,generation)
                    VALUES (:issuer,:subject_id,:organization_id,:workspace_id,:version,true,1)'''),
                    dict(other,subject_id=scope['subject_id'],version=SCOPE_VERSION))
        self.assertEqual(denied.exception.orig.sqlstate,'42501')

    def test_bootstrap_failure_rolls_back_initialization_fact(self):
        from app.enterprise_tenancy import workspace_bootstrap as module
        original=module.initialize_learning_state
        fresh=self.sessions['A'].principal.model_copy(update={'subject':'fresh-failure-'+self.scope})
        def fail(*args):original(*args);raise RuntimeError('INJECTED_BOOTSTRAP_FAILURE')
        with patch.object(module,'initialize_learning_state',fail):
            with self.assertRaises(RuntimeError):self.bootstrap.initialize(fresh)
        self.assertEqual(self.bootstrap.discover(fresh)['workspaces'],[])
        with self.admin.connect() as db:
            self.assertEqual(db.execute(text('SELECT count(*) FROM rehydration_manifests WHERE subject_id=:s'),{'s':fresh.subject}).scalar_one(),0)

    def test_empty_plans_but_progress_query_failure_is_never_empty(self):
        from app.enterprise_tenancy.schema import rls_predicate_for
        c,t,_=self.device();self.seed(c)
        c.delete('plan','plan-1',request_id='delete-plan',expected_revision=1)
        self.assertEqual(c.rehydrate()['plans'],[])
        original=rls_predicate_for('structured_sync_entities')
        # Disposable DB only: force an actual PG evaluation failure on progress.
        broken=original+" AND CASE WHEN kind='progress' THEN 1 / (length(entity_id)-length(entity_id)) > 0 ELSE true END"
        try:
            with self.admin.begin() as db:
                db.execute(text('ALTER POLICY tenant_isolation_structured_sync_entities ON structured_sync_entities USING ('+broken+')'))
            response=t.get(c.PREFIX+'/snapshot')
            self.assertEqual(response.status_code,503)
            self.assertEqual(response.json(),{'code':'SYNC_QUERY_FAILED','sync_status':'QUERY_FAILED'})
        finally:
            with self.admin.begin() as db:
                db.execute(text('ALTER POLICY tenant_isolation_structured_sync_entities ON structured_sync_entities USING ('+original+')'))

    def test_initialization_and_write_share_commit_order(self):
        c,_,writer_engine=self.device();b,_,_=self.device()
        subject=self.sessions['A'].principal.subject
        with self.admin.begin() as db:
            db.execute(text('DELETE FROM rehydration_manifests WHERE subject_id=:s'),{'s':subject})
        inserted=threading.Event();release=threading.Event();initializing=threading.Event()
        def hold(connection,cursor,statement,parameters,context,many):
            if statement.startswith('INSERT INTO structured_sync_entities'):
                inserted.set()
                if not release.wait(10):raise AssertionError('WRITE_RELEASE_TIMEOUT')
        def initialize():
            initializing.set()
            return self.bootstrap.initialize(self.sessions['A'].principal)
        event.listen(writer_engine,'after_cursor_execute',hold)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                write=pool.submit(c.write,'plan','initializing',request_id='initializing',expected_revision=0,payload={'title':'First'})
                try:
                    self.assertTrue(inserted.wait(10))
                    init=pool.submit(initialize)
                    self.assertTrue(initializing.wait(10))
                    self.assertEqual(b.rehydrate()['learning_state']['status'],'NOT_INITIALIZED')
                finally:release.set()
                write.result(timeout=10);init.result(timeout=10)
            state=b.rehydrate()
            self.assertEqual(state['learning_state']['status'],'NONEMPTY')
            self.assertEqual(state['learning_state']['state_generation'],1)
            b.write('plan','initializing',request_id='next',expected_revision=1,payload={'title':'Next'})
            self.assertEqual(b.rehydrate()['learning_state']['state_generation'],2)
        finally:event.remove(writer_engine,'after_cursor_execute',hold);release.set()
