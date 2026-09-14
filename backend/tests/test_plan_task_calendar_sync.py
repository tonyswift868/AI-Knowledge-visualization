from types import SimpleNamespace
import pytest
from app.enterprise_tenancy.plan_task_calendar_sync import *

class Repo:
    def __init__(self): self.tasks={}; self.calendars={}
    def upsert_task(self, key, payload, **meta): self.tasks[key]=(payload,meta); return key
    def upsert_calendar(self, key, payload, **meta): self.calendars[key]=(payload,meta); return key

def task():
    return SimpleNamespace(id='task-1', workspace_id='ws-1', revision=3, deleted=False,
        plan_id='plan-1', map_revision_id='map-1', knowledge_point_id='kp-1', day_date='2026-09-14',
        original_day_date='2026-09-14', day_index=0, task_order=1, title='Read', objective='Understand',
        acceptance_condition='quiz', estimated_task_minutes=20, completed_task_minutes=0,
        actual_elapsed_minutes=0, status='planned', task_type='initial_learning', learning_phase='understand',
        point_sequence=1, phase_part_index=1, phase_part_count=1, concept_snapshot={}, source_evidence=[])

def test_task_export_apply_is_scoped_and_deterministic():
    p=PlanTaskAdapter().export(task(), workspace_id='ws-1'); r=Repo()
    assert PlanTaskAdapter().apply(p,r,workspace_id='ws-1') == 'task-1'
    assert deterministic_snapshot([p]) == deterministic_snapshot([p])
    assert r.tasks['task-1'][0]['title']=='Read'

def test_task_wrong_workspace_and_tampered_projection_rejected():
    p=PlanTaskAdapter().export(task(), workspace_id='ws-1'); r=Repo()
    with pytest.raises(ValueError): PlanTaskAdapter().apply(p,r,workspace_id='ws-2')
    with pytest.raises(ValueError): PlanTaskAdapter().apply(DomainProjection('task','x','ws-1',1,False,'bad',{}),r,workspace_id='ws-1')

def test_calendar_export_apply_delete_and_no_fake_revision():
    row={'id':'cal-1','workspace_id':'ws-1','plan_id':'plan-1','task_id':'task-1','day_date':'2026-09-14','status':'scheduled'}
    a=CalendarAdapter(); p=a.export(row,workspace_id='ws-1'); assert p.revision is None
    r=Repo(); a.apply(p,r,workspace_id='ws-1')
    deleted=DomainProjection(p.kind,p.stable_id,p.workspace_id,p.revision,True,p.schema,p.payload)
    a.apply(deleted,r,workspace_id='ws-1'); assert r.calendars['cal-1'][1]['deleted'] is True

def test_calendar_wrong_workspace_rejected():
    with pytest.raises(ValueError): CalendarAdapter().export({'id':'c','workspace_id':'other'},workspace_id='ws-1')
