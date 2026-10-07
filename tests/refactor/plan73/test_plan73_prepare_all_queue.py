"""Sequential whole-plan preparation with real storage and offline IO."""
import copy
import threading
from types import SimpleNamespace

from refactor.learning.app.classroom import Classroom
from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course.store import CourseStore, StoreError
from tests.refactor.plan37.test_grounded_plan37_flow import fixture, adopt_and_map
from tests.refactor.plan66.test_plan66_just_in_time_materials import env, seed_module, wait_for, PLAN, ACCOUNT, DAY
from tests.refactor.integration38.support import context


def four(fixture):
    f, model, ctx, *_ = fixture
    d=adopt_and_map(fixture)
    seed=d['outline']['stages'][0]['modules'][0]
    d['outline']['stages'][0]['modules']=[dict(copy.deepcopy(seed),id=f'm{i}',key=f'm{i}',title=f'模块{i}') for i in range(4)]
    f.estimate(d)
    return f,ctx,d


def test_plan73_generate_all_failure_isolated_and_retry_only_failed(fixture):
    f,cx,d=four(fixture);pid=d['plan_id'];calls=[];views=[]
    f.push=lambda name,data:views.append(copy.deepcopy(data)) if name=='plan_draft' else None
    def prep(store, pid, mid, ctx, progress):
        calls.append(mid)
        if mid=='m1' and calls.count(mid)==1:raise StoreError('SOURCE_MISSING','资料正文取不到，请补充资料。')
        seed_module(store,pid,mid)
    f._prepare_module=prep
    f.generate(d,cx)
    wait_for(lambda:pid not in f._preparing)
    saved=f.load(pid)
    assert calls==['m0','m1','m2','m3'] and saved['phase']=='ready'
    assert saved['generation']==dict(m0='done',m1='failed',m2='done',m3='done')
    assert saved['generation_errors']==dict(m1='资料正文取不到，请补充资料。')
    assert any(v.get('generation_errors',{}).get('m1')=='资料正文取不到，请补充资料。' for v in views)
    assert f.services.store.records('modules',pid)[1]['material_preparation']['error']=='资料正文取不到，请补充资料。'
    f.run(pid,lambda d,c:f.generate(d,c),cx)
    wait_for(lambda:pid not in f._preparing)
    assert calls==['m0','m1','m2','m3','m1']
    assert set(f.load(pid)['generation'].values())=={'done'} and not f.load(pid)['generation_errors']


def test_plan73_current_module_moves_to_head_without_cancelling(env):
    e=env;running=threading.Event();release=threading.Event();peak=0;active=0
    def prep(store,pid,mid,ctx,progress):
        nonlocal peak,active
        active+=1;peak=max(peak,active);e.calls.append(mid)
        if mid=='m1':running.set();assert release.wait(3)
        seed_module(store,pid,mid);active-=1
    e.materials.prepare_module=prep
    e.flow._prepare_remaining(PLAN,e.store.records('modules',PLAN),prep)
    assert running.wait(1)
    try:
        e.room.run(PLAN,lambda co,st,cx:st.update(module_id='m3',index=0,step='teach'),context())
        assert e.calls==['m1']
        with e.flow._material_jobs['guard']:
            assert [entry[0]['id'] for entry in e.flow._material_jobs['queued'][PLAN]]==['m3','m2']
    finally:release.set()
    wait_for(lambda:PLAN not in e.flow._preparing)
    assert e.calls==['m1','m3','m2'] and peak==1


def test_plan73_reopen_classroom_resumes_all_unfinished_keeps_done(env):
    e=env;e.store.save_module_preparation(PLAN,'m1',dict(state='running'))
    fresh=CourseStore(ACCOUNT,e.services.data_root)
    services=SimpleNamespace(**vars(e.services));services.store=fresh
    for k in ('_material_flow','_material_jobs'):delattr(services,k)
    room=Classroom(services,lambda:DAY);room._materials=lambda:e.materials
    try:
        room.perform(PLAN,'enter',context())
        wait_for(lambda:PLAN not in services._material_flow._preparing)
        assert e.calls==['m1','m2','m3']
        assert all(m['material_preparation']['state']=='done' for m in fresh.records('modules',PLAN)[1:])
        assert room._ready(room._course(PLAN),'m0')
        room.perform(PLAN,'enter',context())
        assert e.calls==['m1','m2','m3']
    finally:
        room.cancel_materials();fresh.close()


def test_plan73_reopen_ready_draft_resumes_pending_and_not_failed(fixture):
    f,cx,d=four(fixture);pid=d['plan_id'];calls=[]
    def prep(store,pid,mid,ctx,progress):calls.append(mid);seed_module(store,pid,mid)
    f._prepare_module=prep
    # Persist the generated module data but simulate process exit before the tail.
    f._prepare_remaining=lambda *a,**k:None
    f.generate(d,cx)
    # Exit between the module's committed completion and the draft's update.
    seed_module(f.services.store,pid,'m1')
    f.services.store.save_module_preparation(pid,'m1',dict(state='done'))
    fresh=PlanFlow(f.services,lambda *a:None,f.study_day)
    fresh._prepare_module=prep
    view=fresh.get_draft(pid)
    assert view['generation']['m1']=='done'
    wait_for(lambda:pid not in fresh._preparing)
    assert calls==['m0','m2','m3']
    assert set(fresh.get_draft(pid)['generation'].values())=={'done'}
    assert all(m['material_preparation']['state']=='done' for m in fresh.services.store.records('modules',pid))
