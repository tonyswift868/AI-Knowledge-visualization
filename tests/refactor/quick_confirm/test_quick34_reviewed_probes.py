"""Real classroom/store wiring: no unreviewed confirmation or depth probe."""
import json
import time
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.refactor.question_review.test_question_review26 import lab, fill, PLAN, choice, short
from refactor.learning.app.classroom import Classroom
from refactor.learning.connections.errors import ModelError
from refactor.learning.course import question_review as qr, question_solver
from refactor.learning.course.judge_agent import Verdict
from refactor.learning.course.question_agent import Question


def position(room):
    co=room._course(PLAN)
    pending=dict(kind='casual',answer_id='original',asks=1,point_ids=['p0'],question=choice(stem='旧随口问',angle='recall'))
    state=dict(version=1,module_id='m',index=0,step='answer',pending=pending,day='2026-10-01',test=None)
    room._prepare_later=room._go_on=lambda *a,**kw:None
    return co,state,pending


def test_reviewed_bank_quick_is_instant_preferred_choice_and_excluded_later(lab):
    room,store,calls,reviewer,_=lab; fill(room)
    co,state,pending=position(room)
    before=(len(calls),len(reviewer.calls))
    started=time.monotonic();room._quick_check(co,state,None,pending)
    assert time.monotonic()-started<1
    assert before==(len(calls),len(reviewer.calls))
    selected=state['pending'];assert selected['question']['type']=='choice'
    asked,_=room._bank_history(co,'m');assert selected['question']['id'] in asked
    assert selected['question']['id'] not in [q['id'] for q in room._probe_pool(co,'p0')]
    assert selected['question']['id'] not in [q['id'] for q in room._bank_select(co,None,'m',['p0'])]
    assert not store.records('judgements',PLAN) and not store.records('question_answers',PLAN)


def test_legacy_review_journal_vouches_for_pre_marker_bank_without_calls(lab):
    room,store,calls,reviewer,_=lab;fill(room)
    co,state,pending=position(room)
    room._probe_review_file(co,'m').write({}) # simulate older reviewed-bank storage
    before=(len(calls),len(reviewer.calls))
    room._quick_check(co,state,None,pending)
    assert state['pending']['kind']=='quick' and before==(len(calls),len(reviewer.calls))


def test_old_unreviewed_bank_question_is_not_trusted(lab):
    room,store,_,_,_=lab;co,state,pending=position(room)
    old_id=store.add_bank_questions(PLAN,'m',[choice(angle='apply')],fingerprint=room._test_fingerprint(co,'m'))[0]
    assert old_id not in [q['id'] for q in room._probe_pool(co,'p0')]


def test_bank_missing_reuses_structure_blind_solve_review_before_exposure(lab):
    room,store,calls,reviewer,_=lab;co,state,pending=position(room)
    room._quick_check(co,state,None,pending)
    assert calls and reviewer.calls and all('verification' in c for c in reviewer.calls)
    selected=state['pending']['question']
    assert selected['id'] in room._probe_review_file(co,'m').read({})['question_ids']
    assert selected['id'] in {q['id'] for q in store.records('question_bank',PLAN)}
    assert all(c['verification'].get('matched') is True for c in reviewer.calls)


def test_quick_reopens_same_question_without_regenerating_or_double_message(lab):
    room,store,calls,reviewer,services=lab;fill(room)
    co,state,pending=position(room);room._quick_check(co,state,None,pending)
    chosen=deepcopy(state['pending']);before=(len(calls),len(reviewer.calls),len(store.records('messages',PLAN)))
    reopened=Classroom(services,room.study_day)
    restored=reopened._load(co)
    reopened._questions=lambda *a,**k:pytest.fail('reopening must not generate')
    reopened._quick_check(co,restored,None,restored['pending'])
    assert restored['pending']==chosen
    assert before==(len(calls),len(reviewer.calls),len(store.records('messages',PLAN)))


def test_review_interruption_reopens_saved_candidate_without_new_generation(lab):
    room,store,calls,reviewer,services=lab;co,state,pending=position(room)
    reviewer.actions=[ModelError('MODEL_TIMEOUT','核对中断')]
    room._quick_check(co,state,None,pending)
    assert state['pending'] is None
    initial_calls=list(calls)
    reopened=Classroom(services,room.study_day)
    reopened._questions=lambda *a,**k:pytest.fail('resume existing draft')
    reopened._prepare_later=reopened._go_on=lambda *a,**k:None
    co,state,pending=position(reopened)
    reopened._quick_check(co,state,None,pending)
    assert state['pending']['kind']=='quick' and calls==initial_calls


def test_failed_review_skips_quick_without_mastery_error_and_records_once(lab):
    room,store,_,reviewer,_=lab;co,state,pending=position(room)
    reviewer.actions=[dict(verdict='reject',issues=['答案写在这里，不应显示给学生'],revised=None)]*6
    room._quick_check(co,state,None,pending)
    assert state['pending'] is None and state['index']==1
    assert not store.records('judgements',PLAN) and not store.records('question_answers',PLAN)
    audit=[m for m in store.records('messages',PLAN) if m['kind']=='unconfirmed']
    assert len(audit)==1 and '答案写在这里' not in audit[0]['content']
    assert '答案写在这里' in pending['probe_failure']['reason']


def test_deepen_uses_new_angle_and_no_model_and_preserves_context(lab):
    room,store,calls,reviewer,_=lab;fill(room)
    co,state,pending=position(room)
    before=(len(calls),len(reviewer.calls));started=time.monotonic()
    room._deepen_question(co,state,None,pending,Verdict('clarify',None,(),'拿不准。'))
    assert time.monotonic()-started<1 and before==(len(calls),len(reviewer.calls))
    q=state['pending']['question']
    assert q['angle']!='recall' and q['stem']!=pending['question']['stem']
    assert state['pending']['kind']=='casual' and state['pending']['depth_probe']
    assert q['id'] in room._bank_history(co,'m')[0]


def test_deepen_missing_bank_reviews_then_reopens_without_reasking(lab):
    room,store,calls,reviewer,services=lab;co,state,pending=position(room)
    room._deepen_question(co,state,None,pending,Verdict('clarify',None,(),'拿不准。'))
    selected=deepcopy(state['pending'])
    assert selected['depth_probe'] and reviewer.calls
    assert all(c['verification'].get('matched') is True for c in reviewer.calls)
    before=(len(calls),len(reviewer.calls),len(store.records('messages',PLAN)))
    restored=Classroom(services,room.study_day)._load(co)
    assert restored['pending']==selected
    assert selected['question']['id'] in room._bank_history(co,'m')[0]
    assert not room._probe_pool(co,'p0',selected['question'])
    assert before==(len(calls),len(reviewer.calls),len(store.records('messages',PLAN)))


@pytest.mark.parametrize('verdict',[Verdict('answer',False,('p0',),'原结果错误。',True),Verdict('clarify',None,(),'原结果拿不准。')])
def test_deepen_failure_keeps_original_result_or_original_unconfirmed_question(lab,verdict):
    room,store,_,reviewer,_=lab;co,state,pending=position(room)
    reviewer.actions=[dict(verdict='reject',issues=['题目不合格'],revised=None)]*6
    result=room._deepen_question(co,state,None,pending,verdict)
    assert state['pending'] is pending and not store.records('judgements',PLAN)
    assert (result is None) if verdict.correct is None else (result.correct is False and not result.uncertain)
    assert len([m for m in store.records('messages',PLAN) if m['kind']=='unconfirmed'])==1


def test_double_correct_acceptance_question_is_rejected_by_existing_pipeline(lab):
    room,store,_,reviewer,_=lab;co,state,pending=position(room)
    bad=json.loads(Path(__file__).with_name('double-choice34.json').read_text(encoding='utf-8'))
    # Independent arithmetic proof: both original A and D equations hold.
    y=Fraction(3,4);assert 4*y+3==8*y and 4*y+6==8*y+3
    bad.update(point_ids=['p0'],chunk_ids=['c'],angle='apply')
    store.add_bank_questions(PLAN,'m',[short(point_ids=['p1'],angle='contrast')],fingerprint=room._test_fingerprint(co,'m'))
    room._questions=lambda *a,**k:[bad]
    reviewer.forced_result=[bad]
    baseline=reviewer.complete
    def reject(purpose,messages,**kw):
        if purpose=='question_solve':
            public=json.loads(messages[-1]['content'].split('\n',1)[1])
            assert set(public)=={'type','stem','options'} # standard answer really absent
            return SimpleNamespace(text=json.dumps(dict(answerable=True,answer=public['options'][0],viable_options=[0,3],reason='两条代数等式都成立。'),ensure_ascii=False))
        if purpose=='question_review':
            payload=json.loads(messages[-1]['content'].split('\n',1)[1])
            reviewer.calls.append(payload)
            assert payload['verification']['matched'] is False
            return SimpleNamespace(text='{"verdict":"reject","issues":["两个选项都成立"],"revised":null}')
        return baseline(purpose,messages,**kw)
    reviewer.complete=reject
    room._quick_check(co,state,None,pending)
    assert state['pending'] is None and len(reviewer.calls)==2
    assert all(q['stem']!=bad['stem'] for q in store.records('question_bank',PLAN))
    assert not any(bad['stem'] in m['content'] for m in store.records('messages',PLAN))
