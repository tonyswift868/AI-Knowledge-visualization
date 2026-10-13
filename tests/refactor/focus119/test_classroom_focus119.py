import json,threading
from types import SimpleNamespace
import pytest
from refactor.learning.app.classroom import Classroom
from refactor.learning.course import teacher_agent as agent
from refactor.learning.course.store import CourseStore
from tests.refactor.teacher_agent.test_class_teacher_agent import context
from tests.refactor.concept114.test_concept114_classroom import concept_environment
from tests.refactor.conversation37.test_grounded_conversation37 import act,PLAN,ACCOUNT,make_environment

def test_focus119_prompt_and_literal_highlights():
    ctx=context();system,messages=agent.teacher_request(ctx)
    assert '==重点句==' in system and '200～300' in system and '不截断' in system
    assert agent.reply_metrics('`==代码==` $==数学==$ ==**压力**与面积有关。==')['highlight_sentences']==1
    assert agent.reply_metrics('==第一句。第二句。==')['highlight_sentences']==2
    assert agent.wants_expansion('展开讲讲') and agent.wants_expansion('详细点')
    assert not agent.wants_expansion('不用详细讲')

@pytest.mark.parametrize('invalid',['没有荧光的一句话。','==一。== ==二。== ==三。== ==四。==','==为什么？=='])
def test_focus119_bad_highlights_rewrite_once_without_editing(tmp_path,invalid):
    env=make_environment(tmp_path)
    try:
        calls=[]
        def complete(*args,**kwargs):
            calls.append(args);kwargs['on_text'](invalid);return SimpleNamespace(text=invalid)
        env.gateway.complete=complete
        co=env.room._course(PLAN);st=env.room._load(co)
        result=env.room._teacher(co,st,None,'解释本点。',['p0'])
        assert len(calls)==2 and result.speech==invalid
        assert 'presentation' in calls[-1][1][-1]['content']
        assert not env.store.messages(PLAN,'m0')
    finally:env.room.cancel_materials();env.store.close()

def test_focus119_long_reply_kept_and_expansion_avoids_length_retry(tmp_path):
    env=make_environment(tmp_path);text='==压力相同时面积越小压强越大。=='+('对照压力与面积理解这个前提。'*35)
    try:
        calls=[]
        def complete(*args,**kwargs):calls.append(args);return SimpleNamespace(text=text)
        env.gateway.complete=complete;co=env.room._course(PLAN);st=env.room._load(co)
        assert env.room._teacher(co,st,None,'解释。',['p0']).speech==text and len(calls)==2
        calls.clear()
        assert env.room._teacher(co,st,None,'解释。',['p0'],user='展开讲讲').speech==text and len(calls)==1
    finally:env.room.cancel_materials();env.store.close()

def test_focus119_recap_complete_notes_reopen_and_sync(tmp_path,monkeypatch):
    env=concept_environment(tmp_path/'first');monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:env.materials))
    other=None
    try:
        act(env,'next');act(env,'next');act(env,'next');before=len(env.gateway.calls)
        view=act(env,'next');message=view['messages'][-1]
        assert message['kind']=='recap' and len(env.gateway.calls)==before
        assert message['recap']['title']=='面积与压强' and len(message['recap']['items'])==3
        for row,sub in zip(message['recap']['items'],env.store.records('points',PLAN)[0]['subpoints']):
            assert row['text'] in sub['notes'][0]['text'] and row['text'].endswith('。')
            assert row['chunk_id']==sub['notes'][0]['chunk_id']
        assert len(env.store.records('credits',PLAN))==1
        env.room=Classroom(env.services,env.room.study_day,now=env.room.now)
        assert act(env,'enter')['messages'][-1]['recap']==message['recap']
        other=CourseStore(ACCOUNT,tmp_path/'second')
        other.apply_remote([(record,i+1) for i,record in enumerate(env.store.pending_records(10000))])
        assert json.loads(next(m['content'] for m in other.messages(PLAN,'m0') if m['kind']=='recap'))==message['recap']
        assert '要点卡：' in env.room._dialogue(env.room._course(PLAN),env.room._load(env.room._course(PLAN)))[-1]['text']
    finally:
        env.room.cancel_materials();env.store.close()
        if other:other.close()

def test_focus119_recap_long_sentence_never_truncated_and_legacy_no_recap(tmp_path,monkeypatch):
    env=concept_environment(tmp_path/'concept');monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:env.materials))
    try:
        co=env.room._course(PLAN);st=env.room._load(co);point=dict(co.points[0])
        long='完整原文结论很长，'*20+'这是最后的完整句号。'
        point['subpoints']=[dict(title='长讲义',notes=[dict(text='**1. 数据库类型。**',chunk_id='c0'),dict(text=long,chunk_id='c0')])]
        env.room._recap(co,st,point)
        assert json.loads(env.store.messages(PLAN,'m0')[-1]['content'])['items'][0]['text']==long
    finally:env.room.cancel_materials();env.store.close()
    legacy=make_environment(tmp_path/'legacy');monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:legacy.materials))
    try:
        act(legacy,'next');act(legacy,'choose',0);view=act(legacy,'next')
        assert not any(m['kind']=='recap' for m in view['messages'])
    finally:legacy.room.cancel_materials();legacy.store.close()
