"""Returning to learning leaves the actual shared preparation worker alive."""
import threading

import pytest

from refactor.learning.app.classroom import Classroom
from tests.refactor.plan66.test_plan66_just_in_time_materials import env, seed_module, wait_for, PLAN
from tests.refactor.integration38.support import context


@pytest.mark.parametrize('previous_step', ['teach', 'teaching'])
def test_wait129_exit_restores_step_and_background_finishes_without_auto_action(env, previous_step):
    e=env
    started=threading.Event();release=threading.Event();ready=threading.Event()
    def prepare(store, plan_id, module_id, ctx, progress):
        started.set();progress(0,1,'正在找题')
        assert release.wait(10), 'test did not release the worker'
        assert not ctx['cancel'].is_set(), 'exiting foreground wait cancelled background preparation'
        seed_module(store,plan_id,module_id)
        progress(1,1,'题目已备好')
    e.materials.prepare_module=prepare
    e.room.material_event=lambda data:ready.set() if data['status']=='ready' else None
    try:
        def wait(course,state,ctx):
            state.update(module_id='m1',index=0,step=previous_step)
            e.room._wait(course,state,'start_test',('module_test',))
        view=e.room.run(PLAN,wait,context())
        assert started.wait(5) and view['step']=='preparing_materials'
        job=e.room._jobs[(PLAN,'m1')]
        before={name:e.store.records(name,PLAN) for name in ('judgements','credits','question_answers')}
        view=e.room.perform(PLAN,'stop_preparation',context())
        assert not view['failure'] and view['step']==previous_step
        assert e.room._jobs[(PLAN,'m1')] is job and not job['cancel'].is_set()
        assert view['preparation'] and not e.room._load(e.room._course(PLAN)).get('materials_paused')
        assert 'material_wait' not in e.room._load(e.room._course(PLAN))
        release.set();assert ready.wait(5), 'background job did not finish'
        view=e.room.run(PLAN,lambda *args:None)
        assert view['step']==previous_step and not view['question'] and not view['preparation']
        assert {name:e.store.records(name,PLAN) for name in before}==before
        assert e.room._ready(e.room._course(PLAN),'m1')
    finally:
        release.set()
        if hasattr(e.services,'_material_flow'):
            wait_for(lambda:PLAN not in e.services._material_flow._preparing,timeout=10)
