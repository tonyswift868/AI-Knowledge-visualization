"""135: real loopback HTTP, 413 subdivision, CAS replay and slow receipts."""
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import time
from types import SimpleNamespace

import httpx
import pytest

from test_sync_course import connection, MemoryStore, record, wire
from refactor.learning.course.sync_course import CourseSync
from refactor.learning.sync_identity import PlanError
from refactor.learning import sync_transport as transport_module

PATH='/api/enterprise/sync/v1/entities/bulk'


class Local135:
    def __init__(self, server):
        self.server=server
        self.cap=2400
        self.batch_calls=[];self.singles=[];self.timeouts=[]
        self.bad=set();self.singleton_reject=False
        self.route_status=0;self.route_after=0;self.auth_status=0
        self.delay=0;self.drop_once=False

    def handle(self, method, path, headers, content):
        request=httpx.Request(method,'http://local.test'+path,headers=headers,content=content)
        if self.auth_status:return httpx.Response(self.auth_status,json={'error':{'code':'UNAUTHORIZED'}})
        if path.split('?')[0]==PATH:
            rows=json.loads(content)['records'];self.batch_calls.append(rows)
            if self.route_status and len(self.batch_calls)>self.route_after:
                return httpx.Response(self.route_status)
            if len(content)>self.cap or self.singleton_reject and len(rows)==1:
                return httpx.Response(413)
            results=[]
            for row in rows:
                deleted=row.get('deleted',False)
                endpoint=f"/api/enterprise/sync/v1/entities/{row['kind']}/{row['entity_id']}"
                body={k:row[k] for k in ('request_id','expected_revision')}
                if not deleted:body['payload']=row['payload']
                response=self.server.handle(httpx.Request('POST' if deleted else 'PUT',
                    'http://local.test'+endpoint+('/delete' if deleted else ''),headers=headers,json=body))
                results.append(dict(kind=row['kind'],entity_id=row['entity_id'],status=response.status_code,
                    **({'result':response.json()} if response.status_code==200 else {})))
            if self.drop_once:
                self.drop_once=False
                return None  # Commit succeeded, but lose the HTTP receipt once.
            return httpx.Response(200,json=dict(schema='mentorflow.structured-sync-bulk.v1',results=results))
        if method in {'PUT','POST'}:
            self.singles.append(json.loads(content))
            if request.url.path.rsplit('/',1)[-1] in self.bad:
                return httpx.Response(413)
        return self.server.handle(request)


@pytest.fixture
def local135(connection):
    server,transport,_=connection
    fake=Local135(server)
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def dispatch(self):
            content=self.rfile.read(int(self.headers.get('Content-Length','0')))
            response=fake.handle(self.command,self.path,dict(self.headers),content)
            if response is None:
                self.close_connection=True
                return
            body=response.content
            self.send_response(response.status_code)
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            if fake.delay and body:
                self.wfile.write(body[:1]);self.wfile.flush()
                time.sleep(fake.delay)
                self.wfile.write(body[1:])
            else:self.wfile.write(body)
            self.wfile.flush()
        do_GET=do_POST=do_PUT=dispatch
        def log_message(self,*_):pass
    httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=httpd.serve_forever,daemon=True);thread.start()
    transport._client.close()
    transport._client=httpx.Client(timeout=20,trust_env=False,event_hooks={
        'request':[lambda r:fake.timeouts.append((r.url.path,dict(r.extensions['timeout'])))]})
    transport._base_url=f'http://127.0.0.1:{httpd.server_port}'
    try:yield fake,transport
    finally:transport.close();httpd.shutdown();httpd.server_close();thread.join(2)


def rows135(count=9):
    return [record(str(i),payload={'title':f'module {i}','text':'x'*350}) for i in range(count)]


def test_split135_oversize_batch_halves_with_unchanged_cas_and_no_duplicates(local135,caplog):
    fake,transport=local135;rows=rows135();store=MemoryStore(*rows)
    progress=[];sync=CourseSync(store,transport)
    sync.on_progress=lambda value,cancel:progress.append(value)
    with caplog.at_level('INFO',logger='refactor.runtime'):
        result=sync.sync()
    assert result['ok'] and result['uploaded']==len(rows) and not store.pending
    assert len(fake.batch_calls)>1 and len(fake.batch_calls[0])==len(rows)
    operations={}
    for batch in fake.batch_calls:
        for op in batch:
            if op['entity_id'] in operations:assert op==operations[op['entity_id']]
            operations[op['entity_id']]=op
    assert len(fake.server.requests)==len(rows) and fake.server.sequence==len(rows)
    assert all(r['revision']==1 for r in fake.server.rows.values())
    assert Counter(a[0] for a in store.acks)==Counter((r.entity,r.entity_id) for r in rows)
    assert [v['completed'] for v in progress if v['phase']=='upload']==sorted(
        v['completed'] for v in progress if v['phase']=='upload')
    fields=[r.mf_fields for r in caplog.records if hasattr(r,'mf_fields')]
    assert any(f.get('state')=='UPLOAD_BATCH_SPLIT' for f in fields)
    assert not any(f.get('state')=='ROUND_ERROR' for f in fields)


def test_split135_singleton_bulk_falls_back_only_bad_record_remains(local135):
    fake,transport=local135;rows=rows135(5)
    fake.cap=1;fake.bad={wire(rows[2])[0]};store=MemoryStore(*rows)
    result=CourseSync(store,transport).sync()
    assert result['uploaded']==4 and result['failed']==1 and result['pending']==1
    assert result['failure_codes']==['SYNC_RECORD_TOO_LARGE']
    assert list(store.pending)==[(rows[2].entity,rows[2].entity_id)]
    originals={r['entity_id']:r for r in fake.batch_calls[0]}
    for body in fake.singles:
        id=wire(next(r for r in rows if wire(r)[1]==body['payload']))[0]
        assert body['request_id']==originals[id]['request_id']
        assert body['expected_revision']==originals[id]['expected_revision']
    assert len(fake.server.requests)==4
    fake.bad.clear()
    assert CourseSync(store,transport).sync()['ok']
    assert len(fake.server.requests)==5 and fake.server.sequence==5


def test_split135_entirely_rejected_single_record_fails_without_ack(local135):
    fake,transport=local135;r=record();fake.cap=1;fake.bad={wire(r)[0]};store=MemoryStore(r)
    with pytest.raises(PlanError,match='服务器或代理拒收') as caught:CourseSync(store,transport).sync()
    assert caught.value.code=='SYNC_RECORD_TOO_LARGE' and store.pending and not store.acks


@pytest.mark.parametrize('status',[404,405])
@pytest.mark.parametrize('after',[0,2])
def test_split135_missing_route_legacy_fallback_keeps_unsent_halves(local135,status,after):
    fake,transport=local135;fake.route_status=status;fake.route_after=after
    rows=rows135(9);store=MemoryStore(*rows)
    assert CourseSync(store,transport).sync()['ok']
    assert not transport._bulk_supported and len(fake.server.requests)==len(rows)
    assert fake.server.sequence==len(rows) and not store.pending


@pytest.mark.parametrize('status',[401,403])
def test_split135_auth_rejection_does_not_split_or_fallback(local135,status):
    fake,transport=local135;fake.auth_status=status
    with pytest.raises(PlanError) as caught:
        transport.write_many([dict(kind='learning_run',entity_id='one')])
    assert caught.value.code=='REAUTH_REQUIRED'
    assert not fake.batch_calls and not fake.singles


def test_split135_lost_child_receipt_replays_same_ids_once(local135):
    fake,transport=local135;fake.drop_once=True;rows=rows135();store=MemoryStore(*rows)
    assert CourseSync(store,transport).sync()['ok']
    assert len(fake.server.requests)==len(rows) and fake.server.sequence==len(rows)
    assert all(r['revision']==1 for r in fake.server.rows.values())


@pytest.mark.parametrize('operation',['bulk','changes','single'])
def test_split135_slow_200_uses_httpx_read_timeout_not_total_deadline(local135,monkeypatch,operation):
    fake,transport=local135;fake.cap=10000;fake.delay=.04;fake.server.incremental=True
    real_monotonic=time.monotonic
    monkeypatch.setattr(transport_module,'time',SimpleNamespace(monotonic=lambda:real_monotonic()*1000))
    r=record();eid,payload=wire(r);started=real_monotonic()
    if operation=='bulk':
        result=transport.write_many([dict(kind='learning_run',entity_id=eid,request_id='slow135',
            expected_revision=0,payload=payload)])
        assert result[0]['status']==200
    elif operation=='changes':assert transport.read_changes()['entities']==[]
    else:assert transport.write('learning_run',eid,request_id='slow135',expected_revision=0,payload=payload)['revision']==1
    assert (real_monotonic()-started)*1000>20
    assert fake.timeouts[-1][1]==dict(connect=20,read=60,write=20,pool=20)


def test_split135_other_requests_keep_existing_timeout(local135):
    fake,transport=local135;transport.read_snapshot()
    assert fake.timeouts[-1][1]==dict(connect=20,read=20,write=20,pool=20)


def test_split135_window_status_remains_syncing_during_split(local135):
    from PySide6.QtCore import QCoreApplication
    from refactor.learning.app.sync import _WindowCourseSyncService
    app=QCoreApplication.instance() or QCoreApplication([])
    fake,transport=local135;store=MemoryStore(*rows135());states=[]
    service=_WindowCourseSyncService(store,transport,auto_start=False)
    service.state_changed.connect(lambda value:states.append(dict(value)))
    try:
        service.start();deadline=time.monotonic()+10
        while not any(s['state']=='synced' for s in states) and time.monotonic()<deadline:
            app.processEvents();time.sleep(.002)
        assert states and states[-1]['state']=='synced'
        assert all(s['state'] in {'syncing','synced'} for s in states)
        assert any('正在同步' in s['message'] for s in states)
    finally:service.dispose()


def test_split135_partial_rejection_ui_keeps_syncing_and_pending_count(local135):
    from PySide6.QtCore import QCoreApplication
    from refactor.learning.app.sync import _WindowCourseSyncService
    app=QCoreApplication.instance() or QCoreApplication([])
    fake,transport=local135;rows=rows135(5);fake.cap=1;fake.bad={wire(rows[2])[0]}
    service=_WindowCourseSyncService(MemoryStore(*rows),transport,auto_start=False)
    states=[];service.state_changed.connect(lambda value:states.append(dict(value)))
    try:
        service.start();deadline=time.monotonic()+10
        while not service._receipt.get('failed') and time.monotonic()<deadline:
            app.processEvents();time.sleep(.002)
        assert service._receipt['failure_codes']==['SYNC_RECORD_TOO_LARGE']
        assert service.status()['state']=='syncing' and '1 条待重试' in service.status()['message']
        assert all(s['state']=='syncing' for s in states)
        # Existing UI omits per-row failure reasons; report this scope gap.
        assert '413' not in service.status()['message']
    finally:service.dispose()
