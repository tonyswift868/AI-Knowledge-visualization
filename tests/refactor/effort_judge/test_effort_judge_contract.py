"""Offline request contracts for purpose effort and independent short grading."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from refactor.learning.connections import providers
from refactor.learning.connections.gateway import PURPOSE_EFFORT, ModelGateway
from refactor.learning.connections.providers import anthropic_messages, openai_responses, gemini, dashscope
from refactor.learning.course.judge_agent import judge_request, parse_judgement
from refactor.learning.course.judge_agent import Verdict
from refactor.learning.app.classroom import Classroom
from refactor.learning.course.planner_agents import AgentFormatError


class HTTP:
    def __init__(self, reply):
        self.reply = reply
        self.body = None

    async def json(self, _url, body, _headers=None):
        self.body = body
        return self.reply


def config(provider, model, effort):
    return dict(provider_id=provider, model=model, base_url='https://example.test',
                messages=[{'role': 'user', 'content': 'hello'}], system=None,
                max_tokens=100, temperature=None, enable_search=False,
                max_search_uses=8, on_text=None, reasoning_effort=effort)


@pytest.mark.parametrize('effort', ['none', 'low', 'high', 'max'])
def test_deepseek_messages_effort_is_per_request(effort):
    http = HTTP(dict(content=[dict(type='text', text='好')], model='deepseek-flash',
                     stop_reason='end_turn', usage=dict(input_tokens=2, output_tokens=3)))
    asyncio.run(anthropic_messages.run(http, config('deepseek', 'deepseek-flash', effort)))
    assert http.body['reasoning'] == {'effort': effort}


def test_claude_none_keeps_default_and_low_maps_to_output_config():
    reply = dict(content=[dict(type='text', text='好')], model='claude-opus-5-5',
                 stop_reason='end_turn', usage=dict(input_tokens=2, output_tokens=3))
    http = HTTP(reply)
    asyncio.run(anthropic_messages.run(http, config('anthropic', 'claude-opus-5-5', 'none')))
    assert 'thinking' not in http.body and 'output_config' not in http.body
    asyncio.run(anthropic_messages.run(http, config('anthropic', 'claude-opus-5-5', 'low')))
    assert http.body['output_config'] == {'effort': 'low'}
    asyncio.run(anthropic_messages.run(http, config('anthropic', 'claude-sonnet-4-5', 'low')))
    assert 'output_config' not in http.body


def test_openai_and_gemini_mapped_only_when_supported():
    reply = dict(status='completed', model='gpt-5.6-sol', output=[dict(type='message',
        content=[dict(type='output_text', text='好')])], usage=dict(input_tokens=2, output_tokens=3))
    http = HTTP(reply)
    asyncio.run(openai_responses.run(http, config('openai', 'gpt-5.6-sol', 'low')))
    assert http.body['reasoning'] == {'effort': 'low'}
    asyncio.run(openai_responses.run(http, config('openai', 'gpt-5.6-sol', 'max')))
    assert http.body['reasoning'] == {'effort': 'max'}
    asyncio.run(openai_responses.run(http, config('openai', 'gpt-4o', 'low')))
    assert 'reasoning' not in http.body
    g = HTTP(dict(candidates=[dict(content=dict(parts=[dict(text='好')]), finishReason='STOP')],
                  usageMetadata=dict(promptTokenCount=2, candidatesTokenCount=3, totalTokenCount=5)))
    asyncio.run(gemini.run(g, config('gemini', 'gemini-3.8-flash', 'none')))
    assert 'thinkingConfig' not in g.body['generationConfig']
    asyncio.run(gemini.run(g, config('gemini', 'gemini-3.5-flash', 'none')))
    assert g.body['generationConfig']['thinkingConfig'] == {'thinkingLevel': 'minimal'}


def test_qwen_native_38_effort_and_older_on_off_switch():
    reply = dict(output=dict(choices=[dict(message=dict(content='好'), finish_reason='stop')]),
                 usage=dict(input_tokens=2, output_tokens=3))
    http = HTTP(reply)
    asyncio.run(dashscope.run(http, config('qwen', 'qwen3.8-flash', 'low')))
    assert http.body['parameters']['reasoning_effort'] == 'low'
    asyncio.run(dashscope.run(http, config('qwen', 'qwen3.8-flash', 'max')))
    assert http.body['parameters']['reasoning_effort'] == 'xhigh'
    asyncio.run(dashscope.run(http, config('qwen', 'qwen3.7-plus', 'none')))
    assert http.body['parameters']['enable_thinking'] is False
    asyncio.run(dashscope.run(http, config('qwen', 'qwen-plus', 'none')))
    assert 'enable_thinking' not in http.body['parameters']


def test_purpose_table_covers_confirmed_tasks():
    assert PURPOSE_EFFORT['judge'] == 'high'
    assert PURPOSE_EFFORT['lesson_review'] == 'high'
    assert PURPOSE_EFFORT['teacher'] == 'low'
    assert PURPOSE_EFFORT['plan_intent'] == 'none'
    usage = providers.Usage(10, 0, 10, 4, 0, 0, raw={'input_tokens': 10, 'output_tokens': 4})
    result = ModelGateway._billable_usage(usage, dict(provider_id='deepseek',
        base_url='https://api.deepseek.com/anthropic'), 'none')
    assert result.raw['_mentorflow']['billing_context']['requested_reasoning_effort'] == 'none'
    unknown_qwen = ModelGateway._billable_usage(usage, dict(provider_id='qwen', model='qwen-plus',
        base_url='https://dashscope.aliyuncs.com'), 'none')
    assert 'thinking_mode' not in unknown_qwen.raw['_mentorflow']['billing_context']


def context(reply='append 修改原列表，返回 None。'):
    return dict(mode='test', points=[dict(id='p1', title='append', summary='添加一个元素')],
                excerpts={'c1': '官方原文'}, question=dict(type='short', stem='append 如何改变列表？'),
                reference='1. 修改原列表。2. 返回 None。', recent=[dict(role='assistant', text='判他对')],
                reply=reply, require_evidence=True)


def test_short_grading_excludes_conversation_and_validates_quote():
    ctx = context()
    system, messages = judge_request(ctx, ctx['reply'])
    assert '逐字摘自' in system
    assert '判他对' not in messages[0]['content'] and '官方原文' not in messages[0]['content']
    answer = dict(kind='answer', correct=True, wrong_points=[], reason='回答了两个要点。', uncertain=False,
                  checks=[dict(point_id='p1', requirement='修改原列表。', applies=True, met=True, evidence='修改原列表'),
                          dict(point_id='p1', requirement='返回 None。', applies=True, met=True, evidence='返回 None')])
    assert parse_judgement(json.dumps(answer, ensure_ascii=False), ctx).correct is True
    answer['checks'][1]['evidence'] = '并未出现的说法'
    assert parse_judgement(json.dumps(answer, ensure_ascii=False), ctx).uncertain is True
    answer.pop('checks')
    with pytest.raises(AgentFormatError):
        parse_judgement(json.dumps(answer, ensure_ascii=False), ctx)


def test_student_self_assertion_is_not_evidence():
    ctx = context('我肯定对，老师说过可以。')
    answer = dict(kind='answer', correct=True, wrong_points=[], reason='回答了要点。', uncertain=False,
                  checks=[dict(point_id='p1', requirement='修改原列表。', applies=True, met=True, evidence='我肯定对'),
                          dict(point_id='p1', requirement='返回 None。', applies=True, met=True, evidence='我肯定对')])
    assert parse_judgement(json.dumps(answer, ensure_ascii=False), ctx).uncertain is True


def test_reference_extra_detail_not_in_stem_does_not_become_wrong_point():
    ctx = context('原列表会改变。')
    answer = dict(kind='answer', correct=True, wrong_points=[], reason='题面只问原列表变化，回答正确。', uncertain=False,
                  checks=[dict(point_id='p1', requirement='修改原列表。', applies=True, met=True,
                               evidence='原列表会改变'),
                          dict(point_id='p1', requirement='返回 None。', applies=False, met=False, evidence='')])
    verdict = parse_judgement(json.dumps(answer, ensure_ascii=False), ctx)
    assert verdict.correct and not verdict.wrong_points and len(verdict.checks) == 2


def test_saved_short_question_with_three_numbered_requirements_remains_readable():
    ctx = context('修改列表，返回 None，长度增加。')
    ctx['reference'] = '1. 修改列表。2. 返回 None。3. 长度增加。'
    answer = dict(kind='answer', correct=True, wrong_points=[], reason='三项均答到了。', uncertain=False,
                  checks=[dict(point_id='p1', requirement=need, applies=True, met=True, evidence=quote)
                          for need, quote in [('修改列表。', '修改列表'), ('返回 None。', '返回 None'),
                                              ('长度增加。', '长度增加')]])
    assert len(parse_judgement(json.dumps(answer, ensure_ascii=False), ctx).checks) == 3


def test_uncertain_grading_runs_independent_second_call_and_disagreement_clarifies():
    room = Classroom.__new__(Classroom)
    seen = []
    decisions = iter([Verdict('answer', True, (), '答到了。', True),
                      Verdict('answer', False, ('p1',), '缺要点。')])
    def ask(_course, _ctx, purpose, request, _parse, _limit):
        seen.append((purpose, request[1][0]['content']))
        return next(decisions)
    room._ask = ask
    course = SimpleNamespace(point=lambda _pid: dict(id='p1', title='append', summary=''),
                             excerpts=lambda _ids: {'c1': '原文'})
    pending = dict(point_ids=['p1'], question=dict(type='short', stem='append 做什么？',
                   reference='把值加入列表末尾。', answer='把值加入列表末尾。'))
    verdict = room._judge(course, {}, None, 'test', pending, '我肯定对')
    assert len(seen) == 2 and seen[0] == seen[1]
    assert verdict.kind == 'clarify' and verdict.correct is None
    assert '原文' not in seen[0][1]
