"""Stable shortcut adapter for the existing signed consumer lifecycle."""
from scripts import per_user_install as install
from scripts import per_user_update_core as core
from desktop.update_protocol import strict_json, RecoverableError, SecurityError


def _recovery_health(root):
    """A writable recovery hint is usable only after native peer validation."""
    from desktop.windows_process_peer import ProcessPeer
    release = install.installed(root)
    doc = strict_json(install.read(root / 'last-recovery.json'))
    journal = strict_json(install.read(root / install.JOURNAL))
    proof = doc.get('health', {})
    if (doc.get('attempt') != journal.get('attempt') or
            journal.get('phase') != 'ROLLED_BACK' or
            doc.get('version') != release.version or
            proof.get('version') != release.version or proof.get('ready') is not True):
        raise SecurityError('recovery health does not match the current transaction')
    with ProcessPeer(proof['pid']) as peer:
        peer.require(executable=root / 'App/MentorFlow.exe', integrity_rid=8192)
        peer.visible_responsive_window(proof['hwnd_hex'])
    return proof


def launch(root):
    from desktop.update_download import InstallUpdateLock
    root = install.target(root)
    with InstallUpdateLock(root):
        install.require_app_stopped(root)
        if core.unsafe_teardown('next_launch'):
            raise RecoverableError('session ending: launch and update deferred')
        # Recovery shares the core's writer mutex. Release it before calling
        # apply, which acquires the same non-reentrant mutex itself.
        install.recover(root)
    if (root / core.PENDING).exists():
        pending = core.read_pending(root)
        if pending['state'] not in ('CURRENT_HEALTHY', 'PREVIOUS_HEALTHY'):
            try:
                # A successful apply already launches/health-checks TARGET.
                result = core.resume_pending_on_normal_launch(root, reason='next_launch')
                if result.get('state') == 'CURRENT_HEALTHY':
                    return result
            except (OSError, RuntimeError, ValueError):
                # Offline/withdrawn/invalid pending content cannot block the
                # signed current App. A rolled-back App is already running;
                # validate its live identity instead of launching a duplicate.
                if core.read_pending(root)['state'] == 'PREVIOUS_HEALTHY':
                    return dict(status='PREVIOUS_HEALTHY', health=_recovery_health(root))
    with InstallUpdateLock(root):
        if core.unsafe_teardown('next_launch'):
            raise RecoverableError('session ending: launch and update deferred')
        install.require_app_stopped(root)
        return dict(status='HEALTHY', health=install.launch_health(root, install.installed(root)))
