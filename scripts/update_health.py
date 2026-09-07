"""Finalize canonical receipt only after native target/process/window evidence.

Caller owns the bounded elevated transaction and its lock. Health messages are
not authority by themselves: actual IPC peer PID, token, image and window are
checked through the retained process handle before publication.
"""
import os
from pathlib import Path
import re
import time
import uuid

from desktop.update_protocol import canonical, SecurityError
from desktop.windows_identity import read_directory
from scripts import windows_install_trust as trust
from scripts.install_receipt_state import read_document, validate_installed_record

SCHEMA='mentorflow.process-window-health.v1'
FIELDS={'schema','attempt_id','nonce','version','manifest_sha256','install_identity','pid','hwnd_hex'}


def atomic_record(path,doc):
    path=Path(path);trust.assert_protected(path.parent)
    temp=path.with_name('record-'+uuid.uuid4().hex+'.pending')
    try:
        with trust._create_protected(temp,directory=False) as stream:
            stream.write(canonical(doc));stream.flush();os.fsync(stream.fileno())
        temp.replace(path)
    finally:temp.unlink(missing_ok=True)


def attest(root,doc,*,peer,attempt_id,nonce,version,manifest_sha256,not_before_hex):
    root=Path(root).absolute()
    if (not isinstance(doc,dict) or set(doc)!=FIELDS or doc['schema']!=SCHEMA or
            doc['attempt_id']!=attempt_id or doc['nonce']!=nonce or doc['version']!=version or
            doc['manifest_sha256']!=manifest_sha256 or type(doc['pid']) is not int or doc['pid']!=peer.pid or
            doc['install_identity']!=read_directory(root)):
        raise SecurityError('health attempt/nonce/version/manifest/root/peer mismatch')
    process=peer.require(executable=root/'App/MentorFlow.exe',integrity_rid=8192,not_before_hex=not_before_hex)
    window=peer.visible_responsive_window(doc['hwnd_hex'])
    # Protected current manifest remains the runtime identity, not the request's
    # version parameter. App cannot modify this file under the installed ACL.
    from scripts.windows_install_transaction import sha
    if sha(root/'App/SELF-CHECK/package-manifest.json')!=manifest_sha256:
        raise SecurityError('health current manifest changed')
    return dict(schema=SCHEMA,attempt_id=attempt_id,nonce=nonce,version=version,
        manifest_sha256=manifest_sha256,install_identity=doc['install_identity'],
        process=process,window=window,attested_at=int(time.time()))


def finalize(root,evidence,*,transaction=None):
    """Internal privileged caller supplies attest() result, never arbitrary CLI JSON."""
    if not __import__('ctypes').windll.shell32.IsUserAnAdmin():
        raise PermissionError('health receipt publication requires transaction elevation')
    root=Path(root).absolute();path=root/trust.RECEIPT
    trust.assert_protected(root);trust.assert_protected(path)
    receipt=validate_installed_record(read_document(path),root)
    binding=receipt['binding']
    if (receipt.get('schema_version')!=2 or receipt['state']!='PENDING_HEALTH' or
            evidence['schema']!=SCHEMA or evidence['version']!=binding['version'] or
            evidence['manifest_sha256']!=binding['manifest_sha256'] or
            evidence['install_identity']!=binding['root_identity']):
        raise SecurityError('health does not own the pending canonical receipt')
    healthy={**receipt,'state':'HEALTHY','health':evidence}
    validate_installed_record(healthy,root)
    from desktop.update_protocol import digest
    pin=digest(canonical(healthy))
    if transaction is not None:
        # Durable intent precedes the authority replacement. A crash here can
        # be distinguished from an unobserved first launch by the exact pin.
        transaction.journal['health_receipt_sha256']=pin
        transaction.journal['health_evidence']=evidence
        transaction.save()
    atomic_record(path,healthy)
    if transaction is not None:
        transaction.journal['pending_update_health']=False
        transaction.journal['commit_receipt_sha256']=pin
        transaction.save()
    return dict(state='HEALTHY',receipt_sha256=pin)


def reconcile_finalization(root):
    """Recover exact journal intent after healthy receipt atomic publication.

    Called under the existing elevated installer lock. Never infer health from
    file verification or a marker. The canonical receipt must contain the SAME
    protected attestation and exact intended bytes, already fully published.
    """
    from scripts import windows_install_transaction as tx
    root=Path(root)
    for journal_path in (root/tx.STAGING/tx.PREVIOUS).glob('*/journal.json'):
        tx.assert_no_alias(journal_path);trust.assert_protected(journal_path)
        journal=read_document(journal_path,1024**2)
        if not journal.get('pending_update_health') or not journal.get('health_receipt_sha256'):continue
        receipt_path=root/trust.RECEIPT
        if tx.sha(receipt_path)!=journal['health_receipt_sha256']:continue
        receipt=validate_installed_record(read_document(receipt_path),root)
        if (journal.get('root')!=str(root) or journal.get('schema')!='mentorflow.install-transaction.v1' or
                receipt.get('state')!='HEALTHY' or receipt.get('health')!=journal.get('health_evidence')):
            raise SecurityError('inconsistent protected health finalization intent')
        trust.verify_installation(root,**tx.identities_from_receipt(root),workers=1)
        journal['pending_update_health']=False
        journal['commit_receipt_sha256']=journal['health_receipt_sha256']
        atomic_record(journal_path,journal)
