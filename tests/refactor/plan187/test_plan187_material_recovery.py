import json
import threading
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from refactor.learning.app.plan_flow import PlanFlow, register
from refactor.learning.connections.errors import ModelError
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore, StoreError

ACCOUNT = 'mfa1_' + '7' * 64
PLAN = '7' * 32
DAY = date(2026, 10, 20)


def wait_for(predicate, timeout=3):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        time.sleep(.005)
    assert predicate()


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Recovery tests must run in daytime too; the real schedule is tested below.
    monkeypatch.setattr(PlanFlow, '_material_peak_now', staticmethod(lambda now=None: False))
    store = CourseStore(ACCOUNT, tmp_path)
    store.create_plan(PLAN, goal='后台备题恢复', final_goal='理解队列', level='beginner',
                      window=PlanWindow(DAY, DAY + timedelta(days=10), 120))
    store.add_sources(PLAN, [dict(id='src', kind='url', url='https://example.test/book',
        title='示例资料', adopted=True, chunks=[dict(chunk_id='chunk', text='队列先进先出。', section=['队列'])])])
    store.save_outline(PLAN, [dict(id='stage', title='阶段')], [
        dict(id=f'm{i}', stage_id='stage', title=f'模块{i}', position=i) for i in range(4)])
    for i in range(4):
        store.save_generated_module(PLAN, f'm{i}', [dict(id=f'p{i}', title=f'知识点{i}',
            summary='掌握队列规则', minutes=5, chunk_ids=['chunk'])], check_minutes=2, today=DAY)
    store.start_plan(PLAN, today=DAY)
    services = SimpleNamespace(store=store, gateway=None, account_id=ACCOUNT, data_root=tmp_path,
        _closed=False, settings=SimpleNamespace(load=lambda: dict(refresh_time='04:00')))
    flow = PlanFlow(services, lambda *_: None, lambda: DAY)
    monkeypatch.setattr(flow, '_material_retry_delay', lambda _: 0)
    ready = set()
    monkeypatch.setattr(flow, '_module_ready', lambda _pid, mid: mid in ready)
    yield SimpleNamespace(store=store, services=services, flow=flow, ready=ready)
    services._closed = True
    wait_for(lambda: PLAN not in flow._preparing)
    store.close()


def test_plan187_transient_disconnect_retries_and_persists_terminal_state(env):
    calls = []

    def prepare(_store, _pid, mid, _ctx, _progress):
        calls.append(mid)
        if len(calls) < 3:
            raise ModelError('MODEL_CONNECTION_FAILED', '网络暂时中断。')
        env.ready.add(mid)
        return {}

    module = dict(env.store.records('modules', PLAN)[0], points=[dict(id='p0')])
    env.flow._prepare_remaining(PLAN, [module], prepare)
    wait_for(lambda: PLAN not in env.flow._preparing)
    saved = env.store.records('modules', PLAN)[0]['material_preparation']
    assert calls == ['m0', 'm0', 'm0']
    assert saved['state'] == 'done' and saved['attempts'] == 3
    assert saved['error'] is None and saved['error_code'] is None


def test_plan187_explicit_stop_wins_race_with_worker_done_commit(env, monkeypatch):
    module = dict(env.store.records('modules', PLAN)[0], points=[dict(id='p0')])
    flow = env.flow
    original_record = flow._record_preparation
    inside_done = threading.Event()
    allow_done = threading.Event()
    stop_finished = threading.Event()

    def record(*args, **kwargs):
        if len(args) >= 3 and args[2] == 'done':
            # Let the underlying conditional write observe the stopped state,
            # then delay returning that stale result to the worker until resume
            # has persisted done. This exercises the hardest completion race.
            saved = original_record(*args, **kwargs)
            inside_done.set()
            assert allow_done.wait(2)
            return saved
        return original_record(*args, **kwargs)

    monkeypatch.setattr(flow, '_record_preparation', record)
    def prepare(*_args):
        calls.append('prepare')
        env.ready.add('m0')
        return {}
    calls = []
    flow._prepare_remaining(PLAN, [module], prepare)
    assert inside_done.wait(2)
    stopper = threading.Thread(target=lambda: (flow.stop_background(PLAN, 'm0'), stop_finished.set()))
    stopper.start()
    # Stop may persist while the worker is between its last cancellation check
    # and the conditional done commit. The store guard must keep stopped final.
    assert stop_finished.wait(2)
    assert flow.resume_background(PLAN, 'm0') == {'queued': 0}
    resumed = env.store.records('modules', PLAN)[0]['material_preparation']
    assert resumed['state'] == 'done'
    job = flow._material_jobs['jobs'][(PLAN, 'm0')]
    assert job['status'] == 'ready' and not job['cancel'].is_set()
    assert job['explicit_stop'] is False
    allow_done.set()
    stopper.join(2)
    wait_for(lambda: PLAN not in flow._preparing)
    saved = env.store.records('modules', PLAN)[0]['material_preparation']
    assert saved['state'] == 'done'
    assert flow._material_jobs['jobs'][(PLAN, 'm0')]['status'] == 'ready'
    # Explicit resume after the worker has exited is also an idempotent
    # acknowledgement; it neither invokes preparation nor queues a duplicate.
    assert flow.resume_background(PLAN, 'm0') == {'queued': 0}
    assert calls == ['prepare']


def test_plan187_summary_merge_preserves_durable_stop_and_attempts(env):
    env.store.update_module_preparation_state(PLAN, 'm0', 'stopped', attempts=2,
        error='由用户停止', error_code='USER_STOPPED')

    env.flow._save_material_summary(PLAN, 'm0', {
        'points': 5, 'cloze_points': 2, 'cloze_heavy': False,
    })

    saved = env.store.records('modules', PLAN)[0]['material_preparation']
    assert saved['state'] == 'stopped'
    assert saved['attempts'] == 2
    assert saved['error'] == '由用户停止'
    assert saved['error_code'] == 'USER_STOPPED'
    assert saved['points'] == 5
    assert saved['cloze_points'] == 2
    assert saved['cloze_heavy'] is False


def test_plan187_recovery_repairs_durable_state_when_outputs_are_already_ready(env, monkeypatch):
    for mid in ('m0', 'm1', 'm2', 'm3'):
        env.ready.add(mid)
        env.store.update_module_preparation_state(PLAN, mid, 'done', attempts=1)
    env.store.update_module_preparation_state(PLAN, 'm0', 'failed', attempts=3,
        error='最后状态写入失败', error_code='STORE_WRITE_FAILED')
    stale_job = dict(module_id='m0', status='failed', done=1, total=1,
        text='备题失败', error='最后状态写入失败', error_code='STORE_WRITE_FAILED',
        cancel=threading.Event(), explicit_stop=False)
    env.flow._material_jobs['jobs'][(PLAN, 'm0')] = stale_job
    calls = []
    events = []
    monkeypatch.setattr(env.flow, '_prepare_callable', lambda: lambda *_: calls.append(1))
    monkeypatch.setattr(env.flow, 'push', lambda name, payload: events.append((name, payload)))

    assert env.flow.resume_started_preparations() == 0
    assert len(events) == 1
    assert events[0][0] == 'plan_preparation'
    assert events[0][1]['module_id'] == 'm0' and events[0][1]['status'] == 'ready'

    saved = env.store.records('modules', PLAN)[0]['material_preparation']
    assert saved['state'] == 'done'
    assert saved['error'] is None and saved['error_code'] is None
    assert stale_job['status'] == 'ready'
    assert stale_job['error'] is None and stale_job['error_code'] is None
    assert calls == []
    assert env.flow.resume_started_preparations() == 0
    assert len(events) == 1


def test_plan187_worker_record_never_holds_queue_guard_while_waiting_for_plan_lock(env, monkeypatch):
    module = dict(env.store.records('modules', PLAN)[0], points=[dict(id='p0')])
    record_started = threading.Event()
    original_record = env.flow._record_preparation

    def record(*args, **kwargs):
        if len(args) >= 3 and args[2] == 'running':
            record_started.set()
        return original_record(*args, **kwargs)

    monkeypatch.setattr(env.flow, '_record_preparation', record)
    def prepare(*_args):
        env.ready.add('m0')
        return {}

    # This models the foreground generate path: it owns the per-plan lock while
    # starting background work, then still needs the queue guard before returning.
    with env.flow._lock(PLAN):
        env.flow._prepare_remaining(PLAN, [module], prepare)
        assert record_started.wait(2)
        guard = env.flow._material_jobs['guard']
        assert guard.acquire(timeout=1), 'worker held the queue guard while waiting for the plan lock'
        guard.release()
    wait_for(lambda: PLAN not in env.flow._preparing)
    assert env.store.records('modules', PLAN)[0]['material_preparation']['state'] == 'done'


def test_plan187_stop_all_preserves_ready_modules(env, monkeypatch):
    env.ready.add('m0')
    env.store.update_module_preparation_state(PLAN, 'm0', 'done', fingerprint='current')
    monkeypatch.setattr(env.flow, '_module_ready', lambda _pid, mid: mid in env.ready)
    result = env.flow.stop_background(PLAN)
    assert result == {'stopped': 3}
    summaries = [m['material_preparation'] for m in env.store.records('modules', PLAN)]
    assert summaries[0]['state'] == 'done'
    assert [summary['state'] for summary in summaries[1:]] == ['stopped'] * 3


def test_plan187_permanent_missing_source_is_not_retried(env):
    calls = []

    def prepare(*_args):
        calls.append(1)
        raise StoreError('POINT_SOURCE_MISSING', '资料缺少对应原文。')

    module = dict(env.store.records('modules', PLAN)[0], points=[dict(id='p0')])
    env.flow._prepare_remaining(PLAN, [module], prepare)
    wait_for(lambda: PLAN not in env.flow._preparing)
    saved = env.store.records('modules', PLAN)[0]['material_preparation']
    assert len(calls) == 1
    assert saved['state'] == 'failed' and saved['error_code'] == 'POINT_SOURCE_MISSING'
    assert '资料缺少' in saved['error']


def test_plan187_ready_event_resumes_started_plan_once_and_stop_is_durable(env, monkeypatch):
    class Signal:
        def __init__(self): self.callbacks = []
        def connect(self, callback): self.callbacks.append(callback)
        def emit(self, value=None):
            for callback in tuple(self.callbacks): callback(value)

    class Bridge:
        def __init__(self): self.event, self.closing = Signal(), Signal(); self.handlers = {}; self.pushed = []
        def register(self, name, fn, **_kwargs): self.handlers[name] = fn
        def push(self, name, data=None): self.pushed.append((name, data))

    resumed = threading.Event()
    seen = []
    original_resume = PlanFlow.resume_started_preparations
    def resume(flow):
        seen.append(flow.services)
        resumed.set()
        return 0
    monkeypatch.setattr(PlanFlow, 'resume_started_preparations', resume)
    current = [None]
    bridge = Bridge()
    register(bridge, lambda: current[0], object(), lambda _settings: DAY)
    assert not seen
    current[0] = env.services
    event = json.dumps({'type': 'event', 'name': 'ready', 'data': {'account': {'account_id': ACCOUNT}}})
    bridge.event.emit(event)
    bridge.event.emit(event)
    assert resumed.wait(2)
    wait_for(lambda: len(seen) == 1)
    assert seen == [env.services]

    registered_flow = env.services._material_flow
    result = bridge.handlers['plan.stop_preparation']({'plan_id': PLAN, 'module_id': 'm1'}, None)
    assert result == {'stopped': 1}
    assert env.store.records('modules', PLAN)[1]['material_preparation']['state'] == 'stopped'
    # Classroom priority/retry after an ordinary enter is not an explicit resume.
    module = dict(env.store.records('modules', PLAN)[1], points=[dict(id='p1')])
    registered_flow._prepare_remaining(PLAN, [module], lambda *_args: pytest.fail('stopped job resumed'),
                                        retry=True, priority=True)
    assert env.store.records('modules', PLAN)[1]['material_preparation']['state'] == 'stopped'
    # A fresh recovery pass does not automatically revive an explicitly stopped module.
    env.ready.update({'m0', 'm2', 'm3'})
    monkeypatch.setattr(registered_flow, '_module_ready', lambda _pid, mid: mid in env.ready)
    assert original_resume(registered_flow) == 0
    assert PLAN not in env.flow._preparing
    calls = []
    def explicit_prepare(_store, _pid, mid, _ctx, _progress):
        calls.append(mid); env.ready.add(mid); return {}
    monkeypatch.setattr(registered_flow, '_prepare_callable', lambda: explicit_prepare)
    assert bridge.handlers['plan.resume_preparation']({'plan_id': PLAN, 'module_id': 'm1'}, None) == {'queued': 1}
    wait_for(lambda: PLAN not in env.flow._preparing)
    assert calls == ['m1']
    assert env.store.records('modules', PLAN)[1]['material_preparation']['state'] == 'done'


def test_plan187_direct_classroom_plan_switch_retries_once_per_return(env, monkeypatch):
    other_plan = '8' * 32
    env.store.create_plan(other_plan, goal='切换计划',
                          window=PlanWindow(DAY, DAY + timedelta(days=10), 120))
    env.store.save_outline(other_plan, [dict(id='stage', title='阶段')],
                           [dict(id='m0', stage_id='stage', title='模块')])
    resumed = []

    def resume(_flow, plan_id):
        resumed.append(plan_id)
        return 1

    monkeypatch.setattr(PlanFlow, '_resume_plan_preparations', resume)
    monkeypatch.setattr(env.flow, '_module_ready', lambda _pid, _mid: True)
    module = dict(env.store.records('modules', PLAN)[0], points=[])
    # This is the direct classroom path: no preceding plan.detail action.
    env.flow._prepare_remaining(PLAN, [module], lambda *_args: {}, priority=True)
    env.flow._prepare_remaining(PLAN, [module], lambda *_args: {}, priority=True)
    env.flow._prepare_remaining(other_plan, [dict(id='m0', points=[])], lambda *_args: {}, priority=True)
    wait_for(lambda: len(resumed) == 1)
    env.flow._prepare_remaining(other_plan, [dict(id='m0', points=[])], lambda *_args: {}, priority=True)
    env.flow._prepare_remaining(PLAN, [module], lambda *_args: {}, priority=True)
    wait_for(lambda: len(resumed) == 2)
    assert resumed == [other_plan, PLAN]


def test_plan187_detail_refresh_does_not_recover_but_switch_back_does(env, monkeypatch):
    class Signal:
        def connect(self, callback): self.callback = callback

    class Bridge:
        def __init__(self): self.event, self.closing = Signal(), Signal(); self.handlers = {}
        def register(self, name, fn, **_kwargs): self.handlers[name] = fn
        def push(self, *_args): pass

    current = [None]
    bridge = Bridge()
    resumed = []
    def resume(_flow, plan_id): resumed.append(plan_id)
    monkeypatch.setattr(PlanFlow, '_resume_plan_preparations', resume)
    monkeypatch.setattr(PlanFlow, 'detail', lambda _flow, plan_id: {'plan_id': plan_id})
    register(bridge, lambda: current[0], object(), lambda _settings: DAY)
    current[0] = env.services  # services become ready only after registration
    call = bridge.handlers['plan.detail']
    call({'plan_id': PLAN}, None)
    call({'plan_id': PLAN}, None)  # same-plan detail refresh is not a retry trigger
    call({'plan_id': '8' * 32}, None)
    call({'plan_id': PLAN}, None)  # returning to the original plan triggers once
    wait_for(lambda: len(resumed) == 2)
    assert resumed == ['8' * 32, PLAN]


def test_plan187_resume_requeues_failed_interrupted_and_fingerprint_stale_but_not_stopped(env):
    for mid, state in [('m0', 'done'), ('m1', 'failed'), ('m2', 'running'), ('m3', 'stopped')]:
        env.store.update_module_preparation_state(PLAN, mid, state, error='old failure' if state == 'failed' else None)
    env.store.save_classroom_position(PLAN, {'module_id': 'm2', 'step': 'teach'})
    calls = []
    events = []
    env.flow.push = lambda name, payload: events.append((name, payload))

    def prepare(_store, _pid, mid, _ctx, _progress):
        calls.append(mid)
        env.ready.add(mid)
        return {}

    env.flow._prepare_callable = lambda: prepare
    assert env.flow.resume_started_preparations() == 3
    wait_for(lambda: PLAN not in env.flow._preparing)
    assert calls[0] == 'm2'  # Current classroom module enters the queue head.
    assert set(calls) == {'m0', 'm1', 'm2'}
    assert env.store.records('modules', PLAN)[3]['material_preparation']['state'] == 'stopped'
    assert all(env.store.records('modules', PLAN)[i]['material_preparation']['state'] == 'done'
               for i in range(3))
    assert not any(name == 'plan_preparation' and payload['module_id'] == 'm3'
                   for name, payload in events)


def test_plan187_peak_windows_use_existing_weekday_schedule():
    assert PlanFlow._material_peak_now(datetime(2026, 10, 21, 10, 0))
    assert PlanFlow._material_peak_now(datetime(2026, 10, 21, 15, 0))
    assert not PlanFlow._material_peak_now(datetime(2026, 10, 21, 13, 0))
    assert not PlanFlow._material_peak_now(datetime(2026, 10, 24, 10, 0))
    assert PlanFlow._material_off_peak_delay(datetime(2026, 10, 21, 10, 0)) == 7200
