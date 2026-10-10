import copy
import pytest

from refactor.learning.app.bridge import BridgeError
from refactor.learning.course.report import build_report
from tests.refactor.integration38.support import context
from .support97 import PLAN,DAY,make,fresh,act,position,finish_review,close

@pytest.mark.parametrize('learned',[0,2])
@pytest.mark.parametrize('correct',[9,8])
def test_direct97_all_questions_any_position_threshold_and_resume(tmp_path,learned,correct):
    e=make(tmp_path)
    try:
        before=position(e,learned,'teach' if learned==0 else 'teaching')
        assert act(e,'enter')['can_skip']
        view=act(e,'start_test','skip_test');state=e.room._file(PLAN).read({})
        assert len(state['test']['queue'])==10 and not view['can_skip']
        # Includes the already explained points' questions, never only the tail.
        assert {q['point_ids'][0] for q in state['test']['queue']}=={'p0','p1','p2','p3'}
        ids=[q['id'] for q in state['test']['queue']]
        for i in range(10):view=act(e,'answer',['先进先出' if i<correct else '后进先出'])
        book=e.store.load_mastery(PLAN)
        assert all(book.question(qid).module_correct==(i<correct) for i,qid in enumerate(ids))
        assert all(book.question(qid).ever_wrong==(i>=correct) for i,qid in enumerate(ids))
        if correct==9:
            assert view['module']['state']=='skipped' and view['step']=='test_done'
            live=e.store.load_ledger(PLAN).to_dict()['credits']
            assert sum(c['minutes'] for c in live if c['unit_id'] in {'p0','p1','p2','p3'})==40
            assert all(c['state']=='confirmed' for c in live)
            profile=e.store.records('teacher_profile',PLAN)[0]['document']['text']
            assert f'已讲{learned}个' in profile and f'另有{4-learned}个点直接考通过' in profile
        else:
            current=e.room._file(PLAN).read({})
            assert all(current[k]==before[k] for k in ('module_id','index','step','conversation'))
            assert view['can_skip'] and view['module']['state']=='failed'
        # The question-grade contract: correct starts final check at 2, wrong at 1.
        # No check answer recorded here; module correctness is the persisted basis.
        assert len(e.store.records('question_answers',PLAN))==10
        assert len(e.store.records('judgements',PLAN))==10
    finally:close(e)

@pytest.mark.parametrize('learned',[0,2])
@pytest.mark.parametrize('skip',[False,True])
def test_direct97_questionless_sample_sources_and_self_choice(tmp_path,learned,skip):
    e=make(tmp_path,questions=0)
    try:
        before=position(e,learned)
        messages=copy.deepcopy(e.store.records('messages',PLAN))
        act(e,'start_test','skip_test');review=e.room._file(PLAN).read({})['direct_review']
        assert len(review['points'])==len(set(review['points']))==3
        if learned==0:assert set(review['points'])<={'p0','p1','p2','p3'}
        else:assert set(review['points']) & {'p0','p1'}  # fallback to whole module (only 2 unlearned)
        view=finish_review(e)
        assert view['direct_review']['number']==3
        assert e.store.records('messages',PLAN)==messages
        assert not e.store.records('judgements',PLAN) and not e.store.records('question_answers',PLAN)
        assert not e.store.records('attempt_closed',PLAN)
        assert '我觉得' not in e.room._file(PLAN).path.read_text(encoding='utf-8')
        view=act(e,'direct_choice',skip)
        if skip:
            assert view['module']['state']=='skipped' and view['step']=='test_done'
            assert len(e.store.records('attempt_closed',PLAN))==1
            assert not e.store.records('judgements',PLAN)
            live=e.store.load_ledger(PLAN).to_dict()['credits']
            assert sum(c['minutes'] for c in live)==40 and all(c['state']=='confirmed' for c in live)
            # Reports are only generated after the whole plan is complete.
            e.store.add_credit(PLAN,'learn:other','other',10,DAY,'confirmed')
            e.store.complete_plan(PLAN,today=DAY)
            report=build_report(e.store,PLAN,today=DAY)
            assert report['modules'][0]['state']=='skipped' and report['modules'][0]['test_count']==0
        else:
            current=e.room._file(PLAN).read({})
            assert all(current[k]==before[k] for k in ('module_id','index','step','conversation'))
            assert not e.store.records('attempt_closed',PLAN)
            assert len(e.store.load_ledger(PLAN).to_dict()['credits'])==learned
    finally:close(e)


@pytest.mark.parametrize('step,phase',[('testing','learning'),('retesting','learning'),('checking','check'),('practicing','practice')])
def test_direct97_entry_hidden_and_rejected_during_other_assessments(tmp_path,step,phase):
    e=make(tmp_path)
    try:
        position(e,0,step)
        state=e.room._file(PLAN).read({});state['phase']=phase;e.room._file(PLAN).write(state)
        assert not act(e,'enter')['can_skip']
        rejected=e.room.perform(PLAN,'start_test',context(),('skip_test',))
        assert '当前答题' in rejected['failure']['message'] and not rejected['can_skip']
        assert not e.store.records('attempt_closed',PLAN)
    finally:close(e)


def test_direct97_registered_bridge_resumes_questionless_dialogue(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from refactor.learning.app import classroom
    e=make(tmp_path,questions=0)
    methods={}
    try:
        before=position(e,1)
        monkeypatch.setattr(classroom,'Classroom',lambda *a,**kw:e.room)
        classroom.register(SimpleNamespace(register=lambda name,fn:methods.setdefault(name,fn),push=lambda *a:None),lambda:e.services,lambda:DAY)
        def call(name,**params):return methods[name](dict(plan_id=PLAN,**params),context())
        assert call('classroom.test',kind='skip_test')['step']=='direct_review'
        for i in range(3):
            view=call('classroom.reply',text='随口说一句')
            if i<2:call('classroom.direct_next')
        assert view['step']=='direct_choice'
        view=call('classroom.direct_choice',skip=False)
        assert view['step']==before['step'] and view['points'][1]['current']
        assert not e.store.records('judgements',PLAN)
    finally:close(e)

def test_direct97_three_unlearned_only_and_small_module(tmp_path):
    e=make(tmp_path/'three',questions=0,points=5)
    try:
        position(e,2);act(e,'start_test','skip_test')
        assert set(e.room._file(PLAN).read({})['direct_review']['points'])=={'p2','p3','p4'}
    finally:close(e)
    e=make(tmp_path/'two',questions=0,points=2)
    try:
        act(e,'start_test','skip_test')
        assert act(e,'enter')['direct_review']['total']==2
        assert finish_review(e)['step']=='direct_choice'
    finally:close(e)

def test_direct97_reopen_during_test_and_second_casual_prompt(tmp_path):
    e=make(tmp_path/'graded')
    try:
        before=position(e,2);act(e,'start_test','skip_test');act(e,'answer',['先进先出'])
        original=e.room._file(PLAN).read({})
        e.room=fresh(e);view=act(e,'enter')
        assert view['question']['id']==original['pending']['question']['id']
        assert e.room._file(PLAN).read({})['test']['resume']['index']==before['index']
        for _ in range(9):view=act(e,'answer',['先进先出'])
        assert view['module']['state']=='skipped'
        assert len(e.store.records('question_answers',PLAN))==10
    finally:close(e)
    e=make(tmp_path/'casual',questions=0)
    try:
        position(e,1);act(e,'start_test','skip_test');act(e,'reply','随口答一句');view=act(e,'direct_next')
        before=copy.deepcopy(view['direct_review']);calls=len(e.teacher.calls)
        e.room=fresh(e);view=act(e,'enter')
        assert view['direct_review']==before and len(e.teacher.calls)==calls
        finish_review(e);view=act(e,'direct_choice',False)
        assert view['step']=='teaching' and view['points'][1]['current']
        assert not e.store.records('question_answers',PLAN)
    finally:close(e)

@pytest.mark.parametrize('questions',[0,10])
def test_direct97_database_committed_before_room_save_recovers_once(tmp_path,monkeypatch,questions):
    e=make(tmp_path,questions=questions)
    try:
        position(e,1);act(e,'start_test','skip_test')
        if questions:
            for _ in range(9):act(e,'answer',['先进先出'])
            action,args='answer',(['先进先出'],)
        else:
            finish_review(e);action,args='direct_choice',(True,)
        monkeypatch.setattr(e.room,'_save',lambda *a:(_ for _ in ()).throw(BridgeError('CLASS_SAVE_FAILED','save interrupted')))
        with pytest.raises(BridgeError):act(e,action,*args,request_id='commit-before-save')
        ledger=e.store.load_ledger(PLAN).to_dict()
        e.room=fresh(e);view=act(e,'enter')
        assert view['step']=='test_done' and view['module']['state']=='skipped'
        assert e.store.load_ledger(PLAN).to_dict()==ledger
        assert len(e.store.records('attempt_closed',PLAN))==1
    finally:close(e)

@pytest.mark.parametrize('step',['teach','opening','teaching','closing','test_offer'])
def test_direct97_entry_all_learning_steps_and_single_point_i_know_unchanged(tmp_path,step):
    e=make(tmp_path)
    try:
        position(e,0,step);assert act(e,'enter')['can_skip']
        if step=='teaching':
            view=act(e,'reply','我会了')
            assert view['points'][1]['current'] and not e.store.records('question_answers',PLAN)
    finally:close(e)
