from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
import threading
import time
from types import SimpleNamespace

import pytest

from refactor.learning.app.classroom import Classroom, register
from refactor.learning.app.bridge import BridgeError
from refactor.learning.course import lesson_prep
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.planner_agents import AgentFormatError
from refactor.learning.course.store import CourseStore
from refactor.learning.connections.errors import ModelError
from tests.refactor.guided_lessons.fixtures import wire, draft

PLAN='a'*32
DAY=date(2026,10,1)


class Gateway:
    def __init__(self):
        self.calls=[]; self.reviews=[]; self.provider='deepseek'; self.period='off_peak'
        self.price_catalog=SimpleNamespace(_period=lambda at:self.period)
    def get_configuration(self): return {'provider_id': self.provider}
    def complete(self,purpose,messages,**kwargs):
        self.calls.append(purpose)
        task=json.loads(messages[-1]['content'].split('\n',1)[1])
        if purpose=='lesson_prep':
            text=json.dumps({'lessons':[{'point_id': p['id'], 'lesson': wire(p['chunk_ids'][0])} for p in task['points']]},ensure_ascii=False) if 'points' in task else json.dumps(wire(task['chunk_ids'][0]),ensure_ascii=False)
        elif purpose=='lesson_review':
            def review(item):
                result=self.reviews.pop(0) if self.reviews else 'approve'
                if isinstance(result, AgentFormatError) and isinstance(task,list):
                    return dict(point_id=item['point']['id'],verdict='invalid',issues=[],revised=None)
                if isinstance(result, Exception): raise result
                value=dict(verdict=result,issues=[] if result=='approve' else ['需修改'],
                           revised=item['draft'] if result=='revise' else None)
                if isinstance(task,list):value['point_id']=item['point']['id']
                return value
            text=json.dumps(dict(reviews=[review(item) for item in task]) if isinstance(task,list) else review(task),ensure_ascii=False)
        elif purpose=='teacher' and kwargs['system'].startswith('现场分步'):
            text=json.dumps(wire(task['chunk_ids'][0],live=True),ensure_ascii=False)
        elif purpose=='teacher':
            text='我们只接这次插话，点回到刚才继续。'
            if kwargs.get('on_text'): kwargs['on_text'](text)
        else: raise AssertionError(purpose)
        return SimpleNamespace(text=text)


@pytest.fixture
def env(tmp_path,monkeypatch):
    store=CourseStore('guided_test',tmp_path)
    store.create_plan(PLAN,goal='学顺序',final_goal='能说明',level='beginner',window=PlanWindow(DAY,DAY+timedelta(days=2),30))
    store.add_sources(PLAN,[dict(id='s',kind='url',url='https://example.invalid',chunks=[dict(chunk_id='k1',text='队列先入先出。')])])
    store.save_outline(PLAN,[dict(id='stage',title='顺序')],[dict(id='m0',stage_id='stage',title='顺序'),dict(id='m1',stage_id='stage',title='应用')])
    for i in range(2):
        store.save_generated_module(PLAN,f'm{i}',[dict(id=f'p{i}{j}',title=f'点{i}{j}',summary='先入先出',minutes=15,chunk_ids=['k1']) for j in range(4)],check_minutes=8,today=DAY)
    store.start_plan(PLAN,today=DAY)
    gateway=Gateway()
    prefs=dict(refresh_time='04:00',teacher_style='warm',detail='brief',examples='normal',analogy_domains=['life'])
    services=SimpleNamespace(store=store,gateway=gateway,settings=SimpleNamespace(load=lambda:prefs),account_id='guided_test',data_root=tmp_path)
    room=Classroom(services,lambda:DAY,now=lambda:datetime(2026,10,1,2,tzinfo=timezone.utc))
    # Separate scheduler/foreground tests from arbitrary bank generation.
    monkeypatch.setattr(room,'_prefetch_banks',lambda *a,**kw:None)
    monkeypatch.setattr(room,'_prepare_later',lambda *a,**kw:None)
    monkeypatch.setattr(room,'_schedule_idle_prep',lambda *a:None)
    e=SimpleNamespace(store=store,gateway=gateway,prefs=prefs,services=services,room=room)
    yield e
    store.close()


def act(e,name,*args):
    context=SimpleNamespace(cancel=threading.Event(),stream=lambda _:None)
    view=e.room.run(PLAN,lambda co,st,cx:getattr(e.room,name)(co,st,cx,*args),context)
    # These legacy tests inspect settled lessons. Nonblocking bridge timings
    # are measured separately in test_prep32_nonblocking.py, without this wait.
    deadline=time.monotonic()+3
    while view['step']=='preparing_lesson':
        assert time.monotonic()<deadline
        time.sleep(.01)
        view=e.room.run(PLAN,lambda *a:None,context)
    return view


def test_new_prep_validates_steps_refs_options_and_two_whole_questions():
    data=wire()
    prep=lesson_prep.parse_prep(json.dumps(data),chunk_ids=['k1'])
    assert len(prep.steps)==3 and len(prep.questions)==2
    for mutate in (lambda d:d['steps'].pop(),lambda d:d['steps'][1]['options'].pop(),
                   lambda d:d['steps'][0].update(chunk_ids=['outside']),lambda d:d['questions'][0].update(answer='outside'),
                   lambda d:d['questions'][1].update(required_points=['a','b','c'])):
        data=wire();mutate(data)
        with pytest.raises(AgentFormatError): lesson_prep.parse_prep(json.dumps(data),chunk_ids=['k1'])
    assert not hasattr(lesson_prep,'casual_repeats_example')


def test_guided_steps_choice_interrupt_return_reopen_and_fast_correct(env):
    e=env
    act(e,'enter'); v=act(e,'next')
    assert v['lesson']==dict(point_id='p00',point_title='点00',step=1,total=3,awaiting='continue',options=[])
    assert v['messages'][-1]['kind']=='step' and 'mf-source:k1' in v['messages'][-1]['content']
    assert len(e.gateway.calls)==2
    v=act(e,'next');assert v['lesson']['awaiting']=='choice'
    before=len(e.gateway.calls);v=act(e,'choose',1)
    assert v['lesson']['awaiting']=='continue' and len(e.gateway.calls)==before
    assert [m['kind'] for m in v['messages'][-2:]]==['choice','step_reply']
    assert e.store.records('judgements',PLAN)==[]
    v=act(e,'reply','想聊一下午饭');assert v['lesson']['awaiting']=='return'
    with pytest.raises(BridgeError):act(e,'next')
    # Reopening restores the step and its continue choice rather than advancing.
    e.room=Classroom(e.services,lambda:DAY)
    # Reopened rooms need the same scheduler isolation as the fixture room;
    # background prefetch calls must not race the foreground no-call assertion.
    e.room._prefetch_banks=lambda *a,**kw:None
    e.room._prepare_later=lambda *a,**kw:None
    e.room._schedule_idle_prep=lambda *a:None
    v=act(e,'enter');assert v['lesson']['step']==2 and v['lesson']['awaiting']=='return'
    v=act(e,'back');assert v['lesson']['awaiting']=='continue'
    v=act(e,'next');assert v['lesson']['step']==3
    v=act(e,'next');assert v['question']['type']=='choice'
    # Pre-approve next point to measure the no-call path.
    co=e.room._course(PLAN); p=co.point('p01')
    e.store.save_lesson_cache_item(PLAN,'lesson_preps','p01',dict(version=lesson_prep.VERSION,status='reviewed',fingerprint=e.room._prep_fingerprint(co,p),lesson=asdict(draft('k1'))))
    before=len(e.gateway.calls);start=time.perf_counter()
    v=act(e,'reply','先进入的先取出')
    assert time.perf_counter()-start<1
    assert len(e.gateway.calls)==before and v['lesson']['point_id']=='p01' and v['lesson']['step']==1
    assert [m['content'] for m in v['messages'] if m['kind']=='verdict'][-1]=='✓ 答对了。'
    assert len(e.store.records('judgements',PLAN))==1


def test_wrong_uses_prepared_second_reference_then_moves_on(env):
    e=env;act(e,'enter');act(e,'next');act(e,'next');act(e,'choose',0);act(e,'next');v=act(e,'next')
    old=e.room._file(PLAN).read({})['pending']['question']
    v=act(e,'reply','随机')
    new=e.room._file(PLAN).read({})['pending']['question']
    assert new['stem']!=old['stem'] and new['answer']=='甲' and new['reference']=='甲先取出'
    v=act(e,'reply','乙')
    assert len(e.store.records('judgements',PLAN))==2 and v['lesson']['point_id']=='p01'


def test_timeout_keeps_draft_and_counters_across_reentry(env):
    e=env;co=e.room._course(PLAN);p=co.point('p00')
    e.gateway.reviews=[ModelError('MODEL_TIMEOUT','核对超时'),'approve']
    e.room._prepare_point(co,p,{'p00'})
    row=e.store.load_lesson_cache(PLAN)['lesson_preps']['p00']
    assert row['generation_attempts']==1 and row['review_attempts']==1 and row['lesson']
    e.room=Classroom(e.services,lambda:DAY)
    e.room._prepare_point(e.room._course(PLAN),p,{'p00'})
    row=e.store.load_lesson_cache(PLAN)['lesson_preps']['p00']
    assert row['status']=='reviewed' and row['generation_attempts']==1 and row['review_attempts']==2
    assert e.gateway.calls==['lesson_prep','lesson_review','lesson_review']


def test_revision_is_reviewed_and_rewrite_once_then_terminal(env):
    e=env;co=e.room._course(PLAN);p=co.point('p00')
    e.gateway.reviews=['revise','approve'];e.room._prepare_point(co,p,{'p00'})
    row=e.store.load_lesson_cache(PLAN)['lesson_preps']['p00'];assert row['generation_attempts']==1 and row['total_reviews']==2
    p=co.point('p01');e.gateway.reviews=['revise','revise','revise','revise']
    e.room._prepare_point(co,p,{'p00','p01'})
    row=e.store.load_lesson_cache(PLAN)['lesson_preps']['p01']
    assert row['generation_attempts']==2 and row['total_reviews']==4 and row['status']=='needs_live_teaching'
    before=list(e.gateway.calls);e.room._prepare_point(co,p,{'p00','p01'});assert before==e.gateway.calls


def test_content_fingerprint_daily_window_and_peak_scheduling(env):
    e=env;co=e.room._course(PLAN);p=co.point('p00')
    fp=e.room._prep_fingerprint(co,p)
    co.plan['window']={'start':'2050-01-01','end':'2050-04-01','daily_cap':3}
    assert e.room._prep_fingerprint(co,p)==fp
    e.prefs['detail']='detailed';assert e.room._prep_fingerprint(co,p)!=fp
    state=e.room._load(co);state['day']=DAY.isoformat()
    e.gateway.period='peak';assert len(e.room._prep_targets(co,state))<=4
    e.gateway.period=None;assert e.room._peak_prep()
    e.gateway.period='off_peak';assert len(e.room._prep_targets(co,state))>=4
    e.gateway.provider='other';assert not e.room._peak_prep()
    e.room._credit_learning(co,state,p)
    assert 'p00' not in [p['id'] for p in e.room._prep_targets(co,state)]


def test_terminal_unverified_point_live_is_structured_and_marked(env):
    e=env;co=e.room._course(PLAN);p=co.point('p00')
    e.gateway.reviews=['live','live'];e.room._prepare_point(co,p,{p['id'] for p in co.points})
    v=act(e,'next')
    assert v['lesson']['total']==2 and v['messages'][-1]['kind']=='step'
    assert e.store.load_lesson_cache(PLAN)['lesson_preps']['p00']['live_used']


def test_choose_back_bridge_contract(env,monkeypatch):
    e=env; methods={}
    registry=SimpleNamespace(register=lambda name,fn:methods.__setitem__(name,fn))
    register(registry,lambda:e.services,lambda settings:DAY)
    # New methods must exist even before room creation; params are validated.
    assert {'classroom.choose','classroom.back'}<=set(methods)
    with pytest.raises(BridgeError):methods['classroom.choose'](dict(plan_id=PLAN,option=True),None)


def test_review_format_failure_retains_batch_drafts_and_is_bounded(env):
    e=env;co=e.room._course(PLAN);ps=[co.point('p00'),co.point('p01')]
    e.gateway.reviews=[AgentFormatError('坏格式'),'approve']
    e.room._prepare_batch(co,ps,{p['id'] for p in co.points})
    rows=e.store.load_lesson_cache(PLAN)['lesson_preps']
    assert rows['p00']['lesson'] and rows['p00']['status']=='review_pending'
    assert rows['p01']['status']=='reviewed' and e.gateway.calls.count('lesson_prep')==1
    e.gateway.reviews=[ModelError('MODEL_TIMEOUT','超时'),'approve']
    e.room._prepare_point(co,ps[0],{p['id'] for p in co.points})
    row=e.store.load_lesson_cache(PLAN)['lesson_preps']['p00']
    assert row['generation_attempts']==2 and row['total_reviews']==3 and row['status']=='reviewed'
    assert e.gateway.calls.count('lesson_prep')==2


def test_cancel_and_write_failure_do_not_drop_existing_draft(env,monkeypatch):
    from refactor.learning.connections.errors import ModelCancelled
    from refactor.learning.course.store import StoreError
    e=env;co=e.room._course(PLAN);p=co.point('p00');keep={p['id']}
    e.gateway.reviews=[ModelCancelled()]
    with pytest.raises(ModelCancelled):e.room._prepare_point(co,p,keep)
    row=e.store.load_lesson_cache(PLAN)['lesson_preps']['p00']
    assert row['lesson'] and row['generation_attempts']==1 and row['review_attempts']==1
    before=list(e.gateway.calls)
    def broken(*args,**kw):raise StoreError('IO_FAILURE','写盘失败')
    monkeypatch.setattr(e.store,'save_lesson_cache_item',broken)
    with pytest.raises(StoreError):e.room._prepare_point(co,p,keep)
    assert before==e.gateway.calls


def test_reopen_before_choosing_preserves_options_and_short_reference(env):
    e=env;act(e,'next');v=act(e,'next');assert v['lesson']['awaiting']=='choice'
    e.room=Classroom(e.services,lambda:DAY)
    v=act(e,'enter');assert v['lesson']['options']==['甲','乙'] and v['lesson']['step']==2
    co=e.room._course(PLAN);p=co.point('p00');e.prefs['detail']='detailed'
    assert e.room._prepared_point(co,p) is None
    q=lesson_prep.parse_prep(json.dumps(wire(short=True))).questions[1].pending('p00')
    assert q['type']=='short' and q['reference']=='甲先取出' and q['required_points']==['甲先取出']


def test_idle_timer_only_runs_on_entered_day_and_after_peak(env,monkeypatch):
    import refactor.learning.app.classroom as mod
    e=env;co=e.room._course(PLAN);state=e.room._load(co);state['day']=DAY.isoformat()
    timers=[];calls=[]
    class Timer:
        def __init__(self,delay,fn):self.fn=fn;timers.append(self)
        def start(self):pass
        def is_alive(self):return True
    monkeypatch.setattr(mod.threading,'Timer',Timer)
    monkeypatch.setattr(e.room,'_prepare_later',lambda *a: calls.append('lessons'))
    monkeypatch.setattr(e.room,'_prefetch_banks',lambda *a,**kw:calls.append('banks'))
    e.gateway.period='peak';Classroom._schedule_idle_prep(e.room,co,state);timers[-1].fn()
    assert calls==[]
    e.gateway.period='off_peak';timers[-1].fn();assert calls==['banks','lessons']
    e.room.study_day=lambda:DAY+timedelta(days=1);before=len(timers);timers[-1].fn()
    assert calls==['banks','lessons'] and len(timers)==before


def test_background_advance_replenishes_after_running_job(env,monkeypatch):
    import refactor.learning.app.classroom as mod
    e=env;co=e.room._course(PLAN);state=e.room._load(co)
    jobs=[];seen=[]
    class Thread:
        def __init__(self,*,target,**kw):self.fn=target;jobs.append(self)
        def start(self):pass
    monkeypatch.setattr(mod.threading,'Thread',Thread)
    monkeypatch.setattr(e.room,'_prepare_batch',lambda c,ps,k:seen.extend(p['id'] for p in ps))
    monkeypatch.setattr(e.room,'_prepare_point',lambda c,p,k:seen.append(p['id']))
    monkeypatch.setattr(e.room,'_prepare_later',Classroom._prepare_later.__get__(e.room))
    e.room._prepare_later(co,state)
    advanced=dict(state,index=2);e.room._prepare_later(co,advanced)
    assert len(jobs)==1
    jobs[0].fn();assert len(jobs)==2
    jobs[1].fn();assert 'p02' in seen and not e.room._preparing


def test_plan_start_prepares_first_day_without_foreground_model(env,monkeypatch):
    e=env;calls=[]
    monkeypatch.setattr(e.room,'_prepare_later',lambda co,st:calls.append(st))
    e.room.prepare_started_plan(PLAN)
    assert len(calls)==1 and calls[0]['start_only'] and calls[0]['day']==DAY.isoformat()
    assert not e.gateway.calls


def test_executable_code_not_counted_as_paragraph_but_long_prose_rejected():
    data=wire(code="a = ['orange', 'apple', 'pear']\na.append('grape')\nprint(a) # ['orange', 'apple', 'pear', 'grape']\nprint(a.append('kiwi')) # None")
    data['steps'][1]['text']='解释。'*25+'\n```python\n'+"a = ['orange', 'apple', 'pear']\na.append('grape')\nprint(a) # ['orange', 'apple', 'pear', 'grape']\nprint(a.append('kiwi')) # None"+'\n```'
    prep=lesson_prep.parse_prep(json.dumps(data));assert not lesson_prep.check_code(prep)
    data['steps'][0]['text']='长'*261
    with pytest.raises(AgentFormatError):lesson_prep.parse_prep(json.dumps(data))
    data=wire();data['steps'][0]['chunk_ids']=[[]]
    with pytest.raises(AgentFormatError):lesson_prep.parse_prep(json.dumps(data))


def test_peak_defers_next_bank_off_peak_prepares_it(env,monkeypatch):
    e=env;co=e.room._course(PLAN);state=e.room._load(co);requested=[]
    monkeypatch.setattr(e.room,'_prepare_bank_later',lambda co,mid:requested.append(mid))
    e.gateway.period='peak';Classroom._prefetch_banks(e.room,co,state,testing=True)
    assert requested==['m0']
    requested.clear();e.gateway.period='off_peak';Classroom._prefetch_banks(e.room,co,state,testing=True)
    assert requested==['m0','m1']


def test_offline_step_choice_and_verdict_latencies(env):
    from pathlib import Path
    e=env;act(e,'next');timings={}
    start=time.perf_counter();act(e,'next');timings['next_step']=time.perf_counter()-start
    start=time.perf_counter();act(e,'choose',0);timings['guide_response']=time.perf_counter()-start
    act(e,'next');act(e,'next')
    co=e.room._course(PLAN);p=co.point('p01');e.room._save_prepared(co,p,draft('k1'),{p['id'] for p in co.points})
    emitted=[];ctx=SimpleNamespace(cancel=threading.Event(),stream=lambda x:emitted.append((time.perf_counter(),x)))
    before=len(e.gateway.calls);start=time.perf_counter()
    e.room.run(PLAN,lambda co,st,cx:e.room.reply(co,st,cx,'先进入的先取出'),ctx)
    timings['choice_verdict']=emitted[0][0]-start
    timings['next_point_step']=time.perf_counter()-start
    assert len(e.gateway.calls)==before and max(timings.values())<1
    timings['kind']='isolated local fake-model timings; not real model latency'
    Path(r'D:\MentorFlow\.local\test-tmp\guided-lesson\offline-latencies.json').write_text(json.dumps(timings,indent=2),encoding='utf-8')
