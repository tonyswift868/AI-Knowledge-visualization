"""Only tool controls change; the learning host remains real and unmodified."""
from decimal import Decimal
from types import SimpleNamespace
from .actors import OfflineGateway,ScriptUser,Persona,offline_fetch
from .engine import Clock,Simulation,Evidence,RecordedGateway
from .driver import Driver


def test_simulation53_first_test_pause_then_retest_and_skip_day(tmp_path):
    # Evidence path follows the same D-drive boundary as the existing tool.
    from pathlib import Path
    import tempfile
    root=Path(r'D:\MentorFlow\.local\test-tmp\block53-class\unit');root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as path:
        sim=Simulation(path,Clock(),lambda _:OfflineGateway(),fetcher=offline_fetch,offline=True,timeout=20)
        try:
            first=Driver(sim,ScriptUser(),Persona(),first_module=True,skip_days=(3,))
            summary=first.run(to='classroom')
            assert summary['stop'] is None,summary
            assert first.state['view']['step']=='test_done' and first.state['view']['red']
            second=Driver(sim,ScriptUser(),Persona(),skip_days=(3,))
            summary=second.run(from_stage='classroom')
            assert summary['stop'] is None and summary['stage']=='free',summary
            assert len(sim.services.store.records('practice_results',first.state['plan_id']))==3
            events=(sim.evidence.root/'events.jsonl').read_text(encoding='utf-8')
            assert 'skipped_study_day' in events
            assert (sim.evidence.root/'learning-report.pdf').read_bytes().startswith(b'%PDF-')
        finally:sim.close()


def test_simulation53_actual_price_forecast_avoids_output_cap_reservation(tmp_path):
    from pathlib import Path
    import tempfile
    from refactor.learning.connections.providers import Usage
    class Prices:
        def estimate_cost(self,*args,**kw):return SimpleNamespace(high=Decimal(kw['output_tokens'][1])/100000,currency='CNY')
        def cost(self,*args,**kw):return SimpleNamespace(amount=Decimal('.03'))
    class Real:
        price_catalog=Prices()
        def get_configuration(self):return dict(provider_id='deepseek',model='deepseek-v4-flash')
        def estimate_context(self):return {}
        def complete(self,*a,**kw):return SimpleNamespace(text='ok',usage=Usage(2,0,2,10,0,0,{}))
    root=Path(r'D:\MentorFlow\.local\test-tmp\block53-class\unit');root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as path:
        gw=RecordedGateway(Real(),Evidence(path),budget='.12')
        gw.complete('point_materials',[],system='',max_tokens=24576)
        gw.complete('point_materials',[],system='',max_tokens=24576)
        assert gw.spent==Decimal('.06')
        assert Decimal(gw.evidence.calls[-1]['reservation_cny'])==Decimal('.033')


def test_simulation53_worker_join_uses_requested_deadline_not_fixed_thirty(monkeypatch):
    from . import engine
    calls=[]
    worker=SimpleNamespace(name='plan-materials-fixture',join=lambda timeout:calls.append(timeout),is_alive=lambda:False)
    monkeypatch.setattr(engine.threading,'enumerate',lambda:[worker])
    sim=Simulation.__new__(Simulation);sim.timeout=240
    sim.join_jobs()
    assert calls==[240]


def _simulation53_wait_fixture(path,monkeypatch,progress):
    import json,threading
    from . import engine
    sim=Simulation.__new__(Simulation)
    sim.evidence=Evidence(path);sim.gateway=SimpleNamespace(attempted=0,halted=threading.Event())
    sim.gateway.halt=sim.gateway.halted.set
    sim.clock=Clock();sim.timeout=.5;sim.received=[]
    timer=SimpleNamespace(value=0,ticks=0,request=None,cancelled=False)
    def dispatch(text):timer.request=json.loads(text)
    def cancel():timer.cancelled=True
    def tick():
        timer.ticks+=1;timer.value+=.4
        if progress and timer.ticks in (1,3):sim.gateway.attempted+=1
        if progress and timer.ticks==4:
            sim.gateway.spent=Decimal(0)
            sim.received.append(dict(id=timer.request['id'],type='response',ok=True,result={'ok':True}))
    sim.bridge=SimpleNamespace(call=dispatch,cancel_pending=cancel)
    sim.qapp=SimpleNamespace(processEvents=tick)
    monkeypatch.setattr(engine.time,'monotonic',lambda:timer.value)
    monkeypatch.setattr(engine.time,'sleep',lambda _:None)
    return sim,timer


def test_simulation53_multicall_bridge_progress_extends_wait(monkeypatch):
    from pathlib import Path
    import tempfile
    root=Path(r'D:\MentorFlow\.local\test-tmp\block53-class\unit');root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as path:
        sim,timer=_simulation53_wait_fixture(path,monkeypatch,True)
        assert sim.call('plan.say',text='确认结构')=={'ok':True}
        assert timer.value>sim.timeout and not timer.cancelled


def test_simulation53_inactive_bridge_still_times_out(monkeypatch):
    from pathlib import Path
    import tempfile,pytest
    from .engine import StopRun
    root=Path(r'D:\MentorFlow\.local\test-tmp\block53-class\unit');root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as path:
        sim,timer=_simulation53_wait_fixture(path,monkeypatch,False)
        with pytest.raises(StopRun,match='plan.say'):sim.call('plan.say',text='确认结构')
        assert timer.cancelled and sim.gateway.halted.is_set()


def test_simulation53_cli_totals_include_worker_close_accounting(tmp_path,monkeypatch,capsys):
    import json
    from . import run
    class Sim:
        def __init__(self,*a,**kw):
            self.closed=False;self.gateway=SimpleNamespace(spent=Decimal(0))
            self.evidence=SimpleNamespace(save=lambda *a:None,summary=self.summary)
        def close(self):self.closed=True;self.gateway.spent=Decimal('.1')
        def summary(self,stage,cost):
            assert self.closed
            return dict(stage=stage,stop=None,calls=1,cost_cny=str(cost))
    class D:
        def __init__(self,*a,**kw):pass
        def run(self,**kw):return dict(stage='form',stop=None,calls=0,cost_cny='0')
    monkeypatch.setattr(run,'SCRATCH',tmp_path)
    monkeypatch.setattr(run,'Simulation',Sim);monkeypatch.setattr(run,'Driver',D)
    assert run.main(['--root',str(tmp_path),'--to','form'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['calls']==1 and result['cost_cny']=='0.1'


def test_simulation53_resume_keeps_incomplete_request_evidence(monkeypatch):
    from pathlib import Path
    import tempfile
    root=Path(r'D:\MentorFlow\.local\test-tmp\block53-class\unit');root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as path:
        sim,timer=_simulation53_wait_fixture(path,monkeypatch,True)
        original={'request':{'id':'old','method':'plan.say','params':{'text':'确认结构'}}}
        sim.evidence.save('steps/step-00001.json',original)
        before=(Path(path)/'steps/step-00001.json').read_bytes()
        assert sim.call('plan.get')=={'ok':True}
        assert timer.request['id'].startswith('sim-2-')
        assert (Path(path)/'steps/step-00001.json').read_bytes()==before
        assert (Path(path)/'steps/step-00002.json').exists()
