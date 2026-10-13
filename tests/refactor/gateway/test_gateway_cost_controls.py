import json
import sqlite3
import threading
import asyncio

import httpx
import pytest

from refactor.learning.connections import providers
from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.gateway import (BACKGROUND_PURPOSES, BACKGROUND_TIMEOUT, MODEL_MAX_OUTPUT,
                                                   PURPOSE_EFFORT, PURPOSE_MIN_OUTPUT)
from refactor.learning.connections.usage import UsageLog
from tests.refactor.gateway.test_gateway import configured, answer, MESSAGES, START, END, KEY
from tests.refactor.providers.samples import anthro

# The directory's autouse fixture forbids real provider calls. Keep the real
# adapter for tests that explicitly inject a MockTransport; sockets remain blocked.
_REAL_COMPLETE = providers.complete


@pytest.mark.parametrize('purpose,minimum', list(PURPOSE_MIN_OUTPUT.items()))
@pytest.mark.parametrize('requested', [1,4096,32768])
def test_purpose_caps_raise_only_and_background_timeout(tmp_path,monkeypatch,purpose,minimum,requested):
    g,p=configured(tmp_path)
    calls=[]
    monkeypatch.setattr(providers,'complete',lambda p,**kw:(calls.append(kw),answer())[1])
    g.complete(purpose,MESSAGES,max_tokens=requested)
    assert calls[0]['max_tokens']==MODEL_MAX_OUTPUT.get(p['model'],max(requested,minimum))
    assert calls[0]['timeout']==(BACKGROUND_TIMEOUT if purpose in BACKGROUND_PURPOSES else 120)
    assert calls[0]['reasoning_effort']==(None if purpose=='profile' else 'high')


@pytest.mark.parametrize('timeout',[1,60,270,300])
@pytest.mark.parametrize('purpose',['teacher','point_materials','outline'])
def test_timeout_keeps_larger_preset_and_foreground_unchanged(tmp_path,monkeypatch,timeout,purpose):
    g,p= configured(tmp_path)
    g.save_preset('deepseek',preset_id=p['id'],timeout=timeout)
    calls=[]
    monkeypatch.setattr(providers,'complete',lambda p,**kw:(calls.append(kw),answer())[1])
    g.complete(purpose,MESSAGES)
    assert calls[0]['timeout']==(BACKGROUND_TIMEOUT if purpose in BACKGROUND_PURPOSES else timeout)


@pytest.mark.parametrize('effort',['none','low','high','max'])
@pytest.mark.parametrize('stream',[False,True])
def test_deepseek_wire_and_journal_show_actual_parameters(tmp_path,monkeypatch,effort,stream):
    from tests.refactor.providers.samples import anthro_frames,sse
    real=_REAL_COMPLETE
    g,p=configured(tmp_path)
    bodies=[]
    def wire(request):
        bodies.append(json.loads(request.content))
        data=anthro(provider='deepseek')
        return sse(anthro_frames(data)) if stream else httpx.Response(200,json=data)
    monkeypatch.setattr(providers,'complete',lambda p,**kw:real(p,http_client=httpx.MockTransport(wire),**kw))
    result=g.complete('profile',MESSAGES,reasoning_effort=effort,on_text=(lambda t:None) if stream else None)
    body=bodies[0]
    expected={'thinking':{'type':'disabled' if effort=='none' else 'enabled'}}
    if effort!='none':expected['output_config']={'effort':effort}
    assert 'reasoning' not in body
    for key,value in expected.items():assert body[key]==value
    with sqlite3.connect(g.usage_log.path) as db:
        meta=json.loads(db.execute('select usage_json from calls').fetchone()[0])['raw']['_mentorflow']
    assert meta['sent_reasoning_parameters']==expected
    assert meta['billing_context']['requested_reasoning_effort']==effort
    assert meta['billing_context']['thinking_mode']==('non_thinking' if effort=='none' else 'thinking')
    assert KEY.encode() not in g.usage_log.path.read_bytes()


def test_all_fixed_educational_efforts_high_except_three():
    assert {p for p,v in PURPOSE_EFFORT.items() if v=='none'}=={'sufficiency','plan_intent','asr_correction'}
    assert PURPOSE_EFFORT['profile'] is None  # real replay quality fallback
    assert all(v=='high' for p,v in PURPOSE_EFFORT.items() if p not in {'profile','sufficiency','plan_intent','asr_correction'})


@pytest.mark.parametrize('code',['MODEL_TIMEOUT','MODEL_CONNECTION_FAILED','MODEL_CANCELLED'])
def test_missing_usage_is_unknown_not_zero(tmp_path,monkeypatch,code):
    g,p=configured(tmp_path)
    def fail(*a,**kw):raise ModelError(code,'固定错误')
    monkeypatch.setattr(providers,'complete',fail)
    with pytest.raises(ModelError):g.complete('profile',MESSAGES)
    for group in ('purpose','model','day'):
        s=g.usage_log.summary(START,END,group,tokens_only=True)
        assert s['total']['unknown_usage_calls']==1
        assert s['groups'][0]['unknown_usage_calls']==1
        assert s['total']['input_tokens'] is None
        assert s['total']['cache_hit_rate'] is None


def test_pre_send_cancel_not_labelled_possibly_billed(tmp_path,monkeypatch):
    g,_=configured(tmp_path);cancel=threading.Event();cancel.set()
    monkeypatch.setattr(providers,'complete',lambda *a,**kw:pytest.fail('must not send'))
    with pytest.raises(ModelError):g.complete('profile',MESSAGES,cancel=cancel)
    s=g.usage_log.summary(START,END,tokens_only=True)['total']
    assert s['cancelled']==1 and s['unknown_usage_calls']==0


def test_partial_stream_subtotals_and_unknown_count_survive_storage(tmp_path):
    log=UsageLog(tmp_path/'partial.sqlite3')
    value=providers.Usage(100,20,80,7,0,0,{'_mentorflow':dict(partial_usage=True,
        unreported_fields=['reasoning_tokens'],request_attempted=True,thinking_blocks=1,thinking_characters=10,
        sent_reasoning_parameters={'thinking':{'type':'enabled'},'output_config':{'effort':'high'},'api_key':KEY})})
    log.record('deepseek','fixture','point_materials',value,error_code='MODEL_CONNECTION_FAILED')
    s=log.summary(START,END)['total']
    assert s['unknown_usage_calls']==s['partial_usage_calls']==1
    assert s['known_tokens']['input_tokens']==100 and s['known_tokens']['output_tokens']==7
    assert s['input_tokens'] is None
    assert KEY.encode() not in log.path.read_bytes()


@pytest.mark.parametrize('mode',['timeout','disconnect','cancel'])
def test_real_interrupted_adapter_journals_partial_meters(tmp_path,monkeypatch,mode):
    from tests.refactor.providers.samples import frame
    g,p=configured(tmp_path)
    cancel=threading.Event()
    class Interrupted(httpx.AsyncByteStream):
        async def __aiter__(self):
            data=anthro(provider='deepseek');data['usage']['output_tokens']=1
            yield frame({'type':'message_start','message':data})
            yield frame({'type':'content_block_start','index':0,'content_block':{'type':'thinking','thinking':''}})
            yield frame({'type':'content_block_delta','index':0,'delta':{'type':'thinking_delta','thinking':'未保存的思考'}})
            if mode=='cancel':
                cancel.set();await asyncio.sleep(.1)
            raise (httpx.ReadTimeout(KEY) if mode=='timeout' else httpx.ReadError(KEY))
    response=httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Interrupted())
    monkeypatch.setattr(providers,'complete',lambda p,**kw:_REAL_COMPLETE(p,http_client=httpx.MockTransport(lambda _:response),**kw))
    with pytest.raises(ModelError):g.complete('point_materials',MESSAGES,on_text=lambda _:None,cancel=cancel)
    s=g.usage_log.summary(START,END)['total']
    assert s['partial_usage_calls']==s['unknown_usage_calls']==1
    assert s['known_tokens']['input_tokens']==150 and s['known_tokens']['output_tokens']==1
    with sqlite3.connect(g.usage_log.path) as db:
        meta=json.loads(db.execute('select usage_json from calls').fetchone()[0])['raw']['_mentorflow']
    assert meta['thinking_blocks']==1 and meta['thinking_characters']==6
    assert meta['sent_reasoning_parameters']['output_config']['effort']=='high'
    assert '未保存的思考'.encode() not in g.usage_log.path.read_bytes()
