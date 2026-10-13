"""One self-rewrite, observable style memory and bounded closing; no reviewer."""
import json

import pytest

from refactor.learning.app.classroom import Classroom
from refactor.learning.course import teacher_agent as agent
from refactor.learning.course.planner_agents import AgentFormatError
from tests.refactor.teacher_agent.test_class_teacher_agent import context
from tests.refactor.concept114.test_concept114_classroom import concept_environment
from tests.refactor.conversation37.test_grounded_conversation37 import act, PLAN


def ctx(**updates):
    concept=dict(title='感知',notes=[dict(text='传感器接收环境信息。',chunk_id='chunk-a')],first_turn=True,
        recent_styles=[],previous_intro_ending=None,wrapping=False)
    concept.update(updates)
    return context(concept=concept)


@pytest.mark.parametrize('word',['只能','一定','完全不能','只有','就得'])
def test_concept115_unsupported_absolute_requires_rewrite(word):
    with pytest.raises(AgentFormatError,match='绝对化'):
        agent.parse_teaching('传感器'+word+'接收声音。',ctx())
    assert agent.parse_teaching('传感器接收环境信息。',ctx()).speech
    assert agent.parse_teaching('这不代表传感器只能接收声音。',ctx()).speech


def test_concept115_supported_qualifier_and_raw_premise_are_retained():
    c=ctx();c['excerpts']['chunk-a']='传感器必须接收输入。如果同一班级学号唯一，才能标识该班学生。'
    assert agent.parse_teaching('传感器必须接收输入。',c).speech
    _,messages=agent.teacher_request(c)
    task=json.loads(messages[-1]['content'].split('\n',1)[1])
    assert '同一班级学号唯一' in task['concept']['source_premises'][0]


def test_concept115_previous_intro_and_lead_are_checked_without_fixed_rotation():
    with pytest.raises(AgentFormatError,match='concept_ending'):
        agent.parse_teaching('感知接收信息。你怎么看？',ctx(previous_intro_ending='question'))
    assert agent.parse_teaching('感知接收信息。这一层先分清输入。',ctx(previous_intro_ending='question')).speech
    with pytest.raises(AgentFormatError,match='concept_opening'):
        agent.parse_teaching('名字和代表你记住了。感知接收输入。',ctx(recent_styles=[dict(lead='名字和代表你记住了。')]))


def test_concept115_closing_is_two_sentences_and_80_characters():
    c=ctx(wrapping=True)
    with pytest.raises(AgentFormatError,match='concept_wrap'):
        agent.parse_teaching('先认识感知。再认识输入。还要认识输出。',c)
    safe=agent.concept_fallback('先认识感知。再认识输入。还要认识输出。',c)
    assert safe=='先认识感知。'
    very_long='感知接收环境信息，'*30+'这就是感知。'
    safe=agent.concept_fallback(very_long,c)
    assert len(safe)<=80 and len(agent._sentences(safe))==1
    assert agent.parse_teaching(safe,c)


def test_concept115_classroom_rewrites_absolute_and_records_cross_subpoint_style(tmp_path,monkeypatch):
    e=concept_environment(tmp_path)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    try:
        e.gateway.replies=['压强一定没有变化。','面积越小压强越大。你觉得呢？']
        view=act(e,'next');assert not view['failure'] and e.gateway.calls==['teacher']*2
        assert '一定' not in view['messages'][-1]['content']
        assert any('绝对化' in m['content'] for m in e.requests[-1][1])
        e.gateway.replies=['单位面积的压力叫压强。你怎么看？','单位面积的压力叫压强。这一层先把定义记清。']
        view=act(e,'next');assert not view['failure']
        assert view['concept']['number']==2 and len(e.gateway.calls)==4
        state=e.store.classroom_position(PLAN)['state']['concept']
        assert e.store.classroom_position(PLAN)['state']['concept_recent_styles']==state['style_history']
        assert [r['ending'] for r in state['intro_endings']]==['question','summary']
        assert state['style_history'][0]['opening']
        task=json.loads(next(m['content'].split('\n',1)[1] for m in reversed(e.requests[-1][1]) if m['content'].startswith('【本步任务】')))
        assert task['concept']['previous_intro_ending']=='question'
        e.room=Classroom(e.services,e.room.study_day,now=e.room.now)
        assert not act(e,'enter')['failure']
        assert e.store.classroom_position(PLAN)['state']['concept']['intro_endings']==state['intro_endings']
    finally:e.room.cancel_materials();e.store.close()


def test_concept115_second_long_closing_uses_first_sentence_without_third_call(tmp_path,monkeypatch):
    e=concept_environment(tmp_path)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    try:
        act(e,'next');act(e,'next');act(e,'next')
        before=len(e.gateway.calls)
        e.gateway.replies=['先看面积。再看定义。最后看公式。']*2
        view=act(e,'next')
        assert not view['failure'] and len(e.gateway.calls)==before+2
        assert view['messages'][-1]['content'].startswith('先看面积。')
        assert '再看定义' not in view['messages'][-1]['content']
        assert e.store.records('credits',PLAN)[0]['credit']['minutes']==15
    finally:e.room.cancel_materials();e.store.close()


def test_concept115_legacy_parser_does_not_gain_concept_guards():
    assert agent.parse_teaching('这里一定要先看环境信息。',context()).speech
