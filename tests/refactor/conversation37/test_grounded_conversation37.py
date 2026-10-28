"""Actual classroom host/store, explicit pending MF04 API substitute; no model IO."""
import copy
import json
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from refactor.learning.app.classroom import Classroom, RESET
from refactor.learning.app.bridge import BridgeError
from refactor.learning.connections.errors import ModelError
from refactor.learning.course import teacher_agent
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore, StoreError
from refactor.learning.course.report import build_report
from refactor.learning.preferences.storage import StorageError

ACCOUNT='mfa1_'+'c'*64
PLAN='a'*32
DAY=date(2026,10,3)
SETTINGS=dict(teacher_style='warm',detail='brief',examples='few',analogy_domains=['life'],refresh_time='04:00')
PARAGRAPH='压强是物体所受压力与受力面积之比，公式p=F/S。压力相同时，受力面积越小，压强越大。图中手指与针尖受力面积不同。'

def question(pid, kind='choice', index=0):
    source=dict(title='压强原文',url='https://example.invalid/pressure',locator='压强 / 第1段')
    answers={'choice':[0],'multi':[0,2],'fill':[['F/S','压力/面积'],['Pa','帕']],
             'short':'压力相同时，受力面积越小，压强越大。','essay':'压强是压力与受力面积之比。','calc':'p=F/S=20/0.01=2000Pa。'}
    q=dict(id=f'{pid}-{kind}-{index}',module_id='m0' if pid=='p0' else 'm1',point_ids=[pid],kind=kind,
           stem='压力相同时，受力面积减小，压强怎样变化？',answer=answers[kind],explanation='',origin='found',
           source=source,answer_source=source,importance='key',role='bank',uses=[],created_at='2026-10-03T00:00:00+00:00')
    if kind in ('choice','multi'):q['options']=['增大','减小','与受力面积有关']
    return q

class Materials:
    """Only MF04's unmerged boundary is substituted; grading/storage are real."""
    def __init__(self):
        self.bank=[question(pid,index=i) for pid in ('p0','p1') for i in range(1)]
        for pid in ('p0','p1'):
            q=question(pid);q.update(id=f'{pid}-quick',role='quick',origin='cloze',explanation=PARAGRAPH)
            self.bank.append(q)
        self.selected=[];self.used=[];self.prepare=[]
    def pick_question(self,store,plan_id,point_id,purpose,exclude_ids=()):
        self.selected.append((point_id,purpose,exclude_ids))
        rows=[q for q in self.bank if point_id in q['point_ids'] and q['id'] not in exclude_ids and q['role']==('quick' if purpose=='quick' else 'bank')]
        return copy.deepcopy(min(rows,key=lambda q:len(q['uses']))) if rows else None
    def mark_used(self,store,plan_id,question_id,purpose):
        self.used.append((question_id,purpose))
        next(q for q in self.bank if q['id']==question_id)['uses'].append(dict(purpose=purpose,at='now'))
    def prepare_module(self,store,plan_id,module_id,ctx,progress):
        self.prepare.append(module_id);progress(1,1,'已准备原文和题目')
    def prepare_point(self,store,plan_id,point,ctx):
        self.prepare.append(point['id'])

class Gateway:
    def __init__(self):self.calls=[];self.replies=[];self.verdicts=[];self.fail=False
    def complete(self,purpose,messages,**kw):
        self.calls.append(purpose)
        if self.fail:raise ModelError('MODEL_CONNECTION_FAILED','模型连接失败，请重试。')
        task=json.loads(next(m['content'].split('\n',1)[1] for m in reversed(messages) if m['content'].startswith('【本步任务】'))) if purpose=='teacher' else None
        if purpose=='teacher':
            explicit=bool(self.replies)
            text=self.replies.pop(0) if self.replies else ('这一章看压力与面积，针尖和手指你觉得哪个压强大？[c1]\n【选项】\n针尖\n手指\n不知道\n【/选项】' if task['opening'] else
                ('硬度与材料有关，不能只凭受力面积推断硬度相同。' if '硬度' in task['user'] else '压力相同，面积越小压强越大，我们顺着面积看。[c1]'))
            if not explicit:text='==压力相同时，受力面积越小，压强越大。==\n'+text
            if task.get('qa') and not text.startswith('【依据】'):
                text=('【依据】supplement【/依据】' if '硬度' in task['user'] else
                      '【依据】material:c1【/依据】' if task.get('question_only') else '【依据】lesson【/依据】')+text
            if kw.get('on_text'):
                for char in text:kw['on_text'](char)
        else:raise AssertionError('计分不得调用模型：'+purpose)
        return SimpleNamespace(text=text)

def make_environment(root,first_points=1):
    store=CourseStore(ACCOUNT,root)
    store.create_plan(PLAN,goal='理解压强',final_goal='用p=F/S比较压强',level='beginner',window=PlanWindow(DAY,DAY+timedelta(days=2),120))
    store.add_sources(PLAN,[dict(id='s',kind='url',url='https://example.invalid/pressure',title='压强原文',adopted=True,
        chunks=[dict(chunk_id='c0',text=PARAGRAPH,section=['压强','第1段'])])])
    store.save_outline(PLAN,[dict(id='st',title='压强')],[dict(id=f'm{i}',stage_id='st',title=f'模块{i}') for i in range(2)])
    for i in range(2):
        points=[dict(id=f'p{i}',title='面积与压强',summary='压力相同看面积',minutes=15,chunk_ids=['c0'])]
        if i==0 and first_points>1:points.append(dict(id='p2',title='压强公式',summary='p=F/S',minutes=15,chunk_ids=['c0']))
        store.save_generated_module(PLAN,f'm{i}',points,check_minutes=5,today=DAY)
    store.start_plan(PLAN,today=DAY)
    gateway=Gateway();materials=Materials()
    services=SimpleNamespace(store=store,gateway=gateway,account_id=ACCOUNT,data_root=root,settings=SimpleNamespace(load=lambda:dict(SETTINGS)))
    room=Classroom(services,lambda:DAY,now=lambda:datetime(2026,10,3,10,tzinfo=timezone.utc))
    records=store.records
    store.records=lambda entity,plan_id='':copy.deepcopy(materials.bank) if entity=='question_bank' else records(entity,plan_id)
    return SimpleNamespace(store=store,gateway=gateway,materials=materials,services=services,room=room,stream=[])

@pytest.fixture
def env(tmp_path,monkeypatch):
    e=make_environment(tmp_path)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    yield e
    # Material preparation is owned by PlanFlow's shared per-account worker,
    # not by the old classroom-materials thread. Close the fixture before
    # cancelling so a cancelled job cannot enter the product's resume/retry path.
    e.services._closed=True
    flow=getattr(e.services,'_material_flow',None)
    e.room.cancel_materials(PLAN)
    if flow is not None:
        jobs=flow._material_jobs
        with jobs['guard']:
            timer=jobs['timers'].pop(PLAN,None)
            if timer is not None:timer.cancel()
            for (pid,_),job in list(jobs['jobs'].items()):
                if pid==PLAN:job['cancel'].set()
            jobs['queued'].pop(PLAN,None)
        deadline=time.monotonic()+3
        while PLAN in jobs['active'] and time.monotonic()<deadline:time.sleep(.005)
        assert PLAN not in jobs['active'], 'PlanFlow worker did not stop before Store close'
    e.store.close()

def act(e,name,*args,token=None):
    ctx=SimpleNamespace(cancel=threading.Event(),stream=e.stream.append)
    return e.room.perform(PLAN,name,ctx,args,token or uuid.uuid4().hex)

def pending(e,kind='choice',purpose='module_test'):
    q=question('p0',kind)
    e.materials.bank[0]=q
    def show(co,st,ctx):
        st['step']='testing'
        st['test']=dict(attempt='fixture-attempt',kind=purpose,queue=[q],index=0,question_ids=[],asked=[])
        e.room._show_question(co,st,q,purpose)
    return e.room.run(PLAN,show)

def test_open_branch_original_cards_and_conversation(env):
    e=env;v=act(e,'next');assert v['step']=='opening' and v['conversation']['options']==['针尖','手指','不知道','直接开始讲']
    assert v['messages'][-1]['sources'][0]['text']==PARAGRAPH
    v=act(e,'choose',0);assert v['step']=='teaching' and not v['question']
    assert not e.store.records('judgements',PLAN)
    act(e,'confused');v=act(e,'ask','硬度相同吗？')
    assert '讲义里没有，下面是补充' in v['messages'][-1]['content']
    assert v['step']=='teaching' and v['conversation']['interrupted']
    assert not act(e,'back')['conversation']['interrupted']
    assert e.gateway.calls==['teacher']*4

@pytest.mark.parametrize('value',[1,2])
def test_open_wrong_or_unknown_still_explains(env,value):
    act(env,'next');v=act(env,'choose',value)
    assert v['step']=='teaching' and v['messages'][-1]['sources']



@pytest.mark.parametrize('kind,value', [('choice',[0]),('multi',[0,2]),('fill',['Ｆ ／ Ｓ','ｐＡ'])])
def test_program_grade_never_calls_model(env,kind,value):
    pending(env,kind);v=act(env,'answer',value)
    assert not v['failure'] if 'failure' in v and v['failure'] else True
    assert v['step']=='test_done' and not env.gateway.calls
    assert env.store.records('judgements',PLAN)[0]['event']['results']==[['p0',True]]


def test_pending_question_is_not_active_judging(env):
    before=pending(env)['question'];v=act(env,'ask','为什么要比较面积？')
    assert v['question']==before and env.gateway.calls==['teacher']
    assert not env.store.records('judgements',PLAN)


def test_model_failure_retry_and_idempotent_buttons(env):
    act(env,'next');env.gateway.fail=True
    v=act(env,'choose',0,token='one');assert v['failure'] and v['step']=='opening'
    assert not any(m['role']=='user' for m in v['messages'])
    env.gateway.fail=False;v=act(env,'retry',token='retry');assert not v['failure']
    assert len([m for m in v['messages'] if m['role']=='user'])==1
    v=act(env,'choose',0,token='one');assert not v['failure'] and len([m for m in v['messages'] if m['role']=='user'])==1

def test_invalid_citation_removed_without_retry_or_literal_fallback(env):
    env.gateway.replies=['==中文讲解。==[c99]\n【选项】\n先看看\n还不知道\n【/选项】']
    v=act(env,'next');assert v['step']=='opening'
    message=v['messages'][-1]
    assert '中文讲解。' in message['content'] and 'c99' not in message['content']
    assert PARAGRAPH not in message['content'] and message['sources']
    assert env.gateway.calls==['teacher'] and 'c99' not in ''.join(env.stream)

def test_valid_reply_displays_after_format_check(env,monkeypatch):
    def generate(*args,**kw):
        text='==压力与面积有关。==[c1]\n\n面积变小会使压强增大。[c1]'
        kw['on_text'](text[:19]);kw['on_text'](text[19:])
        assert env.stream and '[c' not in ''.join(env.stream),'Prose streams before return; source aliases stay hidden'
        return SimpleNamespace(text=text)
    act(env,'next');env.stream.clear();monkeypatch.setattr(env.gateway,'complete',generate)
    v=act(env,'choose',0)
    assert not v['failure'] and any('面积变小会使压强增大' in text for text in env.stream)

@pytest.mark.parametrize('text',['无引用知识。','结论[c99]。','结论[c1,c2]。'])
def test_teacher_keeps_explanation_with_optional_citations(env,text):
    co=env.room._course(PLAN);st=env.room._load(co);plan,module=env.room._skeleton(co,st,['p0'])
    result=teacher_agent.parse_teaching(text,dict(plan=plan,module=module,prefs=SETTINGS,excerpts=co.excerpts(['p0']),director='解释'))
    assert 'c99' not in result.speech and result.speech

def test_save_error_is_not_success_and_durable_grade_replays_once(env,monkeypatch):
    pending(env,'choice');original=env.room._save
    monkeypatch.setattr(env.room,'_save',lambda *a:(_ for _ in ()).throw(BridgeError('CLASS_SAVE_FAILED','保存失败')))
    with pytest.raises(BridgeError):act(env,'answer',[0],token='graded')
    monkeypatch.setattr(env.room,'_save',original)
    v=act(env,'answer',[0],token='graded');assert v['step']=='test_done'
    assert len(env.store.records('judgements',PLAN))==len(env.store.records('question_answers',PLAN))==1
    assert len([m for m in v['messages'] if m['role']=='user'])==1

def test_test_final_retest_and_practice_are_selected_not_generated(env):
    e=env;v=act(e,'start_test','skip_test');assert v['question']['purpose']=='direct'
    act(e,'answer',[0]);act(e,'answer',[0]);act(e,'next_module');act(e,'next');act(e,'choose',0);act(e,'reply','我会了')
    v=act(e,'start_test','module_test');assert v['question']['purpose']=='module'
    act(e,'answer',[1]);act(e,'answer',[0]);v=act(e,'start_test','retest');assert v['question']['purpose']=='retest'
    act(e,'answer',[0]);act(e,'next_module');v=act(e,'start_check')
    assert v['question']['purpose']=='final'
    for _ in range(20):
        if v['step']=='completed':break
        v=act(e,'answer',[0]) if v['question'] else act(e,'start_check')
        assert not v['failure'],v['failure']
    assert v['step']=='completed'
    before=e.store.load_mastery(PLAN).report_stats();v=act(e,'start_practice',None,None,2)
    assert v['question']['purpose']=='free'
    act(e,'answer',[1]);act(e,'answer',[0]);assert e.store.load_mastery(PLAN).report_stats()==before
    assert {'direct','module','retest','final','free'}<=set(p for _,p in e.materials.used)
    assert set(e.gateway.calls)<={'teacher'}

def test_active_teacher_yields_to_new_question_without_late_commit(env,monkeypatch):
    pending(env);started=threading.Event();finish=threading.Event();original=env.gateway.complete
    outcomes=[]
    def gateway(*args,**kwargs):
        if not started.is_set():
            started.set();assert finish.wait(10)
        return original(*args,**kwargs)
    monkeypatch.setattr(env.gateway,'complete',gateway)
    def first():
        try:act(env,'ask','先问一下')
        except BridgeError as exc:outcomes.append(exc.code)
    t=threading.Thread(target=first)
    t.start();assert started.wait(3)
    try:
        view=act(env,'ask','再问一下')
        assert not view['failure']
        latest=[m['id'] for m in view['messages']]
    finally:finish.set();t.join(10)
    assert not t.is_alive() and outcomes==['CANCELLED']
    current=env.room.run(PLAN,lambda *_:None,readonly=True)
    assert [m['id'] for m in current['messages']]==latest

def test_report_uses_actual_explained_points_not_old_drafts(env):
    act(env,'next');assert not env.store.records('teacher_profile',PLAN)
    act(env,'choose',0)
    act(env,'reply','我会了');act(env,'start_test','module_test');act(env,'answer',[0])
    act(env,'next_module');act(env,'start_test','skip_test');act(env,'answer',[0]);act(env,'answer',[0]);act(env,'next_module')
    v=act(env,'start_check')
    for _ in range(20):
        if v['step']=='completed':break
        v=act(env,'answer',[0]) if v['question'] else act(env,'start_check')
    assert v['step']=='completed'
    report=build_report(env.store,PLAN,today=DAY)
    assert report['live_points']==[dict(point_id='p0',title='面积与压强')]


def test_missing_point_sources_not_guessed(env):
    co=env.room._course(PLAN);co.points[0]['chunk_ids']=[]
    assert not co.passages(['p0'])
    with pytest.raises(BridgeError,match='没有关联'):co.excerpts(['p0'])

def test_materials_progress_does_not_block_learning_and_leave_keeps_background_work(env,monkeypatch):
    import time
    bank=env.materials.bank;env.materials.bank=[];started=threading.Event();release=threading.Event()
    def prepare(store,plan_id,module_id,ctx,progress):
        started.set();progress(0,1,'正在查找带答案原题')
        while not release.wait(.005):
            if ctx['cancel'].is_set():return
        env.materials.bank=bank;progress(1,1,'备好原文与题目')
    monkeypatch.setattr(env.materials,'prepare_module',prepare)
    act(env,'enter');assert started.wait(1)
    begin=time.monotonic();v=act(env,'next');assert time.monotonic()-begin<1 and v['step']=='opening'
    assert v['preparation']['total']==1
    release.set()
    deadline=time.monotonic()+2
    while v['preparation'] and time.monotonic()<deadline:
        time.sleep(.005);v=env.room.run(PLAN,lambda *args:None,SimpleNamespace(cancel=threading.Event(),stream=env.stream.append))
    assert v['step']=='opening',v
    env.materials.bank=[];release.clear();act(env,'enter')
    jobs=list(env.room._jobs.values());act(env,'leave')
    assert env.room._jobs and all(not j['cancel'].is_set() for j in jobs)
    assert not getattr(env.gateway,'closed',False)
    release.set()
    from tests.refactor.plan66.test_plan66_just_in_time_materials import wait_for
    wait_for(lambda:PLAN not in env.services._material_flow._preparing)

def test_preparation_failure_is_visible_and_retryable(env,monkeypatch):
    import time
    flow=env.room._preparation_flow()
    # Keep the worker's three-attempt behavior, but remove wall-clock backoff;
    # retry cadence itself is covered by plan187.
    monkeypatch.setattr(flow,'_material_retry_delay',lambda attempt:0)
    env.materials.bank=[]
    monkeypatch.setattr(env.materials,'prepare_module',lambda *a:(_ for _ in ()).throw(RuntimeError('failure')))
    v=act(env,'next')
    deadline=time.monotonic()+2
    while env.room._jobs[(PLAN,'m0')]['status']!='failed' and time.monotonic()<deadline:
        time.sleep(.005)
    assert env.room._jobs[(PLAN,'m0')]['status']=='failed'
    v=env.room.run(PLAN,lambda *a:None)
    assert v['step']=='opening' and v['preparation']['error']
    old=env.room._jobs[(PLAN,'m0')];act(env,'retry')
    assert old['cancel'].is_set() and old is not env.room._jobs[(PLAN,'m0')]





def test_cancelled_model_does_not_write_student(env,monkeypatch):
    from refactor.learning.connections.errors import ModelCancelled
    act(env,'next')
    monkeypatch.setattr(env.gateway,'complete',lambda *a,**kw:(_ for _ in ()).throw(ModelCancelled()))
    with pytest.raises(BridgeError,match='已停止'):act(env,'choose',0)
    assert not any(m['role']=='user' for m in env.store.messages(PLAN,'m0'))

def test_actual_save_storage_error_maps_readably(env,monkeypatch):
    pending(env)
    monkeypatch.setattr(env.store,'save_classroom_position',lambda *a,**kw:(_ for _ in ()).throw(StorageError('WRITE_FAILED')))
    with pytest.raises(BridgeError,match='保存失败'):act(env,'enter')

def test_new_grounded_host_retains_exact_accepted_request_for_cache(env,monkeypatch):
    captures=[];complete=env.gateway.complete
    def recorded(purpose,messages,**kwargs):
        result=complete(purpose,messages,**kwargs);captures.append((copy.deepcopy(messages),result.text));return result
    monkeypatch.setattr(env.gateway,'complete',recorded)
    act(env,'next');first=captures[-1]
    act(env,'choose',0);second=captures[-1]
    assert second[0][:len(first[0])]==first[0]
    assert second[0][len(first[0])]==dict(role='assistant',content=first[1])
    assert len([r for r in second[0] if r['content'].startswith('【当前模块原文')])==1
