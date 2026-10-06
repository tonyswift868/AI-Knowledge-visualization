"""Casual understanding stays in dialogue, outside assessment and question use."""
import threading

import pytest

from refactor.learning.app.classroom import Classroom
from tests.refactor.conversation37.test_grounded_conversation37 import make_environment, act, PLAN, DAY


@pytest.fixture
def dialogue(tmp_path, monkeypatch):
    e=make_environment(tmp_path)
    e.materials.bank=[q for q in e.materials.bank if q['role']=='bank']
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    yield e
    e.room.cancel_materials()
    for t in threading.enumerate():
        if t.name=='classroom-materials':t.join(3)
    e.store.close()


@pytest.mark.parametrize('answer',['压力相同，面积小就压强大。','面积小所以压强也小。','压力小，哦我是说面积小。'])
def test_quick70_understanding_reply_has_no_assessment_or_question_use(dialogue,answer):
    e=dialogue
    act(e,'next');act(e,'choose',0)
    before=e.store.load_mastery(PLAN).to_dict()
    v=act(e,'reply',answer)
    assert v['step']=='teaching' and v['question'] is None and not v['failure']
    assert e.gateway.calls==['teacher']*3
    assert e.store.load_mastery(PLAN).to_dict()==before
    assert not e.store.records('judgements',PLAN) and not e.store.records('question_answers',PLAN)
    assert not e.materials.selected and not e.materials.used
    assert all(p['state']=='untested' for p in v['points'])
    assert v['messages'][-2]['content']==answer  # ordinary dialogue still resumes


@pytest.mark.parametrize('action,value',[('next',None),('reply','继续'),('reply','我会了')])
def test_quick70_continue_finishes_learning_without_confirmation(dialogue,action,value):
    e=dialogue
    act(e,'next');act(e,'choose',0)
    calls=list(e.gateway.calls)
    v=act(e,action,*(() if value is None else (value,)))
    assert v['step']=='test_offer' and not v['question'] and e.gateway.calls==calls
    assert [r['id'] for r in e.store.records('credits',PLAN)]==['learn:p0']
    assert not e.store.records('judgements',PLAN) and not e.materials.used


@pytest.mark.parametrize('kind',['casual','quick'])
def test_quick70_old_unanswered_card_reopens_as_dialogue(dialogue,kind):
    e=dialogue
    act(e,'next');act(e,'choose',0)
    state=e.room._file(PLAN).read({})
    state.update(step='answer',pending=dict(kind=kind,question=dict(id='old-quick'),point_ids=['p0']))
    e.room._file(PLAN).write(state)
    e.room=Classroom(e.services,lambda:DAY)
    v=act(e,'enter')
    assert v['step']=='teaching' and not v['question']
    act(e,'reply','我想问一下面积。')
    assert not e.store.records('judgements',PLAN) and not e.store.records('question_answers',PLAN)


def test_quick70_teacher_prompt_contains_dialogue_rules(dialogue):
    captured=[]
    original=dialogue.gateway.complete
    def complete(purpose,messages,**kwargs):
        captured.append(kwargs['system'])
        return original(purpose,messages,**kwargs)
    dialogue.gateway.complete=complete
    act(dialogue,'next');act(dialogue,'choose',0)
    prompt=captured[-1]
    for rule in ['不要每段都问','口误或表达不完整就不纠缠','不说“答对/答错”','不生成判定','学生说“继续”就往下讲']:
        assert rule in prompt
