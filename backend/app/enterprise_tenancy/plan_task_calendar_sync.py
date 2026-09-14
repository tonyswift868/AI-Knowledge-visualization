"""Domain-owned projections for the PlanTask/Calendar rehydration slice.

These adapters read committed domain rows and apply snapshots through the
owner-supplied repository. They never execute business commands and never make
the generic sync table the domain authority.
"""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class DomainProjection:
    kind: str
    stable_id: str
    workspace_id: str
    revision: int | None
    deleted: bool
    schema: str
    payload: dict

    def canonical_bytes(self) -> bytes:
        return json.dumps({'kind':self.kind,'stable_id':self.stable_id,
            'workspace_id':self.workspace_id,'revision':self.revision,
            'deleted':self.deleted,'schema':self.schema,'payload':self.payload},
            sort_keys=True, separators=(',',':'), ensure_ascii=False).encode()

class PlanTaskAdapter:
    kind, schema = 'task', 'mentorflow.plan-task-projection.v1'
    def export(self, row, *, workspace_id: str) -> DomainProjection:
        if str(getattr(row, 'workspace_id', '')) != workspace_id:
            raise ValueError('task workspace mismatch')
        stable_id = str(getattr(row, 'id', '')).strip()
        if not stable_id: raise ValueError('task stable id required')
        payload = {k: getattr(row, k, None) for k in
            ('plan_id','map_revision_id','knowledge_point_id','day_date','original_day_date',
             'day_index','task_order','title','objective','acceptance_condition','estimated_task_minutes',
             'completed_task_minutes','actual_elapsed_minutes','status','task_type','learning_phase',
             'point_sequence','phase_part_index','phase_part_count','concept_snapshot','source_evidence')}
        return DomainProjection(self.kind, stable_id, workspace_id,
            int(getattr(row, 'revision', 1) or 1), bool(getattr(row, 'deleted', False)), self.schema, payload)

    def apply(self, projection: DomainProjection, repository, *, workspace_id: str):
        self._check(projection, workspace_id)
        return repository.upsert_task(projection.stable_id, projection.payload,
                                       revision=projection.revision, deleted=projection.deleted)

    def _check(self, p, workspace):
        if p.kind != self.kind or p.schema != self.schema or p.workspace_id != workspace:
            raise ValueError('task projection scope/schema mismatch')

class CalendarAdapter:
    kind, schema = 'calendar', 'mentorflow.calendar-projection.v1'
    def export(self, row, *, workspace_id: str) -> DomainProjection:
        value = dict(row)
        if str(value.get('workspace_id','')) != workspace_id: raise ValueError('calendar workspace mismatch')
        stable_id = str(value.get('id','')).strip()
        if not stable_id: raise ValueError('calendar stable id required')
        payload = {k:value.get(k) for k in ('plan_id','task_id','day_date','scheduled_date','day_index',
            'task_order','status','title','created_at','updated_at') if k in value}
        # The legacy calendar domain has no monotonic revision; preserve that fact.
        return DomainProjection(self.kind, stable_id, workspace_id, None,
            bool(value.get('deleted',False)), self.schema, payload)
    def apply(self, projection, repository, *, workspace_id: str):
        if projection.kind != self.kind or projection.schema != self.schema or projection.workspace_id != workspace_id:
            raise ValueError('calendar projection scope/schema mismatch')
        return repository.upsert_calendar(projection.stable_id, projection.payload, deleted=projection.deleted)

def deterministic_snapshot(projections):
    rows = sorted((p.canonical_bytes() for p in projections))
    return hashlib.sha256(b'\n'.join(rows)).hexdigest()

__all__ = ['DomainProjection','PlanTaskAdapter','CalendarAdapter','deterministic_snapshot']
