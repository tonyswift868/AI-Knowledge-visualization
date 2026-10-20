"""188: slow classroom calls yield the plan lock and fence stale commits."""
import threading
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from refactor.learning.app.bridge import BridgeError
from refactor.learning.app.classroom import Classroom
from refactor.learning.course import teacher_agent
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore
from tests.refactor.integration38.support import Agents, seed_bank, context

ACCOUNT = 'mfa1_' + 'e' * 64
PLAN = 'b' * 32
DAY = date(2026, 10, 1)


@pytest.fixture
def env(tmp_path):
    store = CourseStore(ACCOUNT, tmp_path)
    store.create_plan(PLAN, goal='学习队列', final_goal='理解队列', level='beginner',
                      window=PlanWindow(DAY, DAY + timedelta(days=3), 120))
    store.add_sources(PLAN, [dict(id='source', url='https://example.test/lesson', adopted=True, chunks=[
        dict(chunk_id='c1', text='队列是先进先出的数据结构。'),
        dict(chunk_id='c2', text='栈是后进先出的数据结构。')])])
    store.save_outline(PLAN, [dict(id='stage', title='基础')], [
        dict(id='m0', stage_id='stage', title='队列'), dict(id='m1', stage_id='stage', title='栈')])
    store.save_generated_module(PLAN, 'm0', [dict(id='p0', title='队列', summary='先进先出', minutes=10, chunk_ids=['c1'])],
                                check_minutes=5, today=DAY)
    store.save_generated_module(PLAN, 'm1', [dict(id='p1', title='栈', summary='后进先出', minutes=10, chunk_ids=['c2'])],
                                check_minutes=5, today=DAY)
    seed_bank(store, PLAN, count=1)
    store.start_plan(PLAN, today=DAY)
    gateway = Agents()
    services = SimpleNamespace(store=store, gateway=gateway, account_id=ACCOUNT, data_root=tmp_path,
        settings=SimpleNamespace(load=lambda: dict(teacher_style='warm', detail='brief', examples='few',
                                                   analogy_domains=['life'], refresh_time='04:00')))
    room = Classroom(services, lambda: DAY, now=lambda: datetime(2026, 10, 1, 10, tzinfo=timezone.utc))
    room.perform(PLAN, 'enter', context())
    try:
        yield room, store
    finally:
        room.cancel_materials()
        store.close()


def _start_blocked_teacher(room, monkeypatch, *, release_event, entered_event, calls):
    def blocked(plan_id, tctx, request, cancel, emit, **kwargs):
        calls.append(request)
        emit('尚未完成的正文')
        entered_event.set()
        if len(calls) == 1:
            assert release_event.wait(3), 'test did not release fake teacher'
        emit('迟到正文')
        return teacher_agent.Teaching('老师回复已完成。', ('c1',), ('直接开始讲',)), request, '老师回复已完成。'
    monkeypatch.setattr(room, '_generate_teacher', blocked)


def _thread_call(fn):
    box = {}
    def worker():
        try: box['value'] = fn()
        except BaseException as exc: box['error'] = exc
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread, box


def test_slow_teacher_releases_lock_state_is_readable_and_leave_takes_over(env, monkeypatch):
    room, store = env
    entered, release = threading.Event(), threading.Event()
    calls, streamed = [], []
    _start_blocked_teacher(room, monkeypatch, release_event=release, entered_event=entered, calls=calls)
    thread, result = _thread_call(lambda: room.perform(PLAN, 'next', context(streamed)))
    assert entered.wait(2)

    # The blocked provider owns no room lock; polling returns the durable state.
    lock = room._lock(PLAN)
    assert lock.acquire(False)
    lock.release()
    before = time.monotonic()
    snapshot = room.run(PLAN, lambda *_: None, context(), readonly=True)
    assert time.monotonic() - before < 1
    assert snapshot['step'] == 'teach'

    left = room.perform(PLAN, 'leave', context())
    assert not room._load(room._course(PLAN))['in_session']
    release.set()
    thread.join(3)
    assert not thread.is_alive()
    assert isinstance(result.get('error'), BridgeError) and result['error'].code == 'CANCELLED'
    assert '迟到正文' not in streamed
    current = room.run(PLAN, lambda *_: None, context(), readonly=True)
    assert not room._load(room._course(PLAN))['in_session'] and current['step'] == 'teach'
    assert not store.records('credits', PLAN)


def test_new_reply_supersedes_old_teacher_without_late_message_or_position_save(env, monkeypatch):
    room, store = env
    # Move to the opening dialogue, then let the first reply block in the teacher.
    room.perform(PLAN, 'next', context())
    entered, release = threading.Event(), threading.Event()
    calls, streamed1, streamed2 = [], [], []
    _start_blocked_teacher(room, monkeypatch, release_event=release, entered_event=entered, calls=calls)
    t1, first = _thread_call(lambda: room.perform(PLAN, 'reply', context(streamed1), ('我先说说我的理解',)))
    assert entered.wait(2)
    second = room.perform(PLAN, 'reply', context(streamed2), ('我再补充一个问题',))
    assert second['step'] == 'teaching'
    release.set(); t1.join(3)
    assert not t1.is_alive()
    assert isinstance(first.get('error'), BridgeError) and first['error'].code == 'CANCELLED'
    assert '迟到正文' not in streamed1
    current = room.run(PLAN, lambda *_: None, context(), readonly=True)
    assert current['step'] == 'teaching'
    assert any(m['content'] == '老师回复已完成。' for m in current['messages'])
    assert sum(m['content'] == '老师回复已完成。' for m in current['messages']) == 1
    assert not store.records('credits', PLAN)


def test_second_classroom_instance_switches_module_and_fences_first_instance(env, monkeypatch):
    room, store = env
    entered, release = threading.Event(), threading.Event()
    calls, streamed = [], []
    _start_blocked_teacher(room, monkeypatch, release_event=release, entered_event=entered, calls=calls)
    t1, first = _thread_call(lambda: room.perform(PLAN, 'next', context(streamed)))
    assert entered.wait(2)

    other = Classroom(room.services, lambda: DAY, now=room.now)
    moved = other.perform(PLAN, 'next_module', context(), ('m1',))
    assert moved['module']['id'] == 'm1'
    release.set(); t1.join(3)
    assert not t1.is_alive()
    assert isinstance(first.get('error'), BridgeError) and first['error'].code == 'CANCELLED'
    current = other.run(PLAN, lambda *_: None, context(), readonly=True)
    assert current['module']['id'] == 'm1' and current['step'] == 'teach'
    assert '迟到正文' not in streamed
    assert not store.records('credits', PLAN)


def test_slow_point_preparation_also_yields_room_lock_and_cannot_save_after_switch(env, monkeypatch):
    room, store = env
    entered, release = threading.Event(), threading.Event()
    streamed = []
    class SlowMaterials:
        def prepare_point(self, *_args, **_kwargs):
            assert not store._lock._is_owned()
            entered.set()
            assert release.wait(3), 'test did not release fake preparation'
    monkeypatch.setattr(room, '_materials', lambda: SlowMaterials())
    thread, result = _thread_call(lambda: room.run(PLAN,
        lambda co, state, ctx: room._prepare_one(co, 'p0', ctx), context(streamed)))
    assert entered.wait(2)
    lock = room._lock(PLAN)
    assert lock.acquire(False)
    lock.release()
    moved = room.perform(PLAN, 'next_module', context(), ('m1',))
    assert moved['module']['id'] == 'm1'
    release.set(); thread.join(3)
    assert not thread.is_alive()
    assert isinstance(result.get('error'), BridgeError) and result['error'].code == 'CANCELLED'
    current = room.run(PLAN, lambda *_: None, context(), readonly=True)
    assert current['module']['id'] == 'm1'


def test_explicit_cancel_keeps_committed_checkpoint_saved_before_slow_call(env):
    room, _store = env
    entered, release = threading.Event(), threading.Event()
    request_context = context()
    def work(course, state, ctx):
        state['concept'] = dict(point_id='p0', subpoint_id='p0-s2', rounds=0)
        def blocked():
            entered.set()
            assert release.wait(3), 'test did not release fake worker'
        room._slow_call(course, ctx, blocked)
    thread, result = _thread_call(lambda: room.run(PLAN, work, request_context))
    assert entered.wait(2)
    request_context.cancel.set()
    release.set(); thread.join(3)
    assert not thread.is_alive()
    assert isinstance(result.get('error'), BridgeError) and result['error'].code == 'CANCELLED'
    saved = room._load(room._course(PLAN))
    assert saved.get('concept', {}).get('subpoint_id') == 'p0-s2'
