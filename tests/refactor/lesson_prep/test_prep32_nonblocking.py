"""Slow preparation must never hold a classroom bridge request open."""
from datetime import timedelta
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from refactor.learning.app.classroom import Classroom
from refactor.learning.connections.errors import ModelError
from tests.refactor.guided_lessons.test_guided_classroom_contract import env, PLAN, DAY, Gateway


def bridge(room, name, *args):
    started = time.perf_counter()
    view = room.run(PLAN, lambda co, st, cx: getattr(room, name)(co, st, cx, *args),
                    SimpleNamespace(cancel=threading.Event(), stream=lambda _: None))
    seconds = time.perf_counter() - started
    assert seconds < 1, (name, seconds)
    return view, seconds


def state(room):
    started = time.perf_counter()
    view = room.run(PLAN, lambda *a: None)
    assert time.perf_counter() - started < 1, 'state/event refresh waited for preparation'
    return view


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'background did not finish'
        time.sleep(.005)


def finish(room):
    wait_for(lambda: not room._lesson_wait_jobs and not room._preparing)


def isolated_room(services):
    room = Classroom(services, lambda: DAY)
    room._prepare_later = lambda *a: None
    room._prefetch_banks = lambda *a, **k: None
    room._schedule_idle_prep = lambda *a: None
    return room


def test_waiting_point_joins_batch_reopens_without_duplicate_and_resumes_from_event(env):
    e = env
    entered, release = threading.Event(), threading.Event()
    original = e.gateway.complete
    def slow(purpose, messages, **kwargs):
        if purpose == 'lesson_prep':
            entered.set()
            assert release.wait(5)
        return original(purpose, messages, **kwargs)
    e.gateway.complete = slow
    co = e.room._course(PLAN)
    batch = threading.Thread(target=e.room._prepare_batch,
                             args=(co, co.points[:2], {p['id'] for p in co.points}))
    batch.start()
    assert entered.wait(2)
    events = []
    e.room.lesson_event = events.append
    try:
        bridge(e.room, 'enter')
        view, elapsed = bridge(e.room, 'next')
        assert view['step'] == 'preparing_lesson' and view['lesson'] is None
        assert view['lesson_wait']['point_id'] == 'p00'
        e.room.now = lambda: __import__('datetime').datetime.fromisoformat(
            e.room._file(PLAN).read({})['lesson_wait']['started_at']) + timedelta(seconds=12)
        assert state(e.room)['lesson_wait']['elapsed_seconds'] == 12
        view, _ = bridge(e.room, 'reply', '午饭聊一句')
        assert view['step'] == 'preparing_lesson'
        # No point lock spans the paused model call.
        lock = e.room._prep_lock(co, co.point('p00'))
        assert lock.acquire(blocking=False)
        lock.release()
        reopened = isolated_room(e.services)
        reopened.lesson_event = events.append
        view, _ = bridge(reopened, 'enter')
        assert view['step'] == 'preparing_lesson'
        release.set(); batch.join(5)
        finish(e.room); finish(reopened)
        assert len(events) == 2 and all(v['point_id'] == 'p00' for v in events)
        # This is the page's event reaction, not another Continue operation.
        view = state(reopened)
        assert view['lesson']['step'] == 1 and view['step'] == 'lesson'
        state(e.room)
        rows = e.store.load_lesson_cache(PLAN)['lesson_preps']
        assert rows['p00']['generation_attempts'] == rows['p00']['total_reviews'] == 1
        assert e.gateway.calls.count('lesson_prep') == 1
        assert len([m for m in view['messages'] if m['kind'] == 'step']) == 1
    finally:
        release.set(); batch.join(5); finish(e.room)


def test_unrelated_batch_does_not_queue_urgent_point(env):
    e = env
    blocked, release, urgent = threading.Event(), threading.Event(), threading.Event()
    original = e.gateway.complete
    def slow(purpose, messages, **kwargs):
        if purpose == 'lesson_prep':
            task = json.loads(messages[-1]['content'].split('\n', 1)[1])
            if 'points' in task:
                blocked.set(); assert release.wait(5)
            else:
                urgent.set()
        return original(purpose, messages, **kwargs)
    e.gateway.complete = slow
    co = e.room._course(PLAN)
    batch = threading.Thread(target=e.room._prepare_batch,
                             args=(co, co.points[:2], {p['id'] for p in co.points}))
    batch.start(); assert blocked.wait(2)
    try:
        e.room.run(PLAN, lambda co, st, cx: st.update(index=2))
        view, seconds = bridge(e.room, 'next')
        assert seconds < 1
        assert urgent.wait(1), 'urgent point stood behind unrelated batch'
        finish(e.room)
        assert not release.is_set() and state(e.room)['lesson']['point_id'] == 'p02'
    finally:
        release.set(); batch.join(5)


def test_fast_learner_catches_slow_background_all_bridge_calls_return_under_one_second(env):
    e = env
    original = e.gateway.complete
    def slow(purpose, messages, **kwargs):
        if purpose in ('lesson_prep', 'lesson_review'):
            time.sleep(.25)
        return original(purpose, messages, **kwargs)
    e.gateway.complete = slow
    e.room._prepare_later = Classroom._prepare_later.__get__(e.room)
    events, timings = [], []
    e.room.lesson_event = events.append
    view, seconds = bridge(e.room, 'enter'); timings.append(dict(method='enter', seconds=seconds))
    for _ in range(40):
        if view['step'] == 'test_offer': break
        if view['step'] == 'preparing_lesson':
            wait_for(lambda: bool(events))
            events.clear()
            view = state(e.room)
            continue
        name, args = ('reply', (view['question']['options'][0],)) if view['step'] == 'answer' else (
            ('choose', (0,)) if (view.get('lesson') or {}).get('awaiting') == 'choice' else ('next', ()))
        view, seconds = bridge(e.room, name, *args)
        timings.append(dict(method=name, seconds=seconds, step=view['step']))
    assert view['step'] == 'test_offer'
    finish(e.room)
    rows = e.store.load_lesson_cache(PLAN)['lesson_preps']
    assert all(r['generation_attempts'] == 1 and r['total_reviews'] == 1 for r in rows.values())
    assert len(e.store.records('judgements', PLAN)) == 4
    if os.environ.get('MF32_ARTIFACTS'):
        Path(os.environ['MF32_ARTIFACTS'], 'bridge-timings.json').write_text(
            json.dumps(timings, ensure_ascii=False, indent=2), encoding='utf-8')


def test_failure_is_visible_and_explicit_retry_preserves_position_and_counters(env):
    e = env
    events = []; e.room.lesson_event = events.append
    original = e.gateway.complete
    def fail(*a, **k): raise RuntimeError('synthetic transport outage')
    e.gateway.complete = fail
    view, _ = bridge(e.room, 'next')
    wait_for(lambda: bool(events)); finish(e.room)
    view = state(e.room)
    assert view['step'] == 'preparing_lesson' and view['lesson_wait']['error']
    row = e.store.load_lesson_cache(PLAN)['lesson_preps']['p00']
    assert row['generation_attempts'] == 1
    # Reads and reopen do not automatically retry failures.
    reopened = isolated_room(e.services)
    bridge(reopened, 'enter'); state(reopened)
    assert not reopened._lesson_wait_jobs
    e.gateway.complete = original
    bridge(reopened, 'next'); finish(reopened)
    view = state(reopened)
    assert view['lesson']['step'] == 1
    assert e.store.load_lesson_cache(PLAN)['lesson_preps']['p00']['generation_attempts'] == 2


def test_leave_and_material_change_do_not_install_late_stale_lesson(env):
    e = env
    started, release = threading.Event(), threading.Event()
    original = e.gateway.complete
    def slow(*args, **kwargs):
        if args[0] == 'lesson_prep': started.set(); assert release.wait(5)
        return original(*args, **kwargs)
    e.gateway.complete = slow
    bridge(e.room, 'enter'); bridge(e.room, 'next'); assert started.wait(2)
    bridge(e.room, 'leave')
    # Changing the style is part of the existing material/prefs fingerprint.
    e.prefs['detail'] = 'detailed'
    release.set(); finish(e.room)
    view = state(e.room)
    assert view['step'] == 'teach' and view['lesson'] is None
    assert not [m for m in view['messages'] if m['kind'] == 'step']


def test_terminal_live_fallback_is_also_async_and_only_marked_when_shown(env):
    e = env
    co = e.room._course(PLAN); point = co.point('p00')
    e.gateway.reviews = ['live', 'live']
    e.room._prepare_point(co, point, {p['id'] for p in co.points})
    assert e.room._prep_row(co, point)['status'] == 'needs_live_teaching'
    original = e.gateway.complete
    def slow(*args, **kwargs):
        if args[0] == 'teacher': time.sleep(.25)
        return original(*args, **kwargs)
    e.gateway.complete = slow
    view, seconds = bridge(e.room, 'next')
    assert view['step'] == 'preparing_lesson'
    finish(e.room)
    assert not e.room._prep_row(co, point)['live_used']
    view = state(e.room)
    assert view['lesson']['total'] == 2 and e.room._prep_row(co, point)['live_used']


def test_legacy_pending_card_without_lesson_resumes_second_reference_after_async_prep(env):
    from tests.refactor.guided_lessons.fixtures import draft
    e = env
    def legacy(co, st, cx):
        st['lesson'] = dict(point_id='p00')
        e.room._casual_card(co, st, draft('k1').questions[0], asks=1)
        st.pop('lesson')
    e.room.run(PLAN, legacy)
    original = e.gateway.complete
    def slow(*args, **kwargs):
        if args[0] == 'lesson_prep': time.sleep(.1)
        return original(*args, **kwargs)
    e.gateway.complete = slow
    view, _ = bridge(e.room, 'reply', '随机')
    assert view['step'] == 'preparing_lesson'
    finish(e.room)
    view = state(e.room)
    assert view['step'] == 'answer' and view['question']['stem'] == draft('k1').questions[1].stem
    pending = e.room._file(PLAN).read({})['pending']
    assert pending['asks'] == 2 and pending['question']['reference'] == '甲先取出'
    assert len(e.store.records('judgements', PLAN)) == 1
