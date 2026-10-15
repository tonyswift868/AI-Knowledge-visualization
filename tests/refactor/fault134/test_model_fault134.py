"""Real loopback HTTP, including keepalive frames which reset socket read timers."""
import contextlib
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from refactor.learning.connections import credentials, providers, transport
from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.gateway import ModelGateway
from refactor.learning.connections.faults import model_fault
from tests.refactor.gateway.conftest import MemoryVault
from tests.refactor.providers.samples import anthro, chat, anthro_frames, chat_frames


@contextlib.contextmanager
def server134(provider, mode):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(body)
            streaming = body.get('stream',False)
            status = int(mode) if mode.isdigit() else 200
            self.send_response(status)
            self.send_header('Content-Type','text/event-stream' if streaming else 'application/json')
            self.end_headers()
            def write(value):
                self.wfile.write(value.encode() if isinstance(value,str) else value); self.wfile.flush()
            try:
                if status != 200:
                    write(json.dumps({'error':{'type':'vendor_error','message':'PRIVATE_VENDOR_BODY'}}));return
                if mode in {'keepalive','blank','ping'}:
                    for _ in range(40):
                        write('event: ping\ndata: {"type":"ping"}\n\n' if mode == 'ping' else '\n' if mode == 'blank' else ': keep-alive\n\n')
                        time.sleep(.02)
                    return
                if mode == 'overloaded':
                    write('event: error\ndata: {"type":"error","error":{"type":"overloaded_error"}}\n\n');return
                if mode == 'invalid' or mode == 'flaky' and len(calls)==1:
                    write('data: {"unexpected":true}\n\n' if streaming else '{"unexpected":true}');return
                data = anthro(provider='deepseek') if provider == 'deepseek' else chat(provider='zhipu')
                if not streaming:
                    if mode == 'grace':time.sleep(.14)
                    write(json.dumps(data));return
                frames = anthro_frames(data) if provider == 'deepseek' else chat_frames(data)
                if mode == 'grace':time.sleep(.14)
                for i,value in enumerate(frames):
                    write(value)
                    if mode == 'grace' and i == 0:time.sleep(.24)
                    if mode == 'disconnect' and (b'text_delta' in value if provider == 'deepseek' else b'"content"' in value):return
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):pass
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}',calls
    finally:server.shutdown();server.server_close();thread.join(2)


@pytest.fixture
def gateway134(tmp_path,monkeypatch):
    vault=MemoryVault()
    monkeypatch.setattr(credentials,'_get_keyring',lambda:vault)
    gateway = ModelGateway('fault134',tmp_path,first_event_timeout=.20)
    yield gateway
    gateway.close()


def request134(gateway,base,provider,*,stream=True,purpose='connection_test'):
    gateway.save_preset(provider,base_url=base,model='test-model',api_key='fake-secret134',timeout=2)
    return gateway.complete(purpose,[dict(role='user',content='Reply OK')],on_text=(lambda _:None) if stream else None,
                            reasoning_effort='none',max_tokens=32)


@pytest.mark.parametrize('provider',['deepseek','relay_openai'])
@pytest.mark.parametrize('mode,stream',[('keepalive',True),('blank',False),('ping',True)])
@pytest.mark.parametrize('purpose',['teacher','connection_test','outline'])
def test_first_event134_heartbeat_is_not_response_and_never_retries(gateway134,provider,mode,stream,purpose):
    with server134(provider,mode) as (base,calls):
        start=time.monotonic()
        with pytest.raises(ModelError) as caught:request134(gateway134,base,provider,stream=stream,purpose=purpose)
        assert .18 <= time.monotonic()-start < 1
        assert len(calls)==1 and caught.value.code=='MODEL_FIRST_EVENT_TIMEOUT'
    status=gateway134.model_status();fault=status['fault']
    assert status['state']=='error' and fault['category']=='busy'
    assert '服务繁忙' in fault['message'] and [a['id'] for a in fault['actions']]==['retry']
    row=json.loads(gateway134.usage_log.path.with_name('model-failures.log').read_text(encoding='utf-8').splitlines()[-1])
    assert row['reason']=='first_event_timeout'


@pytest.mark.parametrize('provider',['deepseek','relay_openai'])
@pytest.mark.parametrize('stream',[True,False])
def test_first_event134_before_deadline_then_slow_good_reply_survives(gateway134,provider,stream):
    with server134(provider,'grace') as (base,calls):
        result=request134(gateway134,base,provider,stream=stream)
        assert result.text=='答案' and len(calls)==1
        assert 125 <= result.usage.raw['_mentorflow']['first_event_ms'] < 200
    assert gateway134.model_status()['state']=='ok'
    import sqlite3
    with sqlite3.connect(gateway134.usage_log.path) as db:
        value=json.loads(db.execute('select usage_json from calls').fetchone()[0])
    assert value['raw']['_mentorflow']['first_event_ms'] >= 125


@pytest.mark.parametrize('provider',['deepseek','relay_openai'])
@pytest.mark.parametrize('mode,category,phrase,actions',[
    ('402','balance','余额不足',{'recharge','retry'}),('401','key','API Key 无效',{'settings'}),
    ('403','key','API Key 无效',{'settings'}),('429','rate','请求太频繁',{'retry'}),
    ('500','busy','服务繁忙',{'retry'}),('503','busy','服务繁忙',{'retry'}),
    ('overloaded','busy','服务繁忙',{'retry'}),('disconnect','interrupted','回复中断了',{'retry'}),
    ('400','software','软件出了点问题',{'retry'}),('422','software','软件出了点问题',{'retry'}),
    ('invalid','software','软件出了点问题',{'retry'}),
])
def test_fault134_categories_status_actions_no_vendor_text(gateway134,provider,mode,category,phrase,actions):
    # Unknown relay recharge URLs open its existing connection settings, not an invented website.
    with server134(provider,mode) as (base,calls):
        with pytest.raises(ModelError) as caught:request134(gateway134,base,provider)
        assert len(calls)==1
    fault=caught.value.model_fault
    assert fault['category']==category and phrase in fault['message']
    assert {a['id'] for a in fault['actions']}==actions
    assert 'PRIVATE_VENDOR_BODY' not in fault['message']
    assert gateway134.model_status()['fault']==fault


def test_fault134_connect_failure_and_success_clears(gateway134):
    gateway134.first_event_timeout=2
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    with pytest.raises(ModelError) as caught:request134(gateway134,f'http://127.0.0.1:{port}','deepseek')
    assert caught.value.model_fault['category']=='network'
    with server134('deepseek','ok') as (base,_):request134(gateway134,base,'deepseek')
    assert gateway134.model_status()['state']=='ok' and 'fault' not in gateway134.model_status()


def test_fault134_repeat_software_same_scope_and_reset(gateway134):
    seen=[];gateway134.status_listener=seen.append
    for n in range(2):
        error=ModelError('MODEL_RESPONSE_INVALID','INTERNAL ENGLISH')
        notice=gateway134.report_error(error,purpose='same-place',plan_id='plan')
        assert ('还是不行' in notice['message']) == (n==1)
    assert seen[-1]['state']=='error' and seen[-2]['fault']['id']!=seen[-1]['fault']['id']
    gateway134.clear_fault_scope('same-place','plan')
    assert '软件出了点问题' in gateway134.report_error(ModelError('MODEL_RESPONSE_INVALID','raw'),purpose='same-place',plan_id='plan')['message']
    assert '余额' not in transport.http_error(429).message


def test_fault134_legacy_client_nonstream_same_guard():
    import asyncio
    with server134('relay_openai','blank') as (base,calls):
        with pytest.raises(ModelError) as caught:
            asyncio.run(transport.request(dict(base_url=base,model='local',temperature=.2,timeout=2,first_event_timeout=.2),
                                          'fake',[dict(role='user',content='OK')],None,threading.Event()))
        assert caught.value.code=='MODEL_FIRST_EVENT_TIMEOUT' and len(calls)==1


@pytest.mark.parametrize('code',['MODEL_OUTPUT_TRUNCATED','MODEL_RESPONSE_INVALID'])
def test_fault134_exhausted_protocol_and_truncation_are_software(code):
    assert model_fault(ModelError(code,'raw'),'deepseek')['category']=='software'


def test_fault134_plan_existing_retry_only_publishes_exhausted_error(gateway134):
    from types import SimpleNamespace
    from refactor.learning.app.plan_flow import PlanFlow
    gateway134.first_event_timeout=2
    events=[];gateway134.status_listener=events.append
    flow=PlanFlow(SimpleNamespace(gateway=gateway134),lambda *a:None,lambda:None)
    with server134('deepseek','flaky') as (base,calls):
        gateway134.save_preset('deepseek',base_url=base,api_key='fake134',timeout=2)
        parsed,_=flow._ask('outline',('system',[dict(role='user',content='OK')]),lambda _: 'parsed',
                           SimpleNamespace(cancel=threading.Event()),plan_id='plan')
    assert parsed=='parsed' and len(calls)==2
    assert events and all(e['state']=='ok' for e in events)


def test_fault134_status_read_never_instantiates_catalog_or_gateway(tmp_path,monkeypatch):
    from refactor.learning.app.services import AppServices
    services=AppServices(account_id='offline134',data_root=tmp_path,session_provider=lambda:None,
                         api_base_url='https://localhost',ca_bundle=None)
    assert services.model_status()==dict(state='unknown',short='模型待验证')
    assert services._gateway is None and services._catalog is None
    services.close()


def test_fault134_late_old_account_result_cannot_emit_status(gateway134):
    events=[];gateway134.status_listener=events.append;gateway134.close()
    gateway134._publish_status(dict(state='ok',short='模型正常'))
    assert not events


def test_fault134_documented_anthropic_overload_http529():
    from refactor.learning.connections.providers._http import vendor_error
    error=vendor_error('anthropic',529,{'error':{'type':'overloaded_error'}})
    assert model_fault(error,'anthropic')['category']=='busy'
