"""Initial background projections; no credentials, models or network."""
import threading
import json
from types import SimpleNamespace

from refactor.learning.app.plan_flow import PlanFlow, register
from refactor.learning.course.store import CourseStore
from .test_app_point_links82 import seed, PLAN, DAY
from .test_app_point_minutes84 import Model, finish, flow
from .test_app_window_layer import qapp, make_bridge, pump  # noqa: F401


def test_status88_open_during_minutes_then_ready_and_shared_flow(tmp_path):
    store = CourseStore('offline', tmp_path)
    seed(store)
    gate, entered = threading.Event(), threading.Event()
    events = []
    model = Model(gate=gate)
    snapshot = store.point_minutes_snapshot
    caller = threading.get_ident()
    def background_snapshot(plan_id):
        assert threading.get_ident() != caller, 'detail scanned source content on caller'
        entered.set()
        return snapshot(plan_id)
    store.point_minutes_snapshot = background_snapshot
    f = flow(store, model, events)
    try:
        assert f.detail(PLAN)['point_minutes_status'] == 'running'
        assert entered.wait(1)
        other = PlanFlow(f.services, lambda *args: None, lambda: DAY)
        assert other.detail(PLAN)['point_minutes_status'] == 'running'
        gate.set(); finish(f)
        assert f.detail(PLAN)['point_minutes_status'] == 'ready'
        assert len(model.calls) == 2
        assert ('plan.point_minutes_changed', dict(plan_id=PLAN, status='ready')) in events
        # Changes to model input invalidate the read cache, not the persisted minutes.
        store.save_module_preparation(PLAN, 'm0', dict(state='done'))
        f.detail(PLAN); finish(f)
        assert f.detail(PLAN)['point_minutes_status'] == 'ready'
        assert len(model.calls) == 2
    finally:
        gate.set(); finish(f); store.close()


def test_status88_failure_visible_until_explicit_retry_and_bridge_registered(tmp_path):
    store = CourseStore('offline', tmp_path)
    seed(store)
    f = flow(store, Model(fail=True), [])
    try:
        f.detail(PLAN); finish(f)
        assert f.detail(PLAN)['point_minutes_status'] == 'failed'
        assert not f._point_minute_jobs['jobs']
        f.services.gateway.fail = False
        assert f.retry_point_minutes(PLAN)['status'] == 'running'
        finish(f)
        assert f.detail(PLAN)['point_minutes_status'] == 'ready'
        methods = {}
        register(SimpleNamespace(register=lambda name, fn, **kwargs: methods.setdefault(name, fn)),
                 lambda: f.services, lambda *args: None, lambda: DAY)
        assert 'plan.retry_point_minutes' in methods
    finally:
        finish(f); store.close()


def test_status88_preparation_queue_completion_failure_and_stale_running(tmp_path):
    store = CourseStore('offline', tmp_path)
    seed(store)
    f = flow(store, Model(), [])
    try:
        modules = store.records('modules', PLAN)
        assert f.preparation_status(PLAN, modules)['status'] == 'none'
        f._material_jobs['jobs'][(PLAN, 'm0')] = dict(status='running', cancel=threading.Event())
        f._material_jobs['jobs'][(PLAN, 'm1')] = dict(status='queued', cancel=threading.Event())
        assert f.detail(PLAN)['preparation'] == dict(status='running', done=0, failed=0, total=2)
        finish(f)
        f._material_jobs['jobs'].clear()
        for mid in ('m0', 'm1'): store.save_module_preparation(PLAN, mid, dict(state='done'))
        assert f.detail(PLAN)['preparation_status'] == 'ready'
        finish(f)
        store.save_module_preparation(PLAN, 'm1', dict(state='failed'))
        assert f.detail(PLAN)['preparation_status'] == 'partial'
        finish(f)
        store.save_module_preparation(PLAN, 'm0', dict(state='failed'))
        assert f.detail(PLAN)['preparation_status'] == 'failed'
        finish(f)
        for mid in ('m0', 'm1'): store.save_module_preparation(PLAN, mid, dict(state='running'))
        assert f.detail(PLAN)['preparation_status'] == 'none', 'a crashed process cannot still be running'
    finally:
        finish(f); store.close()


def test_status88_real_bridge_initial_state_and_retry(qapp, tmp_path):
    store = CourseStore('offline', tmp_path)
    seed(store)
    bridge, sent = make_bridge(qapp)
    gate = threading.Event()
    services = SimpleNamespace(store=store, gateway=Model(gate=gate), _closed=False,
                               settings=SimpleNamespace(load=lambda: {}))
    register(bridge, lambda: services, None, lambda *_: DAY)
    try:
        bridge.call(json.dumps(dict(id='open', method='plan.detail', params=dict(plan_id=PLAN))))
        result = pump(qapp, bridge, sent, lambda m: m.get('id') == 'open')[0]
        assert result['ok'] and result['result']['point_minutes_status'] == 'running'
        gate.set()
        pump(qapp, bridge, sent, lambda m: m.get('name') == 'plan.point_minutes_changed' and m['data']['status'] == 'ready')
        bridge.call(json.dumps(dict(id='retry', method='plan.retry_point_minutes', params=dict(plan_id=PLAN))))
        assert pump(qapp, bridge, sent, lambda m: m.get('id') == 'retry')[0]['ok']
    finally:
        gate.set()
        finish(services._material_flow)
        bridge.close(); store.close()
