"""Shared producer and preflight with real signed files; ACL adapter only."""
import os
from pathlib import Path
from unittest.mock import patch
import pytest
from desktop import update_request
from desktop.update_protocol import canonical,digest,SecurityError
from scripts import update_preflight,update_health,windows_install_trust as trust,windows_install_transaction as tx
from scripts.test_update_health import evidence
from scripts.test_mature_update_transaction import case


def prepared(feed):
    update_health.finalize(feed.install,evidence(feed.install))
    service=feed.service();assert service.check()['state']=='UPDATE_AVAILABLE'
    assert service.download()['state']=='READY_TO_APPLY'
    path,pin=update_request.produce(service.staged.parent,app_id='MentorFlow',install_root=feed.install,
        staged_app=service.staged,base_receipt=(feed.install/trust.RECEIPT).read_bytes(),release=service.release,
        coordinator_pid=os.getpid())
    return service,path,pin


def test_same_frozen_request_before_and_inside_helper(case):
    _,feed,_=case;service,path,pin=prepared(feed);raw=path.read_bytes()
    with patch.object(update_preflight,'trusted_keys',return_value=feed.keys):
        doc,release=update_preflight.accept(feed.install,path,pin)
        same,_=update_preflight.accept(feed.install,path,pin,verify_staging=False)
    assert doc==same and path.read_bytes()==raw
    assert release.version=='1.1.1'


@pytest.mark.parametrize('failure',['identity','root','version','signature','replay','tamper','unknown','expired'])
def test_negative_before_any_uac(case,failure):
    base,feed,_=case;service,path,pin=prepared(feed)
    doc=update_request.read(path.read_bytes())
    if failure=='identity':doc['install_identity']['volume_hex']='0'*16
    elif failure=='root':doc['install_root']=str(base/'wrong-root')
    elif failure=='version':doc['base_version']='1.0.9'
    elif failure=='expired':doc['expires_at']=doc['created_at']-1
    elif failure=='signature':
        release=service.staged.parent/'release.json';raw=release.read_bytes();release.write_bytes(raw.replace(b'"signature":"',b'"signature":"X',1))
        doc['release_sha256']=digest(release.read_bytes())
    elif failure=='replay':
        claim=feed.install/tx.STAGING/tx.UPDATE_METADATA/(doc['attempt_id']+'.claim.json')
        claim.parent.mkdir(parents=True,exist_ok=True);claim.write_bytes(b'{}')
    elif failure=='tamper':(service.staged/'MentorFlow.exe').write_bytes(b'tampered')
    elif failure=='unknown':(service.staged/'unknown-config.json').write_bytes(b'{}')
    # Negative input is a NEW fixture request, never a refreshed frozen attempt.
    negative=path.with_name('negative.request.json');negative.write_bytes(canonical(doc))
    with patch.object(update_preflight,'trusted_keys',return_value=feed.keys),pytest.raises((ValueError,RuntimeError)):
        update_preflight.accept(feed.install,negative,digest(negative.read_bytes()))
