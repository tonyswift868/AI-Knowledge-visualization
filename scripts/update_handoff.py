"""Bounded helper lifecycle: commit event precedes target launch and helper exit.

The shared transaction retains the installer lock while this callback runs.
Transport owns only peer-checked IPC and process handles; this module owns the
decision to finalize or recover. It never launches a process as administrator.
"""
from pathlib import Path
import time

from desktop.update_protocol import SecurityError
from scripts import windows_install_transaction as tx
from scripts import windows_install_trust as trust
from scripts import windows_update_apply as apply
from scripts import update_health
from scripts.install_receipt_state import read_document


def committed(transaction,result,*,attempt_id,nonce,transport):
    root=transaction.root
    receipt=read_document(root/trust.RECEIPT)
    current=receipt['binding']
    record=dict(schema='mentorflow.update-lifecycle.v2',attempt_id=attempt_id,nonce=nonce,
                phase='APPLY_COMMITTED',events=[],initial_receipt_sha256=tx.sha(root/trust.RECEIPT))
    metadata=root/tx.STAGING/tx.UPDATE_METADATA
    if not metadata.exists():trust._create_protected(metadata,directory=True)
    tx.assert_no_alias(metadata);trust.assert_protected(metadata)
    evidence=metadata/(attempt_id+'.lifecycle.json')
    if evidence.exists():raise SecurityError('update attempt replay refused')
    started=time.monotonic()

    def phase(name,**values):
        record['phase']=name
        record['events'].append(dict(event=name,ms=round((time.monotonic()-started)*1000,3),**values))
        update_health.atomic_record(evidence,record)

    phase('APPLY_COMMITTED')
    target=None
    try:
        # This message must be observable while the helper is still alive.
        transport.send('APPLY_COMMITTED',binding=current)
        target=transport.started(current,timeout=30)
        phase('TARGET_STARTED',pid=target.pid)
        health=transport.health(target,current,attempt_id,nonce,timeout=60)
        healthy=update_health.finalize(root,health,transaction=transaction)
        phase('TARGET_HEALTHY',health=health)
        transport.send('TARGET_HEALTHY',receipt_sha256=healthy['receipt_sha256'])
        return dict(pending_update_health=False,restart='TARGET_HEALTHY',health=health,
                    lifecycle_evidence=str(evidence))
    except Exception as error:
        # Receipt publication is the health commit boundary. A later journal
        # flush or IPC acknowledgement failure must not roll back a version
        # whose exact protected attestation has already been committed.
        published=read_document(root/trust.RECEIPT)
        if (published.get('state')=='HEALTHY' and
                published.get('binding')==current and
                published.get('health',{}).get('attempt_id')==attempt_id and
                published.get('health',{}).get('nonce')==nonce):
            update_health.reconcile_finalization(root)
            raise
        # Record a bounded error category, never arbitrary account/CLI data.
        phase('FIRST_LAUNCH_FAILED',error_type=type(error).__name__)
        if target is not None:
            transport.stop_owned(target,timeout=10)
        else:
            # The peer must explicitly account for a failed launch and ensure
            # it has not left an unreported child locking App. A disconnected
            # coordinator is not fabricated success; preserve the pending txn.
            transport.confirm_no_unreported_target(timeout=10)
        phase('ROLLBACK_REQUIRED')
        recovery=apply.rollback_previous(root,transaction.attempt.name,
            registry=transaction.registry,shortcut=transaction.shortcut,_lock_held=True)
        old=read_document(root/trust.RECEIPT)['binding']
        phase('ROLLBACK_COMMITTED')
        transport.send('ROLLBACK_COMMITTED',binding=old)
        previous=transport.started(old,timeout=30)
        try:
            health=transport.health(previous,old,attempt_id,nonce,timeout=60)
            phase('PREVIOUS_HEALTHY',health=health)
            transport.send('PREVIOUS_HEALTHY',receipt_sha256=tx.sha(root/trust.RECEIPT))
            return dict(pending_update_health=False,restart='PREVIOUS_HEALTHY',
                failed_update_recovered=True,recovery=recovery,health=health,lifecycle_evidence=str(evidence))
        finally:previous.close()
    finally:
        if target is not None:target.close()
