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


@pytest.mark.parametrize('answer',[[0],[1]])
def test_quick_cloze_program_immediate_original_and_no_bank_consumption(e,answer):
    v=teach(e)
    assert v['question']['id']=='p0-quick' and v['question']['kind']=='choice'
    calls=list(e.gateway.calls)
    v=act(e,'answer',answer)
    assert e.gateway.calls==calls and not v['question'] and v['step']=='closing'
    assert v['explanation']['text']=='压力相同时，受力面积越小，压强越大。'
    assert v['explanation']['open']==(answer==[1])
    assert all(not q['uses'] for q in e.materials.bank if q['role']=='bank')
    assert e.store.load_mastery(PLAN).module('m0').state=='untested'
    assert v['points'][0]['state']==('weak' if answer==[1] else 'untested')
    # Correct answers do not append an extra teacher summary.
    if answer==[0]:assert v['messages'][-1]['kind']=='verdict'


def test_module_retest_final_allocation_prefers_unused_then_original(e):
    teach(e);act(e,'answer',[0]);act(e,'next')
    v=act(e,'start_test','module_test');original=v['question']['id']
    act(e,'answer',[1])
    v=act(e,'start_test','retest');assert v['question']['id']!=original
    retest=v['question']['id'];act(e,'answer',[1])
    # If all alternatives disappear, retest repeats the most recent question.
    e.materials.bank=[q for q in e.materials.bank if q['id']==retest or q['point_ids']==['p1'] or q['role']=='quick']
    v=act(e,'start_test','retest');assert v['question']['id']==retest and not v['failure']
    act(e,'answer',[0]);act(e,'next_module');act(e,'start_test','skip_test');act(e,'answer',[0]);act(e,'next_module')
    # Restore the original module question and a new final question.
    q=question('p0');e.materials.bank.append(q)
    q2=question('p0',index=9);e.materials.bank.append(q2)
    v=act(e,'start_check')
    assert v['question']['id']=='p0-choice-9'
    assert e.materials.selected[-1][1]=='final'


def test_final_fallback_uses_module_question(e):
    e.materials.bank=[q for q in e.materials.bank if q['role']=='quick' or q['id'].endswith('-0')]
    act(e,'start_test','skip_test');act(e,'answer',[0]);act(e,'next_module')
    act(e,'start_test','skip_test');act(e,'answer',[0]);act(e,'next_module')
    v=act(e,'start_check');assert v['question']['id']=='p0-choice-0' and not v['failure']


def test_final_preparation_background_targets_only_wrong_or_short_bank(e):
    act(e,'start_test','skip_test');act(e,'answer',[0]);act(e,'next_module')
    act(e,'start_test','skip_test');act(e,'answer',[0]);act(e,'next_module')
    e.materials.bank=[q for q in e.materials.bank if not(q['point_ids']==['p0'] and q['id'].endswith('-2'))]
    started=threading.Event();release=threading.Event()
    def find(store,plan,point,ctx):
        started.set()
        while not release.wait(.005):
            if ctx['cancel'].is_set():return
    e.materials.find=find
    v=act(e,'start_check');assert started.wait(1)
    assert v['question'] and v['preparation']['text']=='正在为最终检验准备题目'
    assert v['preparation']['done']==0 and v['preparation']['total']==1
    assert e.materials.point_calls==['p0']
    release.set()


def test_missing_pick_sync_prepare_recovers_with_cancellable_status(e):
    e.materials.bank=[q for q in e.materials.bank if q['point_ids']!=['p0']]
    def find(store,plan,point,ctx):
        assert isinstance(ctx['cancel'],threading.Event)
        e.materials.bank.append(question(point['id']))
    e.materials.find=find
    v=act(e,'start_test','skip_test')
    assert not v['failure'] and v['question']['id']=='p0-choice-0'
    assert e.materials.point_calls==['p0'] and any(s.startswith(MATERIAL_EVENT) for s in e.stream)


def test_missing_point_skips_without_grade_and_next_module_is_available(e):
    e.materials.bank=[q for q in e.materials.bank if q['point_ids']!=['p0']]
    e.store.set_source_adopted(PLAN,'s',False)
    e.room.run(PLAN,lambda co,st,cx:st.update(step='test_offer'))
    v=act(e,'start_test','module_test')
    assert not v['failure'] and not v['question'] and any('出题失败（程序问题，已记录）' in m['content'] for m in v['messages'])
    assert not e.store.records('judgements',PLAN)
    co=e.room._course(PLAN)
    assert e.room._load(co)['question_gaps'][0]['point_id']=='p0'
    assert e.room._load(co)['question_gaps'][0]['reason']=='没有关联的原文段落'
    assert act(e,'next_module')['module']['id']=='m1'


def test_no_quick_still_closes_instead_of_consuming_bank(e):
    teach(e)
    # Keep bank questions but remove quick, then request it from teaching state.
    e.materials.bank=[q for q in e.materials.bank if q['role']=='bank']
    e.room.run(PLAN,lambda co,st,cx:st.update(step='teaching',pending=None))
    v=act(e,'next')
    assert not v['failure'] and v['step']=='closing' and not v['question']
    assert e.materials.point_calls==['p0'] and not any(q['uses'] for q in e.materials.bank)


@pytest.mark.parametrize('phase',['opening','teaching','quick','test'])
def test_three_exit_positions_resume_exact_question_and_no_duplicate_grade(e,phase):
    if phase=='test':v=act(e,'start_test','skip_test')
    else:
        v=act(e,'next')
        if phase!='opening':v=act(e,'choose',0)
        if phase=='quick':v=act(e,'next')
    before=v['question'];messages=copy.deepcopy(v['messages']);step=v['step']
    v=reopen(e)
    assert v['step']==step and v['question']==before and v['messages']==messages
    if phase not in ('opening','teaching'):
        act(e,'answer',[0],token='resume-answer')
        act(e,'answer',[0],token='resume-answer')
        assert len(e.store.records('judgements',PLAN))==1


def test_cancel_sync_prepare_preserves_previous_position(e):
    e.materials.bank=[]
    def find(store,plan,point,ctx):ctx['cancel'].set();raise ModelCancelled()
    e.materials.find=find
    before=e.room._load(e.room._course(PLAN))
    with pytest.raises(BridgeError,match='已停止'):act(e,'start_test','skip_test')
    assert e.room._load(e.room._course(PLAN))==before
    assert not e.store.records('judgements',PLAN)


def test_practice_missing_all_finishes_without_plan_changes(e):
    teach(e);act(e,'answer',[0]);act(e,'next')
    before=e.store.get_plan(PLAN);mastery=e.store.load_mastery(PLAN).to_dict()
    e.materials.bank=[]
    v=act(e,'start_practice','m0',None,2)
    assert not v['failure'] and not v['question'] and '练习已结束' in v['messages'][-1]['content']
    assert e.store.get_plan(PLAN)==before and e.store.load_mastery(PLAN).to_dict()==mastery


def test_final_missing_does_not_hold_up_other_points_or_fabricate_pass(e):
    act(e,'start_test','skip_test');act(e,'answer',[0]);act(e,'next_module')
    act(e,'start_test','skip_test');act(e,'answer',[0]);act(e,'next_module')
    e.materials.bank=[q for q in e.materials.bank if q['point_ids']==['p1']]
    v=act(e,'start_check')
    assert v['question']['id'].startswith('p1') and not v['failure']
    v=act(e,'answer',[0])
    assert not v['failure'] and not v['question'] and v['plan']['state']=='checking'
    assert e.store.load_mastery(PLAN).point('p0').level<3
    assert any('出题失败' in m['content'] for m in v['messages'])


def test_final_prepare_wrong_module_point_even_with_three_bank_questions(e):
    teach(e);act(e,'answer',[0]);act(e,'next');act(e,'start_test','module_test');act(e,'answer',[1])
    act(e,'start_test','retest');act(e,'answer',[0]);act(e,'next_module')
    act(e,'start_test','skip_test');act(e,'answer',[0]);act(e,'next_module')
    act(e,'start_check')
    deadline=time.monotonic()+1
    while not e.materials.point_calls and time.monotonic()<deadline:time.sleep(.005)
    assert e.materials.point_calls==['p0']


def test_background_cancel_stops_final_prep_without_cancelling_question(e):
    act(e,'start_test','skip_test');act(e,'answer',[0]);act(e,'next_module')
    act(e,'start_test','skip_test');act(e,'answer',[0]);act(e,'next_module')
    e.materials.bank=[q for q in e.materials.bank if not q['id'].endswith('-2')]
    started=threading.Event();stopped=threading.Event()
    def find(store,plan,point,ctx):
        started.set();ctx['cancel'].wait(2);stopped.set()
    e.materials.find=find
    v=act(e,'start_check');assert started.wait(1);pending=v['question']
    v=act(e,'stop_preparation');assert stopped.wait(1)
    assert not v['preparation'] and v['question']==pending
