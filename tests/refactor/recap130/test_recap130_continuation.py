import copy
import json
import logging

import pytest

from refactor.learning.app.classroom import Classroom, TODAY_EVENT
from refactor.learning.app.bridge import BridgeError
from refactor.learning.connections.errors import ModelCancelled
from tests.refactor.concept114.test_concept114_classroom import concept_environment
from tests.refactor.conversation37.test_grounded_conversation37 import act, PLAN, DAY
from tests.refactor.speed116.test_classroom_speed116 import prepare, wait_jobs
from tests.refactor.recap130.support130 import environment130, last_subpoint130, close130


@pytest.mark.parametrize('next_concept',[True,False])
def test_recap130_same_action_card_credit_and_next_point(tmp_path,next_concept):
    e=environment130(tmp_path,next_concept=next_concept)
    try:
        last_subpoint130(e)
        count=len(e.gateway.calls);e.stream.clear()
        view=act(e,'next')
        assert not view['failure'] and len(e.gateway.calls)==count+1
        assert view['step']==('teaching' if next_concept else 'opening')
        assert next(p for p in view['points'] if p['current'])['id']=='p2'
        assert view['messages'][-2]['kind']=='recap' and view['messages'][-1]['role']=='assistant'
        assert json.loads(view['messages'][-2]['content'])==Classroom._recap_content(e.store.records('points',PLAN)[0])
        credits=e.store.records('credits',PLAN)
        assert len([row for row in credits if row['id'].startswith('subpoint:')])==3
        assert sum(row['credit']['minutes'] for row in credits)==15
        assert any(s.startswith(TODAY_EVENT) for s in e.stream)
    finally:close130(e)


def test_recap130_module_end_stops_after_card(tmp_path):
    e=concept_environment(tmp_path);e.room._materials=lambda:e.materials
    try:
        last_subpoint130(e);count=len(e.gateway.calls)
        assert e.room._predicted_teacher(e.room._course(PLAN),e.room._load(e.room._course(PLAN)),'next',()) is None
        view=act(e,'next')
        assert view['step']=='test_offer' and view['messages'][-1]['kind']=='recap'
        assert len(e.gateway.calls)==count
    finally:close130(e)


def test_recap130_target_filled_by_finishing_credit_stops_and_does_not_prefetch(tmp_path):
    e=environment130(tmp_path)
    try:
        last_subpoint130(e)
        course=e.room._course(PLAN);state=e.room._load(course)
        target=e.room._today(course,state)['today_target']
        e.store.add_credit(PLAN,'warmup','p1',max(1,target-15),DAY,'confirmed')
        assert e.room._predicted_teacher(course,state,'next',()) is None
        count=len(e.gateway.calls);view=act(e,'next')
        assert view['step']=='teach' and view['messages'][-1]['kind']=='recap'
        assert len(e.gateway.calls)==count and e.room._target_met(e.room._course(PLAN),e.room._load(course))
    finally:close130(e)


@pytest.mark.parametrize('action,args',[('next',()),('reply',('懂了',))])
@pytest.mark.parametrize('failure_kind',['connection','unexpected'])
def test_recap130_failure_retry_and_reopen_never_repeat_card_or_credit(tmp_path,action,args,failure_kind):
    e=environment130(tmp_path)
    try:
        last_subpoint130(e);original=e.gateway.complete
        if failure_kind=='connection':e.gateway.fail=True
        else:e.gateway.complete=lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('offline failure'))
        view=act(e,action,*args)
        assert view['failure'] and view['step']=='teach' and view['messages'][-1]['kind']=='recap'
        state=e.room._load(e.room._course(PLAN))
        assert state['index']==1 and state['failure']['action']=='next' and state['failure']['args']==[]
        credits=copy.deepcopy(e.store.records('credits',PLAN))
        e.gateway.fail=False;e.gateway.complete=original
        e.room=Classroom(e.services,e.room.study_day,now=e.room.now);e.room._materials=lambda:e.materials
        assert act(e,'enter')['step']=='teach'
        view=act(e,'retry')
        assert not view['failure'] and view['concept']['point_id']=='p2'
        assert len([m for m in view['messages'] if m['kind']=='recap'])==1
        assert e.store.records('credits',PLAN)==credits
    finally:close130(e)


@pytest.mark.parametrize('next_concept',[True,False])
@pytest.mark.parametrize('long_history',[False,True])
def test_recap130_prefetch_exact_request_and_hit_without_second_model_call(tmp_path,caplog,next_concept,long_history):
    e=environment130(tmp_path,next_concept=next_concept)
    try:
        last_subpoint130(e)
        if long_history:
            for i in range(43):e.store.append_message(PLAN,'m0','assistant',f'先前讲解 {i}。'*10)
        before=copy.deepcopy(e.store.pending_records(10000))
        assert prepare(e)['total']==2
        group=wait_jobs(e);job=group['jobs'][('next',())]
        assert job['tctx']['dialogue'][-1]['text'].startswith('要点卡：')
        assert e.store.pending_records(10000)==before
        count=len(e.gateway.calls)
        with caplog.at_level(logging.INFO,logger='refactor.learning.app.classroom'):view=act(e,'next')
        assert not view['failure'] and len(e.gateway.calls)==count
        assert any('classroom prefetch hit' in r.message for r in caplog.records)
        next_calls=[messages for purpose,messages,system in e.requests if
            json.loads(next(m['content'].split('\n',1)[1] for m in reversed(messages) if m['content'].startswith('【本步任务】')))['current_points'][0]['id']=='p2']
        assert len(next_calls)==1 # The other speculative option is “没太懂” at p0.
        (tmp_path/'prefetch-evidence130.json').write_text(json.dumps(dict(
            next_point_teacher_calls=len(next_calls),calls_on_click=len(e.gateway.calls)-count,
            log=[r.message for r in caplog.records if 'classroom prefetch hit' in r.message]),ensure_ascii=False),encoding='utf-8')
        assert job['cancel'].is_set() is False
        assert len([m for m in view['messages'] if m['kind']=='recap'])==1
    finally:close130(e)


@pytest.mark.parametrize('barrier',['not_ready','no_source'])
def test_recap130_prediction_declines_uncertain_path_and_actual_uses_normal_path(tmp_path,monkeypatch,barrier):
    e=environment130(tmp_path)
    try:
        last_subpoint130(e);course=e.room._course(PLAN);state=e.room._load(course)
        if barrier=='not_ready':
            monkeypatch.setattr(e.room,'_ready',lambda *args,**kw:False)
            waits=[]
            def wait(co,st,action,args=()):raise AssertionError('讲课不应等待题库')
            monkeypatch.setattr(e.room,'_wait',wait)
        else:
            course.point('p2')['chunk_ids']=[]
        if barrier=='not_ready':
            assert e.room._predicted_teacher(course,state,'next',()) is not None
            view=act(e,'next')
            assert not view['failure'] and view['step']=='teaching' and waits==[]
            assert view['messages'][-2]['kind']=='recap' and len(e.store.records('credits',PLAN))==3
        else:
            assert e.room._predicted_teacher(course,state,'next',()) is None
    finally:close130(e)


def test_recap130_cancelled_next_teacher_keeps_completed_point(tmp_path,monkeypatch):
    e=environment130(tmp_path)
    try:
        last_subpoint130(e)
        monkeypatch.setattr(e.gateway,'complete',lambda *a,**kw:(_ for _ in ()).throw(ModelCancelled()))
        with pytest.raises(BridgeError,match='已停止'):act(e,'next')
        view=e.room.run(PLAN,lambda *args:None)
        assert view['step']=='teach' and view['messages'][-1]['kind']=='recap'
        assert len([row for row in e.store.records('credits',PLAN) if row['id'].startswith('subpoint:')])==3
    finally:close130(e)
