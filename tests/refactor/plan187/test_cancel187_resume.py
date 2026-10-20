from refactor.learning.connections.errors import ModelCancelled
from tests.refactor.plan187.test_plan187_material_recovery import env, PLAN, wait_for


def test_cancel187_ordinary_cancel_is_durable_and_resumes_with_new_event(env, monkeypatch):
    calls, persisted, events = [], [], []
    update = env.store.update_module_preparation_state

    def record(pid, mid, state, **details):
        persisted.append(state)
        return update(pid, mid, state, **details)

    monkeypatch.setattr(env.store, 'update_module_preparation_state', record)

    def prepare(_store, _pid, mid, ctx, _progress):
        calls.append(mid)
        events.append(ctx['cancel'])
        if len(calls) == 1:
            ctx['cancel'].set()
            raise ModelCancelled()
        assert not ctx['cancel'].is_set()
        env.ready.add(mid)
        return {}

    module = dict(env.store.records('modules', PLAN)[0], points=[dict(id='p0')])
    env.flow._prepare_remaining(PLAN, [module], prepare)
    wait_for(lambda: PLAN not in env.flow._preparing)
    assert calls == ['m0', 'm0']
    assert events[0] is not events[1]
    assert 'cancelled' in persisted
    assert env.store.records('modules', PLAN)[0]['material_preparation']['state'] == 'done'
