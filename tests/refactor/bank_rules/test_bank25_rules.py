"""Batch 25: exact quotas, replayable time facts and same-question process probes."""
import json
import random
import threading
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from tests.refactor.question_solver.fakes import bank_response

from refactor.learning.app.classroom import Classroom
from refactor.learning.course import question_bank
from refactor.learning.course.question_agent import Question, parse_questions
from refactor.learning.course.judge_agent import Verdict, parse_judgement, judge_request
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.mastery import Judgement, AttemptClosed
from refactor.learning.course.store import CourseStore, StoreError
from refactor.learning.course.sync_contract import SyncRecord

PLAN, DAY = 'd' * 32, date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)


@pytest.fixture
def lab(tmp_path):
    with CourseStore('batch25', tmp_path) as store:
        store.create_plan(PLAN, goal='步骤与概念', window=PlanWindow(DAY, DAY + timedelta(days=2), 100))
        store.add_sources(PLAN, [dict(id='s', url='https://example.invalid', chunks=[dict(chunk_id='c', text='列表追加、遍历与汇总。')])])
        store.save_outline(PLAN, [dict(id='st', title='基础')], [dict(id='m', stage_id='st', title='列表')])
        store.save_generated_module(PLAN, 'm', [dict(id=p, title=p, summary='列表操作', minutes=10,
            chunk_ids=['c']) for p in ('p0', 'p1')], check_minutes=8, final_check_minutes=6,
            check_question_minutes=2, today=DAY)
        store.start_plan(PLAN, today=DAY)
        services = SimpleNamespace(store=store, account_id='batch25', data_root=tmp_path,
            settings=SimpleNamespace(load=lambda: dict(refresh_time='04:00')),
            gateway=SimpleNamespace(complete=bank_response))
        room = Classroom(services, lambda: DAY, now=lambda: NOW, rng=random.Random(25))
        room._teacher = room._go_on = room._prepare_later = lambda *a, **k: None
        calls = []
        def questions(co, cx, purpose, ids, **kw):
            calls.append((list(ids), kw))
            return [Question(f'{p}-{len(calls)}-{i}', 'short' if i == 1 else 'choice', f'{p}第{len(calls)}批操作{i}怎样做？',
                () if i == 1 else ('正确步骤', '错法一', '错法二', '错法三'), '正确步骤', '符合步骤要求。', (p,), ('c',), a,
                ('操作怎样做',) if i == 1 else None)
                for p in ids for i, a in enumerate([a for a in ('recall', 'apply', 'contrast', 'fix_error', 'transfer')
                    if a not in kw.get('used_angles', {}).get(p, ())][:kw.get('counts', {}).get(p, 2)])]
        room._questions = questions
        yield room, store, calls
        for t in threading.enumerate():
            if t.name in ('classroom-question-bank', 'classroom-check-profile'): t.join(3)


def wait(room):
    deadline = time.monotonic() + 3
    while any(j['status'] == 'running' for j in room._bank_jobs.values()) and time.monotonic() < deadline:
        time.sleep(.005)
    assert not any(j['status'] == 'running' for j in room._bank_jobs.values())


def test_initial_two_stays_two_after_correct_and_wrong_refill_is_once(lab):
    room, store, calls = lab
    co = room._course(PLAN)
    room._bank_fill(co, None, 'm')
    rows = room._bank_rows(co, 'm')
    assert len(rows) == 4 and len(calls) == 1
    for q in rows: store.mark_bank_correct(PLAN, q['id'])
    room._bank_fill(co, None, 'm')
    assert len(calls) == 1 and len(room._bank_rows(co, 'm')) == 4
    pending = dict(kind='module_test', answer_id='wrong-once', point_ids=['p0'], question=rows[0])
    verdict = Verdict('answer', False, ('p0',), '所问步骤有误。')
    room._refill_wrong(co, {}, pending, verdict); wait(room)
    room._refill_wrong(co, {}, pending, verdict); wait(room)
    assert len(room._bank_rows(co, 'm')) == 6
    assert len(calls) == 2 and calls[-1][0] == ['p0']
    assert all(q['angle'] != rows[0]['angle'] for q in room._bank_rows(co, 'm') if q.get('refill_for'))
    room._refill_wrong(co, {}, pending, Verdict('answer', True, (), '正确。'))
    wait(room); assert len(calls) == 2


def test_final_only_fills_deficit_and_queued_reserve_recounts(lab):
    room, store, calls = lab
    co = room._course(PLAN)
    room._bank_fill(co, None, 'm')
    rows = room._bank_rows(co, 'm')
    store.mark_bank_correct(PLAN, next(q['id'] for q in rows if q['point_ids'] == ['p0']))
    assert question_bank.final_deficits(room._bank_rows(co, 'm'), {'p0': 2, 'p1': 1}) == {'p0': 0, 'p1': 0}
    store.mark_bank_correct(PLAN, next(q['id'] for q in rows if q['point_ids'] == ['p1']))
    needs = question_bank.final_deficits(room._bank_rows(co, 'm'), {'p0': 2, 'p1': 1})
    assert needs == {'p0': 0, 'p1': 1}
    room._bank_fill(co, None, 'm', point_ids=['p1'], reserve={'p1': 2})
    room._bank_fill(co, None, 'm', point_ids=['p1'], reserve={'p1': 2})
    assert len(calls) == 2 and calls[-1][1]['counts'] == {'p1': 1}
    assert len(room._bank_rows(co, 'm')) == 5


def test_initial_job_plus_pending_reserve_does_not_add_extra(lab):
    room, store, calls = lab
    co = room._course(PLAN)
    entered, release = threading.Event(), threading.Event()
    good = room._questions
    def slow(*a, **kw):
        entered.set(); assert release.wait(2)
        return good(*a, **kw)
    room._questions = slow
    room._prepare_bank_later(co, 'm'); assert entered.wait(1)
    room._prepare_bank_later(co, 'm', point_ids=['p0'], reserve={'p0': 1})
    release.set(); wait(room)
    assert len(calls) == 1 and len(room._bank_rows(co, 'm')) == 4


def test_invalid_three_requirements_discards_only_that_question(lab):
    room, _, calls = lab
    ctx = dict(purpose='bank', per_point=2, points=[dict(id=p, title=p, summary='列表', chunk_ids=['c']) for p in ('p0', 'p1')],
               excerpts={'c': '列表操作与迭代'}, counts={'p0': 2, 'p1': 2})
    good = room._questions(room._course(PLAN), None, 'bank', ['p0', 'p1'])
    from dataclasses import asdict
    rows = []
    for q in good:
        row = {k: v for k, v in asdict(q).items() if k not in ('answer_kind', 'accepted', 'tolerance', 'rubric') and v is not None}
        row['options'] = list(row['options']); row['point_ids'] = list(row['point_ids']); row['chunk_ids'] = list(row['chunk_ids'])
        rows.append(row)
    rows[0].update(type='short', options=[], answer='标准过程', required_points=['一步', '二步', '三步'])
    accepted = parse_questions(json.dumps({'questions': rows}, ensure_ascii=False), ctx, partial_bank=True)
    assert len(accepted) == 3 and {q.id for q in accepted} == {q.id for q in good[1:]}
    responses = iter([accepted, [good[0]]])
    room._questions = lambda *a, **k: (calls.append((list(a[3]), k)), next(responses))[1]
    calls.clear(); room._bank_fill(room._course(PLAN), None, 'm')
    assert [c[0] for c in calls] == [['p0', 'p1'], ['p0']]
    assert calls[-1][1]['counts'] == {'p0': 1}


def test_retest_excludes_question_ids_and_angles_even_when_only_old_questions_exist(lab):
    room, store, _ = lab
    co = room._course(PLAN); room._bank_fill(co, None, 'm')
    rows = room._bank_rows(co, 'm')
    q = next(q for q in rows if q['point_ids'] == ['p0'])
    store.append_question_answer(PLAN, 'old', module_id='m', context='module_test', attempt_id='a', question_id=q['id'],
        point_ids=['p0'], angle=q['angle'], correct=False, wrong_points=['p0'], reason='错误。', at=NOW)
    asked, angles = room._bank_history(co, 'm')
    chosen = room._bank_select(co, None, 'm', ['p0'], ['p0'], asked=asked, used_angles=angles)
    assert chosen[0]['id'] != q['id'] and chosen[0]['angle'] != q['angle']
    fresh, missing = question_bank.choose_questions([q], ['p0'], ['p0'], asked=asked, used_angles=angles)
    assert fresh == [] and missing == ('p0',)


def test_exposed_but_unjudged_question_is_also_excluded(lab):
    room, store, _ = lab
    co = room._course(PLAN); room._bank_fill(co, None, 'm')
    q = room._bank_rows(co, 'm')[0]
    store.mark_bank_asked(PLAN, q['id'], 'module_test')
    assert not store.records('judgements', PLAN) and not store.records('question_answers', PLAN)
    asked, angles = room._bank_history(co, 'm')
    assert q['id'] in asked and q['angle'] in angles[q['point_ids'][0]]
    pending = next(r for r in store.pending_records(1000) if r.entity == 'question_bank' and r.payload['id'] == q['id'])
    store.mark_synced(pending.entity, pending.entity_id, pending.local_revision, 1)
    remote = dict(pending.payload); remote.pop('asked_in')
    store.apply_remote([(SyncRecord(pending.entity, pending.entity_id, PLAN, pending.schema, remote, False, 2), 2)])
    assert store.records('question_bank', PLAN)[0]['asked_in'] == ['module_test']


def test_wrong_refill_uses_one_question_when_only_one_new_angle_remains(lab):
    room, _, calls = lab
    co = room._course(PLAN)
    room._bank_fill(co, None, 'm', point_ids=['p0'], refill_for='four-used',
                    used_angles={'p0': ['recall', 'apply', 'contrast', 'fix_error']})
    assert calls[0][1]['counts'] == {'p0': 1}
    assert [q['angle'] for q in room._bank_rows(co, 'm')] == ['transfer']


def test_bank_request_output_cap_and_counts_are_real_gateway_arguments(lab):
    room, _, _ = lab
    captured = []
    def gateway(purpose, messages, **kwargs):
        captured.append(kwargs)
        task = json.loads(messages[0]['content'].split('【本次出题要求】\n')[1])
        assert task['counts'] == {'p0': 1} and task['used_angles'] == {'p0': ['recall']}
        row = dict(id='new', type='short', stem='请说明列表操作的用途', options=[], answer='用于操作序列',
                   reference='说明操作用途。', required_points=['操作用途'], point_ids=['p0'], chunk_ids=['c'], angle='apply')
        return SimpleNamespace(text=json.dumps({'questions': [row]}, ensure_ascii=False))
    room.services.gateway = SimpleNamespace(complete=gateway)
    questions = Classroom._questions(room, room._course(PLAN), None, 'bank', ['p0'], counts={'p0': 1}, used_angles={'p0': ['recall']})
    assert captured[0]['max_tokens'] == 16384 and questions[0].required_points == ('操作用途',)


@pytest.mark.parametrize('kind', ['casual', 'quick', 'module_test', 'retest', 'skip_test', 'check', 'practice'])
def test_missing_process_keeps_same_question_and_grades_combined_reply(lab, kind):
    room, store, _ = lab
    co = room._course(PLAN)
    store.append_judgement(PLAN, Judgement('base', 1, NOW, 'module_test', 'm', 'base', (('p0', True), ('p1', True))))
    store.append_attempt_closed(PLAN, AttemptClosed('closed', 2, 'm', 'base', 'module_test'))
    q = dict(id='process', module_id='m', type='short', point_ids=['p0'], angle='apply', stem='写出求和那一步',
             options=[], answer='用累加器求和', reference='说明累加步骤', required_points=['累加步骤'], chunk_ids=['c'])
    # Store a real question to exercise correct-seen persistence.
    q['id'] = store.add_bank_questions(PLAN, 'm', [q])[0]
    pending = dict(kind=kind, answer_id='process-answer', point_ids=['p0'], question=q, asks=2)
    state = dict(module_id='m', index=0, pending=pending, test=dict(kind=kind, attempt='a', index=0, queue=[q, q], targets=['p0']),
                 practice=dict(index=0, count=1, resume=dict(module_id='m', step='teach')), check=dict(index=0, queue=[q]))
    room._next_check = lambda *a: None
    replies = []
    def judge(*args):
        replies.append(args[-1])
        return Verdict('process_missing', None, (), '没有给出所问步骤。') if len(replies) == 1 else Verdict('answer', True, (), '给出了等价步骤。')
    room._judge = judge
    handler = room._casual_reply if kind == 'casual' else room._practice_reply if kind == 'practice' else room._check_reply if kind == 'check' else room._test_reply
    handler(co, state, None, '10', pending)
    assert state['pending'] is pending and pending['question'] is q and pending['answer_id'] == 'process-answer'
    assert len(store.records('judgements', PLAN)) == 1 and not store.records('question_answers', PLAN)
    assert store.records('messages', PLAN)[-1]['content'] == '写一下你是怎么得到的'
    handler(co, state, None, '先置零，再把每个值加到累加器', pending)
    assert replies[-1] == '原回答：10\n补充过程：先置零，再把每个值加到累加器'
    assert len(store.records('practice_results' if kind == 'practice' else 'judgements', PLAN)) == (1 if kind == 'practice' else 2)


def test_process_missing_parser_and_explicit_rubric_overrides_legacy_prose():
    ctx = dict(mode='test', points=[dict(id='p', title='求和', summary='累加')], excerpts={'c': '从零累加'},
               question=dict(type='short', stem='写出累加步骤'), reference='很长的解释，另有未问内容',
               required_points=['初始化并累加'], require_evidence=True)
    system, messages = judge_request(ctx, '结果是10')
    assert 'process_missing' in system and '初始化并累加' in messages[0]['content']
    value = dict(kind='process_missing', correct=None, wrong_points=[], reason='看不出所问步骤。', uncertain=False, checks=[])
    assert parse_judgement(json.dumps(value, ensure_ascii=False), ctx).correct is None


def test_process_reply_is_not_carried_into_a_replacement_casual_question(lab):
    room, _, _ = lab
    co = room._course(PLAN)
    replies = []
    verdicts = iter([Verdict('process_missing', None, (), '看不出步骤。'),
                     Verdict('answer', False, ('p0',), '步骤有误。'), Verdict('answer', True, (), '新题答对。')])
    def judge(*args):
        replies.append(args[-1]); return next(verdicts)
    room._judge = judge
    pending = dict(answer_id='first', kind='casual', point_ids=['p0'])
    room._adjudicate(co, dict(module_id='m'), None, 'casual', pending, '旧题结果')
    room._adjudicate(co, dict(module_id='m'), None, 'casual', pending, '旧题过程')
    pending['answer_id'] = 'replacement'
    room._adjudicate(co, dict(module_id='m'), None, 'casual', pending, '新题回答')
    assert replies == ['旧题结果', '原回答：旧题结果\n补充过程：旧题过程', '新题回答']


def test_composite_wrong_counts_time_without_point_verdict_and_replay_is_once(lab):
    room, store, _ = lab
    co = room._course(PLAN)
    q = dict(id='both', type='choice', stem='综合应用', options=['好', '错', '错二', '错三'], answer='好',
             point_ids=['p0', 'p1'], angle='apply')
    pending = dict(kind='module_test', answer_id='composite', point_ids=q['point_ids'], question=q)
    state = dict(module_id='m', test=dict(kind='module_test', attempt='a', index=0, queue=[q, q], targets=q['point_ids']))
    room._split_test_question = lambda *a: None
    room._test_reply(co, state, None, '错', pending)
    assert not store.records('judgements', PLAN) and len(store.records('question_answers', PLAN)) == 1
    state['test']['index'] = 0
    room._test_reply(co, state, None, '重开重放', pending)
    assert len(store.records('question_answers', PLAN)) == 1
    store.append_attempt_closed(PLAN, AttemptClosed('close', 1, 'm', 'a', 'module_test'))
    store.settle_module_test(PLAN, 'm', 'a', today=DAY)
    store.settle_module_test(PLAN, 'm', 'a', today=DAY)
    credits = store.records('credits', PLAN)
    assert len(credits) == 1 and credits[0]['credit']['minutes'] == 2


def test_answer_fact_sync_upgrade_conflict_and_pending_tombstone(lab, tmp_path):
    _, store, _ = lab
    fields = dict(module_id='m', context='module_test', attempt_id='a', question_id='q', point_ids=['p0'],
                  angle='apply', correct=False, wrong_points=['p0'], reason='错答。', at=NOW)
    store.append_question_answer(PLAN, 'answer', **fields)
    store.append_question_answer(PLAN, 'answer', **dict(fields, at=NOW + timedelta(hours=1)))
    with pytest.raises(StoreError): store.append_question_answer(PLAN, 'answer', **dict(fields, correct=True))
    rows = store.pending_records(1000)
    fact = next(r for r in rows if r.entity == 'question_answers')
    assert fact.schema == 'mentorflow.course.question_answers.v1'
    store.apply_remote([(SyncRecord(fact.entity, fact.entity_id, fact.plan_key, fact.schema, fact.payload, True, 9), 9)])
    assert len(store.records('question_answers', PLAN)) == 1
    with CourseStore('batch25', tmp_path / 'remote') as remote:
        remote.apply_remote([(r, i + 1) for i, r in enumerate(rows)])
        assert remote.records('question_answers', PLAN) == store.records('question_answers', PLAN)
    store._db.execute('DROP TABLE question_answers')
    store._db.execute('UPDATE schema_version SET version=4')
    with CourseStore('batch25', tmp_path) as upgraded:
        assert upgraded._db.execute('SELECT version FROM schema_version').fetchone()[0] == 5


def test_attribution_only_new_errors_and_reopen_does_not_repeat(lab):
    room, store, _ = lab
    calls = []
    def ask(*a, **kw):
        calls.append(a[2]); return SimpleNamespace(text='操作步骤混淆。')
    room._ask = ask
    co = room._course(PLAN)
    room._attribute_later(co); assert not calls
    details = dict(question='求和步骤？', answer='错答', reason='漏掉初始化。')
    store.append_judgement(PLAN, Judgement('err', 1, NOW, 'casual', 'm', results=(('p0', False),)), details=details)
    room._attribute_later(co)
    for t in threading.enumerate():
        if t.name == 'classroom-check-profile': t.join(2)
    assert calls == ['check_profile']
    room._attribute_later(co)
    store.append_judgement(PLAN, Judgement('right', 2, NOW, 'casual', 'm', results=(('p0', True),)), details=details)
    room._attribute_later(co); assert len(calls) == 1
    store.append_judgement(PLAN, Judgement('err2', 3, NOW, 'casual', 'm', results=(('p1', False),)), details=details)
    room._attribute_later(co)
    for t in threading.enumerate():
        if t.name == 'classroom-check-profile': t.join(2)
    assert len(calls) == 2
