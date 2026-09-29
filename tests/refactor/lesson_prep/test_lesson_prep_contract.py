"""Offline lesson preparation, independent review, sync cache and fallback."""
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace

import pytest

from refactor.learning.app.classroom import Classroom
from refactor.learning.course import lesson_prep
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.planner_agents import AgentFormatError
from refactor.learning.course import planner_agents
from refactor.learning.course.question_agent import Question
from refactor.learning.course.store import CourseStore
from refactor.learning.course.teacher_agent import parse_teaching


ROOT = Path('D:/MentorFlow/.local/test-tmp/class-prep')
PLAN = 'b' * 32
DAY = date(2026, 10, 5)


def draft(code='a, b = 1, 2'):
    return lesson_prep.LessonPrep(
        '队列按先进先出的顺序取出元素。',
        (lesson_prep.Example('排队买票，先到先服务。', True, True, None),
         lesson_prep.Example('a 与 b 同时赋值。', False, True, code)),
        '像排队。', '现实排队可能有人插队，此处只类比先后次序。',
        '队列的取出顺序是什么？', '先进入的先取出。', ('生活中的排队只是类比。',))


def context(point='p1'):
    return {'module': {'id': 'm1', 'title': '队列'},
            'point': {'id': point, 'title': '先进先出', 'summary': '先进入的先取出'},
            'excerpts': {'c1': '队列采用先进先出。'}}


def test_strict_prep_and_review_contract_and_prefix():
    first = context('p1')
    second = context('p2')
    assert lesson_prep.prep_request(first)[1][:2] == lesson_prep.prep_request(second)[1][:2]
    parsed = lesson_prep.parse_prep(json.dumps(asdict(draft()), ensure_ascii=False))
    assert parsed == draft() and lesson_prep.check_code(parsed) == ()
    assert lesson_prep.parse_review('{"verdict":"approve","issues":[],"revised":null}').verdict == 'approve'
    with pytest.raises(AgentFormatError):
        lesson_prep.parse_review('{"verdict":"revise","issues":["有错"],"revised":null}')
    with pytest.raises(AgentFormatError):
        lesson_prep.parse_prep(json.dumps(asdict(draft('a = 1, b = 2')), ensure_ascii=False))


def test_batch_contract_and_archived_failure_path_regression():
    contexts = [context('p1'), context('p2')]
    request = lesson_prep.batch_prep_request(contexts)
    assert request[1][0]['content'] == lesson_prep.prep_request(contexts[0])[1][0]['content']
    ids = ('p1', 'p2')
    wire = {'lessons': [{'point_id': point_id, 'lesson': asdict(draft())} for point_id in ids]}
    parsed = lesson_prep.parse_batch_preps(json.dumps(wire, ensure_ascii=False), ids)
    assert parsed == (draft(), draft())
    reviews = {'reviews': [{'point_id': point_id, 'verdict': 'approve', 'issues': [], 'revised': None}
                           for point_id in ids]}
    assert all(r.verdict == 'approve' for r in lesson_prep.parse_batch_reviews(json.dumps(reviews), ids))
    wire['lessons'][1]['point_id'] = 'wrong'
    with pytest.raises(AgentFormatError, match='编号'):
        lesson_prep.parse_batch_preps(json.dumps(wire, ensure_ascii=False), ids)

    archived = lesson_prep.LessonPrep(
        '不可比较的元素无法排序。', draft().examples, '像排队。', '有些元素不能比较。',
        "为什么 [None, 'hello', 10] 排不了序？sort() 又返回什么？",
        '这个列表排不了序，因为元素不可比较。sort() 返回的是默认的 None。', ())
    assert '失败操作与正常返回值被混为一谈' in lesson_prep.check_lesson(archived)
    fixed = lesson_prep.LessonPrep(
        archived.explanation, archived.examples, archived.analogy, archived.analogy_boundary,
        archived.casual_question, '这个列表调用 sort 会抛出 TypeError，没有正常返回值；可比较的列表排序成功时才返回 None。', ())
    assert lesson_prep.check_lesson(fixed) == ()


def test_teacher_rejects_unrunnable_assignment_in_code_format():
    ctx = {'prefs': {'teacher_style': 'warm', 'detail': 'normal', 'examples': 'few', 'analogy_domains': ['life']},
           'plan': {'id': 'p', 'goal': '学习', 'final_goal': '学会', 'level': '初学'},
           'module': {'id': 'm', 'title': '基础', 'points': [{'id': 'x', 'title': '赋值', 'summary': '赋值'}]},
           'excerpts': {'c1': '多重赋值可以写 a, b = 1, 2。'}, 'dialogue': [], 'profile': '',
           'director': '讲赋值。', 'user': ''}
    with pytest.raises(AgentFormatError, match='code'):
        parse_teaching('从 `a = 1, b = 2` 出发。', ctx)
    with pytest.raises(AgentFormatError, match='python'):
        parse_teaching('原文是交互记录：\n```python\n>>> a = [1]\n>>> a.pop()\n1\n```', ctx)
    with pytest.raises(AgentFormatError, match='打印结果'):
        parse_teaching('例子：\n```python\na = [1, 2, 3]\nprint(a, a.append(4)) # [1, 2, 3] None\n```', ctx)
    assert parse_teaching('例子：\n```python\na = [1, 2, 3]\nprint(a, a.append(4)) # [1, 2, 3, 4] None\n```', ctx).speech
    assert parse_teaching('交互记录：\n```pycon\n>>> a = [1]\n>>> a.pop()\n1\n```', ctx).speech
    assert parse_teaching('可写 `a, b = 1, 2`。', ctx).speech


def test_safe_runnable_examples_are_executed_without_full_builtins():
    good = draft("a = [1, 2]\na.append(3)\nprint(a)")
    assert lesson_prep.check_code(good) == ()
    bad = draft("a = [1, 2]\na.pop(9)")
    assert lesson_prep.check_code(bad) == ('例2代码不能正常运行：IndexError',)


@pytest.fixture
def room():
    ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT) as root:
        store = CourseStore('prep_learner', Path(root))
        store.create_plan(PLAN, goal='学队列', final_goal='能解释顺序', level='beginner',
                          window=PlanWindow(DAY, DAY + timedelta(days=1), 60))
        store.add_sources(PLAN, [{'id': 's', 'kind': 'url', 'url': 'https://example.invalid',
                                  'chunks': [{'chunk_id': 'c1', 'text': '队列采用先进先出。'}]}])
        store.save_outline(PLAN, [{'id': 's1', 'title': '基础'}], [{'id': 'm1', 'stage_id': 's1', 'title': '队列'}])
        store.save_generated_module(PLAN, 'm1', [
            {'id': 'p1', 'title': '顺序', 'summary': '先进入的先取出', 'minutes': 10, 'chunk_ids': ['c1']},
            {'id': 'p2', 'title': '用途', 'summary': '按顺序处理', 'minutes': 10, 'chunk_ids': ['c1']}],
            check_minutes=5, today=DAY)
        store.start_plan(PLAN, today=DAY)
        settings = SimpleNamespace(load=lambda: {'refresh_time': '04:00', 'teacher_style': 'warm',
                                                  'detail': 'normal', 'examples': 'few', 'analogy_domains': ['life']})
        services = SimpleNamespace(store=store, settings=settings, account_id='prep_learner', data_root=Path(root))
        classroom = Classroom(services, lambda: DAY, now=lambda: datetime(2026, 10, 5, 10, tzinfo=timezone.utc))
        yield classroom, store
        for thread in threading.enumerate():
            if thread.name == 'classroom-lesson-prep':
                thread.join(5)
        store.close()


def test_background_reviewed_prep_and_questions_are_reused_then_invalidated(room, monkeypatch):
    classroom, store = room
    calls = []

    def fake_ask(_course, _ctx, purpose, request, _parse, _max_tokens):
        calls.append(purpose)
        if purpose == 'questions':
            return [Question(f'q{i}', 'short', f'解释点 {i}', (), '先进入先取出', '按顺序', (pid,), ('c1',), 'recall')
                    for i, pid in enumerate(('p1', 'p2'))]
        raise AssertionError(purpose)

    def fake_prep(_course, purpose, request, _parse, _max_tokens):
        calls.append(purpose)
        if purpose == 'questions':
            return [Question(f'q{i}', 'short', f'解释点 {i}', (), '先进入先取出', '按顺序', (pid,), ('c1',), 'recall')
                    for i, pid in enumerate(('p1', 'p2'))]
        if purpose == 'lesson_prep': return (draft(), draft())
        if purpose == 'lesson_review': return (lesson_prep.Review('approve', (), None),) * 2
        raise AssertionError(purpose)

    monkeypatch.setattr(classroom, '_ask', fake_ask)
    monkeypatch.setattr(classroom, '_prep_ask', fake_prep)
    course = classroom._course(PLAN)
    state = classroom._load(course)
    state['day'] = DAY.isoformat()
    classroom._prepare_later(course, state)
    for thread in threading.enumerate():
        if thread.name == 'classroom-lesson-prep': thread.join(5)
    assert calls.count('lesson_prep') == 1 and calls.count('lesson_review') == 1
    assert classroom._prepared_point(course, course.point('p1')) == draft()
    assert len(classroom._bank_rows(course, 'm1')) == 2
    # Concurrent profile update must preserve already-synced preparation.
    store.save_teacher_profile(PLAN, {'text': '新档案'})
    assert classroom._prepared_point(course, course.point('p1')) == draft()
    with store.transaction():
        changed = store._get('source_chunks', PLAN, 'c1')
        changed['text'] = '队列采用先进先出，但仅在正常出队时。'
        store._put('source_chunks', PLAN, changed)
    changed_course = classroom._course(PLAN)
    assert classroom._prepared_point(changed_course, changed_course.point('p1')) is None
    assert classroom._bank_rows(changed_course, 'm1') == []


@pytest.mark.parametrize('verdict', ['live', 'revise'])
def test_unverified_prep_never_becomes_teacher_basis(room, monkeypatch, verdict):
    classroom, store = room
    def fake_prep(_course, purpose, _request, _parse, _max_tokens):
        if purpose == 'lesson_prep': return (draft(), draft())
        if purpose == 'lesson_review':
            return (lesson_prep.Review(verdict, ('原文依据不足',), None if verdict == 'live' else draft()),) * 2
        raise AssertionError(purpose)
    monkeypatch.setattr(classroom, '_prep_ask', fake_prep)
    monkeypatch.setattr(classroom, '_questions', lambda *args, **kwargs: [])
    course = classroom._course(PLAN)
    state = classroom._load(course)
    state['day'] = DAY.isoformat()
    classroom._prepare_later(course, state)
    for thread in threading.enumerate():
        if thread.name == 'classroom-lesson-prep': thread.join(5)
    assert classroom._prepared_point(course, course.point('p1')) is None
    assert store.load_lesson_cache(PLAN)['lesson_preps']['p1']['status'] == 'needs_live_teaching'


def test_failed_batch_falls_back_without_retry(room, monkeypatch):
    classroom, store = room
    count = 0
    def fake_prep(_course, purpose, _request, _parse, _max_tokens):
        nonlocal count
        if purpose == 'lesson_prep':
            count += 1
            raise RuntimeError('synthetic model failure')
        raise AssertionError(purpose)
    monkeypatch.setattr(classroom, '_prep_ask', fake_prep)
    monkeypatch.setattr(classroom, '_questions', lambda *args, **kwargs: [])
    course = classroom._course(PLAN)
    state = classroom._load(course); state['day'] = DAY.isoformat()
    classroom._prepare_later(course, state)
    for thread in threading.enumerate():
        if thread.name == 'classroom-lesson-prep': thread.join(5)
    assert store.load_lesson_cache(PLAN)['lesson_preps']['p1']['status'] == 'needs_live_teaching'
    assert store.load_lesson_cache(PLAN)['lesson_preps']['p2']['status'] == 'needs_live_teaching'
    assert count == 1


def test_prepared_question_is_persisted_as_the_actual_question(room, monkeypatch):
    classroom, store = room
    course = classroom._course(PLAN)
    state = classroom._load(course)
    state['day'] = DAY.isoformat()
    replies = iter(['讲解完毕。', '这一步还有问题吗？', '接下来按步骤继续。'])
    calls = []

    def complete(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(text=next(replies))

    classroom.services.gateway = SimpleNamespace(complete=complete)
    prepared = draft()
    teaching = classroom._teacher(course, state, None, '讲解队列。', ['p1'], asks=True, prepared=prepared)
    assert teaching.casual_question == prepared.casual_question
    messages = store.records('messages', PLAN)
    assert any(row['content'] == prepared.casual_question and row['kind'] == 'casual' for row in messages)

    # A later step that did not ask for a question rejects an unsolicited
    # trailing quiz and retries before saving the spoken text.
    classroom._teacher(course, state, None, '总结。', ['p1'], asks=False)
    assert len(calls) == 3
    contents = [row['content'] for row in store.records('messages', PLAN)]
    assert '接下来按步骤继续。' in contents
    assert '这一步还有问题吗？' not in contents


def test_direct_prepared_lesson_and_early_verdict_before_short_feedback(room):
    classroom, store = room
    course = classroom._course(PLAN)
    state = classroom._load(course)
    state['day'] = DAY.isoformat()
    for point_id in ('p1', 'p2'):
        point = course.point(point_id)
        store.save_lesson_cache_item(PLAN, 'lesson_preps', point_id,
            {'version': lesson_prep.VERSION, 'status': 'reviewed',
             'fingerprint': classroom._prep_fingerprint(course, point), 'lesson': asdict(draft())})
    calls, streamed = [], []

    def complete(purpose, _messages, **kwargs):
        calls.append(purpose)
        if purpose == 'judge':
            return SimpleNamespace(text=json.dumps({'kind': 'answer', 'correct': True,
                                                     'wrong_points': [], 'reason': '说出了先进先出'}, ensure_ascii=False))
        if purpose == 'teacher':
            assert streamed and streamed[0].startswith('\x01VERDICT:')
            assert kwargs['max_tokens'] == 4096  # includes the provider's hidden reasoning
            kwargs['on_text']('答得对，先到先服务。')
            return SimpleNamespace(text='答得对，先到先服务。')
        raise AssertionError(purpose)

    classroom.services.gateway = SimpleNamespace(complete=complete)
    ctx = SimpleNamespace(cancel=threading.Event(), stream=streamed.append)
    classroom._teach_point(course, state, ctx, '')
    assert calls == [] and state['pending']['question']['stem'] == draft().casual_question
    classroom._casual_reply(course, state, ctx, '先进入先取出。', state['pending'])
    assert calls == ['judge', 'teacher']
    assert state['step'] == 'answer' and state['pending']['point_ids'] == ['p2']
    messages = store.messages(PLAN, 'm1')
    assert sum(m['kind'] == 'teach' for m in messages) == 3  # two scripts and the one-sentence feedback
    assert any('mf-source:c1' in m['content'] for m in messages if m['kind'] == 'teach')
    assert next(i for i, m in enumerate(messages) if m['kind'] == 'verdict') < next(
        i for i, m in enumerate(messages) if m['content'] == '答得对，先到先服务。')


def test_profile_updates_after_module_and_leave_but_not_each_point(room, monkeypatch):
    classroom, store = room
    course = classroom._course(PLAN)
    state = classroom._load(course)
    state['day'] = DAY.isoformat()
    modes = []
    monkeypatch.setattr(classroom, '_profile_later', lambda _course, _state, mode: modes.append(mode))
    classroom._point_done(course, state)
    assert modes == []
    state['test'] = {'kind': 'module_test', 'attempt': 'synthetic', 'index': 0, 'queue': []}
    state['step'] = 'testing'
    classroom._save(course, state)
    classroom.run(PLAN, lambda _course, current, _ctx: current.update(test=None, step='test_done'))
    assert modes == ['module_complete']
    state['in_session'] = True
    classroom.leave(course, state, None)
    assert modes == ['module_complete', 'session_end']


def test_formal_point_grain_is_about_fifteen_minutes_without_plan_wide_cap():
    assert '15分钟' in planner_agents.GENERATE_SYSTEM
    aliases = {'c1': 'c1'}
    row = {'title': '完整任务', 'summary': '完成几个紧密相连的小要点', 'chunks': ['c1'], 'minutes': 15}
    parsed = planner_agents.parse_points(json.dumps({'points': [row]}, ensure_ascii=False), aliases,
                                          min_points=1, point_minutes_range=(10, 20))
    assert parsed[0]['minutes'] == 15
    with pytest.raises(AgentFormatError, match='point minutes'):
        planner_agents.parse_points(json.dumps({'points': [dict(row, minutes=5)]}, ensure_ascii=False), aliases,
                                    min_points=1, point_minutes_range=(10, 20))
    batches = [[dict(parsed[0], title=f'任务{i}', summary=f'掌握编号{i}的完整任务')]
               for i in range(31)]
    assert len(planner_agents.merge_point_batches(batches, [{'id': 'c1'}])) == 31
