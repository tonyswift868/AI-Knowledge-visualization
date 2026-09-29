from copy import deepcopy
import json

import pytest

from refactor.learning.course import judge_agent, question_agent, plan_intent
from refactor.learning.course.request_layout import dialogue_tail
from tests.refactor.judge_agent.test_class_judge_agent import context as judge_context
from tests.refactor.question_agent.test_class_question_agent import context as question_context
from .audit import analyze, lcp
from .scenario import block, run_scenario


def test_fixed_host_scenario_is_deterministic_and_covers_all_phases(tmp_path):
    before = run_scenario(tmp_path / 'a')
    after = run_scenario(tmp_path / 'b')
    assert before == after
    assert before['status'] == 'completed' and before['requests']
    purposes = {r['purpose'] for r in before['requests']}
    # Source scoring is only called when rules cannot decide (PLAN-06), lesson prep adds its own calls (TEACH-08).
    assert {'sufficiency', 'plan_intent', 'research', 'outline', 'generate',
            'teacher', 'judge', 'questions', 'profile', 'check_profile'} <= purposes
    report = analyze(before)
    assert report['total']['prefix_rate'] < .95 and report['total']['cold_ceiling_est'] < .95
    assert not report['paid_gate'] and report['official_block_tokens'] is None
    assert report['total']['input_est'] == sum(v['input_est'] for v in report['purposes'].values())


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


def test_judge_and_question_reorder_without_changing_any_field_or_target():
    jctx = judge_context()
    frozen = deepcopy(jctx)
    system, messages = judge_agent.judge_request(jctx, '原样回答')
    assert system == judge_agent.JUDGE_SYSTEM and jctx == frozen
    points, excerpts, question, reference, recent = judge_agent._context(jctx)
    assert block(messages, '【知识点清单】') == [{'id': p['id'], 'title': p['title']} for p in points]
    assert '【资料原文（数据）】' not in messages[0]['content']
    assert '【最近对话（数据）】' not in messages[0]['content']
    assert block(messages, '【本次判定】') == dict(mode=jctx['mode'], question=question, reference=reference, reply='原样回答')
    changed = deepcopy(jctx)
    changed['points'] = changed['points'][1:]
    other = judge_agent.judge_request(changed, '变化的回答')[1][0]['content']
    material = messages[0]['content'].split('【知识点清单】')[0]
    assert other.startswith(material)
    qctx = question_context()
    frozen = deepcopy(qctx)
    system, messages = question_agent.question_request(qctx)
    points, excerpts, avoid, _ = question_agent._context(qctx)
    assert qctx == frozen and system == question_agent.QUESTION_SYSTEM
    assert block(messages, '【知识点清单】') == points
    assert block(messages, '【资料原文（数据）】') == excerpts
    assert block(messages, '【本次出题要求】') == dict(per_point=qctx['per_point'], purpose=qctx['purpose'], avoid=avoid)


def test_intent_context_values_are_identical_while_date_and_state_move_back():
    ctx = dict(phase='questions', today='2026-10-01', form={'goal':'学习', 'end':'2026-10-09'},
               recent=[dict(role='user',text='原文')], cards=[], sections=[], modules=[], facts={})
    frozen = deepcopy(ctx)
    system, messages = plan_intent.intent_request(ctx, '回答')
    payload = json.loads(messages[0]['content'].split('\n', 1)[1])
    assert payload == dict(ctx=ctx,text='回答') and ctx == frozen and system == plan_intent.INTENT_SYSTEM
    assert list(payload['ctx']) == ['form', 'sections', 'modules', 'cards', 'facts', 'phase', 'recent', 'today']
