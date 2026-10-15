"""Adopted pending work must not silently restart; failures retain ledger/messages."""
import copy
import threading

from refactor.learning.app.classroom import Classroom
from refactor.learning.connections.errors import ModelError
from tests.refactor.conversation37.test_grounded_conversation37 import make_environment,act,PLAN
from tests.refactor.speed116.test_classroom_speed116 import prepare,wait_jobs


def test_prefetch_fault134_pending_timeout_reaches_card_once_and_retry_preserves_records(tmp_path,monkeypatch):
    e=make_environment(tmp_path,first_points=2)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    original=e.gateway.complete
    entered=threading.Event();release=threading.Event();calls=[]
    try:
        start=act(e,'next');before=copy.deepcopy(e.store.records('credits',PLAN))
        def queued(*args,**kw):
            calls.append(args[0]);entered.set();assert release.wait(3)
            raise ModelError('MODEL_FIRST_EVENT_TIMEOUT','raw queue timeout')
        monkeypatch.setattr(e.gateway,'complete',queued)
        prepare(e);assert entered.wait(2)
        selected=threading.Event();take=e.room._take_prefetch
        def take134(*args):
            job=take(*args);selected.set();return job
        monkeypatch.setattr(e.room,'_take_prefetch',take134)
        answer={};thread=threading.Thread(target=lambda:answer.update(view=act(e,'choose',0)))
        thread.start();assert selected.wait(2);release.set();thread.join(3);assert not thread.is_alive()
        count=len(calls);view=answer['view']
        assert view['failure']['model_fault']['category']=='busy'
        assert len(calls)==count==4  # only the four speculative jobs; no fifth request after click
        assert view['messages']==start['messages'] and e.store.records('credits',PLAN)==before
        monkeypatch.setattr(e.gateway,'complete',original)
        recovered=act(e,'retry')
        assert not recovered['failure'] and e.store.records('credits',PLAN)==before
        assert len([m for m in recovered['messages'] if m['role']=='user'])==1
    finally:
        release.set();e.room.cancel_materials();e.store.close()


def test_prefetch_fault134_already_failed_gets_exactly_one_fresh_call(tmp_path,monkeypatch):
    e=make_environment(tmp_path);monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    original=e.gateway.complete
    try:
        act(e,'next')
        def failed(*a,**k):raise ModelError('MODEL_FIRST_EVENT_TIMEOUT','queued')
        monkeypatch.setattr(e.gateway,'complete',failed);prepare(e);wait_jobs(e)
        count=len(e.gateway.calls);monkeypatch.setattr(e.gateway,'complete',original)
        assert not act(e,'choose',0)['failure']
        assert len(e.gateway.calls)==count+1
    finally:e.room.cancel_materials();e.store.close()


def test_prefetch_fault134_repeated_user_retry_keeps_original_action(tmp_path,monkeypatch):
    e=make_environment(tmp_path);monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    original=e.gateway.complete
    try:
        act(e,'next')
        def fail(*a,**k):raise ModelError('MODEL_FIRST_EVENT_TIMEOUT','queued')
        monkeypatch.setattr(e.gateway,'complete',fail)
        assert act(e,'choose',0)['failure']['action']=='choose'
        assert act(e,'retry')['failure']['action']=='choose'
        monkeypatch.setattr(e.gateway,'complete',original)
        final=act(e,'retry')
        assert not final['failure'] and len([m for m in final['messages'] if m['role']=='user'])==1
        assert not e.store.records('credits',PLAN)
    finally:e.room.cancel_materials();e.store.close()
