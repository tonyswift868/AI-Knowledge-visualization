"""Independent points and default-all state must not reinterpret old snapshots."""
import copy
from types import SimpleNamespace

from refactor.learning.app.classroom import Classroom
from refactor.learning.course import material_questions as mq, question_bank as qb
from refactor.learning.course.self_test import SelfTestStore, compile_row
from tests.refactor.mastery70.test_question70_assessment_flow import env, act, position, PLAN


REFERENCE = '**四个特点**\n\n分片键应该具备以下特点：\n\n- 共性：覆盖查询。\n- 离散性：分布均匀。\n- 稳定性：避免变更。\n- 扩展性：支持扩容。'


def old_question():
    return dict(id='stable214', kind='recall', reference=REFERENCE,
                key_points=mq.previous_answer_units(REFERENCE))


def test_recall_state214_old_213_block_expands_to_four_without_mutating_bank():
    old = old_question()
    frozen = copy.deepcopy(old)
    current = qb.with_recall_key_points(old)
    assert current['id'] == old['id']
    assert len(current['key_points']) == 4
    assert current['key_points'] == mq.answer_units(REFERENCE)
    assert old == frozen
    assert qb.with_recall_key_points(current) == current
    headings = qb.recall_key_point_headings(current)
    assert len(headings) == 4 and all('四个特点' in heading for heading in headings)
    assert all(point in REFERENCE for point in current['key_points'])


def test_recall_state214_title_is_context_not_a_fifth_point():
    old = dict(old_question(), key_points=['**四个特点**', '- 共性：覆盖查询。', '- 离散性：分布均匀。',
                                         '- 稳定性：避免变更。', '- 扩展性：支持扩容。'])
    current = qb.with_recall_key_points(old)
    assert len(current['key_points']) == 4
    assert '**四个特点**' not in current['key_points']


def test_recall_state214_selected_algorithm_title_keeps_only_its_own_explanation():
    reference = '**随机法**\n\n随机选择一台服务器。\n\n**轮询法**\n\n依次选择服务器。\n\n**一致性哈希**\n\n按哈希环选择节点。'
    row = dict(old_question(), reference=reference, key_points=['**随机法**'])
    projected = qb.with_recall_key_points(row)
    assert projected['key_points'] == ['随机选择一台服务器。']
    assert qb.recall_key_point_headings(projected) == ['随机法']


def test_recall_state214_partial_unknown_selection_preserves_complete_answer_access():
    old = dict(old_question(), key_points=['- 共性：覆盖查询。', '无法映射的旧显示片段'])
    current = qb.with_recall_key_points(old)
    assert current['key_points'] == mq.answer_units(REFERENCE)
    assert current['id'] == old['id'] and current['reference'] == old['reference']


def test_recall_state214_fresh_reveal_all_ticks_and_repeat_keeps_crosses():
    room = object.__new__(Classroom)
    room._user = lambda *args, **kwargs: None
    state = dict(pending=dict(question=old_question()))
    room.recall_reveal(None, state, None, 'stable214', '我的回答')
    assert state['pending']['ticked'] == [0, 1, 2, 3]
    room.recall_draft(None, state, None, 'stable214', [0, 2, 3])
    frozen = copy.deepcopy(state)
    room.recall_reveal(None, state, None, 'stable214', '重试不能覆盖回答和叉')
    assert state == frozen


def test_recall_state214_old_revealed_card_keeps_its_exact_points_and_empty_ticks():
    room = object.__new__(Classroom)
    room._user = lambda *args, **kwargs: None
    state = dict(pending=dict(question=old_question(), revealed=True, learner_answer='旧回答', ticked=[]))
    frozen = copy.deepcopy(state)
    room.recall_reveal(None, state, None, 'stable214', '重开')
    assert state == frozen


def test_recall_state214_real_classroom_records_actual_cross_and_self_verdict(env):
    position(env, 'm0')
    view = act(env, 'start_test', 'module_test')
    qid = view['question']['id']
    view = act(env, 'recall_reveal', qid, '回答已写')
    points = view['question']['key_points']
    assert view['question']['ticked'] == list(range(len(points)))
    ticks = list(range(1, len(points)))
    act(env, 'recall_draft', qid, ticks)
    view = act(env, 'recall_submit', qid, ticks, True)
    assert view['recall_result']['correct'] is True
    assert view['recall_result']['missed'] == [0]
    fact = env.store.records('question_answers', PLAN)[0]
    assert fact['ticked'] == ticks and fact['missed'] == [0]
    assert fact['self_correct'] is True and fact['question']['key_points'] == points


def upload_question():
    source = '# 怎样选择分片键？\n\n' + REFERENCE
    raw = dict(kind='recall', stem='怎样选择分片键？', options=[], answer=REFERENCE,
               source=source, objective_kind=None)
    return compile_row(raw, source, SimpleNamespace(complete=lambda *a, **k: (_ for _ in ()).throw(AssertionError('no model'))))


def test_recall_state214_upload_default_all_and_cross_counts_missed(tmp_path):
    store = SelfTestStore('upload214', tmp_path)
    q = upload_question()
    key = store.create('问答自测', dict(questions=[q], missing=[], rejected=[], warnings=[]))['id']
    view = store.start(key)
    attempt = view['run']['current']['attempt_id']
    view = store.reveal(key, attempt, '回答')
    assert view['question']['ticked'] == [0, 1, 2, 3]
    result = store.submit(key, attempt, ticked=[0, 2, 3])
    assert result['run']['current']['verdict'] == dict(correct=False, missed=[1])
    assert result['run']['current']['ticked'] == [0, 2, 3]
    assert SelfTestStore('upload214', tmp_path).resume(key)['question']['ticked'] == [0, 2, 3]


def test_recall_state214_upload_old_revealed_snapshot_not_renumbered(tmp_path):
    store = SelfTestStore('upload-old214', tmp_path)
    q = upload_question()
    q['key_points'] = mq.previous_answer_units(REFERENCE)
    key = store.create('旧自测', dict(questions=[q], missing=[], rejected=[], warnings=[]))['id']
    view = store.start(key)
    attempt = view['run']['current']['attempt_id']
    def restore_old(body):
        body['run']['current'].update(revealed=True, answer='旧回答')
    store.update(key, restore_old)
    view = store.reveal(key, attempt, '事后回答')
    assert view['question']['key_points'] == q['key_points']
    assert view['question']['ticked'] == []
    assert view['question']['answer'] == '旧回答'
