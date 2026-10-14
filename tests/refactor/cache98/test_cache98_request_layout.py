"""Cache layout changes must preserve grounding, scope and original passages."""
from copy import deepcopy
from datetime import date
import json
import re
from types import SimpleNamespace

import pytest

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course import plan_intent as intent, planner_agents as agents


def context():
    return dict(phase='material_preview', today='2026-10-10',
        form=dict(goal='学习', start='2026-10-10', end='2026-10-17', daily_cap=30,
                  notes='只学概念', abandoned_topics=['安装']),
        sections=[dict(id='s1.1', title='网络', source='教程', chars=100)],
        modules=[dict(n=1, title='网络', summary='解释概念', chapters=['网络'])],
        cards=[dict(n=1, title='教程')], facts=dict(scope={'include':['s1.1']}),
        recent=[dict(role='user', text='保留网络')])


def test_cache98_mutable_form_no_longer_breaks_catalog_prefix_and_values_survive():
    ctx=context(); frozen=deepcopy(ctx)
    system, messages=intent.intent_request(ctx,'每天改成40分钟')
    text=messages[0]['content']; payload=json.loads(text.split('\n',1)[1])
    assert payload==dict(ctx=ctx,text='每天改成40分钟') and ctx==frozen
    changed=deepcopy(ctx);changed['form']['daily_cap']=40
    after=intent.intent_request(changed,'只保留第一模块')[1][0]['content']
    prefix=text[:text.index('"form":')]
    assert after.startswith(prefix)
    assert all(text.index('"'+key+'":')<text.index('"form":') for key in ('sections','modules','cards'))
    assert system==intent.INTENT_SYSTEM
    reverse={k:dict(reversed(list(v.items()))) if isinstance(v,dict) else v for k,v in reversed(list(ctx.items()))}
    assert intent.intent_request(reverse,'每天改成40分钟')==(system,messages)
    for raw in ({'kind':'select_modules','include':[1],'exclude':[]},
                {'kind':'limit_scope','include':['s1.1'],'exclude':[]}):
        envelope=json.dumps(dict(intents=[raw],reply=''))
        assert intent.parse_intents(envelope,payload['ctx'])==intent.parse_intents(envelope,ctx)
    with pytest.raises(agents.AgentFormatError):
        intent.parse_intents(json.dumps(dict(intents=[dict(kind='limit_scope',include=['unknown'],exclude=[])],reply='')),ctx)


def test_cache98_host_projects_stage_without_losing_current_ids_or_constraints():
    flow=object.__new__(PlanFlow);flow.study_day=lambda:date(2026,10,10)
    section=SimpleNamespace(id='s1.1',source_title='教程',title='网络',chars=100)
    flow._sections=lambda draft:([],[section])
    module=dict(key='m1',title='网络',summary='解释概念',chapters=['网络'],refs=['s1.1'])
    draft=dict(phase='material_preview',form=context()['form'],messages=context()['recent'],
        sources=[dict(card_id='source1',title='教程',status='adopted')],scope={'include':['s1.1']},
        preview={'old':'旧预览'},estimate={'old':'旧预估'},
        material_structure=dict(modules=[module],missing=['安全'],removed_content=['安装']))
    frozen=deepcopy(draft);ctx=flow._intent_context(draft)
    assert draft==frozen and ctx['form']==draft['form'] and ctx['recent']==draft['messages']
    assert ctx['modules']==[dict(n=1,title='网络',summary='解释概念',chapters=['网络'])]
    assert ctx['sections'][0]['id']=='s1.1' and ctx['cards'][0]['n']==1
    assert ctx['facts']==dict(scope=draft['scope'], preview=draft['preview'],
                             material_structure=dict(missing=['安全'],removed_content=['安装']))
    draft['phase']='preview';ctx=flow._intent_context(draft)
    assert ctx['sections']==ctx['modules']==ctx['cards']==[]
    assert ctx['facts']==dict(preview=draft['preview'])


def generate(chunks, material_map=True):
    form=dict(goal='学习网络',final_goal='解释网络',level='zero',notes='生活化例子',
              start='2026-10-10',end='2026-10-17',daily_cap=30,weekday_weights=[1]*7,
              special_days=[],abandoned_topics=['安装'])
    module=dict(title='网络',summary='解释概念',minutes=[10,30])
    outline=dict(stages=[dict(title='基础',modules=[module])])
    return agents.generate_request(form,form['goal'],outline,module,chunks,
        max_points=3,material_map=material_map,batch_index=1,batch_count=2,
        removed_content=[dict(kind='chapter',title='安装')])


def catalog(message):
    return json.loads(message.split('\n',2)[1])


@pytest.mark.parametrize('material_map',[False,True])
def test_cache98_source_aliases_preserve_every_body_locator_and_citation(material_map):
    title='很长的原资料名“网络教程”'+('与出处约束'*12)
    chunks=[dict(id='original-1',source_id='book-a',source_title=title,section=['网络','IP'],
                 text='IP 地址是设备在网络中的地址。'),
            dict(id='original-2',source_id='book-a',source_title=title,section=['网络','TCP'],
                 text='TCP 提供可靠的传输。')]
    frozen=deepcopy(chunks);system,messages,aliases=generate(chunks,material_map)
    text=messages[0]['content'];names=catalog(text)
    assert list(names.values())==[title] and text.count(title)==1 and chunks==frozen
    assert list(aliases)==['c1','c2']
    assert len(set(re.findall(r'\[c\d+\] (S[0-9a-f]+)',text)))==1
    for c in chunks: assert c['text'] in text and ' / '.join(c['section']) in text
    for term in ('生活化例子','安装','最多3个知识点','第1/2批资料'):assert term in text
    rows=[dict(title='IP 地址',summary='解释网络地址',minutes=15,chunks=['c1']),
          dict(title='TCP',summary='解释可靠传输',minutes=15,chunks=['c2'])]
    parsed=agents.parse_points(json.dumps(dict(points=rows)),aliases,
        paragraph_chunks=chunks if material_map else None)
    assert [p['chunk_ids'] for p in parsed]==[['original-1'],['original-2']]
    if material_map:
        assert [p['citations'][0]['quote'] for p in parsed]==[c['text'] for c in chunks]
    rows[0]['chunks']=[next(iter(names))]
    with pytest.raises(agents.AgentFormatError):agents.parse_points(json.dumps(dict(points=rows)),aliases)


def test_cache98_source_numbers_stay_fixed_across_subsets_and_reordering():
    chunks=[dict(id='a',source_id='A',source_title='同名教程',text='原文A'),
            dict(id='b',source_id='B',source_title='同名教程',text='原文B')]
    whole=catalog(generate(chunks)[1][0]['content'])
    assert len(whole)==2 and whole==catalog(generate(chunks[::-1])[1][0]['content'])
    one=catalog(generate(chunks[1:])[1][0]['content'])
    assert len(one)==1 and set(one)<=set(whole)
