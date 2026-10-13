"""Speculation is model-only; deterministic barriers exercise click/cancel races."""
import copy
import threading
from types import SimpleNamespace
import pytest
from refactor.learning.app.classroom import Classroom,RESET
from refactor.learning.connections.errors import ModelCancelled
from refactor.learning.course import teacher_agent
from tests.refactor.conversation37.test_grounded_conversation37 import make_environment,act,PLAN
from tests.refactor.concept114.test_concept114_classroom import concept_environment

@pytest.fixture
def e(tmp_path,monkeypatch):
    env=make_environment(tmp_path,first_points=2)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:env.materials))
    yield env
    env.room.cancel_materials()
    env.store.close()

def prepare(e):
    co=e.room._course(PLAN);st=e.room._load(co)
    return e.room.prefetch(PLAN,e.room._teaching_version(co,st))

def wait_jobs(e):
    group=e.room._prefetches[PLAN]
    for job in group['jobs'].values():
        with job['condition']:
            assert job['condition'].wait_for(lambda:job['done'],3)
    return group

def test_speed116_ready_hit_no_duplicate_model_and_no_speculative_records(e):
    act(e,'next')
    before=copy.deepcopy(e.store.pending_records(10000))
    assert prepare(e)['total']==4
    wait_jobs(e)
    assert e.store.pending_records(10000)==before
    count=len(e.gateway.calls)
    e.stream.clear();view=act(e,'choose',0)
    assert not view['failure'] and len(e.gateway.calls)==count
    assert e.stream and len([m for m in view['messages'] if m['role']=='user'])==1
    assert not e.store.records('credits',PLAN) and not e.store.records('judgements',PLAN)

def test_speed116_inflight_click_streams_then_finishes(e,monkeypatch):
    act(e,'next');started=threading.Event();release=threading.Event();seen=threading.Event()
    def model(*args,**kw):
        kw['on_text']('先看面积');started.set()
        while not release.wait(.01):
            if kw['cancel'].is_set():raise ModelCancelled()
        kw['on_text']('，压力不变时面积越小压强越大。[c1]')
        return SimpleNamespace(text='先看面积，压力不变时面积越小压强越大。[c1]')
    monkeypatch.setattr(e.gateway,'complete',model)
    prepare(e);assert started.wait(2)
    original=e.stream
    class Stream(list):
        def append(self,x):super().append(x);seen.set()
    e.stream=Stream();answer={}
    thread=threading.Thread(target=lambda:answer.update(view=act(e,'choose',0)))
    thread.start();assert seen.wait(2);assert thread.is_alive()
    release.set();thread.join(3)
    assert not thread.is_alive() and not answer['view']['failure']

def test_speed116_typing_or_changed_state_discards_prepared_answers(e):
    act(e,'next');prepare(e);group=wait_jobs(e)
    count=len(e.gateway.calls)
    view=act(e,'reply','我自己提出一个问题')
    assert len(e.gateway.calls)==count+1 and not view['failure']
    assert all(j['cancel'].is_set() for j in group['jobs'].values())
    assert len([m for m in view['messages'] if m['role']=='user'])==1
    prepare(e);group=wait_jobs(e)
    co=e.room._course(PLAN);st=e.room._load(co);st['new_revision_marker']=1;e.room._save(co,st)
    count=len(e.gateway.calls)
    assert not act(e,'confused')['failure'] and len(e.gateway.calls)==count+1
    assert all(j['cancel'].is_set() for j in group['jobs'].values())

def test_speed116_rewrite_resets_visible_body_and_hides_split_markup(e):
    e.gateway.replies=['我已经修改计划。','中文开场。\n【选项】\n先看压力\n先看面积\n【/选项】']
    view=act(e,'next')
    assert not view['failure'] and '修改计划' not in view['messages'][-1]['content']
    assert RESET in e.stream and '我已经' in ''.join(e.stream[:e.stream.index(RESET)])
    assert '【' not in ''.join(e.stream) and '[c' not in ''.join(e.stream)
    assert teacher_agent.streaming_body('中文[c')=='中文'
    assert teacher_agent.streaming_body('中文【选')=='中文'

def test_speed116_skip_big_point_all_subpoints_credit_once(tmp_path,monkeypatch):
    env=concept_environment(tmp_path);monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:env.materials))
    try:
        act(env,'next');view=act(env,'skip_point')
        assert not view['failure'] and view['concept'] is None
        credits=env.store.records('credits',PLAN)
        assert len(credits)==1 and credits[0]['credit']['minutes']==15
        assert not env.store.records('judgements',PLAN)
        assert not view['direct_review']
    finally:env.room.cancel_materials();env.store.close()

def test_speed116_long_wrap_rewrites_once_and_keeps_every_character(tmp_path,monkeypatch):
    env=concept_environment(tmp_path);monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:env.materials))
    try:
        act(env,'next');act(env,'next');act(env,'next')
        text='我们把感知和输入串起来理解，'*20+'最后再认识UTF-8/16/32的区别。第二句完整保留。'
        env.gateway.replies=[text,text];before=len(env.gateway.calls)
        course=env.room._course(PLAN);state=env.room._load(course);state['concept']['wrapping']=True
        teaching=env.room._teacher(course,state,None,'用一两句收束。',['p0'])
        assert teaching.speech==text
        view=act(env,'next')
        assert not view['failure'] and len(env.gateway.calls)==before+2
        assert view['messages'][-1]['kind']=='recap'
    finally:env.room.cancel_materials();env.store.close()

def test_speed116_concept_continue_prefetch_is_next_subpoint_and_not_credit(tmp_path,monkeypatch):
    env=concept_environment(tmp_path);monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:env.materials))
    try:
        act(env,'next');assert prepare(env)['total']==2
        group=wait_jobs(env);job=group['jobs'][('next',())]
        assert job['tctx']['concept']['subpoint_id']=='p0-s2'
        assert not env.store.records('credits',PLAN)
        count=len(env.gateway.calls);view=act(env,'next')
        assert not view['failure'] and view['concept']['number']==2 and len(env.gateway.calls)==count
    finally:env.room.cancel_materials();env.store.close()

def test_speed116_leave_cancels_all_speculation_and_preserves_position(e):
    view=act(e,'next');prepare(e);group=wait_jobs(e)
    result=act(e,'leave')
    assert not result['failure'] and result['step']==view['step']
    assert not e.room._prefetches and all(j['cancel'].is_set() for j in group['jobs'].values())
    assert not e.store.records('credits',PLAN)

def test_speed116_other_actions_with_list_arguments_discard_prefetch(e):
    act(e,'next');prepare(e);group=wait_jobs(e)
    view=act(e,'answer',[0])
    assert view['failure'] # no pending question; existing STEP_INVALID result
    assert all(j['cancel'].is_set() for j in group['jobs'].values())

def test_speed116_ready_prediction_does_not_replay_its_rejected_draft(e,monkeypatch):
    act(e,'next')
    def model(*args,**kw):
        text='==中文最终讲解。==[c1]' if any('上一条回复格式不合格' in m['content'] for m in args[1]) else '我已经修改计划。'
        kw['on_text'](text)
        return SimpleNamespace(text=text)
    monkeypatch.setattr(e.gateway,'complete',model)
    prepare(e);wait_jobs(e);e.stream.clear()
    view=act(e,'choose',0)
    assert not view['failure'] and e.stream==['==中文最终讲解。==[c1]']
    assert '修改计划' not in ''.join(e.stream)
