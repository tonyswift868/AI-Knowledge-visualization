import pytest

from app.enterprise_tenancy.rehydration_dispatcher import (
    DispatchContext, RehydrationDispatcher, RehydrationRejected,
)


class Adapter:
    def __init__(self): self.items = []
    def apply(self, item, *, scope, store):
        self.items.append((item['kind'], item['entity_id'], dict(scope)))
        return item['entity_id']


def ctx():
    return DispatchContext('iss', 'sub', 'org', 'ws', 'snap-1')


def snapshot(items):
    return {'complete': True, 'snapshot_revision': 'snap-1', 'snapshot_scope': {
        'issuer': 'iss', 'subject_id': 'sub', 'organization_id': 'org', 'workspace_id': 'ws'},
        'entities': items}


def item(kind, entity='one', deleted=False):
    return {'kind': kind, 'entity_id': entity, 'revision': 1, 'deleted': deleted, 'payload': {} if not deleted else {}}


def test_dispatches_all_supported_kinds_without_business_side_effects():
    adapters = {kind: Adapter() for kind in ('plan', 'calendar', 'learner_profile', 'learning_run', 'history', 'setting')}
    result = RehydrationDispatcher(adapters).apply(snapshot([item(k, k) for k in adapters]), context=ctx(), store=object())
    assert result == tuple(adapters[k].items[0][1] for k in adapters)
    assert all(row[2] == ctx().scope for a in adapters.values() for row in a.items)


def test_wrong_scope_and_unknown_kind_fail_closed():
    adapter = Adapter(); dispatcher = RehydrationDispatcher({'plan': adapter})
    wrong = snapshot([item('plan')]); wrong['snapshot_scope']['subject_id'] = 'other'
    with pytest.raises(RehydrationRejected, match='WRONG_REHYDRATION_SCOPE'):
        dispatcher.apply(wrong, context=ctx(), store=object())
    with pytest.raises(RehydrationRejected, match='UNSUPPORTED_SYNC_KIND'):
        dispatcher.apply(snapshot([item('resource')]), context=ctx(), store=object())


def test_duplicate_and_missing_payload_fail_closed():
    dispatcher = RehydrationDispatcher({'plan': Adapter()})
    with pytest.raises(RehydrationRejected, match='DUPLICATE'):
        dispatcher.apply(snapshot([item('plan'), item('plan')]), context=ctx(), store=object())
    with pytest.raises(RehydrationRejected, match='PAYLOAD'):
        dispatcher.apply(snapshot([{'kind': 'plan', 'entity_id': 'x', 'revision': 1, 'deleted': False}]), context=ctx(), store=object())


def test_tombstone_is_dispatchable_and_unknown_adapter_rejected():
    with pytest.raises(RehydrationRejected, match='UNSUPPORTED_ADAPTER_KIND'):
        RehydrationDispatcher({'not-a-kind': Adapter()})
    adapter = Adapter()
    assert RehydrationDispatcher({'plan': adapter}).apply(snapshot([item('plan', deleted=True)]), context=ctx(), store=object()) == ('one',)
