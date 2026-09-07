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


def apply_update(root, source, envelope, *, channel='stable'):
    """Invoked by a qualified elevated launcher only, never by normal UI startup."""
    if not PRODUCTION_RELEASE_KEYS:
        raise SecurityError('production release trust is not configured')
    root, source = tx.validate_target(root), Path(source).absolute()
    with tx.installer_lock():
        for path in (root/'.update-previous').glob('*/journal.json'):
            tx.assert_no_alias(path); trust.assert_protected(path)
            if path.stat().st_size > 1024**2:
                raise SecurityError('oversized health journal')
            if json.loads(path.read_bytes()).get('pending_update_health'):
                raise RecoverableError('previous update still requires first-launch health qualification')
        previous = tx.identities_from_receipt(root)
        trust.verify_installation(root, **previous, workers=1)
        old = json.loads((root/'App'/'mentorflow-version.json').read_bytes())
        release = verify_release(envelope, PRODUCTION_RELEASE_KEYS,
                                 current=old['version'], channel=channel)
        if release.version == old['version']:
            raise SecurityError('same-version update is not an authorized repair')
        manifest_pin = tx.sha(source/'SELF-CHECK/package-manifest.json')
        from desktop.update_protocol import digest
        if manifest_pin != digest(release.manifest_bytes):
            raise SecurityError('staged manifest differs from signed release')
        from scripts.prepare_transactional_installer import verify
        verify(source, manifest_pin)
        ids = dict(manifest_sha256=manifest_pin, source_sha=release.signed['source_sha'],
                   tree_sha=release.signed['tree_sha'])
        transaction = tx.Transaction(source, root, ids, root/'Uninstall.exe', retain_previous=True)
        result = transaction.run()
        result.update(previous_retained=str(transaction.attempt), pending_update_health=True,
                      restart='NOT_IMPLEMENTED_NO_ELEVATED_APP_LAUNCH')
        return result


def rollback_previous(root, attempt_name, *, registry=None, shortcut=None):
    """Explicit elevated recovery, not automatic first-launch acceptance."""
    if not tx.c.windll.shell32.IsUserAnAdmin():
        raise PermissionError('rollback requires elevation')
    if len(attempt_name) != 32 or any(c not in '0123456789abcdef' for c in attempt_name):
        raise SecurityError('invalid previous transaction identifier')
    root = tx.validate_target(root)
    with tx.installer_lock():
        recovery = tx.Transaction(root/'App', root, {}, root/'Uninstall.exe', registry=registry, shortcut=shortcut)
        had_recovery = (root/tx.STAGING/attempt_name).exists()
        recovery.recover()
        if had_recovery and not (root/'.update-previous'/attempt_name).exists():
            trust.verify_installation(root, **tx.identities_from_receipt(root), workers=1)
            return {'status':'ROLLBACK_RECOVERED', 'root':str(root)}
        ids = tx.identities_from_receipt(root)
        trust.verify_installation(root, **ids, workers=1)
        attempt = root/'.update-previous'/attempt_name
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
        trust.verify_installation(root, **tx.identities_from_receipt(root), workers=1)
        return {'status':'ROLLED_BACK', 'root':str(root)}
