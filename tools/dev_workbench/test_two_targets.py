import json
from tools.dev_workbench import authority as a
from tools.dev_workbench.test_workbench import source,candidate,raw
from desktop.test_device_profile import read_profile
from tools.dev_workbench.test_devices import devices


def configure(source,value,qualified):
    receipt='docs/governance/receipts/mvp.json'
    source.files[receipt]=raw({'test_target':value})
    board=json.loads(source.files[a.BOARD]);board['workbench']={
        'current_test_id':value['test_id'],'latest_development_test_id':value['test_id'],
        'latest_qualified_test_id':value['test_id'] if qualified else None,
        'test_targets':[{'receipt_path':receipt,'receipt_sha256':a.sha256(source.files[receipt]),'test_id':value['test_id']}]}
    source.files[a.BOARD]=raw(board)
    source.git=lambda *args:value['source_sha']


def test_development_never_implies_user_acceptance(source,candidate):
    value,paths=candidate;configure(source,value,False)
    view=a.load_view(source,paths)
    assert view['latest_development']==value and view['latest_qualified'] is None
    assert view['same_artifact'] is False


def test_same_artifact_keeps_both_authorities(source,candidate):
    value,paths=candidate;configure(source,value,True)
    view=a.load_view(source,paths)
    assert view['latest_development']==view['latest_qualified']==value
    assert view['same_artifact'] is True


def test_superseded_receipt_cannot_be_qualified(source,candidate):
    value,paths=candidate;value={**value,'status':'SUPERSEDED'};configure(source,value,True)
    assert a.load_view(source,paths)['latest_qualified'] is None


def test_user_test_reuses_owned_storage_without_reset(devices):
    profile=devices.prepare('UserTest');root=devices.root('UserTest')
    (root/'vault/prelaunch/data/sentinel').write_text('retained')
    assert devices.prepare('UserTest')==profile
    assert read_profile(root,base=devices.base)==profile
    assert (root/'vault/prelaunch/data/sentinel').read_text()=='retained'
