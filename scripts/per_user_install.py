"""Consumer install/update transaction. Local ownership is not release authority.

Only compiled Ed25519 keys authorize network/cache content. State and journals
are user-writable recovery hints; they never supply keys, commands or hashes.
The existing protected/per-machine engine remains a separate explicit mode.
"""
from datetime import datetime, timedelta
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from desktop.update_protocol import (canonical, digest, strict_json, verify_release,
    witness_version, version_key, SecurityError, RecoverableError)
from scripts.install_location import existing_parent, low_space_message
from scripts.install_product import configured

CURRENT = 'current-release.json'
HIGHEST = 'highest-release.json'
STATE = 'per-user-install.json'
JOURNAL = 'per-user-transaction.json'
SCHEMA = 'mentorflow.per-user-install.v1'


def keys():
    # Shared authority, not the protected helper's dependency graph. The real
    # desktop PYZ must not import a high-privilege preflight just to read keys.
    try:
        from mentorflow_delivery_authority import PUBLIC_KEYS
    except ModuleNotFoundError as error:
        if error.name != 'mentorflow_delivery_authority': raise
        raise SecurityError('compiled release authority missing') from error
    if (type(PUBLIC_KEYS) is not dict or not PUBLIC_KEYS or
            any(type(key) is not str or type(value) is not bytes or len(value) != 32
                for key, value in PUBLIC_KEYS.items())):
        raise SecurityError('invalid compiled release authority')
    return dict(PUBLIC_KEYS)


def read(path):
    path = Path(path)
    if path.stat().st_size > 16 * 1024**2:
        raise SecurityError('oversized release/state')
    return path.read_bytes()


def atomic(path, data):
    path = Path(path)
    plain(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    with temporary.open('xb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def plain(path):
    """Reject aliases, not the owner of an ordinary existing directory."""
    path = Path(path).absolute()
    if str(path).startswith('\\\\') or '..' in path.parts:
        raise SecurityError('local non-aliased path required')
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            if getattr(item.lstat(), 'st_file_attributes', 0) & 0x400 or item.is_symlink():
                raise SecurityError('reparse path refused')
    return path


def target(path, *, probe=False):
    root = existing_parent(Path(path).absolute())
    configured().validate_root(root)
    for protected in (Path('E:/MentorFlow'), Path('E:/MentorFlowData'), Path(os.environ['SystemRoot'])):
        if root == protected or root.is_relative_to(protected) or protected.is_relative_to(root):
            raise SecurityError('installation overlaps protected system/formal data')
    plain(root)
    if probe:
        # A bounded current-token write check, not ACL ownership inference.
        with tempfile.NamedTemporaryFile(prefix='.mentorflow-location-', dir=root.parent):
            pass
    return root


def historical(raw, public_keys):
    """Validate a signed installed release even after feed freshness expires.

    This is only local version/bootstrap identification. It never admits new
    downloads: install and apply always use current-time verify_release below.
    """
    witness_version(raw, public_keys, channel='stable')
    doc = strict_json(raw)
    issued = datetime.fromisoformat(doc['signed']['issued_at'].replace('Z', '+00:00'))
    return verify_release(raw, public_keys, current='0.0.0', now=issued + timedelta(seconds=1))


def installed(root, public_keys=None):
    root = target(root)
    state = strict_json(read(root / STATE))
    if set(state) != {'schema', 'mode', 'app_id'} or state != {
            'schema': SCHEMA, 'mode': 'per_user', 'app_id': configured().app_id}:
        raise SecurityError('unknown per-user installation state')
    return historical(read(root / CURRENT), public_keys if public_keys is not None else keys())


def is_install(root):
    return (Path(root) / STATE).is_file()


def validate_parent(parent):
    parent = Path(parent)
    if not parent.is_absolute() or not parent.is_dir():
        raise ValueError('existing absolute parent directory required')
    return target(parent / 'MentorFlow', probe=True)


def validate_target(root):
    root = target(root, probe=True)
    if root.exists() and any(root.iterdir()) and not is_install(root):
        raise SecurityError('unknown non-empty installation target')
    return dict(root=str(root), existing=is_install(root),
                version=installed(root).version if is_install(root) else None)


def detect_existing_install(root):
    return validate_target(root)


def write_installed_state(root, envelope):
    """Never accepts caller-provided state or keys. Verify signed App first."""
    root = target(root)
    _, release = admit(root, envelope, keys(), repair=True)
    verify_tree(root / 'App', release)
    atomic(root / CURRENT, release.envelope)
    atomic(root / STATE, canonical(dict(schema=SCHEMA, mode='per_user', app_id=configured().app_id)))


def install_per_user(parent, source, envelope, **options):
    root = validate_parent(parent)
    return transact(root, source, envelope, repair=is_install(root), **options)


def require_app_stopped(root):
    """Native process enumeration. Unknown/inaccessible matching image fails closed."""
    import ctypes as c
    from ctypes import wintypes as w
    from desktop.windows_process_peer import ProcessPeer
    kernel = c.WinDLL('kernel32', use_last_error=True)
    class Entry(c.Structure):
        _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('pid', w.DWORD),
            ('heap', c.c_size_t), ('module', w.DWORD), ('threads', w.DWORD),
            ('parent', w.DWORD), ('priority', w.LONG), ('flags', w.DWORD), ('exe', w.WCHAR * 260)]
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [w.HANDLE, c.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == c.c_void_p(-1).value: raise c.WinError(c.get_last_error())
    try:
        entry = Entry(); entry.dwSize = c.sizeof(entry)
        present = kernel.Process32FirstW(handle, c.byref(entry))
        while present:
            if entry.exe.casefold() == 'mentorflow.exe':
                try:
                    with ProcessPeer(entry.pid) as peer:
                        if Path(peer.inspect()['executable']) == Path(root)/'App/MentorFlow.exe':
                            raise RecoverableError('close the current App before continuing')
                except OSError as error:
                    if error.winerror != 87: raise  # exited PID is harmless
            present = kernel.Process32NextW(handle, c.byref(entry))
    finally: kernel.CloseHandle(handle)


def request_app_shutdown(root, pid, hwnd_hex):
    from desktop.windows_process_peer import ProcessPeer
    import ctypes as c
    from ctypes import wintypes as w
    with ProcessPeer(pid) as peer:
        peer.require(executable=Path(root)/'App/MentorFlow.exe', integrity_rid=8192)
        peer.visible_responsive_window(hwnd_hex)
        user = c.WinDLL('user32'); user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        if not user.PostMessageW(int(hwnd_hex, 16), 0x10, 0, 0): raise c.WinError()
        deadline = time.monotonic() + 15
        while peer.alive() and time.monotonic() < deadline: time.sleep(.05)
        if peer.alive(): raise RecoverableError('current app did not close; not replaced')


def uninstall_per_user(root):
    """Conservative, recoverable uninstall. Never removes user data/unknown files.

    Quarantine signed current App and identity, retaining updater, previous and
    diagnostics for explicit later cleanup. UI owns its shortcuts/registration.
    """
    root = target(root); release = installed(root)
    require_app_stopped(root)
    from desktop.update_download import InstallUpdateLock
    with InstallUpdateLock(root):
        verify_tree(root/'App', release)
        quarantine = root/('uninstalled-' + uuid.uuid4().hex); quarantine.mkdir()
        os.replace(root/'App', quarantine/'App')
        for name in (CURRENT, STATE, HIGHEST, JOURNAL, 'pending-update.json'):
            if (root/name).exists(): os.replace(root/name, quarantine/name)
    return dict(status='UNINSTALLED', quarantine=str(quarantine), unknown_files_preserved=True,
                user_data_deleted=False, registration_cleanup='CALLER_OWNED')


def verify_tree(app, release, *, subset=None):
    """Independent final read/hash; no receipt, timestamps or parent ACL trust."""
    from scripts.verify_windows_package import sha256_file
    app = plain(app)
    if not app.is_dir():
        raise SecurityError('missing application tree')
    if subset is None:
        actual = set()
        for item in app.rglob('*'):
            plain(item)
            if item.is_file(): actual.add(item.relative_to(app).as_posix())
        if actual != set(release.rows):
            raise SecurityError('application file set differs from signed release')
    for name in subset if subset is not None else release.rows:
        row = release.rows.get(name)
        if row is None: raise SecurityError('unsigned bootstrap file')
        item = plain(app / name)
        if not item.is_file() or item.stat().st_size != row['size'] or sha256_file(item) != row['sha256']:
            raise SecurityError('signed application content mismatch: ' + name)


def verify_gui(root, *, executable):
    release = installed(root)
    app = Path(root) / 'App'
    if Path(executable).absolute() != app / 'MentorFlow.exe':
        raise SecurityError('per-user entry outside selected installation')
    from scripts.gui_bootstrap_trust import CLOSURE, closure_paths
    verify_tree(app, release, subset=[CLOSURE])
    closure = closure_paths(strict_json(read(app / CLOSURE)))
    verify_tree(app, release, subset=closure)
    return release


def admit(root, envelope, public_keys, *, repair=False):
    current = installed(root, public_keys) if is_install(root) else None
    epoch, fingerprint = witness_version(read(root / HIGHEST), public_keys, channel='stable') if (root / HIGHEST).exists() else (0, '')
    release = verify_release(envelope, public_keys, current=current.version if current else '0.0.0', highest_seen=epoch)
    if release.signed['withdrawn']:
        raise SecurityError('withdrawn release refused')
    if release.signed['metadata_version'] == epoch and digest(canonical(release.signed)) != fingerprint:
        raise SecurityError('same-epoch metadata equivocation')
    if current and version_key(release.version) <= version_key(current.version) and not repair:
        raise SecurityError('completed release replay/downgrade refused')
    if current and repair and release.version == current.version and release.manifest_bytes != current.manifest_bytes:
        raise SecurityError('same-version replacement refused')
    return current, release


def _copy(source, destination, release, progress):
    total = sum(row['size'] for row in release.rows.values()); copied = 0
    for name, row in release.rows.items():
        path = destination / name; path.parent.mkdir(parents=True, exist_ok=True)
        with (source / name).open('rb') as incoming, path.open('xb') as outgoing:
            while block := incoming.read(1024 * 1024):
                outgoing.write(block); copied += len(block)
                progress(min(80, int(copied * 80 / max(1, total))))


def recover(root, public_keys=None):
    """Recover interrupted swaps using only fixed owned paths and signed releases."""
    root = target(root); public_keys = public_keys if public_keys is not None else keys()
    journal = root / JOURNAL
    if not journal.exists(): return False
    doc = strict_json(read(journal))
    if doc.get('schema') != 'mentorflow.per-user-transaction.v1':
        raise SecurityError('unknown transaction journal')
    if doc.get('phase') in ('HEALTHY', 'ROLLED_BACK'): return False
    previous = root / 'previous'
    if previous.is_dir() and (previous / 'release.json').is_file():
        old = historical(read(previous / 'release.json'), public_keys)
        # An earlier healthy update may already have left a previous version.
        # Do not confuse that older generation with THIS transaction's base.
        if old.version == doc.get('base') and digest(old.manifest_bytes) == doc.get('base_manifest'):
            verify_tree(previous / 'App', old)
            if (root / 'App').exists():
                failed = root / ('failed-' + uuid.uuid4().hex)
                os.replace(root / 'App', failed)
            os.replace(previous / 'App', root / 'App')
            atomic(root / CURRENT, old.envelope)
            atomic(journal, canonical({**doc, 'phase': 'ROLLED_BACK'}))
            return True
    if (root / 'App').is_dir() and is_install(root):
        # Failure before the OLD_MOVED phase never consumes the current app.
        verify_tree(root / 'App', installed(root, public_keys))
        atomic(journal, canonical({**doc, 'phase': 'ROLLED_BACK'}))
        return True
    raise RecoverableError('incomplete first install retained for explicit repair')


def transact(root, source, envelope, *, public_keys=None, health=None, progress=lambda value: None,
             repair=False, before_swap=lambda: None, fault=lambda phase: None):
    """Stage -> verify -> swap -> real health; previous remains recoverable."""
    public_keys = public_keys if public_keys is not None else keys()
    root = target(root, probe=True); source = plain(source)
    if source == root or source.is_relative_to(root) or root.is_relative_to(source):
        raise SecurityError('release source overlaps installed tree')
    exists = root.exists()
    if exists and any(root.iterdir()) and not is_install(root):
        raise SecurityError('该文件夹中已有其他文件。请选择其他安装位置。')
    current, release = admit(root, envelope, public_keys, repair=repair)
    # A distribution may contain a sibling Installer directory. Only signed
    # files are read/copied; the newly materialized tree must match exactly.
    verify_tree(source, release, subset=list(release.rows))
    required = sum(r['size'] for r in release.rows.values()) + 64 * 1024**2
    free = shutil.disk_usage(root.parent).free
    if free < required: raise RecoverableError(low_space_message(required, free))
    root.mkdir(exist_ok=True)  # parent exists; only the MentorFlow level is created
    from desktop.update_download import InstallUpdateLock
    with InstallUpdateLock(root):
        current, release = admit(root, envelope, public_keys, repair=repair)
        if (root / JOURNAL).exists() and strict_json(read(root / JOURNAL)).get('phase') not in ('HEALTHY', 'ROLLED_BACK'):
            raise RecoverableError('previous transaction requires recovery before new apply')
        attempt = uuid.uuid4().hex
        stage = root / ('staging-' + attempt); stage.mkdir()
        journal = dict(schema='mentorflow.per-user-transaction.v1', attempt=attempt, phase='PREPARING', target=release.version,
                       base=current.version if current else None, base_manifest=digest(current.manifest_bytes) if current else None)
        atomic(root / JOURNAL, canonical(journal))
        def mark(phase):
            journal['phase'] = phase; atomic(root / JOURNAL, canonical(journal)); fault(phase)
        moved = False; base_closed = False
        try:
            _copy(source, stage / 'App', release, progress)
            # Cache/staging may be writable: recheck current signed metadata and
            # ALL copied bytes immediately before invoking the swap boundary.
            _, release = admit(root, envelope, public_keys, repair=repair)
            verify_tree(stage / 'App', release); mark('VERIFIED'); progress(85)
            atomic(root / HIGHEST, release.envelope)
            from scripts.per_user_update_core import unsafe_teardown
            if unsafe_teardown(): raise RecoverableError('teardown: update remains pending')
            before_swap()
            base_closed = True
            verify_tree(stage / 'App', release)
            if unsafe_teardown(): raise RecoverableError('teardown: update remains pending')
            if current:
                previous = root / 'previous'
                if previous.exists():
                    # Retain older known versions; cleanup is a later operation.
                    os.replace(previous, root / ('retired-' + uuid.uuid4().hex))
                previous.mkdir(); atomic(previous / 'release.json', current.envelope)
                os.replace(root / 'App', previous / 'App'); moved = True; mark('OLD_MOVED')
            os.replace(stage / 'App', root / 'App'); moved = True
            atomic(root / CURRENT, release.envelope)
            atomic(root / STATE, canonical(dict(schema=SCHEMA, mode='per_user', app_id=configured().app_id)))
            if not (root / 'Updater').exists():
                shutil.copytree(root / 'App/UserRuntime', root / 'Updater')
            mark('NEW_ACTIVE'); progress(95)
            proof = (health or launch_health)(root, release)
            if not isinstance(proof, dict) or proof.get('ready') is not True:
                raise RecoverableError('new version did not become ready')
            mark('HEALTHY'); progress(100)
            return dict(status='HEALTHY', version=release.version, health=proof,
                        install_uac_count=0, update_uac_count=0)
        except BaseException:
            if current and moved:
                recover(root, public_keys)
                # A bad target must not leave the user with a closed application.
                recovery = (health or launch_health)(root, current)
                if not isinstance(recovery, dict) or recovery.get('ready') is not True:
                    raise RecoverableError('previous restored but health not confirmed')
                atomic(root / 'last-recovery.json', canonical(dict(attempt=journal['attempt'], version=current.version, health=recovery)))
            elif current:
                atomic(root / JOURNAL, canonical({**journal, 'phase': 'ROLLED_BACK'}))
                from scripts.per_user_update_core import unsafe_teardown
                if base_closed and not unsafe_teardown(): (health or launch_health)(root, current)
            else:
                # Never publish a usable installation state after failed first
                # health. Preserve only this attempt's generated files for repair.
                if (root / 'App').exists():
                    os.replace(root / 'App', root / ('failed-' + uuid.uuid4().hex))
                (root / STATE).unlink(missing_ok=True)
                (root / CURRENT).unlink(missing_ok=True)
                atomic(root / JOURNAL, canonical({**journal, 'phase': 'INITIAL_FAILED'}))
            raise


def launch_health(root, release, *, timeout=45):
    from desktop.windows_update_pipe import Listener
    from desktop.windows_process_peer import ProcessPeer
    attempt = uuid.uuid4().hex; nonce = os.urandom(32).hex()
    process = None
    with Listener(attempt, 'health') as channel:
        try:
            from scripts.per_user_update_core import unsafe_teardown
            if unsafe_teardown():
                raise RecoverableError('session ending: App launch deferred')
            process = subprocess.Popen([str(root / 'App/MentorFlow.exe'), '--install-mode', 'per_user',
                '--install-root', str(root), '--update-attempt', attempt, '--update-nonce', nonce],
                cwd=root / 'App', close_fds=True)
            pid = channel.accept(timeout=timeout)
            if pid != process.pid: raise SecurityError('health came from another process')
            signal = channel.receive(timeout=5)
            if signal.get('attempt_id') != attempt or signal.get('nonce') != nonce or signal.get('version') != release.version or signal.get('manifest_sha256') != digest(release.manifest_bytes):
                raise SecurityError('health release/correlation mismatch')
            with ProcessPeer(pid) as peer:
                peer.require(executable=root / 'App/MentorFlow.exe', integrity_rid=8192)
                peer.visible_responsive_window(signal['hwnd_hex'])
            channel.send({'health_received': True})
            return dict(ready=True, pid=pid, version=release.version, hwnd_hex=signal['hwnd_hex'])
        except BaseException:
            if process is not None and process.poll() is None:
                process.terminate(); process.wait(timeout=10)  # only this owned child
            raise


def notify_health(root, gate, attempt, nonce):
    if not attempt: return
    from desktop.windows_update_pipe import connect
    from desktop.windows_process_peer import ProcessPeer
    release = installed(root)
    signal = dict(attempt_id=attempt, nonce=nonce, version=release.version,
                  manifest_sha256=digest(release.manifest_bytes), hwnd_hex=f'{int(gate.winId()):016x}')
    def send():
        try:
            with connect(attempt, 'health', timeout=10) as channel:
                with ProcessPeer(channel.peer_pid()) as peer:
                    info = peer.inspect()
                    if info['integrity_rid'] != 8192 or info['elevated']:
                        return
                    image = Path(info['executable'])
                    # Initial installer runs its embedded runtime; the updater
                    # thereafter runs the same compiled UserRuntime entry.
                    if image.name != 'UserRuntime.exe': return
                    channel.send(signal); channel.receive(timeout=5)
        except (OSError, RuntimeError, ValueError, TimeoutError):
            return
    import threading
    threading.Thread(target=send, daemon=True, name='per-user-window-health').start()


def handoff(root, stage, envelope, *, hwnd_hex):
    root = target(root)
    from scripts.per_user_update_core import unsafe_teardown, remember_download
    if unsafe_teardown():
        return remember_download(root, stage, envelope, state='PENDING_INSTALL')
    from desktop.windows_process_peer import ProcessPeer
    with ProcessPeer(os.getpid()) as peer:
        peer.require(executable=root / 'App/MentorFlow.exe', integrity_rid=8192)
        peer.visible_responsive_window(hwnd_hex)
    intent = dict(schema='mentorflow.per-user-intent.v1', root=str(root), stage=str(stage),
                  release_sha256=digest(envelope), pid=os.getpid(), hwnd_hex=hwnd_hex)
    path = Path(stage).parent / (uuid.uuid4().hex + '.per-user-intent.json')
    atomic(path, canonical(intent))
    process = subprocess.Popen([str(root / 'Updater/UserRuntime.exe'), 'apply', '--root', str(root),
                                '--intent', str(path)], cwd=root, close_fds=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    return dict(coordinator_started=True, pid=process.pid, update_uac_count=0)


def coordinate(root, intent_path):
    from desktop.windows_process_peer import ProcessPeer
    root = target(root); intent = strict_json(read(intent_path))
    if set(intent) != {'schema', 'root', 'stage', 'release_sha256', 'pid', 'hwnd_hex'} or intent['schema'] != 'mentorflow.per-user-intent.v1' or Path(intent['root']) != root:
        raise SecurityError('invalid per-user update intent')
    stage = plain(intent['stage']); envelope = read(stage.parent / 'release.json')
    if digest(envelope) != intent['release_sha256']: raise SecurityError('prepared release changed')
    def close_base():
        import ctypes as c
        from ctypes import wintypes as w
        with ProcessPeer(intent['pid']) as peer:
            peer.require(executable=root / 'App/MentorFlow.exe', integrity_rid=8192)
            peer.visible_responsive_window(intent['hwnd_hex'])
            user = c.WinDLL('user32'); user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
            user.PostMessageW(int(intent['hwnd_hex'], 16), 0x10, 0, 0)
            deadline = time.monotonic() + 15
            while peer.alive() and time.monotonic() < deadline: time.sleep(.05)
            if peer.alive(): raise RecoverableError('current app did not close; not replaced')
    result_path = Path(intent_path).with_suffix('.result.json')
    try:
        result = transact(root, stage, envelope, before_swap=close_base)
        atomic(result_path, canonical(result)); return result
    except Exception as error:
        atomic(result_path, canonical(dict(status='FAILED', error=type(error).__name__, detail=str(error), update_uac_count=0)))
        raise


def _execute(args, parser, progress):
    if args.parent is not None:
        if args.action not in ('preflight', 'install'): parser.error('parent is only valid for preflight/install')
        args.root = validate_parent(args.parent)
    if args.action == 'launch':
        from scripts.per_user_launcher import launch
        return launch(args.root)
    if args.action == 'prepare-update':
        from scripts.per_user_update_core import prepare_update
        return prepare_update(args.root)
    if args.action == 'apply-pending':
        from scripts.per_user_update_core import apply_update
        return apply_update(args.root, pid=args.pid, hwnd_hex=args.hwnd_hex, reason=args.exit_reason)
    if args.action == 'update-status':
        from scripts.per_user_update_core import status
        return status(args.root)
    if args.action == 'uninstall': return uninstall_per_user(args.root)
    if args.action == 'shutdown':
        request_app_shutdown(args.root, args.pid, args.hwnd_hex)
        return dict(status='APP_CLOSED')
    if args.action == 'apply': return coordinate(args.root, args.intent)
    if args.action == 'recover': return dict(recovered=recover(args.root))
    root = target(args.root, probe=True)
    if root.exists() and any(root.iterdir()) and not is_install(root):
        raise SecurityError('该文件夹中已有其他文件。请选择其他安装位置。')
    if args.action == 'preflight':
        if is_install(root): installed(root)
        return dict(status='PASS', **validate_target(root), install_uac_count=0)
    if args.source is None or args.release is None: parser.error('source and signed release required')
    result = transact(root, args.source, read(args.release), repair=is_install(root), progress=progress)
    if args.close_after_health:
        # The branded shell owns the explicit Finish/Launch action. Close only
        # the native peer the core just launched and independently validated.
        proof = result['health']
        request_app_shutdown(root, proof['pid'], proof['hwnd_hex'])
        result['installer_finish'] = 'AWAITING_EXPLICIT_LAUNCH'
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('preflight', 'install', 'apply', 'recover',
        'prepare-update', 'apply-pending', 'update-status', 'uninstall', 'shutdown', 'launch'))
    location = parser.add_mutually_exclusive_group(required=True)
    location.add_argument('--root', type=Path)
    location.add_argument('--parent', type=Path)
    parser.add_argument('--source', type=Path); parser.add_argument('--release', type=Path)
    parser.add_argument('--intent', type=Path)
    parser.add_argument('--result', type=Path)
    parser.add_argument('--status-file', type=Path)
    parser.add_argument('--cancel-file', type=Path)
    parser.add_argument('--close-after-health', action='store_true')
    parser.add_argument('--pid', type=int); parser.add_argument('--hwnd-hex')
    parser.add_argument('--exit-reason', choices=('normal', 'next_launch', 'shutdown', 'logoff', 'unsafe'), default='normal')
    args = parser.parse_args()
    from desktop.windows_process_peer import ProcessPeer
    with ProcessPeer(os.getpid()) as peer:
        info = peer.inspect()
        if info['elevated'] or info['integrity_rid'] != 8192:
            raise SecurityError('per-user mode requires an ordinary user token; never elevates')
    last_progress = [-1]
    def progress(value):
        if args.cancel_file and args.cancel_file.exists():
            raise RecoverableError('安装已取消。')
        if args.status_file and value != last_progress[0]:
            atomic(args.status_file, ('[install]\r\nstage=正在安装 MentorFlow…\r\npercent='+str(value)+'\r\nerror=\r\n').encode('utf-16'))
            last_progress[0] = value
    try:
        from contextlib import nullcontext
        from scripts.per_user_runtime_session import runtime_session
        pid = args.pid
        if args.action == 'apply': pid = strict_json(read(args.intent))['pid']
        scope = (runtime_session(pid) if args.action in
                 ('install', 'apply', 'apply-pending', 'recover', 'launch', 'uninstall') else nullcontext())
        with scope:
            result = _execute(args, parser, progress)
        if args.result: atomic(args.result, canonical(result))
        return result
    except Exception as error:
        if args.status_file:
            atomic(args.status_file, ('[install]\r\nstage=安装未完成\r\npercent=0\r\nerror='+str(error).replace('\n',' ')+'\r\n').encode('utf-16'))
        if args.result: atomic(args.result, canonical(dict(status='FAILED', error=type(error).__name__, detail=str(error))))
        raise


if __name__ == '__main__':
    try: print(canonical(main()).decode(), flush=True)
    except Exception as error:
        print(str(error), file=sys.stderr, flush=True); raise SystemExit(3)
