"""Question-based assessment with real room/store; MF05 admission is outside this task."""
import copy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import threading
import uuid

import pytest

from refactor.learning.app.classroom import Classroom, register
from refactor.learning.app.bridge import BridgeError
from refactor.learning.course.mastery import MasteryBook, PointRef, Judgement, AttemptClosed
from refactor.learning.course import question_bank
from refactor.learning.course.store import CourseStore
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.report import build_report

DAY=date(2026,10,6);NOW=datetime(2026,10,6,12,tzinfo=timezone.utc);PLAN='7'*32

def rows(counts=(5,0,2)):
    source = dict(title='合成资料',url='',locator='原文块 original',chunk_ids=['original'],block_ids=[0])  # Agreed source contract: literal original block ID.
    return [dict(id=f'q{m}-{i}',module_id=f'm{m}',point_ids=[f'p{m}'],kind='objective',stem=f'{m}-{i}：1+1是多少？',
        options=['2','3'],answer='2',objective_kind='choice',reference='',key_points=[],source=source,answer_source=source,origin='found',role='bank',importance='key',
        explanation='',uses=[],created_at=NOW.isoformat()) for m,n in enumerate(counts) for i in range(n)]

def book(counts=(5,0,2)):
    return MasteryBook([PointRef(f'p{m}',f'm{m}') for m in range(len(counts))],rows(counts),['p1'])

def judge(b,qid,good,context='module_test',attempt='a'):
    q=b._questions[qid];seq=len(b._events)+1
    b.apply(Judgement(f'j{seq}',seq,NOW,context,q['module_id'],attempt if context!='check' else None,
        ((q['point_ids'][0],good),),question_id=qid))

def close(b,mid,attempt='a',kind='module_test'):
    seq=len(b._events)+1;b.apply(AttemptClosed(f'c{seq}',seq,mid,attempt,kind))

def detect(b,wrong=()):
    for mid in ('m0','m2'):
        for q in b._questions.values():
            if q['module_id']==mid:judge(b,q['id'],q['id'] not in wrong)
        close(b,mid)

def test_question_counts_threshold_and_wrong_only_retest():
    b=book();detect(b,('q0-0',))
    assert b.module('m0').state=='failed'  # 4/5, not one point passed.
    assert b.module('m1').state=='passed'
    assert not b.check_ready()
    judge(b,'q0-0',True,'retest','r');close(b,'m0','r','retest')
    assert b.check_ready() and b.question('q0-0').ever_wrong

def test_exact_ninety_percent_can_pass_with_one_wrong():
    b=MasteryBook([PointRef('p0','m0')],rows((10,)))
    for qid in b._questions:judge(b,qid,qid!='q0-0')
    close(b,'m0');assert b.module('m0').state=='passed'


def test_passed_separator_question_wrong_requires_two_again():
    b=book();detect(b)
    judge(b,'q0-1',True,'check');assert b.question('q0-1').passed
    judge(b,'q0-1',False,'check');assert not b.question('q0-1').passed
    judge(b,'q0-1',True,'check');assert not b.question('q0-1').passed
    judge(b,'q0-1',True,'check');assert b.question('q0-1').passed

def test_first_correct_once_wrong_twice_and_reset():
    b=book();detect(b,('q0-0',));judge(b,'q0-0',True,'retest','r');close(b,'m0','r','retest')
    judge(b,'q0-1',True,'check');assert b.question('q0-1').passed
    judge(b,'q0-0',True,'check');assert not b.question('q0-0').passed
    judge(b,'q0-0',False,'check');assert b.question('q0-0').streak==0
    judge(b,'q0-0',True,'check');judge(b,'q0-0',True,'check');assert b.question('q0-0').passed
    judge(b,'q0-2',False,'check');judge(b,'q0-2',True,'check');assert not b.question('q0-2').passed
    judge(b,'q0-2',True,'check');assert b.question('q0-2').passed

@pytest.mark.parametrize('size',[1,2,3,4,7])
def test_repeat_spacing_and_termination(size):
    b=MasteryBook([PointRef('p0','m0')],rows((size,)))
    for qid in b._questions:judge(b,qid,qid!='q0-0')
    close(b,'m0');judge(b,'q0-0',True,'retest','r');close(b,'m0','r','retest')
    history=[]
    for _ in range(40):
        if b.check_complete():break
        qid=b.next_check_questions()[0]
        if qid in history:
            assert len(history)-1-history.index(qid)>=min(3,size-1)
        history.append(qid);judge(b,qid,True,'check')
    assert b.check_complete()
    assert MasteryBook.from_dict(b.to_dict()).to_dict()==b.to_dict()

@pytest.fixture
def env(tmp_path,monkeypatch):
    store=CourseStore('question70',tmp_path)
    store.create_plan(PLAN,goal='合成知识',final_goal='理解',level='beginner',window=PlanWindow(DAY,DAY+timedelta(days=5),100))
    store.add_sources(PLAN,[dict(id='s',kind='file',sha256='a'*64,filename='fake.txt',title='合成资料',adopted=True,
        chunks=[dict(chunk_id='original',text='第一条原文。第二条原文。第三条原文。')])])
    store.save_outline(PLAN,[dict(id='stage',title='合成')],[dict(id=f'm{m}',stage_id='stage',title=f'模块{m}') for m in range(3)])
    for m in range(3):store.save_generated_module(PLAN,f'm{m}',[dict(id=f'p{m}',title=f'点{m}',summary='原文知识',minutes=8,chunk_ids=['original'])],check_minutes=0,final_check_minutes=0,check_question_minutes=2,today=DAY)
    qs=rows();qs[0].update(kind='recall',reference='第一条原文。第二条原文。第三条原文。',key_points=['第一条原文。','第二条原文。','第三条原文。'],answer='',options=[],objective_kind=None)
    for m in (0,2):store.add_bank_questions(PLAN,f'm{m}',[q for q in qs if q['module_id']==f'm{m}'])
    for m in range(3):store.save_module_preparation(PLAN,f'm{m}',dict(state='done'))
    store.start_plan(PLAN,today=DAY)
    settings=dict(refresh_time='04:00',teacher_style='warm',detail='brief',examples='few',analogy_domains=['life'])
    services=SimpleNamespace(store=store,account_id='question70',data_root=tmp_path,settings=SimpleNamespace(load=lambda:settings),gateway=None)
    room=Classroom(services,lambda:DAY,now=lambda:NOW)
    room._prepare_next_module=lambda *a,**k:None
    e=SimpleNamespace(store=store,services=services,room=room)
    yield e
    store.close()

def act(e,name,*args,token=None):
    v=e.room.perform(PLAN,name,SimpleNamespace(cancel=threading.Event(),stream=lambda _:None),args,token or uuid.uuid4().hex)
    assert not v.get('failure'),v.get('failure')
    return v

def position(e,mid,step='test_offer'):
    return e.room.run(PLAN,lambda co,st,ctx:st.update(module_id=mid,index=0 if step=='closing' else 1,step=step,pending=None,test=None))

def answer(e,v,good=True):
    if v['question']['kind']=='recall':
        qid=v['question']['id'];act(e,'recall_reveal',qid,'学生自己的原文回答')
        return act(e,'recall_submit',qid,[0,1,2] if good else [0,1])
    return act(e,'answer',[0] if good else [1])


def test_recall_bridge_contract_and_interrupted_submit_are_idempotent(env,monkeypatch):
    e=env;position(e,'m0');v=act(e,'start_test','module_test');qid=v['question']['id']
    class Registry:
        def __init__(self):self.methods={}
        def register(self,name,fn):self.methods[name]=fn
        def push(self,*args):pass
    registry=Registry();register(registry,lambda:e.services,lambda:DAY,now=lambda:NOW)
    ctx=SimpleNamespace(cancel=threading.Event(),stream=lambda _:None,request_id='reveal70')
    failed = registry.methods['class.recall_reveal'](dict(plan_id=PLAN,question_id=qid,answer=''),ctx)
    assert failed['failure'] and 'reference' not in failed
    revealed=registry.methods['class.recall_reveal'](dict(plan_id=PLAN,question_id=qid,answer='我的答案原文'),ctx)
    assert revealed['reference'] and len(revealed['key_points'])==3
    assert not e.store.records('judgements',PLAN)
    original=e.room._save
    monkeypatch.setattr(e.room,'_save',lambda *a:(_ for _ in ()).throw(RuntimeError('interrupted save')))
    with pytest.raises(RuntimeError):act(e,'recall_submit',qid,[0,1],token='submit70')
    monkeypatch.setattr(e.room,'_save',original)
    # A changed retry cannot replace a judgement that already committed.
    v=act(e,'recall_submit',qid,[0,1,2],token='submit70')
    assert v['recall_result']==dict(question_id=qid,correct=False,missed=[2])
    rows=e.store.records('question_answers',PLAN)
    assert len(rows)==1 and rows[0]['answer']=='我的答案原文' and rows[0]['ticked']==[0,1]
    failed = registry.methods['class.recall_submit'](dict(plan_id=PLAN,question_id='wrong-id',ticked=[]),ctx)
    assert failed['failure'] and 'correct' not in failed


def test_question_estimate_zero_module_and_every_answer_time(env):
    e=env
    for m,count in enumerate((5,0,2)):e.store.set_question_estimate(PLAN,f'm{m}',count,today=DAY)
    modules=e.store.records('modules',PLAN)
    assert [(m['check_minutes'],m['final_check_minutes']) for m in modules]==[(10,13),(0,0),(4,5)]
    assert e.store.get_plan(PLAN)['total_minutes']==24+14+18
    position(e,'m0');v=act(e,'start_test','module_test')
    while v['question']:v=answer(e,v)
    position(e,'m1','closing');act(e,'next')
    position(e,'m2');v=act(e,'start_test','module_test')
    while v['question']:v=answer(e,v)
    act(e,'next_module');v=act(e,'start_check');qid=v['question']['id']
    answer(e,v,False)
    credits=[r for r in e.store.records('credits',PLAN) if r['id'].startswith('check:')]
    assert len(credits)==1 and credits[0]['credit']['minutes']==2 and credits[0]['credit']['unit_id']=='check:'+qid

def test_real_room_recall_reveal_missing_ticks_restart_and_retest(env):
    e=env;position(e,'m0');v=act(e,'start_test','module_test');qid=v['question']['id']
    assert v['question']['kind']=='recall' and 'reference' not in v['question'] and 'key_points' not in v['question']
    bad=e.room.perform(PLAN,'recall_submit',None,(qid,[0,1,2]))
    assert bad['failure'] and not e.store.records('judgements',PLAN)
    v=act(e,'recall_reveal',qid,'第一条，第二条');assert v['question']['reference'] and v['question']['answer']=='第一条，第二条'
    e.room=Classroom(e.services,lambda:DAY,now=lambda:NOW);e.room._prepare_next_module=lambda *a,**k:None
    v=act(e,'recall_submit',qid,[0,1]);assert v['recall_result']==dict(question_id=qid,correct=False,missed=[2])
    while v['question']:v=answer(e,v)
    assert v['module']['state']=='failed'
    v=act(e,'start_test','retest');assert v['question']['id']==qid and v['question']['total']==1
    v=answer(e,v);assert v['module']['state']=='passed'
    saved=next(r['details'] for r in e.store.records('judgements',PLAN) if r['details']['question_id']==qid and not all(v for _,v in r['event']['results']))
    assert saved['answer']=='第一条，第二条' and saved['ticked']==[0,1]

def test_whole_course_final_and_question_report_free_no_mutation(env):
    e=env
    position(e,'m0');v=act(e,'start_test','module_test')
    while v['question']:v=answer(e,v)
    # Learning-only module consumes learning time and skips testing automatically.
    position(e,'m1','closing');v=act(e,'next');assert v['module']['id']=='m2' and v['step']=='teach'
    position(e,'m2');v=act(e,'start_test','module_test')
    while v['question']:v=answer(e,v)
    v=act(e,'next_module');assert v['step']=='check_offer'
    v=act(e,'start_check');n=0
    while v['step']!='completed':
        n+=1;assert n<30
        v=answer(e,v) if v['question'] else act(e,'start_check')
    report=build_report(e.store,PLAN,today=DAY)
    assert len(report['questions'])==7 and all(q['passed'] for q in report['questions'])
    from refactor.learning.app.plan_flow import report_markdown
    markdown = report_markdown(report, {})
    assert '模块1：只学不考' in markdown and '5/5 道题通过最终检验' in markdown
    before=copy.deepcopy(e.store.load_mastery(PLAN).to_dict())
    v=act(e,'start_practice','m0',None,2)
    v=answer(e,v,False);v=answer(e,v)
    assert e.store.load_mastery(PLAN).to_dict()==before


def test_real_room_final_wrong_streak_spacing_and_restart(env):
    e = env
    position(e, 'm0'); v = act(e, 'start_test', 'module_test')
    v = answer(e, v, False)  # Recall is wrong in the first module detection.
    while v['question']: v = answer(e, v)
    v = act(e, 'start_test', 'retest')
    assert v['question']['total'] == 1
    answer(e, v)
    position(e, 'm1', 'closing'); act(e, 'next')
    position(e, 'm2'); v = act(e, 'start_test', 'module_test')
    while v['question']: v = answer(e, v)
    act(e, 'next_module'); v = act(e, 'start_check')
    asked, last, intentionally_wrong, restarted = [], {}, False, False
    while v['step'] != 'completed':
        assert len(asked) < 40
        if not v['question']:
            v = act(e, 'start_check'); continue
        qid = v['question']['id']
        if qid in last: assert len(asked) - last[qid] - 1 >= 3
        last[qid] = len(asked); asked.append(qid)
        good = qid != 'q0-2' or intentionally_wrong
        intentionally_wrong |= not good
        v = answer(e, v, good)
        if qid == 'q0-0' and not restarted:
            assert not e.store.load_mastery(PLAN).question(qid).passed
            current = copy.deepcopy(v.get('question'))
            e.room = Classroom(e.services, lambda: DAY, now=lambda: NOW)
            e.room._prepare_next_module = lambda *a, **k: None
            v = e.room.run(PLAN, lambda *args: None, None)
            assert v['question'] == current
            restarted = True
    assert intentionally_wrong and restarted
    assert asked.count('q0-0') == 2 and asked.count('q0-2') == 3
    report = build_report(e.store, PLAN, today=DAY)
    assert all(q['passed'] for q in report['questions'])
    assert len([r for r in e.store.records('judgements', PLAN) if r['event']['context'] == 'check']) == len(asked)

def test_empty_plan_only_learning_no_test_no_final(env):
    e=env
    with e.store.transaction():
        for q in e.store.records('question_bank',PLAN):e.store._delete('question_bank',PLAN,q['id'])
    for m in range(3):
        position(e,f'm{m}','closing');v=act(e,'next')
    assert v['step']=='completed' and not e.store.records('judgements',PLAN)
    assert not e.store.records('attempt_closed',PLAN)
    report=build_report(e.store,PLAN,today=DAY)
    assert report['assessment']=='learning_only' and report['questions']==[]
    from refactor.learning.app.plan_flow import report_markdown
    markdown = report_markdown(report, {})
    assert '只学习，不考核' in markdown and '## 错误分布' not in markdown
    assert '## 每日目标与实际 / 历史明细' in markdown


def test_bank_picker_accepts_recall_without_angles_or_new_question_search():
    q=rows()[0];q.update(kind='recall',reference='第一条原文。第二条原文。第三条原文。',
                        key_points=['第一条原文。','第二条原文。','第三条原文。'])
    assert question_bank.select_question([q],'p0','final')['id']==q['id']
    assert question_bank.select_question([dict(q,origin='cloze')],'p0','final') is None


@pytest.mark.parametrize('options,answer', [(['-2', '2'], '-2'), (['1.5', '15'], '1.5')])
def test_literal_objective_choice_keeps_signs_and_decimals(options, answer):
    from refactor.learning.course import answer_compare
    q = question_bank.grading_question(dict(kind='objective', options=options, answer=answer))
    assert q['kind'] == 'choice' and q['answer'] == [0]
    assert answer_compare.grade(q, [0])[0]
    assert not answer_compare.grade(q, [1])[0]


def test_original_chunk_locator_recall_no_reference_before_own_answer(env):
    e=env
    position(e,'m0');v=act(e,'start_test','module_test')
    assert v['question']['source']['chunk_ids']==['original']
    assert 'text' not in v['question']['source'] and 'reference' not in v['question']
    qid=v['question']['id']
    wrong= e.room.perform(PLAN,'recall_reveal',None,('another-id','我的回答'))
    assert wrong['failure'] and not wrong['question']['revealed']
    v=act(e,'recall_reveal',qid,'自己的原话')
    assert v['question']['reference']=='第一条原文。第二条原文。第三条原文。'


def test_practice_wrong_question_survives_different_right_question():
    first=dict(id='a',at=NOW.isoformat(),sequence=1,question_id='q1',point_ids=['p'],correct=False)
    second=dict(first,id='b',sequence=2,question_id='q2',correct=True)
    assert question_bank.review_questions([first,second])=={'q1'}
    assert question_bank.review_points([first,second])=={'p'}
    third=dict(first,id='c',sequence=3,correct=True)
    assert question_bank.review_questions([first,second,third])==set()


def test_idempotent_events_and_late_outcomes_do_not_reopen_closed_attempt():
    b=book();detect(b)
    assert b.check_ready()
    original=b.to_dict()
    event=next(e for e in b._events.values() if isinstance(e,Judgement))
    b.apply(event);assert b.to_dict()==original
    with pytest.raises(ValueError):b.apply(replace(event,results=((event.results[0][0],False),)))
    judge(b,'q0-0',False)  # Delivered after this attempt closed: ignored for mastery.
    assert b.question('q0-0').module_correct is True and not b.question('q0-0').ever_wrong


def test_previous_point_based_plan_is_not_migrated(env):
    e = env
    with e.store.transaction():
        plan = e.store._get('plans', PLAN, PLAN)
        plan['material_version'] = 2
        e.store._put('plans', PLAN, plan)
    with pytest.raises(BridgeError, match='旧版计划，已不可用'):
        e.room._course(PLAN)
    assert e.store.get_plan(PLAN)['material_version'] == 2


def test_version_two_plan_without_questions_is_not_old(tmp_path):
    # User test 2026-10-07: a draft saved before version 3, still preparing with no
    # questions, was refused as an old plan and module 1 preparation failed.
    store=CourseStore('question70-empty',tmp_path)
    store.create_plan('p-empty',goal='合成知识',final_goal='理解',level='beginner',window=PlanWindow(DAY,DAY+timedelta(days=5),100))
    with store.transaction():
        plan=store._get('plans','p-empty','p-empty');plan['material_version']=2;store._put('plans','p-empty',plan)
    assert not store.get_plan('p-empty').get('unusable')
    store.require_material_plan('p-empty')
