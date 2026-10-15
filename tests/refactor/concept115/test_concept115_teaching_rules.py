"""Observable style memory remains guidance, not a teacher rejection gate."""
import json

import pytest

from refactor.learning.app.classroom import Classroom
from refactor.learning.course import teacher_agent as agent
from tests.refactor.teacher_agent.test_class_teacher_agent import context
from tests.refactor.concept114.test_concept114_classroom import concept_environment
from tests.refactor.conversation37.test_grounded_conversation37 import act, PLAN


def ctx(**updates):
    concept=dict(title='感知',notes=[dict(text='传感器接收环境信息。',chunk_id='chunk-a')],first_turn=True,
        recent_styles=[],previous_intro_ending=None,wrapping=False)
    concept.update(updates)
    return context(concept=concept)


@pytest.mark.parametrize('word',['只能','一定','完全不能','只有','就得'])
def test_concept115_absolute_words_are_allowed(word):
    # User 2026-10-15: absolute wording is no longer rejected (it wasted rewrites).
    assert agent.parse_teaching('传感器'+word+'接收声音。',ctx()).speech
    assert agent.parse_teaching('传感器接收环境信息。',ctx()).speech


def test_concept115_supported_qualifier_and_raw_premise_are_retained():
    c=ctx();c['excerpts']['chunk-a']='传感器必须接收输入。如果同一班级学号唯一，才能标识该班学生。'
    assert agent.parse_teaching('传感器必须接收输入。',c).speech
    _,messages=agent.teacher_request(c)
    task=json.loads(messages[-1]['content'].split('\n',1)[1])
    assert '同一班级学号唯一' in task['concept']['source_premises'][0]


def test_concept115_previous_intro_and_lead_are_accepted_without_rejection():
    # Style memory remains prompt context, but a repeated ending is not a
    # reason to discard an otherwise usable teacher reply.
    assert agent.parse_teaching('感知接收信息。你怎么看？',ctx(previous_intro_ending='question')).speech
    assert agent.parse_teaching('感知接收信息。这一层先分清输入。',ctx(previous_intro_ending='question')).speech
    assert agent.parse_teaching('名字和代表你记住了。感知接收输入。',ctx(recent_styles=[dict(lead='名字和代表你记住了。')])).speech


def test_concept115_closing_style_is_kept_without_rejection():
    c=ctx(wrapping=True)
    # Closing style/length is a preference; preserve it as written.
    assert agent.parse_teaching('先认识感知。再认识输入。还要认识输出。',c).speech=='先认识感知。再认识输入。还要认识输出。'
    safe=agent.concept_fallback('先认识感知。再认识输入。还要认识输出。',c)
    assert safe=='先认识感知。再认识输入。还要认识输出。'
    very_long='感知接收环境信息，'*30+'这就是感知。'
    safe=agent.concept_fallback(very_long,c)
    assert safe==very_long
    assert agent.parse_teaching(safe,dict(c,concept=dict(c['concept'],rewrite_exhausted=True)))


def test_concept115_classroom_keeps_absolute_and_records_cross_subpoint_style(tmp_path,monkeypatch):
    e=concept_environment(tmp_path)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    try:
        e.gateway.replies=['==压强一定没有变化。==你觉得呢？','==面积越小压强越大。==你觉得呢？']
        view=act(e,'next');assert not view['failure'] and e.gateway.calls==['teacher']
        assert '一定' in view['messages'][-1]['content']
        e.gateway.replies=['==单位面积的压力叫压强。==你怎么看？','==单位面积的压力叫压强。==这一层先把定义记清。']
        view=act(e,'next');assert not view['failure']
        # Repeating a previous ending is a style preference, so this draft
        # reaches the learner in its first model call.
        assert view['concept']['number']==2 and len(e.gateway.calls)==2
        state=e.store.classroom_position(PLAN)['state']['concept']
        assert e.store.classroom_position(PLAN)['state']['concept_recent_styles']==state['style_history']
        assert [r['ending'] for r in state['intro_endings']]==['question','question']
        assert state['style_history'][0]['opening']
        task=json.loads(next(m['content'].split('\n',1)[1] for m in reversed(e.requests[-1][1]) if m['content'].startswith('【本步任务】')))
        assert task['concept']['previous_intro_ending']=='question'
        e.room=Classroom(e.services,e.room.study_day,now=e.room.now)
        assert not act(e,'enter')['failure']
        assert e.store.classroom_position(PLAN)['state']['concept']['intro_endings']==state['intro_endings']
    finally:e.room.cancel_materials();e.store.close()


def test_concept115_style_closing_kept_whole_without_rewrite(tmp_path,monkeypatch):
    e=concept_environment(tmp_path)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    try:
        act(e,'next');act(e,'next');act(e,'next')
        before=len(e.gateway.calls)
        e.gateway.replies=['先看面积。再看定义。最后看公式。']*2
        course=e.room._course(PLAN);state=e.room._load(course);state['concept']['wrapping']=True
        teaching=e.room._teacher(course,state,None,'用一两句收束。',['p0'])
        assert '先看面积。再看定义。最后看公式。' in teaching.speech
        view=act(e,'next')
        assert not view['failure'] and len(e.gateway.calls)==before+1
        assert view['messages'][-1]['kind']=='recap'
        assert e.store.records('credits',PLAN)[0]['credit']['minutes']==15
    finally:e.room.cancel_materials();e.store.close()


def test_concept115_legacy_parser_does_not_gain_concept_guards():
    assert agent.parse_teaching('这里一定要先看环境信息。',context()).speech
