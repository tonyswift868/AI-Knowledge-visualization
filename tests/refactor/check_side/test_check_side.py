"""Fixed offline check-side sample: no provider calls or user data."""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

from refactor.learning.course.judge_agent import judge_request, parse_judgement
from refactor.learning.course.mastery import AttemptClosed, Judgement, MasteryBook, PointRef
from refactor.learning.course.question_agent import Question, parse_questions
from refactor.learning.course.question_bank import choose_questions, review_points
from refactor.learning.course.report_comments import generate_comments
from refactor.learning.course.store import CourseStore
from refactor.learning.course.sync_contract import SyncRecord
from tests.refactor.app.test_app_classroom import ACCOUNT, PLAN, act, room


def item(identifier, ids, *, angle='recall', correct=False):
    return dict(id=identifier, point_ids=list(ids), angle=angle, correct_seen=correct)


def question(identifier, ids, chunk='k1'):
    return Question(identifier, 'short', f'请解释{identifier}的作用？', (), '标准答案',
                    '题面要求的作用', tuple(ids), (chunk,), 'recall')


def test_selector_prefers_unstable_single_then_compact_combination_and_never_reuses_correct():
    bank = [item('single-p0', ['p0']), item('single-p1', ['p1']),
            item('combo', ['p1', 'p2']), item('old', ['p0'], correct=True)]
    selected, missing = choose_questions(bank, ['p0', 'p1', 'p2'], ['p0'], rng=random.Random(1))
    assert [q['id'] for q in selected] == ['single-p0', 'combo']
    assert missing == ()
    selected, missing = choose_questions(bank, ['p1', 'p2'], smaller_than=2, single_once=['p1'])
    assert [q['id'] for q in selected] == [] and missing == ('p1', 'p2')


def test_composite_wrong_has_no_point_event_then_splits_once(room):
    classroom, agents, store = room
    store.add_bank_questions(PLAN, 'm0', [question('both', ('p0', 'p1')),
                                           question('zero', ('p0',)), question('one', ('p1',))])
    view = act(classroom, 'start_test', 'skip_test')
    assert view['question']['total'] == 1
    agents.verdicts = [('answer', False, ['p0']), ('answer', True, []), ('answer', True, [])]
    view = act(classroom, 'reply', '不清楚')
    assert view['step'] == 'testing'
    assert store.records('judgements', PLAN) == []
    assert view['question']['total'] == 3
    act(classroom, 'reply', '答案一')
    view = act(classroom, 'reply', '答案二')
    assert view['module']['state'] == 'skipped'
    rows = store.records('judgements', PLAN)
    assert len(rows) == 2 and all(row['event']['results'][0][1] for row in rows)
    assert len({row['event']['results'][0][0] for row in rows}) == 2
    assert all(store.load_mastery(PLAN).point(pid).module_errors == 0 for pid in ('p0', 'p1'))
    assert sum(q['correct_seen'] for q in store.records('question_bank', PLAN)) == 2


def test_overlapping_questions_both_count_for_the_point():
    at = datetime(2026, 9, 29, tzinfo=timezone.utc)
    book = MasteryBook([PointRef('p0', 'm0'), PointRef('p1', 'm0')])
    book.apply(Judgement('first', 1, at, 'module_test', 'm0', 'attempt',
                         (('p0', True), ('p1', True)), None))
    book.apply(Judgement('second', 2, at, 'module_test', 'm0', 'attempt',
                         (('p0', False),), None))
    book.apply(AttemptClosed('close', 3, 'm0', 'attempt', 'module_test'))
    assert book.point('p0').module_errors == 1
    assert book.point('p0').state == 'red'
    assert book.point('p1').state == 'green'


def test_final_check_round_stops_after_four_questions(room):
    classroom, agents, store = room
    for module_id, answers in (('m0', 2), ('m1', 1)):
        agents.verdicts = [('answer', True, [])] * answers
        view = act(classroom, 'start_test', 'skip_test')
        for _ in range(answers):
            view = act(classroom, 'reply', '正确回答')
        act(classroom, 'next_module')
    agents.verdicts = [('answer', False, ['p0', 'p1', 'p2'])] * 4
    view = act(classroom, 'start_check')
    for _ in range(4):
        point_id = classroom._load(classroom._course(PLAN))['pending']['point_ids'][0]
        agents.verdicts[0] = ('answer', False, [point_id])
        view = act(classroom, 'reply', '不会')
    assert view['step'] == 'checking' and view['question'] is None
    assert len([r for r in store.records('judgements', PLAN) if r['event']['context'] == 'check']) == 4


def test_exhausted_bank_refills_before_module_test(room):
    classroom, agents, store = room
    old = store.add_bank_questions(PLAN, 'm0', [question('old-zero', ('p0',)),
                                               question('old-one', ('p1',))])
    for question_id in old:
        store.mark_bank_correct(PLAN, question_id)
    view = act(classroom, 'start_test', 'skip_test')
    assert view['step'] == 'testing'
    assert 'questions' in agents.calls
    queued = classroom._load(classroom._course(PLAN))['test']['queue']
    assert queued and all(q['id'] not in old for q in queued)


def test_practice_is_separate_and_review_marker_follows_latest_answer(room):
    classroom, agents, store = room
    store.add_bank_questions(PLAN, 'm0', [question('zero', ('p0',)), question('one', ('p1',)),
                                           question('again', ('p0',))])
    act(classroom, 'start_test', 'skip_test')
    agents.verdicts = [('answer', True, []), ('answer', True, [])]
    act(classroom, 'reply', '回答一')
    act(classroom, 'reply', '回答二')
    before_plan = store.get_plan(PLAN)
    before_book = store.load_mastery(PLAN).to_dict()
    view = classroom.run(PLAN, lambda course, state, ctx: classroom.start_practice(
        course, state, ctx, point_id='p0', count=2))
    assert view['step'] == 'practicing'
    agents.verdicts = [('answer', False, ['p0']), ('answer', True, [])]
    act(classroom, 'reply', '不会')
    assert review_points(store.records('practice_results', PLAN)) == {'p0'}
    view = act(classroom, 'reply', '会了')
    assert view['step'] == 'test_done'
    assert review_points(store.records('practice_results', PLAN)) == set()
    assert len(store.records('practice_results', PLAN)) == 2
    assert store.get_plan(PLAN) == before_plan
    assert store.load_mastery(PLAN).to_dict() == before_book
    assert store.records('reports', PLAN) == []


def test_question_bank_and_practice_sync_revision_race_and_remote_dedup(room, tmp_path):
    _, _, store = room
    question_id = store.add_bank_questions(PLAN, 'm0', [question('one', ('p0',))])[0]
    submitted = next(r for r in store.pending_records(1000) if r.entity == 'question_bank')
    store.mark_bank_correct(PLAN, question_id)
    store.mark_synced(submitted.entity, submitted.entity_id, submitted.local_revision, 4)
    pending = next(r for r in store.pending_records(1000) if r.entity == 'question_bank')
    assert pending.local_revision > submitted.local_revision and pending.payload['correct_seen']
    store.append_practice_result(PLAN, question_id, 'm0', ['p0'], False,
                                 result_id='practice-one', at=datetime(2026, 9, 29, tzinfo=timezone.utc))
    upload = store.pending_records(1000)
    with CourseStore(ACCOUNT, tmp_path) as replica:
        replica.apply_remote([(record, index + 10) for index, record in enumerate(upload)])
        assert replica.records('question_bank', PLAN)[0]['correct_seen']
        assert len(replica.records('practice_results', PLAN)) == 1
        practice = next(r for r in upload if r.entity == 'practice_results')
        replica.apply_remote([(practice, 100)])
        assert len(replica.records('practice_results', PLAN)) == 1
        answered = next(r for r in upload if r.entity == 'question_bank')
        stale = SyncRecord(answered.entity, answered.entity_id, PLAN, answered.schema,
                           dict(answered.payload, correct_seen=False), False, answered.local_revision)
        replica.apply_remote([(stale, 101)])
        assert replica.records('question_bank', PLAN)[0]['correct_seen']
        assert any(r.entity == 'question_bank' and r.payload['correct_seen']
                   for r in replica.pending_records(1000))
        tombstone = SyncRecord(practice.entity, practice.entity_id, PLAN, practice.schema,
                               practice.payload, True, practice.local_revision)
        replica.apply_remote([(tombstone, 101)])
        assert len(replica.records('practice_results', PLAN)) == 1


def test_short_answer_uncertainty_returns_one_clarifying_question():
    context = dict(mode='test', points=[dict(id='p', title='概念', summary='说明')],
                   excerpts={'c': '概念资料'}, question={'stem': '请说明概念的作用？', 'type': 'short'},
                   reference='只要求作用')
    system, _ = judge_request(context, '可能是这样')
    assert '先问一句澄清' in system
    verdict = parse_judgement(json.dumps(dict(kind='clarify', correct=None, wrong_points=[],
        reason='你说的作用是指哪一步？'), ensure_ascii=False), context)
    assert verdict.kind == 'clarify' and verdict.correct is None


def test_bank_batch_has_two_single_questions_per_point_and_one_composite():
    ctx = dict(purpose='bank', per_point=2, avoid=[], excerpts={'c': '一个概念及其应用。'},
               points=[dict(id=p, title=p, summary='应用', chunk_ids=['c']) for p in ('a', 'b')])
    rows = []
    for pid in ('a', 'b'):
        for index, angle in enumerate(('recall', 'apply')):
            rows.append(dict(id=f'{pid}{index}', type='short', stem=f'请解释{pid}的第{index}种用途？',
                             options=[], answer='可以处理概念', reference='用途来自题面',
                             point_ids=[pid], chunk_ids=['c'], angle=angle))
    rows.append(dict(id='both', type='short', stem='请比较两个概念的用途？', options=[],
                     answer='两个概念用于不同处', reference='分别说明用途',
                     point_ids=['a', 'b'], chunk_ids=['c'], angle='contrast'))
    parsed = parse_questions(json.dumps({'questions': rows}, ensure_ascii=False), ctx)
    assert len(parsed) == 5 and len(parsed[-1].point_ids) == 2


def test_report_comments_quote_only_stored_reasons_and_never_call_gateway():
    class Store:
        def records(self, entity, plan):
            assert entity == 'judgements'
            return [dict(id='j1', event=dict(event_id='j1', seq=1, context='module_test',
                        results=[['p', False]]), details={'reason': '把入队和出队顺序弄反了。'}),
                    dict(id='j2', event=dict(event_id='j2', seq=2, context='check',
                        results=[['p', False]]), details={})]
    class Forbidden:
        def complete(self, *args, **kwargs): raise AssertionError('model used')
    report = {'points': [dict(point_id='p', level=3), dict(point_id='q', summary='后进先出', level=2)]}
    comments = generate_comments(Forbidden(), Store(), 'plan', report)
    assert comments['p'] == '模块检测错 1 次、最终检验错 1 次；当时的判定原因：把入队和出队顺序弄反了。；现已通过。'
    assert comments['q'] == '“后进先出”无错答记录；目前尚未通过。'
