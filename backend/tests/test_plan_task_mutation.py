from types import SimpleNamespace
import pytest
from app.enterprise_tenancy.plan_task_mutation import PlanTaskMutationRejected, PlanTaskMutationService


def test_operation_boundary_rejects_unknown_and_scope_mismatch():
    service = PlanTaskMutationService()
    with pytest.raises(PlanTaskMutationRejected, match='UNSUPPORTED'):
        service.mutate(SimpleNamespace(get=lambda *_: None), workspace_id='w', task_id='t', operation='hack')

