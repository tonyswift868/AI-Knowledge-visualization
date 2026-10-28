"""Content totals, v1 migration and atomic ordinary ledger replans; no network."""
from datetime import timedelta
import json

import pytest

from refactor.learning.course import planner_agents as agents
from refactor.learning.course.store import POINT_MINUTES_VERSION
from .test_app_point_minutes84 import store, labels, flow, Model, finish  # noqa: F401
from .test_app_point_links82 import PLAN, DAY


def test_content_total_direct_values_and_v1_migration_without_model(store):
    state = store.point_minutes_snapshot(PLAN)
    difficulties = labels(state)
    with store.transaction():
        for p in state['points']:
            p.update(difficulty=difficulties[p['id']], difficulty_fingerprint=state['point_fingerprints'][p['id']],
                     point_minutes_version=1)
            store._put('points', PLAN, p)
        plan = state['plan']
        plan.update(point_minutes_version=1, point_minutes_signature=state['signature'])
        store._put('plans', PLAN, plan)
    model, events = Model(), []
    f = flow(store, model, events)
    assert not store.point_minutes_snapshot(PLAN)['ready']
    f.ensure_point_minutes(PLAN); finish(f)
    after = store.point_minutes_snapshot(PLAN)
    assert model.calls == [] and after['plan']['point_minutes_version'] == POINT_MINUTES_VERSION == 2
    expected = {p['id']: agents.estimate_point_minutes(**state['metrics'][p['id']], difficulty=difficulties[p['id']])
                for p in state['points']}
    assert {p['id']: p['minutes'] for p in after['points']} == expected
    assert after['plan']['total_minutes'] == sum(expected.values()) != state['plan']['total_minutes']
    assert ('classroom.materials_ready', dict(plan_id=PLAN, reason='point_minutes')) in events
    event_count = len(store.records('ledger_events', PLAN))
    assert f.ensure_point_minutes(PLAN) == 'ready'
    assert len(store.records('ledger_events', PLAN)) == event_count


def test_content_local_preview_does_not_call_model_and_cancel_discards_labels(store):
    from refactor.learning.connections.errors import ModelCancelled
    import threading
    model = Model()
    f = flow(store, model, [])
    f.save = lambda draft: None
    point = dict(title='说明', summary='说明', citations=[dict(chunk_id='c', quote='学'*900)])
    draft = dict(plan_id=PLAN,phase='outline',form=dict(start=DAY.isoformat(),end='2026-10-18',
        daily_cap=180,weekday_weights=[1]*7,special_days=[]),messages=[],
        outline=dict(stages=[dict(title='阶段',modules=[dict(key='m0',title='模块',points=[point])])]))
    f.estimate(draft, annotate=False)
    assert not model.calls and point['minutes'] == 6
    f._call_context.cancel = threading.Event()
    f._call_context.cancel.set()
    with pytest.raises(ModelCancelled): f.estimate(draft)
    assert not model.calls and 'difficulty' not in point


def test_content_refined_old_preview_requires_confirmation_before_generation(store):
    model = Model()
    def hard(purpose,messages,**kwargs):
        assert purpose == 'point_difficulty'
        model.calls.append(purpose)
        return json.dumps([dict(id=p['id'],difficulty='难') for p in json.loads(messages[0]['content'])['points']])
    model.complete = hard
    f = flow(store, model, [])
    f.save = lambda draft: None
    f._prepare_module = lambda *args, **kwargs: pytest.fail('Preparation before refined preview confirmation')
    point = dict(title='说明', summary='说明', citations=[dict(chunk_id='c',paragraph_id='c@0:900',
                 paragraph_start=0,paragraph_end=900,quote='学'*900)],chunk_ids=['c'])
    draft = dict(plan_id=PLAN,phase='outline',form=dict(start=DAY.isoformat(),end='2026-10-18',
        daily_cap=180,weekday_weights=[1]*7,special_days=[]),messages=[],sources=[],
        outline=dict(grounded=True,stages=[dict(title='阶段',modules=[dict(key='m0',title='模块',points=[point])])]))
    f._adopted = lambda d: ([dict(id='s')],None)
    f.estimate(draft, annotate=False)
    from types import SimpleNamespace
    import threading
    f.generate(draft,SimpleNamespace(cancel=threading.Event()))
    assert draft['phase'] == 'outline' and draft['outline']['ranges']['learning'] == 8
    assert model.calls == ['point_difficulty']
    assert all(p['minutes'] == 15 for p in store.records('points',PLAN))


def test_content_replan_freezes_history_and_preserves_booked_time(store):
    state = store.point_minutes_snapshot(PLAN)
    store.add_credit(PLAN, 'learn:m00', 'm00', 15, DAY)
    # A revoked learning submission still freezes that point's estimate.
    store.add_credit(PLAN, 'relearn:m01:1', 'm01', 15, DAY)
    store.revoke_credits(PLAN, 'm01', event_id='revoke', today=DAY)
    original_credits = store.records('credits', PLAN)
    old = store.load_ledger(PLAN)
    today = DAY + timedelta(days=2)
    assert store.save_point_minutes(PLAN, state['fingerprint'], labels(state), today=today)
    new = store.load_ledger(PLAN)
    expected_total = sum(p['minutes'] for p in store.records('points', PLAN))
    # Automatic estimates must retain payable past targets, unlike an explicit
    # user-requested schedule reset (BUG-83, 106-MF06).
    old.replan(today, old.window, expected_total, preserve_past=True)
    assert new.to_dict() == old.to_dict()
    assert sum(new.targets.values()) == expected_total
    assert set(new.targets) == {DAY + timedelta(days=i) for i in range(11)}
    assert new.status(today).today_target == max(n for d,n in new.targets.items() if d>=today)
    assert new.status(today).debt_minutes > 0
    assert new.status(today).history_debt_minutes == 0
    assert store.records('credits', PLAN) == original_credits
    assert {p['id']:p['minutes'] for p in store.records('points', PLAN) if p['id'] in ('m00','m01')} == {'m00':15,'m01':15}


def test_content_replan_failure_rolls_back_minutes_modules_version_outbox(store, monkeypatch):
    before = store.point_minutes_snapshot(PLAN)
    records = store.pending_records(10000)
    events = store.records('ledger_events', PLAN)
    put = store._put
    def fail(entity, plan_id, payload, **kwargs):
        if entity == 'plans' and payload.get('point_minutes_version') == 2:
            raise OSError('commit failed after replan event')
        return put(entity, plan_id, payload, **kwargs)
    monkeypatch.setattr(store, '_put', fail)
    with pytest.raises(OSError):
        store.save_point_minutes(PLAN, before['fingerprint'], labels(before), today=DAY)
    assert store.point_minutes_snapshot(PLAN) == before
    assert store.records('ledger_events', PLAN) == events
    assert store.pending_records(10000) == records
    monkeypatch.setattr(store, '_put', put)
    assert store.save_point_minutes(PLAN, before['fingerprint'], labels(before), today=DAY)


def test_content_preview_labels_cached_dates_change_and_new_save_agree(store):
    model = Model()
    f = flow(store, model, [])
    f.save = lambda draft: None
    points = [dict(title='短', summary='短段', citations=[dict(chunk_id='c',quote='学'*100)]),
              dict(title='长', summary='长段', citations=[dict(chunk_id='c',quote='学'*1500)])]
    module = dict(id='m0',key='m0',title='模块',points=points)
    draft = dict(plan_id=PLAN,phase='outline',form=dict(start=DAY.isoformat(),end='2026-10-18',
        daily_cap=180,weekday_weights=[1]*7,special_days=[]),messages=[],
        outline=dict(stages=[dict(title='阶段',modules=[module])]))
    f.estimate(draft)
    preview = draft['outline']['ranges']
    assert preview['learning'] == 3+8
    # Before preparation, total includes two provisional module-test
    # questions (4 minutes) and their final-check reserve (5 minutes).
    assert preview['total'] == [20,20]
    assert preview['daily'] == [2,2]
    assert '平均 8' not in draft['messages'][-1]['text']
    draft['form']['end'] = '2026-10-12'
    f.estimate(draft)
    assert model.calls == ['point_difficulty']
    assert draft['outline']['ranges']['daily'] == [4,4]
    store.delete_module(PLAN,'m1',today=DAY)
    store.save_generated_module(PLAN, 'm0', [dict(p, id=f'm0-{i:02d}',chunk_ids=['c']) for i,p in enumerate(points,1)],
                                today=DAY,check_minutes=0)
    state = store.point_minutes_snapshot(PLAN)
    store.save_point_minutes(PLAN,state['fingerprint'],{p['id']:p['difficulty'] for p in state['points']},today=DAY)
    # This saved fixture explicitly has zero prepared questions: only the
    # learning estimate remains, rather than the provisional test reserve.
    assert store.get_plan(PLAN)['total_minutes'] == preview['learning']
    assert f.ensure_point_minutes(PLAN) == 'ready'
