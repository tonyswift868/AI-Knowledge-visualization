"""Batch 65 usual-case calibration, warning boundaries and preserved form actions."""
import copy
from statistics import median
from types import SimpleNamespace
import pytest
from refactor.learning.app.plan_flow import CHARS_PER_POINT_TYPICAL
from refactor.learning.course import planner_agents as agents
from tests.refactor.plan37.test_grounded_plan37_flow import fixture, adopt_and_map

CHARS = [21845,12931,28813,10011,55595,202515,87311,38248]

def javaguide(flow, draft, monkeypatch, cap=600, end='2026-10-30'):
    draft['form'].update(start='2026-10-06',end=end,daily_cap=cap,weekday_weights=[1]*7,special_days=[])
    draft['material_structure']['modules']=[dict(key=f'm{i}',title=f'模块{i}',summary='目标',refs=[f's{i}'],chapters=[f'章节{i}']) for i in range(8)]
    sections=[SimpleNamespace(id=f's{i}',chunk_ids=[f'c{i}'],source_title='JavaGuide',title=f'章节{i}',chars=n) for i,n in enumerate(CHARS)]
    chunks=[dict(id=f'c{i}',text='文'*n) for i,n in enumerate(CHARS)]
    monkeypatch.setattr(flow,'_sections',lambda _: ([],sections))
    monkeypatch.setattr(flow,'_adopted',lambda _: ([],{'source':chunks}))
    return flow.structure_ranges(draft)

def test_plan65_five_real_records_use_median():
    assert CHARS_PER_POINT_TYPICAL == median([4481/10,123846/163,2434/8,43743/26,28813/62]) == 28813/62
    assert '不要写具体日期和天数' in agents.PREVIEW_SYSTEM
    assert '不要写具体日期和天数' in agents.SUFFICIENCY_SYSTEM

@pytest.mark.parametrize('cap,end,warning,daily,suggestion',[
    (600,'2026-10-30','none',498,None),
    (450,'2026-10-30','possible',498,'2026-11-02'),
    (200,'2026-10-30','possible',498,'2026-12-07'),
    (200,'2026-10-20','impossible',830,'2026-12-07'),
])
def test_plan65_warning_uses_usual_and_optimistic_cases(fixture,monkeypatch,cap,end,warning,daily,suggestion):
    flow,model,*_=fixture;draft=adopt_and_map(fixture);calls=list(model.calls)
    modules,r=javaguide(flow,draft,monkeypatch,cap,end)
    assert r['warning_level']==warning and r['typical']['points']==987
    assert [m['estimated_point_count'] for m in modules]==[48,28,62,22,120,436,188,83]
    assert r['typical']['total']==[12437]*2 and r['typical']['daily']==[daily]*2
    assert r['suggested_cap']==(500 if daily==498 else 830)
    assert r['suggested_end']==suggestion
    assert r['points']==[272,1530] and r['total']==[3428,19278]
    assert model.calls==calls

def test_plan65_cap_boundary_and_form_changes_preserve_structure(fixture,monkeypatch):
    flow,model,ctx,*_=fixture;draft=adopt_and_map(fixture)
    draft['phase']='material_preview'
    _,r=javaguide(flow,draft,monkeypatch,498)
    original=copy.deepcopy(draft['material_structure']);sources=copy.deepcopy(draft['sources']);outline=copy.deepcopy(draft['outline'])
    assert r['warning_level']=='none'
    calls=list(model.calls)
    flow.adjust_window(draft,ctx,{'daily_cap':500,'end':'2026-11-02'})
    assert draft['material_structure']==original and draft['sources']==sources
    assert {k:v for k,v in draft['outline'].items() if k!='ranges'}=={k:v for k,v in outline.items() if k!='ranges'}
    assert flow.structure_ranges(draft)[1]['warning_level']=='none' and model.calls==calls
