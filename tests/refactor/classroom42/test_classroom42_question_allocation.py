"""Batch 42 contract doubles; real Classroom, mastery, ledger, and persistence."""
import copy
import threading
import time

import pytest

from tests.refactor.conversation37.test_grounded_conversation37 import (
    make_environment, act, question, PLAN, DAY, PARAGRAPH, Materials,
)
from refactor.learning.app.classroom import Classroom, MATERIAL_EVENT
from refactor.learning.app.bridge import BridgeError
from refactor.learning.connections.errors import ModelCancelled


class Materials42(Materials):
    def __init__(self):
        super().__init__()
        self.point_calls=[]
        self.find=None
        for q in self.bank:
            if q['role']=='quick':
                q.update(stem='压力相同时，受力面积越小，压强____。',options=['越大','越小','不变'],answer=[0],explanation='')

    def prepare_point(self,store,plan_id,point,ctx):
        self.point_calls.append(point['id'])
        if self.find:self.find(store,plan_id,point,ctx)
        return dict(point_id=point['id'],questions=sum(point['id'] in q['point_ids'] and q['role']=='bank' for q in self.bank))


@pytest.fixture
def e(tmp_path,monkeypatch):
    env=make_environment(tmp_path)
    old_records=env.store.records
    env.materials=Materials42()
    env.store.records=lambda entity,plan_id='':copy.deepcopy(env.materials.bank) if entity=='question_bank' else old_records(entity,plan_id)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:env.materials))
    yield env
    env.room.cancel_materials()
    for t in threading.enumerate():
        if t.name=='classroom-materials':t.join(3)
    env.store.close()


def teach(e):
    act(e,'next');act(e,'choose',0)
    return act(e,'next')


def reopen(e):
    act(e,'leave')
    e.room=Classroom(e.services,lambda:DAY)
    return act(e,'enter')
















@pytest.mark.parametrize('phase',['opening','teaching','test'])
def test_three_exit_positions_resume_exact_question_and_no_duplicate_grade(e,phase):
    if phase=='test':v=act(e,'start_test','skip_test')
    else:
        v=act(e,'next')
        if phase!='opening':v=act(e,'choose',0)
    before=v['question'];messages=copy.deepcopy(v['messages']);step=v['step']
    v=reopen(e)
    assert v['step']==step and v['question']==before and v['messages']==messages
    if phase not in ('opening','teaching'):
        act(e,'answer',[0],token='resume-answer')
        act(e,'answer',[0],token='resume-answer')
        assert len(e.store.records('judgements',PLAN))==1




def test_practice_missing_all_finishes_without_plan_changes(e):
    teach(e)
    e.materials.bank=[]
    before=e.store.get_plan(PLAN);mastery=e.store.load_mastery(PLAN).to_dict()
    v=act(e,'start_practice','m0',None,2)
    assert not v['failure'] and not v['question'] and '没有题' in v['messages'][-1]['content']
    assert e.store.get_plan(PLAN)==before and e.store.load_mastery(PLAN).to_dict()==mastery
