"""A keep-one grant supersedes stale cards without resetting the kept draft."""
from refactor.learning.app.classroom import Classroom
from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course.mastery import AttemptClosed, Judgement
from refactor.learning.course.question_bank import assessment_questions
from tests.refactor.mastery70.test_question70_assessment_flow import (
    env, act, PLAN, DAY, NOW,
)


def _passed(e):
    seq=0
    for mid in ('m0','m2'):
        questions=assessment_questions(e.store.records('question_bank',PLAN),mid)
        for q in questions:
            seq+=1
            e.store.append_judgement(PLAN,Judgement(f'seed-{seq}',seq,NOW,'module_test',mid,
                mid,tuple((p,True) for p in q['point_ids']),question_id=q['id']))
        seq+=1
        e.store.append_attempt_closed(PLAN,AttemptClosed(f'close-{mid}',seq,mid,mid,'module_test'),
                                     details={'question_ids':[q['id'] for q in questions]})
    e.store.add_credit(PLAN,'learn:p1','p1',8,DAY,'confirmed')
    e.room._profile=lambda *_a:None
    assert e.store.load_mastery(PLAN).check_ready()


def test_212_keep_grant_replaces_old_authorized_pending_on_enter(env):
    e=env;_passed(e)
    act(e,'start_check')
    old=next(q for q in assessment_questions(e.store.records('question_bank',PLAN)) if q['id']=='q0-1')
    def show(co,st,ctx):
        st.update(phase='check',step='checking',check={'queue':[old],'index':0})
        e.room._show_question(co,st,old,'check')
    e.room.run(PLAN,show)
    before=e.store.records('question_answers',PLAN)
    e.store.grant_final_check_questions_except(PLAN,'q0-0',grant_id='keep-one212')
    entered=act(e,'enter')
    assert entered['question']['id']=='q0-0'
    assert e.store.records('question_answers',PLAN)==before
    saved=e.room._load(e.room._course(PLAN))
    answer_id=saved['pending']['answer_id']
    assert saved['keep_question_grant']=='keep-one212'
    assert act(e,'enter')['question']['id']=='q0-0'
    assert e.room._load(e.room._course(PLAN))['pending']['answer_id']==answer_id


def test_212_kept_recall_reveal_and_ticks_survive_grant_then_finish_normally(env):
    e=env;_passed(e)
    assert act(e,'start_check')['question']['id']=='q0-0'
    act(e,'recall_reveal','q0-0','我自己的理解原话')
    act(e,'recall_draft','q0-0',[0])
    saved=e.room._load(e.room._course(PLAN))['pending']
    e.store.grant_final_check_questions_except(PLAN,'q0-0',grant_id='keep-draft212')
    entered=act(e,'enter')
    assert entered['question']['id']=='q0-0' and entered['question']['revealed']
    assert entered['question']['answer']=='我自己的理解原话'
    assert entered['question']['ticked']==[0]
    assert e.room._load(e.room._course(PLAN))['pending']['answer_id']==saved['answer_id']
    completed=act(e,'recall_submit','q0-0',[0],True)
    assert completed['step']=='completed'
    assert e.store.get_plan(PLAN)['state']=='completed'
    # The normal Report page creates the report on first plan.report read.
    PlanFlow(e.services,lambda *_a:None,lambda:DAY).report(PLAN)
    assert e.store.load_report(PLAN)['schema']=='mentorflow.course.report.v1'
    facts=e.store.records('question_answers',PLAN)
    assert len(facts)==1 and facts[0]['answer']=='我自己的理解原话'
    assert facts[0]['ticked']==[0] and facts[0]['missed']==[1,2] and facts[0]['self_correct'] is True
