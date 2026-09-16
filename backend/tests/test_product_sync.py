"""Product handler -> existing PostgreSQL service -> independent product cache."""
import json
import sqlite3
from datetime import date
from pathlib import Path
import pytest
from app import active_teaching, orm
from app.main import app
from app.product_sync import ProductSyncProducer, ProductSyncFailure
from app.enterprise_tenancy.structured_sync import Write, Delete
from app.enterprise_tenancy.models import EnterprisePersistenceRequestError
from test_profile_settings_postgres import server
from test_account_import_ledger_schema import native


def port(service, actor, tenant):
    def send(message):
        if message['operation']=='authorize': return {'authorized':True}
        if message['operation']=='snapshot': return service.snapshot(actor,tenant)
        deleting=message['operation']=='delete'
        model=Delete if deleting else Write
        fields={key:message[key] for key in ('request_id','expected_revision')}
        if not deleting:fields['payload']=message['payload']
        return service.write(actor,tenant,message['kind'],message['entity_id'],model(**fields),deleting=deleting)
    return send


def scope(actor, tenant):
    return dict(issuer=actor.issuer,subject_id=actor.subject_id,organization_id=tenant.organization_id,workspace_id=tenant.workspace_id)


@pytest.fixture
def product(client, server, monkeypatch):
    _, service, contexts=server
    with monkeypatch.context() as local:
        local.setenv('MENTORFLOW_DEPLOYMENT_MODE','PERSONAL_LOCAL')
        local.delenv('MENTORFLOW_DATABASE_URL',raising=False)
        local.delenv('MENTORFLOW_MIGRATION_DATABASE_URL',raising=False)
        orm.configure_orm(orm.ORM_DB)
        actor,tenant=contexts[0]
        producer=ProductSyncProducer(orm.ORM_DB,scope(actor,tenant),port(service,actor,tenant))
        producer.start()
        local.setattr(app.state,'product_sync',producer,raising=False)
        local.setattr(app.state,'product_sync_request_lock',None,raising=False)
        yield client,producer,service,actor,tenant



def test_real_upload_commits_metadata_without_file_bytes(product):
    client,producer,service,actor,tenant=product
    payload=b'Python lists hold elements. Loops repeat a statement.'
    result=client.post('/api/active-teaching/workspaces/product-fixture/materials',files={'file':('lesson.txt',payload,'text/plain')})
    assert result.status_code==200,result.text
    assert result.headers['X-MentorFlow-Product-Sync']=='SERVER_ACKNOWLEDGED'
    material=result.json()
    entities=service.snapshot(actor,tenant)['entities']
    entity=next(e for e in entities if e['entity_id']==material['id'])
    assert entity['revision']==1
    assert entity['payload']['filename']=='lesson.txt'
    serialized=json.dumps(entity)
    assert payload.decode() not in serialized
    assert 'stored_path' not in serialized
    assert 'product-fixture' in serialized
    duplicate=client.post('/api/active-teaching/workspaces/product-fixture/materials',files={'file':('lesson.txt',payload,'text/plain')})
    assert duplicate.status_code==200
    assert duplicate.json()['id']==material['id']
    assert len(service.snapshot(actor,tenant)['entities'])==len(entities)


def test_lost_ack_retry_reuses_frozen_request(product):
    client,producer,service,actor,tenant=product
    send=producer.send
    lost=[False]
    def lose(message):
        result=send(message)
        if message['operation']=='write' and not lost[0]:
            lost[0]=True
            raise OSError('synthetic lost response')
        return result
    producer.send=lose
    result=client.post('/api/active-teaching/workspaces/retry-fixture/materials',files={'file':('lesson.txt',b'Synthetic lesson.','text/plain')})
    assert result.status_code==503
    assert result.json()['sync_status']=='PRODUCT_SYNC_PENDING'
    first=service.snapshot(actor,tenant)['entities']
    producer.send=send
    producer.flush()
    final={e['entity_id']:e for e in service.snapshot(actor,tenant)['entities']}
    assert all(final[e['entity_id']]==e for e in first)
    producer.flush()
    assert {e['entity_id']:e for e in service.snapshot(actor,tenant)['entities']}==final
    with producer.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM product_sync_intents WHERE status!='ACKED'").fetchone()[0]==0


def test_conflicting_real_queue_does_not_block_existing_material_read(product):
    client, producer, service, actor, tenant = product
    response = client.post('/api/active-teaching/workspaces/read-fixture/materials',
        files={'file': ('existing.txt', b'Already committed original material.', 'text/plain')})
    assert response.status_code == 200
    material_id = response.json()['id']
    send = producer.send
    def conflict(message):
        if message['operation'] == 'write':
            raise ProductSyncFailure('PRODUCT_SYNC_CONFLICT')
        return send(message)
    producer.send = conflict
    with producer.connect() as c:
        c.execute('UPDATE active_materials SET original_filename=? WHERE id=?', ('local-change.txt', material_id))
    with pytest.raises(ProductSyncFailure, match='PRODUCT_SYNC_CONFLICT'):
        producer.flush()
    pending = producer.status()['pending']
    result = client.get('/api/active-teaching/workspaces/read-fixture/materials')
    assert result.status_code == 200
    assert material_id in result.text
    assert result.headers['X-MentorFlow-Product-Sync'] == 'PRODUCT_SYNC_CONFLICT'
    assert producer.status()['pending'] == pending > 0


def test_independent_b_reads_resource_and_returns_setting_change(product, tmp_path):
    client,a,service,actor,tenant=product
    material=client.post('/api/active-teaching/workspaces/ab/materials',
        files={'file':('中文资料.txt',b'Synthetic resource kept only on device A.','text/plain')})
    assert material.status_code==200,material.text
    a_path=orm.ORM_DB
    with a.connect() as c:
        original_chunks=[tuple(r) for r in c.execute('SELECT * FROM active_chunks ORDER BY id')]
    assert original_chunks
    b_path=tmp_path/'device-b'/'mentorflow_v2.db'
    b_path.parent.mkdir()
    try:
        orm.configure_orm(b_path)
        orm.upgrade_orm_schema()
        orm.init_orm()
        active_teaching.initialize_active_teaching_storage()
        b=ProductSyncProducer(b_path,scope(actor,tenant),port(service,actor,tenant))
        b.start()
        restored=active_teaching.list_materials('ab')
        assert [r['id'] for r in restored]==[material.json()['id']]
        assert restored[0]['original_filename']=='中文资料.txt'
        with b.connect() as c:
            assert c.execute('SELECT stored_path FROM active_materials').fetchone()[0]==''
            assert [tuple(r) for r in c.execute('SELECT * FROM active_chunks ORDER BY id')]==original_chunks
        active_teaching.update_learning_workspace('ab',{'archived':True})
        b.flush()
        a.restore(service.snapshot(actor,tenant))
        orm.configure_orm(a_path)
        with a.connect() as c:
            assert c.execute("SELECT archived FROM active_workspace_settings WHERE workspace_id='ab'").fetchone()[0]==1
            assert c.execute('SELECT stored_path FROM active_materials').fetchone()[0]
        assert a_path != b_path
    finally:
        orm.configure_orm(a_path)


def test_product_receipt_profile_updates_restore_on_fresh_b(product,tmp_path):
    from test_knowledge_map_v1_api import _receipt
    client,a,service,actor,tenant=product
    material=client.post('/api/active-teaching/workspaces/v1/materials',
        files={'file':('learning.txt',b'Synthetic learning content.','text/plain')}).json()
    receipt=_receipt(material['id'])
    receipt['learner_interests']='桌游、做饭'
    response=client.put('/api/active-teaching/workspaces/v1/receipt',json=receipt)
    assert response.status_code==200,response.text
    first=service.snapshot(actor,tenant)
    assert any(e['kind']=='learner_profile' for e in first['entities'])
    receipt['learner_interests']='骑车'
    response=client.put('/api/active-teaching/workspaces/v1/receipt',json=receipt)
    assert response.status_code==200,response.text
    snapshot=service.snapshot(actor,tenant)
    assert next(e for e in snapshot['entities'] if e['kind']=='learner_profile')['revision']==2
    a_path=orm.ORM_DB
    try:
        orm.configure_orm(tmp_path/'fresh-b.db')
        orm.upgrade_orm_schema();orm.init_orm();active_teaching.initialize_active_teaching_storage()
        b=ProductSyncProducer(orm.ORM_DB,scope(actor,tenant),port(service,actor,tenant))
        b.start()
        assert active_teaching.get_receipt('v1')['learner_interests']=='骑车'
        assert active_teaching.get_learner_preference_profile('v1')['preferences']==[
            {'key':'interest','value':'骑车','source':'explicit'}]
        assert active_teaching.list_materials('v1')[0]['device_availability']=='MISSING_ON_THIS_DEVICE'
        b.restore(snapshot)
        with b.connect() as c:
            assert c.execute('SELECT COUNT(*) FROM product_sync_intents').fetchone()[0]==0
    finally:
        orm.configure_orm(a_path)


def seed_synthetic_plan(client):
    """A legal deterministic domain fixture; no Provider or generation bypass."""
    from test_knowledge_map_v1_api import _receipt
    response=client.post('/api/active-teaching/workspaces/domain/materials',
        files={'file':('concept.txt',b'A synthetic concept to practice.','text/plain')})
    assert response.status_code==200,response.text
    material=response.json()['id'];receipt=_receipt(material);now=active_teaching._now()
    with active_teaching._connect() as c:
        c.execute('''INSERT INTO active_knowledge_points
            (id,workspace_id,material_id,normalized_name,name,explanation,difficulty,
             estimated_task_minutes,mastery,acceptance_method,source_evidence,created_at)
            VALUES ('point','domain',?,'concept','Concept','Synthetic concept','easy',10,0,'Explain','[]',?)''',(material,now))
        c.execute('''INSERT INTO active_plans(id,workspace_id,receipt,created_at,feasible,
            total_estimated_task_minutes,unscheduled_task_minutes) VALUES ('plan','domain',?,?,1,10,0)''',(json.dumps(receipt),now))
        c.execute('''INSERT INTO active_plan_tasks(id,plan_id,workspace_id,day_date,day_index,
            task_order,knowledge_point_id,title,objective,source_evidence,acceptance_condition,estimated_task_minutes)
            VALUES ('task','plan','domain',?,0,1,'point','Learn concept','Explain','[]','Explain',10)''',(date.today().isoformat(),))
        c.execute('''INSERT INTO active_teaching_sessions(id,workspace_id,plan_id,state,started_at,updated_at)
            VALUES ('session','domain','plan','{}',?,?)''',(now,now))
        c.commit()
    return receipt


def test_plan_task_progress_and_history_real_domain_consumer(product,tmp_path):
    client,a,service,actor,tenant=product
    receipt=seed_synthetic_plan(client)
    # Existing user self-attestation command and progress projection are the
    # real product writers; the test does not construct sync payloads.
    with active_teaching._connect() as c:
        session=c.execute("SELECT * FROM active_teaching_sessions WHERE id='session'").fetchone()
        task=c.execute("SELECT * FROM active_plan_tasks WHERE id='task'").fetchone()
        point=c.execute("SELECT * FROM active_knowledge_points WHERE id='point'").fetchone()
        active_teaching._v1_self_attested_completion(c,session=session,tasks=[task],task=task,point=point,
            state={},message='I can explain this synthetic concept.',citations=[])
        active_teaching._capture_knowledge_progress(c,'domain')
        active_teaching._record_message(c,'session','learner','answer','Synthetic learning history.',[])
        active_teaching._refresh_plan_calendar(c,'plan',receipt)
        c.commit()
    # This fixture writes directly to domain stores. Reads deliberately do not
    # flush the queue; acknowledge these writes through the existing producer.
    a.flush()
    response=client.get('/api/active-teaching/workspaces/domain/plans/current')
    assert response.status_code==200,response.text
    assert response.json()['tasks'][0]['status']=='completed'
    snapshot=service.snapshot(actor,tenant)
    assert {'plan','task','progress','learning_run','history'} <= {e['kind'] for e in snapshot['entities']}
    assert next(e for e in snapshot['entities'] if e['kind']=='plan')['payload']['title']==receipt['learning_goal']
    a_path=orm.ORM_DB
    with a.connect() as c:
        original_chunks=[tuple(r) for r in c.execute('SELECT * FROM active_chunks ORDER BY id')]
    try:
        orm.configure_orm(tmp_path/'b.db');orm.upgrade_orm_schema();orm.init_orm()
        active_teaching.initialize_active_teaching_storage()
        b=ProductSyncProducer(orm.ORM_DB,scope(actor,tenant),port(service,actor,tenant));b.start()
        result=active_teaching.current_plan('domain')
        assert result['id']=='plan' and result['tasks'][0]['id']=='task'
        assert result['tasks'][0]['status']=='completed'
        with b.connect() as c:
            assert c.execute('SELECT completed_task_count FROM active_knowledge_progress_snapshots').fetchone()[0]==1
            assert c.execute("SELECT content FROM active_teaching_messages").fetchone()[0]=='Synthetic learning history.'
            assert [tuple(r) for r in c.execute('SELECT * FROM active_chunks ORDER BY id')]==original_chunks
    finally:
        orm.configure_orm(a_path)


@pytest.mark.parametrize('field',['issuer','subject_id','organization_id','workspace_id'])
def test_wrong_snapshot_scope_rejected_without_local_mutation(product,field):
    _,producer,service,actor,tenant=product
    snapshot=service.snapshot(actor,tenant)
    snapshot['snapshot_scope']={**snapshot['snapshot_scope'],field:'foreign'}
    with producer.connect() as c:
        before=c.execute('SELECT COUNT(*) FROM product_sync_revisions').fetchone()[0]
    with pytest.raises(ProductSyncFailure,match='SCOPE_MISMATCH'):
        producer.restore(snapshot)
    with producer.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM product_sync_revisions').fetchone()[0]==before


def test_real_review_start_commits_and_rehydrates(product,tmp_path):
    client,a,service,actor,tenant=product
    receipt=seed_synthetic_plan(client)
    now=active_teaching._now();today=date.today().isoformat()
    with active_teaching._connect() as c:
        task=c.execute("SELECT * FROM active_plan_tasks WHERE id='task'").fetchone()
        active_teaching._complete_execution_task(c,task)
        c.execute('''INSERT INTO active_review_items(id,session_id,workspace_id,plan_id,
            knowledge_point_id,source_task_id,source_mastery_evidence_id,due_date,
            estimated_task_minutes,created_at,updated_at)
            VALUES ('review','session','domain','plan','point','task','synthetic-evidence',?,5,?,?)''',(today,now,now))
        review=c.execute("SELECT * FROM active_review_items WHERE id='review'").fetchone()
        session=c.execute("SELECT * FROM active_teaching_sessions WHERE id='session'").fetchone()
        point=c.execute("SELECT * FROM active_knowledge_points WHERE id='point'").fetchone()
        active_teaching._materialize_review_occurrence(c,session=session,review=review,point=point,
            due_date=today,cycle=1,trigger_reason='synthetic_due_review')
        active_teaching._reschedule_plan_work(c,plan_id='plan',receipt=receipt,affected_from=date.today())
        c.commit()
    response=client.post('/api/active-teaching/sessions/session/reviews/review/start',json={'as_of':today})
    assert response.status_code==200,response.text
    assert response.json()['review']['status']=='in_progress'
    snapshot=service.snapshot(actor,tenant)
    item=next(e for e in snapshot['entities'] if e['kind']=='review')
    assert item['payload']['product_record']['values']['status']=='in_progress'
    a_path=orm.ORM_DB
    try:
        orm.configure_orm(tmp_path/'review-b.db');orm.upgrade_orm_schema();orm.init_orm()
        active_teaching.initialize_active_teaching_storage()
        b=ProductSyncProducer(orm.ORM_DB,scope(actor,tenant),port(service,actor,tenant));b.start()
        restored=active_teaching.list_session_reviews('session')['items']
        assert restored[0]['id']=='review' and restored[0]['status']=='in_progress'
    finally:
        orm.configure_orm(a_path)


def test_denied_account_fails_before_product_write(product):
    client,producer,*_=product
    def denied(message): raise ProductSyncFailure('PRODUCT_SYNC_DENIED')
    producer.send=denied
    response=client.post('/api/active-teaching/workspaces/denied/materials',
        files={'file':('test.txt',b'Not authorized.','text/plain')})
    assert response.status_code==503
    assert response.json()['sync_status']=='PRODUCT_SYNC_DENIED'
    with producer.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM active_materials WHERE workspace_id='denied'").fetchone()[0]==0


def test_invalid_ack_preserves_intent_and_reports_failure(product):
    client,producer,service,actor,tenant=product
    send=producer.send
    def bad(message):
        result=send(message)
        return {**result,'revision':999} if message['operation']=='write' else result
    producer.send=bad
    response=client.post('/api/active-teaching/workspaces/bad-ack/materials',
        files={'file':('test.txt',b'No false success.','text/plain')})
    assert response.status_code==503
    assert producer.status()['status']=='PRODUCT_SYNC_INVALID_ACK'
    producer.send=send;producer.flush()
    assert producer.status()['pending']==0


def test_resource_tombstone_restore_rejects_older_snapshot(product,tmp_path):
    client,a,service,actor,tenant=product
    response=client.post('/api/active-teaching/workspaces/deletion/materials',
        files={'file':('test.txt',b'Disposable resource.','text/plain')})
    assert response.status_code==200,response.text
    material=response.json()['id']
    first=service.snapshot(actor,tenant)
    # The real material delete handler owns local side effects and protection.
    response=client.delete(f'/api/active-teaching/workspaces/deletion/materials/{material}')
    assert response.status_code==200,response.text
    archived=next(e for e in service.snapshot(actor,tenant)['entities'] if e['entity_id']==material)
    assert archived['deleted'] is False
    assert archived['payload']['product_record']['values']['archived']==1
    response=client.request('DELETE',f'/api/active-teaching/workspaces/deletion/materials/{material}',
                            json={'physical':True,'confirm_unreferenced':True})
    assert response.status_code==200,response.text
    deleted=next(e for e in service.snapshot(actor,tenant)['entities'] if e['entity_id']==material)
    assert deleted['deleted'] is True and deleted['revision']==3
    with pytest.raises(ProductSyncFailure,match='STALE_SNAPSHOT'):
        a.restore(first)


def test_missing_acknowledged_entity_is_not_a_complete_snapshot(product):
    client,producer,service,actor,tenant=product
    result=client.post('/api/active-teaching/workspaces/omission/materials',
        files={'file':('test.txt',b'Durable record.','text/plain')})
    assert result.status_code==200
    snapshot=service.snapshot(actor,tenant);snapshot['entities']=[]
    with pytest.raises(ProductSyncFailure,match='INCOMPLETE_SNAPSHOT'):
        producer.restore(snapshot)
    assert len(active_teaching.list_materials('omission'))==1


def test_unbound_existing_database_is_not_implicitly_migrated(product):
    client,producer,*_=product
    result=client.post('/api/active-teaching/workspaces/retained/materials',
        files={'file':('test.txt',b'Unbound existing data.','text/plain')})
    assert result.status_code==200
    with producer.connect() as c:
        c.execute('DELETE FROM product_sync_binding')
    with pytest.raises(ProductSyncFailure,match='EXISTING_DATA_REQUIRES_MIGRATION'):
        producer.install()
    assert len(active_teaching.list_materials('retained'))==1


def test_server_rejects_secret_before_product_record_validation(product):
    _,_,service,actor,tenant=product
    with pytest.raises(EnterprisePersistenceRequestError) as failure:
        service.write(actor,tenant,'task','private-fixture',Write(request_id='private-field',
            expected_revision=0,payload={'password':'synthetic-not-a-credential'}))
    assert failure.value.status==422
    assert failure.value.code=='SYNC_SECRET_FIELD_REJECTED'
