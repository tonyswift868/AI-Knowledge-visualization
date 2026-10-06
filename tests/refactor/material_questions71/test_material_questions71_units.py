"""Saved batch-70 originals: selection formatting never loses a source question."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.learning.course import material_questions as mq, point_materials as pm
from refactor.learning.course.question_bank import validate_question
from tests.refactor.material_questions70.test_material_questions70_contract import material_store70


CASES = json.loads(Path(__file__).with_name('discarded70.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', CASES, ids=[c['candidate']['stem'] for c in CASES])
def test_material71_ten_saved_questions_keep_literal_answer_with_numbered_units(case, material_store70):
    c = case['candidate']
    units = mq.answer_units(c['reference'])
    ids = list(range(1, min(5, len(units))+1))
    q = mq.compile_question(dict(kind='recall', stem=c['stem'], key_point_ids=ids), c, case['module_id'])
    assert validate_question(q) == q
    assert q['reference'] == c['reference'] and q['stem'] == c['stem']
    assert q['key_points'] == [units[i-1] for i in ids]
    # Only the isolated fixture's point/module IDs change; original question,
    # answer, unit selection and source evidence remain literal saved data.
    persisted=dict(q,module_id='m',point_ids=['mp'])
    assert material_store70.add_bank_questions('p','m',[persisted])==[q['id']]
    assert material_store70.records('question_bank','p')[0]['reference']==c['reference']


def test_material71_units_keep_list_items_and_table_rows_literal():
    reference = '定义。第二句。\n\n1. 第一阶段。包括两件事。\n- 第二阶段\n\n| 类型 | 说明 |\n| --- | --- |\n| 键值 | 用键取值 |'
    assert mq.answer_units(reference) == ['定义。', '第二句。', '1. 第一阶段。包括两件事。', '- 第二阶段', '| 类型 | 说明 |', '| 键值 | 用键取值 |']
    assert mq.selected_units(reference, [1, 3, 6]) == ['定义。', '1. 第一阶段。包括两件事。', '| 键值 | 用键取值 |']


def test_material71_bold_numbered_items_are_whole_units_not_formatting_fragments():
    assert mq.answer_units('**1. 键值数据库。支持按键查值。**\n>\n**\n**2. 文档数据库。**') == [
        '**1. 键值数据库。支持按键查值。**', '**2. 文档数据库。**']


def test_material71_short_reference_no_model_or_format_gate(material_store70):
    s = material_store70
    result = pm.prepare_module(s, 'p', 'm', dict(gateway=SimpleNamespace(complete=lambda *a, **k: pytest.fail('no model for short answers'))), lambda *a: None)
    assert result['questions'] == 2 and result['rejected'] == 0


@pytest.mark.parametrize('ids', [[1,1,2], [1,2,99], [], ['1',2,3]])
def test_material71_invalid_selection_keeps_question_with_logged_fallback(material_store70, ids, caplog):
    s = material_store70
    c = s._get('source_chunks', 'p', 'c')
    c['text'] = '# 什么是队列？\n\n第一句。第二句。第三句。第四句。第五句。第六句。'
    with s.transaction(): s._put('source_chunks', 'p', c)
    def select(packet, **kwargs):
        candidate = packet['candidates'][0]
        assert len(candidate['units']) >= 6
        return dict(questions=[dict(candidate_id=candidate['id'], key_point_ids=ids)])
    with caplog.at_level('INFO'):
        result = pm.prepare_module(s, 'p', 'm', dict(question_splitter=select), lambda *a: None)
    assert result['questions'] == 1 and result['rejected'] == 0
    assert 'selection fallback' in caplog.text
    assert s.records('question_bank', 'p')[0]['key_points'] == mq.answer_units(c['text'].split('\n\n')[1])[:5]


def test_material71_single_unit_is_not_rejected_for_fewer_than_three_points():
    c = dict(candidate_type='recall', stem='什么是队列？', reference='队列遵循先进先出。', point_ids=['p'], source_title='资料', source_url='', block_ids=[1], chunk_ids=['c'])
    c['id'] = 'q'
    q = mq.compile_question(dict(kind='recall', stem=c['stem']), c, 'm')
    assert validate_question(q)['key_points'] == ['队列遵循先进先出。']


def test_material71_invalid_selection_json_keeps_original_recall(material_store70, caplog):
    s=material_store70
    c=s._get('source_chunks','p','c')
    c['text']='# 什么是队列？\n\n第一句。第二句。第三句。第四句。第五句。第六句。'
    with s.transaction():s._put('source_chunks','p',c)
    with caplog.at_level('INFO'):
        result=pm.prepare_module(s,'p','m',dict(gateway=SimpleNamespace(complete=lambda *a,**k:SimpleNamespace(text='{bad JSON'))),lambda *a:None)
    assert result['questions']==1 and result['rejected']==0
    assert 'JSON fallback' in caplog.text
    original=mq.candidates(s.records('source_chunks','p'),s.records('sources','p'))[0]['reference']
    assert s.records('question_bank','p')[0]['reference']==original
