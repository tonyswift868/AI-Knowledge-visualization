"""Public consumer lifecycle facade. Receipts describe state, never authority.

UI calls run on its existing worker. No UI, credentials or elevation are created
here. A downloaded update may be deferred and revalidated on a normal launch.
"""
from pathlib import Path
import threading

from desktop.update_protocol import canonical, digest, strict_json, RecoverableError, SecurityError
from scripts import per_user_install as install

PENDING = 'pending-update.json'
SCHEMA = 'mentorflow.per-user-pending.v1'
STATES = frozenset(('UPDATE_DOWNLOADED', 'PENDING_INSTALL', 'APPLYING', 'CURRENT_HEALTHY', 'PREVIOUS_HEALTHY'))
_ending = threading.Event()


def unsafe_teardown(reason='normal'):
    if reason not in ('normal', 'next_launch', 'shutdown', 'logoff', 'unsafe'):
        raise ValueError('unknown exit reason')
    import ctypes
    return reason in ('shutdown', 'logoff', 'unsafe') or _ending.is_set() or bool(ctypes.windll.user32.GetSystemMetrics(0x2000))


def attach_shutdown_guard(application):
    """Keep the Windows shutdown/logoff notification independent of update UI."""
    from PySide6.QtCore import QAbstractNativeEventFilter
    import ctypes
    from ctypes import wintypes
    class Guard(QAbstractNativeEventFilter):
        def nativeEventFilter(self, event_type, message):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message in (0x11, 0x16):  # WM_QUERYENDSESSION / WM_ENDSESSION
                _ending.set()
            return False, 0
    guard = Guard()
    application.installNativeEventFilter(guard)
    application._per_user_shutdown_guard = guard


def configured_service(root):
    root = install.target(root)
    release = install.installed(root)
    install.verify_tree(root/'App', release, subset=['mentorflow-version.json'])
    from desktop.update_service import configured_mature_service
    manifest = strict_json(install.read(root/'App/mentorflow-version.json'))
    service = configured_mature_service(version_manifest=manifest, install_root=root)
    if service is None: raise RecoverableError('signed update feed is not configured')
    return service


def remember_download(root, stage, envelope, *, state='UPDATE_DOWNLOADED'):
    if state not in ('UPDATE_DOWNLOADED', 'PENDING_INSTALL'): raise ValueError('invalid pending state')
    root = install.target(root); stage = install.plain(stage)
    _, release = install.admit(root, envelope, install.keys())
    install.verify_tree(stage, release)
    doc = dict(schema=SCHEMA, state=state, stage=str(stage), target=release.version,
               release_sha256=digest(envelope))
    install.atomic(root/PENDING, canonical(doc))
    return dict(state=state, target=release.version)


def read_pending(root):
    doc = strict_json(install.read(Path(root)/PENDING))
    if set(doc) != {'schema', 'state', 'stage', 'target', 'release_sha256'} or doc['schema'] != SCHEMA or doc['state'] not in STATES:
        raise SecurityError('unknown pending update state')
    return doc


def status(root):
    root = install.target(root)
    current = install.installed(root)
    result = dict(state='CURRENT_HEALTHY', current=current.version)
    journal = root/install.JOURNAL
    if journal.exists():
        phase = strict_json(install.read(journal)).get('phase')
        if phase == 'ROLLED_BACK': result['state'] = 'PREVIOUS_HEALTHY'
        elif phase != 'HEALTHY': result['state'] = 'APPLYING'
    if (root/PENDING).exists():
        pending = read_pending(root)
        if pending['state'] not in ('CURRENT_HEALTHY', 'PREVIOUS_HEALTHY'):
            result.update(state=pending['state'], target=pending['target'])
    return result


def prepare_update(root, *, service=None):
    """Existing signed downloader, Range/resume/cache/full-fallback unchanged.

    Passing an already configured service lets UI own progress, cancel and retry.
    The frozen CLI always creates its service from the installed signed config.
    """
    owned = service is None; service = service or configured_service(root)
    try:
        snapshot = service.check(manual=True)
        if snapshot['state'] != 'UPDATE_AVAILABLE': return snapshot
        snapshot = service.download()
        if snapshot['state'] != 'READY_TO_APPLY': return snapshot
        pending = remember_download(root, service.staged, service.release.envelope)
        return {**snapshot, **pending, 'ui_state': 'READY_TO_APPLY'}
    finally:
        if owned: service.close()


def apply_update(root, *, pid=None, hwnd_hex=None, reason='normal', service=None):
    """Fresh discovery + cache validation, then the existing real transaction.

    next_launch is called by the stable launcher BEFORE starting App. Normal
    apply supplies the exact running App PID/HWND; no guessed process is closed.
    """
    root = install.target(root); pending = read_pending(root)
    if pending['state'] in ('CURRENT_HEALTHY', 'PREVIOUS_HEALTHY'):
        raise RecoverableError('no pending update')
    if unsafe_teardown(reason):
        install.atomic(root/PENDING, canonical({**pending, 'state': 'PENDING_INSTALL'}))
        return dict(state='PENDING_INSTALL', destructive_apply=False, relaunched=False)
    if bool(pid) != bool(hwnd_hex): raise ValueError('complete App identity required')
    if pid is None and reason != 'next_launch': raise ValueError('normal apply requires running App identity')
    if pid is None: install.require_app_stopped(root)
    owned = service is None; service = service or configured_service(root)
    applied = False
    try:
        snapshot = service.check(manual=True)  # never trust a stale cached envelope
        if snapshot['state'] != 'UPDATE_AVAILABLE':
            raise RecoverableError('pending target is no longer available; check update again')
        if digest(service.release.envelope) != pending['release_sha256']:
            raise SecurityError('pending release changed; download the current signed release')
        stage = install.plain(pending['stage'])
        if digest(install.read(stage.parent/'release.json')) != pending['release_sha256']:
            raise SecurityError('cached release changed')
        service.cache.verify_tree(stage, service.release)
        service.staged = stage
        def bridge(root, stage, envelope):
            nonlocal applied
            if unsafe_teardown(reason): raise RecoverableError('teardown: update remains pending')
            install.atomic(root/PENDING, canonical({**pending, 'state': 'APPLYING'}))
            applied = True
            return install.transact(root, stage, envelope,
                before_swap=(lambda: install.request_app_shutdown(root, pid, hwnd_hex)) if pid else lambda: install.require_app_stopped(root))
        service.apply_bridge = bridge
        result = service.apply_and_restart()  # independently refreshes metadata again
        install.atomic(root/PENDING, canonical({**pending, 'state': 'CURRENT_HEALTHY'}))
        return {**result, 'state': 'CURRENT_HEALTHY'}
    except Exception:
        journal = strict_json(install.read(root/install.JOURNAL))
        # A prior transaction's ROLLED_BACK journal must not consume a newly
        # downloaded pending update after an offline/fresh-metadata failure.
        recovery = root/'last-recovery.json'
        proof = strict_json(install.read(recovery)) if recovery.exists() else {}
        recovered = (applied and journal.get('phase') == 'ROLLED_BACK'
                     and proof.get('attempt') == journal.get('attempt')
                     and proof.get('health', {}).get('ready') is True)
        state = 'PREVIOUS_HEALTHY' if recovered else 'PENDING_INSTALL'
        install.atomic(root/PENDING, canonical({**pending, 'state': state}))
        raise
    finally:
        if owned: service.close()


resume_pending_on_normal_launch = apply_update
launch_updated_app = install.launch_health
record_health = install.notify_health
rollback_previous = install.recover
