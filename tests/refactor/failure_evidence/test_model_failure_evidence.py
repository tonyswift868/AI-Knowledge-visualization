import json

import httpx
import pytest

from refactor.learning.connections import credentials, providers
from refactor.learning.connections.gateway import ModelGateway
from refactor.learning.connections.errors import ModelError
from tests.refactor.gateway.conftest import MemoryVault

KEY='sk-FAKE-evidence-secret-only'
STUDENT='这是不能进入日志的学生原始回答'
MESSAGES=[dict(role='user',content='【对话原文（数据）】\n'+json.dumps(dict(role='user',text=STUDENT),ensure_ascii=False))]


@pytest.fixture(autouse=True)
def no_real_io(monkeypatch):
    monkeypatch.setattr(credentials,'_get_keyring',lambda:MemoryVault())
    async def blocked(*a,**kw):raise AssertionError('Real network forbidden')
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',blocked)


def invoke(handler,stream=False):
    return providers.complete('deepseek',base_url='https://api.deepseek.com',model='deepseek-flash',
        api_key=KEY,messages=MESSAGES,system='固定资料正文不得记录',
        http_client=httpx.MockTransport(handler),on_text=(lambda _:None) if stream else None)


@pytest.mark.parametrize('status',[401,402,429,503])
def test_vendor_errors_keep_status_type_and_safe_message_not_headers_body(status):
    error=dict(type='service_error',message=f'Service unavailable; {KEY}; {STUDENT}')
    with pytest.raises(ModelError) as caught:
        invoke(lambda _:httpx.Response(status,json=dict(error=error,student=STUDENT)))
    d=caught.value.diagnostics
    assert d['http_status']==status and d['vendor_error_type']=='service_error'
    assert 'Service unavailable' in d['vendor_error_message']
    serialized=json.dumps(d,ensure_ascii=False)
    assert KEY not in serialized and STUDENT not in serialized and '固定资料正文' not in serialized


def test_json_instead_of_sse_proves_protocol_mismatch_without_body_logging():
    with pytest.raises(ModelError) as caught:
        invoke(lambda _:httpx.Response(200,json={'error':{'type':'upstream_error','message':'temporarily unavailable'}}),True)
    assert caught.value.code=='MODEL_RESPONSE_INVALID'
    d=caught.value.diagnostics
    assert d['stage']=='expected_event_stream' and d['http_status']==200
    assert d['content_type']=='application/json' and d['vendor_error_message']=='temporarily unavailable'
    assert d['last_sse_events']==[]


def frames(values):
    return ''.join(f'event: {name}\ndata: {json.dumps(value)}\n\n' for name,value in values).encode()


def test_sse_recent_eight_types_and_vendor_error_have_no_student_deltas():
    values=[('ping',{'type':'ping','content':STUDENT})]*10
    values += [('error',{'type':'error','error':{'type':'overloaded_error','message':'busy '+KEY+' '+STUDENT}})]
    with pytest.raises(ModelError) as caught:
        invoke(lambda _:httpx.Response(200,headers={'content-type':'text/event-stream'},content=frames(values)),True)
    d=caught.value.diagnostics
    assert d['last_sse_events']==['ping']*7+['error']
    assert d['http_status']==200 and d['vendor_error_type']=='overloaded_error'
    assert STUDENT not in json.dumps(d,ensure_ascii=False) and KEY not in json.dumps(d)


def test_incomplete_sse_records_complete_event_types_without_partial_text():
    values=[('message_start',{'type':'message_start','message':{'id':'m','usage':{'input_tokens':1}}}),
            ('content_block_start',{'type':'content_block_start','index':0,'content_block':{'type':'text','text':STUDENT}})]
    with pytest.raises(ModelError) as caught:
        invoke(lambda _:httpx.Response(200,headers={'content-type':'text/event-stream'},content=frames(values)),True)
    assert caught.value.diagnostics['stage']=='incomplete_anthropic_message'
    assert caught.value.diagnostics['last_sse_events']==['message_start','content_block_start']
    assert STUDENT not in json.dumps(caught.value.diagnostics,ensure_ascii=False)


def test_connection_exception_keeps_class_not_secret_exception_text():
    def fail(request):raise httpx.ConnectError(KEY+' '+STUDENT,request=request)
    with pytest.raises(ModelError) as caught:invoke(fail)
    assert caught.value.code=='MODEL_CONNECTION_FAILED'
    assert caught.value.diagnostics['exception_type']=='ConnectError'
    assert caught.value.diagnostics['http_status'] is None
    assert KEY not in json.dumps(caught.value.diagnostics)


def test_gateway_writes_correlated_local_failure_and_still_records_usage(tmp_path,monkeypatch):
    backend=MemoryVault()
    monkeypatch.setattr(credentials,'_get_keyring',lambda:backend)
    gateway=ModelGateway('failure-test',tmp_path)
    gateway.save_preset('deepseek',api_key=KEY)
    original=providers.complete
    def simulated(*args,**kwargs):
        kwargs['http_client']=httpx.MockTransport(lambda _:httpx.Response(503,json={
            'error':{'type':'overloaded_error','message':'temporarily busy '+KEY+' '+STUDENT}}))
        return original(*args,**kwargs)
    monkeypatch.setattr(providers,'complete',simulated)
    with pytest.raises(ModelError) as caught:gateway.complete('teacher',MESSAGES)
    path=gateway.usage_log.path.with_name('model-failures.log')
    row=json.loads(path.read_text(encoding='utf-8'))
    assert row['request_id']==caught.value.request_id and row['purpose']=='teacher'
    assert row['http_status']==503 and row['vendor_error_type']=='overloaded_error'
    assert row['code']==caught.value.code and caught.value.diagnostic_log_written
    assert KEY not in path.read_text() and STUDENT not in path.read_text(encoding='utf-8')
    assert gateway.usage_log.summary('2020-01-01T00:00:00Z','2099-01-01T00:00:00Z')['total']['calls']==1


def test_invalid_sse_json_keeps_event_name_but_never_logs_raw_data():
    data=b'event: message_delta\ndata: {'+KEY.encode()+b'\n\n'
    with pytest.raises(ModelError) as caught:
        invoke(lambda _:httpx.Response(200,headers={'content-type':'text/event-stream'},content=data),True)
    assert caught.value.diagnostics['stage']=='decoding_sse_event'
    assert caught.value.diagnostics['last_sse_events']==['message_delta']
    assert KEY not in json.dumps(caught.value.diagnostics)


def test_log_write_failure_does_not_replace_original_model_error(tmp_path,monkeypatch):
    backend=MemoryVault()
    monkeypatch.setattr(credentials,'_get_keyring',lambda:backend)
    gateway=ModelGateway('failure-test',tmp_path)
    gateway.save_preset('deepseek',api_key=KEY)
    def fail(*args,**kwargs):raise ModelError('MODEL_CONNECTION_FAILED','connection lost')
    monkeypatch.setattr(providers,'complete',fail)
    def cannot_write(*args,**kwargs):raise PermissionError('read only test')
    monkeypatch.setattr('refactor.learning.connections.gateway.RotatingFileHandler',cannot_write)
    with pytest.raises(ModelError) as caught:gateway.complete('teacher',MESSAGES)
    assert caught.value.code=='MODEL_CONNECTION_FAILED' and not caught.value.diagnostic_log_written
