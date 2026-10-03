"""Offline default tests; live use is CLI-only and requires a task budget."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import tempfile
import threading

import pytest

from .actors import Persona,ScriptUser,ModelUser,OfflineGateway,offline_fetch
from .driver import Driver
from .engine import Clock,Evidence,RecordedGateway,Simulation,StopRun

ROOT=Path(r'D:\MentorFlow\.local\test-tmp\fix42-sim\offline-tests')


@pytest.fixture
def run_root():
    ROOT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT) as d:yield Path(d)


@pytest.fixture
def simulation(run_root,monkeypatch):
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('离线模拟企图联网'))
    sim=Simulation(run_root,Clock(),lambda _:OfflineGateway(),fetcher=offline_fetch,offline=True,timeout=20)
    try:yield sim
    finally:sim.close()


def test_simulation42_full_bridge_flow_from_dialogue_to_pdf_and_free(simulation):
    driver=Driver(simulation,ScriptUser(),Persona())
    summary=driver.run()
    assert summary['stop'] is None,summary
    assert summary['stage']=='free'
    assert summary['cost_cny']=='0'
    store=simulation.services.store;pid=driver.state['plan_id']
    assert store.get_plan(pid)['state']=='completed'
    assert len(store.records('practice_results',pid))==3
    assert (simulation.evidence.root/'learning-report.pdf').read_bytes().startswith(b'%PDF-')
    assert (simulation.evidence.root/'learning-report.md').read_text(encoding='utf-8').startswith('# 学习报告')
    methods=[s['method'] for s in simulation.evidence.steps]
    assert methods[0:2]==['plan.current','plan.submit']
    assert methods.count('plan.say')>=2
    assert methods.index('plan.say')<methods.index('plan.decide')<methods.index('plan.outline')<methods.index('plan.generate')
    assert 'classroom.ask' in methods and 'classroom.reply' in methods
    assert 'classroom.leave' in methods and 'reopen' in driver.state['flags']
    purposes={u['purpose'] for q in store.records('question_bank',pid) for u in q['uses']}
    assert purposes=={'quick','module','direct','retest','final','free'}
    dates={r['credit']['day'] for r in store.records('credits',pid)}
    assert len(dates)>=2
    at=[datetime.fromisoformat(r['at']) for r in store.records('messages',pid)]
    assert at and {t.date() for t in at} <= {driver.sim.clock.day()-timedelta(days=i) for i in range(10)}
    assert 'quality' in summary and '不自动' in summary['quality']
    assert any(c['purpose']=='research' for c in simulation.evidence.calls)
    assert not any(c['purpose'] in ('questions','question_review','question_solve') for c in simulation.evidence.calls)


def test_simulation42_map_checkpoint_then_resume_same_store(simulation):
    first=Driver(simulation,ScriptUser(),Persona())
    result=first.run(to='map')
    assert result['stop'] is None,result
    pid=first.state['plan_id'];assert first.state['view']['outline']['grounded']
    assert simulation.services.store.get_plan(pid)['state']!='active'
    second=Driver(simulation,ScriptUser(),Persona())
    result=second.run(from_stage='map')
    assert result['stop'] is None,result
    assert second.state['plan_id']==pid and simulation.services.store.get_plan(pid)['state']=='completed'


def test_simulation42_only_day_three_requires_created_checkpoint(simulation):
    driver=Driver(simulation,ScriptUser(),Persona())
    assert driver.run(to='generated')['stop'] is None
    result=driver.run(to='classroom',from_stage='generated',day=3)
    assert result['stop'] is None,result
    assert simulation.clock.day().isoformat()=='2026-10-05'
    assert simulation.services.store.get_plan(driver.state['plan_id'])['state']=='active'


def test_simulation42_stuck_does_not_skip_or_invent_materials(simulation):
    class Repeating:
        def choose(self,*args):return dict(method='classroom.ask',params={'text':'重复这个问题'})
    driver=Driver(simulation,ScriptUser(),Persona(),stuck_limit=2)
    assert driver.run(to='generated')['stop'] is None
    driver.request('plan.start');driver.state['stage']='classroom';driver.request('classroom.enter');driver.save()
    driver.user=Repeating()
    result=driver.run(to='classroom')
    assert result['stop']['code']=='STUCK'
    assert simulation.gateway.halted.is_set()
    assert simulation.services.store.get_plan(driver.state['plan_id'])['state']=='active'
    assert (simulation.evidence.root/'issues.json').exists()


def test_simulation42_bridge_error_stops_and_preserves_evidence(simulation):
    with pytest.raises(StopRun):simulation.call('plan.submit',plan_id='bad',form={})
    assert simulation.gateway.halted.is_set()
    last=json.loads((simulation.evidence.root/'steps/step-00001.json').read_text(encoding='utf-8'))
    assert not last['response']['ok'] and last['request']['method']=='plan.submit'


def test_simulation42_refresh_day_and_now_do_not_change_real_billing_clock():
    real_before=datetime.now(timezone.utc)
    clock=Clock('2026-10-04T03:59:00+09:00')
    assert clock.day().isoformat()=='2026-10-03'
    clock.advance(days=0,minutes=1);assert clock.day().isoformat()=='2026-10-04'
    clock.advance();assert clock.day().isoformat()=='2026-10-05'
    assert datetime.now(timezone.utc)-real_before<timedelta(seconds=1)
    with pytest.raises(ValueError):clock.advance(-1)


def test_simulation42_model_user_sees_only_public_screen_and_persona():
    class Gateway:
        def complete(self,purpose,messages,**kwargs):
            assert purpose=='simulation_user'
            data=json.loads(messages[-1]['content'])
            assert data['screen']=={'question':{'kind':'choice','stem':'选择顺序','options':['一','二']}}
            assert 'answer' not in data['screen']['question']
            assert data['persona']['wrong_ratio']==.2
            return SimpleNamespace(text='{"method":"classroom.answer","params":{"value":[1]}}')
    result=ModelUser(Gateway(),Persona()).choose({'question':{'kind':'choice','stem':'选择顺序','options':['一','二']}},
        [dict(method='classroom.answer',params='value')],'classroom')
    assert result['params']['value']==[1]


def test_simulation42_budget_reserves_before_call_and_failed_usage_not_free(run_root):
    from refactor.learning.connections.providers import Usage
    from refactor.learning.connections.errors import ModelError
    class Catalog:
        def estimate_cost(self,*args,**kwargs):return SimpleNamespace(high=Decimal('.2'),currency='CNY')
        def cost(self,*args,**kwargs):return SimpleNamespace(amount=Decimal('.1'))
    class Real:
        price_catalog=Catalog();calls=0
        def get_configuration(self):return dict(provider_id='deepseek',model='deepseek-v4-flash')
        def estimate_context(self):return {}
        def complete(self,*args,**kwargs):
            self.calls+=1;error=ModelError('DISCONNECT','断线');error.usage=Usage(1,0,1,1,0,0,{});raise error
    real=Real();gw=RecordedGateway(real,Evidence(run_root),budget='.3')
    with pytest.raises(ModelError):gw.complete('sufficiency',[],system='',max_tokens=8)
    assert gw.spent==Decimal('.1') and gw.halted.is_set()
    with pytest.raises(ModelError):gw.complete('sufficiency',[],system='',max_tokens=8)
    assert real.calls==1 and gw.evidence.calls[0]['error']['code']=='DISCONNECT'
    clean=RecordedGateway(real,Evidence(run_root/'other'),budget='.1')
    with pytest.raises(ModelError,match='上限'):clean.complete('sufficiency',[],system='',max_tokens=8)
    assert real.calls==1


def test_simulation42_account_and_segment_do_not_overwrite_missing_checkpoint(simulation):
    driver=Driver(simulation,ScriptUser(),Persona())
    result=driver.run(from_stage='classroom',day=3)
    assert result['stop']['code']=='CHECKPOINT_STAGE'
    assert not simulation.services.store.list_plans()


def test_simulation42_recorder_ids_unique_after_resume(run_root):
    ev=Evidence(run_root);gw=RecordedGateway(OfflineGateway(),ev,offline=True)
    gw.complete('sufficiency',[])
    new=RecordedGateway(OfflineGateway(),Evidence(run_root),offline=True);new.complete('sufficiency',[])
    assert [r['id'] for r in new.evidence.calls]==[1,2]


def test_simulation42_sources_segment_stops_before_adoption(simulation):
    driver=Driver(simulation,ScriptUser(),Persona())
    result=driver.run(to='sources')
    assert not result['stop'] and result['stage']=='sources'
    assert driver.state['view']['sources']
    assert 'plan.decide' not in [s['method'] for s in simulation.evidence.steps]


def test_simulation42_reopen_rebinds_independent_model_user_gateway(simulation):
    driver=Driver(simulation,ScriptUser(),Persona())
    assert not driver.run(to='generated')['stop']
    driver.request('plan.start');driver.state['stage']='classroom';driver.request('classroom.enter')
    class ModelActor:
        gateway=simulation.gateway
        def choose(self,*args):return dict(method='simulation.reopen',params={})
    actor=ModelActor();previous=actor.gateway;driver.user=actor;driver.action()
    assert actor.gateway is simulation.gateway and actor.gateway is not previous
    assert previous.halted.is_set() and not actor.gateway.halted.is_set()


def test_simulation42_clarification_has_no_default_three_round_limit(simulation):
    simulation.gateway.real.clarification_rounds=8
    driver=Driver(simulation,ScriptUser(),Persona())
    result=driver.run(to='sources')
    assert result['stop'] is None,result
    assert driver.state['rounds']==8
    assert sum(s['method']=='plan.say' for s in simulation.evidence.steps)==7


def test_simulation42_script_resume_consumes_remaining_fixed_replies(simulation):
    script=[dict(method='plan.say',params=dict(text='固定第一句')),dict(method='plan.say',params=dict(text='固定第二句'))]
    first=Driver(simulation,ScriptUser(script),Persona())
    assert not first.run(to='dialogue',rounds=2)['stop']
    second=Driver(simulation,ScriptUser(script),Persona())
    assert not second.run(to='sources')['stop']
    user_texts=[m['text'] for m in second.state['view']['messages'] if m['kind']=='user_text']
    assert user_texts==['固定第一句','固定第二句']
