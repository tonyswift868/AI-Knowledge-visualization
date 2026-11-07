"""Session suspension fences shared material workers without persisting a stop."""
from datetime import date
import threading
import time

import pytest

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.app.services import AppServices
from refactor.learning.course.store import CourseStore
from .test_app_point_links82 import PLAN, seed


@pytest.fixture
def learning(tmp_path):
    store = CourseStore('account', tmp_path)
    seed(store)
    store.start_plan(PLAN, today=date(2026, 10, 8))
    services = AppServices(account_id='account', data_root=tmp_path, session_provider=lambda: None,
                           api_base_url='https://example.invalid', ca_bundle=None)
    services._store = store
    entered, release = threading.Event(), threading.Event()
    calls, summaries, completed = [], [], set()
    call_lock = threading.Lock()

    def prepare(_store, plan_id, module_id, _ctx, _progress):
        with call_lock:
            calls.append(module_id)
            first = len(calls) == 1
        if first:
            entered.set()
            assert release.wait(8)
        if not first:
            completed.add(module_id)
        return {}

    flow = PlanFlow(services, lambda *_: None, lambda: date(2026, 10, 8), prepare_module=prepare)
    flow._module_ready = lambda _plan_id, module_id: module_id in completed
    save_summary = flow._save_material_summary
    flow._save_material_summary = lambda plan_id, module_id, result: (
        summaries.append(module_id), save_summary(plan_id, module_id, result))
    modules = store.records('modules', PLAN)
    yield services, flow, store, modules, entered, release, calls, summaries
    release.set()
    for _ in range(100):
        with flow._material_jobs['guard']:
            active = bool(flow._material_jobs['active'])
        if not active:
            break
        time.sleep(.02)
    services.close()


def test_session_cancelled_inflight_and_tail_recover_once_without_late_write(learning):
    services, flow, store, modules, entered, release, calls, summaries = learning
    flow._prepare_remaining(PLAN, modules, flow._prepare_callable())
    assert entered.wait(3)
    state = flow._material_jobs
    with state['guard']:
        old = state['jobs'][(PLAN, modules[0]['id'])]
        tail = state['jobs'][(PLAN, modules[1]['id'])]
        assert state['queued'][PLAN][0][2] is tail

    assert services.suspend_learning() is True
    with state['guard']:
        assert old['session_cancelled'] and tail['session_cancelled']
        assert not state['queued']
    assert all((m.get('material_preparation') or {}).get('state') != 'stopped'
               for m in store.records('modules', PLAN))

    assert services.resume_learning() is True
    assert services.resume_learning() is False
    release.set()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        with state['guard']:
            active = bool(state['active'])
        if not active:
            break
        time.sleep(.02)

    assert not active
    assert calls.count(modules[0]['id']) == 2  # late old result discarded; one recovery run
    assert calls.count(modules[1]['id']) == 1
    assert summaries.count(modules[0]['id']) == 1
    assert summaries.count(modules[1]['id']) == 1
    assert all((m.get('material_preparation') or {}).get('state') == 'done'
               for m in store.records('modules', PLAN))


def test_suspended_materials_preserve_pending_without_retry_or_status_write(learning):
    services, flow, store, modules, entered, release, calls, summaries = learning
    flow._prepare_remaining(PLAN, modules, flow._prepare_callable())
    assert entered.wait(3)
    pending = store.pending_records(1000)
    durable = store.records('modules', PLAN)
    services.suspend_learning()
    assert flow._prepare_remaining(PLAN, modules, flow._prepare_callable()) is None
    release.set()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        with flow._material_jobs['guard']:
            active = bool(flow._material_jobs['active'])
        if not active:
            break
        time.sleep(.02)
    assert not active
    assert calls == [modules[0]['id']]
    assert summaries == []
    assert store.records('modules', PLAN) == durable
    assert store.pending_records(1000) == pending
