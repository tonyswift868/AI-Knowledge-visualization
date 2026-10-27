"""Read-time key-point migration must never reinterpret saved self-check indices."""
import copy
from datetime import date, timedelta

from refactor.learning.app.classroom import Classroom
from refactor.learning.course import material_questions as mq, question_bank as qb
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore


REFERENCE = '**特点**\n\n具备以下特点：\n- 分布均匀。\n- 保持稳定。\n\n**选择**\n\n按查询路径选择键。避免跨库查询。'


def legacy_question():
    candidate = dict(id='original213', candidate_type='recall', stem='如何选择分片键？',
        reference=REFERENCE, point_ids=['point'], source_title='原文', source_url='',
        chunk_ids=['c'], block_ids=[1], source_id='src')
    row = mq.compile_question(dict(kind='recall', stem=candidate['stem']), candidate, 'm')
    row['key_points'] = ['- 分布均匀。', '- 保持稳定。', '按查询路径选择键。']
    return row


def test_history213_current_question_regroups_without_mutating_wire_or_id():
    old = legacy_question()
    before = copy.deepcopy(old)
    current = qb.assessment_questions([old])[0]
    assert current['id'] == old['id']
    assert len(current['key_points']) == 2
    assert all(point in REFERENCE for point in current['key_points'])
    assert '分布均匀' in current['key_points'][0] and '保持稳定' in current['key_points'][0]
    assert old == before
    assert qb.with_recall_key_points(current) == current
    qb.validate_question(old)
    qb.validate_question(current)


def test_history213_unmatched_old_selection_keeps_question_usable():
    old = legacy_question()
    # An imported historical card can lack recognizable selection fragments;
    # it must remain visible with its literal reference, never be discarded.
    old['key_points'] = ['旧卡片显示片段']
    current = qb.with_recall_key_points(old)
    assert current['id'] == old['id']
    assert current['reference'] == REFERENCE
    assert current['key_points'] == mq.answer_units(REFERENCE)
    assert old['key_points'] == ['旧卡片显示片段']


def test_history213_more_than_five_complete_groups_are_valid():
    reference = '\n\n'.join(f'## 概念{i}\n\n第{i}层意思的完整解释。还有相关条件。' for i in range(1, 8))
    row = legacy_question()
    row.update(reference=reference, key_points=mq.answer_units(reference))
    assert len(row['key_points']) == 7
    qb.validate_question(row)


def test_history213_short_legacy_label_does_not_expand_to_every_mention():
    reference = '下面介绍ASCII与其它编码。\n\nASCII\n\nASCII编码的定义。\n\nUnicode与ASCII的联系。\n\nUTF-8兼容ASCII。'
    row = legacy_question()
    row.update(reference=reference, key_points=['ASCII'])
    grouped = qb.with_recall_key_points(row)
    assert grouped['key_points'] == ['ASCII\n\nASCII编码的定义。']
    assert qb.with_recall_key_points(grouped) == grouped


def test_history213_reveal_migrates_only_unrevealed_pending_and_freezes_indices():
    room = object.__new__(Classroom)
    room._user = lambda *args, **kwargs: None
    state = dict(pending=dict(question=legacy_question()))
    room.recall_reveal(None, state, None, 'original213', '根据查询来选')
    assert len(state['pending']['question']['key_points']) == 2
    state['pending']['ticked'] = [1]
    frozen = copy.deepcopy(state)
    room.recall_reveal(None, state, None, 'original213', '重试')
    assert state == frozen

    old = dict(pending=dict(question=legacy_question(), revealed=True, learner_answer='旧作答', ticked=[2]))
    frozen = copy.deepcopy(old)
    room.recall_reveal(None, old, None, 'original213', '重开')
    assert old == frozen
    room.recall_draft(None, old, None, 'original213', [2])


def test_history213_saved_history_and_outbox_unchanged_after_reopen(tmp_path):
    today = date(2026, 10, 28)
    store = CourseStore('history213', tmp_path)
    store.create_plan('p', goal='原题', window=PlanWindow(today, today + timedelta(days=2), 60))
    store.add_sources('p', [dict(id='src', title='原文', url='https://example.test/guide', adopted=True, chunks=[dict(chunk_id='c', text=REFERENCE, section=['模块'])])])
    store.save_outline('p', [dict(id='stage', title='阶段')], [dict(id='m', stage_id='stage', title='模块', prerequisites=[])])
    store.save_generated_module('p', 'm', [dict(id='point', title='大点', summary='目标', minutes=10, chunk_ids=['c'])], check_minutes=5, today=today)
    old = legacy_question()
    store.add_bank_questions('p', 'm', [old])
    store.append_question_answer('p', 'old-answer', module_id='m', context='check', attempt_id='old-attempt',
        question_id=old['id'], point_ids=['point'], angle=None, correct=False, wrong_points=['point'],
        answer='旧答案', ticked=[2], missed=[0, 1], self_correct=False, question=copy.deepcopy(old))
    before = {entity: store.records(entity, 'p') for entity in ('question_answers', 'judgements', 'credits', 'ledger_events', 'question_bank')}
    pending = copy.deepcopy(store.pending_records(1000))
    history = store.wrong_answers('p')
    store.close()
    store = CourseStore('history213', tmp_path)
    try:
        assert len(qb.assessment_questions(store.records('question_bank', 'p'))[0]['key_points']) == 2
        assert store.wrong_answers('p') == history
        attempt = history[0]['attempts'][0]
        assert attempt['ticked'] == [2] and attempt['missed'] == [0, 1]
        assert attempt['question']['key_points'] == old['key_points']
        assert {entity: store.records(entity, 'p') for entity in before} == before
        assert store.pending_records(1000) == pending
    finally:
        store.close()
