"""The lesson route stays usable while assessment material is still preparing."""
from threading import Event

from refactor.learning.course.teacher_agent import Teaching
from tests.refactor.integration38.support import context
from tests.refactor.plan66.test_plan66_just_in_time_materials import DAY, PLAN, env


def _prepare_stub(room, calls):
    def prepare(course, module_id, *, retry=False):
        calls.append((module_id, retry))
        job=dict(module_id=module_id,status='running',cancel=Event(),done=0,total=4,text='正在准备题目',error=None,
                 fingerprint=room._fingerprint(course,module_id))
        room._jobs[(course.plan_id,module_id)]=job
        return job
    return prepare


def test_188_lesson_opens_and_advances_without_bank_readiness(env, monkeypatch):
    e=env;room=e.room;course=room._course(PLAN)
    ready={'value':False}
    monkeypatch.setattr(room,'_ready',lambda *_a,**_k:ready['value'])
    room._teacher=lambda *a,**k:Teaching('沿着原文继续讲解。',())
    room._prepare_one=lambda *a,**k:(_ for _ in ()).throw(AssertionError('lesson must not prepare a question'))
    room._prepare_next_module=lambda *a,**k:None
    state=room._rebuild(course)
    state.update(module_id='m1',index=0,step='teach',phase='learning',day=DAY.isoformat(),in_session=True)
    room._save(course,state)

    opened=room.perform(PLAN,'next',context())
    assert opened['step']=='opening' and opened['question'] is None
    assert 'material_wait' not in room._load(room._course(PLAN))
    room.perform(PLAN,'choose',context(),(0,))
    finished=room.perform(PLAN,'next',context())
    assert finished['step']=='test_offer'
    assert not finished.get('material_notice')
    assert not room._load(room._course(PLAN)).get('material_wait')


def test_188_assessment_stays_at_offer_and_becomes_available_when_ready(env, monkeypatch):
    e=env;room=e.room;course=room._course(PLAN);calls=[]
    ready={'value':False}
    monkeypatch.setattr(room,'_ready',lambda *_a,**_k:ready['value'])
    monkeypatch.setattr(room,'_prepare',_prepare_stub(room,calls))
    room._materials=lambda:type('Materials',(),{'mark_used':staticmethod(lambda *_a,**_k:None)})()
    room._prepare_next_module=lambda *a,**k:None
    state=room._rebuild(course);state.update(module_id='m0',index=2,step='test_offer',phase='learning')
    room._save(course,state)

    waiting=room.perform(PLAN,'start_test',context(),('module_test',))
    assert waiting['step']=='test_offer'
    assert waiting['material_notice']['kind']=='module_test'
    assert waiting['test_readiness']['module_test'] is False
    assert calls==[('m0',True)]

    ready['value']=True
    refreshed=room.run(PLAN,lambda *_a:None,context())
    assert refreshed['test_readiness']['module_test'] is True
    assert refreshed.get('material_notice') is None
    started=room.perform(PLAN,'start_test',context(),('module_test',))
    assert started['step']=='testing' and started['question'] is not None


def test_188_direct_test_notice_can_be_dismissed_then_teaching_continues(env, monkeypatch):
    e=env;room=e.room;course=room._course(PLAN);calls=[]
    monkeypatch.setattr(room,'_ready',lambda *_a,**_k:False)
    monkeypatch.setattr(room,'_prepare',_prepare_stub(room,calls))
    room._teacher=lambda *a,**k:Teaching('沿着原文继续讲解。',())
    room._prepare_next_module=lambda *a,**k:None
    state=room._rebuild(course);state.update(module_id='m0',index=0,step='teaching',phase='learning',
        conversation=dict(point_id='p0',options=[],interrupted=False))
    room._save(course,state)

    waiting=room.perform(PLAN,'start_test',context(),('skip_test',))
    assert waiting['step']=='teaching' and waiting['material_notice']['kind']=='skip_test'
    resumed=room.perform(PLAN,'stop_preparation',context())
    assert resumed['step']=='teaching' and resumed.get('material_notice') is None
    continued=room.perform(PLAN,'next',context())
    assert continued['step']=='test_offer' and continued['question'] is None
    assert e.store.records('credits',PLAN)
    assert calls==[('m0',True)]


def test_188_unmarked_empty_bank_does_not_bypass_assessment_preparation(env, monkeypatch):
    room=env.room;course=room._course(PLAN);calls=[]
    monkeypatch.setattr(room,'_ready',lambda *_a,**_k:False)
    monkeypatch.setattr(room,'_prepare',_prepare_stub(room,calls))
    room._prepare_next_module=lambda *a,**k:None
    state=room._rebuild(course);state.update(module_id='m1',index=0,step='teach',phase='learning')
    room._save(course,state)

    view=room.perform(PLAN,'start_test',context(),('skip_test',))

    assert view['step']=='teach'
    assert view['material_notice']['kind']=='skip_test'
    assert not view.get('direct_review')
    assert calls==[('m1',True)]


def test_188_switching_modules_preserves_exact_subpoint_and_does_not_credit(env, monkeypatch):
    e=env;room=e.room;course=room._course(PLAN)
    room._prepare_next_module=lambda *a,**k:None
    state=room._rebuild(course)
    state.update(module_id='m0',index=0,step='teaching',phase='learning',
        conversation=dict(point_id='p0',options=[],interrupted=True),
        concept=dict(point_id='p0',subpoint_id='p0-s2',rounds=3,style_history=['calm']),
        answer_explanation='旧模块的答案解释',recall_result={'old':True})
    room._save(course,state)
    credits_before=e.store.records('credits',PLAN)

    other=room.perform(PLAN,'next_module',context())
    assert other['module']['id']=='m1' and other['step']=='teach'
    other_state=room._load(room._course(PLAN))
    assert other_state.get('concept') is None
    assert other_state.get('conversation') is None
    assert other_state.get('answer_explanation') is None
    assert other_state.get('recall_result') is None
    restored=room.perform(PLAN,'next_module',context(),('m0',))
    assert restored['module']['id']=='m0' and restored['step']=='teaching'
    restored_state=room._load(room._course(PLAN))
    assert restored_state['concept']==state['concept']
    assert restored_state['conversation']==state['conversation']
    assert e.store.records('credits',PLAN)==credits_before
    # m1 was actually entered but still needs teaching, so it remains a
    # learnable module rather than being mislabeled as a deferred test.
    assert not any(item['module_id']=='m1' for item in restored['deferred_module_tests'])
    assert any(item['module_id']=='m1' for item in restored['module_options'])
    assert not any(item['module_id']=='m2' for item in restored['deferred_module_tests'])

    reopened=room.__class__(room.services,lambda:DAY)
    reopened_state=reopened._load(reopened._course(PLAN))
    assert reopened_state['module_id']=='m0'
    assert reopened_state['concept']==state['concept']
    assert reopened_state['conversation']==state['conversation']


def test_188_final_check_wait_does_not_enter_preparing_step(env, monkeypatch):
    e=env;room=e.room;course=room._course(PLAN);calls=[]
    monkeypatch.setattr(room,'_ready',lambda _c,mid,**_k:mid!='m1')
    monkeypatch.setattr(room,'_prepare',_prepare_stub(room,calls))
    room._prepare_next_module=lambda *a,**k:None
    state=room._rebuild(course);state.update(module_id='m0',step='check_offer',phase='learning')
    room._save(course,state)
    view=room.perform(PLAN,'start_check',context())
    assert view['step']=='check_offer'
    assert view['material_notice']['kind']=='check'
    assert view['test_readiness']['check'] is False
    assert calls==[('m1',True)]


def test_188_legacy_next_wait_is_normalized_on_enter(env):
    room=env.room;course=room._course(PLAN)
    state=room._rebuild(course)
    state.update(step='preparing_materials',material_wait=dict(module_id='m0',action='next',args=[],
        previous_step='opening',done=0,total=8,text='正在准备题目',error=None))
    room._save(course,state)
    view=room.perform(PLAN,'enter',context())
    saved=room._load(room._course(PLAN))
    assert view['step']=='opening'
    assert 'material_wait' not in saved


def test_188_lecture_action_cannot_enter_legacy_material_wait(env):
    import pytest
    room=env.room;course=room._course(PLAN);state=room._rebuild(course)
    state.update(module_id='m0',step='opening')
    with pytest.raises(AssertionError,match='reserved for legacy assessment'):
        room._wait(course,state,'next')


def test_188_optional_material_queue_failure_does_not_block_lesson_and_can_retry(env, monkeypatch):
    room=env.room;course=room._course(PLAN)
    state=room._rebuild(course)
    state.update(module_id='m0',index=0,step='teach',phase='learning')
    room._save(course,state)
    room._teacher=lambda *a,**k:Teaching('沿着原文继续讲解。',())
    attempts=[]
    def fail(*_a,**_k):
        attempts.append('failed')
        raise RuntimeError('queue unavailable')
    monkeypatch.setattr(room,'_prepare_next_module',fail)
    view=room.perform(PLAN,'next',context())
    state=room._load(room._course(PLAN))
    assert state['step']=='opening'
    assert not view['failure']
    assert state['preparation_error']['module_id']=='m0'
    view=room.snapshot(course,state)
    assert view['preparation_error']['message']

    monkeypatch.setattr(room,'_prepare',lambda _course,mid,*,retry=False:attempts.append((mid,retry)))
    room.retry(course,state,context())
    assert attempts==['failed',('m0',True)]
    assert 'preparation_error' not in state
