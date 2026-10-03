"""Actual classroom host/store, explicit pending MF04 API substitute; no model IO."""
import copy
import json
import threading
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
        self.bank=[question(pid,index=i) for pid in ('p0','p1') for i in range(3)]
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
            text=self.replies.pop(0) if self.replies else ('这一章看压力与面积，针尖和手指你觉得哪个压强大？[c1]\n【选项】\n针尖\n手指\n不知道\n【/选项】' if task['opening'] else
                ('资料里没讲到硬度相同，可以在计划里补充资料；它只比较面积。[c1]' if '硬度' in task['user'] else '压力相同，面积越小压强越大，我们顺着面积看。[c1]'))
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
    for j in e.room._jobs.values():j['cancel'].set()
    for t in threading.enumerate():
        if t.name=='classroom-materials':t.join(3)
    e.store.close()

def act(e,name,*args,token=None):
    ctx=SimpleNamespace(cancel=threading.Event(),stream=e.stream.append)
    return e.room.perform(PLAN,name,ctx,args,token or uuid.uuid4().hex)

def pending(e,kind='choice',purpose='casual'):
    q=question('p0',kind)
    e.materials.bank[0]=q
    return e.room.run(PLAN,lambda co,st,ctx:e.room._show_question(co,st,q,purpose))

def test_open_branch_original_cards_and_conversation(env):
    e=env;v=act(e,'next');assert v['step']=='opening' and len(v['conversation']['options'])==3
    assert v['messages'][-1]['sources'][0]['text']==PARAGRAPH
    v=act(e,'choose',0);assert v['step']=='teaching' and not v['question']
    assert not e.store.records('judgements',PLAN)
    act(e,'confused');v=act(e,'ask','硬度相同吗？')
    assert '资料里没讲到' in v['messages'][-1]['content']
    assert v['step']=='teaching' and v['conversation']['interrupted']
    assert not act(e,'back')['conversation']['interrupted']
    assert e.gateway.calls==['teacher']*4

@pytest.mark.parametrize('value',[1,2])
def test_open_wrong_or_unknown_still_explains(env,value):
    act(env,'next');v=act(env,'choose',value)
    assert v['step']=='teaching' and v['messages'][-1]['sources']

def test_known_quick_confirm_uses_bank_and_advances(env):
    act(env,'next');act(env,'choose',0);v=act(env,'reply','我会了')
    assert v['question']['purpose']=='quick'
    v=act(env,'answer',[0]);assert v['step']=='test_offer'
    assert len(env.store.records('judgements',PLAN))==1
    assert env.materials.used[-1][1]=='quick'

def test_casual_wrong_second_question_and_original_reason(env):
    pending(env);v=act(env,'answer',[1]);assert v['question'] is None and v['step']=='closing'
    assert v['explanation']['open'] and '压强越大' in v['explanation']['text']
    assert len(env.store.records('judgements',PLAN))==1
    v=act(env,'why');assert v['explanation']['open']
    v=act(env,'next');assert not v['failure'] and v['step']=='test_offer'

@pytest.mark.parametrize('kind,value', [('choice',[0]),('multi',[0,2]),('fill',['Ｆ ／ Ｓ','ｐＡ'])])
def test_program_grade_never_calls_model(env,kind,value):
    pending(env,kind);v=act(env,'answer',value)
    assert not v['failure'] if 'failure' in v and v['failure'] else True
    assert v['step']=='closing' and not env.gateway.calls
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

def test_invalid_citation_rewrite_then_literal_fallback(env):
    env.gateway.replies=['不属于本点的知识。[c99]','仍然不合格。[c99]']
    v=act(env,'next');assert v['step']=='opening'
    assert PARAGRAPH in v['messages'][-1]['content'] and 'c99' not in v['messages'][-1]['content']
    assert env.gateway.calls==['teacher','teacher'] and env.stream.count(RESET)==2

def test_valid_paragraph_streams_before_model_return(env,monkeypatch):
    def generate(*args,**kw):
        text='压力与面积有关。[c1]\n\n面积变小会使压强增大。[c1]'
        kw['on_text'](text[:19]);kw['on_text'](text[19:])
        assert env.stream,'A cited paragraph should be shown while generating'
        return SimpleNamespace(text=text)
    act(env,'next');monkeypatch.setattr(env.gateway,'complete',generate)
    act(env,'choose',0)

@pytest.mark.parametrize('text',['无引用知识。','结论[c99]。','结论[c1,c2]。'])
def test_teacher_rejects_invalid_or_missing_citation(env,text):
    co=env.room._course(PLAN);st=env.room._load(co);plan,module=env.room._skeleton(co,st,['p0'])
    with pytest.raises(ValueError if False else teacher_agent.AgentFormatError):
        teacher_agent.parse_teaching(text,dict(plan=plan,module=module,prefs=SETTINGS,excerpts=co.excerpts(['p0']),director='解释'))

def test_save_error_is_not_success_and_durable_grade_replays_once(env,monkeypatch):
    pending(env,'choice','quick');original=env.room._save
    monkeypatch.setattr(env.room,'_save',lambda *a:(_ for _ in ()).throw(BridgeError('CLASS_SAVE_FAILED','保存失败')))
    with pytest.raises(BridgeError):act(env,'answer',[0],token='graded')
    monkeypatch.setattr(env.room,'_save',original)
    v=act(env,'answer',[0],token='graded');assert v['step']=='test_offer'
    assert len(env.store.records('judgements',PLAN))==len(env.store.records('question_answers',PLAN))==1
    assert len([m for m in v['messages'] if m['role']=='user'])==1

def test_test_final_retest_and_practice_are_selected_not_generated(env):
    e=env;v=act(e,'start_test','skip_test');assert v['question']['purpose']=='direct'
    act(e,'answer',[0]);act(e,'next_module');act(e,'next');act(e,'choose',0);act(e,'reply','我会了');act(e,'answer',[0])
    v=act(e,'start_test','module_test');assert v['question']['purpose']=='module'
    act(e,'answer',[1]);v=act(e,'start_test','retest');assert v['question']['purpose']=='retest'
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
    assert {'quick','direct','module','retest','final','free'}<=set(p for _,p in e.materials.used)
    assert set(e.gateway.calls)<={'teacher'}

def test_active_teacher_only_blocks_concurrent_turn(env,monkeypatch):
    pending(env);started=threading.Event();finish=threading.Event();original=env.gateway.complete
    def gateway(*args,**kwargs):started.set();assert finish.wait(3);return original(*args,**kwargs)
    monkeypatch.setattr(env.gateway,'complete',gateway)
    t=threading.Thread(target=lambda:act(env,'ask','问一下'))
    t.start();assert started.wait(3)
    try:
        with pytest.raises(BridgeError,match='正在处理'):act(env,'ask','问一下')
    finally:finish.set();t.join(3)

def test_report_uses_actual_explained_points_not_old_drafts(env):
    act(env,'next');assert not env.store.records('teacher_profile',PLAN)
    act(env,'choose',0)
    act(env,'reply','我会了');act(env,'answer',[0]);act(env,'start_test','module_test');act(env,'answer',[0])
    act(env,'next_module');act(env,'start_test','skip_test');act(env,'answer',[0]);act(env,'next_module')
    v=act(env,'start_check')
    for _ in range(20):
        if v['step']=='completed':break
        v=act(env,'answer',[0]) if v['question'] else act(env,'start_check')
    assert v['step']=='completed'
    report=build_report(env.store,PLAN,today=DAY)
    assert report['live_points']==[dict(point_id='p0',title='面积与压强')]

def test_missing_question_is_readable_retryable_no_generation(env):
    pending(env);env.materials.bank=[]
    v=act(env,'answer',[1]);assert not v['failure'] and v['step']=='closing'
    assert not env.gateway.calls

def test_missing_point_sources_not_guessed(env):
    co=env.room._course(PLAN);co.points[0]['chunk_ids']=[]
    assert not co.passages(['p0'])
    with pytest.raises(BridgeError,match='没有关联'):co.excerpts(['p0'])

def test_materials_wait_progress_auto_resume_and_leave_cancel(env,monkeypatch):
    import time
    bank=env.materials.bank;env.materials.bank=[];started=threading.Event();release=threading.Event()
    def prepare(store,plan_id,module_id,ctx,progress):
        started.set();progress(0,1,'正在查找带答案原题')
        while not release.wait(.005):
            if ctx['cancel'].is_set():return
        env.materials.bank=bank;progress(1,1,'备好原文与题目')
    monkeypatch.setattr(env.materials,'prepare_module',prepare)
    act(env,'enter');assert started.wait(1)
    begin=time.monotonic();v=act(env,'next');assert time.monotonic()-begin<1 and v['step']=='preparing_materials'
    assert v['preparation']['total']==1
    release.set()
    deadline=time.monotonic()+2
    while v['step']=='preparing_materials' and time.monotonic()<deadline:
        time.sleep(.005);v=env.room.run(PLAN,lambda *args:None,SimpleNamespace(cancel=threading.Event(),stream=env.stream.append))
    assert v['step']=='opening',v
    env.materials.bank=[];release.clear();act(env,'enter')
    jobs=list(env.room._jobs.values());act(env,'leave')
    assert not env.room._jobs and all(j['cancel'].is_set() for j in jobs)
    assert not getattr(env.gateway,'closed',False)

def test_preparation_failure_is_visible_and_retryable(env,monkeypatch):
    import time
    env.materials.bank=[]
    monkeypatch.setattr(env.materials,'prepare_module',lambda *a:(_ for _ in ()).throw(RuntimeError('failure')))
    v=act(env,'next')
    time.sleep(.02);v=env.room.run(PLAN,lambda *a:None)
    assert v['step']=='preparing_materials' and v['preparation']['error']
    old=env.room._jobs[(PLAN,'m0')];act(env,'retry')
    assert old['cancel'].is_set() and old is not env.room._jobs[(PLAN,'m0')]



def test_incomplete_bank_composite_wrong_splits_without_point_attribution(tmp_path,monkeypatch):
    e=make_environment(tmp_path,first_points=2)
    for i in range(3):
        q=question('p2',index=i);q['module_id']='m0';e.materials.bank.append(q)
    composite=question('p0');composite['id']='composite';composite['point_ids']=['p0','p2']
    e.materials.bank.insert(0,composite)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    try:
        v=act(e,'start_test','skip_test');assert v['question']['id']=='composite'
        v=act(e,'answer',[1]);assert not v['failure'],v['failure']
        assert not e.store.records('judgements',PLAN) and len(e.store.records('question_answers',PLAN))==1
        act(e,'answer',[0]);v=act(e,'answer',[0])
        assert not v['failure'] and v['module']['state']=='skipped'
        assert len(e.store.records('judgements',PLAN))==2
    finally:e.store.close()

def test_ready_question_next_latency_and_no_teacher_generation(env):
    import time
    act(env,'next');act(env,'choose',0);before=list(env.gateway.calls)
    start=time.monotonic();v=act(env,'next')
    assert time.monotonic()-start<1 and v['question'] and env.gateway.calls==before

def test_cancelled_model_does_not_write_student(env,monkeypatch):
    from refactor.learning.connections.errors import ModelCancelled
    act(env,'next')
    monkeypatch.setattr(env.gateway,'complete',lambda *a,**kw:(_ for _ in ()).throw(ModelCancelled()))
    with pytest.raises(BridgeError,match='已停止'):act(env,'choose',0)
    assert not any(m['role']=='user' for m in env.store.messages(PLAN,'m0'))

def test_actual_save_storage_error_maps_readably(env,monkeypatch):
    pending(env)
    monkeypatch.setattr(env.room,'_file',lambda *a:SimpleNamespace(read=lambda default:default,write=lambda *a:(_ for _ in ()).throw(StorageError('WRITE_FAILED'))))
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
