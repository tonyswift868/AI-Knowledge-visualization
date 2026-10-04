"""Numbered source evidence: the model cannot author or edit original text."""
import json
from pathlib import Path
from types import SimpleNamespace

from refactor.learning.course import planner_agents as agents
from tests.refactor.plan51.test_plan51_selected_material_flow import flow, URL, QUOTE


def chunk():
    return dict(id='stored', text='目录标题\n\n第一段正文说明队列按先进先出的次序处理元素。\n\n第二段正文说明栈按后进先出的次序处理元素。',
        block_fragments=[dict(kind='heading', text='目录标题'),
            dict(kind='paragraph', text='第一段正文说明队列按先进先出的次序处理元素。'),
            dict(kind='paragraph', text='第二段正文说明栈按后进先出的次序处理元素。')])


def parse(value, chunks):
    paragraphs=agents.material_paragraphs(chunks)
    aliases={f'c{i}': p['id'] for i,p in enumerate(paragraphs,1)}
    return agents.parse_points(json.dumps(dict(points=value),ensure_ascii=False), aliases,
        min_points=1,paragraph_chunks=chunks)


def point(refs, title='次序'):
    return dict(title=title,summary='学习次序',minutes=15,chunks=refs)


def test_plan54_number_only_resolves_exact_paragraph_and_ignores_forged_quote():
    p=point(['c2']);p['citations']=[dict(quote='模型伪造的新原文')]
    result=parse([p],[chunk()])[0]
    assert result['chunk_ids']==['stored']
    cite=result['citations'][0]
    assert cite['quote']=='第一段正文说明队列按先进先出的次序处理元素。'
    assert agents.resolve_paragraph(chunk(),cite)==cite['quote']


def test_plan54_out_of_batch_or_heading_drops_only_that_point():
    result=parse([point(['c99'],'越界'),point(['c1'],'标题'),point(['c2','c3'],'正文')],[chunk()])
    assert [p['title'] for p in result]==['正文']
    assert len(result[0]['citations'])==2


def test_plan54_all_bad_requests_existing_single_repair(flow,monkeypatch):
    f,ctx,pid,model=flow
    calls=[]
    def reply(purpose,messages,**kwargs):
        calls.append(messages)
        return SimpleNamespace(text=json.dumps(dict(points=[point(['c99'] if len(calls)==1 else ['c2'])])))
    monkeypatch.setattr(model,'complete',reply)
    result,_=f._ask('generate',('规则',[]),lambda s:parse(json.loads(s)['points'],[chunk()]),ctx,plan_id=pid)
    assert len(calls)==2 and result[0]['citations']
    assert '本批正文段落编号' in calls[-1][-1]['content']


def test_plan54_math53_reply_references_only_numbers():
    data=json.loads(Path(__file__).with_name('math53_numbered.json').read_text(encoding='utf-8'))
    # Keep the captured knowledge-point wording, replace copied quotations with
    # the actual supplied paragraph aliases. No whitespace repair is involved.
    originals=data['chunks']
    paragraphs=agents.material_paragraphs(originals)
    aliases={f'c{i}':p['id'] for i,p in enumerate(paragraphs,1)}
    value=json.loads(data['response'])
    for p in value['points']:
        p.pop('citations',None)
        p['chunks']=[next(a for a,pid in aliases.items() if
            next(row for row in paragraphs if row['id']==pid)['parent_chunk_id']==p['chunks'][0])]
    result=agents.parse_points(json.dumps(value),aliases,min_points=1,paragraph_chunks=originals)
    assert len(result)==len(value['points'])
    assert all(agents.resolve_paragraph(next(c for c in originals if c['id']==cite['chunk_id']),cite)==cite['quote']
        for p in result for cite in p['citations'])


def test_plan54_split_offsets_resolve_only_sent_fragment():
    source=dict(id='big',text='正文内容。'*30)
    rows=agents.material_paragraphs([source])
    batches=agents.split_module_chunks(rows,max_chars=35)
    for batch in batches:
        normalized=agents.material_paragraphs(batch)
        result=parse([point(['c1'])],normalized)[0]
        cite=result['citations'][0]
        assert cite['quote']==batch[0]['text']
        assert agents.resolve_paragraph(source,cite)==batch[0]['text']


def test_plan54_nested_split_keeps_original_coordinates():
    source=dict(id='big',text='第一段资料。'*40)
    outer=agents.split_module_chunks(agents.material_paragraphs([source]),max_chars=80)[1]
    inner=agents.split_module_chunks(agents.material_paragraphs(outer),max_chars=30)
    for batch in inner:
        cite=parse([point(['c1'])],batch)[0]['citations'][0]
        assert cite['paragraph_start']>=80
        assert agents.resolve_paragraph(source,cite)==batch[0]['text']


def test_plan54_short_body_is_not_inferred_to_be_a_heading():
    source=dict(id='short',text='原文说明',block_fragments=[dict(kind='paragraph',text='原文说明')])
    assert parse([point(['c1'])],[source])[0]['citations'][0]['quote']=='原文说明'


def test_plan54_only_heading_batch_is_rejected():
    import pytest
    with pytest.raises(agents.AgentFormatError,match='本批正文段落编号'):
        parse([point(['c1'])],[chunk()])


def test_plan54_heading_with_valid_body_keeps_only_body_evidence():
    assert len(parse([point(['c1','c2'])],[chunk()])[0]['citations'])==1


def test_plan54_real_flow_stores_program_text_without_model_quotes(flow,monkeypatch):
    f,ctx,pid,model=flow
    complete=model.complete
    def reply(purpose,messages,**kwargs):
        result=complete(purpose,messages,**kwargs)
        if purpose=='generate':
            assert '不抄原句' in kwargs['system']
            value=json.loads(result.text)
            for p in value['points']:p.pop('citations',None)
            return SimpleNamespace(text=json.dumps(value))
        return result
    monkeypatch.setattr(model,'complete',reply)
    f.run(pid,lambda d,c:f.chat(d,c,URL),ctx)
    f.run(pid,lambda d,c:f.make_outline(d,c),ctx)
    draft=f.run(pid,lambda d,c:f.resolve_missing(d,c,'continue'),ctx)['draft']
    assert draft['phase']=='outline'
    citations=[c for s in draft['outline']['stages'] for m in s['modules'] for p in m['points'] for c in p['citations']]
    assert citations and all('paragraph_id' in c and c['quote'] for c in citations)
    f.run(pid,lambda d,c:f.estimate(d),ctx)
    assert f.run(pid,lambda d,c:f.generate(d,c),ctx)['draft']['phase']=='ready'
