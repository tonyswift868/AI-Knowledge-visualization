"""Unsent answers survive replacement of Chromium and the account's services."""
from types import SimpleNamespace
import pytest
from refactor.learning.app.handlers import register
from refactor.learning.app.bridge import BridgeError


def draft_bridge160(tmp_path, account):
    methods = {}
    bridge = SimpleNamespace(register=lambda name, fn, **kw: methods.update({name: fn}), push=lambda *a: None)
    services = SimpleNamespace(account_id=account, data_root=tmp_path,
                               store=SimpleNamespace(get_plan=lambda key: {'id': 'p'} if key == 'p' else None))
    register(bridge, SimpleNamespace(services=services))
    return lambda method, **params: methods[method](params, None)


def test_classroom_draft160_reopened_account_recovers_and_other_account_cannot_read(tmp_path):
    first = draft_bridge160(tmp_path, 'draft-a')
    assert first('classroom.draft_set', plan_id='p', text='还没发送的答案') == {'saved': True}
    reopened = draft_bridge160(tmp_path, 'draft-a')
    assert reopened('classroom.draft_get', plan_id='p') == {'text': '还没发送的答案'}
    other = draft_bridge160(tmp_path, 'draft-b')
    assert other('classroom.draft_get', plan_id='p') == {'text': ''}
    assert reopened('classroom.draft_set', plan_id='p', text='') == {'saved': True}
    assert first('classroom.draft_get', plan_id='p') == {'text': ''}


def test_classroom_draft160_missing_plan_and_failed_write_do_not_claim_saved(tmp_path, monkeypatch):
    from refactor.learning.preferences.storage import AccountJSON, StorageError
    call = draft_bridge160(tmp_path, 'draft-a')
    with pytest.raises(BridgeError) as missing:
        call('classroom.draft_set', plan_id='missing', text='x')
    assert missing.value.code == 'PLAN_NOT_FOUND'
    monkeypatch.setattr(AccountJSON, 'write', lambda *a: (_ for _ in ()).throw(StorageError()))
    with pytest.raises(BridgeError) as failed:
        call('classroom.draft_set', plan_id='p', text='x')
    assert failed.value.code == 'CLASSROOM_DRAFT_UNAVAILABLE'
