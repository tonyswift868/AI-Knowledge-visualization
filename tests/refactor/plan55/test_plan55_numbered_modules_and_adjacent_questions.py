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
    # Duration estimates are a recalculated public projection, not generated content.
    assert f.load(pid)['material_structure']['modules'] == [
        {k:v for k,v in m.items() if k not in ('estimated_points', 'estimated_point_count', 'chars', 'estimated_question_count', 'has_material_questions')} for m in (before[0],before[2])]


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
