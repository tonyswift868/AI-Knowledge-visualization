"""Batch 23 fixed offline sample; exercise real store, room and preparation jobs."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import json
import random
import threading
import time

import pytest

from refactor.learning.app.classroom import Classroom
from refactor.learning.connections.errors import ModelError
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.mastery import Judgement, AttemptClosed
from refactor.learning.course.question_agent import Question, parse_questions, _answer_in_stem, _normal
from refactor.learning.course.judge_agent import Verdict, parse_judgement, _required_points
from refactor.learning.course.store import CourseStore

PLAN = 'b' * 32
DAY = date(2026, 10, 1)


def test_rubric_decimals_are_not_numbered_requirements():
    reference = '1. 结果应为 "python 3.0"，空格和数字 3.0 保留。2. s 不变，仍为 "PyThOn 3.0"。'
    assert _required_points(reference) == (
        '结果应为 "python 3.0"，空格和数字 3.0 保留。', 's 不变，仍为 "PyThOn 3.0"。')
    assert _required_points(r'1. 返回 True。\n2. 元组中任一前缀匹配即可。') == (
        '返回 True。', '元组中任一前缀匹配即可。')


def test_computed_answer_inside_longer_input_literal_is_not_disclosure():
    assert not _answer_in_stem(_normal('hello'), _normal('"xxhelloxx".strip("x") 的结果是什么？'))
    assert not _answer_in_stem(_normal('hello'), _normal('"aa-hello-bb".strip("ab-") 的结果是什么？'))
    assert _answer_in_stem(_normal('hello'), _normal('正确结果为 "hello"，请选择。'))
    assert _answer_in_stem(_normal('先进先出'), _normal('按先进先出处理，应遵循哪种顺序？'))
    assert len({_normal(o, preserve_case=True) for o in ('py3.8-ok', 'PY3.8-OK', 'Py3.8-OK', 'py3.8-OK')}) == 4


@pytest.fixture
def bankroom(tmp_path):
    with CourseStore('bank-judge-offline', tmp_path) as store:
        store.create_plan(PLAN, goal='六个固定概念', window=PlanWindow(DAY, DAY + timedelta(days=2), 120))
        store.add_sources(PLAN, [dict(id='s', url='https://example.invalid/fixed', chunks=[dict(chunk_id='k', text='概念的定义与应用。')])])
        store.save_outline(PLAN, [dict(id='stage', title='基础')],
                           [dict(id=mid, stage_id='stage', title=mid) for mid in ('m0', 'm1')])
        for mid, ids in (('m0', range(6)), ('m1', range(6, 7))):
            store.save_generated_module(PLAN, mid, [dict(id=f'p{i}', title=f'概念{i}', summary='定义与应用',
                minutes=10, chunk_ids=['k']) for i in ids], check_minutes=2, today=DAY)
        store.start_plan(PLAN, today=DAY)
        services = SimpleNamespace(store=store, account_id='bank-judge-offline', data_root=tmp_path,
            settings=SimpleNamespace(load=lambda: dict(refresh_time='04:00')), gateway=None)
        room = Classroom(services, lambda: DAY, rng=random.Random(7),
                         now=lambda: datetime(2026, 10, 1, 10, tzinfo=timezone.utc))
        room._prepare_later = lambda *a: None  # teaching belongs to the parallel task
        room._teacher = lambda *a, **k: None
        calls = []
        def questions(course, ctx, purpose, ids, **kw):
            calls.append(tuple(ids))
            return [Question(f'{pid}-{len(calls)}-{j}', 'choice', f'{pid}第{len(calls)}批应用{j}应选哪个？',
                ('正确选项', '干扰一', '干扰二', '干扰三'), '正确选项', '符合资料要点。',
                (pid,), ('k',), angle) for pid in ids for j, angle in enumerate(('recall', 'apply'))]
        room._questions = questions
        yield room, store, calls, questions
        for thread in threading.enumerate():
            if thread.name == 'classroom-question-bank': thread.join(3)


def run(room, method=None, *args):
    return room.run(PLAN, lambda co, st, cx: getattr(room, method)(co, st, cx, *args) if method else None)


def settle(room):
    deadline = time.monotonic() + 3
    while any(j['status'] == 'running' for j in room._bank_jobs.values()) and time.monotonic() < deadline:
        time.sleep(.005)
    return run(room)


def test_six_points_use_three_small_batches_and_failure_repairs_only_missing(bankroom):
    room, store, calls, good = bankroom
    failed = True
    def flaky(co, cx, purpose, ids, **kw):
        if failed and 'p3' in ids:
            calls.append(tuple(ids))
            raise ModelError('MODEL_TIMEOUT', '单批超时。')
        return good(co, cx, purpose, ids, **kw)
    room._questions = flaky
    course = room._course(PLAN)
    with pytest.raises(ModelError): room._bank_fill(course, None, 'm0')
    assert calls == [('p0', 'p1'), ('p2', 'p3'), ('p2',), ('p3',), ('p4', 'p5')]
    assert {q['point_ids'][0] for q in store.records('question_bank', PLAN)} == {'p0', 'p1', 'p2', 'p4', 'p5'}
    failed = False
    calls.clear()
    room._bank_fill(course, None, 'm0')
    assert calls == [('p3',)]
    assert len(store.records('question_bank', PLAN)) == 12


def test_partial_pair_keeps_covered_point_and_repairs_other_point(bankroom):
    room, store, calls, good = bankroom
    def partial(co, cx, purpose, ids, **kw):
        rows = good(co, cx, purpose, ids, **kw)
        return [q for q in rows if q.point_ids == ('p0',)] if len(ids) == 2 else rows
    room._questions = partial
    room._bank_fill(room._course(PLAN), None, 'm0', point_ids=['p0', 'p1'])
    assert calls == [('p0', 'p1'), ('p1',)]
    assert len(store.records('question_bank', PLAN)) == 4


def test_entry_prepares_immediately_and_pending_test_auto_starts(bankroom):
    room, store, calls, good = bankroom
    entered, release = threading.Event(), threading.Event()
    def blocked(*a, **kw):
        entered.set()
        assert release.wait(2)
        return good(*a, **kw)
    room._questions = blocked
    start = time.monotonic()
    run(room, 'enter')
    assert time.monotonic() - start < .2 and entered.wait(1)
    start = time.monotonic()
    view = run(room, 'start_test', 'skip_test')
    assert view['step'] == 'preparing_questions' and time.monotonic() - start < .2
    assert any('正在准备题目' in m['content'] for m in view['messages'])
    release.set()
    view = settle(room)
    assert view['step'] == 'testing' and view['question']
    assert (PLAN, 'm1') in room._bank_jobs  # next module prepared while testing
    assert max(map(len, calls)) <= 2


def test_ready_bank_starts_test_without_model_wait(bankroom):
    room, store, calls, _ = bankroom
    run(room, 'enter'); settle(room)
    start = time.monotonic()
    view = run(room, 'start_test', 'skip_test')
    assert view['step'] == 'testing' and time.monotonic() - start < .2


def test_failed_preparation_returns_to_entry_with_point_and_reason(bankroom):
    room, store, calls, _ = bankroom
    def failed(co, cx, purpose, ids, **kw):
        calls.append(tuple(ids))
        raise ModelError('MODEL_TIMEOUT', '固定样本超时。')
    room._questions = failed
    run(room, 'enter')
    run(room, 'start_test', 'skip_test')
    view = settle(room)
    assert view['step'] == 'teach' and not view['question']
    assert any('概念' in m['content'] and '超时' in m['content'] for m in view['messages'])


@pytest.mark.parametrize('mode', ['check', 'practice'])
@pytest.mark.parametrize('correct', [True, False])
def test_deeper_probe_uses_original_final_or_practice_context(bankroom, mode, correct):
    room, store, calls, _ = bankroom
    room._attribute_later = lambda *a: None
    run(room, 'enter'); settle(room)
    course = room._course(PLAN)
    for module in course.modules:
        mid = module['id']
        store.append_judgement(PLAN, Judgement('pass:'+mid, course.next_seq(), room.now(), 'module_test', mid, 'a',
            tuple((p['id'], True) for p in course.points_of(mid))))
        store.append_attempt_closed(PLAN, AttemptClosed('closed:'+mid, course.next_seq(), mid, 'a', 'module_test'))
    if mode == 'check':
        state = room._load(course)
        state.update(step='check_offer', pending=None)
        room._save(course, state)
        run(room, 'start_check')
    else:
        room.run(PLAN, lambda co, st, cx: room.start_practice(co, st, cx, point_id='p0', count=1))
    state = room._load(room._course(PLAN))
    original = state['pending']
    original['question']['type'] = 'short'
    room._save(course, state)
    count = len(store.records('judgements', PLAN))
    room._judge = lambda *a: Verdict('answer', True, (), '含义不明确。', True)
    run(room, 'reply', '不明确的作答')
    assert len(store.records('judgements', PLAN)) == count
    pending = room._load(room._course(PLAN))['pending']
    assert pending['depth_probe'] and pending['point_ids'] == original['point_ids']
    text = pending['question']['answer'] if correct else next(o for o in pending['question']['options'] if o != pending['question']['answer'])
    run(room, 'reply', text)
    if mode == 'check':
        event = max((r['event'] for r in store.records('judgements', PLAN)), key=lambda e: e['seq'])
        assert event['context'] == 'check' and event['results'] == [[pending['point_ids'][0], correct]]
    else:
        assert len(store.records('judgements', PLAN)) == count
        assert store.records('practice_results', PLAN)[0]['correct'] is correct


def test_material_addition_rebuilds_only_affected_module_and_new_module(bankroom):
    room, store, calls, _ = bankroom
    room.run(PLAN, lambda co, st, cx: (st.update(module_id='m1'), room.enter(co, st, cx)))
    settle(room)  # last module entry also prepares final-check reserves
    before = {q['id'] for q in room._bank_rows(room._course(PLAN), 'm1')}
    calls.clear()
    store.add_sources(PLAN, [dict(id='new', url='https://example.invalid/new', chunks=[dict(chunk_id='kn', text='新增应用条件。')])])
    store.append_material_additions(PLAN, [], [dict(point_id='p0', chunk_ids=['kn'])], ['m0', 'm1'], today=DAY)
    settle(room)
    assert calls and all(set(group) <= {f'p{i}' for i in range(6)} for group in calls)
    assert {q['id'] for q in room._bank_rows(room._course(PLAN), 'm1')} == before
    calls.clear()
    store.append_material_additions(PLAN, [dict(id='m2', stage_id='stage', title='新模块', summary='',
        prerequisites=[], check_minutes=2, points=[dict(id='p7', title='新点', summary='新应用', minutes=10,
                                                       chunk_ids=['kn'])])], [], ['m0', 'm1', 'm2'], today=DAY)
    settle(room)
    assert calls == [('p7',)]


@pytest.mark.parametrize('correct', [True, False])
def test_uncertain_answer_is_replaced_by_deeper_question_and_only_probe_is_credited(bankroom, correct):
    room, store, calls, _ = bankroom
    run(room, 'enter'); settle(room)
    view = run(room, 'start_test', 'skip_test')
    course = room._course(PLAN)
    state = room._load(course)
    original = state['pending']
    original['question']['type'] = 'short'
    state['test']['queue'][0] = original['question']
    room._save(course, state)
    room._judge = lambda *a: Verdict('answer', True, (), '表述还不能确定。', True)
    view = run(room, 'reply', '可能是这样的')
    assert store.records('judgements', PLAN) == []
    pending = room._load(room._course(PLAN))['pending']
    assert pending['depth_probe'] and pending['question']['stem'] != original['question']['stem']
    assert pending['point_ids'] == original['point_ids']
    text = pending['question']['answer'] if correct else next(x for x in pending['question']['options'] if x != pending['question']['answer'])
    run(room, 'reply', text)
    rows = store.records('judgements', PLAN)
    assert len(rows) == 1 and rows[0]['event']['results'] == [[pending['point_ids'][0], correct]]


def test_paraphrased_or_joined_evidence_does_not_create_uncertainty():
    ctx = dict(mode='test', points=[dict(id='p', title='列表', summary='可变')], excerpts={'c': '列表可变。'},
               question=dict(type='short', stem='修改的是哪个列表？'), reference='修改原列表。',
               reply='是原来的那个对象，里面的元素会变化。', require_evidence=True)
    data = dict(kind='answer', correct=True, wrong_points=[], reason='说明原对象发生变化。', uncertain=False,
                checks=[dict(point_id='p', requirement='修改原列表。', applies=True, met=True,
                             evidence='原来的对象……其中元素改变')])
    verdict = parse_judgement(json.dumps(data, ensure_ascii=False), ctx)
    assert verdict.correct and not verdict.uncertain

