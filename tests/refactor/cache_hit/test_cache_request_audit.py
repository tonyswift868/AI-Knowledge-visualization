from copy import deepcopy
import json

import pytest

from refactor.learning.course import judge_agent, plan_intent
from refactor.learning.course.request_layout import dialogue_tail
from tests.refactor.judge_agent.test_class_judge_agent import context as judge_context
from .audit import analyze, lcp
from tests.refactor.integration38.support import block

def test_real_host_capture_uses_only_current_purposes_and_new_question_reference(tmp_path):
    from .scenario import run_scenario
    from refactor.learning.connections.gateway import PURPOSE_EFFORT
    capture=run_scenario(tmp_path)
    assert capture['status']=='completed'
    assert {'teacher','judge','point_materials'} <= {r['purpose'] for r in capture['requests']}
    assert not {'profile','check_profile'} & {r['purpose'] for r in capture['requests']}  # New host writes factual archives locally.
    assert not {'questions','question_solve','question_review','lesson_prep','lesson_review'} & {r['purpose'] for r in capture['requests']}
    assert all(r['purpose'] in PURPOSE_EFFORT for r in capture['requests'])
    rows=[r for r in capture['requests'] if r['purpose']=='judge']
    assert len(rows)>2 and all(block(r['messages'],'【本次判定】')['reference']=='先进先出' for r in rows)
    assert analyze(capture,block=64)['requests']

def test_extended_capture_keeps_free_check_and_repeated_request_prefixes(tmp_path):
    from .scenario import run_scenario
    from .classroom_audit import classroom_only
    capture=run_scenario(tmp_path,extended=True)
    assert capture['practice_start'] is not None and not capture['practice_reply_lost_pending']
    assert sum(r['purpose']=='judge' for r in capture['requests'][capture['practice_start']:])==2
    audit=analyze(classroom_only(capture),block=64)
    assert any(r['prefix_est']>0 for r in audit['requests'])





def test_common_prefix_needs_two_observations_in_endpoint_model_and_rounding_is_explicit():
    requests = [dict(index=i, purpose='test', system='fixed', model='fixture', options={},
                     messages=[dict(role='user', content='Material ' * 300 + last)]) for i,last in enumerate('BCD')]
    report = analyze(dict(requests=requests), block=64)
    rows = report['requests']
    assert rows[0]['prefix_est'] == rows[1]['endpoint_model_est'] == 0
    assert rows[1]['prefix_est'] > 0 and rows[2]['endpoint_model_est'] > 0
    assert all(r['prefix_est'] % 64 == 0 for r in rows)
    assert report['official_block_tokens'] is None
    assert lcp('abc', 'abd') == 2
    with pytest.raises(ValueError):
        analyze(dict(requests=[]), 0)


def test_dialogue_retains_original_rows_then_drops_whole_pages_and_survives_replay():
    rows = [dict(role='user', text=str(i)) for i in range(90)]
    assert dialogue_tail(rows[:40], max_rows=40) == rows[:40]
    tail = dialogue_tail(rows[:41], max_rows=40)
    assert tail == rows[21:41]
    for end in range(42, 62):
        assert dialogue_tail(rows[:end], max_rows=40) == rows[21:end]
    assert dialogue_tail(rows[:62], max_rows=40) == rows[42:62]
    assert dialogue_tail(deepcopy(rows), max_rows=40) == dialogue_tail(rows, max_rows=40)


def test_dialogue_char_cap_drops_a_page_without_rewriting_messages():
    rows = [dict(role='assistant', text=str(i) * 10) for i in range(9)]
    assert dialogue_tail(rows[:3], max_rows=40, max_chars=30) == rows[:3]
    assert dialogue_tail(rows[:4], max_rows=40, max_chars=30) == rows[3:4]
    assert dialogue_tail(rows[:5], max_rows=40, max_chars=30) == rows[3:5]
    assert dialogue_tail([{'text':'x'*31}], max_rows=40, max_chars=30) == []
    assert dialogue_tail([{'text':'x'*25}], max_rows=40, max_chars=30) == [{'text':'x'*25}]


def test_judge_reorder_without_changing_any_field_or_target():
    jctx = judge_context()
    frozen = deepcopy(jctx)
    system, messages = judge_agent.judge_request(jctx, '原样回答')
    assert system == judge_agent.JUDGE_SYSTEM and jctx == frozen
    points, excerpts, question, reference, recent = judge_agent._context(jctx)
    assert block(messages, '【知识点清单】') == [{'id': p['id'], 'title': p['title']} for p in points]
    assert '【资料原文（数据）】' not in messages[0]['content']
    assert '【最近对话（数据）】' not in messages[0]['content']
    assert block(messages, '【本次判定】') == dict(mode=jctx['mode'], question=question, reference=reference, reply='原样回答')
    assert messages[0]['content'].index('"reference"') < messages[0]['content'].index('"reply"')
    changed = deepcopy(jctx)
    changed['points'] = changed['points'][1:]
    other = judge_agent.judge_request(changed, '变化的回答')[1][0]['content']
    material = messages[0]['content'].split('【知识点清单】')[0]
    assert other.startswith(material)


def test_subjective_rubric_precedes_only_the_current_reply_without_changing_fields():
    ctx = judge_context()
    ctx['require_evidence'] = True
    ctx['reply'] = '本次回答'
    _, messages = judge_agent.judge_request(ctx, ctx['reply'])
    content = messages[0]['content']
    task = block(messages, '【本次判定】')
    assert task['reply'] == '本次回答'
    assert task['required_points']
    assert content.index('"required_points"') < content.index('"reply"')


def test_intent_context_values_are_identical_while_date_and_state_move_back():
    ctx = dict(phase='questions', today='2026-10-01', form={'goal':'学习', 'end':'2026-10-09'},
               recent=[dict(role='user',text='原文')], cards=[], sections=[], modules=[], facts={})
    frozen = deepcopy(ctx)
    system, messages = plan_intent.intent_request(ctx, '回答')
    payload = json.loads(messages[0]['content'].split('\n', 1)[1])
    assert payload == dict(ctx=ctx,text='回答') and ctx == frozen and system == plan_intent.INTENT_SYSTEM
    assert list(payload['ctx']) == ['form', 'sections', 'modules', 'cards', 'facts', 'phase', 'recent', 'today']
