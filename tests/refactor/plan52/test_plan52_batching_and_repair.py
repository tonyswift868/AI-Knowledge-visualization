"""Batch 52: bounded module requests, host structure, explicit one-shot repair."""
import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from refactor.learning.course import planner_agents as agents, plan_intent
from refactor.learning.course.source_filter import recommendation_rejection
from refactor.learning.connections.errors import ModelError, MODEL_OUTPUT_TRUNCATED
from tests.refactor.plan51.test_plan51_selected_material_flow import flow, URL, QUOTE


def points_response(prompt, title='测试点'):
    text=prompt.split('[c1] ',1)[1].split('\n',1)[1].split('\n\n【本次生成约束】',1)[0]
    return json.dumps(dict(points=[dict(title=title,summary='理解资料',minutes=15,chunks=['c1'],
        citations=[dict(chunk_id='c1',quote=text[:80])])]),ensure_ascii=False)


def test_plan52_large_module_lossless_bounded_and_other_chapter_absent(flow,monkeypatch):
    f,ctx,pid,model=flow
    seen=[]
    def complete(purpose,messages,**kw):
        assert purpose=='generate' and kw['system']!=agents.MAP_SYSTEM
        prompt=messages[0]['content'];seen.append(prompt)
        assert '另外一个章节的正文' not in prompt
        return SimpleNamespace(text=points_response(prompt),search_hits=(),citations=())
    monkeypatch.setattr(model,'complete',complete)
    module=dict(id='m1',title='已确认模块',summary='理解资料',minutes=[15,15])
    draft=dict(f.load(pid),material_map=True,outline=dict(stages=[dict(title='路线',modules=[module])]))
    original=QUOTE*1100
    chunks=[dict(id='large',text=original,section=['当前章节'],source_title='教材')]
    points=f._generate_module_batches(draft,module,chunks,ctx)
    material=[p.split('[c1] ',1)[1].split('\n',1)[1].split('\n\n【本次生成约束】',1)[0] for p in seen]
    assert ''.join(material)==original
    assert len(seen)>2 and max(map(len,material))<=agents.DEFAULT_BATCH_CHARS
    assert len(points)==1 and points[0]['citations']


def test_plan52_truncated_existing_batch_is_split_and_citations_retained(flow,monkeypatch):
    f,ctx,pid,model=flow;seen=[]
    def complete(purpose,messages,**kw):
        text=messages[0]['content'].split('[c1] ',1)[1].split('\n',1)[1].split('\n\n【本次生成约束】',1)[0]
        seen.append(len(text))
        if len(text)>3000:raise ModelError(MODEL_OUTPUT_TRUNCATED,'模拟截断')
        return SimpleNamespace(text=points_response(messages[0]['content']))
    monkeypatch.setattr(model,'complete',complete)
    module=dict(id='m1',title='模块',summary='理解',minutes=[15,15])
    d=dict(f.load(pid),material_map=True,outline=dict(stages=[dict(title='路线',modules=[module])]))
    points=f._generate_module_batches(d,module,[dict(id='c',text=QUOTE*150)],ctx)
    assert len(seen)==4 and len(points)==1 and points[0]['citations']
    assert seen[0]==seen[1] and max(seen[2:])<seen[0]


def test_plan52_confirmed_structure_cannot_be_reordered_by_model(flow,monkeypatch):
    f,c,pid,model=flow
    f.run(pid,lambda d,c:f.chat(d,c,URL),c)
    f.run(pid,lambda d,c:f.make_outline(d,c),c)
    before=[m['title'] for m in f.load(pid)['material_structure']['modules']]
    result=f.run(pid,lambda d,c:f.resolve_missing(d,c,'continue'),c)['draft']
    assert result['phase']=='outline'
    assert [m['title'] for m in agents.outline_modules(result['outline'])]==before
    assert model.calls.count('generate')==3
    assert not any(r['system']==agents.MAP_SYSTEM for r in model.requests)


@pytest.mark.parametrize('bad',json.loads((Path(__file__).with_name('intent51_bad_responses.json')).read_text(encoding='utf-8'))+[
    '{"intents":[{"kind":"chat","params":{"reply":"好"}}],"reply":""}',
    '{"intents":[{"kind":"chat"}],"reply":"好"'])
def test_plan52_intent_real_bad_outputs_repaired_with_reason(flow,monkeypatch,bad):
    f,c,pid,model=flow;sent=[]
    def complete(purpose,messages,**kw):
        sent.append(copy.deepcopy(messages))
        return SimpleNamespace(text=bad if len(sent)==1 else '{"intents":[{"kind":"chat"}],"reply":"继续等你资料。"}')
    monkeypatch.setattr(model,'complete',complete)
    d=f.load(pid);context=f._intent_context(d)
    value,_=f._ask('plan_intent',plan_intent.intent_request(context,'稍等'),lambda text:plan_intent.parse_intents(text,context),c,plan_id=pid)
    assert value[0].kind=='chat' and len(sent)==2
    assert sent[1][-2]['content']==bad
    assert '格式校验失败' in sent[1][-1]['content'] and 'reply 必须在根对象' in sent[1][-1]['content']


def test_plan52_intent_two_bad_outputs_leave_domain_unchanged(flow,monkeypatch):
    f,c,pid,model=flow
    before=f.load(pid);domain=('phase','form','requirements','sources','material_structure','outline','recommendations')
    monkeypatch.setattr(model,'complete',lambda *a,**k:SimpleNamespace(text='{"intents":['))
    result=f.run(pid,lambda d,c:f.chat(d,c,'改成别的目标'),c)['draft']
    assert all(result.get(k)==before.get(k) for k in domain)
    assert result['messages'][-1]['text'].startswith('没听懂，换个说法。')


def test_plan52_truncation_does_not_consume_one_format_repair(flow,monkeypatch):
    f,c,pid,model=flow;sent=[]
    bad='{"points": [{"title":"因式分解","summary":"理解原文","minutes":15,"chunks":["c1"],"citations":[{"chunk_id":"c1","quote":"这段引用并不在原文中存在"}]}]}'
    def complete(purpose,messages,**kw):
        sent.append(copy.deepcopy(messages))
        if len(sent)==1:raise ModelError(MODEL_OUTPUT_TRUNCATED,'达到输出上限')
        return SimpleNamespace(text=bad if len(sent)==2 else points_response('[c1] 教材\n'+QUOTE))
    monkeypatch.setattr(model,'complete',complete)
    value,_=f._ask('generate',('填知识点',[dict(role='user',content='资料')]),
        lambda t:agents.parse_points(t,{'c1':'original'},min_points=1,quoted_chunks=[dict(id='original',text=QUOTE)]),
        c,plan_id=pid,max_tokens=8192)
    assert len(sent)==3 and value[0]['citations'][0]['quote']==QUOTE[:80]
    assert 'quote必须逐字存在于本批原文' in sent[2][-1]['content']


def test_plan52_combined_retries_still_stop_after_one_format_repair(flow,monkeypatch):
    f,c,pid,model=flow;count=0
    def complete(*a,**kw):
        nonlocal count
        count+=1
        if count==1:raise ModelError(MODEL_OUTPUT_TRUNCATED,'达到输出上限')
        return SimpleNamespace(text='{')
    monkeypatch.setattr(model,'complete',complete)
    with pytest.raises(ModelError,match='已重试一次'):
        f._ask('generate',('填点',[]),agents.parse_json,c,plan_id=pid,max_tokens=8192)
    assert count==3


@pytest.mark.parametrize('text,confirmed',[
    ('可以，就按这个来。让步段和结尾这两块多安排点练习就行。',True),
    ('让步段和结尾这两块多安排点练习就行。',False),
    ('不可以就按这个来。让步段和结尾这两块多安排点练习。',False)])
def test_plan52_live_explicit_confirmation_is_not_removed(flow,text,confirmed):
    f,c,pid,model=flow
    ctx=dict(f._intent_context(f.load(pid)),phase='preview')
    response=json.dumps(dict(intents=[dict(kind='supplement',instruction='让步段和结尾多安排练习'),
                                     dict(kind='proceed',step='sources')],reply=''),ensure_ascii=False)
    intents=plan_intent.parse_intents(response,ctx,user_text=text)
    assert any(i.kind=='proceed' for i in intents)==confirmed


@pytest.mark.parametrize('url',[
    'https://www.runoob.com/computer/computer-tutorial.html',
    'https://edu.gcfglobal.org/en/', 'https://example.org/', 'https://example.org/zh-cn/',
    'https://example.org/fr/', 'https://example.org/ja/',
    'https://youtube.com/watch?v=1','https://example.org/video/lesson',
    'https://example.org/signup/course','https://example.org/login','https://example.org/enroll/course',
    'https://edu.gcfglobal.org/en/gcflearnfreeorg/select-a-goal/1/'])
def test_plan52_recommendation_rejects_generic_video_registration(url):
    assert recommendation_rejection(url)


@pytest.mark.parametrize('url',[
    'https://docs.python.org/3/tutorial/index.html', 'https://example.org/books/algebra.pdf',
    'https://github.com/example/course', 'https://www.runoob.com/computer/computer.html'])
def test_plan52_recommendation_keeps_complete_material(url):
    assert not recommendation_rejection(url)


def test_plan52_recommendation_checks_final_redirect_without_extract(flow,monkeypatch):
    f,c,pid,_=flow
    def reply(req):
        return (httpx.Response(302,headers={'location':'https://example.org/signup/course'})
            if req.url.path=='/book' else httpx.Response(200,text='registration'))
    monkeypatch.setattr('refactor.learning.course.research.ResearchService._client',lambda _:httpx.Client(transport=httpx.MockTransport(reply)))
    assert not f._link_available('https://example.org/book',c.cancel)[0]
    assert not f.services.store.records('source_chunks',pid)


def test_plan52_batch_quote_cannot_be_in_a_different_fragment():
    chunks=[dict(id='original',text=QUOTE)]
    response=points_response('[c1] 教材\n'+QUOTE)
    result=agents.parse_points(response,{'c1':'original'},min_points=1,quoted_chunks=chunks)
    assert result[0]['minutes']==15 and result[0]['citations'][0]['chunk_id']=='original'
    with pytest.raises(agents.AgentFormatError,match='逐字'):
        agents.parse_points(response,{'c1':'original'},min_points=1,quoted_chunks=[dict(id='original',text='另一段原文')])
