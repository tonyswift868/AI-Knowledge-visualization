from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from refactor.learning.course.mastery import AttemptClosed, Judgement
from tests.refactor.integration38.support import context
from tests.refactor.plan66.test_plan66_just_in_time_materials import DAY, PLAN, env, seed_module


def _close_module(course, module_id, *, wrong=(), include=None, attempt=None, seq=1000):
    store=course.store
    questions=[q for q in course.store.records('question_bank',PLAN)
               if q.get('module_id')==module_id and q.get('role','bank')=='bank']
    if include is not None:questions=[q for q in questions if q['id'] in include]
    attempt=attempt or f'assessment-{module_id}'
    ids=[q['id'] for q in questions]
    for index,q in enumerate(questions):
        store.append_judgement(PLAN,Judgement(
            f'j-{attempt}-{index}',seq+index,datetime(2026,10,27,tzinfo=timezone.utc),
            'module_test',module_id,attempt,
            tuple((pid,q['id'] not in wrong) for pid in q['point_ids']),question_id=q['id']))
    store.append_attempt_closed(PLAN,AttemptClosed(
        f'close-{attempt}',seq+len(questions)+1,module_id,attempt,'module_test'),
        details={'question_ids':ids,'assessment_id':attempt})
    return questions,attempt


def _seed_all_except_fixture_module(e):
    for module in e.store.records('modules',PLAN):
        if module['id']!='m0':seed_module(e.store,PLAN,module['id'])


@pytest.mark.parametrize(('module_state','module_correct','ready','has_questions','expected'),[
    ('passed',False,False,True,'next'),
    ('failed',False,True,True,'retest'),
    ('failed',None,True,True,'module_test'),
    ('failed',None,False,True,'prepare'),
    ('failed',None,True,False,'prepare'),
])
def test_204_shared_module_continuation_routes_to_actionable_path(
        module_state,module_correct,ready,has_questions,expected):
    from refactor.learning.app.classroom import Classroom

    class Book:
        def module(self,_module_id):return SimpleNamespace(state=module_state)
        def question(self,_question_id):return SimpleNamespace(module_correct=module_correct)

    questions=[{'id':'q0'}] if has_questions else []
    decision=Classroom._module_continuation(Book(),'m0',questions,ready)
    assert decision==expected


def test_204_failed_module_routes_once_to_real_false_question_retest(env, monkeypatch):
    e=env;room=e.room;course=room._course(PLAN)
    from types import SimpleNamespace
    room._materials=lambda:SimpleNamespace(mark_used=lambda *_a,**_k:None)
    _seed_all_except_fixture_module(e)
    rows={m['id']:course.store.records('question_bank',PLAN) for m in course.modules}
    module_rows=[q for q in rows['m1'] if q.get('module_id')=='m1' and q.get('role','bank')=='bank']
    ids=[q['id'] for q in module_rows[:2]]
    assert len(ids)==2
    # M1's original assessment was 1/2. All other modules earned their pass.
    _close_module(course,'m1',wrong={ids[0]},include=ids,attempt='m1-first',seq=1000)
    for index,mid in enumerate(('m0','m2','m3'),start=1):
        _close_module(course,mid,attempt=f'{mid}-first',seq=2000*index)
    assert room._course(PLAN).book().module('m1').state=='failed'

    state=room._rebuild(room._course(PLAN))
    state.update(module_id='m0',index=1,step='test_done',phase='learning')
    before=len(room._course(PLAN).store.messages(PLAN,'m1'))
    room.next_module(course,state,context())
    assert state['module_id']=='m1' and state['step']=='test_done'
    first_count=len(room._course(PLAN).store.messages(PLAN,'m1'))
    assert first_count==before+1
    # Repeating Continue leaves the learner at an actual test_done control and
    # does not append the same dead-end explanation again.
    room.next_module(course,state,context())
    assert len(room._course(PLAN).store.messages(PLAN,'m1'))==first_count

    view=room.snapshot(room._course(PLAN),state)
    assert view['test_readiness']['retest'] is True
    monkeypatch.setattr(room,'_ready',lambda *_a,**_k:False)
    room.start_test(room._course(PLAN),state,context(),'retest')
    assert [q['id'] for q in state['test']['queue']]==[ids[0]]
    assert state['test']['assessment_id']=='m1-first'
    assert state['test']['assessment_question_ids']==ids
    retest_attempt=state['test']['attempt']
    q=state['test']['queue'][0]
    e.store.append_judgement(PLAN,Judgement(
        'm1-retest-correct',9000,datetime(2026,10,27,tzinfo=timezone.utc),'retest','m1',
        retest_attempt,tuple((pid,True) for pid in q['point_ids']),question_id=q['id']))
    room._finish_test(room._course(PLAN),state,context())
    assert room._course(PLAN).book().module('m1').state=='passed'
    close=next(row for row in e.store.records('attempt_closed',PLAN)
               if row['event']['attempt_id']==retest_attempt)
    assert close['details']=={'question_ids':[ids[0]],'assessment_id':'m1-first'}
    assert '答对 2/2' in e.store.messages(PLAN,'m1')[-1]['content']


def test_204_check_offer_ignores_failed_preparation_after_all_modules_pass(env, monkeypatch):
    e=env;room=e.room;course=room._course(PLAN)
    _seed_all_except_fixture_module(e)
    from types import SimpleNamespace
    room._materials=lambda:SimpleNamespace(mark_used=lambda *_a,**_k:None)
    for index,mid in enumerate(('m0','m1','m2','m3'),start=1):
        _close_module(course,mid,attempt=f'{mid}-passed',seq=3000*index)
    assert course.book().check_ready()

    # Preparation failure/count drift must not hide final-exam entry.
    monkeypatch.setattr(room,'_ready',lambda *_a,**_k:False)
    monkeypatch.setattr(room,'_prepare',lambda *_a,**_k:(_ for _ in ()).throw(
        AssertionError('final-check entry must not wait for preparation')))
    state=room._rebuild(course)
    assert state['step']=='check_offer'
    view=room.perform(PLAN,'start_check',context())
    assert view['step']=='checking'
    assert view['question'] is not None
    assert view['test_readiness']['check'] is True


def test_204_completed_current_final_pool_ignores_stale_failed_preparation(env, monkeypatch):
    e=env;room=e.room;course=room._course(PLAN)
    _seed_all_except_fixture_module(e)
    for index,mid in enumerate(('m0','m1','m2','m3'),start=1):
        _close_module(course,mid,attempt=f'{mid}-passed',seq=4000*index)
    # A failed background preparation does not undo module passes or block
    # completion after every question in the current final-check pool passed.
    e.store.update_module_preparation_state(PLAN,'m0','failed',error_code='PAYLOAD')
    course=room._course(PLAN)
    from refactor.learning.course.question_bank import assessment_questions
    for index,q in enumerate(assessment_questions(e.store.records('question_bank',PLAN)),start=1):
        e.store.append_judgement(PLAN,Judgement(
            f'final-{index}',20000+index,datetime(2026,10,27,tzinfo=timezone.utc),'check',
            q['module_id'],f'final-attempt-{index}',tuple((pid,True) for pid in q['point_ids']),
            question_id=q['id']))
    course=room._course(PLAN)
    assert course.book().check_complete()
    monkeypatch.setattr(room,'_ready',lambda *_a,**_k:(_ for _ in ()).throw(
        AssertionError('completion of the current final pool must not await preparation')))
    monkeypatch.setattr(room,'_prepare',lambda *_a,**_k:(_ for _ in ()).throw(
        AssertionError('no preparation should run after the current pool is complete')))
    state=room._rebuild(course)
    state.update(phase='check',step='checking',check=None,check_used=0)
    room._next_check(course,state,context())
    assert state['step']=='completed'


def test_204_failed_module_without_a_current_wrong_question_keeps_retry_action(env, monkeypatch):
    from threading import Event
    from types import SimpleNamespace

    e=env;room=e.room;course=room._course(PLAN)
    statuses={'m0':'failed','m1':'passed','m2':'passed','m3':'passed'}
    class Book:
        def module(self,mid):return SimpleNamespace(state=statuses[mid])
        def question(self,_qid):return SimpleNamespace(module_correct=None)
        def check_ready(self):return False
    course.book=lambda:Book()
    state=room._rebuild(room._course(PLAN))
    state.update(module_id='m0',index=1,step='test_done',phase='learning')
    attempts=[]
    def prepare(_course,mid,*,retry=False):
        attempts.append((mid,retry))
        job=dict(module_id=mid,status='running',cancel=Event(),done=0,total=1,text='准备中',error=None,
                 fingerprint='test')
        room._jobs[(PLAN,mid)]=job
        return job
    monkeypatch.setattr(room,'_ready',lambda *_a,**_k:False)
    monkeypatch.setattr(room,'_prepare',prepare)

    room.next_module(course,state,context())
    assert state['step']=='test_done' and state['material_notice']['kind']=='module_test'
    first=len(e.store.messages(PLAN,'m0'))
    room.next_module(course,state,context())
    assert len(e.store.messages(PLAN,'m0'))==first
    assert attempts==[('m0',True)]


def test_204_attempt_close_saves_original_manifest_and_retest_link(env):
    e=env;room=e.room;course=room._course(PLAN)
    _seed_all_except_fixture_module(e)
    from types import SimpleNamespace
    room._materials=lambda:SimpleNamespace(mark_used=lambda *_a,**_k:None)
    state=room._rebuild(course)
    state.update(module_id='m1',index=1,step='test_offer',phase='learning')
    room._ready=lambda *_a,**_k:True
    room.start_test(course,state,context(),'module_test')
    test=state['test'];ids=[q['id'] for q in test['queue']]
    attempt=test['attempt']
    # Complete with already persisted deterministic responses to avoid model/UI
    # behavior while exercising the actual close metadata path.
    for index,q in enumerate(test['queue']):
        course.store.append_judgement(PLAN,Judgement(
            f'close-j-{index}',5000+index,datetime(2026,10,27,tzinfo=timezone.utc),
            'module_test','m1',attempt,tuple((pid,True) for pid in q['point_ids']),question_id=q['id']))
    room._finish_test(course,state,context())
    row=next(r for r in course.store.records('attempt_closed',PLAN)
             if r['event']['attempt_id']==attempt)
    assert row['details']=={'question_ids':ids,'assessment_id':attempt}

    state.update(module_id='m1',index=1,step='test_done',phase='learning')
    course=room._course(PLAN)
    # Add one late question after the first close. It must not change the
    # original test denominator, and it remains available for final mastery.
    import copy
    template=room._questions(course,'m1')[0]
    q=copy.deepcopy(template)
    q.update(id='m1-late-question',stem='迟到的新题？')
    course.store.add_bank_questions(PLAN,'m1',[q])
    course=room._course(PLAN)
    assert all(course.book().question(qid).module_correct is True for qid in ids)
    assert course.book().question('m1-late-question').module_correct is None
    assert room.snapshot(course,state)['module_assessment']==dict(correct=len(ids),total=len(ids),passed=True)
    for index,mid in enumerate(('m0','m2','m3'),start=1):
        _close_module(course,mid,attempt=f'{mid}-passed',seq=7000*index)
    course=room._course(PLAN)
    assert course.book().check_ready()
    assert 'm1-late-question' in course.book().next_check_questions(min_gap=3)
