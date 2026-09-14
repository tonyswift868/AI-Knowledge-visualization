import pytest
from app.enterprise_tenancy.profile_settings_store import LocalProfileSettingsStore, outbox
from app.enterprise_tenancy.profile_settings_sync import ProfileSettingsRejected

S = dict(account_id='a', organization_id='o', workspace_id='w')

def test_multi_settings_export_and_duplicate_apply_are_safe():
    s=LocalProfileSettingsStore(scope=S); s.mutate(**S, setting_updates={'locale':'en','theme':'dark'})
    snap=s.export_snapshot(**S); assert snap[0]['revision'] == 1
    assert s.apply_snapshot(snap, **S)

def test_server_revision_tombstone_and_complete_replace():
    s=LocalProfileSettingsStore(scope=S)
    s.apply_snapshot([{'kind':'setting','entity_id':'a','revision':10,'deleted':False,'payload':{'theme':'dark','locale':'en'}}], **S)
    assert s.export_snapshot(**S)[0]['revision'] == 10
    s.apply_snapshot([{'kind':'setting','entity_id':'a','revision':11,'deleted':False,'payload':{'theme':'light'}}], **S)
    assert s.read_settings(**S) == {'theme':'light'}
    s.apply_snapshot([{'kind':'learner_profile','entity_id':'a','revision':12,'deleted':False,'payload':{'display_name':'x'}}], **S)
    s.apply_snapshot([{'kind':'learner_profile','entity_id':'a','revision':13,'deleted':True,'payload':{}}], **S)
    assert s.read_profile(**S) == {}

def test_request_id_payload_conflict_rolls_back_and_revision_content_is_not_duplicate():
    s=LocalProfileSettingsStore(scope=S); s.mutate(**S, setting_updates={'theme':'dark'}, request_id='same')
    with pytest.raises(ProfileSettingsRejected, match='request_id_payload_conflict'):
        s.mutate(**S, setting_updates={'theme':'light'}, request_id='same')
    assert s.read_settings(**S) == {'theme':'dark'}
    with pytest.raises(ProfileSettingsRejected, match='stale_revision'):
        s.apply_item({'kind':'setting','entity_id':'a','revision':1,'payload':{'theme':'light'}}, scope=S)

def test_empty_export_is_not_self_rejected():
    assert LocalProfileSettingsStore(scope=S).export_snapshot(**S) == []

def test_invalid_second_snapshot_item_rolls_back_first():
    s=LocalProfileSettingsStore(scope=S)
    with pytest.raises((ProfileSettingsRejected, ValueError)):
        s.apply_snapshot([{'kind':'setting','entity_id':'a','revision':1,'payload':{'theme':'dark'}}, {'kind':'learner_profile','entity_id':'a','revision':0,'payload':{}}], **S)
    assert s.read_settings(**S) == {}
