"""Batch 53 regressions captured from batch 52, without credentials or model calls."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.learning.course import planner_agents as agents
from refactor.learning.course.question_bank import contains_original
from refactor.learning.connections.errors import ModelError
from tests.refactor.plan51.test_plan51_selected_material_flow import flow, URL, QUOTE


def fixture(name):
    return json.loads(Path(__file__).with_name(name).read_text(encoding='utf-8'))


def test_plan53_math52_raw_response_whitespace_accepted():
    data = fixture('math52_response.json')
    points = agents.parse_points(data['response'], {c['id']: c['id'] for c in data['chunks']},
                                 min_points=1, quoted_chunks=data['chunks'])
    assert len(points) == len(json.loads(data['response'])['points'])
    assert all(contains_original(c['quote'], next(x['text'] for x in data['chunks'] if x['id']==c['chunk_id']))
               for p in points for c in p['citations'])


def test_plan53_history52_title_citation_drops_point():
    data = fixture('history52_heading.json')
    good = dict(title='理解遗产', summary='理解历史遗产', minutes=15, chunks=['c1'],
                citations=[data['point']['citations'][0]])
    points = agents.parse_points(json.dumps(dict(points=[data['point'], good]), ensure_ascii=False),
        {c['id']:c['id'] for c in data['chunks']}, min_points=1, quoted_chunks=data['chunks'])
    assert [p['title'] for p in points] == ['理解遗产']


@pytest.mark.parametrize('reverse', [False, True])
def test_plan53_material_confirmation52_amends_then_fills_once(flow, monkeypatch, reverse):
    f,c,pid,model = flow
    f.run(pid, lambda d,c:f.chat(d,c,URL),c)
    f.run(pid, lambda d,c:f.make_outline(d,c),c)
    captured = fixture('mixed52_intents.json')
    value = json.loads(captured['response'])
    if reverse: value['intents'].reverse()
    complete = model.complete
    requests = []
    def reply(purpose, messages, **kwargs):
        requests.append(purpose)
        if purpose=='plan_intent': return SimpleNamespace(text=json.dumps(value,ensure_ascii=False))
        if purpose=='outline':
            packet=json.loads(messages[-1]['content'])
            assert '让步段和结尾' in packet['form']['notes']
            assert '让步段和结尾' in packet['instruction']
        return complete(purpose,messages,**kwargs)
    monkeypatch.setattr(model,'complete',reply)
    result=f.run(pid, lambda d,c:f.chat(d,c,captured['user_text']), c)['draft']
    assert result['phase']=='outline'
    assert requests==['plan_intent','outline','generate','generate','generate']
    assert len(result['requirements'])==1
    assert result['material_structure']['version']==2
    previews=[m for m in result['messages'] if m['kind']=='material_preview']
    assert previews[-1]['version']==2 and '请确认' not in previews[-1]['text']


@pytest.mark.parametrize('quote', ['队列按后进先出的顺序处理元素。', '共有13个元素依次进入队列。'])
def test_plan53_changed_word_or_digit_still_rejected(quote):
    text='队列按先进先出的顺序处理元素。共有12个元素依次进入队列。'
    value=dict(points=[dict(title='次序', summary='理解顺序', minutes=15,chunks=['c1'],
                           citations=[dict(chunk_id='c1',quote=quote)])])
    with pytest.raises(agents.AgentFormatError, match='不能改写'):
        agents.parse_points(json.dumps(value,ensure_ascii=False), {'c1':'c'}, min_points=1,
                            quoted_chunks=[dict(id='c',text=text)])


def test_plan53_all_heading_batch_repaired_once_with_body_reason(flow,monkeypatch):
    f,c,pid,model=flow
    heading='The Silk Roads Map'
    chunks=[dict(id='c',text=heading+'\n\n'+QUOTE,section=[heading])]
    bad=dict(points=[dict(title='读地图',summary='看路线',minutes=15,chunks=['c1'],
                         citations=[dict(chunk_id='c1',quote=heading)])])
    good=copy.deepcopy(bad);good['points'][0]['citations'][0]['quote']=QUOTE
    sent=[]
    def reply(purpose,messages,**kwargs):
        sent.append(copy.deepcopy(messages))
        return SimpleNamespace(text=json.dumps(bad if len(sent)==1 else good,ensure_ascii=False))
    monkeypatch.setattr(model,'complete',reply)
    points,_=f._ask('generate',('原文',[dict(role='user',content=chunks[0]['text'])]),
        lambda t:agents.parse_points(t,{'c1':'c'},min_points=1,quoted_chunks=chunks), c,plan_id=pid)
    assert len(sent)==2 and '出处要引用正文句子' in sent[1][-1]['content']
    assert points[0]['citations'][0]['quote']==QUOTE
    monkeypatch.setattr(model,'complete',lambda *a,**k:SimpleNamespace(text=json.dumps(bad)))
    with pytest.raises(ModelError,match='已重试一次'):
        f._ask('generate',('原文',[]),lambda t:agents.parse_points(t,{'c1':'c'},min_points=1,
               quoted_chunks=chunks),c,plan_id=pid)


def test_plan53_heading_plus_body_quote_is_not_heading_only():
    heading='正文标题与父目录不相同的章节'
    text=heading+'\n\n'+QUOTE*5
    chunk=dict(id='c',text=text,section=['父目录'],block_fragments=[dict(kind='heading',text=heading)])
    value=dict(points=[dict(title='正文依据',summary='理解正文',minutes=15,chunks=['c1'],
        citations=[dict(chunk_id='c1',quote=text[:80])])])
    points=agents.parse_points(json.dumps(value,ensure_ascii=False),{'c1':'c'},min_points=1,quoted_chunks=[chunk])
    assert points[0]['citations'][0]['quote']==text[:80]
    value['points'][0]['citations'][0]['quote']=heading
    with pytest.raises(agents.AgentFormatError,match='出处要引用正文句子'):
        agents.parse_points(json.dumps(value,ensure_ascii=False),{'c1':'c'},min_points=1,quoted_chunks=[chunk])


def test_plan53_final_map_and_generation_use_same_normalized_quotes(flow,monkeypatch):
    f,c,pid,model=flow
    complete=model.complete
    def reply(purpose,messages,**kwargs):
        result=complete(purpose,messages,**kwargs)
        if purpose=='generate':
            value=json.loads(result.text)
            for p in value['points']:
                for cite in p['citations']: cite['quote']=' '.join(cite['quote'])
            return SimpleNamespace(text=json.dumps(value,ensure_ascii=False))
        return result
    monkeypatch.setattr(model,'complete',reply)
    f.run(pid,lambda d,c:f.chat(d,c,URL),c)
    f.run(pid,lambda d,c:f.make_outline(d,c),c)
    result=f.run(pid,lambda d,c:f.resolve_missing(d,c,'continue'),c)['draft']
    assert result['phase']=='outline'
    f.run(pid,lambda d,c:f.estimate(d),c)
    result=f.run(pid,lambda d,c:f.generate(d,c),c)['draft']
    assert result['phase']=='ready'
