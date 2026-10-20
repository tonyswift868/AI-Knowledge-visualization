"""Leaving/reopening the lesson never cancels its shared preparation worker."""
import threading

from tests.refactor.integration38.support import Agents, context
from tests.refactor.plan66.test_plan66_just_in_time_materials import env, seed_module, wait_for, PLAN


def test_classroom188_leave_and_reenter_keep_shared_preparation_while_teaching(env):
    e = env
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    e.services.gateway = Agents()

    def prepare(store, plan_id, module_id, ctx, progress):
        started.set()
        assert release.wait(10), 'test must release its fake preparation'
        assert not ctx['cancel'].is_set()
        seed_module(store, plan_id, module_id)
        progress(1, 1, '题目已准备')
        finished.set()

    e.materials.prepare_module = prepare
    try:
        e.room.run(PLAN, lambda co, st, cx: st.update(module_id='m1', index=0, step='teach'), context())
        assert started.wait(5)
        job = e.room._jobs[(PLAN, 'm1')]
        view = e.room.perform(PLAN, 'next', context())
        assert view['step'] == 'opening' and not view['failure']
        before = {kind: e.store.records(kind, PLAN) for kind in ('credits', 'judgements', 'question_answers')}

        e.room.perform(PLAN, 'leave', context())
        assert not job['cancel'].is_set()
        view = e.room.perform(PLAN, 'enter', context())
        assert view['step'] == 'opening' and e.room._jobs[(PLAN, 'm1')] is job
        assert not job['cancel'].is_set()
        assert {kind: e.store.records(kind, PLAN) for kind in before} == before
        release.set()
        assert finished.wait(5)
        wait_for(lambda: PLAN not in e.flow._preparing, timeout=10)
        view = e.room.run(PLAN, lambda *_: None, context(), readonly=True)
        assert view['step'] == 'opening'
        assert view['test_readiness']['skip_test']
    finally:
        release.set()
        wait_for(lambda: PLAN not in e.flow._preparing, timeout=10)
