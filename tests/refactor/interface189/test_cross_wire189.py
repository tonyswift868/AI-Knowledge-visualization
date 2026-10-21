"""Cross-interface coverage for the shared PlanFlow/Classroom material queue."""
import threading
from datetime import date
from types import SimpleNamespace

from refactor.learning.app.classroom import Classroom
from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.connections.errors import ModelCancelled
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.point_materials import prepare_module
from refactor.learning.course.store import CourseStore
from tests.refactor.integration38.support import context
from tests.refactor.plan66.test_plan66_just_in_time_materials import (
    env as material_env, PLAN, seed_module, wait_for,
)
from tests.refactor.concept114.test_concept114_classroom import (
    concept_environment, PLAN as CONCEPT_PLAN,
)

DAY = date(2026, 10, 6)


def _prepare_blocked(store, plan_id, module_id, ctx, progress, *, started, release, calls):
    calls.append(module_id)
    if module_id == 'm1':
        started.set()
        assert release.wait(10)
    if ctx['cancel'].is_set():
        raise ModelCancelled()
    seed_module(store, plan_id, module_id)
    progress(1, 1, '题已好')


def test_returning_to_learning_and_leaving_keep_shared_preparation_running(material_env, monkeypatch):
    e = material_env
    started, release = threading.Event(), threading.Event()
    calls, stop_calls, events = [], [], []

    def prepare(store, plan_id, module_id, ctx, progress):
        _prepare_blocked(store, plan_id, module_id, ctx, progress,
                         started=started, release=release, calls=calls)

    e.materials.prepare_module = prepare
    e.room.material_event = events.append
    e.room._profile = lambda *_: None
    original_stop = e.flow.stop_background
    monkeypatch.setattr(e.flow, 'stop_background',
                        lambda *a, **k: (stop_calls.append(a), original_stop(*a, **k))[1])
    try:
        e.room.perform(PLAN, 'enter', context())
        assert started.wait(3)
        moved = e.room.perform(PLAN, 'next_module', context(), ('m1',))
        assert moved['module']['id'] == 'm1'

        # The learner has reached the module test gate while its questions run.
        e.room.run(PLAN, lambda _co, state, _cx: state.update(index=1, step='test_offer'), context())
        waiting = e.room.perform(PLAN, 'start_test', context(), ('module_test',))
        assert waiting['material_notice']['module_id'] == 'm1'
        job = e.flow._material_jobs['jobs'][(PLAN, 'm1')]
        returned = e.room.perform(PLAN, 'stop_preparation', context())
        assert returned['step'] == 'test_offer' and returned['material_notice'] is None
        assert not job['cancel'].is_set()

        e.room.perform(PLAN, 'leave', context())
        assert not job['cancel'].is_set()
        assert e.store.records('modules', PLAN)[1]['material_preparation']['state'] == 'running'
        assert stop_calls == []

        release.set()
        wait_for(lambda: PLAN not in e.flow._preparing)
        assert calls == ['m1', 'm2', 'm3']
        assert e.store.records('modules', PLAN)[1]['material_preparation']['state'] == 'done'
    finally:
        release.set()
        wait_for(lambda: PLAN not in e.flow._preparing)


def test_entering_module_moves_its_real_thread_job_to_queue_head(material_env):
    e = material_env
    started, release = threading.Event(), threading.Event()
    calls = []
    e.room._profile = lambda *_: None

    def prepare(store, plan_id, module_id, ctx, progress):
        _prepare_blocked(store, plan_id, module_id, ctx, progress,
                         started=started, release=release, calls=calls)

    e.materials.prepare_module = prepare
    try:
        entered = e.room.perform(PLAN, 'enter', context())
        assert entered['module']['id'] == 'm0'
        assert started.wait(3)
        assert calls == ['m1']
        assert [item[0]['id'] for item in e.flow._material_jobs['queued'][PLAN]] == ['m2', 'm3']

        moved = e.room.perform(PLAN, 'next_module', context(), ('m3',))
        assert moved['module']['id'] == 'm3'
        assert [item[0]['id'] for item in e.flow._material_jobs['queued'][PLAN]] == ['m3', 'm2']
        release.set()
        wait_for(lambda: PLAN not in e.flow._preparing)
        assert calls == ['m1', 'm3', 'm2']
    finally:
        release.set()
        wait_for(lambda: PLAN not in e.flow._preparing)


def test_finished_background_job_makes_same_classroom_test_ready(material_env):
    e = material_env
    started, release = threading.Event(), threading.Event()
    events, calls = [], []
    e.room.material_event = events.append
    e.room._profile = lambda *_: None

    def prepare(store, plan_id, module_id, ctx, progress):
        _prepare_blocked(store, plan_id, module_id, ctx, progress,
                         started=started, release=release, calls=calls)

    e.materials.prepare_module = prepare
    try:
        e.room.perform(PLAN, 'enter', context())
        assert started.wait(3)
        moved = e.room.perform(PLAN, 'next_module', context(), ('m1',))
        assert moved['module']['id'] == 'm1'
        before = e.room.run(PLAN, lambda *_: None, context(), readonly=True)
        assert before['test_readiness']['module_test'] is False

        release.set()
        wait_for(lambda: PLAN not in e.flow._preparing)
        assert any(event.get('status') == 'ready' for event in events)

        # No re-entry or refresh action: this same Classroom reads the committed Store state.
        after = e.room.run(PLAN, lambda *_: None, context(), readonly=True)
        assert after['test_readiness']['module_test'] is True
        assert after['material_notice'] is None
        assert any(q['module_id'] == 'm1' for q in e.store.records('question_bank', PLAN))
    finally:
        release.set()
        wait_for(lambda: PLAN not in e.flow._preparing)


def test_explicit_stop_persists_across_store_reopen_without_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(PlanFlow, '_material_peak_now', staticmethod(lambda now=None: False))
    account_id = 'mfa1_' + '3' * 64
    store = CourseStore(account_id, tmp_path)
    plan_id = '3' * 32
    store.create_plan(plan_id, goal='恢复核对', final_goal='完成', level='beginner',
                      window=PlanWindow(DAY, DAY.replace(day=16), 60))
    store.add_sources(plan_id, [dict(id='src', kind='url', url='https://example.test', adopted=True,
                                     chunks=[dict(chunk_id='c', text='资料原文。')])])
    store.save_outline(plan_id, [dict(id='s', title='阶段')],
                       [dict(id='m', stage_id='s', title='模块')])
    store.save_generated_module(plan_id, 'm', [dict(id='p', title='知识点', summary='目标',
                                  minutes=5, chunk_ids=['c'])], check_minutes=2, today=DAY)
    store.start_plan(plan_id, today=DAY)
    services = SimpleNamespace(store=store, gateway=None, account_id=account_id,
                               data_root=tmp_path, _closed=False,
                               settings=SimpleNamespace(load=lambda: dict(refresh_time='04:00')))
    flow = PlanFlow(services, lambda *_: None, lambda: DAY)
    started, release = threading.Event(), threading.Event()

    def prepare(_store, _plan, _module, ctx, _progress):
        started.set()
        assert release.wait(10)
        if ctx['cancel'].is_set():
            raise ModelCancelled()

    try:
        flow._prepare_remaining(plan_id,
            [dict(store.records('modules', plan_id)[0], points=store.records('points', plan_id))], prepare)
        assert started.wait(3)
        assert flow.stop_background(plan_id, 'm')['stopped'] == 1
        release.set()
        wait_for(lambda: plan_id not in flow._preparing)
        assert store.records('modules', plan_id)[0]['material_preparation']['state'] == 'stopped'
        services._closed = True
        store.close()

        reopened = CourseStore(account_id, tmp_path)
        services2 = SimpleNamespace(store=reopened, gateway=None, account_id=account_id,
                                    data_root=tmp_path, _closed=False,
                                    settings=SimpleNamespace(load=lambda: dict(refresh_time='04:00')))
        flow2 = PlanFlow(services2, lambda *_: None, lambda: DAY)
        accidental = []
        monkeypatch.setattr(flow2, '_prepare_callable', lambda: (lambda *_a, **_k: accidental.append('run')))
        try:
            assert flow2.resume_started_preparations() == 0
            assert accidental == []
            assert reopened.records('modules', plan_id)[0]['material_preparation']['state'] == 'stopped'
        finally:
            services2._closed = True
            reopened.close()
    finally:
        release.set()
        if not services._closed:
            services._closed = True
            store.close()


def test_empty_question_lecture_keeps_prior_day_and_classroom_restore_source_day(tmp_path):
    store = CourseStore('mfa1_' + '4' * 64, tmp_path)
    plan_id = '4' * 32
    prior_day, recovery_day = date(2026, 10, 20), date(2026, 10, 21)
    store.create_plan(plan_id, goal='无题模块', final_goal='讲懂资料', level='beginner',
                      window=PlanWindow(prior_day, recovery_day.replace(day=24), 60))
    store.add_sources(plan_id, [dict(id='src', kind='url', url='https://example.test', adopted=True,
                                     chunks=[dict(chunk_id='c', text='队列按照元素进入的先后顺序处理。')])])
    store.save_outline(plan_id, [dict(id='s', title='阶段')],
                       [dict(id='m', stage_id='s', title='队列')])
    store.save_generated_module(plan_id, 'm', [dict(id='p', title='队列顺序', summary='理解先后顺序',
                                  minutes=5, chunk_ids=['c'])], check_minutes=2, today=prior_day)
    store.start_plan(plan_id, today=prior_day)
    result = prepare_module(store, plan_id, 'm',
                            {'_original_points_only': True, 'cancel': threading.Event()}, lambda *_: None)
    assert result['questions'] == 0
    store.update_module_preparation_state(plan_id, 'm', 'done')
    services = SimpleNamespace(store=store, gateway=None, account_id='mfa1_' + '4' * 64,
                               data_root=tmp_path, _closed=False,
                               settings=SimpleNamespace(load=lambda: dict(refresh_time='04:00')))
    clock = [prior_day]
    room = Classroom(services, lambda: clock[0])
    try:
        entered = room.perform(plan_id, 'enter', context())
        assert entered['step'] == 'teach'
        clock[0] = recovery_day
        room.run(plan_id, lambda _co, state, _cx: state.update(step='closing'), context())
        after = room.perform(plan_id, 'next', context())
        assert after['step'] in ('test_done', 'check_offer', 'completed')
        assert not store.records('question_bank', plan_id)
        learned = next(row['credit'] for row in store.records('credits', plan_id)
                       if row.get('credit', {}).get('credit_id') == 'learn:p')
        assert learned['day'] == prior_day.isoformat() and learned['state'] == 'tentative'
    finally:
        services._closed = True
        room.cancel_materials(plan_id)
        store.close()

    # Classroom's actual restore helper preserves each original learning day,
    # even when the failed-test revocation and successful retest are on a later day.
    e = concept_environment(tmp_path / 'concept-recovery')
    try:
        cstore, croom = e.store, e.room
        original = croom.study_day()
        recovery = original.fromordinal(original.toordinal() + 1)
        cstore.add_credit(CONCEPT_PLAN, 'subpoint:source', 'p0', 7, original, 'tentative')
        cstore.revoke_credits(CONCEPT_PLAN, 'p0', event_id='cross-day-failure', today=recovery)
        croom._restore_concept_learning(croom._course(CONCEPT_PLAN),
                                        {'module_id': 'm0', 'day': recovery.isoformat()}, 'cross-day-retest')
        recovered = [row['credit'] for row in cstore.records('credits', CONCEPT_PLAN)
                     if row['id'].startswith('relearn:') and row['credit']['unit_id'] == 'p0']
        assert [(row['day'], row['minutes']) for row in recovered] == [(original.isoformat(), 7)]
    finally:
        croom.cancel_materials()
        cstore.close()
