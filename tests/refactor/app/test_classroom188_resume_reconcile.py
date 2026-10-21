"""A parked module resume must not outrun synced learning progress."""

from tests.refactor.plan66.test_plan66_just_in_time_materials import DAY, PLAN, env, seed_module
from tests.refactor.integration38.support import context


def _parked_state(room, course, *, current='m1'):
    state=room._rebuild(course)
    state.update(module_id=current,index=0,step='teach',phase='learning',module_resumes={})
    return state


def test_188_saved_module_resume_advances_past_synced_taught_point(env):
    # The parked target is later in course order. A global rebuild would jump
    # back to m0 instead of reconciling this selected module only.
    with env.store.transaction():
        env.store._put('points',PLAN,dict(id='p1-next',module_id='m1',position=1,
            title='后续知识点',summary='掌握后续内容',minutes=8,chunk_ids=['k1']))
    room=env.room;course=room._course(PLAN)
    env.store.add_credit(PLAN,'learn:p1','p1',8,DAY,'confirmed')
    assert 'p1' in course.taught()
    assert course.book().module('m1').state=='untested'
    state=_parked_state(room,course,current='m0')
    state['module_resumes']['m1']=dict(module_id='m1',index=0,step='teaching',phase='learning',
        conversation=dict(point_id='p1',options=[],interrupted=True),
        concept=dict(point_id='p1',subpoint_id='p1-s1',rounds=2))
    room._save(course,state)

    resumed=room.perform(PLAN,'next_module',context(),('m1',))
    assert not resumed.get('failure'),resumed.get('failure')
    assert resumed['module']['id']=='m1'
    assert resumed['step']=='teach'
    restored=room._load(room._course(PLAN))
    assert restored['index']==1 and restored['module_id']=='m1'
    assert restored.get('conversation') is None and restored.get('concept') is None


def test_188_saved_test_result_page_is_not_reconciled_as_lesson(env):
    room=env.room;course=room._course(PLAN)
    env.store.add_credit(PLAN,'learn:p0','p0',8,DAY,'confirmed')
    state=_parked_state(room,course)
    state['module_resumes']['m0']=dict(module_id='m0',index=0,step='test_done',phase='learning')
    room._save(course,state)

    resumed=room.perform(PLAN,'next_module',context(),('m0',))
    assert resumed['module']['id']=='m0' and resumed['step']=='test_done'


def test_188_completed_module_resume_is_listed_as_deferred_test(env):
    room=env.room;course=room._course(PLAN)
    state=_parked_state(room,course,current='m2')
    state['module_resumes']['m1']=dict(module_id='m1',index=1,step='test_offer',phase='learning')
    room._save(course,state)

    view=room.run(PLAN,lambda *_a:None,context(),readonly=True)
    assert view['module']['id']=='m2'
    assert any(row['module_id']=='m1' and row['kind']=='module_test'
               for row in view['deferred_module_tests'])


def test_188_module_switch_clears_another_modules_preparation_error(env):
    room=env.room;course=room._course(PLAN);state=_parked_state(room,course,current='m0')
    state['preparation_error']=dict(module_id='m0',message='旧模块队列失败')
    room._save(course,state)

    room.next_module(course,state,context(),'m1')
    assert state['module_id']=='m1'
    assert 'preparation_error' not in state


def test_188_unknown_legacy_wait_action_is_cleared_on_enter(env):
    room=env.room;course=room._course(PLAN);state=_parked_state(room,course,current='m0')
    state.update(step='preparing_materials',material_wait=dict(module_id='m0',action='old_action',args=[],
        previous_step='closing',done=0,total=1,text='等待旧任务',error=None))
    room._save(course,state)

    entered=room.perform(PLAN,'enter',context())
    restored=room._load(room._course(PLAN))
    assert entered['step']=='closing'
    assert 'material_wait' not in restored


def test_188_default_module_switch_prefers_unfinished_lesson_over_parked_test(env):
    seed_module(env.store,PLAN,'m1')
    env.store.add_credit(PLAN,'learn:p0','p0',8,DAY,'confirmed')
    env.store.add_credit(PLAN,'learn:p1','p1',8,DAY,'confirmed')
    room=env.room;course=room._course(PLAN)
    assert {'p0','p1'} <= course.taught()
    assert course.book().module('m0').state=='untested'
    assert course.book().module('m1').state=='untested'
    state=_parked_state(room,course,current='m0')
    state['step']='test_offer'
    state['index']=len(course.points_of('m0'))
    state['module_resumes']['m1']=dict(module_id='m1',index=1,step='test_offer',phase='learning')
    old_plan_order=[m['id'] for m in course.modules if m['id']!='m0'
        and (m['id'] in state['module_resumes'] or any(p['id'] not in course.taught()
            for p in course.points_of(m['id'])))]
    assert old_plan_order[0]=='m1'  # prior first-candidate policy loops back to this parked test

    room.next_module(course,state,context())

    assert state['module_id']=='m2'
    assert state['index']==0 and state['step']=='teach'


def test_188_test_done_advance_also_prefers_unfinished_lesson(env):
    seed_module(env.store,PLAN,'m1')
    env.store.add_credit(PLAN,'learn:p0','p0',8,DAY,'confirmed')
    env.store.add_credit(PLAN,'learn:p1','p1',8,DAY,'confirmed')
    room=env.room;course=room._course(PLAN)
    assert {'p0','p1'} <= course.taught()
    assert course.book().module('m0').state=='untested'
    assert course.book().module('m1').state=='untested'
    state=_parked_state(room,course,current='m0')
    state.update(step='test_done',index=1)
    old_after=[m['id'] for m in course.modules if course.book().module(m['id']).state=='untested']
    assert old_after[0]=='m0'

    room.next_module(course,state,context())

    assert state['module_id']=='m2'
    assert state['index']==0 and state['step']=='teach'
