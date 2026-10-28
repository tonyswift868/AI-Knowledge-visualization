"""Exercise the installed handlers registration, not a simulator-only date seam."""
import json
import threading
from types import SimpleNamespace

import pytest

from refactor.learning.app import handlers
from refactor.learning.app.classroom import Classroom
from refactor.learning.app.classroom import _Course
from refactor.learning.course import teacher_agent
from tests.refactor.teacher_agent.test_class_teacher_agent import context
from tests.refactor.conversation37.test_grounded_conversation37 import make_environment, PLAN


@pytest.mark.parametrize('tail', ['', '\n【选项】\n一个\n【/选项】', '\n【选项】\n未闭合'])
def test_classroom77_missing_or_malformed_options_preserve_speech(tail):
    result=teacher_agent.parse_teaching('先一起认识感知的作用。[c1]'+tail,context(opening=True))
    assert result.speech=='先一起认识感知的作用。[c1]'
    assert result.options==('直接开始讲','我先说说我知道的')


def test_classroom77_opening_requirement_is_last_task_field():
    _,messages=teacher_agent.teacher_request(context(opening=True))
    task=json.loads(messages[-1]['content'].split('\n',1)[1])
    assert list(task)[-1]=='output_format'
    assert '【选项】' in task['output_format'] and '【/选项】' in task['output_format']


@pytest.mark.parametrize('tail',['【随口问】模型现场题【/随口问】','【选项】多余选项【/选项】','【随口问'])
def test_classroom77_formatting_does_not_discard_explanation(tail):
    result=teacher_agent.parse_teaching('感知接收信息。[c1]'+tail,context())
    assert result.speech=='感知接收信息。[c1]' and result.options==()


def test_classroom77_formal_handlers_date_opening_and_reopen(tmp_path,monkeypatch):
    e=make_environment(tmp_path)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    # Ready-account recovery is covered by plan187; keep this direct handler
    # exercise deterministic instead of racing its seeded preparation job.
    from refactor.learning.app.plan_flow import PlanFlow
    monkeypatch.setattr(PlanFlow, 'resume_started_preparations', lambda self: 0)
    class Signal:
        def __init__(self): self.callbacks=[]
        def connect(self, callback): self.callbacks.append(callback)
        def emit(self, value=None):
            for callback in tuple(self.callbacks): callback(value)
    class Bridge:
        def __init__(self):self.actions={};self.event=Signal();self.closing=Signal()
        def register(self,name,fn,**kw):self.actions[name]=fn
        def push(self,*args):pass
    bridge=Bridge();handlers.register(bridge,SimpleNamespace(services=e.services))
    ctx=SimpleNamespace(cancel=threading.Event(),stream=lambda text:None)
    def call(name,**params):
        value=bridge.actions[name](dict(plan_id=PLAN,**params),ctx)
        assert not value.get('failure'),value
        return value
    try:
        v=call('classroom.enter')
        assert v['today']['study_day']==handlers._local_study_day(e.services.settings.load()).isoformat()
        e.gateway.replies=['==压力相同时，面积越小压强越大。==我们先从面积与压强的区别开始。']
        v=call('classroom.next')
        assert v['step']=='opening'
        assert v['conversation']['options']==['直接开始讲','我先说说我知道的']
        assert len(e.gateway.calls)==1
        assert any('我们先从面积与压强' in m['content'] for m in v['messages'])
        call('classroom.leave')
        reopened=Bridge();handlers.register(reopened,SimpleNamespace(services=e.services))
        v=reopened.actions['classroom.enter'](dict(plan_id=PLAN),ctx)
        assert not v['failure'] and v['step']=='opening'
        assert v['conversation']['options']==['直接开始讲','我先说说我知道的']
    finally:e.store.close()


def test_classroom77_interruption_uses_bounded_related_sources():
    rows={'focus':{'text':'本点原文。'},'rag':{'text':'RAG 检索资料。'*1000}}
    rows.update({f'other{i}':{'text':'无关原文。'*1000} for i in range(150)})
    chunks={cid:dict(source_id='adopted',**row) for cid,row in rows.items()}
    course=SimpleNamespace(points=[{'id':'p'}],_source_rows=lambda:(chunks,{'adopted':{'adopted':True}}))
    result=Classroom._related_passages(course,{'focus':'本点原文。'},'RAG 如何检索资料？')
    assert result['focus']=='本点原文。' and 'rag' in result
    assert sum(len(t) for cid,t in result.items() if cid!='focus')<=16000
    assert len(result)<=13


def test_classroom77_source_cards_read_once_per_operation_and_refresh_next(tmp_path):
    e=make_environment(tmp_path)
    try:
        counts=[];records=e.store.records
        def tracked(entity,*args,**kwargs):
            counts.append(entity);return records(entity,*args,**kwargs)
        e.store.records=tracked
        course=_Course(e.store,e.store.get_plan(PLAN))
        for _ in range(20):
            assert course.passages(['p0'])
            assert course.passage_card('c0')['text']
        assert counts.count('source_chunks')==1 and counts.count('sources')==1
        fresh=_Course(e.store,e.store.get_plan(PLAN));fresh.passage_card('c0')
        assert counts.count('source_chunks')==2 and counts.count('sources')==2
    finally:e.store.close()
