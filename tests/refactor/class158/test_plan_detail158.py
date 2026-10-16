"""Plan detail counts a concept point earned only through subpoint credits as taught."""
from refactor.learning.app.plan_flow import PlanFlow
from tests.refactor.conversation37.test_grounded_conversation37 import DAY, PLAN
from tests.refactor.concept114.test_concept114_classroom import concept_environment
from tests.refactor.class158.test_subpoint_credits158 import _close, _finish_first_concept


def _taught(env):
    flow = PlanFlow(env.services, lambda *a, **k: None, lambda: DAY)
    detail = flow.detail(PLAN)
    found = {}
    def walk(value):
        if isinstance(value, dict):
            if 'id' in value and 'taught' in value:
                found[value['id']] = value['taught']
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(detail)
    return found


def test_plan_detail158_subpoint_credits_mark_concept_point_taught(tmp_path, monkeypatch):
    env = concept_environment(tmp_path)
    monkeypatch.setattr(type(env.room), '_materials', staticmethod(lambda: env.materials))
    try:
        assert _taught(env).get('p0') is False
        _finish_first_concept(env)
        rows = env.store.records('credits', PLAN)
        assert not [row for row in rows if row['id'] == 'learn:p0']
        assert _taught(env)['p0'] is True
    finally:
        _close(env)
