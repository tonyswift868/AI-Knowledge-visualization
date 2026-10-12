"""Batch 81: one teacher turn for normal text, explicit questions never score."""
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from refactor.learning.app import handlers
from refactor.learning.app.classroom import Classroom, _Course
from tests.refactor.conversation37.test_grounded_conversation37 import make_environment, PLAN, pending


@pytest.mark.parametrize('method', ['classroom.reply', 'classroom.ask'])
@pytest.mark.parametrize('text', ['面积越小压强越大。', '硬度相同吗？', '刚才说面积越大是口误，我想说越小。'])
def test_classroom81_formal_single_teacher_call_no_grading(tmp_path, monkeypatch, method, text):
    e=make_environment(tmp_path)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    class Bridge:
        def __init__(self):self.actions={}
        def register(self,name,fn,**kwargs):self.actions[name]=fn
        def push(self,*args):pass
    bridge=Bridge();handlers.register(bridge,SimpleNamespace(services=e.services))
    ctx=SimpleNamespace(cancel=threading.Event(),stream=lambda _:None)
    def call(name,**params):
        v=bridge.actions[name](dict(plan_id=PLAN,**params),ctx)
        assert not v.get('failure'),v
        return v
    try:
        call('classroom.enter');call('classroom.next');before=call('classroom.choose',option=0)
        calls=len(e.gateway.calls);seen=[];complete=e.gateway.complete
        def tracked(purpose,messages,**kwargs):
            seen.append((messages,kwargs));return complete(purpose,messages,**kwargs)
        e.gateway.complete=tracked
        v=call(method,text=text)
        assert len(e.gateway.calls)==calls+1 and e.gateway.calls[-1]=='teacher'
        assert not e.store.records('judgements',PLAN) and not e.store.records('question_answers',PLAN)
        assert v['step']==before['step'] and v['points']==before['points'] and not v['question']
        task=json.loads(seen[0][0][-1]['content'].split('\n',1)[1])
        assert '自行辨别' in task['director'] and task['user']==text
        assert '只有一个输入框' in seen[0][1]['system']
    finally:e.store.close()


def test_classroom81_pending_question_ask_preserves_answer_and_reply_grades(tmp_path,monkeypatch):
    e=make_environment(tmp_path)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    try:
        before=pending(e)
        original=copy.deepcopy(e.room._load(e.room._course(PLAN))['pending'])
        requests=[];complete=e.gateway.complete
        def tracked(purpose,messages,**kwargs):
            requests.append(json.loads(messages[-1]['content'].split('\n',1)[1]))
            return complete(purpose,messages,**kwargs)
        e.gateway.complete=tracked
        v=e.room.perform(PLAN,'ask',SimpleNamespace(cancel=threading.Event(),stream=lambda _:None),['题目说的面积是什么意思？'])
        assert not v['failure'] and v['question']==before['question']
        assert e.room._load(e.room._course(PLAN))['pending']==original
        assert not e.store.records('judgements',PLAN)
        assert e.gateway.calls==['teacher']
        assert '当前屏幕上正在检测' in requests[0]['director']
        assert '先写自己的回答' in requests[0]['director'] and '不会跳过检测题' in requests[0]['director']
        v=e.room.perform(PLAN,'reply',None,['A'])
        assert not v['failure'] and len(e.store.records('question_answers',PLAN))==1
        assert e.gateway.calls==['teacher']
    finally:e.store.close()


def test_classroom81_readiness_reads_bank_once_and_skips_unneeded_flow(tmp_path,monkeypatch):
    e=make_environment(tmp_path)
    try:
        course=_Course(e.store,e.store.get_plan(PLAN));state=e.room._load(course)
        records=e.store.records;reads=[]
        def tracked(entity,*args,**kwargs):
            reads.append(entity);return records(entity,*args,**kwargs)
        e.store.records=tracked
        monkeypatch.setattr(e.room,'_preparation_flow',lambda:pytest.fail('Ready modules must not load preparation flow'))
        e.room._prepare_next_module(course,state,entering=True)
        assert reads.count('question_bank')==1
        assert course.points_of('m0')==[course.point('p0')]
        reads.clear();e.room._prepare_next_module(course,state)
        assert reads.count('question_bank')==1 # next action reads fresh bank
    finally:e.store.close()
