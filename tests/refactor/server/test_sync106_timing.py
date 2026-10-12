"""Opt-in 106 local TCP benchmark against the real server sync routes.

SYNC106_BENCH=1; SYNC106_COPY=<readonly-backup>; SYNC106_OUTPUT=<task-dir>.
No remote address, credentials or model is used. Not part of ordinary CI timing.
"""
import asyncio
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import threading
import time
from types import ModuleType

import httpx
import pytest
import uvicorn
from sqlalchemy import text

from test_sync106_bulk import sync, connection106, PATH
from slim_sqlite_support import system
from refactor.server import app as entry
from refactor.learning.course.store import CourseStore
from refactor.learning.course.sync_course import CourseSync, _envelope, _wire_id, _metadata, _json, CURSOR_SCHEMA


@pytest.mark.skipif(os.getenv('SYNC106_BENCH') != '1', reason='explicit local timing run only')
def test_sync106_real_server_proxy_latency_before_after(sync, tmp_path):
    output = Path(os.environ['SYNC106_OUTPUT'])
    account, transport, _ = connection106(sync)
    with sqlite3.connect(Path(os.environ['SYNC106_COPY']).as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute('SELECT * FROM _records ORDER BY local_revision,entity,entity_id')]
    values = [dict(schema=f"mentorflow.course.{r['entity']}.v1", plan_key=r['plan_key'],
                   entity=r['entity'], entity_id=r['entity_id'], payload=json.loads(r['payload'])) for r in rows]
    # The measured source is a genuine readonly snapshot. Seed all its records
    # through the real server's API; tombstones use the unchanged delete route.
    def seed():
        for offset in range(0,len(rows),100):
            batch = [dict(kind='learning_run', entity_id=_wire_id(v), expected_revision=0,
                          request_id=f'seed106-{i}', payload=_envelope(v))
                     for i,v in enumerate(values[offset:offset+100],offset)]
            receipts = sync[0].post(PATH, headers=sync[2], json={'records':batch}).json()['results']
            assert all(r['status']==200 for r in receipts), receipts
        for i,r in enumerate(rows):
            if r['deleted']:
                assert sync[0].post('/api/enterprise/sync/v1/entities/learning_run/'+_wire_id(values[i])+'/delete',
                    headers=sync[2],json=dict(request_id=f'delseed106-{i}',expected_revision=1)).status_code==200
    seed()
    class Delayed:
        async def __call__(self, scope, receive, send):
            if scope['type']=='http': await asyncio.sleep(.25)
            await entry.app(scope,receive,send)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    assert port not in (18763,18764)
    server=uvicorn.Server(uvicorn.Config(Delayed(), log_level='error', lifespan='off',
        proxy_headers=True,forwarded_allow_ips='127.0.0.1',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    ready=time.monotonic()+10
    while not server.started and thread.is_alive() and time.monotonic()<ready: time.sleep(.01)
    assert server.started
    class LocalTCP(httpx.BaseTransport):
        def __init__(self): self.inner=httpx.HTTPTransport();self.calls=0
        def handle_request(self, request):
            self.calls+=1
            url=request.url.copy_with(scheme='http',host='127.0.0.1',port=port)
            request=httpx.Request(request.method,url,headers=dict(request.headers)|{'X-Forwarded-Proto':'https'},
                                  content=request.content,extensions=request.extensions)
            return self.inner.handle_request(request)
        def close(self): self.inner.close()
    tcp=LocalTCP()
    transport._client.close();transport._client=httpx.Client(transport=tcp,timeout=20)
    before=ModuleType('baseline106')
    before.__package__='refactor.learning.course'
    code=subprocess.run(['git','show','c2cf62bd:refactor/learning/course/sync_course.py'],
                        check=True,capture_output=True,text=True,encoding='utf-8').stdout
    exec(compile(code,'baseline106.py','exec'),before.__dict__)
    arms=os.getenv('SYNC106_ARMS','both')
    results=(json.loads((output/'timings.json').read_text(encoding='utf-8')) if arms=='after'
             else {'port':port,'records':len(rows),'delay_ms':250,'measurements':{}})
    results.setdefault('ports',{})
    selected=[]
    for entity,count in [('points',471),('modules',6),('plans',1),('ledger_events',1)]:
        selected.extend((r['entity'],r['entity_id']) for r in rows if r['entity']==entity and not r['deleted'])
        # Keep exactly the specified count for each entity.
        selected=[k for k in selected if k[0]!=entity]+[k for k in selected if k[0]==entity][:count]
    assert len(selected)==479
    def cursor_now():
        page_rows=[];since='0'
        while True:
            page=sync[0].get('/api/enterprise/sync/v1/changes',headers=sync[2],params={'since':since,'limit':500}).json()
            page_rows.extend(page['entities']);since=page['next_cursor']
            if not page['has_more']:break
        scope=transport.require_current().snapshot_scope()
        cursor=dict(schema=CURSOR_SCHEMA,scope=scope,since=since,records={})
        for r in page_rows:
            if not r['deleted']:
                cursor['records'][r['entity_id']]=_metadata(r['payload']['values'],r['revision'],False)
        return cursor
    def copy_store(name, selected):
        store=CourseStore(account,tmp_path/name)
        with sqlite3.connect(Path(os.environ['SYNC106_COPY']).as_uri()+'?mode=ro',uri=True) as db:
            db.backup(store._db)
        cursor=cursor_now()
        with store.transaction():
            store._db.execute('UPDATE _records SET pending=0')
            store._db.executemany('UPDATE _records SET pending=1 WHERE entity=? AND entity_id=?',selected)
            store.set_sync_cursor(_json(cursor))
        return store
    try:
        for label,cls in ([('after',CourseSync)] if arms=='after' else [('before',before.CourseSync),('after',CourseSync)]):
            results['ports'][label]=port
            if label=='after':
                # Identical starting server state, not the previous arm's
                # echo/repair revisions. Only this isolated fixture is reset.
                with sync[1].begin() as c:
                    c.execute(text('DELETE FROM structured_sync_requests'))
                    c.execute(text('DELETE FROM structured_sync_entities'))
                seed()
            with CourseStore(account,tmp_path/(label+'-new')) as fresh:
                start=time.monotonic();calls=tcp.calls
                outcome=cls(fresh,transport).sync()
                results['measurements'][label+'-a']=dict(seconds=time.monotonic()-start,requests=tcp.calls-calls,**outcome)
                assert outcome['ok']
                # An unknown tombstone stays in the sync cursor; it has no
                # business row to delete on a fresh device.
                assert fresh._db.execute('SELECT count(*) FROM _records WHERE deleted=0').fetchone()[0]==sum(not r['deleted'] for r in rows)
            with copy_store(label+'-upload',selected) as sender:
                audit=[]
                runner=cls(sender,transport)
                runner.on_progress=lambda p,c:audit.append((time.monotonic(),p.copy(),len(sender.pending_records(1000))))
                start=time.monotonic();calls=tcp.calls
                outcome=runner.sync()
                results['measurements'][label+'-b']=dict(seconds=time.monotonic()-start,requests=tcp.calls-calls,**outcome)
                assert outcome['ok'] and outcome['uploaded']==479
                results[label+'-queue-evidence']=[dict(elapsed=t-start,progress=p,pending=n) for t,p,n in audit[:8]]
            # A switch after 40 changed learning records: upload plus peer pull.
            lesson=[(r['entity'],r['entity_id']) for r in rows if not r['deleted'] and
                    r['entity'] in ('messages','judgements','ledger_events','point_events')][:40]
            assert len(lesson)==40
            with copy_store(label+'-peer',[]) as peer, copy_store(label+'-lesson',lesson) as sender:
                start=time.monotonic();calls=tcp.calls
                sent=cls(sender,transport).sync();got=cls(peer,transport).sync()
                results['measurements'][label+'-c']=dict(seconds=time.monotonic()-start,requests=tcp.calls-calls,
                                                       uploaded=sent['uploaded'],pulled=got['pulled'])
                assert sent['ok'] and got['ok']
        (output/'timings.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        server.should_exit=True;thread.join(10);transport.close()
    assert not thread.is_alive()
