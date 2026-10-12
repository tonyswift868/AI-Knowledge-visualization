"""BUG-84: meaningful opening options and presentation-only source footers."""
import json

import pytest

from refactor.learning.course import teacher_agent as agent
from tests.refactor.teacher_agent.test_class_teacher_agent import context
from tests.refactor.conversation37.test_grounded_conversation37 import env, act, PLAN


def test_open108_matching_options_and_one_click_skip_survive_reopen(env):
    env.gateway.replies=['面积变小时，你觉得下面哪种说法最贴近？\n【选项】\n压强增大\n压强减小\n【/选项】']
    view=act(env,'next')
    assert view['conversation']['options']==['压强增大','压强减小','直接开始讲']
    act(env,'leave')
    assert act(env,'enter')['conversation']['options']==view['conversation']['options']
    assert act(env,'choose',2)['step']=='teaching'
    assert not env.store.records('judgements',PLAN)


@pytest.mark.parametrize('tail',['','\n【选项】\n只有一项\n【/选项】','\n【选项】\n没有结束'])
def test_open108_missing_options_removes_dangling_question(tail):
    result=agent.parse_teaching('我们先认识感知。那我先听听你的直觉：按原文，你觉得下面哪种说法最贴近？'+tail,context(opening=True))
    assert result.speech=='我们先认识感知。'
    assert result.options==('直接开始讲','我先说说我知道的')
    assert agent.parse_teaching('你觉得下面哪个更合适？',context(opening=True)).speech


def test_open108_task_requests_answers_instead_of_fixed_controls():
    system,messages=agent.teacher_request(context(opening=True))
    task=json.loads(messages[-1]['content'].split('\n',1)[1])
    assert '与这个问题配套' in task['output_format']
    assert '和问题配套' in system and '程序另提供' in system


def test_open108_copied_source_footer_displayed_once_and_never_sent_back(env):
    env.gateway.replies=['先看面积。\n看原文：[〔1〕](mf-source:c0)\n看原文：[〔1〕](mf-source:c0)',
                         '面积变小，压强增大。[c1]']
    captured=[];complete=env.gateway.complete
    def record(purpose,messages,**kwargs):
        captured.append(messages)
        return complete(purpose,messages,**kwargs)
    env.gateway.complete=record
    view=act(env,'next')
    displayed=view['messages'][-1]['content']
    assert displayed.count('看原文：')==1 and displayed.count('mf-source:c0')==1
    act(env,'choose',0)
    assert 'mf-source:' not in json.dumps(captured[1],ensure_ascii=False)
    assert '看原文：' not in json.dumps(captured[1],ensure_ascii=False)
    # Presentation cleanup must never rewrite the stored historical message.
    assert env.store.messages(PLAN,'m0')[0]['content']==displayed


def test_open108_inline_source_links_convert_or_disappear():
    result=agent.parse_teaching('感知[〔1〕](mf-source:chunk-a)，未知[〔9〕](mf-source:other)。',context())
    assert result.speech=='感知[c1]，未知。'
    assert result.cited_chunks==('chunk-a',)
    _,messages=agent.teacher_request(context(dialogue=[dict(role='assistant',text='感知[〔1〕](mf-source:chunk-a)。\n\n看原文：[〔1〕](mf-source:chunk-a)')]))
    history=next(m['content'] for m in messages if m['content'].startswith('【对话原文'))
    assert 'mf-source:' not in history and '看原文' not in history and '感知。' in history
