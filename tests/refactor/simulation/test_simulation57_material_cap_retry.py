"""Recorder must permit the real locator's single candidate-halving retry."""
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import pytest
from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.providers import Usage
from refactor.learning.course import point_materials
from .engine import Evidence, RecordedGateway
from .driver import Driver

ROOT=Path(r'D:\MentorFlow\.local\test-tmp\simulation57-material-retry')


class Prices:
    def estimate_cost(self,*args,**kwargs):
        return SimpleNamespace(high=Decimal('.02'),currency='CNY')
    def cost(self,*args,**kwargs):
        return SimpleNamespace(amount=Decimal('.01'),currency='CNY')




def test_simulation57_other_truncation_still_halts_and_unknown_usage_is_not_free():
    class Real:
        price_catalog=Prices()
        def get_configuration(self):return dict(provider_id='deepseek',model='deepseek-v4-flash')
        def estimate_context(self):return {}
        def complete(self,*args,**kwargs):raise ModelError('MODEL_OUTPUT_TRUNCATED','离线截断样例')
    ROOT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT) as path:
        recorder=RecordedGateway(Real(),Evidence(path),budget='1')
        with pytest.raises(ModelError):recorder.complete('teacher',[])
        assert recorder.halted.is_set()
        assert recorder.spent==Decimal('.02')
        assert 'time_cost_cny' not in recorder.evidence.calls[0]


@pytest.mark.parametrize('worker_timeout',[False,True])
def test_simulation57_preparation_waits_before_poll_and_propagates_worker_timeout(worker_timeout):
    from .engine import StopRun
    calls=[]
    class Sim:
        evidence=SimpleNamespace(event=lambda *a,**k:None)
        clock=SimpleNamespace(now=lambda:SimpleNamespace(isoformat=lambda:'fixture'))
        def join_jobs(self):
            calls.append('wait')
            if worker_timeout:raise StopRun('WORKER_TIMEOUT','classroom-materials')
    user=SimpleNamespace(choose=lambda *a:dict(method='classroom.state',params={}))
    driver=object.__new__(Driver)
    driver.sim,driver.user=Sim(),user
    driver.state=dict(stage='classroom',view=dict(step='preparing_materials'))
    driver.request=lambda method,**params:calls.append(method)
    if worker_timeout:
        with pytest.raises(StopRun) as failure:driver.action()
        assert failure.value.code=='WORKER_TIMEOUT'
        assert calls==['wait']
    else:
        driver.action()
        assert calls==['wait','classroom.state']
