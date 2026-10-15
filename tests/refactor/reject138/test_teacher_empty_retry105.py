"""BUG-105: a thinking-only (empty) teacher reply is asked once more, quietly."""
import contextlib
from types import SimpleNamespace
import pytest
from refactor.learning.app.classroom import Classroom
from refactor.learning.connections.errors import ModelError
from refactor.learning.course import teacher_agent as agent
from tests.refactor.teacher_agent.test_class_teacher_agent import context

GOOD='==单位面积上的压力叫压强。==你会怎样比较面积？'


def empty():
    error=ModelError('MODEL_RESPONSE_INVALID','模型回复格式无效。')
    error.diagnostics={'reason':'empty_text_or_invalid_model'}
    return error


def room_with(outcomes):
    calls=[];deferred=[]
    def complete(*args,**kw):
        calls.append(kw.get('plan_id'))
        outcome=outcomes.pop(0)
        if isinstance(outcome,Exception):raise outcome
        kw['on_text'](outcome);return SimpleNamespace(text=outcome)
    @contextlib.contextmanager
    def defer_errors():
        deferred.append(True);yield
    gateway=SimpleNamespace(complete=complete,defer_errors=defer_errors)
    return Classroom(SimpleNamespace(gateway=gateway),lambda:None),calls,deferred


def ctx():
    value=context()
    value['concept']=dict(notes=[dict(text='单位面积上的压力叫压强。',chunk_id='chunk-a')],first_turn=False,recent_styles=[])
    return value


@pytest.mark.parametrize('prefetched',[False,True])
def test_empty105_thinking_only_reply_is_asked_again(prefetched):
    room,calls,deferred=room_with([empty(),GOOD])
    c=ctx();emitted=[]
    teaching,_,speech=room._generate_teacher('plan',c,agent.teacher_request(c),None,emitted.append,prefetched=prefetched)
    assert len(calls)==2 and deferred==[True]
    assert '压强' in speech


def test_empty105_twice_empty_still_reports():
    room,calls,_=room_with([empty(),empty()])
    c=ctx()
    with pytest.raises(ModelError) as caught:
        room._generate_teacher('plan',c,agent.teacher_request(c),None,lambda _:None)
    assert caught.value.code=='MODEL_RESPONSE_INVALID' and len(calls)==2


def test_empty105_other_errors_are_not_retried():
    busy=ModelError('MODEL_FIRST_EVENT_TIMEOUT','模型暂时无法使用，请稍后再试。')
    room,calls,_=room_with([busy,GOOD])
    c=ctx()
    with pytest.raises(ModelError) as caught:
        room._generate_teacher('plan',c,agent.teacher_request(c),None,lambda _:None)
    assert caught.value.code=='MODEL_FIRST_EVENT_TIMEOUT' and len(calls)==1
