"""Simulated acceptance-three rhythm with the real preparation worker/cache."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import threading
import time

import pytest

from refactor.learning.app.classroom import Classroom
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore
from tests.refactor.guided_lessons.test_guided_classroom_contract import Gateway

PLAN = 'e' * 32
DAY = date(2026, 10, 5)


@pytest.fixture
def room30(tmp_path, monkeypatch):
    store = CourseStore('prep30', tmp_path)
    store.create_plan(PLAN, goal='备课时机', final_goal='完成', level='beginner', window=PlanWindow(DAY, DAY+timedelta(days=3), 90))
    store.add_sources(PLAN, [dict(id='s',kind='url',url='https://offline.invalid',chunks=[dict(chunk_id='k1',text='队列先入先出。')])])
    store.save_outline(PLAN, [dict(id='s',title='顺序')], [dict(id=f'm{i}',stage_id='s',title=f'模块{i}') for i in range(3)])
    for i, minutes in enumerate(([20]*3, [16,16,18,18,16], [20]*3)):
        store.save_generated_module(PLAN, f'm{i}', [dict(id=f'p{i}{j}',title=f'点{i}{j}',summary='先入先出',minutes=m,chunk_ids=['k1']) for j,m in enumerate(minutes)],check_minutes=(6,10,6)[i],final_check_minutes=(8,13,8)[i],check_question_minutes=2,today=DAY)
    store.start_plan(PLAN,today=DAY)
    clock = SimpleNamespace(day=DAY)
    gateway = Gateway(); gateway.period='peak'
    prefs = dict(refresh_time='04:00',teacher_style='warm',detail='brief',examples='normal',analogy_domains=['life'])
    services=SimpleNamespace(store=store,gateway=gateway,settings=SimpleNamespace(load=lambda:prefs),account_id='prep30',data_root=tmp_path)
    room=Classroom(services,lambda:clock.day,now=lambda:datetime.combine(clock.day,datetime.min.time(),timezone.utc)+timedelta(hours=2))
    # Bank/profile are another worker's scope; all lesson scheduling, writes,
    # parsing and reviews remain real, only gateway output is synthetic.
    monkeypatch.setattr(room,'_prefetch_banks',lambda *a,**kw:None)
    monkeypatch.setattr(room,'_schedule_idle_prep',lambda *a:None)
    monkeypatch.setattr(room,'_profile_later',lambda *a:None)
    yield room, clock, gateway
    settle(room)
    store.close()


def settle(room):
    deadline=time.monotonic()+5
    while room._preparing:
        assert time.monotonic()<deadline, 'preparation did not finish'
        time.sleep(.001)


def test_peak_after_daily_target_still_has_current_plus_three(room30):
    room,_,_=room30; course=room._course(PLAN); state=room._load(course)
    state['day']=DAY.isoformat()
    room._today=lambda *a:dict(today_confirmed=64,today_tentative=0)
    targets=room._prep_targets(course,state)
    assert [p['id'] for p in targets]==['p00','p01','p02','p10']
    assert len(room._prep_targets(course,dict(state,start_only=True)))==1


def test_acceptance_three_days_skip_boundary_and_continue_no_foreground_preparation(room30):
    room, clock, gateway=room30
    room.prepare_started_plan(PLAN); settle(room)
    foreground=[]
    original=room._prepare_point
    def recorded(*a,**kw):
        if getattr(room._prep_context_local,'foreground',None):foreground.append(a[1]['id'])
        return original(*a,**kw)
    room._prepare_point=recorded
    def call(method,*args):
        return room.run(PLAN,lambda co,st,cx:getattr(room,method)(co,st,cx,*args))
    call('enter'); settle(room)
    # D1 complete module one using its existing lesson; artificial points'
    # reference content is fixed, so the correct choice is always first.
    taught=[]
    def learn_module():
        view=call('next')
        for _ in range(60):
            if view['step']=='test_offer':return
            if view['step']=='teach':view=call('next')
            elif view['step']=='lesson':
                if view['lesson']['step']==1:taught.append(view['lesson']['point_id'])
                view=call('choose',0) if view['lesson']['awaiting']=='choice' else call('next')
            elif view['step']=='answer':view=call('reply',view['question']['options'][0])
            else:raise AssertionError(view['step'])
            # Scheduler gets CPU between synthetic learner actions, rather
            # than replacing Thread.start with synchronous foreground work.
            settle(room)
        raise AssertionError('lesson loop')
    learn_module();call('leave')
    # D2 advances by directly mastering/skipping module two, matching the
    # acceptance script. Detection/judging themselves are not under test.
    clock.day=DAY+timedelta(days=1)
    def skip_to_third(co,st,cx):
        from refactor.learning.course.mastery import Judgement, AttemptClosed
        for module in co.modules[:2]:
            # Real store judgement API, no fabricated lesson cache.
            points=co.points_of(module['id'])
            attempt='skip-'+module['id']
            co.store.append_judgement(PLAN, Judgement(attempt,co.next_seq(),room.now(),'skip_test',module['id'],attempt,tuple((p['id'],True) for p in points)))
            co.store.append_attempt_closed(PLAN, AttemptClosed(attempt+'-closed',co.next_seq(),module['id'],attempt,'skip_test'))
        st.update(module_id='m1',index=0,step='test_done',day=clock.day.isoformat())
        room.next_module(co,st,cx)
    room.run(PLAN,skip_to_third);settle(room)
    co=room._course(PLAN)
    assert all(room._prepared_point(co,p) is not None for p in co.points_of('m2'))
    clock.day=DAY+timedelta(days=2)
    call('enter');learn_module()  # no artificial warm-up before D3's first next
    assert len(set(taught))==6
    assert foreground==[]
    assert gateway.calls.count('lesson_prep')>0 and gateway.calls.count('lesson_review')>0


def test_running_worker_updates_targets_before_next_batch(room30,monkeypatch):
    room,_,_=room30;co=room._course(PLAN);st=room._load(co)
    jobs=[];seen=[]
    import refactor.learning.app.classroom as module
    class Thread:
        def __init__(self,*,target,**kw):self.fn=target;jobs.append(self)
        def start(self):pass
    monkeypatch.setattr(module.threading,'Thread',Thread)
    def batch(c,points,keep):
        seen.extend(p['id'] for p in points)
        if len(seen)==2:room._prepare_later(c,dict(st,module_id='m2',index=0))
    monkeypatch.setattr(room,'_prepare_batch',batch)
    monkeypatch.setattr(room,'_prepare_point',lambda c,p,k:seen.append(p['id']))
    room._prepare_later(co,st);jobs[0].fn()
    assert seen[:2]==['p00','p01']
    assert seen[2:]==['p20','p21','p22']  # no obsolete p02/p10 batch first
    for job in jobs[1:]:job.fn()
