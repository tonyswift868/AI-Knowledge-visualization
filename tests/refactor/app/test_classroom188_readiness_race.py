"""Readiness projections must not use a stale course snapshot after a slow call."""

import threading

from refactor.learning.course.teacher_agent import Teaching
from tests.refactor.integration38.support import context
from tests.refactor.plan66.test_plan66_just_in_time_materials import DAY, PLAN, env, seed_module


def test_188_slow_teacher_reply_reports_newly_completed_material_preparation(env, monkeypatch):
    room=env.room;course=room._course(PLAN)
    entered=threading.Event();release=threading.Event();result=[];errors=[]
    env.store.save_module_preparation(PLAN,'m1',dict(state='running'))
    room._prepare_next_module=lambda *a,**k:None
    room._resume_materials=lambda *a,**k:None
    room._teacher_request_page=None

    def slow_teacher(_plan_id,_tctx,_request,_cancel,_emit,**_kwargs):
        entered.set()
        assert release.wait(10),'test did not release teacher request'
        return Teaching('==定义已经准备好了。==按原文继续理解定义。[c1]',()),None,None
    monkeypatch.setattr(room,'_generate_teacher',slow_teacher)
    state=room._rebuild(course)
    state.update(module_id='m1',index=0,step='teach',phase='learning',day=DAY.isoformat(),in_session=True)
    room._save(course,state)

    def operate():
        try:result.append(room.perform(PLAN,'next',context()))
        except BaseException as exc:errors.append(exc)
    worker=threading.Thread(target=operate,daemon=True)
    worker.start()
    try:
        assert entered.wait(5),'teacher call did not enter slow-call checkpoint'
        seed_module(env.store,PLAN,'m1')
        env.store.save_module_preparation(PLAN,'m1',dict(state='done'))
        current=room.run(PLAN,lambda *_a:None,context(),readonly=True)
        module=next(m for m in env.store.records('modules',PLAN) if m['id']=='m1')
        assert current['test_readiness']['module_test'] is True, (module.get('material_preparation'),
            module.get('question_count'),len(room._questions(room._course(PLAN),'m1')))
    finally:
        release.set();worker.join(10)
    assert not worker.is_alive()
    assert not errors,errors
    assert result[0]['step']=='opening'
    assert result[0]['test_readiness']['module_test'] is True
    latest=room._course(PLAN);latest_state=room._load(latest)
    assert result[0]['teaching_version']==room._teaching_version(latest,latest_state)
