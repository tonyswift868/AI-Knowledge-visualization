"""User-specific quantities, one adjustment, and absence of a global point cap."""
from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace
import threading

import pytest

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.connections.errors import ModelError
from refactor.learning.course import planner_agents as agents
from tests.refactor.planner.test_planner_agents import FORM

NOTES = '限定3个模块、总共8–10个知识点（推荐每模块3点），每点约15分钟。模块1等式与加减消元，模块2乘除及综合解法，模块3简单应用与检验。不含二元方程组、二次方程、不等式；不需要把整本教材每小节分别设点。'

def outline(minutes=(30, 60)):
    return dict(stages=[dict(title='方程', modules=[dict(key=f'm{i}', title=f'模块{i}', summary='解方程',
        refs=['s1.1'], prerequisites=[], minutes=list(minutes)) for i in range(1, 4)])], message='路线')

@pytest.mark.parametrize('notes,expected', [
    (NOTES, dict(modules=[3,3], total=[8,10], per_module=[3,3])),
    ('三个模块，合计九个点，每个模块三点', dict(modules=[3,3], total=[9,9], per_module=[3,3])),
    ('3 modules, total 8-10 points, 3 points per module', dict(modules=[3,3], total=[8,10], per_module=[3,3])),
    ('模块数：3，总点数：9，每模块3点', dict(modules=[3,3], total=[9,9], per_module=[3,3])),
])
def test_explicit_number_ranges_and_raw_scope(notes, expected):
    result = agents.note_requirements(dict(notes=notes))
    assert result == dict(expected, scope=notes)

def test_no_notes_keeps_original_minutes_based_point_estimation():
    plan = outline((300,450))
    assert not agents.outline_requirements(plan, dict(notes='只学基础，不做项目'))
    assert all('point_count' not in m for m in agents.outline_modules(plan))
    assert not agents.point_requirement_errors([{}]*40, agents.outline_modules(plan)[0])

def test_explicit_scope_exclusions_checked_without_rejecting_gap_explanations():
    form = dict(notes=NOTES)
    point = dict(title='二次方程解法', summary='学习求根公式')
    assert agents.scope_requirement_errors([point], form)
    assert not agents.scope_requirement_errors([dict(title='方程解法',summary='本计划不含二次方程、不等式。')],form)
    assert agents.point_requirement_errors([point],dict(title='方程'),form)

def test_distribution_totals_conflicts_and_minutes_are_visible():
    plan = outline()
    assert not agents.outline_requirements(plan, dict(notes=NOTES))
    assert [m['point_count'] for m in agents.outline_modules(plan)] == [3,3,3]
    assert agents.outline_requirements(outline(), dict(notes='总共8点，每模块3点'))
    assert agents.outline_requirements(outline((75,90)), dict(notes=NOTES))
    plan = outline()
    assert not agents.outline_requirements(plan, dict(notes='总共8–10点'))
    assert 8 <= sum(m['point_count'] for m in agents.outline_modules(plan)) <= 10

def wire(count):
    return json.dumps(dict(points=[dict(title=f'解法{i}', summary=f'完成步骤{i}', chunks=['c1'], minutes=15)
                                  for i in range(count)]), ensure_ascii=False)

@pytest.mark.parametrize('counts,success', [([5,3],True), ([5,5],False), ([3],True)])
def test_formal_generation_adjusts_once_without_truncating(counts, success):
    flow = PlanFlow(None, lambda *_: None, date.today)
    plan = outline(); agents.outline_requirements(plan, dict(notes=NOTES))
    module = agents.outline_modules(plan)[1]
    draft = dict(plan_id='p', form=dict(FORM, notes=NOTES), goal='方程', outline=plan)
    requests = []
    def ask(purpose, request, parse, ctx, **kwargs):
        requests.append(request)
        return parse(wire(counts[len(requests)-1])), None
    flow._ask = ask
    args = (draft, module, [dict(id='original', text='方程解法原文')], SimpleNamespace(cancel=threading.Event()))
    if success:
        points = flow._generate_module(*args)
        assert len(points) == 3 and all(p['chunk_ids'] == ['original'] for p in points)
    else:
        with pytest.raises(agents.AgentFormatError, match='要求3点，实际5点'): flow._generate_module(*args)
    assert len(requests) == len(counts)
    assert NOTES in requests[0][1][0]['content']
    if len(requests) == 2:
        assert '一次调整' in requests[1][1][-1]['content'] and '上一版' in requests[1][1][-1]['content']

def test_outline_attempts_one_adjustment_then_blocks_formal_generation():
    flow = PlanFlow(None, lambda *_: None, date.today)
    form = dict(FORM, notes='限定2个模块、每模块3点')
    draft = dict(plan_id='p', phase='sources', form=form, goal='方程', outline=None, messages=[])
    section = agents.Section('s1.1','s','资料','章节',('c',),500,0,0)
    flow._sections = lambda _: ([dict(id='s', title='资料')],[section])
    flow.save = lambda _: None
    calls = []
    flow._ask = lambda *args, **kwargs: (calls.append(args) or deepcopy(outline()), None)
    flow.make_outline(draft, SimpleNamespace(cancel=threading.Event()))
    assert len(calls) == 2 and draft['outline']['constraint_errors']
    assert '仍不符合' in draft['messages'][-1]['text']
    flow.services = SimpleNamespace(store=None)
    flow.generate(draft, SimpleNamespace(cancel=threading.Event()))
    assert draft['phase'] == 'outline' and len(calls) == 2

def test_explicit_single_point_is_not_rejected_by_legacy_batch_minimum():
    flow = PlanFlow(None,lambda *_: None,date.today)
    plan = outline((10,20)); form=dict(FORM,notes='每模块1点')
    assert not agents.outline_requirements(plan,form)
    draft=dict(plan_id='p',form=form,goal='方程',outline=plan)
    flow._ask = lambda purpose,request,parse,*a,**kw: (parse(wire(1)),None)
    assert len(flow._generate_module(draft,agents.outline_modules(plan)[0],
                                    [dict(id='original',text='资料正文')],None))==1

def test_invalid_adjustment_reports_original_count_failure_in_outline():
    flow=PlanFlow(None,lambda *_:None,date.today)
    plan=outline(); agents.outline_requirements(plan,dict(notes=NOTES))
    draft=dict(plan_id='p',form=dict(FORM,notes=NOTES),goal='方程',outline=plan)
    count=[]
    def ask(purpose,request,parse,*args,**kwargs):
        count.append(purpose)
        if len(count)==2: raise ModelError('AGENT_FORMAT','回复格式无效')
        return parse(wire(5)),None
    flow._ask=ask
    with pytest.raises(agents.AgentFormatError,match='要求3点，实际5点.*调整回复'):
        flow._generate_module(draft,agents.outline_modules(plan)[0],[dict(id='original',text='资料正文')],None)
    assert count==['generate','generate']
