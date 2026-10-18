"""Local map deletions: real persisted draft, explicit confirmation, no refill."""
import copy
import json
from types import SimpleNamespace

import pytest

from refactor.learning.course import plan_intent, planner_agents as agents
from tests.refactor.plan37.test_grounded_plan37_flow import fixture, adopt_and_map


def three(fixture):
    flow, model, ctx, *_ = fixture
    d = adopt_and_map(fixture)
    seed = d['outline']['stages'][0]['modules'][0]
    modules = []
    for i,title in enumerate(['资料导航与学习路线', 'Java 基础', '面试计划、项目表达与求职准备'], 1):
        m = copy.deepcopy(seed)
        m.update(key=f'm{i}', title=title, prerequisites=[])
        m['points'] = [dict(copy.deepcopy(seed['points'][0]), title=f'{title}点{j}') for j in [1,2]]
        modules.append(m)
    d['outline']['stages'][0]['modules'] = modules
    d['material_structure']['modules'] = [dict(key=m['key'],title=m['title'],summary=m['summary'],
        refs=m['refs'],chapters=['共用章节']) for m in modules]
    flow.save(d)
    return flow,model,ctx,d


@pytest.mark.parametrize('text,selected', [('删掉导航和求职两个模块',[1,3]),('删掉第1和第3模块',[1,3])])
def test_plan72_chat_delete_confirm_keeps_generated_content(fixture, monkeypatch, text, selected):
    f,model,ctx,d=three(fixture)
    keep=copy.deepcopy(d['outline']['stages'][0]['modules'][1]['points'])
    sources=copy.deepcopy(d['sources']); calls=[]
    def complete(purpose,messages,**kw):
        calls.append(purpose)
        packet=json.loads(messages[0]['content'].split('\n',1)[1])
        assert [m['n'] for m in packet['ctx']['modules']]==[1,2,3]
        assert len(packet['ctx']['modules'][0]['points'])==2
        assert '知识地图' in kw['system'] and 'delete_map' in kw['system']
        return SimpleNamespace(text=json.dumps(dict(intents=[dict(kind='delete_map',modules=selected,points=[],unknown=[])],reply='')))
    monkeypatch.setattr(model,'complete',complete)
    pid=d['plan_id']
    before=f.run(pid,lambda d,c:f.chat(d,c,text),ctx)['draft']
    assert len(agents.outline_modules(before['outline']))==3
    assert '共 4 个知识点' in before['pending_map_delete']['text']
    after=f.run(pid,lambda d,c:f.chat(d,c,'确定'),ctx)['draft']
    assert calls==['plan_intent']
    assert [m['key'] for m in agents.outline_modules(after['outline'])]==['m2']
    assert agents.outline_modules(after['outline'])[0]['points']==keep
    assert after['sources']==sources and after['phase']=='outline'
    assert after['outline']['ranges']['points']==2
    assert after['outline']['ranges']['learning']==sum(p['minutes'] for p in keep)
    assert [r['title'] for r in after['removed_content'] if r['kind']=='module']==['资料导航与学习路线','面试计划、项目表达与求职准备']
    assert f.public(f.load(pid))['outline']['stages']==after['outline']['stages']


def test_plan72_point_button_cancel_and_confirm_no_calls(fixture):
    f,model,ctx,d=three(fixture); before=copy.deepcopy(d['outline']); calls=list(model.calls)
    f.request_map_delete(d,[],[dict(module='m2',n=1)])
    f.confirm_map_delete(d,False)
    assert d['outline']==before and model.calls==calls
    f.request_map_delete(d,[],[dict(module='m2',n=1)])
    f.confirm_map_delete(d,True)
    assert agents.outline_modules(d['outline'])[1]['points']==before['stages'][0]['modules'][1]['points'][1:]
    assert dict(kind='point',title='Java 基础点1') in d['removed_content']
    assert d['outline']['ranges']['points']==5 and model.calls==calls


def test_plan72_unknown_lists_existing_modules_and_never_mutates(fixture):
    f,model,ctx,d=three(fixture); before=copy.deepcopy(d['outline'])
    context=f._intent_context(d)
    intent=plan_intent.parse_intents(json.dumps(dict(intents=[dict(kind='delete_map',modules=[],points=[],unknown=['不存在的模块'])],reply='')),context)[0]
    f._carry_out(d,ctx,intent,context)
    assert '现有模块：1. 资料导航与学习路线' in d['messages'][-1]['text']
    assert 'Java 基础' in d['messages'][-1]['text']
    assert d['outline']==before and not d.get('pending_map_delete')


def test_plan72_removed_points_still_filtered_on_refill(fixture):
    f,model,ctx,d=three(fixture)
    f.request_map_delete(d,['m1'],[dict(module='m2',n=1)])
    f.confirm_map_delete(d,True)
    p=dict(title='Java 基础点1', citations=[dict(chunk_id='c1')])
    retained=dict(title='Java 基础点2', citations=[dict(chunk_id='c1')])
    assert agents.filter_removed_content([p,retained],[],d['removed_content'])==[dict(retained,chunk_ids=['c1'])]
    # A broad shared chapter is not removed with a sibling module.
    assert not [r for r in d['removed_content'] if r['kind']=='chapter']
    sections=f._sections(d)[1]
    request=agents.material_structure_request(d['goal'],d['form'],d.get('preview'),sections,removed_content=d['removed_content'])
    assert '资料导航与学习路线' in request[1][0]['content']


def test_plan72_stale_confirm_and_material_preview_do_not_delete(fixture):
    f,model,ctx,d=three(fixture)
    f.request_map_delete(d,['m1'],[])
    d['outline']['version']+=1
    f.confirm_map_delete(d,True)
    assert len(agents.outline_modules(d['outline']))==3
    d['phase']='material_preview'
    from refactor.learning.app.bridge import BridgeError
    with pytest.raises(BridgeError): f.request_map_delete(d,['m1'],[])


def test_plan72_delete_intent_cannot_also_refill(fixture,monkeypatch):
    f,model,ctx,d=three(fixture)
    monkeypatch.setattr(model,'complete',lambda *a,**k:SimpleNamespace(text=json.dumps(dict(intents=[
        dict(kind='delete_map',modules=[1],points=[],unknown=[]),dict(kind='supplement',instruction='删模块')],reply=''))))
    monkeypatch.setattr(f,'material_structure',lambda *a,**k:pytest.fail('no structure call'))
    f.chat(d,ctx,'删掉导航模块')
    assert d['phase']=='outline' and len(agents.outline_modules(d['outline']))==3


def test_plan72_all_modules_delete_yields_empty_local_map(fixture):
    f,model,ctx,d=three(fixture)
    f.request_map_delete(d,['m1','m2','m3'],[]);f.confirm_map_delete(d,True)
    assert d['outline']['stages']==[] and d['outline']['ranges']['points']==0


def test_plan72_later_structure_cannot_readd_deleted_module_on_shared_chapter(fixture,monkeypatch):
    f,model,ctx,d=three(fixture)
    f.request_map_delete(d,['m1'],[]);f.confirm_map_delete(d,True)
    def complete(purpose,messages,**kw):
        assert purpose=='outline'
        packet=json.loads(messages[0]['content'])
        assert any(r['title']=='资料导航与学习路线' for r in packet['removed_content'])
        ref=packet['directory'][0]['id']
        return SimpleNamespace(text=json.dumps(dict(summary='调整结构',missing=[],modules=[
            dict(key='new1',title='资料导航与学习路线',summary='又从概述抽出来',refs=[ref]),
            dict(key='new2',title='Java 基础',summary='保留知识',refs=[ref])]),ensure_ascii=False))
    monkeypatch.setattr(model,'complete',complete)
    f.material_structure(d,ctx,'重新整理保留的内容')
    assert [m['title'] for m in d['material_structure']['modules']]==['Java 基础']
