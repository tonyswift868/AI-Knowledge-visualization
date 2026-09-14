from app.enterprise_tenancy.sync_registry import SYNC_ITEM_REGISTRY, SYNC_KINDS

def test_single_registry_covers_learning_world_and_stable_projection_names():
    assert SYNC_KINDS == {'plan','progress','resource','task','calendar','review',
                          'learner_profile','learning_run','history','setting'}
    assert len({a.domain for a in SYNC_ITEM_REGISTRY.values()}) == len(SYNC_KINDS)

def test_domain_adapters_preserve_business_payload_without_second_model():
    payload = {'stable_id': 'task-1', 'title': 'read', 'done': False}
    assert SYNC_ITEM_REGISTRY['task'].project(payload) == payload
