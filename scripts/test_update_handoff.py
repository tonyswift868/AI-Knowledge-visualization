"""Control-order/rollback unit tests; transport/window adapter is synthetic.

Actual file transaction runs here. This is NOT packaged or protected evidence.
"""
from pathlib import Path
from unittest.mock import patch
import pytest
from scripts.test_mature_update_transaction import case
from scripts.test_install_process_recovery import Registry,Shortcut
from scripts import windows_update_apply as engine,windows_install_transaction as tx,update_handoff,update_health


class Peer:
    def __init__(self,pid):self.pid=pid;self.closed=False
    def close(self):self.closed=True


class Transport:
    def __init__(self,fail):self.events=[];self.fail=fail;self.launches=0;self.peers=[]
    def send(self,kind,**values):self.events.append(kind)
    def started(self,binding,*,timeout):
        assert self.events[-1] in ('APPLY_COMMITTED','ROLLBACK_COMMITTED')
        self.launches+=1;peer=Peer(self.launches);self.peers.append(peer)
        self.events.append('STARTED');return peer
    def health(self,peer,binding,attempt,nonce,*,timeout):
        if self.fail and self.launches==1:raise TimeoutError('synthetic bad first launch')
        self.events.append('HEALTH');return {'scope':'SYNTHETIC_TRANSPORT_ONLY'}
    def stop_owned(self,peer,*,timeout):self.events.append('STOPPED')
    def confirm_no_unreported_target(self,*,timeout):raise AssertionError('unexpected ownership loss')


@pytest.mark.parametrize('fail',[False,True])
def test_commit_before_launch_before_exit_and_same_lock_recovery(case,fail):
    base,feed,_=case
    service=feed.service();service.check();service.download()
    transport=Transport(fail)
    def finish(transaction,result):
        # Prove the helper still owns the installer lock at APPLY_COMMITTED.
        with pytest.raises(ValueError):
            with tx.installer_lock():pass
        return update_handoff.committed(transaction,result,attempt_id='e'*32,nonce='f'*64,transport=transport)
    with patch.object(engine,'PRODUCTION_RELEASE_KEYS',feed.keys),patch.object(tx,'Registry',lambda:Registry(base)),\
         patch.object(tx,'Shortcut',lambda _:Shortcut(base)),\
         patch.object(update_health,'finalize',return_value={'receipt_sha256':'a'*64}) as finalize:
        result=engine.apply_update(feed.install,service.staged,service.release.envelope,on_committed=finish)
    assert transport.events[0]=='APPLY_COMMITTED'
    assert all(peer.closed for peer in transport.peers)
    if fail:
        assert transport.events==['APPLY_COMMITTED','STARTED','STOPPED','ROLLBACK_COMMITTED','STARTED','HEALTH','PREVIOUS_HEALTHY']
        assert result['failed_update_recovered'] is True
        assert (feed.install/'App/mentorflow-version.json').read_text().find('1.1.0')>=0
        finalize.assert_not_called()
    else:
        assert transport.events==['APPLY_COMMITTED','STARTED','HEALTH','TARGET_HEALTHY']
        finalize.assert_called_once()
        assert Path(result['previous_retained']).is_dir()
