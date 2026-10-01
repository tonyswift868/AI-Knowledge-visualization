"""Durable explicit rounds; semantic uncertainty is still independently reviewed."""
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.learning.course import question_review as qr
from refactor.learning.connections.errors import ModelError
from refactor.learning.app.classroom import Classroom
from refactor.learning.course.store import CourseStore
from refactor.learning.course.ledger import PlanWindow
from tests.refactor.question_review.test_question_review26 import lab, fill, PLAN, short

DATA = json.loads((Path(__file__).parent / 'data/bank_recovery30.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('q', DATA['questions'], ids=lambda q: q['id'])
def test_saved_math_simultaneous_operation_reaches_blind_solver(q):
    assert not qr.check_question(q, points=DATA['points'], excerpts=DATA['excerpts'])
    _, messages = qr.review_request(q, points=DATA['points'], excerpts=DATA['excerpts'])
    assert 'semantic_hints' in json.loads(messages[-1]['content'].split('\n', 1)[1])


def test_exhaustion_survives_reopen_but_user_round_recovers(lab):
    room, store, calls, reviewer, services = lab
    reject = dict(verdict='reject', issues=['资料不能支持答案'], revised=None)
    reviewer.actions = [reject] * 20
    with pytest.raises(ModelError): fill(room, point_ids=['p0'])
    assert not store.records('question_bank', PLAN)
    total = len(calls) + len(reviewer.forced_calls)
    with pytest.raises(ModelError): fill(room, point_ids=['p0'])
    assert len(calls) + len(reviewer.forced_calls) == total == 2
    reviewer.actions.clear()
    fill(room, point_ids=['p0'], retry_round='user-click-1')
    assert len(store.records('question_bank', PLAN)) == 2
    total = len(calls) + len(reviewer.forced_calls)
    fill(room, point_ids=['p0'], retry_round='user-click-1')
    assert len(calls) + len(reviewer.forced_calls) == total


def test_explicit_retry_resumes_network_draft_without_regeneration(lab):
    room,store,calls,reviewer,_=lab
    reviewer.actions=[ModelError('MODEL_TIMEOUT','核对超时')]
    with pytest.raises(ModelError): fill(room,point_ids=['p0'])
    generated=len(calls)+len(reviewer.forced_calls)
    fill(room,point_ids=['p0'],retry_round='network-retry')
    assert len(calls)+len(reviewer.forced_calls)==generated==1
    assert len(store.records('question_bank',PLAN))==2


def test_round_history_keeps_exhausted_counters_and_candidates(tmp_path):
    journal = qr.ReviewJournal('test30', tmp_path, PLAN, 'm', {'p': dict(scope='saved')})
    with journal.lock:
        journal.start_generation(['p']); journal.start_generation(['p'])
        with pytest.raises(ValueError): journal.start_generation(['p'])
        q = short(point_ids=['p']); token, _ = journal.add(q)
        journal.begin_round(['p'], 'click')
        journal.start_generation(['p'])
        journal.begin_round(['p'], 'click')
        assert journal.generations('p') == 1
        row = journal._point('p')[1]
        assert row['rounds'][0]['generation_attempts'] == 2
        assert row['candidates'] == [token]
    reopened = qr.ReviewJournal('test30', tmp_path, PLAN, 'm', {'p': dict(scope='saved')})
    assert reopened.generations('p') == 1


def test_failed_job_stops_polling_and_user_retry_has_one_token(lab):
    room, store, _, _, _ = lab
    course = room._course(PLAN)
    wait = dict(module_id='m', points=['p0'], method='_next_check', args=[], step='checking',
                needs={'p0': 1}, used_angles={}, asked=[])
    state = dict(step='preparing_questions', phase='check', module_id='m', bank_wait=deepcopy(wait))
    messages, requests = [], []
    room._say = lambda co, st, text, **kw: messages.append(text)
    room._prepare_bank_later = lambda co, mid, **kw: requests.append(kw)
    room._bank_jobs[(PLAN, 'm')] = dict(fingerprint=room._test_fingerprint(course, 'm'), status='failed', error='两次核对仍无原文依据。')
    room._resume_bank_wait(course, state, None)
    assert state['step'] == 'checking' and 'bank_wait' not in state
    assert '列表追加p0' in messages[-1] and '继续检验' in messages[-1] and '离开' in messages[-1]
    for _ in range(10): room._resume_bank_wait(course, state, None)
    assert not requests and len(messages) == 1
    room.start_check(course, state, None)
    assert state['step'] == 'preparing_questions' and len(requests) == 1
    assert requests[0]['retry_round'] and state['bank_wait'] == wait
    assert room._retry_bank(course, state) is False  # no extra round from duplicate call


def test_bad_multi_claim_still_rejected_by_independent_review(lab):
    room, store, _, reviewer, _ = lab
    reviewer.actions = [dict(verdict='reject', issues=['一个要点塞了修改列表和返回值两个要求'], revised=None)] * 30
    with pytest.raises(ModelError): fill(room, point_ids=['p0'])
    assert not store.records('question_bank', PLAN)
    assert all('verification' in payload for payload in reviewer.calls)


def test_acceptance_saved_candidate_and_saved_solver_review_recover_exhausted_bank(tmp_path):
    """Actual recorded model responses, not a fabricated quality verdict."""
    saved = DATA['recovery']; q = saved['question']; pid = q['point_ids'][0]
    day = date(2026,10,2)
    with CourseStore('replay30', tmp_path) as store:
        store.create_plan(PLAN, goal='等式性质', window=PlanWindow(day,day+timedelta(days=4),90))
        store.add_sources(PLAN, [dict(id='s', url='https://openstax.org/books/prealgebra-2e/pages/8-1',
            chunks=[dict(chunk_id=ref,text=text) for ref,text in DATA['excerpts'].items()])])
        store.save_outline(PLAN,[dict(id='st',title='方程')],[dict(id='m',stage_id='st',title='等式')])
        store.save_generated_module(PLAN,'m',[dict(DATA['points'][0],minutes=15)],
                                    check_minutes=2,final_check_minutes=4,check_question_minutes=2,today=day)
        store.start_plan(PLAN,today=day)
        calls = []
        def complete(purpose, messages, **kwargs):
            calls.append(purpose)
            # Saved Verdict dataclass adds review=None; it was not a wire field.
            judgement = {k:v for k,v in saved['verification']['judgement'].items() if k != 'review'}
            rows = {'question_solve': saved['verification']['solution'],
                    'judge': judgement, 'question_review': saved['review']}
            return SimpleNamespace(text=json.dumps(rows[purpose],ensure_ascii=False))
        services = SimpleNamespace(store=store,account_id='replay30',data_root=tmp_path,
                                   gateway=SimpleNamespace(complete=complete))
        room = Classroom(services,lambda:day)
        room._questions = lambda *a, **kw: [deepcopy(q)]
        course = room._course(PLAN)
        scope = dict(version=qr.VERSION,fingerprint=room._test_fingerprint(course,'m'),point_id=pid,
                     mode='reserve',force=False,refill_for=None,reserve=1,quota=None,used_angles=[])
        journal = qr.ReviewJournal('replay30',tmp_path,PLAN,'m',{pid:scope})
        with journal.lock: journal.start_generation([pid]); journal.start_generation([pid])
        with pytest.raises(ModelError): room._bank_fill(course,None,'m',point_ids=[pid],reserve={pid:1})
        assert not calls and not store.records('question_bank',PLAN)
        room._bank_fill(course,None,'m',point_ids=[pid],reserve={pid:1},retry_round='continue-check')
        selected = room._bank_select(course,None,'m',[pid])
        assert len(selected)==1 and selected[0]['stem']==q['stem']
        assert calls==['question_solve','judge','question_review']
