"""Progressing multi-call workers must not inherit a whole-job deadline."""
from types import SimpleNamespace
import pytest
from tests.refactor.simulation import engine


def make_worker(monkeypatch, *, progressing):
    sim=engine.Simulation.__new__(engine.Simulation)
    sim.timeout=1.5
    sim.gateway=SimpleNamespace(attempted=0)
    sim.evidence=SimpleNamespace(calls=[])
    clock=[0.0]
    class Worker:
        name='plan-materials'
        ticks=0
        def is_alive(self):return self.ticks<4
        def join(self,timeout):
            assert timeout<=.1
            self.ticks+=1;clock[0]+=1
            if progressing:sim.gateway.attempted+=1
    worker=Worker()
    monkeypatch.setattr(engine.threading,'enumerate',lambda:[worker])
    monkeypatch.setattr(engine.time,'monotonic',lambda:clock[0])
    return sim,worker


def test_simulation54_join_allows_long_worker_with_model_progress(monkeypatch):
    sim,worker=make_worker(monkeypatch,progressing=True)
    sim.join_jobs()
    assert worker.ticks==4


def test_simulation54_join_still_stops_inactive_worker(monkeypatch):
    sim,worker=make_worker(monkeypatch,progressing=False)
    with pytest.raises(engine.StopRun,match='plan-materials') as caught:
        sim.join_jobs()
    assert caught.value.code=='WORKER_TIMEOUT'
    assert worker.ticks==2
