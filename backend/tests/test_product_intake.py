"""HTTP -> app-role PostgreSQL -> independent empty product store."""
import json
import os
import sqlite3
from pathlib import Path
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from app import orm
from app.enterprise.structured_sync_api import router
from app.product_sync_contract import RECORDS,project,record_id
from app.product_sync import ProductSyncProducer, ProductSyncFailure
from app.product_intake import prepare,execute
from test_product_sync import scope
from test_profile_settings_postgres import server
from test_account_import_ledger_schema import native


def test_confirmed_group_preserves_exact_records_and_dependencies():
    source=os.environ.get('MF04_INTAKE_SNAPSHOT')
    if not source:pytest.skip('explicit retained read-only snapshot not selected')
    from app.product_intake import sha256_file
    args=dict(expected_sha256='bfb6805df8913f5891a8d5b01f96683e0d432684870f1e6b79e9c5ef4a0caf43',owner_sha256='1fafc6b6212ca2ba594bcf72aea9764092688ab44b1aa8f75813265dd2c1aee5')
    before=prepare(source,**args)
    after=prepare(source,**args,confirmed_workspace_ids=['internet-interview-20260815'])
    old={(r['record_type'],r['row_key']):r for r in before['records']}
    new={(r['record_type'],r['row_key']):r for r in after['records']}
    assert all(new[k]==v for k,v in old.items())
    added=[v for k,v in new.items() if k not in old]
    assert len(added)==1173
    assert len(after['records'])==29207
    assert after['held']=={'orm_learning_runs':6}
    assert sum(r['record_type']=='orm_plans' for r in added)==5
    assert sum(r['record_type']=='active_plans' for r in added)==2
    assert sum(r['record_type']=='orm_plan_execution_tasks' for r in added)==797
    with sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as c:
        c.row_factory=sqlite3.Row
        admitted={}
        for r in after['records']:
            t=r['record_type'];spec=RECORDS[t]
            row=dict(c.execute('SELECT * FROM "'+t+'" WHERE '+' AND '.join('"'+k+'"=?' for k in spec['keys']),json.loads(r['row_key'])).fetchone())
            assert project(t,row)==r['payload'] and record_id(t,row)==r['entity_id']
            if 'workspace_id' in row and r in added:assert row['workspace_id']=='internet-interview-20260815'
            admitted.setdefault(t,[]).append(row)
        for t,rows in admitted.items():
            for fk in c.execute('PRAGMA foreign_key_list("'+t+'")'):
                parent_values={row[fk[4]] for row in admitted.get(fk[2],[])}
                assert all(row[fk[3]] is None or row[fk[3]] in parent_values for row in rows)
    assert sha256_file(source)==args['expected_sha256']


def test_confirmed_workspace_cannot_override_other_owner_or_invent_root(tmp_path):
    import hashlib
    from app.product_intake import sha256_file
    path=tmp_path/'ownership.sqlite'
    with sqlite3.connect(path) as c:
        c.executescript("CREATE TABLE orm_workspaces(id TEXT); CREATE TABLE orm_workspace_ownership(workspace_id TEXT,user_id TEXT); INSERT INTO orm_workspaces VALUES('mine'),('theirs'); INSERT INTO orm_workspace_ownership VALUES('mine','a'),('theirs','b');")
    args=dict(expected_sha256=sha256_file(path),owner_sha256=hashlib.sha256(b'a').hexdigest())
    with pytest.raises(ValueError,match='CONFIRMED_WORKSPACE_OTHER_OWNER'):prepare(path,**args,confirmed_workspace_ids=['theirs'])
    with pytest.raises(ValueError,match='CONFIRMED_WORKSPACE_NOT_FOUND'):prepare(path,**args,confirmed_workspace_ids=['absent'])
    with pytest.raises(ValueError,match='CONFIRMED_WORKSPACE_EXCLUDED'):prepare(path,**args,confirmed_workspace_ids=['mine'],excluded_workspace_ids=['mine'])
    with pytest.raises(ValueError,match='EXCLUDED_WORKSPACE_NOT_FOUND'):prepare(path,**args,excluded_workspace_ids=['absent'])


def test_confirmed_clean_selection_excludes_fixtures_without_losing_personal_state():
    source=os.environ.get('MF04_INTAKE_SNAPSHOT')
    if not source:pytest.skip('explicit retained read-only snapshot not selected')
    manifest=json.loads((Path(__file__).resolve().parents[2]/'docs/release/MF-R1-04-DATA-REPAIR/MIGRATION_INPUTS.json').read_text(encoding='utf-8'))
    selection=manifest['intake_selection']
    excluded=[r['workspace_id'] for r in selection['test_demo_exclusions']+selection['pending_classification']]
    args=dict(expected_sha256=selection['source_sha256'],owner_sha256=manifest['owner_sha256'],confirmed_workspace_ids=selection['confirmed_workspace_ids'])
    all_owned=prepare(source,**args)
    clean=prepare(source,**args,excluded_workspace_ids=excluded)
    assert len(clean['records'])==2941
    clean_index={(r['record_type'],r['row_key']):r for r in clean['records']}
    original_index={(r['record_type'],r['row_key']):r for r in all_owned['records']}
    assert all(r==original_index[k] for k,r in clean_index.items())
    prior=prepare(source,expected_sha256=args['expected_sha256'],owner_sha256=args['owner_sha256'])
    prior_keys={(r['record_type'],r['row_key']) for r in prior['records']}
    confirmed_keys=set(original_index)-prior_keys
    assert len(confirmed_keys)==1173 and confirmed_keys<=set(clean_index)
    assert clean['admitted']['orm_plan_execution_tasks']==797
    assert clean['admitted']['orm_plans']==5
    assert clean['admitted']['active_plan_tasks']==648
    assert clean['admitted']['active_knowledge_progress_snapshots']==150
    assert clean['admitted']['active_knowledge_points']==150
    assert clean['admitted']['active_learner_profiles']==0  # mixed demo provenance stays in backup
    assert clean['admitted']['orm_sources']==0  # includes seven exact metadata-only fixtures
    with sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as c:
        c.row_factory=sqlite3.Row
        for r in all_owned['records']:
            t=r['record_type'];spec=RECORDS[t]
            row=dict(c.execute('SELECT * FROM "'+t+'" WHERE '+' AND '.join('"'+k+'"=?' for k in spec['keys']),json.loads(r['row_key'])).fetchone())
            if row.get('workspace_id')=='internet-interview-20260815' or (t=='orm_workspaces' and row['id']=='internet-interview-20260815'):
                assert clean_index[(t,r['row_key'])]==r
            if row.get('workspace_id') in excluded:
                assert (t,r['row_key']) not in clean_index


def http_client(service, actor, tenant):
    api=FastAPI();api.include_router(router);api.state.structured_sync_service=service
    @api.middleware('http')
    async def fixture_scope(request: Request, call_next):
        request.state.actor_context=actor;request.state.tenant_context=tenant
        return await call_next(request)
    return TestClient(api)


class Relay:
    def __init__(self,client,actor,tenant):self.client=client;self.scope=scope(actor,tenant)
    def call(self,m):
        if m['operation']=='authorize':return {'authorized':True}
        if m['operation']=='snapshot':r=self.client.get('/api/enterprise/sync/v1/snapshot')
        else:
            r=self.client.put('/api/enterprise/sync/v1/entities/'+m['kind']+'/'+m['entity_id'],json={k:m[k] for k in ('request_id','expected_revision','payload')})
        assert r.status_code==200,(r.status_code,r.json().get('code'))
        return r.json()


def empty(path):
    with pytest.MonkeyPatch.context() as local:
        local.setenv('MENTORFLOW_DEPLOYMENT_MODE','PERSONAL_LOCAL')
        local.delenv('MENTORFLOW_DATABASE_URL',raising=False)
        local.delenv('MENTORFLOW_MIGRATION_DATABASE_URL',raising=False)
        orm.configure_orm(path);orm.upgrade_orm_schema();orm.init_orm()
        from app import active_teaching
        active_teaching.initialize_active_teaching_storage()


def test_large_http_commit_retry_cas_restore(server,tmp_path):
    _,service,contexts=server;actor,tenant=contexts[0]
    client=http_client(service,actor,tenant)
    table='active_plan_calendars'
    row=dict(plan_id='large-plan',workspace_id='large-business',calendar=json.dumps({'slots':'x'*6_458_439}),available_capacity_minutes=60,created_at='2026-09-17')
    payload=project(table,row);entity=record_id(table,row)
    path='/api/enterprise/sync/v1/entities/calendar/'+entity
    body=dict(request_id='large-write',expected_revision=0,payload=payload)
    first=client.put(path,json=body);assert first.status_code==200
    assert client.put(path,json=body).json()==first.json()
    assert client.put(path,json={**body,'request_id':'stale'}).status_code==409
    huge=json.loads(json.dumps(payload));huge['product_record']['values']['calendar']='x'*(8*1024*1024)
    assert client.put(path,json={**body,'payload':huge,'request_id':'oversize'}).status_code==422
    b=tmp_path/'b.sqlite';empty(b)
    relay=Relay(client,actor,tenant);producer=ProductSyncProducer(b,relay.scope,relay.call);producer.start()
    with sqlite3.connect(b) as c:
        assert c.execute('select calendar from active_plan_calendars where plan_id=?',('large-plan',)).fetchone()[0]==payload['product_record']['values']['calendar']
    other=http_client(service,*contexts[1]);assert other.get('/api/enterprise/sync/v1/snapshot').json()['entities']==[]


def test_retained_primary_intake_rehearsal(server,tmp_path):
    source=os.environ.get('MF04_INTAKE_SNAPSHOT')
    if not source:pytest.skip('explicit retained read-only snapshot not selected')
    prepared=prepare(Path(source),expected_sha256='bfb6805df8913f5891a8d5b01f96683e0d432684870f1e6b79e9c5ef4a0caf43',owner_sha256='1fafc6b6212ca2ba594bcf72aea9764092688ab44b1aa8f75813265dd2c1aee5')
    assert prepared['admitted']['orm_plans']==83
    assert prepared['admitted']['orm_sources']==330
    _,service,contexts=server;actor,tenant=contexts[0]
    relay=Relay(http_client(service,actor,tenant),actor,tenant)
    a=tmp_path/'intake.sqlite';empty(a)
    with pytest.raises(ValueError,match='TARGET_SCOPE_MISMATCH'):
        execute(prepared,a,relay,expected_scope=scope(*contexts[1]))
    send=relay.call
    lost=[False]
    def lose_after_commit(message):
        result=send(message)
        if message['operation']=='write' and not lost[0]:
            lost[0]=True
            raise OSError('synthetic ACK lost after commit')
        return result
    relay.call=lose_after_commit
    with pytest.raises(ProductSyncFailure):execute(prepared,a,relay,expected_scope=scope(actor,tenant))
    with sqlite3.connect(a) as c:assert c.execute('select completed from product_intake_run').fetchone()[0]==0
    relay.call=send
    result=execute(prepared,a,relay,expected_scope=scope(actor,tenant));assert result['status']=='VERIFIED_COMPLETE'
    assert execute(prepared,a,relay,expected_scope=scope(actor,tenant))==result
    # Independent B has no intake journal/source database and A is not used.
    b=tmp_path/'empty-b.sqlite';empty(b)
    producer=ProductSyncProducer(b,relay.scope,relay.call);producer.start()
    with sqlite3.connect(b) as c:
        for table,count in prepared['admitted'].items():
            assert c.execute('select count(*) from "'+table+'"').fetchone()[0]==count
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    assert http_client(service,*contexts[1]).get('/api/enterprise/sync/v1/snapshot').json()['entities']==[]


def test_snapshot_item_budget_fails_closed(server):
    from sqlalchemy import text
    from app.enterprise_tenancy.context import TenantTransactionContext
    from app.enterprise_tenancy.models import EnterprisePersistenceRequestError
    _,service,contexts=server;actor,tenant=contexts[0]
    scoped=scope(actor,tenant)
    with service.transactions.transaction(TenantTransactionContext(actor=actor,tenant=tenant)) as tx:
        service._bind(tx,scoped,writing=True)
        tx.connection.execute(text("""INSERT INTO structured_sync_entities
          (issuer,subject_id,organization_id,workspace_id,kind,entity_id,revision,deleted,payload)
          SELECT :issuer,:subject_id,:organization_id,:workspace_id,'setting','budget-'||n,1,false,'{}'::json
          FROM generate_series(1,32769) n"""),scoped)
    with pytest.raises(EnterprisePersistenceRequestError,match='Snapshot limit'):
        service.snapshot(actor,tenant)


def test_retained_admission_covers_active_namespace_and_text():
    source=os.environ.get('MF04_INTAKE_SNAPSHOT')
    if not source:pytest.skip('explicit read-only snapshot required')
    prepared=prepare(Path(source),expected_sha256='bfb6805df8913f5891a8d5b01f96683e0d432684870f1e6b79e9c5ef4a0caf43',owner_sha256='1fafc6b6212ca2ba594bcf72aea9764092688ab44b1aa8f75813265dd2c1aee5')
    assert prepared['admitted']['active_plans']==55
    assert prepared['admitted']['active_plan_tasks']==11363
    assert prepared['admitted']['active_knowledge_progress_snapshots']==3654
    assert prepared['admitted']['active_knowledge_points']==3654
    assert prepared['admitted']['orm_chunks']==500
    assert prepared['admitted']['active_learner_profiles']==1
    assert prepared['held']['orm_plan_execution_tasks']==797
    assert prepared['held']['active_plans']==2


def test_retained_active_product_plan_and_text_on_empty_b(server,tmp_path):
    from app.product_intake import sha256_file
    from app import active_teaching
    source=os.environ.get('MF04_INTAKE_SNAPSHOT')
    if not source:pytest.skip('explicit read-only snapshot required')
    workspace='ws_eb4425c22355-active'
    # Scope this expensive HTTP/PG proof to one actual active learning world and
    # an existing text source. Full admission is checked separately above.
    clone=tmp_path/'source-slice.sqlite'
    with sqlite3.connect(Path(source).as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(clone) as dst:src.backup(dst)
    with sqlite3.connect(clone) as c:
        text_workspace=c.execute("select workspace_id from orm_sources where source_type='text' and id in (select source_id from orm_chunks) limit 1").fetchone()[0]
        c.execute('delete from orm_workspace_ownership where workspace_id not in (?,?)',(workspace,text_workspace))
    prepared=prepare(clone,expected_sha256=sha256_file(clone),owner_sha256='1fafc6b6212ca2ba594bcf72aea9764092688ab44b1aa8f75813265dd2c1aee5')
    assert prepared['admitted']['active_plan_tasks']==648
    assert prepared['admitted']['orm_chunks']>0
    _,service,contexts=server;actor,tenant=contexts[0]
    relay=Relay(http_client(service,actor,tenant),actor,tenant)
    a=tmp_path/'a.sqlite';empty(a)
    assert execute(prepared,a,relay,expected_scope=scope(actor,tenant))['status']=='VERIFIED_COMPLETE'
    b=tmp_path/'empty-b.sqlite';empty(b)
    producer=ProductSyncProducer(b,relay.scope,relay.call);producer.start()
    # Query the real product read service, not just the transport table.
    plan=active_teaching.current_plan(workspace)
    expected=next(r for r in prepared['records'] if r['record_type']=='active_plans')
    assert plan['id']==expected['payload']['product_record']['values']['id']
    with producer.connect() as c:
        for record in prepared['records']:
            spec=RECORDS[record['record_type']]
            row=c.execute('select * from "'+record['record_type']+'" where '+' AND '.join('"'+k+'"=?' for k in spec['keys']),json.loads(record['row_key'])).fetchone()
            assert project(record['record_type'],dict(row))==record['payload']
        assert c.execute('select count(*) from active_plan_tasks').fetchone()[0]==648
        assert c.execute('select count(*) from active_knowledge_progress_snapshots').fetchone()[0]>0
        assert c.execute('select count(*) from active_knowledge_points').fetchone()[0]>0
        assert c.execute('select count(*) from active_chunks').fetchone()[0]>0
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    assert http_client(service,*contexts[1]).get('/api/enterprise/sync/v1/snapshot').json()['entities']==[]


def test_cross_task_profile_requires_owned_provenance(tmp_path):
    from app.product_intake import sha256_file
    source=os.environ.get('MF04_INTAKE_SNAPSHOT')
    if not source:pytest.skip('explicit read-only snapshot required')
    clone=tmp_path/'foreign-profile.sqlite'
    with sqlite3.connect(Path(source).as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(clone) as dst:src.backup(dst)
    with sqlite3.connect(clone) as c:
        profile=json.loads(c.execute('select compact_profile from active_learner_profiles').fetchone()[0])
        profile['learned_points'].append({'workspace_id':'foreign-unproven','name':'foreign'})
        c.execute('update active_learner_profiles set compact_profile=?',(json.dumps(profile),))
    prepared=prepare(clone,expected_sha256=sha256_file(clone),owner_sha256='1fafc6b6212ca2ba594bcf72aea9764092688ab44b1aa8f75813265dd2c1aee5')
    assert prepared['admitted']['active_learner_profiles']==0
    assert prepared['held']['active_learner_profiles']==1
    assert prepared['admitted']['active_plan_tasks']==11363


@pytest.mark.parametrize('media_type',['text/plain','application/json'])
def test_original_text_recovery_requires_exact_recorded_hash(tmp_path,media_type):
    import hashlib
    from app.product_intake import attach_original_files,sha256_file
    original=tmp_path/'lesson.txt';original.write_bytes(b'hello\r\nworld\r\n')
    source=tmp_path/'source.sqlite'
    with sqlite3.connect(source) as c:
        c.execute('create table orm_sources(id text,local_path text,source_type text)')
        c.execute('create table active_materials(id text,stored_path text)')
        c.execute('insert into active_materials values(?,?)',('material-real',str(original)))
    raw=b'hello\nworld\n'
    payload={'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw),'media_type':media_type}
    prepared={'source_sha256':sha256_file(source),'admitted':{},'records':[{'record_type':'active_materials','entity_id':'material-real','payload':payload}]}
    result=attach_original_files(prepared,source,recovery_root=tmp_path/'recovered')
    assert len(result['original_files'])==1 and result['missing_originals']==[]
    assert Path(result['original_files'][0]['source_path']).read_bytes()==raw
    assert original.read_bytes()==b'hello\r\nworld\r\n'
    payload['sha256']='0'*64
    rejected=attach_original_files(prepared,source,recovery_root=tmp_path/'recovered')
    assert rejected['original_files']==[] and rejected['missing_originals']==['material-real']
    assert sha256_file(source)==prepared['source_sha256']
