"""159-MF06: concept parents are projections of subpoint minutes."""
import json
import threading

from refactor.learning.course import planner_agents as agents
from refactor.learning.course.store import POINT_MINUTES_VERSION
from refactor.learning.app.plan_flow import PlanFlow
from tests.refactor.app.test_app_point_minutes84 import Model, finish
from tests.refactor.app.test_app_point_links82 import PLAN, DAY, seed
from refactor.learning.course.store import CourseStore


def _concept(point, minutes=(7, 11)):
    p = dict(point)
    p.update(structure='concept_v1', goal=p['summary'], citations=[], chunk_ids=['c'])
    p['subpoints'] = [dict(id=f"{p['id']}-s{i}", title=f"子项{i}", minutes=n,
                           chunk_ids=['c'], notes=[dict(text='权威正文', chunk_id='c')])
                      for i, n in enumerate(minutes, 1)]
    p['minutes'] = sum(minutes)
    return p


def _flow(store, model):
    svc = type('S', (), {'store': store, 'gateway': model, '_closed': False, 'settings': None})()
    return PlanFlow(svc, lambda *_: None, lambda: DAY)


def test_mixed_concept_minutes_skip_parent_model_and_set_version(tmp_path):
    store = CourseStore('account', tmp_path)
    seed(store)
    try:
        with store.transaction():
            p = store._get('points', PLAN, 'm00')
            store._put('points', PLAN, _concept(p),)
        model = Model()
        flow = _flow(store, model)
        flow.ensure_point_minutes(PLAN)
        finish(flow)
        state = store.point_minutes_snapshot(PLAN)
        parent = next(p for p in state['points'] if p['id'] == 'm00')
        assert parent['minutes'] == sum(s['minutes'] for s in parent['subpoints']) == 18
        assert state['plan']['point_minutes_version'] == POINT_MINUTES_VERSION
        # Only the legacy points are classified.  A concept parent with no
        # citation must never cause a model format failure or be re-estimated.
        assert model.calls == ['point_difficulty', 'point_difficulty']
        assert flow.ensure_point_minutes(PLAN) == 'ready'
    finally:
        store.close()


def test_all_concept_plan_is_idempotent_without_model(tmp_path):
    store = CourseStore('account', tmp_path)
    seed(store)
    try:
        with store.transaction():
            for p in store.records('points', PLAN):
                store._put('points', PLAN, _concept(p, (5, 6, 4)))
        model = Model()
        flow = _flow(store, model)
        flow.ensure_point_minutes(PLAN)
        finish(flow)
        assert model.calls == []
        assert store.point_minutes_snapshot(PLAN)['ready']
        event_count = len(store.records('ledger_events', PLAN))
        assert flow.ensure_point_minutes(PLAN) == 'ready'
        assert len(store.records('ledger_events', PLAN)) == event_count
    finally:
        store.close()


def test_concept_save_rejects_missing_legacy_labels(tmp_path):
    store = CourseStore('account', tmp_path)
    seed(store)
    try:
        with store.transaction():
            store._put('points', PLAN, _concept(store._get('points', PLAN, 'm00')))
        state = store.point_minutes_snapshot(PLAN)
        # m00 is concept, while the remaining legacy points still need labels.
        try:
            store.save_point_minutes(PLAN, state['fingerprint'], {'m00': '中'})
        except Exception as exc:
            assert getattr(exc, 'code', '') == 'MINUTE_POINTS_CHANGED'
        else:
            raise AssertionError('missing legacy difficulty labels was accepted')
    finally:
        store.close()
