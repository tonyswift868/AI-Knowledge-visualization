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

    def fake_ask(_course, _ctx, purpose, _request, _parse, _max_tokens):
        calls.append(purpose)
        if purpose == 'lesson_prep': return draft()
        if purpose == 'lesson_review': return lesson_prep.Review('approve', (), None)
        if purpose == 'questions':
            return [Question(f'q{i}', 'short', f'解释点 {i}', (), '先进入先取出', '按顺序', (pid,), ('c1',))
                    for i, pid in enumerate(('p1', 'p2'))]
        raise AssertionError(purpose)

    monkeypatch.setattr(classroom, '_ask', fake_ask)
    course = classroom._course(PLAN)
    state = classroom._load(course)
    state['day'] = DAY.isoformat()
    classroom._prepare_later(course, state)
    for thread in threading.enumerate():
        if thread.name == 'classroom-lesson-prep': thread.join(5)
    assert calls.count('lesson_prep') == 2 and calls.count('lesson_review') == 2
    assert classroom._prepared_point(course, course.point('p1')) == draft()
    assert len(classroom._prepared_test(course, 'm1')) == 2
    # Concurrent profile update must preserve already-synced preparation.
    store.save_teacher_profile(PLAN, {'text': '新档案'})
    assert classroom._prepared_point(course, course.point('p1')) == draft()
    with store.transaction():
        changed = store._get('source_chunks', PLAN, 'c1')
        changed['text'] = '队列采用先进先出，但仅在正常出队时。'
        store._put('source_chunks', PLAN, changed)
    changed_course = classroom._course(PLAN)
    assert classroom._prepared_point(changed_course, changed_course.point('p1')) is None
    assert classroom._prepared_test(changed_course, 'm1') is None


@pytest.mark.parametrize('verdict', ['live', 'revise'])
def test_unverified_prep_never_becomes_teacher_basis(room, monkeypatch, verdict):
    classroom, store = room
    def fake_ask(_course, _ctx, purpose, _request, _parse, _max_tokens):
        if purpose == 'lesson_prep': return draft()
        if purpose == 'lesson_review':
            return lesson_prep.Review(verdict, ('原文依据不足',), None if verdict == 'live' else draft())
        if purpose == 'questions': return []
        raise AssertionError(purpose)
    monkeypatch.setattr(classroom, '_ask', fake_ask)
    course = classroom._course(PLAN)
    state = classroom._load(course)
    state['day'] = DAY.isoformat()
    classroom._prepare_later(course, state)
    for thread in threading.enumerate():
        if thread.name == 'classroom-lesson-prep': thread.join(5)
    assert classroom._prepared_point(course, course.point('p1')) is None
    assert store.load_lesson_cache(PLAN)['lesson_preps']['p1']['status'] == 'needs_live_teaching'


def test_one_failed_point_does_not_discard_other_prep(room, monkeypatch):
    classroom, store = room
    count = 0
    def fake_ask(_course, _ctx, purpose, _request, _parse, _max_tokens):
        nonlocal count
        if purpose == 'questions': return []
        if purpose == 'lesson_prep':
            count += 1
            if count == 1: raise RuntimeError('synthetic model failure')
            return draft()
        if purpose == 'lesson_review': return lesson_prep.Review('approve', (), None)
        raise AssertionError(purpose)
    monkeypatch.setattr(classroom, '_ask', fake_ask)
    course = classroom._course(PLAN)
    state = classroom._load(course); state['day'] = DAY.isoformat()
    classroom._prepare_later(course, state)
    for thread in threading.enumerate():
        if thread.name == 'classroom-lesson-prep': thread.join(5)
    assert store.load_lesson_cache(PLAN)['lesson_preps']['p1']['status'] == 'needs_live_teaching'
    assert classroom._prepared_point(course, course.point('p2')) == draft()


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
