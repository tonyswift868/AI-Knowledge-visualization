"""Batch 98: section order and strictly lossless wire-only answer dedup."""
from copy import deepcopy
import json

import pytest

from refactor.learning.course import teacher_agent as teacher, material_questions as mq, point_materials as pm
from refactor.learning.course.request_layout import AppendOnlyPage
from tests.refactor.teacher_agent.test_class_teacher_agent import context
from tests.refactor.material_questions70.test_material_questions70_contract import material_store70


def test_cache98_new_page_fixed_source_precedes_append_only_history():
    ctx = context(plan_passages={'elsewhere': '其他已采用原文'})
    frozen = deepcopy(ctx)
    system, first = teacher.teacher_request(ctx)
    ctx['dialogue'].append(dict(role='user', text='下一轮原话'))
    _, second = teacher.teacher_request(ctx)
    assert first[:3] == second[:3]
    assert first[3:-1] == second[3:-2]
    assert second[-2]['content'].endswith('"下一轮原话"}')
    assert first[-1] == second[-1]
    assert frozen['excerpts'] == ctx['excerpts']
    assert system == teacher.teacher_request(frozen)[0]


def test_cache98_page_moves_between_points_without_stale_aliases_or_lost_turns():
    page = AppendOnlyPage()
    ctx = context(request_page=page, dialogue=[])
    system, first = teacher.teacher_request(ctx)
    page.accept(system, first, [], '原始老师回复')
    ctx['dialogue'] = [dict(role='assistant', text='显示的老师回复'), dict(role='user', text='学生新问题')]
    ctx['excerpts'] = {'chunk-new': '新的知识点原文'}
    _, second = teacher.teacher_request(ctx)
    assert second[:len(first)] == first
    assert second[len(first)]['content'] == '原始老师回复'
    assert [json.loads(m['content'].split('\n', 1)[1]) for m in second[len(first)+1:-2]] == ctx['dialogue']
    assert json.loads(second[-2]['content'].split('\n', 1)[1]) == [dict(alias='c1', chunk_id='chunk-new', text='新的知识点原文')]
    page.accept(system, second, ctx['dialogue'], '第二次原始回复')
    ctx['dialogue'].append(dict(role='user', text='又一轮'))
    _, third = teacher.teacher_request(ctx)
    assert third[:len(second)] == second
    assert sum(m['content'].startswith('【当前模块原文') for m in third) == 2
    # Overflow creates a new page using the CURRENT source map before history.
    page.max_messages = 4
    _, reset = teacher.teacher_request(ctx)
    assert reset[1] == second[-2]
    assert not any('chunk-a' in m['content'] for m in reset)
    assert len([m for m in reset if m['content'].startswith('【对话原文')]) == 3


@pytest.mark.parametrize('reference,omitted', [
    ('第一句。\n 第二句。\t第三句。', True),
    ('# 标题\n正文第一句。第二句。', False),
    ('```python\nx = 1\n```\n解释。', True),
    ('第一句。\n第一句。', True),
    ('| 类型 | 说明 |\n| --- | --- |\n| 键 | 值 |', False),
    ('第一句。\u3000第二句。', True),
])
def test_cache98_reference_omitted_only_for_exact_nonwhitespace_equality(reference, omitted):
    candidate = dict(candidate_type='recall', reference=reference, id='q', stem='原题？')
    original = deepcopy(candidate)
    packet = mq.request_candidate(candidate)
    assert ('reference' not in packet) is omitted
    assert packet['units'] == [dict(id=i+1, text=t) for i, t in enumerate(mq.answer_units(reference))]
    if not omitted:
        assert packet['reference'] == reference
    assert candidate == original
    objective = dict(candidate_type='objective', reference=reference)
    assert mq.request_candidate(objective) == objective


def test_cache98_wire_dedup_keeps_full_answer_and_selected_ids_in_store(material_store70):
    store = material_store70
    chunk = store._get('source_chunks', 'p', 'c')
    reference = '第一句。第二句。第三句。第四句。第五句。第六句。'
    chunk['text'] = '# 什么是队列？\n\n' + reference
    with store.transaction():
        store._put('source_chunks', 'p', chunk)
    reference = mq.candidates(store.records('source_chunks', 'p'), store.records('sources', 'p'))[0]['reference']
    def splitter(packet, **kwargs):
        row = packet['candidates'][0]
        assert 'reference' not in row
        assert ''.join(u['text'] for u in row['units']) == ''.join(reference.split())
        return dict(questions=[dict(candidate_id=row['id'], key_point_ids=[1])])
    result = pm.prepare_module(store, 'p', 'm', dict(question_splitter=splitter), lambda *a: None)
    saved = store.records('question_bank', 'p')[0]
    assert result['questions'] == 1 and result['rejected'] == 0
    assert saved['reference'] == reference
    assert saved['key_points'] == mq.answer_units(reference)
