"""Health publication/crash boundaries with real files, synthetic native proof.

Normal-user ACL adapters only; not elevated or real-window acceptance.
"""
import time
from unittest.mock import patch
import pytest
from desktop.update_protocol import canonical,digest
from scripts.test_mature_update_transaction import case
from scripts import update_health,windows_install_trust as trust,windows_install_transaction as tx,windows_update_apply as engine
from scripts.test_install_process_recovery import Registry,Shortcut
from scripts.install_receipt_state import read_document


def evidence(root):
    binding=read_document(root/trust.RECEIPT)['binding']
    return dict(schema=update_health.SCHEMA,attempt_id='1'*32,nonce='2'*64,
        version=binding['version'],manifest_sha256=binding['manifest_sha256'],install_identity=binding['root_identity'],
        process=dict(pid=123,executable=str(root/'App/MentorFlow.exe'),created_filetime_hex='0'*15+'1',
                     integrity_rid=8192,elevated=False,alive=True),
        window=dict(hwnd_hex='0'*15+'1',window_pid=123,visible=True,responsive=True),attested_at=int(time.time()))


def test_hash_only_install_pending_and_health_publish(case):
    _,feed,_=case;root=feed.install
    assert read_document(root/trust.RECEIPT)['state']=='PENDING_HEALTH'
    result=update_health.finalize(root,evidence(root))
    assert result['receipt_sha256']==tx.sha(root/trust.RECEIPT)
    assert read_document(root/trust.RECEIPT)['state']=='HEALTHY'
    trust.verify_installation(root,**tx.identities_from_receipt(root),workers=1)


@pytest.mark.parametrize('change',[{'version':'9.0.0'},{'manifest_sha256':'0'*64},{'process':{}}])
def test_invalid_health_cannot_publish(case,change):
    _,feed,_=case;path=feed.install/trust.RECEIPT;before=path.read_bytes()
    with pytest.raises((ValueError,RuntimeError)):
        update_health.finalize(feed.install,{**evidence(feed.install),**change})
    assert path.read_bytes()==before


def test_reconcile_exact_durable_health_intent(case):
    base,feed,_=case
    service=feed.service();service.check();service.download()
    def committed(transaction,result):
        proof=evidence(feed.install)
        original_save=transaction.save;calls=0
        def save():
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('synthetic post-receipt journal failure')
            original_save()
        with patch.object(transaction,'save',save):
            with pytest.raises(OSError):update_health.finalize(feed.install,proof,transaction=transaction)
        assert read_document(transaction.attempt/'journal.json')['pending_update_health'] is True
        update_health.reconcile_finalization(feed.install)
        journal=read_document(transaction.attempt/'journal.json')
        assert journal['pending_update_health'] is False
        assert journal['commit_receipt_sha256']==tx.sha(feed.install/trust.RECEIPT)
        return {}
    with patch.object(engine,'PRODUCTION_RELEASE_KEYS',feed.keys),patch.object(tx,'Registry',lambda:Registry(base)),patch.object(tx,'Shortcut',lambda _:Shortcut(base)):
        engine.apply_update(feed.install,service.staged,service.release.envelope,on_committed=committed)
