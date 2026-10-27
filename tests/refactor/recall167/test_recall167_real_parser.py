"""Real original-question parser/compiler/store; no fabricated candidate rows."""
import pytest

from refactor.learning.course import point_materials, question_bank
from tests.refactor.recall167.test_recall167_concept_recall import recall167_store


def put_chunk(store, chunk_id, text, position=0):
    with store.transaction():
        store._put('source_chunks', 'p', dict(id=chunk_id, source_id='src', position=position,
            text=text, section=['模块'], page_start=None, page_end=None))


def test_recall167_real_parser_compiles_all_own_questions_and_reuses_saved_bank(recall167_store):
    text = '''### 队列是什么？
- 参考要点：队列遵循先进先出。
- 参考要点：新元素从队尾加入。
- 参考要点：最早进入的元素从队首移除。

### 栈是什么？
- 参考要点：栈遵循后进先出。
- 参考要点：新元素放在栈顶。
- 参考要点：移除操作也发生在栈顶。
'''
    put_chunk(recall167_store, 'c1', text)
    def no_cloze(*args, **kwargs):
        pytest.fail('Original questions must not call the gap generator')
    result = point_materials.prepare_module(recall167_store, 'p', 'm',
        {'finder': no_cloze}, lambda *args: None)
    bank = question_bank.assessment_questions(recall167_store.records('question_bank', 'p'), 'm')
    assert result['recall'] == 2 and result['questions'] == 2
    assert {q['stem'] for q in bank} == {'队列是什么？', '栈是什么？'}
    assert all(q['origin'] == 'found' and q['subpoint_id'] == 'point-s1' for q in bank)
    for q in bank:
        assert len(q['key_points']) == 1  # One complete explanation, not three checklist fragments.
        assert q['key_points'][0].count('- 参考要点：') == 3
        assert all(part in text for part in q['key_points'])
        assert q['source']['chunk_ids'] == ['c1']
    before = recall167_store.records('question_bank', 'p')
    assert point_materials.prepare_module(recall167_store, 'p', 'm',
        {'finder': no_cloze}, lambda *args: None) == result
    assert recall167_store.records('question_bank', 'p') == before


def test_recall167_real_parser_does_not_borrow_another_subpoints_answer(recall167_store):
    put_chunk(recall167_store, 'other', '''### 队列是什么？
- 队列遵循先进先出。
- 元素从队尾加入。
- 元素从队首移除。
''', position=1)
    def no_gaps(packet, passages, **kwargs):
        assert [row['id'] for row in passages] == ['c1']
        return {'subpoints': [{'id': s['id'], 'cloze': []} for s in packet['subpoints']]}
    result = point_materials.prepare_point(recall167_store, 'p',
        recall167_store.records('points', 'p')[0], {'finder': no_gaps})
    assert result['questions'] == 0
    assert recall167_store.records('question_bank', 'p') == []
