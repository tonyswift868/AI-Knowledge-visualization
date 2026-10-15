"""Recorder preserves the real host's bounded retry, without making live calls."""
from pathlib import Path
import tempfile

import pytest

from refactor.learning.connections.errors import ModelError
import json
from types import SimpleNamespace
from .actors import ModelUser, OfflineGateway, Persona, ScriptUser, offline_fetch
from .driver import Driver, controls
from .engine import Clock, Evidence, RecordedGateway, Simulation


@pytest.fixture
def recorder():
    root=Path(r'D:\MentorFlow\.local\test-tmp\fix51-plan\offline-tests')
    root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        gateway=RecordedGateway(OfflineGateway(),Evidence(directory),offline=True)
        yield gateway
        gateway.close()


def test_simulation45_host_gets_one_protocol_retry_and_failure_is_retained(recorder):
    original=recorder.real.complete
    attempts=[]
    def complete(*args,**kwargs):
        attempts.append(args)
        if len(attempts)==1:
            exc=ModelError('MODEL_RESPONSE_INVALID','协议失败')
            exc.diagnostics={'http_status':200,'stage':'validating_anthropic_response'}
            raise exc
        return original(*args,**kwargs)
    recorder.real.complete=complete
    with pytest.raises(ModelError):recorder.complete('sufficiency',[])
    assert len(attempts)==1  # recorder did not retry on its own
    assert not recorder.halted.is_set()
    recorder.complete('sufficiency',[])
    assert len(attempts)==2
    assert len(recorder.evidence.calls)==2
    assert recorder.evidence.calls[0]['host_protocol_retry_allowed']
    assert recorder.evidence.calls[0]['diagnostics']['http_status']==200


@pytest.mark.parametrize('purpose,code,allowed',[
    ('research','MODEL_RESPONSE_INVALID',True),
    ('simulation_user','MODEL_RESPONSE_INVALID',False),
    ('research','MODEL_CONNECTION_FAILED',False),
    ('research','MODEL_TIMEOUT',False),
])
def test_simulation45_second_failure_or_transport_stops(recorder,purpose,code,allowed):
    calls=[]
    def fail(*args,**kwargs):
        calls.append(args)
        raise ModelError(code,'失败')
    recorder.real.complete=fail
    with pytest.raises(ModelError):recorder.complete(purpose,[])
    assert recorder.halted.is_set() is not allowed
    if allowed:
        with pytest.raises(ModelError):recorder.complete(purpose,[])
        assert recorder.halted.is_set()
    with pytest.raises(ModelError,match='止损'):recorder.complete(purpose,[])
    assert len(calls)==(2 if allowed else 1)


def test_simulation45_bridge_reports_real_protocol_failure_after_host_retry(recorder):
    real=OfflineGateway()
    original=real.complete
    def complete(purpose,*args,**kwargs):
        if purpose=='research':raise ModelError('MODEL_RESPONSE_INVALID','原始协议失败')
        return original(purpose,*args,**kwargs)
    real.complete=complete
    sim=Simulation(recorder.evidence.root/'bridge',Clock(),lambda _:real,
                   fetcher=offline_fetch,offline=True,timeout=20)
    try:
        summary=Driver(sim,ScriptUser(),Persona()).run(to='sources')
        assert summary['stop']['code']=='BRIDGE_FAILED'
        assert '软件出了点问题，没拿到回复' in summary['stop']['message']
        assert '原始协议失败' not in summary['stop']['message']
        assert '模拟已止损' not in summary['stop']['message']
        assert sum(c['purpose']=='research' for c in sim.evidence.calls)==2
        assert sim.gateway.halted.is_set()
    finally:sim.close()


def test_simulation45_user_sees_cards_and_chat_without_invisible_research_audit():
    class Gateway:
        def complete(self,purpose,messages,**kwargs):
            data=json.loads(messages[0]['content'])
            screen=data['screen']
            assert 'research' not in screen
            assert screen['messages']==[{'text':'已找到资料，请选。'}]
            card=screen['sources'][0]
            assert card['card_id']=='a' and card['quality']['note']=='适合入门'
            assert 'evidence' not in card['quality'] and 'context' not in card['quality']
            assert len(messages[0]['content'])<3000
            return SimpleNamespace(text='{"method":"plan.decide","params":{"card_id":"a","adopt":true}}')
    view={'messages':[{'text':'已找到资料，请选。'}], 'research':{'quality_audit':'x'*300000},
          'sources':[{'card_id':'a','title':'官方教程','quality':{'note':'适合入门','credibility':5,
                      'evidence':{'excerpt':'x'*10000},'context':{'goal':'内部审计'}}}]}
    original=json.dumps(view)
    action=ModelUser(Gateway(),Persona()).choose(view,[{'method':'plan.decide'}],'sources')
    assert action['params']['card_id']=='a'
    assert json.dumps(view)==original  # evidence/public bridge response intact


def test_simulation45_source_actions_match_checkbox_and_map_button():
    cards=[{'card_id':'a','status':'new'},{'card_id':'b','status':'adopted'}]
    actions=controls({'sources':cards},'sources')
    description=next(a['params'] for a in actions if a['method']=='plan.decide')
    assert '不是数组' in description
    assert json.loads(description.split('可选组合：')[1])==[
        {'card_id':'a','adopt':True},{'card_id':'b','adopt':False}]
    assert any(a['method']=='plan.outline' for a in actions)
    assert not any(a['method']=='plan.outline' for a in controls({'sources':cards[:1]},'sources'))
