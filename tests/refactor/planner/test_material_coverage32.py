"""N-P2-01: real saved duplicate replay and material-limited generation."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course import planner_agents as agents
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore
from tests.refactor.planner.test_planner_agents import FORM
from tests.refactor.planner.test_notes_constraints30 import NOTES


def sample():
    return json.loads((Path(__file__).parent/'fixtures/duplicates32.json').read_text(encoding='utf-8-sig'))


def curriculum():
    data = sample()
    modules = sorted(data['modules'], key=lambda m: m['position'])
    groups = [[dict(p, module_title=m['title']) for p in data['points'] if p['module_id'] == m['id']]
              for m in modules]
    return modules, groups


def test_saved_acceptance3b_three_repeated_points_and_distinct_applications():
    _, groups = curriculum()
    hits = agents.cross_module_duplicates(groups[1], groups[0])
    assert [hit['index'] for hit in hits] == [0, 1, 2]
    assert not agents.cross_module_duplicates(groups[2], groups[0])
    assert sum(p['minutes'] for p in groups[1]) == 48  # previously counted twice


def test_shared_material_alone_and_generic_titles_are_not_duplicate_evidence():
    p = dict(title='代入检验', summary='将数值代入方程检验两边相等', chunk_ids=['c'], minutes=15)
    q = dict(title='应用题完整作答', summary='按实际情境检验合理性、写出单位及答句', chunk_ids=['c'], minutes=15)
    assert not agents.cross_module_duplicates([q], [p])
    q = dict(p, chunk_ids=['other'])
    assert not agents.cross_module_duplicates([q], [p])


def covered_outline():
    return dict(stages=[dict(title='方程', modules=[dict(key=f'm{i}', title=title,
        summary='由资料支持的独立内容', refs=['s1.1'], prerequisites=[], minutes=[40,50])
        for i,title in enumerate(['加减方程', '文字应用'], 1)])], message='缺乘除资料',
        coverage=dict(available_modules=2, available_points=6, missing=['乘除等式性质']))


def test_coverage_parsing_and_visible_choice_does_not_silently_relax_counts():
    outline = agents.parse_outline(json.dumps(covered_outline()), ['s1.1'])
    form = dict(FORM, notes=NOTES)
    notice = agents.coverage_notice(outline, form)
    assert all(s in notice for s in ['2个模块', '6个独立知识点', '乘除', '贴网址', '再找资料', '按资料范围生成'])
    assert agents.outline_requirements(outline, form)
    assert not agents.outline_requirements(outline, agents.covered_form(form, outline))
    assert [m['point_count'] for m in agents.outline_modules(outline)] == [3,3]
    assert agents.covered_form(form, outline)['notes'] == NOTES
    malformed = covered_outline(); malformed['coverage']['available_modules'] = 3
    with pytest.raises(agents.AgentFormatError, match='coverage'):
        agents.parse_outline(json.dumps(malformed), ['s1.1'])


def test_outline_shortfall_no_futile_count_retry_and_explicit_acceptance():
    flow = PlanFlow(None, lambda *_: None, date.today)
    draft = dict(plan_id='p', phase='sources', form=dict(FORM,notes=NOTES), goal='方程', outline=None, messages=[])
    section = agents.Section('s1.1','s','资料','章节',('c',),500,0,0)
    flow._sections = lambda _: ([dict(id='s',title='资料')],[section])
    flow.save = lambda _: None
    calls=[]
    flow._ask = lambda *args, **kwargs: (calls.append(args) or deepcopy(covered_outline()), None)
    flow.make_outline(draft, SimpleNamespace(cancel=threading.Event()))
    assert len(calls)==1 and not draft['outline'].get('coverage_accepted')
    flow.services=SimpleNamespace(store=None)
    flow.generate(draft,SimpleNamespace(cancel=threading.Event()))
    assert draft['phase']=='outline' and len(calls)==1
    flow.make_outline(draft,None,'按资料范围生成')
    assert draft['outline']['coverage_accepted'] and not draft['outline']['constraint_errors']
    assert len(calls)==1  # host acknowledges an existing covered route, no paid call


def test_cross_module_adjustment_once_and_previous_context_not_overwritten():
    _, groups=curriculum()
    flow=PlanFlow(None,lambda *_:None,date.today)
    module=dict(key='m2',title='综合解法',summary='综合',minutes=[45,50],point_count=3)
    chunks=[dict(id=c,text='原文') for c in dict.fromkeys(c for p in groups[1] for c in p['chunk_ids'])]
    draft=dict(plan_id='p',form=dict(FORM,notes=NOTES),goal='方程',outline=covered_outline())
    calls=[]
    def ask(purpose, request, parse, *args, **kwargs):
        calls.append(request)
        aliases={c['id']:f'c{i}' for i,c in enumerate(chunks,1)}
        wire=[dict(title=p['title'],summary=p['summary'],chunks=[aliases[c] for c in p['chunk_ids']],minutes=p['minutes'])
              for p in groups[1]]
        return parse(json.dumps(dict(points=wire))),None
    flow._ask=ask
    with pytest.raises(agents.MaterialCoverageError,match='调整一次仍有跨模块重复'):
        flow._check_module_points(draft,module,chunks,None,groups[1],groups[0])
    assert len(calls)==1
    tail=calls[0][1][-1]['content']
    assert groups[0][0]['summary'] in tail and '不能重复前面模块' in tail


@pytest.mark.parametrize('earlier_failed', [False, True])
def test_parallel_generation_admits_in_order_and_returns_to_working_outline_actions(tmp_path, earlier_failed):
    modules, groups=curriculum()
    form=dict(FORM,notes=NOTES,start='2026-10-05',end='2026-10-08',daily_cap=90)
    with CourseStore('test32',tmp_path) as store:
        store.create_plan('p',goal='方程',final_goal='解方程',level='beginner',notes=NOTES,
                          window=PlanWindow(date(2026,10,5),date(2026,10,8),90,(1,)*7))
        ids=list(dict.fromkeys(c for group in groups for p in group for c in p['chunk_ids']))
        store.add_sources('p',[dict(id='source',title='OpenStax',url='https://openstax.org/test',adopted=True,chunks=[dict(chunk_id=c,text='原文',
            position=i,section=[]) for i,c in enumerate(ids)])])
        outline=dict(stages=[dict(title='方程',modules=[dict(key=f'm{i}',id=m['id'],title=m['title'],
            summary='方程学习',minutes=[45,50],refs=['s1.1'],prerequisites=[],spans=[['source',0,len(ids)-1]])
            for i,m in enumerate(modules,1)])])
        draft=dict(plan_id='p',phase='outline',form=form,goal='方程',outline=outline,generation={},messages=[],
                   sources=[dict(status='adopted',source_id='source')])
        flow=PlanFlow(SimpleNamespace(store=store),lambda *_:None,lambda:date(2026,10,2))
        flow.save=lambda _:None
        completed=[]
        initial_failure=earlier_failed
        second_ready=threading.Event()
        def batches(draft,module,chunks,ctx):
            index=next(i for i,m in enumerate(modules) if m['id']==module['id'])
            if index==0 and initial_failure:
                raise RuntimeError('first module temporarily unavailable')
            if index==0: assert second_ready.wait(5)
            else: second_ready.set()
            completed.append(index)
            return deepcopy(groups[index])
        flow._generate_module_batches=batches
        adjusted=[]
        def ask(purpose,request,parse,ctx,**kwargs):
            adjusted.append(request)
            aliases={c:f'c{i}' for i,c in enumerate(ids,1)}
            return parse(json.dumps(dict(points=[dict(title=p['title'],summary=p['summary'],minutes=p['minutes'],
                chunks=[aliases[c] for c in p['chunk_ids']]) for p in groups[1]]))),None
        flow._ask=ask
        flow.generate(draft,SimpleNamespace(cancel=threading.Event()))
        if earlier_failed:
            assert draft['phase']=='generating' and len(store.records('points','p'))==6
            initial_failure=False
            flow.generate(draft,SimpleNamespace(cancel=threading.Event()))
        assert completed.index(1)<completed.index(0)
        assert len(adjusted)==1 and draft['phase']=='outline'
        assert '资料只够2个模块' in draft['outline']['coverage_notice']
        assert not store.records('points','p')  # invalid creation never becomes a ready plan
        assert not store.records('modules','p')  # duplicate 48 minutes gone, not left as an estimate
        assert len(agents.outline_modules(draft['outline']))==2
        flow._sections=lambda _: ([dict(id='source')],[agents.Section('s1.1','source','资料','章',tuple(ids),500,0,len(ids)-1)])
        flow.make_outline(draft,None,'按资料范围生成')
        assert draft['outline']['coverage_accepted']


def test_replacement_unique_but_insufficient_returns_material_shortfall_not_count_loop():
    flow=PlanFlow(None,lambda *_:None,date.today)
    prior=dict(title='检查方程的解',summary='代入方程检验两边相等是否成立',chunk_ids=['c'],minutes=15)
    module=dict(title='重复模块',summary='检查',minutes=[30,40],point_count=2)
    draft=dict(plan_id='p',form=dict(FORM,notes=''),goal='方程',outline=covered_outline())
    calls=[]
    def ask(purpose,request,parse,*args,**kwargs):
        calls.append(request)
        return parse(json.dumps(dict(points=[dict(title='文字句翻译',summary='识别等号词并将文字问题列为方程',
                                                   chunks=['c1'],minutes=15)]))),None
    flow._ask=ask
    with pytest.raises(agents.MaterialCoverageError,match='去重后不能满足数量'):
        flow._check_module_points(draft,module,[dict(id='c',text='原文')],None,[prior,prior],[prior])
    assert len(calls)==1
