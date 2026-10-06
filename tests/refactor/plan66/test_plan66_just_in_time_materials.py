"""Real store/admission and scheduler; only preparation IO is substituted."""
import copy
import threading
import time
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from refactor.learning.app.classroom import Classroom
from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore
from tests.refactor.integration38.support import bank_question, context
from tests.refactor.plan37.test_grounded_plan37_flow import fixture, adopt_and_map

ACCOUNT = 'mfa1_' + '6' * 64
PLAN = '6' * 32
DAY = date(2026, 10, 6)


def seed_module(store, pid, mid):
    from refactor.learning.course.point_materials import _selection_passages
    refs = {c['id']: c['source'] for c in _selection_passages(store, pid)}
    for p in store.records('points', pid):
        if p['module_id'] != mid: continue
        source = refs[p['chunk_ids'][0]]
        rows = [bank_question(p['id'], mid, kind='choice', index=i) | dict(source=source, answer_source=source) for i in range(2)]
        quick = bank_question(p['id'], mid, kind='choice', index=2) | dict(source=source, answer_source=source)
        quick.update(role='quick', origin='cloze')
        rows.append(quick)
        store.add_bank_questions(pid, mid, rows)
    # Scheduler tests replace preparation IO. Persist the new completion
    # contract too; the synthetic reviewed legacy bank tests classroom state.
    from refactor.learning.course.point_materials import prepare_module
    from tests.refactor.material_questions70.test_material_questions70_contract import split70
    prepare_module(store,pid,mid,dict(question_splitter=split70,cancel=threading.Event()),lambda *a:None)


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline: time.sleep(.005)
    assert predicate()


@pytest.fixture
def env(tmp_path):
    store = CourseStore(ACCOUNT, tmp_path)
    store.create_plan(PLAN, goal='资料备题节奏', final_goal='理解定义', level='beginner', window=PlanWindow(DAY, DAY+timedelta(days=10), 120))
    store.add_sources(PLAN, [dict(id='s', kind='url', url='https://docs.python.org/3/tutorial/', title='Python官方资料', adopted=True,
        chunks=[dict(chunk_id='k1', text='队列先进先出，栈后进先出。', section=['定义'])])])
    store.save_outline(PLAN, [dict(id='st', title='阶段')], [dict(id=f'm{i}', stage_id='st', title=f'模块{i}', position=i) for i in range(3)])
    for i in range(3):
        store.save_generated_module(PLAN, f'm{i}', [dict(id=f'p{i}', title=f'概念{i}', summary='掌握定义', minutes=8, chunk_ids=['k1'])], check_minutes=2, today=DAY)
    store.start_plan(PLAN, today=DAY)
    services = SimpleNamespace(store=store, gateway=None, account_id=ACCOUNT, data_root=tmp_path, _closed=False,
        settings=SimpleNamespace(load=lambda:dict(refresh_time='04:00')))
    flow = PlanFlow(services, lambda *_: None, lambda: DAY)
    room = Classroom(services, lambda: DAY)
    calls = []
    def prepare(store, pid, mid, ctx, progress):
        calls.append(mid)
        seed_module(store, pid, mid)
        progress(1, 1, '已备好')
    material = SimpleNamespace(prepare_module=prepare)
    room._materials = lambda: material
    seed_module(store, PLAN, 'm0')
    e = SimpleNamespace(store=store, services=services, flow=flow, room=room, calls=calls, materials=material)
    yield e
    room.cancel_materials()
    services._closed = True
    wait_for(lambda: PLAN not in flow._preparing)
    store.close()


def test_generate_prepares_first_and_second_only(fixture):
    flow, _, ctx, _, _ = fixture
    draft = adopt_and_map(fixture)
    original = draft['outline']['stages'][0]['modules'][0]
    modules = []
    for i in range(3):
        m = copy.deepcopy(original)
        m.update(id=f'm{i}', key=f'm{i}', title=f'压强模块{i}')
        modules.append(m)
    draft['outline']['stages'][0]['modules'] = modules
    flow.estimate(draft)
    calls = []
    def prepare(store, pid, mid, ctx, progress):
        calls.append(mid); seed_module(store, pid, mid)
    flow._prepare_module = prepare
    flow.generate(draft, ctx)
    wait_for(lambda: draft['plan_id'] not in flow._preparing)
    assert calls == ['m0', 'm1']
    assert not [q for q in flow.services.store.records('question_bank', draft['plan_id']) if q['module_id']=='m2']
    assert flow.load(draft['plan_id'])['generation'] == dict(m0='done', m1='done', m2='pending')


def test_enter_second_prefetches_third_and_ready_does_not_repeat(env):
    e = env
    e.room.perform(PLAN, 'enter', context())
    wait_for(lambda: PLAN not in e.flow._preparing)
    assert e.calls == ['m1']
    e.room.perform(PLAN, 'enter', context())
    assert e.calls == ['m1']
    e.room.run(PLAN, lambda co, st, cx: st.update(module_id='m1', index=0, step='teach'), context())
    wait_for(lambda: PLAN not in e.flow._preparing)
    assert e.calls == ['m1', 'm2']
    e.room.perform(PLAN, 'enter', context())
    assert e.calls == ['m1', 'm2']


def test_failed_second_retries_on_entry_without_losing_first(env):
    e = env
    def prepare(store, pid, mid, ctx, progress):
        e.calls.append(mid)
        if len(e.calls)==1: raise RuntimeError('offline failure')
        seed_module(store, pid, mid)
    e.materials.prepare_module = prepare
    e.room.perform(PLAN, 'enter', context())
    wait_for(lambda: PLAN not in e.flow._preparing)
    assert e.store.records('modules', PLAN)[1]['material_preparation']['state']=='failed'
    assert e.room._ready(e.room._course(PLAN), 'm0')
    e.room.run(PLAN, lambda co, st, cx: st.update(module_id='m1', index=0, step='teach'), context())
    wait_for(lambda: PLAN not in e.flow._preparing)
    assert e.calls == ['m1', 'm1']
    assert e.store.records('modules', PLAN)[1]['material_preparation']['state']=='done'


def test_failed_next_is_not_polled_forever_and_retries_on_preceding_entry(env):
    e=env
    def prepare(store,pid,mid,ctx,progress):
        e.calls.append(mid)
        if len(e.calls)==1:raise RuntimeError('offline failure')
        seed_module(store,pid,mid)
    e.materials.prepare_module=prepare
    e.room.perform(PLAN,'enter',context())
    wait_for(lambda:PLAN not in e.flow._preparing)
    e.room.run(PLAN,lambda *args:None,context())
    assert e.calls==['m1']
    e.room.perform(PLAN,'enter',context())
    wait_for(lambda:PLAN not in e.flow._preparing)
    assert e.calls==['m1','m1']


def test_foreground_wait_is_nonblocking_and_deduplicates_generation_job(env):
    e = env; started = threading.Event(); release = threading.Event(); events=[]
    def prepare(store, pid, mid, ctx, progress):
        e.calls.append(mid); started.set()
        assert release.wait(2)
        seed_module(store, pid, mid)
    e.materials.prepare_module = prepare
    modules = e.store.records('modules', PLAN)
    e.flow._prepare_remaining(PLAN, [modules[1], modules[2]], prepare)
    assert started.wait(1)
    e.room.material_event = events.append
    entered_view=e.room.run(PLAN, lambda co, st, cx: st.update(module_id='m1', index=0, step='teach'), context())
    assert '这个模块的题还在准备' in entered_view['preparation']['text']
    begin=time.monotonic(); view=e.room.perform(PLAN, 'next', context())
    assert time.monotonic()-begin < 1 and view['step']=='preparing_materials'
    assert '这个模块的题还在准备' in view['preparation']['text']
    assert e.calls == ['m1']
    release.set(); wait_for(lambda: PLAN not in e.flow._preparing)
    assert events and e.calls == ['m1']


def test_one_worker_per_plan_even_when_current_is_queued(env):
    e=env; release=threading.Event(); active=0; peak=0; entered=threading.Event()
    def prepare(store, pid, mid, ctx, progress):
        nonlocal active, peak
        active+=1; peak=max(peak,active); e.calls.append(mid); entered.set()
        if mid=='m1': assert release.wait(2)
        seed_module(store,pid,mid); active-=1
    e.flow._prepare_remaining(PLAN,[dict(id='m1')],prepare)
    assert entered.wait(1)
    e.flow._prepare_remaining(PLAN,[dict(id='m2')],prepare)
    assert e.calls==['m1']
    release.set(); wait_for(lambda: PLAN not in e.flow._preparing)
    assert e.calls==['m1','m2'] and peak==1




def test_reopen_keeps_completed_bank_and_restarts_running_next(env):
    e=env
    e.store.save_module_preparation(PLAN,'m0',dict(state='done'))
    e.store.save_module_preparation(PLAN,'m1',dict(state='running'))
    fresh=CourseStore(ACCOUNT,e.services.data_root)
    services=SimpleNamespace(**vars(e.services)); services.store=fresh
    for key in ('_material_flow','_material_jobs'): delattr(services,key)
    room=Classroom(services,lambda:DAY); room._materials=lambda:e.materials
    try:
        room.perform(PLAN,'enter',context())
        wait_for(lambda: PLAN not in services._material_flow._preparing)
        assert e.calls==['m1'] and room._ready(room._course(PLAN),'m0')
        assert fresh.records('modules',PLAN)[1]['material_preparation']['state']=='done'
        assert not [q for q in fresh.records('question_bank',PLAN) if q['module_id']=='m2']
    finally:
        room.cancel_materials();fresh.close()


def test_cross_module_stem_dedup_normalizes_width_whitespace_punctuation(env):
    e=env
    first=bank_question('p0','m0',kind='choice') | dict(id='same-first',stem='ＡＢＣ，是什么？')
    second=bank_question('p1','m1',kind='choice') | dict(id='same-second',stem='A B C 是什么!')
    assert e.store.add_bank_questions(PLAN,'m0',[first])==['same-first']
    assert e.store.add_bank_questions(PLAN,'m1',[second])==[]
    assert not [q for q in e.store.records('question_bank',PLAN) if q['id']=='same-second']
    assert e.store.add_bank_questions(PLAN,'m0',[first])==['same-first']


def test_concurrent_cross_module_admission_and_role_compatibility(env):
    from concurrent.futures import ThreadPoolExecutor
    e=env
    rows=[bank_question(f'p{i}',f'm{i}',kind='choice')|dict(id=f'race-{i}',stem='同一个原文题面？') for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda q:e.store.add_bank_questions(PLAN,q['module_id'],[q]),rows))
    assert sum(map(len,results))==1
    admitted=next(q for q in e.store.records('question_bank',PLAN) if q['stem']=='同一个原文题面？')
    quick=dict(admitted,id='same-point-quick',role='quick',origin='cloze')
    assert e.store.add_bank_questions(PLAN,admitted['module_id'],[quick])==['same-point-quick']
