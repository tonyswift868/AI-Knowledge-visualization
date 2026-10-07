"""Knowledge structure exclusions, using the existing host and local drafts."""
import copy
import json
from types import SimpleNamespace

import pytest

from refactor.learning.course import planner_agents as a
from refactor.learning.app.plan_flow import PlanFlow
from tests.refactor.plan51.test_plan51_selected_material_flow import flow


def sections():
    return [a.Section(f's1.{i}', 'book', 'JavaGuide', title, (f'c{i}',), 30, i, i)
            for i, title in enumerate(('关于作者', '资料常见问答/有没有 PDF', 'Java并发', '项目中如何用Redis'), 1)]


def response():
    return dict(summary='只纳入知识', modules=[
        dict(key='m1', title='关于作者', summary='作者介绍', refs=['s1.1']),
        dict(key='m2', title='资料常见问答', summary='有没有 PDF', refs=['s1.2']),
        dict(key='m3', title='Java并发与Redis面试知识', summary='机制与项目应用', refs=['s1.3','s1.4'])],
        missing=['关于作者', '数据库'], excluded=[
            dict(name='关于作者', reason='作者介绍与学习无关', refs=['s1.1']),
            dict(name='资料常见问答', reason='资料取得方式与学习无关', refs=['s1.2'])])


def test_plan67_parser_drops_excluded_even_if_reply_also_lists_them_as_modules():
    parsed=a.parse_material_structure(json.dumps(response()), sections())
    assert [m['title'] for m in parsed['modules']]==['Java并发与Redis面试知识']
    assert parsed['modules'][0]['chapters']==['Java并发','项目中如何用Redis']
    assert parsed['missing']==['数据库']
    assert len(parsed['excluded'])==2


def test_plan67_request_checks_nonknowledge_without_dropping_interview_knowledge():
    system,messages=a.material_structure_request('Java面试', {}, None, sections(), previous=response(), instruction='把资料常见问答也加进来')
    assert all(t in system for t in ('目录说明','如何使用本资料','学习路线','求职','简历','面试准备','项目表达','面试心态与流程','推广与赞助','关于作者','项目中如何用Redis'))
    assert '将该内容恢复为模块并从excluded移除' in system
    packet=json.loads(messages[0]['content'])
    assert packet['previous']['excluded']==response()['excluded']
    assert packet['instruction']=='把资料常见问答也加进来'
    assert len(packet['directory'])==4


@pytest.mark.parametrize('excluded', [None, ['导航'], [dict(name='导航',reason='非知识',refs=['unknown'])], [dict(name='',reason='非知识',refs=[])]])
def test_plan67_excluded_format_and_scope_validated(excluded):
    value=response();value['excluded']=excluded
    with pytest.raises(a.AgentFormatError):a.parse_material_structure(json.dumps(value),sections())


def test_plan67_old_reply_compatible_and_folded_exclusions_expand():
    value=response();value.pop('excluded')
    assert a.parse_material_structure(json.dumps(value),sections())['excluded']==[]
    rows=[a.Section(f's1.{i}', 'book','资料',f'导航 / 小节{i}',(f'c{i}',),10,i,i) for i in range(1,160)] + [sections()[2]]
    # Use unique canonical IDs; no knowledge chapter may alias a navigation one.
    rows[-1]=a.Section('s2.1','java','Java','并发',('knowledge',),30,0,0)
    directory=a.build_sections((),{},directory_sections=rows)
    value=dict(summary='结构', modules=[dict(key='m',title='并发',summary='知识',refs=[directory[-1].id])],missing=[],
               excluded=[dict(name='导航',reason='非知识',refs=[directory[0].id])])
    parsed=a.parse_material_structure(json.dumps(value),rows)
    assert len(parsed['excluded'][0]['refs'])==159
    assert parsed['modules'][0]['refs']==['s2.1']
    _, messages=a.material_structure_request('并发', {}, None, rows, previous=parsed)
    previous=json.loads(messages[0]['content'])['previous']
    assert previous['excluded'][0]['refs']==[directory[0].id]


def test_plan67_saved_exclusion_can_be_restored_through_existing_adjustment(flow, monkeypatch):
    f,ctx,pid,model=flow
    monkeypatch.setattr(f,'_sections',lambda d:([dict(id='book',title='JavaGuide')],sections()))
    value=response()
    d=f.load(pid)
    d['material_structure']=a.parse_material_structure(json.dumps(dict(value,excluded=[])),sections())
    monkeypatch.setattr(model,'complete',lambda *args,**kwargs:SimpleNamespace(text=json.dumps(value)))
    f.material_structure(d,ctx)
    assert not d['removed_content']  # Automatic exclusions are not irreversible learner deletions.
    reopened=PlanFlow(f.services,f.push,f.study_day,fetcher=f.fetcher).load(pid)
    assert len(reopened['material_structure']['excluded'])==2
    assert '这些内容不是知识，未放进计划' in reopened['messages'][-1]['text']
    seen=[]
    restored=copy.deepcopy(value)
    restored['modules']=restored['modules'][1:]
    restored['excluded']=restored['excluded'][:1]
    def reply(*args,**kwargs):
        if args[0]=='plan_intent':
            return SimpleNamespace(text=json.dumps(dict(intents=[dict(kind='revise_outline',instruction='把资料常见问答也加进来')],reply='')))
        seen.append(json.loads(args[1][0]['content']))
        return SimpleNamespace(text=json.dumps(restored))
    monkeypatch.setattr(model,'complete',reply)
    f.chat(reopened,ctx,'把资料常见问答也加进来')
    assert seen[0]['instruction']=='把资料常见问答也加进来'
    assert any(m['title']=='资料常见问答' for m in reopened['material_structure']['modules'])
    assert [r['name'] for r in reopened['material_structure']['excluded']]==['关于作者']

