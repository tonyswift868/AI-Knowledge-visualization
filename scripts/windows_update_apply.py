"""Protected update bridge contract, reusing the NSIS installation transaction.

Production feed keys and ordinary-user restart/UAC handoff are NOT provisioned.
The desktop therefore does not expose this as a qualified executable launcher.
No private keys, auto-elevation, process killing, or first-install wizard here.
"""
from __future__ import annotations
import json
from pathlib import Path

from desktop.update_protocol import verify_release, SecurityError, RecoverableError
from scripts import windows_install_transaction as tx
from scripts import windows_install_trust as trust

# Populated only by a future reviewed release build. Never read public keys from
# CLI arguments, downloaded metadata, a user cache, or environment variables.
PRODUCTION_RELEASE_KEYS: dict[str, bytes] = {}


def verify_committed(root):
    """Reuse whole-snapshot retry for delayed NTFS notifications, never skip it."""
    transaction = tx.Transaction(root/'App', root, tx.identities_from_receipt(root), root/'Uninstall.exe')
    return transaction.verify(root, None, False)


def protected_witness(root, channel, release=None):
    """Helper-owned network-replay floor; it cannot be reset by user cache edits."""
    from desktop.update_protocol import witness_version, digest, canonical
    import os
    if channel not in ('stable','beta'): raise SecurityError('invalid protected witness channel')
    base=root/tx.STAGING/tx.UPDATE_METADATA
    path=base/(channel+'.json')
    epoch, signed_hash=0,''
    if path.exists():
        tx.assert_no_alias(path); trust.assert_protected(path)
        if path.stat().st_size>16*1024**2: raise SecurityError('oversized protected witness')
        epoch,signed_hash=witness_version(path.read_bytes(),PRODUCTION_RELEASE_KEYS,channel=channel)
    if release is not None:
        incoming=release.signed['metadata_version']
        if incoming<epoch or incoming==epoch and signed_hash!=digest(canonical(release.signed)):
            raise SecurityError('protected metadata replay/equivocation')
        for directory in (root/tx.STAGING,base):
            if not directory.exists(): trust._create_protected(directory,directory=True)
            tx.assert_no_alias(directory); trust.assert_protected(directory)
        temporary=base/(channel+'.pending')
        if temporary.exists():
            tx.assert_no_alias(temporary); trust.assert_protected(temporary)
            temporary.unlink()  # exact protected pending witness, never a program/data file
        with trust._create_protected(temporary,directory=False) as stream:
            stream.write(release.envelope); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(path)
    return epoch,signed_hash


def apply_update(root, source, envelope, *, channel='stable', on_committed=None):
    """Invoked by a qualified elevated launcher only, never by normal UI startup."""
    if not tx.c.windll.shell32.IsUserAnAdmin():
        raise PermissionError('protected update requires explicit elevation')
    if not PRODUCTION_RELEASE_KEYS:
        raise SecurityError('production release trust is not configured')
    root, source = tx.validate_target(root), Path(source).absolute()
    with tx.installer_lock():
        tx.Transaction(source, root, {}, root/'Uninstall.exe').recover()
        from scripts.update_health import reconcile_finalization
        reconcile_finalization(root)
        for path in (root/tx.STAGING/tx.PREVIOUS).glob('*/journal.json'):
            tx.assert_no_alias(path); trust.assert_protected(path)
            if path.stat().st_size > 1024**2:
                raise SecurityError('oversized health journal')
            if json.loads(path.read_bytes()).get('pending_update_health'):
                raise RecoverableError('previous update still requires first-launch health qualification')
        previous = tx.identities_from_receipt(root)
        trust.verify_installation(root, **previous, workers=1)
        old = json.loads((root/'App'/'mentorflow-version.json').read_bytes())
        release = verify_release(envelope, PRODUCTION_RELEASE_KEYS,
                                 current=old['version'], channel=channel,
                                 highest_seen=protected_witness(root,channel)[0])
        from desktop.update_protocol import rollout_eligible, digest
        import os
        if not rollout_eligible(release,digest(os.path.normcase(str(root)).encode('utf-8'))):
            protected_witness(root,channel,release)
            raise SecurityError('withdrawn/ineligible release cannot apply')
        if release.version == old['version']:
            raise SecurityError('same-version update is not an authorized repair')
        manifest_pin = tx.sha(source/'SELF-CHECK/package-manifest.json')
        from desktop.update_protocol import digest
        if manifest_pin != digest(release.manifest_bytes):
            raise SecurityError('staged manifest differs from signed release')
        # Transaction copies every signed file into NEW protected staging and
        # hashes it before the protected full verification/commit. User-writable
        # staging never becomes executable authority. This accepts immutable
        # incremental hardlinks without chmod/ACL mutation of current/previous.
        version_doc = json.loads((source/'mentorflow-version.json').read_bytes())
        if version_doc.get('product') != 'MentorFlow' or version_doc.get('version') != release.version:
            raise SecurityError('target version file differs from signed release')
        protected_witness(root,channel,release)
        ids = dict(manifest_sha256=manifest_pin, source_sha=release.signed['source_sha'],
                   tree_sha=release.signed['tree_sha'])
        transaction = tx.Transaction(source, root, ids, root/'Uninstall.exe', retain_previous=True,signed_source=True)
        result = transaction.run()
        result.update(previous_retained=str(transaction.attempt), pending_update_health=True,
                      restart='NOT_IMPLEMENTED_NO_ELEVATED_APP_LAUNCH')
        if on_committed is not None:
            # Keep this helper and its installer lock ALIVE through first
            # launch/finalize/recovery. The coordinator must react to the
            # committed event, never wait for helper exit to launch the target.
            result.update(on_committed(transaction,result))
        return result


def rollback_previous(root, attempt_name, *, registry=None, shortcut=None, _lock_held=False):
    """Explicit elevated recovery, not automatic first-launch acceptance."""
    if not tx.c.windll.shell32.IsUserAnAdmin():
        raise PermissionError('rollback requires elevation')
    if len(attempt_name) != 32 or any(c not in '0123456789abcdef' for c in attempt_name):
        raise SecurityError('invalid previous transaction identifier')
    root = tx.validate_target(root)
    from contextlib import nullcontext
    with nullcontext() if _lock_held else tx.installer_lock():
        recovery = tx.Transaction(root/'App', root, {}, root/'Uninstall.exe', registry=registry, shortcut=shortcut)
        had_recovery = (root/tx.STAGING/attempt_name).exists()
        recovery.recover()
        if had_recovery and not (root/tx.STAGING/tx.PREVIOUS/attempt_name).exists():
            verify_committed(root)
            return {'status':'ROLLBACK_RECOVERED', 'root':str(root)}
        ids = tx.identities_from_receipt(root)
        trust.verify_installation(root, **ids, workers=1)
        attempt = root/tx.STAGING/tx.PREVIOUS/attempt_name
        tx.assert_no_alias(attempt); trust.assert_protected(attempt)
        journal_path = attempt/'journal.json'
        tx.assert_no_alias(journal_path); trust.assert_protected(journal_path)
        if journal_path.stat().st_size > 1024**2:
            raise SecurityError('oversized rollback journal')
        journal = json.loads(journal_path.read_bytes())
        if (journal.get('schema') != 'mentorflow.install-transaction.v1' or
            journal.get('root') != str(root) or not journal.get('retain_previous') or
            journal.get('phase') != 'COMMITTED' or
            tx.sha(root/trust.RECEIPT) != journal.get('commit_receipt_sha256')):
            raise SecurityError('previous journal does not own current install')
        prior = attempt/'prior-program'
        old_receipt = json.loads((prior/trust.RECEIPT).read_bytes())
        from scripts.prepare_transactional_installer import verify
        verify(prior/'App', old_receipt['binding']['manifest_sha256'])
        if tx.sha(prior/'Uninstall.exe') != old_receipt['control_hashes']['Uninstall.exe']:
            raise SecurityError('previous control modified')
        transaction = tx.Transaction(root/'App', root, ids, root/'Uninstall.exe', registry=registry, shortcut=shortcut)
        stagebase = root/tx.STAGING
        if not stagebase.exists(): trust._create_protected(stagebase,directory=True)
        tx.assert_no_alias(stagebase); trust.assert_protected(stagebase)
        destination = stagebase/attempt_name
        if destination.exists(): raise SecurityError('rollback staging occupied')
        # Before the intent is saved, recovery merely re-retains the committed
        # previous tree. Afterward the existing rollback journal is resumable.
        attempt.rename(destination)
        transaction.attempt, transaction.journal = destination, journal
        journal['phase']='ROLLING_BACK'; journal['rollback_requested']=True
        transaction.save()
        transaction.rollback()
        verify_committed(root)
        return {'status':'ROLLED_BACK', 'root':str(root)}


def observe_first_launch(process, *, expected_version, ready_probe, timeout=10.0):
    """Observe an already-owned ordinary-user process; never launch as admin.

    Result is evidence, not an instruction to mark a protected receipt healthy.
    Production nonce/window/binary attestation handoff remains unqualified.
    """
    import time
    if not 0 < timeout <= 60:
        raise ValueError('invalid health observation timeout')
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if process.poll() is not None:
            return dict(state='FAILED_FIRST_LAUNCH',exit_code=process.returncode,rollback_required=True)
        ready=ready_probe()
        if ready is not None:
            if (ready.get('pid') != process.pid or ready.get('version') != expected_version
                    or ready.get('ready') is not True):
                raise SecurityError('health signal identity mismatch')
            return dict(state='READY_OBSERVED',pid=process.pid,pending_privileged_attestation=True)
        time.sleep(.01)
    return dict(state='FAILED_FIRST_LAUNCH',reason='ready timeout',rollback_required=True)


def main():
    import argparse
    parser=argparse.ArgumentParser(description='Explicit protected previous-version recovery; no automatic elevation')
    parser.add_argument('command',choices=['rollback'])
    parser.add_argument('--install-root',type=Path,required=True)
    parser.add_argument('--attempt',required=True)
    args=parser.parse_args()
    print(json.dumps(rollback_previous(args.install_root,args.attempt)))


if __name__=='__main__': main()
