import json
import multiprocessing
import threading
import time

import httpx
import pytest

from desktop.product_sync_bridge import ChildProductSyncPort,ProductSyncRelay,ProductSyncBridgeError
from desktop.rehydration_transport import invalidate_active
from desktop.tests.test_rehydration_transport import transport,snapshot


@pytest.fixture(autouse=True)
def fence():
    invalidate_active()
    yield
    invalidate_active()


def test_private_port_reuses_authenticated_sync_and_rejects_stale_account():
    seen=[]
    def handler(request):
        seen.append((request.method,request.url.path))
        assert request.headers['x-mentorflow-workspace-id']=='ws-a'
        if request.method=='PUT':
            body=json.loads(request.content)
            return httpx.Response(200,json=dict(kind='task',entity_id='task-a',revision=1,
                deleted=False,payload=body['payload']))
        return httpx.Response(200,json=snapshot())
    relay=ProductSyncRelay(transport(handler))
    parent,child=multiprocessing.Pipe();stop=threading.Event()
    thread=threading.Thread(target=relay.serve,args=(parent,stop));thread.start()
    port=ChildProductSyncPort(child)
    try:
        assert port({'operation':'authorize'})=={'authorized':True}
        result=port(dict(operation='write',kind='task',entity_id='task-a',request_id='one',
                        expected_revision=0,payload={'title':'Synthetic task'}))
        assert result['revision']==1
        assert port({'operation':'snapshot'})['entities']==snapshot()['entities']
        assert relay.scope['subject_id']=='a'
        assert all('token' not in key for key in relay.scope)
        invalidate_active()
        with pytest.raises(ProductSyncBridgeError,match='DENIED'):
            port({'operation':'authorize'})
    finally:
        stop.set();child.close();thread.join(3)
    assert not thread.is_alive()


@pytest.mark.parametrize('status,code',[(409,'CONFLICT'),(410,'CONFLICT'),(422,'CONTRACT_REJECTED'),(503,'PENDING')])
def test_server_failure_is_not_an_ack(status,code):
    relay=ProductSyncRelay(transport(lambda _:httpx.Response(status,json={})))
    with pytest.raises(ProductSyncBridgeError,match=code):
        relay.call(dict(operation='write',kind='task',entity_id='task-a',request_id='one',
                        expected_revision=0,payload={}))


def test_spawned_account_backend_uses_private_parent_transport(tmp_path,monkeypatch):
    """Real account API child + existing HTTP transport; server responses synthetic."""
    from desktop.account_vault import AccountVaultManager,VaultLayout
    from desktop.runtime import LocalService
    writes=[]
    def handler(request):
        if request.method=='PUT':
            body=json.loads(request.content)
            kind,entity=request.url.path.rsplit('/',2)[-2:]
            writes.append((kind,entity,body))
            return httpx.Response(200,json=dict(kind=kind,entity_id=entity,
                revision=body['expected_revision']+1,deleted=False,payload=body['payload']))
        state=snapshot();state['entities']=[]
        state['snapshot_authority']='POSTGRESQL_SINGLE_STATEMENT_MVCC'
        return httpx.Response(200,json=state)
    session=transport(handler)
    profile=session._identity.profile()
    monkeypatch.setenv('MENTORFLOW_ACCOUNT_VAULT_ROOT',str(tmp_path))
    manager=AccountVaultManager(VaultLayout(tmp_path));manager.prepare_profile(profile)
    service=LocalService(resolved_profile=profile,product_transport=session)
    try:
        service.start()
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            try:
                if LocalService._is_expected_health(httpx.get(service.origin+'/api/health',timeout=.5)):
                    break
            except httpx.HTTPError:
                pass
            assert service.thread.is_alive()
            time.sleep(.1)
        else:
            raise AssertionError('Owned account API did not become healthy')
        response=httpx.post(service.origin+'/api/active-teaching/workspaces/w/materials',
            files={'file':('sample.txt',b'Synthetic product bridge content.','text/plain')},timeout=20)
        assert response.status_code==200,response.text
        assert response.headers['X-MentorFlow-Product-Sync']=='SERVER_ACKNOWLEDGED'
        resource=next(body for kind,entity,body in writes if kind=='resource' and entity==response.json()['id'])
        assert 'Synthetic product bridge content.' not in json.dumps(resource)
        assert 'stored_path' not in json.dumps(resource)
    finally:
        service.stop()
    assert service.handles_closed
    assert service.thread.exitcode==0


def test_measured_aggregate_crosses_private_pipe_without_truncation():
    payload={'product_record':{'calendar':'x'*6_458_439}}
    def handler(request):
        body=json.loads(request.content)
        return httpx.Response(200,json=dict(kind='calendar',entity_id='calendar-large',revision=1,
            deleted=False,payload=body['payload']))
    relay=ProductSyncRelay(transport(handler))
    parent,child=multiprocessing.Pipe();stop=threading.Event()
    thread=threading.Thread(target=relay.serve,args=(parent,stop));thread.start()
    try:
        result=ChildProductSyncPort(child)(dict(operation='write',kind='calendar',entity_id='calendar-large',
            request_id='large',expected_revision=0,payload=payload))
        assert result['payload']==payload
    finally:
        stop.set();child.close();thread.join(3)
    assert not thread.is_alive()
