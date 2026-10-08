"""Point pairing, durable sync and real bridge contract; no real model/network."""
from datetime import date, timedelta
import json
import threading
from types import SimpleNamespace

import pytest

from refactor.learning.app.plan_flow import PlanFlow, register
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore
from refactor.learning.course import planner_agents as agents
from .test_app_window_layer import qapp, make_bridge, pump  # noqa: F401

PLAN = 'a' * 32
DAY = date(2026, 10, 8)


def seed(store):
    store.create_plan(PLAN, goal='关系', window=PlanWindow(DAY, DAY + timedelta(days=10), 180))
    store.add_sources(PLAN, [dict(id='s', kind='url', url='https://example.invalid', adopted=True,
        chunks=[dict(chunk_id='c', text='权威正文', page_start=None, page_end=None, section=[])])])
    store.save_outline(PLAN, [dict(id='stage', title='阶段')], [dict(id=m, stage_id='stage', title=m,
        prerequisites=['m0'] if m == 'm1' else []) for m in ('m0', 'm1')])
    for m in ('m0', 'm1'):
        store.save_generated_module(PLAN, m, [dict(id=m + str(i), title=f'{m}点{i}', summary=f'说明{i}',
            minutes=15, chunk_ids=['c']) for i in range(6)], today=DAY, check_minutes=0)


@pytest.fixture
def store(tmp_path):
    s = CourseStore('account', tmp_path)
    seed(s)
    yield s
    s.close()


def validate(store, rows, module='m1', existing=()):
    state = store.point_links_state(PLAN)
    m = next(m for m in state['modules'] if m['id'] == module)
    return agents.validate_point_links(rows, {p['id']: p['id'] for p in state['points']}, m, state['points'], existing)


def edge(a, b, kind='prerequisite'):
    return {'from': a, 'to': b, 'kind': kind}


@pytest.mark.parametrize('row,reason', [
    (edge('missing', 'm10'), 'unknown'), (edge('m10', 'm10'), 'self'),
    (edge('m10', 'm00'), 'scope'), (edge('m00', 'm01', 'related'), 'scope'),
    (edge('m00', 'm10', 'bogus'), 'invalid'), ({'from': [], 'to': 'm10', 'kind': 'related'}, 'invalid')])
def test_pairing_discards_invalid_scope(store, row, reason):
    links, counts = validate(store, [row])
    assert links == [] and counts[reason] == 1


def test_pairing_cycle_duplicate_and_undirected(store):
    links, counts = validate(store, [edge('m10', 'm11'), edge('m11', 'm12'), edge('m12', 'm10'),
        edge('m10', 'm11'), edge('m10', 'm11', 'related'), edge('m11', 'm10', 'related')])
    assert len(links) == 3
    assert counts['cycle'] == 1 and counts['duplicate'] == 2
    assert validate(store, [edge('m12', 'm10')], existing=links)[1]['cycle'] == 1


def test_pairing_degree_caps_apply_to_both_related_endpoints(store):
    links, counts = validate(store, [edge(f'm0{i}', 'm10') for i in range(4)] +
        [edge('m11', f'm0{i}', 'related') for i in range(4)])
    assert len(links) == 6
    assert counts['prerequisite_limit'] == counts['related_limit'] == 1
    assert validate(store, [edge('m12', 'm11', 'related')], existing=links)[1]['related_limit'] == 1


def test_aliases_only_current_and_predecessor_description(store):
    state = store.point_links_state(PLAN)
    messages, aliases = agents.point_links_request(state['modules'][1], state['points'])
    packet = json.loads(messages[0]['content'])
    assert len(aliases) == 12 and aliases['P1'] == 'm10'
    assert 'summary' in packet['points'][0] and 'summary' not in packet['prerequisite_points'][0]
    with pytest.raises(agents.AgentFormatError): validate(store, {})


def save_all(store):
    state = store.point_links_state(PLAN)
    for m in state['modules']:
        store.save_point_links(PLAN, m['id'], state['fingerprints'][m['id']],
            [edge(m['id'] + '0', m['id'] + '1')])


def test_sync_and_delete_point_module_cleanup(store, tmp_path):
    save_all(store)
    other = CourseStore('account', tmp_path / 'device2')
    try:
        other.apply_remote([(r, i + 1) for i, r in enumerate(store.pending_records(10000))])
        assert other.point_links_state(PLAN)['links'] == store.point_links_state(PLAN)['links']
        points = [p for p in store.records('points', PLAN) if p['module_id'] == 'm0' and p['id'] != 'm00']
        store.save_generated_module(PLAN, 'm0', points, today=DAY, check_minutes=0)
        assert all('m00' not in (e['from'], e['to']) for r in store.records('point_links', PLAN) for e in r['links'])
        assert set(store.point_links_state(PLAN)['dirty']) == {'m0', 'm1'}
        store.delete_module(PLAN, 'm1', today=DAY)
        assert [r['module_id'] for r in store.records('point_links', PLAN)] == ['m0']
        other.apply_remote([(r, i + 100) for i, r in enumerate(store.pending_records(10000))])
        assert all(e['from'] != 'm00' for e in other.point_links_state(PLAN)['links'])
        assert other.records('point_links', PLAN)[0]['status'] == 'pending'
    finally: other.close()


def test_stale_reply_cannot_replace_new_points(store):
    state = store.point_links_state(PLAN)
    points = [p for p in store.records('points', PLAN) if p['module_id'] == 'm0']
    points[0]['summary'] = '修改过的说明'
    store.save_generated_module(PLAN, 'm0', points, today=DAY, check_minutes=0)
    assert not store.save_point_links(PLAN, 'm0', state['fingerprints']['m0'], [])


def test_delayed_sync_result_cannot_resurrect_deleted_module(store, tmp_path):
    save_all(store)
    delayed = next(r for r in store.pending_records(10000) if r.entity == 'point_links' and r.payload['module_id'] == 'm1')
    other = CourseStore('account', tmp_path / 'remote-delete')
    try:
        other.apply_remote([(r, i + 1) for i, r in enumerate(store.pending_records(10000))])
        other.delete_module(PLAN, 'm1', today=DAY)
        other.apply_remote([(delayed, 1000)])
        assert not any(r['module_id'] == 'm1' for r in other.records('point_links', PLAN))
        assert all(r.deleted for r in other.pending_records(10000) if r.entity == 'point_links' and r.payload['id'] == 'm1')
    finally: other.close()


def test_sync_sanitizes_dangling_point_result(store):
    from dataclasses import replace
    save_all(store)
    record = next(r for r in store.pending_records(10000) if r.entity == 'point_links')
    # A result arriving from an older map is repaired and queued for upload.
    payload = dict(record.payload, links=[edge('missing', 'm00')], _field_versions={})
    for r in store.pending_records(10000): store.mark_synced(r.entity, r.entity_id, r.local_revision, 1)
    store.apply_remote([(replace(record, payload=payload), 100)])
    row = next(r for r in store.records('point_links', PLAN) if r['id'] == payload['id'])
    assert row['links'] == [] and row['status'] == 'pending'
    assert any(r.entity == 'point_links' and r.payload['id'] == payload['id'] for r in store.pending_records(10000))


class Model:
    def __init__(self, failures=0, gate=None):
        self.failures, self.gate, self.calls = failures, gate, []
    def complete(self, purpose, messages, **kwargs):
        packet = json.loads(messages[0]['content'])
        if purpose == 'point_difficulty':
            return SimpleNamespace(text=json.dumps([dict(id=p['id'], difficulty='中') for p in packet['points']]))
        self.calls.append(packet['module'])
        if self.gate: self.gate.wait(3)
        if packet['module'] == 'm1' and self.failures:
            self.failures -= 1
            raise ValueError('offline failed')
        return SimpleNamespace(text=json.dumps([edge('P1', 'P2')]))


def services(store, model):
    return SimpleNamespace(store=store, gateway=model, _closed=False, settings=None)


def finish(flow):
    for job in list(flow._point_link_jobs['jobs'].values()): job['thread'].join(5)
    for job in list(flow._point_minute_jobs['jobs'].values()): job['thread'].join(5)
    assert not flow._point_link_jobs['jobs']


def test_old_plan_detail_nonblocking_shared_worker_events_partial_retry(store):
    gate, model, events = threading.Event(), Model(failures=2), []
    model.gate = gate
    svc = services(store, model)
    flow = PlanFlow(svc, lambda n, d: events.append((n, d)) if n == 'plan.point_links_changed' else None, lambda: DAY)
    other = PlanFlow(svc, lambda *args: None, lambda: DAY)
    try:
        detail = flow.detail(PLAN)
        assert detail['point_links'] == [] and detail['point_links_status'] == 'running'
        assert other.retry_point_links(PLAN) == {'status': 'running'}
        assert len(flow._point_link_jobs['jobs']) == 1
    finally: gate.set()
    finish(flow)
    assert store.point_links_state(PLAN)['status'] == 'partial'
    assert model.calls == ['m0', 'm1', 'm1']
    assert events[0][1] == {'plan_id': PLAN, 'status': 'running'} and events[-1][1]['status'] == 'partial'
    assert all(n == 'plan.point_links_changed' for n, _ in events)
    assert flow.detail(PLAN)['point_links_status'] == 'partial'  # failed modules need explicit retry
    flow.retry_point_links(PLAN); finish(flow)
    assert model.calls == ['m0', 'm1', 'm1', 'm1']
    assert flow.detail(PLAN)['point_links_status'] == 'ready'


def test_all_failed_and_reopen_does_not_loop(store):
    model = Model()
    model.complete = lambda *a, **k: (_ for _ in ()).throw(ValueError('failed'))
    flow = PlanFlow(services(store, model), lambda *a: None, lambda: DAY)
    flow.ensure_point_links(PLAN); finish(flow)
    assert store.point_links_state(PLAN)['status'] == 'failed'
    assert flow.ensure_point_links(PLAN) == 'failed'


def test_closed_account_discards_inflight_result(store):
    gate, entered = threading.Event(), threading.Event()
    model = Model()
    def complete(*a, **k):
        entered.set(); gate.wait(3)
        return SimpleNamespace(text='[]')
    model.complete = complete
    svc, events = services(store, model), []
    flow = PlanFlow(svc, lambda n, d: events.append(d), lambda: DAY)
    flow.ensure_point_links(PLAN)
    assert entered.wait(2)
    svc._closed = True
    gate.set(); finish(flow)
    assert store.records('point_links', PLAN) == []
    assert len(events) == 1


def test_new_module_additions_are_automatically_dirty(store):
    save_all(store)
    # PLAN-10 works only after start; imitate a started course using existing state transaction.
    with store.transaction():
        plan = store._get('plans', PLAN, PLAN)
        plan.update(state='active', started_on=DAY.isoformat())
        store._put('plans', PLAN, plan)
    store.append_material_additions(PLAN, [dict(id='new', stage_id='stage', title='新增', summary='新增内容',
        prerequisites=['m0'], check_minutes=0, required_by=['m1'], points=[dict(id='new0', title='新点',
            summary='新说明', minutes=15, chunk_ids=['c'])])], [], ['m0', 'new', 'm1'], today=DAY)
    state = store.point_links_state(PLAN)
    assert set(state['dirty']) == {'new', 'm1'}
    model = Model()
    model.complete = lambda *a, **k: SimpleNamespace(text='[]')
    flow = PlanFlow(services(store, model), lambda *a: None, lambda: DAY)
    assert flow.ensure_point_links(PLAN) == 'running'; finish(flow)
    assert store.point_links_state(PLAN)['status'] == 'ready'


def test_new_plan_links_start_after_point_finalization_without_waiting_for_materials(store):
    entered = threading.Event()
    model = Model()
    def pair(*a, **k):
        if a[0] == 'point_difficulty':
            return SimpleNamespace(text=json.dumps([dict(id=p['id'],difficulty='中') for p in json.loads(a[1][0]['content'])['points']]))
        entered.set()
        return SimpleNamespace(text='[]')
    model.complete = pair
    def prepare(*a, **k):
        assert entered.wait(2), 'Point links must already run independently of preparation'
        raise RuntimeError('offline materials unavailable')
    flow = PlanFlow(services(store, model), lambda *a: None, lambda: DAY, prepare_module=prepare)
    flow.save = lambda draft: None  # creation draft IO is unrelated to this contract
    flow._save_outline = lambda draft: None  # seed already persisted the confirmed outline
    draft = dict(plan_id=PLAN, phase='outline', estimate={'total': 30}, form=dict(start=DAY.isoformat(),end='2026-10-18',daily_cap=180,weekday_weights=[1]*7,special_days=[]), sources=[], messages=[],
        outline=dict(grounded=True, stages=[dict(id='stage', modules=[dict(id=m, key=m, title=m,
            points=[dict(title='定稿点', summary='定稿说明', chunk_ids=['c'], citations=[])]) for m in ('m0', 'm1')])]))
    with pytest.raises(RuntimeError, match='materials unavailable'):
        flow.generate(draft, SimpleNamespace(cancel=threading.Event(), stream=lambda s: None))
    finish(flow)
    assert store.point_links_state(PLAN)['status'] == 'ready'
    assert all(r['status'] == 'ready' for r in store.records('point_links', PLAN))


def test_bridge_detail_retry_and_events(qapp, store):
    bridge, sent = make_bridge(qapp)
    svc = services(store, Model())
    register(bridge, lambda: svc, None, lambda *_: DAY)
    try:
        bridge.call(json.dumps({'id': 'd', 'method': 'plan.detail', 'params': {'plan_id': PLAN}}))
        result = pump(qapp, bridge, sent, lambda m: m.get('id') == 'd')[0]
        assert result['ok'] and 'point_links' in result['result']
        pump(qapp, bridge, sent, lambda m: m.get('name') == 'plan.point_links_changed' and m['data']['status'] == 'ready')
        bridge.call(json.dumps({'id': 'r', 'method': 'plan.retry_point_links', 'params': {'plan_id': PLAN}}))
        assert pump(qapp, bridge, sent, lambda m: m.get('id') == 'r')[0]['result'] == {'status': 'ready'}
    finally: bridge.close()
