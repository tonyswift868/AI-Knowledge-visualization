import pytest
from .profile_settings_sync import *

def test_roundtrip_and_idempotent_apply():
    s = ProfileSettingsSnapshot.create('a','w',2,{'locale':'zh-CN','theme':'light'})
    assert s.apply('a','w',0,{}) == (2, {'locale':'zh-CN','theme':'light'})
    assert s.apply('a','w',2,dict(s.values)) == (2, dict(s.values))

@pytest.mark.parametrize('key', ['password','access_token','credential_blob','unknown'])
def test_sensitive_and_unknown_rejected(key):
    with pytest.raises(ProfileSettingsRejected): ProfileSettingsSnapshot.create('a','w',0,{key:'x'})

def test_wrong_scope_zero_mutation_and_stale_rejected():
    s=ProfileSettingsSnapshot.create('a','w',1,{'locale':'en'})
    assert s.apply('b','w',4,{'theme':'x'}) == (4, {'theme':'x'})
    with pytest.raises(ProfileSettingsRejected): s.apply('a','w',2,{})
