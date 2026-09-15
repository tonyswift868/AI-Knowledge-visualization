"""Real mounted product commands, native PostgreSQL and independent local readers."""
import json
from datetime import date, timedelta
import pytest
from app import orm, db, active_teaching
from app.main import app
from app.product_sync import ProductSyncProducer, ProductSyncStores, ProductSyncFailure
from test_product_sync import product, port, scope
from test_profile_settings_postgres import server
from test_account_import_ledger_schema import native


def preview(client):
    result = client.post('/api/plan-product/previews',json=dict(request_id='fresh-plan',plan_id='plan-ab',
        start_date=date.today().isoformat(),deadline=(date.today()+timedelta(days=21)).isoformat(),
        daily_minutes=60, review_rounds=0,buffer_minutes=0,
        tasks=[dict(task_id='task-a',title='Synthetic A',minutes=20),dict(task_id='task-b',title='Synthetic B',minutes=20)]))
    assert result.status_code==200,result.text
    return result.json()['preview']['preview_id']


def publish(client):
    key=preview(client)
    result=client.post(f'/api/plan-product/previews/{key}/publish',json=dict(
        confirmation_id='publish-ab',explicitly_confirmed_by_user=True,published_on=date.today().isoformat()))
    assert result.status_code==200,result.text
    return result.json()['published']['publication_id']


def fresh_b(path, actor, tenant, service):
    orm.configure_orm(path)
    orm.upgrade_orm_schema();orm.init_orm();active_teaching.initialize_active_teaching_storage()
    producer=ProductSyncProducer(path,scope(actor,tenant),port(service,actor,tenant));producer.start()
    return producer


def test_plan_publish_completion_roundtrip_b_modifies_plan_progress(product,tmp_path,monkeypatch):
    client,a,service,actor,tenant=product
    key=publish(client)
    a_path=orm.ORM_DB
    resource=client.post('/api/active-teaching/workspaces/ab/materials',files={'file':('test.txt',b'Synthetic bytes on A only.','text/plain')})
    assert resource.status_code==200,resource.text
    assert client.put('/api/product-settings/analogy/keywords',json={'value':'Chess'}).status_code==200
    try:
        b=fresh_b(tmp_path/'b.db',actor,tenant,service)
        monkeypatch.setattr(app.state,'product_sync',b)
        assert client.get(f'/api/plan-product/published/{key}').status_code==200
        assert client.get('/api/product-settings').json()['analogy/keywords']=='Chess'
        assert client.get('/api/active-teaching/workspaces/ab/materials').json()[0]['device_availability']=='MISSING_ON_THIS_DEVICE'
        complete=client.post(f'/api/plan-product/published/{key}/tasks/task-a/complete',json={'explicitly_completed_by_user':True})
        assert complete.status_code==200,complete.text
        shift=client.post(f'/api/plan-product/published/{key}/shifts/preview',json={
            'request_id':'shift-b','calendar_days_delta':1,'weekly_learning_days':['monday','tuesday','wednesday','thursday','friday']})
        assert shift.status_code==200,shift.text
        shift_id=shift.json()['shift_preview']['preview_id']
        changed=client.post(f'/api/plan-product/published/{key}/shifts/{shift_id}/confirm',json={'confirmation_id':'shift-confirm-b','explicitly_confirmed_by_user':True})
        assert changed.status_code==200,changed.text
        b_daily=client.get(f'/api/plan-product/published/{key}/daily').json()
        a.restore(service.snapshot(actor,tenant))
        orm.configure_orm(a_path);monkeypatch.setattr(app.state,'product_sync',a)
        assert client.get(f'/api/plan-product/published/{key}/daily').json()==b_daily
        assert client.get(f'/api/plan-product/published/{key}').json()['current_schedule']['version']==2
        with b.connect() as c:
            assert c.execute('SELECT COUNT(*) FROM active_chunks').fetchone()[0]==0
            assert c.execute('SELECT stored_path FROM active_materials').fetchone()[0]==''
    finally:
        orm.configure_orm(a_path)


@pytest.mark.parametrize('domain',['PLAN','PROGRESS','RESOURCE','SETTING'])
def test_whole_command_retry_after_server_commit_lost_ack(product,domain):
    client,a,service,actor,tenant=product
    if domain=='PROGRESS':
        key=publish(client)
        path=f'/api/plan-product/published/{key}/tasks/task-a/complete'
        args={'json':{'explicitly_completed_by_user':True}}
    elif domain=='PLAN':
        path='/api/plan-product/previews';args={'json':dict(request_id='retry-plan',plan_id='retry-plan',
            start_date=date.today().isoformat(),deadline=(date.today()+timedelta(days=21)).isoformat(),
            daily_minutes=60,review_rounds=0,buffer_minutes=0,
            tasks=[dict(task_id='retry-task',title='Synthetic retry',minutes=20)])}
    elif domain=='RESOURCE':
        path='/api/active-teaching/workspaces/retry/materials'
        args={'files':{'file':('test.txt',b'Synthetic retry bytes.','text/plain')}}
    else:
        path='/api/product-settings/theme';args={'json':{'value':'dark'}}
    method='PUT' if domain=='SETTING' else 'POST'
    send=a.send
    lost=[False]
    def drop(message):
        result=send(message)
        if message['operation']=='write' and not lost[0]:
            lost[0]=True;raise TimeoutError('synthetic ACK loss')
        return result
    a.send=drop
    first=client.request(method,path,headers={'X-MentorFlow-Command-ID':'same-command'},**args)
    assert first.status_code==503,first.text
    a.send=send
    second=client.request(method,path,headers={'X-MentorFlow-Command-ID':'same-command'},**args)
    assert second.status_code<300,second.text
    snapshot=service.snapshot(actor,tenant)['entities']
    third=client.request(method,path,headers={'X-MentorFlow-Command-ID':'same-command'},**args)
    assert third.json()==second.json()
    assert service.snapshot(actor,tenant)['entities']==snapshot
    with a.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM product_sync_commands WHERE id='same-command'").fetchone()[0]==1


def test_review_campaign_real_writes_survive_fresh_device(product,tmp_path,monkeypatch):
    client,a,service,actor,tenant=product
    created=client.post('/api/review/campaigns',json={'mode':'demo'})
    assert created.status_code==201,created.text
    key=created.json()['campaign_id']
    started=client.post(f'/api/review/campaigns/{key}/rounds/start')
    assert started.status_code==200,started.text
    hinted=client.post(f'/api/review/campaigns/{key}/hints')
    # The route names are the mounted product contract, not a transport fixture.
    assert hinted.status_code==200,hinted.text
    answered=client.post(f'/api/review/campaigns/{key}/answers',json={'answer':'Synthetic learner answer','submission_id':'answer-1','attempt_id':'attempt-1'})
    assert answered.status_code==200,answered.text
    before=client.get(f'/api/review/campaigns/{key}').json()
    path=orm.ORM_DB
    try:
        b=fresh_b(tmp_path/'review-b.db',actor,tenant,service)
        monkeypatch.setattr(app.state,'product_sync',b)
        after=client.get(f'/api/review/campaigns/{key}')
        assert after.status_code==200,after.text
        assert after.json()['campaign_id']==before['campaign_id']
        assert after.json()['state']==before['state']
    finally: orm.configure_orm(path)


def test_legacy_create_import_update_delete_and_b_reader(product,tmp_path,monkeypatch):
    client,a,service,actor,tenant=product
    both=ProductSyncStores(a.database,db.DB_PATH,a.scope,a.send)
    both.start();monkeypatch.setattr(app.state,'product_sync',both)
    workspace=client.post('/api/workspaces',json={'title':'Synthetic legacy','execution_mode':'demo'})
    assert workspace.status_code==200,workspace.text
    key=workspace.json()['id']
    source=client.post(f'/api/workspaces/{key}/sources/text',json={'title':'Notes','text':'Synthetic lesson text, kept only on A.'})
    assert source.status_code==200,source.text
    assert client.patch(f'/api/workspaces/{key}',json={'title':'Updated legacy'}).status_code==200
    path,legacy=orm.ORM_DB,db.DB_PATH
    try:
        db.DB_PATH=tmp_path/'legacy-b.db';db.init_db()
        b=fresh_b(tmp_path/'orm-b.db',actor,tenant,service)
        both_b=ProductSyncStores(b.database,db.DB_PATH,b.scope,b.send);both_b.start()
        monkeypatch.setattr(app.state,'product_sync',both_b)
        restored=client.get(f'/api/workspaces/{key}')
        assert restored.status_code==200,restored.text
        assert restored.json()['title']=='Updated legacy'
        assert len(restored.json()['sources'])==1
        with db.connection() as c:
            assert c.execute('SELECT text FROM sources').fetchone()[0]==''
            assert c.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]==0
    finally:
        orm.configure_orm(path);db.DB_PATH=legacy


def test_vault_real_import_metadata_and_b_missing_view(product,tmp_path,monkeypatch):
    from desktop.account_identity import AccountProfile
    from desktop.account_vault import AccountVaultManager,VaultLayout
    from desktop.local_asset_lifecycle import LocalAssetLifecycle
    from desktop.product_asset_metadata import bind_vault_metadata,resource_rows
    client,a,service,actor,tenant=product
    class Api:
        def request(self,method,path,**kwargs):
            response=client.request(method,path,**kwargs)
            assert response.status_code<300,response.text
            return response.json()
    manager=AccountVaultManager(VaultLayout(tmp_path/'vault-a'))
    vault=manager.mount(AccountProfile.personal_local())
    identity={'issuer':'https://synthetic.test','subject':'synthetic'}
    # A real Vault import commits its metadata intent in that same SQLite transaction.
    bind_vault_metadata(vault,Api())
    source=tmp_path/'asset.txt';source.write_bytes(b'Private file bytes never leave A.')
    asset=vault.import_managed(source)
    linked=vault.import_linked(source)
    metadata=client.get('/api/product-resources').json()
    assert {row['id'] for row in metadata}=={asset.local_asset_id,linked.local_asset_id}
    assert 'Private file bytes' not in json.dumps(service.snapshot(actor,tenant))
    assert str(source) not in json.dumps(service.snapshot(actor,tenant))
    path=orm.ORM_DB
    other=AccountVaultManager(VaultLayout(tmp_path/'vault-b'))
    try:
        b=fresh_b(tmp_path/'asset-b.db',actor,tenant,service)
        monkeypatch.setattr(app.state,'product_sync',b)
        target=other.mount(AccountProfile.personal_local())
        lifecycle=LocalAssetLifecycle(target,account_identity=identity)
        rows=resource_rows(target,lifecycle,Api())
        assert len(rows)==2 and all(row.state=='MISSING_ON_THIS_DEVICE' for row in rows)
        assert target.list_assets()==[]
        # Rename is metadata only; delete creates a server tombstone.
        value=metadata[0];key=value.pop('id');value['original_filename']='renamed.txt'
        assert client.put('/api/product-resources/'+key,json=value).status_code==200
        assert client.delete('/api/product-resources/'+key).status_code==200
        assert any(e['deleted'] for e in service.snapshot(actor,tenant)['entities'])
    finally:
        manager.unmount();other.unmount();orm.configure_orm(path)


@pytest.mark.parametrize('failure',['SERVER_UNAVAILABLE','AUTH_DENIED','WORKSPACE_DENIED'])
def test_failure_is_explicit_and_retryable(product,failure):
    client,a,service,actor,tenant=product
    send=a.send
    def fail(message):
        if failure=='SERVER_UNAVAILABLE': raise OSError('synthetic unavailable')
        raise ProductSyncFailure('PRODUCT_SYNC_DENIED')
    a.send=fail
    response=client.put('/api/product-settings/theme',json={'value':'light'},headers={'X-MentorFlow-Command-ID':'failure-retry'})
    assert response.status_code==503
    assert response.json()['sync_status']==('PRODUCT_SYNC_PENDING' if failure=='SERVER_UNAVAILABLE' else 'PRODUCT_SYNC_DENIED')
    assert service.snapshot(actor,tenant)['entities']==[]
    a.send=send
    response=client.put('/api/product-settings/theme',json={'value':'light'},headers={'X-MentorFlow-Command-ID':'failure-retry'})
    assert response.status_code==200,response.text


def test_real_cas_conflict_requires_explicit_fenced_retry(product):
    from app.enterprise_tenancy.models import EnterprisePersistenceRequestError
    from app.enterprise_tenancy.structured_sync import Write
    client,a,service,actor,tenant=product
    assert client.put('/api/product-settings/theme',json={'value':'light'}).status_code==200
    item=service.snapshot(actor,tenant)['entities'][0]
    service.write(actor,tenant,item['kind'],item['entity_id'],Write(request_id='other-device',expected_revision=1,
        payload={**item['payload'],'product_record':{**item['payload']['product_record'],'values':{'id':'theme','state':'"blue"'}}}))
    send=a.send
    def wire(message):
        try:return send(message)
        except EnterprisePersistenceRequestError as exc:
            # The production HTTP bridge performs this same status mapping.
            raise ProductSyncFailure('PRODUCT_SYNC_CONFLICT' if exc.status==409 else 'PRODUCT_SYNC_PENDING') from None
    a.send=wire
    result=client.put('/api/product-settings/theme',json={'value':'dark'},headers={'X-MentorFlow-Command-ID':'conflict-command'})
    assert result.status_code==409,result.text
    conflict=client.get('/api/product-sync/conflicts').json()[0]
    stale=client.post('/api/product-sync/conflicts/'+conflict['request_id'],json={'resolution':'keep_local','expected_server_revision':1})
    assert stale.status_code==409
    resolved=client.post('/api/product-sync/conflicts/'+conflict['request_id'],json={'resolution':'keep_local','expected_server_revision':2})
    assert resolved.status_code==200,resolved.text
    assert service.snapshot(actor,tenant)['entities'][0]['revision']==3
    assert client.get('/api/product-settings').json()['theme']=='dark'


def test_same_command_identity_with_changed_input_is_rejected(product):
    client,*_=product
    args={'headers':{'X-MentorFlow-Command-ID':'fixed'}}
    assert client.put('/api/product-settings/theme',json={'value':'dark'},**args).status_code==200
    response=client.put('/api/product-settings/theme',json={'value':'light'},**args)
    assert response.status_code==503
    assert response.json()['sync_status']=='PRODUCT_SYNC_COMMAND_REUSED'


def test_default_plan_page_payload_can_publish_and_regenerate(product):
    client,*_=product
    demo=client.get('/api/plan-product/demo').json()['creation']
    response=client.post('/api/plan-product/previews',json=demo)
    assert response.status_code==200,response.text
    key=response.json()['preview']['preview_id']
    response=client.post(f'/api/plan-product/previews/{key}/publish',json={'explicitly_confirmed_by_user':True})
    assert response.status_code==200,response.text
    published=response.json()['published']['publication_id']
    demo['request_id']='regenerate-default'
    response=client.post(f'/api/plan-product/published/{published}/regenerate',json={'explicitly_requested_by_user':True,'creation':demo})
    assert response.status_code==200,response.text


def test_full_review_round_and_resume_are_durable(product):
    from app.review_product_v1.demo_fixture import QUESTION_BY_ID
    client,a,service,actor,tenant=product
    view=client.post('/api/review/campaigns',json={}).json()
    path='/api/review/campaigns/'+view['campaign_id']
    view=client.post(path+'/rounds/start').json()
    assert client.post(path+'/pause').status_code==200
    assert client.post(path+'/resume').status_code==200
    for index in range(10):
        item=view['question']['item_id']
        response=client.post(path+'/answers',json={'answer':QUESTION_BY_ID[item].canonical_answer})
        assert response.status_code==200,response.text
        view=response.json()
        if index<9:
            response=client.post(path+'/questions/next')
            assert response.status_code==200,response.text
            view=response.json()
    response=client.post(path+'/finish')
    assert response.status_code==200,response.text
    from app.product_runtime_state import restore_review
    states=a.runtime_states('product_review_state')
    restored=restore_review(states).get_campaign(view['campaign_id'])
    assert restored['state']==response.json()['state']
    assert restored['round_result']==response.json()['round_result']


def test_legacy_task_start_and_complete_use_durable_commands(product,monkeypatch):
    client,a,service,actor,tenant=product
    both=ProductSyncStores(a.database,db.DB_PATH,a.scope,a.send);both.start()
    monkeypatch.setattr(app.state,'product_sync',both)
    ws=client.post('/api/workspaces',json={'title':'Legacy task','execution_mode':'demo'}).json()['id']
    task={'id':'legacy-task','status':'not_started','lesson_id':'lesson','estimated_minutes':10}
    plan={'id':'legacy-plan','workspace_id':ws,'status':'active','study_days':[{'id':'legacy-day','tasks':[task]}]}
    with db.connection() as c:
        c.execute('INSERT INTO plans VALUES(?,?,?,?,?,?,?)',('legacy-plan',ws,1,json.dumps(plan),'active',db.now(),db.now()))
    started=client.post('/api/tasks/legacy-task/start-legacy',headers={'X-MentorFlow-Command-ID':'legacy-start'})
    assert started.status_code==200,started.text
    assert json.loads(db.row('plans','id','legacy-plan')['payload'])['study_days'][0]['tasks'][0]['status']=='in_progress'
    response=client.post('/api/tasks/legacy-task/complete-legacy')
    assert response.status_code==200,response.text
    payload=next(e['payload'] for e in service.snapshot(actor,tenant)['entities'] if e['payload'].get('product_record',{}).get('record_type')=='legacy_plans')
    assert json.loads(payload['product_record']['values']['payload'])['study_days'][0]['tasks'][0]['status']=='completed'


def test_interrupted_command_is_not_reported_synced_or_reexecuted(product):
    client,a,*_=product
    assert a.begin_command('interrupted','digest') is None
    status=client.get('/api/product-sync/status')
    assert status.status_code==200
    assert status.json()['status']=='PRODUCT_SYNC_COMMAND_INTERRUPTED'
    assert status.json()['interrupted_commands']==1
    with pytest.raises(ProductSyncFailure,match='COMMAND_INTERRUPTED'):
        a.begin_command('interrupted','digest')


def test_model_resource_metadata_is_pathless_and_requires_local_binary(product,tmp_path,monkeypatch):
    import hashlib
    client,a,service,actor,tenant=product
    metadata=dict(original_filename='model-X.gguf',media_type='application/octet-stream',
        sha256=hashlib.sha256(b'synthetic model marker').hexdigest(),size_bytes=22,source_kind='MODEL',classification='MODEL')
    assert client.put('/api/product-resources/model-X',json=metadata).status_code==200
    entry=service.snapshot(actor,tenant)['entities'][0]
    assert entry['payload']['product_classification']=='MODEL'
    path=orm.ORM_DB
    try:
        b=fresh_b(tmp_path/'model-b.db',actor,tenant,service)
        monkeypatch.setattr(app.state,'product_sync',b)
        assert client.get('/api/product-resources').json()==[{'id':'model-X',**metadata}]
        assert not list(tmp_path.glob('**/*.gguf'))
    finally:orm.configure_orm(path)
