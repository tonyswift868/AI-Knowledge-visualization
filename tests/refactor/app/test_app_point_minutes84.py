"""Content weighting, atomic budgets, frozen history and background flow (no network)."""
from datetime import date
import json
import threading
from types import SimpleNamespace

import pytest
from refactor.learning.course import planner_agents as agents
from refactor.learning.course.store import CourseStore
from refactor.learning.app.plan_flow import PlanFlow
from .test_app_point_links82 import seed, PLAN, DAY


@pytest.fixture
def store(tmp_path):
    s = CourseStore('account', tmp_path)
    seed(s)
    with s.transaction():
        for i, p in enumerate(s.records('points', PLAN)):
            c = s._get('source_chunks', PLAN, 'c')
            c['text'] = '学' * 1800
            s._put('source_chunks', PLAN, c)
            p['citations'] = [dict(chunk_id='c', quote=c['text'][:(i+1)*100])]
            s._put('points', PLAN, p)
    yield s
    s.close()


@pytest.mark.parametrize('chars,questions,difficulty,expected', [(0,0,'易',3),(300,0,'中',4),
    (300,2,'中',8),(300,2,'难',10),(300,2,'易',6),(100000,100,'难',25)])
def test_minutes_formula(chars, questions, difficulty, expected):
    assert agents.estimate_point_minutes(chars, questions, difficulty) == expected


@pytest.mark.parametrize('weights,total', [([3,4,25],24),([.01,100,1],60),([3,3,3],24),
    ([3,25],2),([3,25],80),([],0)])
def test_minutes_allocation_exact_budget_deterministic(weights,total):
    values = agents.allocate_point_minutes(weights,total)
    assert sum(values) == total
    assert values == agents.allocate_point_minutes(weights,total)
    if weights:
        assert min(values) >= 1
        if 3*len(weights) <= total <= 25*len(weights): assert min(values)>=3 and max(values)<=25


def test_minute_provenance_dedup_and_no_summary_fallback():
    p = dict(id='p', summary='标题不能计数', citations=[dict(chunk_id='c',quote='原 文')]*2)
    assert agents.point_minute_metrics([p], [dict(id='c',text='原 文', section=[])],
        [dict(id='q',point_ids=['p','p'])]*2)['p'] == dict(characters=2,questions=1)
    p['citations'] = [dict(chunk_id='c',quote='不在原文')]
    assert agents.point_minute_metrics([p], [dict(id='c',text='原 文', section=[])], [])['p']['characters'] == 0


def labels(state): return {p['id']: ('难' if i % 3 == 0 else '易') for i,p in enumerate(state['points'])}


def test_minutes_atomic_total_frozen_ledger_and_sync(store,tmp_path):
    state = store.point_minutes_snapshot(PLAN)
    store.add_credit(PLAN,'learn:m00','m00',15,DAY)
    store.add_credit(PLAN,'relearn:m01:1','m01',15,DAY)
    before = {entity: store.records(entity,PLAN) for entity in ('credits','ledger_events')}
    assert store.save_point_minutes(PLAN,state['fingerprint'],labels(state))
    after = store.point_minutes_snapshot(PLAN)
    assert after['ready'] and sum(p['minutes'] for p in after['points']) == 180
    assert {p['id']:p['minutes'] for p in after['points'] if p['id'] in ('m00','m01')} == {'m00':15,'m01':15}
    assert after['plan']['total_minutes'] == state['plan']['total_minutes']
    assert after['plan']['window'] == state['plan']['window']
    for key in ('initial_total_minutes','initial_window'): assert after['plan'][key] == state['plan'][key]
    assert all(store.records(e,PLAN) == v for e,v in before.items())
    assert [m['check_minutes'] for m in after['modules']] == [m['check_minutes'] for m in state['modules']]
    other = CourseStore('account',tmp_path/'other')
    try:
        other.apply_remote([(r,i+1) for i,r in enumerate(store.pending_records(10000))])
        assert other.point_minutes_snapshot(PLAN)['ready']
        assert [p['minutes'] for p in other.records('points',PLAN)] == [p['minutes'] for p in after['points']]
    finally: other.close()


def test_minutes_failed_write_rolls_back_every_point_and_version(store,monkeypatch):
    state = store.point_minutes_snapshot(PLAN)
    put = store._put
    def fail(entity,plan,payload,**kwargs):
        if entity == 'modules': raise OSError('disk write failed')
        return put(entity,plan,payload,**kwargs)
    monkeypatch.setattr(store,'_put',fail)
    with pytest.raises(OSError): store.save_point_minutes(PLAN,state['fingerprint'],labels(state))
    assert store.point_minutes_snapshot(PLAN) == state


def test_minutes_stale_reply_preserves_values(store):
    state = store.point_minutes_snapshot(PLAN)
    with store.transaction():
        p = store._get('points',PLAN,'m00'); p['summary']='改变后的知识点'; store._put('points',PLAN,p)
    assert not store.save_point_minutes(PLAN,state['fingerprint'],labels(state))
    assert all(p['minutes']==15 for p in store.records('points',PLAN))


class Model:
    def __init__(self,gate=None,fail=False): self.calls=[]; self.gate=gate; self.fail=fail
    def complete(self,purpose,messages,**kwargs):
        if purpose=='point_links': return '[]'
        self.calls.append(purpose)
        if self.gate: self.gate.wait(3)
        if self.fail: raise RuntimeError('offline failure')
        rows=json.loads(messages[0]['content'])['points']
        return json.dumps([dict(id=p['id'],difficulty='中') for p in rows])


def flow(store,model,events):
    svc=SimpleNamespace(store=store,gateway=model,_closed=False,settings=None)
    return PlanFlow(svc,lambda n,d:events.append((n,d)),lambda:DAY)


def finish(f):
    for shared in (f._point_minute_jobs,f._point_link_jobs):
        for j in list(shared['jobs'].values()): j['thread'].join(5)
        assert not shared['jobs']


def test_minutes_old_detail_background_once_and_events(store):
    model, events, gate=Model(),[],threading.Event()
    model.gate=gate
    f=flow(store,model,events)
    other=PlanFlow(f.services,lambda *a:None,lambda:DAY)
    try:
        detail=f.detail(PLAN)
        assert 'stages' in detail
        assert other.ensure_point_minutes(PLAN)=='running'
        assert len(f._point_minute_jobs['jobs'])==1
    finally: gate.set()
    finish(f)
    assert store.point_minutes_snapshot(PLAN)['ready']
    assert f.ensure_point_minutes(PLAN)=='ready' and len(model.calls)==2
    assert [e[1]['status'] for e in events if e[0]=='plan.point_minutes_changed']==['running','ready']
    # Reopen with fresh services must use persisted version, not a process flag.
    assert flow(store,Model(),[]).ensure_point_minutes(PLAN)=='ready'


def test_minutes_failure_preserves_and_next_open_retries(store):
    model,events=Model(fail=True),[]
    f=flow(store,model,events)
    original=store.point_minutes_snapshot(PLAN)
    f.ensure_point_minutes(PLAN); finish(f)
    assert store.point_minutes_snapshot(PLAN)==original
    assert events[-1][1]['status']=='failed'
    model.fail=False
    f.ensure_point_minutes(PLAN); finish(f)
    assert store.point_minutes_snapshot(PLAN)['ready']


def test_minutes_late_question_bank_reweights_without_repeating_ai(store,monkeypatch):
    model=Model(); f=flow(store,model,[])
    f.ensure_point_minutes(PLAN); finish(f)
    before={p['id']:p['minutes'] for p in store.records('points',PLAN)}
    records=store.records
    bank=[dict(id=f'q{i}',point_ids=['m00']) for i in range(10)]
    monkeypatch.setattr(store,'records',lambda entity,plan='':bank if entity=='question_bank' else records(entity,plan))
    assert not store.point_minutes_snapshot(PLAN)['ready']
    f.ensure_point_minutes(PLAN); finish(f)
    assert model.calls==['point_difficulty','point_difficulty']
    assert store.point_minutes_snapshot(PLAN)['ready']
    points=store.records('points',PLAN)
    p=next(p for p in points if p['id']=='m00')
    assert p['minute_estimate']['questions']==10 and p['minutes']>before['m00']
    assert sum(p['minutes'] for p in points)==180


def test_minutes_account_exit_drops_reply(store):
    model,events,gate=Model(),[],threading.Event()
    model.gate=gate
    f=flow(store,model,events)
    f.ensure_point_minutes(PLAN)
    f.services._closed=True; gate.set(); finish(f)
    assert not store.point_minutes_snapshot(PLAN)['ready']
    assert events == [('plan.point_minutes_changed',dict(plan_id=PLAN,status='running'))]


def test_minutes_finished_preparation_does_not_read_closed_store(store):
    f=flow(store,Model(),[])
    store.close()
    # A completed queue can race fixture/account teardown before services flag changes.
    f._prepare_worker(PLAN)
    assert not f._point_minute_jobs['jobs']


def test_minutes_new_generation_initial_weights_before_preparation(store):
    events,model=[],Model()
    def prepare(*args,**kwargs):
        points=store.records('points',PLAN)
        assert len({p['minutes'] for p in points})>1
        assert sum(p['minutes'] for p in points)==24
        raise RuntimeError('materials stub')
    f=flow(store,model,events); f._prepare_module=prepare
    f.save=lambda draft:None; f._save_outline=lambda draft:None
    draft=dict(plan_id=PLAN,phase='outline',estimate={'total':24},form={},sources=[],messages=[],outline=dict(grounded=True,
        stages=[dict(id='stage',modules=[dict(id='m0',key='m0',title='模块',points=[
            dict(title='短点',summary='短说明',chunk_ids=['c'],citations=[]),
            dict(title='长点',summary='长说明',chunk_ids=['c'],citations=[]),
            dict(title='更长点',summary='更长说明',chunk_ids=['c'],citations=[])])])]))
    # Real finalization resolves original spans; use adopted source and valid offsets.
    draft['sources']=[dict(source_id='s',status='adopted')]
    for i,p in enumerate(draft['outline']['stages'][0]['modules'][0]['points'],1):
        p['citations']=[dict(chunk_id='c',paragraph_id=f'c@0:{i*500}',paragraph_start=0,paragraph_end=i*500)]
    f._adopted=lambda d:([dict(id='s')],None)
    # Existing other module would inflate budget; remove it before new generation.
    store.delete_module(PLAN,'m1',today=DAY)
    with pytest.raises(RuntimeError,match='materials stub'):
        f.generate(draft,SimpleNamespace(cancel=threading.Event(),stream=lambda s:None))
    finish(f)
    assert model.calls==['point_difficulty']


@pytest.mark.parametrize('rows', [[],[dict(id='P2',difficulty='中')],
    [dict(id='P1',difficulty='偏难')],[dict(id='P1',difficulty='中')]*2])
def test_minutes_difficulty_validation(rows):
    with pytest.raises(agents.AgentFormatError): agents.parse_point_difficulty(json.dumps(rows),{'P1':'real'})
