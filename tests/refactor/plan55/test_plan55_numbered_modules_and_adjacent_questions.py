"""Batch 54 captured failures; offline host actions and exact-text de-duplication."""
import copy
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.learning.course import plan_intent, point_materials as pm
from refactor.learning.course.planner_agents import AgentFormatError
from tests.refactor.plan51.test_plan51_selected_material_flow import flow, URL
from tests.refactor.point_materials.test_point_materials37_contract import store, ctx, pointer


def captured():
    return json.loads(Path(__file__).with_name('source54_scope_and_questions.json').read_text(encoding='utf-8'))


def test_plan55_original_scope54_unknown_ids_confirmed():
    data = captured()
    with pytest.raises(AgentFormatError, match='unknown/duplicate'):
        plan_intent.parse_intents(data['reply'], data['ctx'], user_text=data['user_text'])


def test_plan55_material_modules_displayed_and_selected_without_generation(flow, monkeypatch):
    f, c, pid, model = flow
    f.run(pid, lambda d,c:f.chat(d,c,URL), c)
    d = f.run(pid, lambda d,c:f.make_outline(d,c), c)['draft']
    assert '1. ' in d['messages'][-1]['text']
    before = copy.deepcopy(d['material_structure']['modules'])
    ctx = f._intent_context(d)
    assert [m['n'] for m in ctx['modules']] == [1,2,3]
    assert [m['title'] for m in ctx['modules']] == [m['title'] for m in before]
    calls = []
    def complete(purpose, messages, **kwargs):
        calls.append(purpose)
        assert 'select_modules' in kwargs['system']
        return SimpleNamespace(text=json.dumps(dict(intents=[dict(kind='select_modules',
            include=[1,3,3,99], exclude=[1,2])], reply='')))
    monkeypatch.setattr(model, 'complete', complete)
    result = f.run(pid, lambda d,c:f.chat(d,c,'只留第1和第3模块'), c)['draft']
    assert result['phase'] == 'material_preview' and calls == ['plan_intent']
    assert result['material_structure']['modules'] == [before[0],before[2]]
    assert '3、99、1' in result['messages'][-1]['text']
    assert '2. ' in result['messages'][-1]['text']  # renumber after deletion
    assert f.load(pid)['material_structure']['modules'] == [before[0],before[2]]


def test_plan55_invalid_keep_does_not_erase_structure(flow):
    f,c,pid,_ = flow
    f.run(pid,lambda d,c:f.chat(d,c,URL),c)
    d=f.run(pid,lambda d,c:f.make_outline(d,c),c)['draft']
    ctx=f._intent_context(d)
    parsed=plan_intent.parse_intents(json.dumps(dict(intents=[dict(kind='select_modules',include=[99],exclude=[])],reply='')),ctx)
    before=copy.deepcopy(d['material_structure']['modules'])
    f._carry_out(d,c,parsed[0],ctx)
    assert d['material_structure']['modules']==before and '99' in d['messages'][-1]['text']
    ctx=f._intent_context(d)
    parsed=plan_intent.parse_intents(json.dumps(dict(intents=[dict(kind='select_modules',include=[99],exclude=[2])],reply='')),ctx)
    f._carry_out(d,c,parsed[0],ctx)
    assert d['material_structure']['modules']==[before[0],before[2]]


def test_plan55_delete_and_confirm_apply_before_fill(flow,monkeypatch):
    f,c,pid,model=flow
    f.run(pid,lambda d,c:f.chat(d,c,URL),c)
    f.run(pid,lambda d,c:f.make_outline(d,c),c)
    old=model.complete
    def complete(purpose,messages,**kwargs):
        if purpose=='plan_intent':
            return SimpleNamespace(text=json.dumps(dict(intents=[dict(kind='proceed',step='outline'),
                dict(kind='select_modules',include=[],exclude=[2,3])],reply='')))
        return old(purpose,messages,**kwargs)
    monkeypatch.setattr(model,'complete',complete)
    result=f.run(pid,lambda d,c:f.chat(d,c,'确认，只留第一模块'),c)['draft']
    assert result['phase']=='outline'
    assert len(result['outline']['stages'][0]['modules'])==1


def test_plan55_scope54_first_two_point_answer_and_stem_duplicates_rejected():
    data=captured()
    points=sorted(data['points'],key=lambda p:(p['module_id'],p['position']))
    first=next(p for p in points if p['title']=='互联网是什么')
    second=next(p for p in points if p['id']==first['id'].rsplit('-',1)[0]+'-02')
    adjacent=[q for q in data['questions'] if first['id'] in q['point_ids'] and q.get('role')=='bank']
    candidates=[q for q in data['questions'] if second['id'] in q['point_ids'] and q.get('role')=='bank']
    rejected=[]
    assert len(candidates)==2 and pm._exclude_adjacent(candidates,adjacent,rejected)==[]
    assert len(rejected)==2
    quick=[q for q in data['questions'] if second['id'] in q['point_ids'] and q.get('role')=='quick']
    assert len(quick)==1 and pm._exclude_adjacent(quick,adjacent,rejected)==[]


def test_plan55_neighbor_retry_finds_new_sentence_and_does_not_touch_other_modules(store):
    point=store.records('points','p')[0]
    today=date(2026,10,4)
    contrast=store._get('source_chunks','p','other37')
    contrast['text']+='速度的单位是米每秒。密度的单位是千克每立方米。'
    with store.transaction():store._put('source_chunks','p',contrast)
    store.save_generated_module('p','m',[dict(point),dict(id='next',title='相邻点',summary='另一个事实',
        minutes=15,chunk_ids=['c'],importance='key')],check_minutes=5,today=today)
    point=store.records('points','p')[0]
    pm.prepare_point(store,'p',point,ctx())
    next_point=next(p for p in store.records('points','p') if p['id']=='next')
    calls=[]
    def finder(p,ps,mode,**kw):
        calls.append((mode,p.get('cloze_rejections',[])))
        if mode=='cloze_retry':
            # There is only one independent new answer; retain it and report insufficiency.
            return dict(passages=['c'],found=[],cloze=[dict(target='压强的单位是什么？',chunk_id='c',
                sentence='国际单位制中压强的单位是帕。',word='帕',subject='压强',importance='key',contrasts=[
                    dict(chunk_id='other37',contrast_quote='速度的单位是米每秒。',contrast_subject='速度',contrast_value='米每秒'),
                    dict(chunk_id='other37',contrast_quote='密度的单位是千克每立方米。',contrast_subject='密度',contrast_value='千克每立方米')])])
        return pointer()
    context=ctx();context['finder']=finder
    with pytest.raises(pm.StoreError) as err:pm.prepare_point(store,'p',next_point,context)
    assert err.value.code=='CLOZE_FAILED'
    assert [mode for mode,_ in calls]==['adopted','cloze_retry']
    assert any('相邻' in r['reason'] for r in calls[-1][1])
    rows=[q for q in store.records('question_bank','p') if q.get('role')=='bank' and 'next' in q['point_ids']]
    assert len(rows)==1 and pm._answer_key(rows[0])==('帕',)
    neighbors=pm._adjacent_questions(store,'p',next_point)
    assert {tuple(q['point_ids']) for q in neighbors}=={(point['id'],)}


def test_plan55_answer_comparison_ignores_punctuation_not_words():
    q=dict(kind='choice',options=['计算机网络。','网页'],answer=[0],stem='定义？')
    same=dict(q,options=['计 算 机 网 络！','网页'],stem='另一个问法')
    different=dict(q,options=['计算机网页','网页'],stem='区别？')
    rejected=[]
    assert pm._exclude_adjacent([same,different],[q],rejected)==[different]
