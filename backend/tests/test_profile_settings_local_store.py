import pytest
from app.enterprise_tenancy.profile_settings_store import LocalProfileSettingsStore

S = dict(account_id='a', organization_id='o', workspace_id='w')

def test_real_store_mutation_outbox_and_read_path():
    a = LocalProfileSettingsStore(); a.mutate(**S, profile={'display_name':'A'}, setting_updates={'locale':'zh-CN','theme':'dark','daily_minutes':30}, unset=['timezone'], request_id='req-1')
    assert a.read_profile(**S) == {'display_name':'A'}
    assert a.read_settings(**S) == {'locale':'zh-CN','theme':'dark','daily_minutes':30}
    pending = a.pending(); assert len(pending) == 2 and all(x['status']=='PENDING' for x in pending)
    assert a.retry(pending[0]['request_id']) and a.pending()[0]['request_id'] == pending[0]['request_id']
    assert a.ack(pending[0]['request_id']) and len(a.pending()) == 1

def test_fresh_b_apply_and_duplicate_is_idempotent_read_path():
    a, b = LocalProfileSettingsStore(scope=S), LocalProfileSettingsStore(scope=S); a.mutate(**S, profile={'display_name':'A'}, setting_updates={'locale':'zh-CN'})
    b.apply_snapshot(a.export_snapshot(**S), **S)
    first = (b.read_profile(**S), b.read_settings(**S));
    b.apply_snapshot(a.export_snapshot(**S), **S)
    assert (b.read_profile(**S), b.read_settings(**S)) == first

@pytest.mark.parametrize('scope', [dict(account_id='other', organization_id='o', workspace_id='w'), dict(account_id='a', organization_id='other', workspace_id='w'), dict(account_id='a', organization_id='o', workspace_id='other')])
def test_wrong_scope_does_not_mutate(scope):
    b = LocalProfileSettingsStore(scope=S);
    with pytest.raises(Exception): b.apply_item({'kind':'setting','entity_id':'a','payload':{'locale':'zh-CN'}}, scope=scope)
    assert b.read_settings(**S) == {}

def test_secret_rejected_before_store_or_outbox():
    a = LocalProfileSettingsStore()
    with pytest.raises(Exception): a.mutate(**S, profile={'password':'x'}, setting_updates={'token':'y'})
    assert not a.pending() and a.read_profile(**S) == {}

def test_mutation_failure_rolls_back_store_and_outbox():
    a = LocalProfileSettingsStore(); a.mutate(**S, profile={'display_name':'A'}, request_id='base')
    before = (a.read_profile(**S), a.read_settings(**S), len(a.pending()))
    with pytest.raises(Exception): a.mutate(**S, setting_updates={'locale': object()}, request_id='bad')
    assert (a.read_profile(**S), a.read_settings(**S), len(a.pending())) == before
