"""Fixed offline check-side sample: no provider calls or user data."""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

from refactor.learning.course.mastery import AttemptClosed, Judgement, MasteryBook, PointRef
from tests.refactor.integration38.support import bank_question
from refactor.learning.course.question_bank import select_question, review_points
from refactor.learning.course.report_comments import generate_comments
from refactor.learning.course.store import CourseStore
from refactor.learning.course.sync_contract import SyncRecord
from tests.refactor.app.test_app_classroom import ACCOUNT, PLAN, act, room








def test_composite_wrong_has_no_point_event_then_splits_once(room):
    classroom, agents, store = room
    store.add_bank_questions(PLAN, 'm0', [question('00both', ('p0', 'p1')),
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
    assert sum(len(q['uses']) for q in store.records('question_bank', PLAN)) == 3


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

def question(identifier,ids,chunk='k1'):
    return bank_question(ids[0],'m0',kind='fill',ids=list(ids),answer='队列是先进先出的结构。',chunk=chunk)|{'id':identifier}

def test_selector_uses_per_purpose_history_and_can_reuse_original_questions():
    bank=[question('a',['p0']),question('b',['p0'])]
    first=select_question(bank,'p0','module');first['uses'].append(dict(purpose='module',at='2026-10-03T00:00:00Z'))
    assert select_question(bank,'p0','module')['id']=='b'
    assert select_question(bank,'p0','final')['id']=='a'
    bank[1]['uses'].append(dict(purpose='module',at='2026-10-03T00:01:00Z'))
    assert select_question(bank,'p0','module')['id']=='a'

def test_new_bank_sync_use_revision_and_practice_dedup(room,tmp_path):
    _,_,store=room
    qid=store.add_bank_questions(PLAN,'m0',[question('sync',['p0'])])[0]
    before=next(r for r in store.pending_records(1000) if r.entity=='question_bank' and r.payload['id']==qid)
    store.record_question_use(PLAN,qid,'free')
    store.mark_synced(before.entity,before.entity_id,before.local_revision,4)
    after=next(r for r in store.pending_records(1000) if r.entity=='question_bank' and r.payload['id']==qid)
    assert after.local_revision>before.local_revision and after.payload['uses'][0]['purpose']=='free'
    store.append_practice_result(PLAN,qid,'m0',['p0'],False,result_id='practice-one',wrong_points=['p0'])
    with CourseStore(ACCOUNT,tmp_path/'peer') as replica:
        replica.apply_remote([(r,i+10) for i,r in enumerate(store.pending_records(1000))])
        assert next(q for q in replica.records('question_bank',PLAN) if q['id']==qid)['uses']
        practice=next(r for r in store.pending_records(1000) if r.entity=='practice_results')
        replica.apply_remote([(practice,100)])
        assert len(replica.records('practice_results',PLAN))==1
