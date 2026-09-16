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
