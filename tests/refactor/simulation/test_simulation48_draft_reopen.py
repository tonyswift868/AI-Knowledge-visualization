"""Batch48 probes use saved drafts, without synthetic adoption or product edits."""
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace

import pytest

from .actors import ModelUser, OfflineGateway, Persona, ScriptUser, offline_fetch
from .driver import Driver, controls
from .engine import Clock, Simulation


def test_simulation48_reopen_before_adoption_preserves_sources_and_budget(monkeypatch):
    root=Path(r'D:\MentorFlow\.local\test-tmp\block48-plan\offline-tests')
    root.mkdir(parents=True,exist_ok=True)
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('offline network'))
    with tempfile.TemporaryDirectory(dir=root) as directory:
        sim=Simulation(directory,Clock(),lambda _:OfflineGateway(),fetcher=offline_fetch,offline=True,timeout=20)
        try:
            driver=Driver(sim,ScriptUser(),Persona(level_note='一般，说不清基础'),reopen_before_adopt=True)
            initial=driver.run(to='sources')
            assert initial['stop'] is None,initial
            assert driver.state['view'].get('sources'),driver.state['view']
            result=driver.run(to='map',from_stage='sources')
            assert result['stop'] is None,result
            assert result['stage']=='map'
            assert driver.state['flags'].count('draft_reopen')==1
            assert driver.state['view']['form']['level_note']=='一般，说不清基础'
            steps=[json.loads((Path(directory)/s['evidence']).read_text(encoding='utf-8')) for s in sim.evidence.steps]
            names=[s['request']['method'] for s in steps]
            assert names.count('plan.get')==1
            assert names.index('plan.get')<names.index('plan.decide')
            reopened=steps[names.index('plan.get')]['response']['result']['draft']
            assert reopened['sources'] and all(s['status']=='new' for s in reopened['sources'])
            assert all(s['status']=='adopted' for s in driver.state['view']['sources'])
            ids=[call['id'] for call in sim.evidence.calls]
            assert ids==list(range(1,len(ids)+1))
            assert 'classroom.leave' not in names and 'classroom.enter' not in names
            resumed=Driver(sim,ScriptUser(),Persona(),reopen_before_adopt=True)
            assert resumed.run(to='map',from_stage='map')['stop'] is None
            assert sum(s['method']=='plan.get' for s in sim.evidence.steps)==1
        finally:sim.close()


def test_simulation48_invalid_user_toggle_corrected_once_without_executing_it():
    replies=iter(['{"method":"plan.decide","params":{"card_id":"a","adopt":true}}',
                  '{"method":"plan.outline","params":{}}'])
    requests=[]
    class Gateway:
        def complete(self,purpose,messages,**kwargs):
            requests.append(json.loads(json.dumps(messages)))
            return SimpleNamespace(text=next(replies))
    view={'sources':[{'card_id':'a','status':'adopted'}]}
    assert ModelUser(Gateway(),Persona()).choose(view,controls(view,'sources'),'sources')['method']=='plan.outline'
    assert len(requests)==2 and len(requests[1])==3
    assert '没有执行' in requests[1][-1]['content']
    assert view['sources'][0]['status']=='adopted'


def test_simulation48_invalid_user_twice_stops_and_transport_is_not_retried():
    calls=[]
    class Gateway:
        def complete(self,*a,**k):
            calls.append(1);return SimpleNamespace(text='{"method":"unknown","params":{}}')
    with pytest.raises(ValueError):ModelUser(Gateway(),Persona()).choose({},[{'method':'plan.say'}],'dialogue')
    assert len(calls)==2
    class Disconnected:
        def complete(self,*a,**k):calls.append(1);raise RuntimeError('disconnected')
    with pytest.raises(RuntimeError):ModelUser(Disconnected(),Persona()).choose({},[{'method':'plan.say'}],'dialogue')
    assert len(calls)==3
