"""Batch44: real classroom/store accounting, no grading model or paid calls."""
import copy
import threading
import pytest
from refactor.learning.course import answer_compare as compare, judge_agent
from refactor.learning.connections.gateway import PURPOSE_EFFORT, PURPOSE_MIN_OUTPUT
from refactor.learning.app.classroom import Classroom
from tests.refactor.conversation37.test_grounded_conversation37 import make_environment, question, act, PLAN


@pytest.mark.parametrize('reply',['Python','ＰＹＴＨＯＮ',' （ python ） 。','Py thon!','“PYTHON”'])
def test_fill_normalization_presentation(reply):
    assert compare.grade({'kind':'fill','answer':[['python']]},[reply])[0]


@pytest.mark.parametrize('reference,reply',[('-2','2'),('1.5','15'),('1/2','12'),('python','java')])
def test_fill_does_not_erase_meaning(reference,reply):
    assert not compare.grade({'kind':'fill','answer':[[reference]]},[reply])[0]


@pytest.mark.parametrize('answer,reply,tolerance,good',[
    ('1.5 N','1.5',0,True),('1.5 N','1.50 N',0,True),('1.5 N','１．５０ 牛顿',0,True),
    ('1.5 N','1500 mN',0,True),('1.5 N','3/2N',0,True),('1.5 N','1.5 kg',0,False),
    ('1.5 N','1.51N','.01',True),('1.5 N','1.5101N','.01',False),
    ('2000 Pa','2 kPa',0,True),('2000Pa','2×10^3 Pa',0,True),('1500Pa','1,500Pa',0,True),
    ('p=F/S=20/0.01=2000Pa。','p=2000Pa',0,True),('1.5 ± 0.01 N','1.49牛顿',0,True),
    ('0 N','0.0N',0,True),('-1.5 N','−１．５N',0,True),('1.5','1.50N',0,True),
    ('1.5N','不是1.5N',0,False),('1.5N','NaN',0,False),('1.5N','inf',0,False),
    ('1.5N','1.5未知单位',0,False),('1.5N','我肯定对',0,False),
    ('1 N','1000mN',0,True),('1N','1MN',0,False),
    ('1 Pa','1000mPa',0,True),('1Pa','1MPa',0,False),('2000Pa','2E3Pa',0,True),
])
def test_calc_tolerance_units_and_forms(answer,reply,tolerance,good):
    assert compare.compare_number({'kind':'calc','answer':answer,'tolerance':tolerance},reply)==good


@pytest.mark.parametrize('kind,answer,value,good',[
    ('choice',[0],[0],True),('choice',[0],[1],False),
    ('multi',[0,2],[2,0],True),('multi',[0,2],[0],False),('multi',[0,2],[0,1,2],False),
])
def test_option_index_comparison(kind,answer,value,good):
    assert compare.grade(dict(kind=kind,options=['a','b','c'],answer=answer),value)[0]==good


@pytest.fixture
def e(tmp_path,monkeypatch):
    env=make_environment(tmp_path)
    records=env.store.records
    env.store.records=lambda entity,plan='':copy.deepcopy(env.materials.bank) if entity=='question_bank' else records(entity,plan)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:env.materials))
    complete=env.gateway.complete
    def no_judge(purpose,*args,**kwargs):
        assert purpose!='judge','grading model called'
        return complete(purpose,*args,**kwargs)
    env.gateway.complete=no_judge
    yield env
    env.room.cancel_materials()
    for thread in threading.enumerate():
        if thread.name=='classroom-materials':thread.join(3)
    env.store.close()


@pytest.mark.parametrize('kind,value,good',[
    ('choice',[0],True),('choice',[1],False),('multi',[0,2],True),('multi',[1],False),
    ('fill',['Ｆ ／ Ｓ','（pa）。'],True),('fill',['不对','pa'],False),
    ('calc','2000 Pa',True),('calc','2001Pa',False),
])
def test_all_program_paths_record_right_and_wrong_without_model(e,kind,value,good):
    q=question('p0',kind)
    e.materials.bank=[q]
    act(e,'start_test','skip_test')
    v=act(e,'answer',value)
    assert not v['failure'] and not e.gateway.calls
    rows=e.store.records('judgements',PLAN)
    assert rows[0]['event']['results']==[['p0',good]]
    assert e.store.records('question_answers',PLAN)[0]['correct']==good
    assert v['explanation']['open']==(not good)


@pytest.mark.parametrize('purpose',['module','retest','direct','final','free'])
def test_old_subjective_questions_filtered_for_every_purpose(e,purpose):
    e.materials.bank=[question('p0','short'),question('p0','essay'),question('p0','calc')]
    co=e.room._course(PLAN);st=e.room._load(co)
    q=e.room._pick(co,'p0',purpose,state=st)
    assert q['kind']=='calc' and not e.store.records('judgements',PLAN) and not e.gateway.calls


def test_all_subjective_skips_without_grade_or_empty_close(e):
    e.materials.bank=[question('p0','short'),question('p0','essay')]
    v=act(e,'start_test','skip_test')
    assert not v['failure'] and not v['question'] and v['step']=='test_done'
    assert not e.gateway.calls and not e.store.records('judgements',PLAN)
    assert not e.store.records('attempt_closed',PLAN)
    assert e.store.load_mastery(PLAN).module('m0').state=='untested'
    assert any('旧主观题不计分' in m['content'] for m in v['messages'])


def test_reopen_pending_old_subjective_changes_question_without_grading(e):
    q=question('p0','short')
    e.room.run(PLAN,lambda co,st,cx:st.update(step='testing',test=dict(kind='module_test',attempt='old',index=0,queue=[q],targets=['p0']),pending=dict(kind='module_test',point_ids=['p0'],question=q,answer_id='old-answer')))
    e.room=Classroom(e.services,lambda:__import__('datetime').date(2026,10,3))
    v=act(e,'enter')
    assert not v['failure'] and v['question']['kind']=='choice'
    assert not e.store.records('judgements',PLAN)
    act(e,'answer',[0])
    assert len(e.store.records('judgements',PLAN))==1


def test_grading_model_and_followup_prompts_removed():
    assert 'judge' not in PURPOSE_EFFORT and 'judge' not in PURPOSE_MIN_OUTPUT
    assert not hasattr(judge_agent,'JUDGE_SYSTEM') and not hasattr(judge_agent,'judge_request')
    assert not hasattr(Classroom,'_judge')


def test_leave_with_saved_subjective_does_not_prepare_or_grade(e):
    q=question('p0','short')
    e.room._file(PLAN).write(dict(e.room._rebuild(e.room._course(PLAN)),
        pending=dict(kind='module_test',point_ids=['p0'],question=q,answer_id='legacy'),step='testing'))
    before=list(e.materials.prepare)
    act(e,'leave')
    assert e.materials.prepare==before and not e.gateway.calls
    assert not e.store.records('judgements',PLAN)
