"""121: production plan wiring, persisted notes, classroom fallback and ledger."""
import copy
import json
import threading
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from refactor.learning.app.bridge import CallContext
from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course import planner_agents as a
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore
from tests.refactor.points115.test_directory115 import fake_model

DAY = date(2026,10,14)
PLAN = '1'*32


def chunks():
    return [dict(id=f'c{i}',source_id='source',position=i,section=['教材',title],text=text)
            for i,(title,text) in enumerate([
                ('书籍推荐','推荐阅读《算法入门》。本书介绍编程知识。'),
                ('队列','队列采用先进先出。栈采用后进先出。'),
                ('密度','密度等于质量除以体积。速度等于位移除以时间。'),
                ('压强','压强等于压力除以面积。压力相同则面积越小压强越大。')])]


@pytest.fixture
def env(tmp_path):
    store=CourseStore('wire121',tmp_path)
    store.create_plan(PLAN,goal='教材',window=PlanWindow(DAY,DAY+timedelta(days=5),60))
    store.add_sources(PLAN,[dict(id='source',title='教材',kind='url',url='https://example.edu/text',adopted=True,
        chunks=[dict(chunk_id=c['id'],text=c['text'],section=c['section']) for c in chunks()])])
    module=dict(id='m',key='m',title='教材',summary='学习原文',prerequisites=[],refs=[])
    result=a.directory_plan_module(module,chunks(),fake_model)
    module.update(result)
    store.save_outline(PLAN,[dict(id='s',title='阶段')],[dict(id='m',stage_id='s',title='教材',navigation=result['navigation'])])
    store.save_generated_module(PLAN,'m',result['points'],check_minutes=0,final_check_minutes=0,
                                check_question_minutes=2,today=DAY)
    calls=[]
    def complete(purpose,messages,**kw):
        calls.append((purpose,kw['system']))
        return SimpleNamespace(text=fake_model('notes',kw['system'],messages))
    services=SimpleNamespace(store=store,gateway=SimpleNamespace(complete=complete),account_id='wire121',data_root=tmp_path)
    flow=PlanFlow(services,lambda *args:None,lambda:DAY)
    ctx=CallContext('test',threading.Event(),lambda _:None)
    yield SimpleNamespace(store=store,flow=flow,module=module,calls=calls,ctx=ctx,services=services)
    services._closed=True
    for job in flow._point_minute_jobs['jobs'].values():
        job['cancel'].set(); job['thread'].join(5)
    store.close()


def test_121_creation_is_directory_owned_and_has_no_notes_calls():
    stages=[]
    def complete(stage,*args):
        stages.append(stage);return fake_model(stage,*args)
    result=a.directory_plan_module(dict(id='m',title='教材'),chunks(),complete)
    assert stages==['classify']
    assert result['navigation'][0]['title'].endswith('书籍推荐')
    assert not any('书' in p['title'] for p in result['points'])
    assert all(p['structure']=='concept_v1' and p['goal']==p['summary'] for p in result['points'])
    assert all(s['notes']==[] and s['id'].startswith(p['id']+'-s') for p in result['points'] for s in p['subpoints'])
    assert {cid for p in result['points'] for cid in p['chunk_ids']}=={'c1','c2','c3'}


def test_121_module_notes_persist_sync_and_skip_existing(env):
    assert not any(s['notes'] for p in env.store.records('points',PLAN) for s in p['subpoints'])
    env.flow._prepare_notes(PLAN,'m',env.ctx)
    updated=env.store.records('points',PLAN)
    assert all(s['notes'] for p in updated for s in p['subpoints'])
    assert all(n['chunk_id'] in s['chunk_ids'] for p in updated for s in p['subpoints'] for n in s['notes'])
    count=len(env.calls)
    env.flow._prepare_notes(PLAN,'m',env.ctx)
    assert len(env.calls)==count and updated==env.store.records('points',PLAN)
    assert any(r.entity=='points' for r in env.store.pending_records(10000))


def test_121_note_failure_retries_once_and_worker_still_prepares(env,monkeypatch):
    # Exercise note retries, independent of the host's peak-hour queue policy.
    monkeypatch.setattr(env.flow,'_material_peak_now',lambda now=None:False)
    def bad(*args,**kw):env.calls.append('failure');raise RuntimeError('offline transport')
    env.services.gateway.complete=bad
    observed=[]
    def prepare(store,plan,mid,ctx,progress):
        observed.append(store.records('points',plan));return {}
    monkeypatch.setattr(env.flow,'_record_preparation',lambda *a,**k:{'state':a[2]})
    monkeypatch.setattr(env.flow,'_save_material_summary',lambda *a:None)
    monkeypatch.setattr(env.flow,'_module_ready',lambda *a:bool(observed))
    monkeypatch.setattr(env.flow,'ensure_point_minutes',lambda *a,**k:'ready')
    env.flow._prepare_remaining(PLAN,[env.module],prepare)
    for _ in range(100):
        with env.flow._material_jobs['guard']:
            job=env.flow._material_jobs['jobs'][(PLAN,'m')]
        if job['status'] not in ('running','queued'):break
        threading.Event().wait(.01)
    assert job['status']=='ready' and observed
    assert env.calls==['failure','failure']
    assert not any(s['notes'] for p in observed[0] for s in p['subpoints'])


def test_121_content_preview_and_saved_ledger_match_without_big_point_cap(env):
    draft=dict(plan_id=PLAN,phase='outline',sources=[dict(source_id='source',status='adopted')],
        form=dict(start=DAY.isoformat(),end=(DAY+timedelta(days=5)).isoformat(),
        daily_cap=60,weekday_weights=[1]*7,special_days=[]))
    ranges=env.flow._content_ranges(draft,[env.module],annotate=True)
    expected=sum(s['minutes'] for p in env.module['points'] for s in p['subpoints'])
    assert ranges['learning']==expected==env.store.get_plan(PLAN)['total_minutes']
    ledger=env.store.load_ledger(PLAN)
    assert sum(ledger.targets.values())==expected
    before=copy.deepcopy(env.store.records('points',PLAN))
    assert env.flow.ensure_point_minutes(PLAN)=='running'
    job=env.flow._point_minute_jobs['jobs'].get(PLAN)
    if job:job['thread'].join(5)
    assert not env.calls and before==env.store.records('points',PLAN)


def test_121_legacy_points_do_not_gain_structure_or_notes(env):
    p=env.store.records('points',PLAN)[0]
    p.pop('structure');p.pop('subpoints');p.pop('goal')
    with env.store.transaction():env.store._put('points',PLAN,p)
    env.flow._prepare_notes(PLAN,'m',env.ctx)
    assert not env.calls and env.store.records('points',PLAN)[0]==p


def test_121_empty_notes_classroom_uses_source_and_reads_later_notes(tmp_path,monkeypatch):
    from refactor.learning.app.classroom import Classroom
    from tests.refactor.concept114.test_concept114_classroom import concept_environment,task
    from tests.refactor.conversation37.test_grounded_conversation37 import act,PLAN as classroom_plan
    e=concept_environment(tmp_path)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    try:
        with e.store.transaction():
            p=e.store.records('points',classroom_plan)[0]
            for s in p['subpoints']:s['notes']=[]
            e.store._put('points',classroom_plan,p)
        view=act(e,'next')
        assert view['step']=='teaching' and not view['failure']
        assert task(e)['concept']['notes']==[]
        assert any('面积' in m['content'] for m in e.requests[-1][1])
        with e.store.transaction():
            p=e.store.records('points',classroom_plan)[0]
            p['subpoints'][1]['notes']=[dict(text='单位面积上的压力叫压强。',chunk_id='c1')]
            e.store._put('points',classroom_plan,p)
        act(e,'next')
        assert task(e)['concept']['notes'][0]['text']=='单位面积上的压力叫压强。'
    finally:
        e.room.cancel_materials();e.store.close()
