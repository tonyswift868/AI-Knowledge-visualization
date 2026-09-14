"""Canonical PlanTask mutation boundary.

Callers pass their already-open SQLAlchemy session and compatibility projector;
the projector and outbox callback therefore participate in the same commit.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Mapping

from ..orm import PlanExecutionTaskORM


class PlanTaskMutationRejected(ValueError):
    pass


class PlanTaskMutationService:
    OPERATIONS = frozenset({'create', 'update', 'start', 'complete', 'reopen', 'reschedule', 'archive'})

    def mutate(self, session, *, workspace_id: str, task_id: str, operation: str,
               changes: Mapping | None = None, expected_revision: int | None = None,
               project_compatibility: Callable | None = None,
               append_sync_intent: Callable | None = None):
        if operation not in self.OPERATIONS:
            raise PlanTaskMutationRejected('UNSUPPORTED_PLAN_TASK_OPERATION')
        if not isinstance(workspace_id, str) or not workspace_id or not isinstance(task_id, str) or not task_id:
            raise PlanTaskMutationRejected('PLAN_TASK_SCOPE_REQUIRED')
        task = session.get(PlanExecutionTaskORM, task_id)
        if operation == 'create':
            if task is not None:
                raise PlanTaskMutationRejected('PLAN_TASK_ALREADY_EXISTS')
            values = dict(changes or {})
            if values.get('workspace_id') != workspace_id:
                raise PlanTaskMutationRejected('PLAN_TASK_SCOPE_MISMATCH')
            task = PlanExecutionTaskORM(id=task_id, **values)
            session.add(task)
        else:
            if task is None or task.workspace_id != workspace_id:
                raise PlanTaskMutationRejected('PLAN_TASK_NOT_IN_SCOPE')
            current = int(getattr(task, 'revision', 0) or 0)
            if expected_revision is not None and expected_revision != current:
                raise PlanTaskMutationRejected('PLAN_TASK_STALE_REVISION')
            if operation == 'start': task.status = 'in_progress'
            elif operation == 'complete': task.status = 'completed'; task.completed_at = datetime.now(timezone.utc)
            elif operation == 'reopen': task.status = 'planned'; task.completed_at = None
            elif operation == 'archive': task.status = 'archived'
            elif operation == 'reschedule':
                if not changes or 'day_date' not in changes:
                    raise PlanTaskMutationRejected('PLAN_TASK_DATE_REQUIRED')
                task.day_date = str(changes['day_date'])
            elif operation == 'update':
                for key, value in dict(changes or {}).items():
                    if key in {'id', 'workspace_id', 'revision', 'created_at'}:
                        raise PlanTaskMutationRejected('PLAN_TASK_IMMUTABLE_FIELD')
                    if hasattr(task, key): setattr(task, key, value)
                else:
                    if not changes: raise PlanTaskMutationRejected('PLAN_TASK_UPDATE_EMPTY')
            task.revision = current + 1
        session.flush()
        if project_compatibility is not None:
            project_compatibility(session, task)
        if append_sync_intent is not None:
            append_sync_intent(session, task, operation)
        return task
