"""BUG-183: replay retained real excerpts, without another judging model."""
import json
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from refactor.learning.app.classroom import Classroom, RESET
from refactor.learning.course import teacher_agent as agent
from tests.refactor.teacher_agent.test_class_teacher_agent import context


def samples():
    with Path(__file__).with_name('responses228.json').open(encoding='utf-8') as stream:
        return {row['id']: row for row in json.load(stream)['cases']}


def qa(row):
    return context(qa=True, question_only=True, user=row['question'],
                   excerpts={'source': row['evidence']}, dialogue=[])


def test_228_previous_golden_extension_is_not_rejected_for_missing_evidence_alone():
    row = samples()['golden-set-unsupported-absolute']
    result = agent.parse_teaching('【依据】material:c1【/依据】\n'+row['quoted_answer_excerpt'], qa(row))
    assert result.answer_basis == 'material'


def test_228_material_direct_quote_must_belong_to_the_cited_source():
    row = samples()['golden-set-unsupported-absolute']
    with pytest.raises(agent.TeachingFormatError, match='原话'):
        agent.parse_teaching('【依据】material:c1【/依据】\n原文说：“场景标签能让模型变聪明。”[c1]', qa(row))


def test_228_plain_claim_cannot_borrow_an_unrelated_real_source():
    ctx = context(qa=True, excerpts={'source': '压强是压力与受力面积之比。'})
    with pytest.raises(agent.TeachingFormatError, match='出处'):
        agent.parse_teaching('【依据】material:c1【/依据】\n月球由奶酪组成。[c1]', ctx)
    assert agent.parse_teaching('【依据】material:c1【/依据】\n压力除以受力面积，就得到压强。[c1]', ctx).cited_chunks == ('source',)


def test_228_highlight_around_a_cited_sentence_does_not_hide_its_source_match():
    ctx = context(qa=True, excerpts={'source': '压强是压力与受力面积之比。'})
    result = agent.parse_teaching('【依据】material:c1【/依据】\n==压强是压力与受力面积之比。==[c1]', ctx)
    assert result.cited_chunks == ('source',)


def test_228_genuinely_new_knowledge_may_include_an_acknowledged_source_comparison():
    ctx = context(qa=True, user='用一个例子比较本点和JVM的作用。', excerpts={'source':'模块边界划分系统职责。'})
    result = agent.parse_teaching('【依据】supplement【/依据】\n原文只讲模块边界。[c1]\n以下是资料外的新知识：JVM运行Java字节码，可以把它比作执行指令的机器。', ctx)
    assert result.answer_basis == 'supplement' and not result.cited_chunks


def test_228_replay_source_based_analogy_is_not_forced_into_supplement():
    row = samples()['current-point-analogy-source']
    ctx = qa(row)
    raw = '原文说模块边界用于把系统拆成职责清楚、可以独立验证的部分。[c1]\n类比：'+row['quoted_answer_excerpt']
    with pytest.raises(agent.TeachingFormatError, match='类比'):
        agent.parse_teaching('【依据】supplement【/依据】\n'+raw, ctx)
    parsed = agent.parse_teaching('【依据】material:c1【/依据】\n'+raw, ctx)
    assert parsed.answer_basis == 'material' and parsed.cited_chunks == ('source',)


def test_228_reasonable_extension_and_supported_absolute_remain_allowed():
    row = samples()['consistency-domain-example']
    raw = '强一致性要求写入后立即读到新值，最终一致性允许短暂延迟。[c1]\n类比：'+row['quoted_answer_excerpt']
    assert agent.parse_teaching('【依据】material:c1【/依据】\n'+raw, qa(row)).answer_basis == 'material'
    ctx = context(qa=True, excerpts={'source': '当前模块只能读取配置，不能修改配置。'})
    parsed = agent.parse_teaching('【依据】material:c1【/依据】\n当前模块只能读取配置，不能修改配置。[c1]', ctx)
    assert '只能读取配置' in parsed.speech


def test_228_previous_business_explanation_is_not_rejected_just_for_extra_detail():
    row = samples()['business-layer-extrapolation']
    result = agent.parse_teaching('【依据】material:c1【/依据】\n'+row['quoted_answer_excerpt'], qa(row))
    assert result.answer_basis == 'material'


def test_228_material_policy_allows_bounded_analogy_and_small_correct_extensions():
    system, _ = agent.teacher_request(qa(samples()['current-point-analogy-source']))
    assert '合理延伸' in system and '类比仍选 material' in system
    assert '不是因为用了例子就选 supplement' in system
    assert '没有写不等于禁止' in system


def test_228_real_replay_rewrites_once_before_committing_final_source():
    row = samples()['golden-set-unsupported-absolute']
    ctx = qa(row)
    replies = iter(['【依据】material:c1【/依据】\n资料写道：“标签能让模型变聪明。”[c1]',
                    '【依据】material:c1【/依据】\n场景标签便于按场景检查模型输出是否覆盖预期行为。[c1]'])
    calls = []
    def complete(purpose, messages, **kwargs):
        calls.append(messages)
        text = next(replies)
        kwargs['on_text'](text)
        return SimpleNamespace(text=text)
    room = object.__new__(Classroom)
    room.services = SimpleNamespace(gateway=SimpleNamespace(complete=complete))
    streamed = []
    result, _, _ = room._generate_teacher('plan', ctx, agent.teacher_request(ctx), threading.Event(), streamed.append)
    assert len(calls) == 2 and RESET in streamed
    assert result.cited_chunks == ('source',)
    assert '能让模型变聪明' not in result.speech
    assert '标签能让模型变聪明' in calls[1][-2]['content']


def live_rows():
    with Path(__file__).with_name('live_responses228.json').open(encoding='utf-8') as stream:
        return json.load(stream)['responses']


@pytest.mark.parametrize('row', live_rows(), ids=lambda row: str(row['call_index']))
def test_228_saved_live_outputs_keep_valid_citations_and_hide_protocol(row):
    # Replay actual responses after the single paid run; this is not a new
    # live success and says nothing about the factual truth of the sentences.
    from tests.refactor.teacher226.live_compare226 import SAMPLES, _context_for
    sample = next(s for s in SAMPLES if s[0] == row['sample_id'])
    result = agent.parse_teaching(row['response'], _context_for(sample, current=True))
    assert '【/依据】' not in result.speech
    if row['sample_id'] == 'plan_material_golden_set':
        assert result.answer_basis == 'material'
        assert set(result.cited_chunks) == {'current', 'test-note'}
    if row['sample_id'] == 'current_point_concept':
        assert result.answer_basis == 'material' and result.cited_chunks == ('current',)


def test_228_rejecting_an_absolute_is_not_itself_an_absolute_claim():
    ctx = qa(samples()['golden-set-unsupported-absolute'])
    result = agent.parse_teaching('【依据】material:c1【/依据】\n场景标签便于按场景检查模型输出是否覆盖预期行为。[c1] 资料没有说标签是唯一检查方式。', ctx)
    assert '没有说' in result.speech


@pytest.mark.parametrize('lead, alias', [('', 'c1'), ('模块边界划分职责。[c1]', 'c2')])
def test_228_analogy_back_reference_needs_the_same_preceding_source(lead, alias):
    ctx = context(qa=True, excerpts={'a': '模块边界划分职责。', 'b': '光合作用需要光照。'})
    with pytest.raises(agent.TeachingFormatError, match='出处'):
        agent.parse_teaching(f'【依据】material:c1【/依据】\n{lead}\n这个类比对应的是原文那两点。[{alias}]', ctx)
