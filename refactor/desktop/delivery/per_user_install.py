"""Consumer install/update transaction. Local ownership is not release authority.

Only compiled Ed25519 keys authorize network/cache content. State and journals
are user-writable recovery hints; they never supply keys, commands or hashes.
The existing protected/per-machine engine remains a separate explicit mode.
"""
from datetime import datetime, timedelta
from dataclasses import replace
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from functools import lru_cache
from contextlib import contextmanager

# Paint before cryptography/Qt imports or hidden shutdown-monitor windows.
if __name__ == '__main__' and sys.argv[1:2] == ['launch']:
    from refactor.desktop.delivery.startup_window import native_splash
    native_splash()
elif __name__ == '__main__' and sys.argv[1:2] == ['apply-latest'] and '--app-self-exit' in sys.argv:
    from refactor.desktop.delivery.startup_window import native_splash
    native_splash('正在更新 MentorFlow…')

from refactor.desktop.delivery.update_protocol import canonical
from refactor.desktop.delivery.update_protocol import digest
from refactor.desktop.delivery.update_protocol import strict_json
from refactor.desktop.delivery.update_protocol import verify_release
from refactor.desktop.delivery.update_protocol import witness_version
from refactor.desktop.delivery.update_protocol import version_key
from refactor.desktop.delivery.update_protocol import SecurityError
from refactor.desktop.delivery.update_protocol import RecoverableError
from refactor.desktop.delivery.install_location import existing_parent
from refactor.desktop.delivery.install_location import low_space_message
from refactor.desktop.delivery.install_product import configured
from refactor.filesystem_ops import RenameStateChangedError, rename_with_retry

CURRENT = 'current-release.json'
UPDATER_RELEASE = 'updater-release.json'
HIGHEST = 'highest-release.json'
STATE = 'per-user-install.json'
JOURNAL = 'per-user-transaction.json'
UPDATER_SWAP = 'updater-swap'
SCHEMA = 'mentorflow.per-user-install.v1'


class RecoveryEvidenceMissing(RecoverableError):
    """A transaction cannot be restored because its signed evidence is absent."""


class DeferredAppActivation(RecoverableError):
    """The verified App tree could not be activated within the bounded retry."""

    def __init__(self, winerror):
        self.winerror = winerror
        super().__init__('新版已下载好，下次打开软件时完成更新。')


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
    # Inno polls the progress INI with GetIniString. On Windows that brief read
    # can deny delete-sharing, so an otherwise valid atomic replace must retry.
    # Never fall back to truncating a live signed-state or progress file.
    for attempt in range(8):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if os.name != 'nt' or attempt == 7:
                raise
            time.sleep(min(.01 * 2 ** attempt, .2))


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
    return _historical_bytes(bytes(raw), tuple(sorted(public_keys.items())))


@lru_cache(maxsize=2)
def _historical_bytes(raw, key_items):
    # Reuse only byte-identical metadata with the same compiled public keys.
    # Unlike a stat hint this cannot hide changed metadata. Network admission
    # still uses fresh verify_release/anti-replay checks, never this local cache.
    public_keys = dict(key_items)
    witness_version(raw, public_keys, channel='stable')
    doc = strict_json(raw)
    issued = datetime.fromisoformat(doc['signed']['issued_at'].replace('Z', '+00:00'))
    return verify_release(raw, public_keys, current='0.0.0', now=issued + timedelta(seconds=1))


def installed(root, public_keys=None):
    root = target(root)
    # A legacy Inno flat installation has no core STATE. Its signed sidecar
    # still has exactly the same release authority; Inno's INI is NOT authority.
    if not (root / STATE).exists() and (root / CURRENT).is_file() and (root / 'MentorFlow.exe').is_file():
        return historical(read(root / CURRENT), public_keys if public_keys is not None else keys())
    state = strict_json(read(root / STATE))
    if set(state) != {'schema', 'mode', 'app_id'} or state != {
            'schema': SCHEMA, 'mode': 'per_user', 'app_id': configured().app_id}:
        raise SecurityError('unknown per-user installation state')
    return historical(read(root / CURRENT), public_keys if public_keys is not None else keys())


def is_install(root):
    return (Path(root) / STATE).is_file() or ((Path(root) / CURRENT).is_file() and (Path(root) / 'MentorFlow.exe').is_file())


def app_path(root):
    """Fixed layout only; no path is ever read from writable INI/receipts."""
    root = target(root)
    return root if not (root / STATE).exists() and (root / 'MentorFlow.exe').is_file() else root / 'App'


def resolve_launch_root(executable, selected=None):
    image = plain(executable)
    if image.name.casefold() != 'mentorflow.exe':
        raise SecurityError('unexpected desktop executable')
    inferred = image.parent.parent if image.parent.name.casefold() == 'app' else image.parent
    root = target(selected if selected is not None else inferred)
    if image not in (root / 'App/MentorFlow.exe', root / 'MentorFlow.exe'):
        raise SecurityError('desktop entry outside selected installation')
    return root


def check_launch_files(root, release=None, *, executable=None):
    """Small signed identity/presence check; hashes belong to install/background."""
    release = release or installed(root)
    app = app_path(root)
    if executable is not None and Path(executable).absolute() != app / 'MentorFlow.exe':
        raise SecurityError('per-user entry outside selected installation')
    for name in ('MentorFlow.exe', 'UserRuntime/UserRuntime.exe'):
        if name not in release.rows or not plain(app / name).is_file():
            raise SecurityError('missing signed launch file: ' + name)
    return release


def verify_recovery_runtime(root, release=None):
    release = release or installed(root)
    # The runtime is independently bound while replacement waits for its
    # process to exit. A healthy App/installer refreshes this binding below.
    if (Path(root) / UPDATER_SWAP).exists():
        require_updater_stopped(root)
        _recover_updater_swap(root, keys())
    binding = Path(root) / UPDATER_RELEASE
    if (Path(root) / 'Updater').exists() and binding.exists():
        plain(binding)
        release = historical(read(binding), keys())
    rows = {name[len('UserRuntime/'):]: row for name, row in release.rows.items() if name.startswith('UserRuntime/')}
    if not rows: raise SecurityError('signed repair runtime missing')
    runtime = Path(root) / 'Updater'
    if not runtime.exists(): runtime = app_path(root) / 'UserRuntime'
    verify_tree(runtime, replace(release, rows=rows))
    return runtime / 'UserRuntime.exe'


def _runtime_release(release):
    rows = {name[len('UserRuntime/'):]: row for name, row in release.rows.items()
            if name.startswith('UserRuntime/')}
    if 'UserRuntime.exe' not in rows:
        raise SecurityError('signed repair runtime missing')
    return replace(release, rows=rows)


def _recover_updater_swap(root, public_keys, *, snapshot=None):
    """Resume/roll back only fixed paths, with independently signed bindings."""
    root = Path(root); work = plain(root / UPDATER_SWAP)
    if not work.exists(): return
    state = snapshot if snapshot is not None else strict_json(read(plain(work / 'state.json')))
    if (set(state) != {'phase', 'had_updater', 'had_binding'} or
            state['phase'] not in ('COPYING', 'PREPARED', 'COMMITTED') or
            type(state['had_updater']) is not bool or type(state['had_binding']) is not bool):
        raise SecurityError('invalid updater swap state')
    active, binding, backup = plain(root / 'Updater'), plain(root / UPDATER_RELEASE), plain(work / 'old')
    if state['phase'] == 'COMMITTED':
        new = historical(read(work / 'new-release.json'), public_keys)
        verify_tree(active, _runtime_release(new))
        if read(binding) != new.envelope: raise SecurityError('updater binding mismatch')
    elif state['phase'] == 'PREPARED':
        if state['had_updater']:
            if snapshot is None:
                old = historical(read(work / 'old-release.json'), public_keys)
                verify_tree(backup if backup.exists() else active, _runtime_release(old))
            if backup.exists():
                if active.exists(): _recovery_rename(root, active, work / 'discarded', component='UPDATER_RECOVER_DISCARD')
                _recovery_rename(root, backup, active, component='UPDATER_RECOVER_RESTORE')
        elif active.exists():
            if snapshot is None:
                new = historical(read(work / 'new-release.json'), public_keys)
                verify_tree(active, _runtime_release(new))
            _recovery_rename(root, active, work / 'discarded', component='UPDATER_RECOVER_DISCARD')
        if state['had_binding']:
            atomic(binding, read(work / 'old-release.json'))
        else:
            binding.unlink(missing_ok=True)
    # COPYING has not touched the live pair. COMMITTED keeps the new pair.
    shutil.rmtree(work)


def _rename_directory(root, source, target, *, component, diagnose=False,
                      budget_seconds=None):
    def record(state, phase, count, elapsed):
        # UserRuntime has no desktop logging configuration. Its existing
        # short-lived maintenance writer also records retries in mentorflow.log.
        maintenance_log(root, 'directory_rename', state=state, step=phase,
                        count=count, elapsed_seconds=round(elapsed, 3))
    def record_attempt(details):
        maintenance_log(root, 'directory_rename_attempt', step=component,
                        attempt=details.get('attempt'), timestamp=details.get('timestamp'),
                        elapsed_seconds=round(details.get('elapsed_seconds', 0.0), 3),
                        error_code=details.get('error_code'),
                        blockers=details.get('blockers', []),
                        diagnostic_status=details.get('diagnostic_status', 'unknown'),
                        diagnostic_reason=details.get('diagnostic_reason'))
    return rename_with_retry(source, target, component=component, on_event=record,
                             diagnose=diagnose, on_attempt=record_attempt if diagnose else None,
                             budget_seconds=budget_seconds)


def _recovery_rename(root, source, target, *, component):
    try:
        _rename_directory(root, source, target, component=component)
    except (OSError, RenameStateChangedError) as error:
        maintenance_log(root, 'recovery_incomplete', step=component,
                        code='DIRECTORY_RENAME_RETRY_EXHAUSTED')
        raise RecoverableError('恢复没完成，下次启动再试') from error


def replace_updater(root, release, *, public_keys=None, fault=lambda phase: None):
    """Caller holds InstallUpdateLock. Stage/hash, rename, bind; roll back on failure."""
    root = target(root); public_keys = public_keys if public_keys is not None else keys()
    require_updater_stopped(root)
    _recover_updater_swap(root, public_keys)
    # Do not admit a caller-supplied row set as authority.
    release = historical(release.envelope, public_keys)
    incoming = _runtime_release(release)
    active, binding = plain(root / 'Updater'), plain(root / UPDATER_RELEASE)
    if active.exists() and binding.exists() and read(binding) == release.envelope:
        try:
            verify_tree(active, incoming)
            return dict(status='UP_TO_DATE', version=release.version)
        except SecurityError:
            pass  # An explicit installer repair must repair corrupt runtime bytes.
    source = plain(app_path(root) / 'UserRuntime')
    verify_tree(source, incoming)
    work = plain(root / UPDATER_SWAP); work.mkdir()
    state = dict(phase='COPYING', had_updater=active.exists(), had_binding=binding.exists())
    atomic(work / 'state.json', canonical(state))
    try:
        atomic(work / 'new-release.json', release.envelope)
        if state['had_updater']:
            atomic(work / 'old-release.json', read(binding) if binding.exists() else installed(root, public_keys).envelope)
        elif state['had_binding']:
            atomic(work / 'old-release.json', read(binding))
        shutil.copytree(source, work / 'next')
        verify_tree(work / 'next', incoming); fault('VERIFIED')
        state['phase'] = 'PREPARED'; atomic(work / 'state.json', canonical(state))
        # Recheck just before the swap, in case an independent shortcut started.
        require_updater_stopped(root)
        if active.exists(): _rename_directory(root, active, work / 'old', component='UPDATER_SWAP_OLD')
        fault('OLD_MOVED')
        _rename_directory(root, work / 'next', active, component='UPDATER_SWAP_NEW'); fault('NEW_ACTIVE')
        atomic(binding, release.envelope); fault('BOUND')
        verify_tree(active, incoming)
        state['phase'] = 'COMMITTED'; atomic(work / 'state.json', canonical(state))
    except BaseException:
        # An in-process repair failure restores the exact original bytes,
        # including an already corrupt runtime. Recovery after process death
        # has no such trusted in-memory snapshot and verifies signed bindings.
        _recover_updater_swap(root, public_keys, snapshot=state)
        raise
    # Cleanup cannot undo a committed runtime pair; a later startup retries it.
    try: _recover_updater_swap(root, public_keys)
    except OSError: pass
    return dict(status='UPDATED', version=release.version)


def refresh_updater(root, *, persist_deferred=True):
    """One background attempt after healthy App startup; failure leaves old runtime."""
    import logging
    from refactor.desktop.delivery.update_download import InstallUpdateLock
    destination = None
    try:
        root = target(root)
        destination = root
        with InstallUpdateLock(root):
            release = installed(root)
            if (root / JOURNAL).exists() and strict_json(read(root / JOURNAL)).get('phase') not in ('HEALTHY', 'ROLLED_BACK'):
                raise RecoverableError('App health transaction not finished; retry next startup')
            result = replace_updater(root, release)
    except RecoverableError as error:
        result = dict(status='DEFERRED', detail=str(error))
    except Exception as error:
        result = dict(status='FAILED', detail=str(error), error=type(error).__name__)
    logging.getLogger(__name__).info('Updater refresh: %s', result)
    if destination is not None and (persist_deferred or result['status'] != 'DEFERRED'):
        try: atomic(destination / 'updater-refresh.json', canonical(result))
        except OSError: logging.getLogger(__name__).exception('Cannot write updater refresh result')
    return result


MAINTENANCE_RETRY_DELAYS = (2, 3, 5, 10, 20, 40)
# App-local serialization; the existing installation lock still coordinates
# with other processes. Never hold this lock on the GUI thread.
import threading as _maintenance_threading
_maintenance_lock = _maintenance_threading.Lock()


def schedule_updater_refresh(root):
    import threading
    def work():
        for attempt, delay in enumerate(MAINTENANCE_RETRY_DELAYS, 1):
            time.sleep(delay)
            with _maintenance_lock:
                result = refresh_updater(root, persist_deferred=False)
            maintenance_log(root, 'updater_refresh_attempt', attempt=attempt,
                            delay_seconds=delay, **result)
            if result['status'] != 'DEFERRED':
                return
        # A busy coordinator is expected during handoff. Only publish the
        # deferred result once this startup's bounded retry budget is spent.
        try: atomic(Path(root) / 'updater-refresh.json', canonical(result))
        except OSError as error:
            maintenance_log(root, 'updater_refresh_result_write_failed', detail=str(error))
        maintenance_log(root, 'updater_refresh_deferred', attempts=attempt, **result)
    thread = threading.Thread(target=work, daemon=True, name='updater-refresh')
    thread.start()
    return thread


def schedule_program_cleanup(root, *, initial_delay=2):
    """Housekeeping must not wait for a slow/offline update-feed request."""
    import threading
    if not (Path(root) / STATE).is_file(): return None
    def work():
        # Startup discovery and updater refresh can still own their locks at
        # two seconds. Retry only a busy lock, with a bounded background budget.
        for delay in (initial_delay, *MAINTENANCE_RETRY_DELAYS[1:]):
            time.sleep(delay)
            try:
                with _maintenance_lock:
                    cleanup_program_files(root, retry_busy=True)
                maintenance_log(root, 'cleanup_complete')
                return
            except RecoverableError:
                continue
            except (OSError, RuntimeError, ValueError) as error:
                maintenance_log(root, 'cleanup_deferred', error=str(error))
                return
    thread = threading.Thread(target=work, daemon=True, name='program-cleanup')
    thread.start()
    return thread


def maintenance_log(root, phase, **fields):
    """Short-lived appenders: never retain a file handle in the launch process."""
    from refactor.desktop.delivery.startup_window import log_phase
    import json
    try:
        log_phase(root, phase, **fields)
        log = Path(root) / 'Data/logs/mentorflow.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(event='update', phase=phase, **fields),
                                    ensure_ascii=False) + '\n')
    except OSError:
        pass


@contextmanager
def _install_stage(root, stage):
    """Measure local work separately from the existing network/rename timers."""
    started = time.perf_counter()
    # An incomplete first installation must reject untrusted repair inputs
    # without touching its evidence (including logs). Update timings apply to
    # installations whose identity already exists.
    enabled = (Path(root) / STATE).is_file()
    if enabled:
        maintenance_log(root, 'install_stage_started', stage=stage)
    complete = False
    try:
        yield
        complete = True
    finally:
        if enabled:
            maintenance_log(root, 'install_stage_finished', stage=stage, complete=complete,
                            seconds=time.perf_counter() - started)


def _program_cache_roots(root):
    identity = digest((configured().app_id + '\0' + os.path.normcase(str(root))).encode())[:12]
    roots = [root.parent / '.MentorFlow-runtime' / '.mfu']
    if os.environ.get('LOCALAPPDATA'):
        roots.append(Path(os.environ['LOCALAPPDATA']) / '.mfu' / identity)
    return roots


def _remove_program_tree(root, path):
    """Only call for owned transaction/cache trees, never Data or unknown files."""
    try:
        plain(path)
        # Preserve any unexpectedly embedded learning data rather than guessing.
        # Product resources include course/data and tzdata. Only the install
        # data namespace is learning data; do not mistake resources for it.
        if (path / 'Data').exists() or (path / 'App/Data').exists():
            maintenance_log(root, 'cleanup_skipped', path=str(path), reason='contains_Data')
            return False
        size = sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
        shutil.rmtree(path)
        maintenance_log(root, 'cleanup_removed', path=str(path), bytes=size)
        return True
    except (OSError, RuntimeError, ValueError) as error:
        maintenance_log(root, 'cleanup_deferred', path=str(path), error=str(error))
        return False


def cleanup_program_files(root, *, retry_busy=False):
    """Keep current, rollback and the one still-pending download; retry locked junk."""
    from refactor.desktop.delivery.update_download import InstallUpdateLock
    from refactor.desktop.delivery import per_user_update_core as core
    root = target(root)
    removed = []
    try:
        from contextlib import ExitStack
        from refactor.desktop.delivery.update_service import install_cache_identity
        with ExitStack() as locks:
            # Download/reconstruction holds the metadata cache lock, not the
            # installation lock. Take both existing namespaces before deleting
            # any staged tree, including an as-yet-unpublished pending download.
            locals_ = [root.parent / '.MentorFlow-runtime']
            if os.environ.get('LOCALAPPDATA'):
                locals_.append(Path(os.environ['LOCALAPPDATA']))
            caches = {local / configured().app_id / 'Updates' / install_cache_identity(root)
                      for local in locals_}
            for cache in sorted(caches):
                if cache.is_dir(): locks.enter_context(InstallUpdateLock(cache))
            locks.enter_context(InstallUpdateLock(root))
            if (root / JOURNAL).exists():
                phase = strict_json(read(root / JOURNAL)).get('phase')
                if phase not in ('HEALTHY', 'ROLLED_BACK', 'INITIAL_FAILED'):
                    return removed
            core.discard_stale_pending(root)
            protected = None
            if (root / core.PENDING).exists():
                pending = core.read_pending(root)
                if (pending['state'] in ('UPDATE_DOWNLOADED', 'PENDING_INSTALL', 'APPLYING') or
                        (pending['state'] == 'PREVIOUS_HEALTHY' and
                         pending.get('deferred_reason') == 'app_directory_busy')):
                    protected = plain(pending['stage'])
            candidates = [p for p in root.iterdir() if p.is_dir() and
                          re.fullmatch(r'(?:staging|retired|failed)-[0-9a-f]{32}', p.name)]
            for cache in _program_cache_roots(root):
                if not cache.exists(): continue
                plain(cache)
                # Existing downloader layout: <release>/App, or <install>/<release>/App.
                for directory in (*cache.iterdir(), *cache.glob('*/*')):
                    if directory.is_dir() and re.fullmatch('[0-9a-f]{12}', directory.name):
                        candidates.append(directory)
            for path in candidates:
                if not path.exists(): continue
                if protected is not None and (protected == path or protected.is_relative_to(path)):
                    continue
                if _remove_program_tree(root, path): removed.append(str(path))
    except (OSError, RuntimeError, ValueError) as error:
        maintenance_log(root, 'cleanup_deferred', error=str(error))
        if retry_busy and isinstance(error, RecoverableError): raise
    return removed


def verify_payload(root, release=None):
    """Separate signed payload from the observed Inno v1 install namespace.

    The legacy INI is a typed ownership claim, never a hash/signature authority.
    We neither execute nor delete uninstallers here. Canonical installs keep all
    installer/runtime records outside App, so their payload set remains exact.
    """
    release = release or installed(root)
    app = app_path(root)
    if app != Path(root):
        verify_tree(app, release)
        return
    import configparser
    ini = app / '.mentorflow-install.ini'
    plain(ini)
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    raw = read(ini)
    try:
        parser.read_string(raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'))
    except configparser.Error as error:
        raise SecurityError('invalid legacy installer ownership format') from error
    if set(parser.sections()) != {'Product', 'Files', 'Directories'}:
        raise SecurityError('unknown legacy installer ownership format')
    product = dict(parser['Product'])
    if product != dict(Id='MentorFlow.Inno.v1', Version=release.version,
                       ExecutableSHA256=release.rows['MentorFlow.exe']['sha256']):
        raise SecurityError('legacy installer claim differs from signed payload')
    files = {name.replace('\\', '/'): value for name, value in parser['Files'].items()}
    if set(files) != set(release.rows) or any(value != '1' for value in files.values()):
        raise SecurityError('legacy installer file inventory differs from signed payload')
    expected_dirs = set()
    for name in release.rows:
        expected_dirs.update(p.as_posix() for p in Path(name).parents if p.as_posix() != '.')
    declared_dirs = {name.replace('\\', '/'): value for name,value in parser['Directories'].items()}
    if set(declared_dirs) != expected_dirs or any(value != '1' for value in declared_dirs.values()):
        raise SecurityError('legacy installer directory inventory differs from signed payload')
    # Exact artifacts emitted by the audited Inno v1 installer, not a wildcard
    # executable/DLL exemption. These inert uninstall records never load in App.
    metadata = {'.mentorflow-install.ini', 'unins000.exe', 'unins000.dat'}
    for name in ('unins000.exe', 'unins000.dat'):
        item = plain(app / name)
        if not item.is_file() or not item.stat().st_size:
            raise SecurityError('incomplete installer-owned metadata')
    with (app/'unins000.exe').open('rb') as stream:
        if stream.read(2) != b'MZ': raise SecurityError('invalid Inno uninstaller')
    controls = {CURRENT, UPDATER_RELEASE, STATE, HIGHEST, JOURNAL, 'pending-update.json', 'operation.lock'}
    actual = set()
    for item in app.rglob('*'):
        plain(item)
        name = item.relative_to(app).as_posix()
        if item.is_file(): actual.add(name)
        elif name not in expected_dirs: raise SecurityError('unknown flat installation directory')
    if actual - metadata - controls != set(release.rows):
        raise SecurityError('unknown flat payload content')
    verify_tree(app, release, subset=list(release.rows))


def validate_parent(parent):
    parent = Path(parent)
    if not parent.is_absolute() or not parent.is_dir():
        raise ValueError('existing absolute parent directory required')
    return target(parent / 'MentorFlow', probe=True)


def validate_target(root):
    root = target(root, probe=True)
    if root.exists() and any(root.iterdir()) and not is_install(root):
        release = _initial_repair_release(root, keys())
        return dict(root=str(root), existing=False, version=release.version, repairable=True)
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


def _require_image_stopped(image, message):
    """Native process enumeration. Unknown/inaccessible matching image fails closed."""
    import ctypes as c
    from ctypes import wintypes as w
    from refactor.desktop.delivery.windows_process_peer import ProcessPeer
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
            if entry.exe.casefold() == image.name.casefold():
                try:
                    with ProcessPeer(entry.pid) as peer:
                        if Path(peer.inspect()['executable']) == image:
                            raise RecoverableError(message)
                except OSError as error:
                    if error.winerror != 87: raise  # exited PID is harmless
            present = kernel.Process32NextW(handle, c.byref(entry))
    finally: kernel.CloseHandle(handle)


def require_app_stopped(root):
    _require_image_stopped(app_path(root) / 'MentorFlow.exe', 'close the current App before continuing')


def require_updater_stopped(root):
    _require_image_stopped(Path(root) / 'Updater/UserRuntime.exe',
                          '请先关闭 MentorFlow 修复与更新窗口或正在运行的更新程序，再重试。')


def request_app_shutdown(root, pid, hwnd_hex):
    from refactor.desktop.delivery.windows_process_peer import ProcessPeer
    import ctypes as c
    from ctypes import wintypes as w
    with ProcessPeer(pid) as peer:
        peer.require(executable=app_path(root)/'MentorFlow.exe', integrity_rid=8192)
        peer.visible_responsive_window(hwnd_hex)
        user = c.WinDLL('user32'); user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        if not user.PostMessageW(int(hwnd_hex, 16), 0x10, 0, 0): raise c.WinError()
        deadline = time.monotonic() + 15
        while peer.alive() and time.monotonic() < deadline: time.sleep(.05)
        if peer.alive(): raise AppShutdownTimeout('current app did not close; not replaced')


class AppShutdownTimeout(RecoverableError):
    """The legacy close request could not stop an older App instance."""


def wait_for_app_exit(root, pid, hwnd_hex, *, timeout=30, ready=lambda: None):
    """Wait for a new App to exit itself; never send it a window close message."""
    from refactor.desktop.delivery.windows_process_peer import ProcessPeer
    with ProcessPeer(pid) as peer:
        peer.require(executable=app_path(root) / 'MentorFlow.exe', integrity_rid=8192)
        peer.visible_responsive_window(hwnd_hex)
        # Keep this native handle open across the acknowledgement and wait.
        # Opening it after the App quits would race PID reuse/process removal.
        ready()
        deadline = time.monotonic() + timeout
        while peer.alive() and time.monotonic() < deadline:
            time.sleep(.05)
        if peer.alive():
            raise AppShutdownTimeout('app self-exit timed out; update remains pending')


def launch_coordination(root):
    """Share the existing nonblocking lock across launch and update handoff.

    The transaction still owns its own installation lock. This outer gate
    prevents a shortcut from starting the old App between PID exit and swap.
    """
    from refactor.desktop.delivery.update_download import InstallUpdateLock
    directory = plain(target(root) / 'launch-operation')
    directory.mkdir(exist_ok=True)
    return InstallUpdateLock(directory)


def uninstall_per_user(root):
    """Conservative, recoverable uninstall. Never removes user data/unknown files.

    Quarantine signed current App and identity, retaining updater, previous and
    diagnostics for explicit later cleanup. UI owns its shortcuts/registration.
    """
    root = target(root); release = installed(root)
    require_app_stopped(root)
    from refactor.desktop.delivery.update_download import InstallUpdateLock
    with InstallUpdateLock(root):
        verify_tree(root/'App', release)
        quarantine = root/('uninstalled-' + uuid.uuid4().hex); quarantine.mkdir()
        _rename_directory(root, root/'App', quarantine/'App', component='UNINSTALL_APP')
        for name in (CURRENT, STATE, HIGHEST, JOURNAL, 'pending-update.json'):
            if (root/name).exists(): os.replace(root/name, quarantine/name)
    return dict(status='UNINSTALLED', quarantine=str(quarantine), unknown_files_preserved=True,
                user_data_deleted=False, registration_cleanup='CALLER_OWNED')


def verify_tree(app, release, *, subset=None):
    """Independent final read/hash; no receipt, timestamps or parent ACL trust."""
    from refactor.desktop.delivery.package_verification import sha256_file
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
    app = app_path(root)
    if Path(executable).absolute() != app / 'MentorFlow.exe':
        raise SecurityError('per-user entry outside selected installation')
    from refactor.desktop.delivery.gui_bootstrap_trust import CLOSURE
    verify_tree(app, release, subset=[CLOSURE])
    closure = gui_configuration(root, release)
    verify_tree(app, release, subset=closure)
    return release


def gui_configuration(root, release):
    """Loader namespace check after signed bytes are verified or stat-reused."""
    from refactor.desktop.delivery.gui_bootstrap_trust import CLOSURE, closure_paths
    app = app_path(root)
    closure = closure_paths(strict_json(read(app / CLOSURE)))
    if not set(closure).issubset(release.rows):
        raise SecurityError('unsigned bootstrap file')
    # Qt scans all platform plugins, not just qwindows. The signed closure
    # cannot be bypassed by placing an extra DLL or qt.conf beside it.
    for item in (app / '_internal/PySide6/plugins/platforms').iterdir():
        if not item.is_file() or item.relative_to(app).as_posix() not in closure:
            raise SecurityError('unknown bootstrap platform plugin')
    for name in ('qt.conf', '_internal/qt.conf', '_internal/PySide6/qt.conf'):
        if (app / name).exists() and name not in closure:
            raise SecurityError('unverified Qt loader configuration')
    return closure


def admit(root, envelope, public_keys, *, repair=False):
    current = installed(root, public_keys) if is_install(root) else None
    previous = read(plain(root / HIGHEST)) if (root / HIGHEST).exists() else None
    epoch, fingerprint = witness_version(previous, public_keys, channel='stable') if previous is not None else (0, '')
    release = verify_release(envelope, public_keys, current=current.version if current else '0.0.0',
                             highest_seen=epoch, previous_envelope=previous)
    if current:
        from refactor.desktop.delivery.update_protocol import require_monotonic_floor
        require_monotonic_floor(release, current)
    if release.signed['withdrawn']:
        raise SecurityError('withdrawn release refused')
    if release.signed['metadata_version'] == epoch and digest(canonical(release.signed)) != fingerprint:
        raise SecurityError('same-epoch metadata equivocation')
    if current and version_key(release.version) <= version_key(current.version) and not repair:
        raise SecurityError('completed release replay/downgrade refused')
    if current and repair and release.version == current.version and release.manifest_bytes != current.manifest_bytes:
        raise SecurityError('same-version replacement refused')
    return current, release


def _initial_repair_release(root, public_keys, expected=None):
    """Read-only admission of an incomplete first install, never journal authority.

    Require independently signed identity AND a complete matching local App.
    Old staging/failed directories are evidence only: never delete, overwrite,
    or follow a journal-supplied path. A retry still verifies fresh signed input
    and copies it to a new stage. Unknown root neighbors are left untouched.
    """
    root = plain(root)
    for name in (STATE, CURRENT, HIGHEST, JOURNAL, 'operation.lock', 'App', 'Updater'):
        plain(root / name)
    if (root / STATE).exists() or not (root / HIGHEST).is_file() or not (root / JOURNAL).is_file():
        raise SecurityError('unknown non-empty installation target')
    doc = strict_json(read(root / JOURNAL))
    if (type(doc) is not dict or
            set(doc) != {'schema', 'attempt', 'phase', 'target', 'base', 'base_manifest'} or
            doc['schema'] != 'mentorflow.per-user-transaction.v1' or
            type(doc['attempt']) is not str or re.fullmatch('[0-9a-f]{32}', doc['attempt']) is None or
            doc['phase'] not in ('PREPARING', 'VERIFIED', 'NEW_ACTIVE', 'INITIAL_FAILED') or
            doc['base'] is not None or doc['base_manifest'] is not None):
        raise SecurityError('unproven initial-install recovery hint')
    release = historical(read(root / HIGHEST), public_keys)
    if release.signed['withdrawn'] or doc['target'] != release.version:
        raise SecurityError('initial-install signed identity mismatch')
    if expected is not None and canonical(release.signed) != canonical(expected.signed):
        raise SecurityError('initial repair requires the same signed release')
    if (root / CURRENT).exists():
        current = historical(read(root / CURRENT), public_keys)
        if canonical(current.signed) != canonical(release.signed):
            raise SecurityError('initial-install current identity mismatch')
    lock = root / 'operation.lock'
    # Do not reread the locked byte through a second handle on Windows.
    if lock.exists() and (not lock.is_file() or lock.stat().st_size != 1):
        raise SecurityError('unknown initial-install lock file')
    # A surviving executable location cannot be treated as arbitrary leftovers.
    # Verify it fully before retaining/reusing it or moving App out of the way.
    if (root / 'Updater').exists():
        rows = {name[len('UserRuntime/'):]: row for name, row in release.rows.items()
                if name.startswith('UserRuntime/')}
        if not rows: raise SecurityError('unsigned initial-install updater')
        verify_tree(root / 'Updater', replace(release, rows=rows))
    candidates = []
    if (root / 'App').exists():
        verify_tree(root / 'App', release)
        candidates.append(root / 'App')
    stage = plain(root / ('staging-' + doc['attempt']))
    if stage.is_dir(): candidates.append(plain(stage / 'App'))
    for item in root.iterdir():
        if re.fullmatch('failed-[0-9a-f]{32}', item.name):
            candidates.append(plain(item))
    for app in candidates:
        try:
            verify_tree(app, release)
        except SecurityError:
            # Incomplete/foreign evidence cannot authorize writes and stays put.
            continue
        return release
    raise SecurityError('no independently verified initial-install App remains')


def _copy(source, destination, release, progress):
    total = sum(row['size'] for row in release.rows.values()); copied = 0
    for name, row in release.rows.items():
        path = destination / name; path.parent.mkdir(parents=True, exist_ok=True)
        with (source / name).open('rb') as incoming, path.open('xb') as outgoing:
            while block := incoming.read(1024 * 1024):
                outgoing.write(block); copied += len(block)
                progress(min(80, int(copied * 80 / max(1, total))))


def _restore_transaction_runtime(root, attempt, public_keys=None):
    """Restore the runtime backup named by this transaction's fixed attempt."""
    if attempt is None: return  # Compatibility with pre-marker transaction journals.
    if not isinstance(attempt, str) or re.fullmatch(r'[0-9a-f]{32}', attempt) is None:
        raise SecurityError('invalid transaction attempt')
    stage = plain(root / ('staging-' + attempt))
    marker = plain(stage / 'previous-updater.json')
    if not marker.is_file(): return
    state = strict_json(read(marker))
    if type(state) is not dict or set(state) != {'had_binding'} or type(state['had_binding']) is not bool:
        raise SecurityError('invalid runtime recovery marker')
    backup = plain(stage / 'previous-updater')
    active = plain(root / 'Updater')
    binding_backup = plain(stage / 'previous-updater-release.json')
    if not binding_backup.is_file():
        raise RecoveryEvidenceMissing('恢复证据缺失：previous-updater-release.json')
    old_release = historical(read(binding_backup), public_keys if public_keys is not None else keys())
    old_runtime = _runtime_release(old_release)
    if backup.exists():
        verify_tree(backup, old_runtime)
        failed = plain(stage / 'failed-updater')
        if active.exists():
            if failed.exists(): failed = plain(stage / ('failed-updater-' + uuid.uuid4().hex))
            _recovery_rename(root, active, failed, component='UPDATER_TRANSACTION_DISCARD')
        _recovery_rename(root, backup, active, component='UPDATER_TRANSACTION_RESTORE')
    elif not active.exists():
        raise RecoveryEvidenceMissing('恢复证据缺失：Updater 与 previous-updater 均不存在')
    else:
        # Recovery may resume after the directory move succeeded but before
        # restoring its binding. Only accept the signed runtime from that phase.
        verify_tree(active, old_runtime)

    binding = plain(root / UPDATER_RELEASE)
    if state['had_binding']:
        if not binding_backup.is_file():
            raise RecoveryEvidenceMissing('恢复证据缺失：previous-updater-release.json')
        atomic(binding, read(binding_backup))
    else:
        binding.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    binding_backup.unlink(missing_ok=True)


def recover(root, public_keys=None):
    """Recover interrupted swaps using only fixed owned paths and signed releases."""
    root = target(root); public_keys = public_keys if public_keys is not None else keys()
    journal = root / JOURNAL
    if not journal.exists(): return False
    doc = strict_json(read(journal))
    if doc.get('schema') != 'mentorflow.per-user-transaction.v1':
        raise SecurityError('unknown transaction journal')
    if doc.get('phase') in ('HEALTHY', 'ROLLED_BACK'): return False
    try:
        _restore_transaction_runtime(root, doc.get('attempt'), public_keys)
    except RecoveryEvidenceMissing as error:
        maintenance_log(root, 'recovery_incomplete', code='RECOVERY_EVIDENCE_MISSING')
        raise RecoverableError('恢复证据缺失，保留现场并等待修复') from error
    except (OSError, RenameStateChangedError, RecoverableError) as error:
        maintenance_log(root, 'recovery_incomplete', code='RECOVERY_INCOMPLETE')
        raise RecoverableError('恢复没完成，下次启动再试') from error
    previous = root / 'previous'
    if previous.is_dir() and (previous / 'release.json').is_file():
        old = historical(read(previous / 'release.json'), public_keys)
        # An earlier healthy update may already have left a previous version.
        # Do not confuse that older generation with THIS transaction's base.
        if old.version == doc.get('base') and digest(old.manifest_bytes) == doc.get('base_manifest'):
            if (previous / 'App').exists():
                verify_tree(previous / 'App', old)
                if (root / 'App').exists():
                    failed = root / ('failed-' + uuid.uuid4().hex)
                    _recovery_rename(root, root / 'App', failed, component='APP_ROLLBACK_DISCARD')
                _recovery_rename(root, previous / 'App', root / 'App', component='APP_ROLLBACK_RESTORE')
            else:
                # OLD_MOVED may never have happened, or the restore rename
                # finished before its binding write. Only the signed old tree
                # at the live path proves either case; CURRENT can still be new.
                verify_tree(root / 'App', old)
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
             repair=False, before_swap=lambda: None, fault=lambda phase: None, refresh_runtime=False,
             prepared=False, allow_deferred_stage=False):
    """Stage -> verify -> swap -> real health; previous remains recoverable."""
    public_keys = public_keys if public_keys is not None else keys()
    root = target(root, probe=True); source = plain(source)
    deferred_attempt = None
    if allow_deferred_stage:
        # This path is only used after a verified APP_ACTIVATE denial. Bind it
        # to the rolled-back journal before allowing an internal source tree.
        match = re.fullmatch(r'staging-([0-9a-f]{32})', source.parent.name)
        if (match is None or source.name != 'App' or source.parent.parent != root):
            raise SecurityError('invalid deferred prepared source')
        deferred_attempt = match.group(1)
        prior_journal = strict_json(read(root / JOURNAL))
        if (deferred_attempt not in (prior_journal.get('attempt'),
                                     prior_journal.get('deferred_source_attempt')) or
                prior_journal.get('phase') != 'ROLLED_BACK'):
            raise SecurityError('deferred prepared source is not bound to rollback')
    elif source == root or source.is_relative_to(root) or root.is_relative_to(source):
        raise SecurityError('release source overlaps installed tree')
    exists = root.exists()
    initial_repair = exists and any(root.iterdir()) and not is_install(root)
    if initial_repair: _initial_repair_release(root, public_keys)
    with _install_stage(root, 'initial_signed_admission'):
        current, release = admit(root, envelope, public_keys, repair=repair)
    if initial_repair: _initial_repair_release(root, public_keys, release)
    # A distribution may contain a sibling Installer directory. Only signed
    # files are read/copied; the newly materialized tree must match exactly.
    # Ordinary installers may include an unsigned sibling Installer directory.
    # Prepared updates have an exact file inventory, held write-denied through
    # activation. Invalid/old receipts retain the legacy verified copy path.
    # Check the receipt once, under the writer lock. An optimistic ready()
    # inventory here repeated every file identity check immediately before the
    # locked lease, without extending the immutable interval used for activation.
    reuse_candidate = prepared and source.drive.casefold() == root.drive.casefold()
    if not prepared:
        with _install_stage(root, 'source_full_verification'):
            verify_tree(source, release, subset=list(release.rows))
    required = sum(r['size'] for r in release.rows.values()) + 64 * 1024**2
    free = shutil.disk_usage(root.parent).free
    if free < required: raise RecoverableError(low_space_message(required, free))
    root.mkdir(exist_ok=True)  # parent exists; only the MentorFlow level is created
    from refactor.desktop.delivery.update_download import InstallUpdateLock
    from contextlib import ExitStack
    with InstallUpdateLock(root), ExitStack() as verified_files:
        from refactor.desktop.delivery.prepared_update import lease, inventory
        from refactor.desktop.delivery.startup_window import set_startup_text
        set_startup_text('正在更新 MentorFlow：核对已下载的文件…', root=root)
        with _install_stage(root, 'prepared_inventory_locked'):
            reuse = reuse_candidate and verified_files.enter_context(lease(source, release))
        maintenance_log(root, 'prepared_inventory_result', reused=bool(reuse), files=len(release.rows))
        if prepared and not reuse:
            with _install_stage(root, 'source_full_verification'):
                verify_tree(source, release, subset=list(release.rows))
        if refresh_runtime: require_updater_stopped(root)
        with _install_stage(root, 'locked_signed_admission'):
            current, release = admit(root, envelope, public_keys, repair=repair)
        if deferred_attempt is not None:
            locked_journal = strict_json(read(root / JOURNAL))
            if (deferred_attempt not in (locked_journal.get('attempt'),
                                         locked_journal.get('deferred_source_attempt')) or
                    locked_journal.get('phase') != 'ROLLED_BACK' or
                    locked_journal.get('target') != release.version):
                raise SecurityError('deferred prepared source changed before transaction')
        if initial_repair: _initial_repair_release(root, public_keys, release)
        if not initial_repair and (root / JOURNAL).exists() and strict_json(read(root / JOURNAL)).get('phase') not in ('HEALTHY', 'ROLLED_BACK'):
            raise RecoverableError('previous transaction requires recovery before new apply')
        if current and (root / 'Updater').exists() and not (root / UPDATER_RELEASE).exists():
            verify_recovery_runtime(root, current)
            atomic(root / UPDATER_RELEASE, current.envelope)
        attempt = uuid.uuid4().hex
        stage = root / ('staging-' + attempt); stage.mkdir()
        journal = dict(schema='mentorflow.per-user-transaction.v1', attempt=attempt, phase='PREPARING', target=release.version,
                       base=current.version if current else None, base_manifest=digest(current.manifest_bytes) if current else None)
        if deferred_attempt is not None:
            journal['deferred_source_attempt'] = deferred_attempt
        atomic(root / JOURNAL, canonical(journal))
        committed = False
        def mark(phase):
            nonlocal committed
            journal['phase'] = phase; atomic(root / JOURNAL, canonical(journal))
            if phase == 'HEALTHY': committed = True
            fault(phase)
        moved = False; base_closed = False; activated = False
        activation_denied = None
        deferred_exception = None
        restored_previous = False
        recovery_incomplete = False
        try:
            set_startup_text('正在更新 MentorFlow：安装文件…', root=root)
            if reuse:
                from refactor.desktop.delivery.prepared_update import RECEIPT
                ready_inventory = strict_json(read(source.parent / RECEIPT))['files']
                # Windows refuses renaming a directory with open child handles,
                # even with FILE_SHARE_DELETE. Release immediately at rename;
                # the cache/install writer locks still own the whole operation.
                verified_files.close()
                move_started = time.perf_counter()
                _rename_directory(root, source, stage / 'App', component='APP_STAGING_MOVE')
                maintenance_log(root, 'prepared_tree_moved', files=len(release.rows),
                                bytes=sum(r['size'] for r in release.rows.values()),
                                seconds=time.perf_counter()-move_started)
            else:
                _copy(source, stage / 'App', release, progress)
            # Cache/staging may be writable: recheck current signed metadata and
            # ALL copied bytes immediately before invoking the swap boundary.
            with _install_stage(root, 'activation_signed_admission'):
                _, release = admit(root, envelope, public_keys, repair=repair)
            with _install_stage(root, 'moved_inventory'):
                verified_inventory = verified_files.enter_context(inventory(stage / 'App', release))
            if not reuse or verified_inventory != ready_inventory:
                # One final independent hash pass; hold these exact objects
                # against writes until the swap (including the close callback).
                with _install_stage(root, 'activation_full_verification'):
                    verify_tree(stage / 'App', release)
            mark('VERIFIED'); progress(85)
            atomic(root / HIGHEST, release.envelope)
            from refactor.desktop.delivery.per_user_update_core import unsafe_teardown
            if unsafe_teardown(): raise RecoverableError('teardown: update remains pending')
            try:
                before_swap()
            except PermissionError as error:
                raise SecurityError('verified staging has a concurrent writer') from error
            base_closed = True
            # Check names/identity again (not SHA) and release child handles at
            # the native rename boundary. No other product writer can enter.
            with _install_stage(root, 'activation_identity_recheck'), inventory(stage / 'App', release) as final_inventory:
                if final_inventory != verified_inventory:
                    raise SecurityError('verified staging changed before activation')
            verified_files.close()
            if unsafe_teardown(): raise RecoverableError('teardown: update remains pending')
            if initial_repair: _initial_repair_release(root, public_keys, release)
            if initial_repair and (root / 'App').exists():
                # Interrupted before STATE publication: retain only a fully
                # signed App, never move unknown content out of the user's way.
                verify_tree(root / 'App', release)
                require_app_stopped(root)
                _rename_directory(root, root / 'App', root / ('failed-' + uuid.uuid4().hex),
                                  component='APP_REPAIR_QUARANTINE')
            if current:
                previous = root / 'previous'
                if previous.exists():
                    # Retain older known versions; cleanup is a later operation.
                    _rename_directory(root, previous, root / ('retired-' + uuid.uuid4().hex),
                                      component='APP_PREVIOUS_RETIRE')
                previous.mkdir(); atomic(previous / 'release.json', current.envelope)
                if app_path(root) == root:
                    # Migrate only through the existing transaction. Preserve
                    # ALL original flat files and Inno records at their original
                    # paths; materialize a signed previous without deleting them.
                    _copy(root, previous / 'App', current, lambda _: None)
                else:
                    _rename_directory(root, root / 'App', previous / 'App', component='APP_PREVIOUS_SAVE')
                moved = True; mark('OLD_MOVED')
            try:
                _rename_directory(root, stage / 'App', root / 'App', component='APP_ACTIVATE',
                                  diagnose=True, budget_seconds=3.0)
            except OSError as activation_error:
                if getattr(activation_error, 'winerror', None) in (5, 32, 33):
                    activation_denied = activation_error
                raise
            moved = True; activated = True
            atomic(root / CURRENT, release.envelope)
            atomic(root / STATE, canonical(dict(schema=SCHEMA, mode='per_user', app_id=configured().app_id)))
            if not (root / 'Updater').exists():
                shutil.copytree(root / 'App/UserRuntime', root / 'Updater')
                atomic(root / UPDATER_RELEASE, release.envelope)
            # Installer owns the writer lock and requires the Updater stopped.
            # Finish its replacement BEFORE launching the App's classifier.
            # Keep the old pair until visible health succeeds, for rollback.
            old_runtime = stage / 'previous-updater'
            old_binding = read(root / UPDATER_RELEASE) if (root / UPDATER_RELEASE).exists() else None
            runtime_backup_ready = False
            if refresh_runtime:
                shutil.copytree(root / 'Updater', old_runtime)
                if old_binding is not None:
                    atomic(stage / 'previous-updater-release.json', old_binding)
                elif current is not None:
                    atomic(stage / 'previous-updater-release.json', current.envelope)
                else:
                    atomic(stage / 'previous-updater-release.json', release.envelope)
                atomic(stage / 'previous-updater.json', canonical({'had_binding': old_binding is not None}))
                runtime_backup_ready = True
            runtime = replace_updater(root, release, public_keys=public_keys) if refresh_runtime else None
            from refactor.desktop.delivery.startup_integrity import remember_verified, verified_stat_snapshot
            verified_stats = verified_stat_snapshot(root, release)
            mark('NEW_ACTIVE'); progress(95)
            set_startup_text('更新已安装，正在打开 MentorFlow…', root=root)
            with _install_stage(root, 'new_app_health'):
                proof = (health or launch_health)(root, release)
            if not isinstance(proof, dict) or proof.get('ready') is not True:
                raise RecoverableError('new version did not become ready')
            successful_result = dict(status='HEALTHY', version=release.version, health=proof,
                                     install_uac_count=0, update_uac_count=0,
                                     **({'updater': runtime} if runtime else {}))
            mark('HEALTHY'); progress(100)
            try:
                remember_verified(root, release, stats=verified_stats)
            except OSError:
                # An optimization record cannot turn a healthy install into a
                # failed transaction. Missing record means a full check next time.
                import logging
                logging.getLogger(__name__).warning('integrity stat record unavailable', exc_info=True)
            if old_runtime.exists():
                try: shutil.rmtree(old_runtime)
                except OSError: pass  # committed install; cleanup cannot undo health
            return successful_result
        except BaseException as operation_error:
            if committed:
                # Visible health and the durable commit already agree. A late
                # progress/cancellation/diagnostic failure cannot roll back just
                # the runtime while recover() correctly keeps the healthy App.
                maintenance_log(root, 'healthy_post_commit_failure',
                                error_type=type(operation_error).__name__)
                if not isinstance(operation_error, Exception): raise
                return successful_result
            try:
                if refresh_runtime and locals().get('runtime_backup_ready'):
                    _restore_transaction_runtime(root, attempt, public_keys)
                if current and moved:
                    recover(root, public_keys)
                    # A bad target must not leave the user with a closed application.
                    recovery = (health or launch_health)(root, current)
                    if not isinstance(recovery, dict) or recovery.get('ready') is not True:
                        raise RecoverableError('previous restored but health not confirmed')
                    atomic(root / 'last-recovery.json', canonical(dict(attempt=journal['attempt'], version=current.version, health=recovery)))
                    restored_previous = True
                elif current:
                    if isinstance(operation_error, (OSError, RenameStateChangedError)):
                        raise RecoverableError('恢复没完成，下次启动再试') from operation_error
                    atomic(root / JOURNAL, canonical({**journal, 'phase': 'ROLLED_BACK'}))
                    from refactor.desktop.delivery.per_user_update_core import unsafe_teardown
                    if base_closed and not unsafe_teardown(): (health or launch_health)(root, current)
                else:
                    if activated and (root / 'App').exists():
                        _recovery_rename(root, root / 'App', root / ('failed-' + uuid.uuid4().hex),
                                          component='APP_INITIAL_QUARANTINE')
                    if activated:
                        (root / STATE).unlink(missing_ok=True)
                        (root / CURRENT).unlink(missing_ok=True)
                    atomic(root / JOURNAL, canonical({**journal, 'phase': 'INITIAL_FAILED'}))
            except (OSError, RenameStateChangedError, RecoverableError) as recovery_error:
                recovery_incomplete = True
                maintenance_log(root, 'recovery_incomplete', code='DIRECTORY_RENAME_RETRY_EXHAUSTED')
                raise RecoverableError('恢复没完成，下次启动再试') from recovery_error
            if activation_denied is not None and restored_previous:
                deferred_exception = DeferredAppActivation(getattr(activation_denied, 'winerror', None))
                raise deferred_exception from operation_error
            raise

        finally:
            # Failed before activation: put the same verified download back so
            # the existing next-start flow can retry it. Never consume pending
            # content just because the App could not close.
            verified_files.close()
            preserve_deferred = deferred_exception is not None
            if preserve_deferred and (stage / 'App').exists():
                # Keep the verified prepared bytes in their transaction tree.
                # The same live App handle that denied activation may also deny
                # moving them back to the downloader cache.
                deferred_exception.prepared_stage = str(stage / 'App')
            if reuse and not activated and (stage / 'App').exists() and not source.exists():
                if not preserve_deferred:
                    _recovery_rename(root, stage / 'App', source, component='APP_STAGING_RESTORE')
                else:
                    maintenance_log(root, 'prepared_tree_retained_for_next_start',
                                    attempt=attempt, target=release.version)
            # This attempt's copy is never needed for resume; the independently
            # verified download remains outside the installation until applied.
            if (stage.exists() and not recovery_incomplete and not preserve_deferred and
                    not (stage / 'previous-updater').exists()):
                _remove_program_tree(root, stage)


# Health means a visible responsive App window, not background inventory or feed.
STARTUP_HEALTH_TIMEOUT = 180


def app_launch_options():
    """Pass the user's launcher foreground permission to its normal App child."""
    if os.name != 'nt': return {}
    import ctypes
    # ASFW_ANY must precede Popen; granting the returned PID can lose the race
    # against the child's initial showEvent. Windows may still reject a grant
    # from a background caller; do not force focus or attach input threads.
    from refactor.desktop.delivery.startup_window import allow_foreground
    allow_foreground(0xffffffff)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 1  # SW_SHOWNORMAL
    return {'startupinfo': startup}


def launch_health(root, release, *, timeout=STARTUP_HEALTH_TIMEOUT):
    from refactor.desktop.delivery.windows_update_pipe import Listener
    from refactor.desktop.delivery.windows_process_peer import ProcessPeer
    attempt = uuid.uuid4().hex; nonce = os.urandom(32).hex()
    process = None
    with Listener(attempt, 'health') as channel:
        try:
            from refactor.desktop.delivery.per_user_update_core import unsafe_teardown
            if unsafe_teardown():
                raise RecoverableError('session ending: App launch deferred')
            app = app_path(root)
            process = subprocess.Popen([str(app / 'MentorFlow.exe'), '--install-mode', 'per_user',
                '--install-root', str(root), '--update-attempt', attempt, '--update-nonce', nonce],
                cwd=app, close_fds=True, **app_launch_options())
            pid = channel.accept(timeout=timeout)
            if pid != process.pid: raise SecurityError('health came from another process')
            signal = channel.receive(timeout=5)
            if signal.get('attempt_id') != attempt or signal.get('nonce') != nonce or signal.get('version') != release.version or signal.get('manifest_sha256') != digest(release.manifest_bytes):
                raise SecurityError('health release/correlation mismatch')
            with ProcessPeer(pid) as peer:
                peer.require(executable=app / 'MentorFlow.exe', integrity_rid=8192)
                peer.visible_responsive_window(signal['hwnd_hex'])
            channel.send({'health_received': True})
            return dict(ready=True, pid=pid, version=release.version, hwnd_hex=signal['hwnd_hex'])
        except BaseException:
            if process is not None and process.poll() is None:
                process.terminate(); process.wait(timeout=10)  # only this owned child
            raise


def notify_health(root, gate, attempt, nonce):
    if not attempt: return
    from refactor.desktop.delivery.windows_update_pipe import connect
    from refactor.desktop.delivery.windows_process_peer import ProcessPeer
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
    from refactor.desktop.delivery.per_user_update_core import unsafe_teardown
    from refactor.desktop.delivery.per_user_update_core import remember_download
    if unsafe_teardown():
        return remember_download(root, stage, envelope, state='PENDING_INSTALL')
    from refactor.desktop.delivery.windows_process_peer import ProcessPeer
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


def handoff_pending(root, *, hwnd_hex, reason='normal'):
    """Continue one consent in the stable ordinary-token runtime, not this App."""
    from refactor.desktop.delivery.per_user_update_core import read_pending
    from refactor.desktop.delivery.per_user_update_core import unsafe_teardown
    from refactor.desktop.delivery.per_user_update_core import PENDING
    from refactor.desktop.delivery.windows_process_peer import ProcessPeer
    root = target(root); pending = read_pending(root)
    if unsafe_teardown(reason):
        atomic(root / PENDING, canonical({**pending, 'state': 'PENDING_INSTALL'}))
        return dict(state='PENDING_INSTALL', destructive_apply=False, relaunched=False)
    with ProcessPeer(os.getpid()) as peer:
        peer.require(executable=app_path(root) / 'MentorFlow.exe', integrity_rid=8192)
        peer.visible_responsive_window(hwnd_hex)
    runtime = verify_recovery_runtime(root)
    result = root / ('one-click-' + uuid.uuid4().hex + '.json')
    ready = result.with_suffix('.ready.json')
    refreshed = refresh_updater(root)
    if refreshed['status'] not in ('UPDATED', 'UP_TO_DATE'):
        raise RecoverableError('cannot refresh the update coordinator before handoff')
    runtime = verify_recovery_runtime(root)
    from refactor.desktop.delivery.startup_window import allow_foreground
    allow_foreground(0xffffffff, root=root, stage='app_to_updater')
    process = subprocess.Popen([str(runtime), 'apply-latest',
        '--root', str(root), '--pid', str(os.getpid()), '--hwnd-hex', hwnd_hex,
        '--result', str(result), '--app-self-exit', '--ready-file', str(ready)], cwd=root, close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 30
    while not ready.exists():
        if process.poll() is not None or time.monotonic() >= deadline:
            raise RecoverableError('update coordinator did not acknowledge the App')
        time.sleep(.05)
    acknowledgement = strict_json(read(ready))
    ready.unlink(missing_ok=True)
    if acknowledgement != dict(app_pid=os.getpid(), coordinator_pid=process.pid):
        raise RecoverableError('unexpected update coordinator acknowledgement')
    return dict(state='APPLYING', coordinator_started=True, coordinator_pid=process.pid,
                result_path=str(result), update_uac_count=0, app_exit='self')


def handoff_repair(root, *, hwnd_hex=None, shell=False):
    from refactor.desktop.delivery.windows_process_peer import ProcessPeer
    runtime = verify_recovery_runtime(root)
    command = [str(runtime), 'repair-update' if shell else 'repair-installation', '--root', str(root)]
    if not shell:
        with ProcessPeer(os.getpid()) as peer:
            peer.require(executable=app_path(root)/'MentorFlow.exe', integrity_rid=8192)
            peer.visible_responsive_window(hwnd_hex)
        command += ['--pid', str(os.getpid()), '--hwnd-hex', hwnd_hex]
    process = subprocess.Popen(command, cwd=root, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return dict(state='APPLYING', coordinator_started=True, coordinator_pid=process.pid, update_uac_count=0)


def coordinate(root, intent_path):
    from refactor.desktop.delivery.windows_process_peer import ProcessPeer
    root = target(root); intent = strict_json(read(intent_path))
    if set(intent) != {'schema', 'root', 'stage', 'release_sha256', 'pid', 'hwnd_hex'} or intent['schema'] != 'mentorflow.per-user-intent.v1' or Path(intent['root']) != root:
        raise SecurityError('invalid per-user update intent')
    stage = plain(intent['stage']); envelope = read(stage.parent / 'release.json')
    if digest(envelope) != intent['release_sha256']: raise SecurityError('prepared release changed')
    def close_base():
        # Compatibility for pre-self-exit Apps only. New Apps use apply-latest
        # with a native-handle acknowledgement before exiting themselves.
        request_app_shutdown(root, intent['pid'], intent['hwnd_hex'])
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
        from refactor.desktop.delivery.per_user_launcher import launch
        return launch(args.root)
    if args.action == 'repair-update':
        from refactor.desktop.delivery.per_user_launcher import repair_update_shell
        return repair_update_shell(args.root)
    if args.action == 'startup-state':
        from refactor.desktop.delivery.per_user_update_core import get_startup_update_state
        return get_startup_update_state(args.root)
    if args.action == 'repair-installation':
        from refactor.desktop.delivery.per_user_update_core import repair_installation
        return repair_installation(args.root, pid=args.pid, hwnd_hex=args.hwnd_hex)
    if args.action == 'bootstrap-install':
        from refactor.desktop.delivery.bootstrap_install import bootstrap_install
        return bootstrap_install(args.root, args.status_file, args.cancel_file, args.result,
                                args.close_after_health)
    if args.action in ('check-latest', 'download-latest', 'update-to-latest', 'apply-latest'):
        from refactor.desktop.delivery import per_user_update_core as core
        cancelled = lambda: bool(args.cancel_file and args.cancel_file.exists())
        if args.action == 'check-latest': return core.check_latest(args.root)
        if args.action == 'download-latest': return core.download_latest(args.root, cancel=cancelled)
        if args.action == 'apply-latest':
            if args.app_self_exit:
                ready = plain(args.ready_file)
                if ready.parent != target(args.root) or not ready.name.startswith('one-click-'):
                    raise SecurityError('unexpected handoff acknowledgement path')
                try:
                    wait_for_app_exit(args.root, args.pid, args.hwnd_hex,
                        ready=lambda: atomic(ready, canonical(dict(
                            app_pid=args.pid, coordinator_pid=os.getpid()))))
                except AppShutdownTimeout:
                    pending = core.read_pending(args.root)
                    atomic(Path(args.root) / core.PENDING,
                           canonical({**pending, 'state': 'PENDING_INSTALL'}))
                    return dict(state='PENDING_INSTALL', message=core.PENDING_MESSAGE)
                try:
                    return core.apply_and_relaunch(args.root, reason='next_launch')
                except (OSError, RuntimeError, ValueError) as error:
                    maintenance_log(args.root, 'handoff_install_deferred', detail=str(error))
                    # The App has already closed. Bring back the signed current
                    # version; keep the downloaded update for normal startup.
                    if core.read_pending(args.root)['state'] != 'PREVIOUS_HEALTHY':
                        launch_health(args.root, installed(args.root))
                    return dict(state='PENDING_INSTALL', message=core.PENDING_MESSAGE)
            return core.apply_and_relaunch(args.root, pid=args.pid, hwnd_hex=args.hwnd_hex,
                                           reason=args.exit_reason, wait_for_exit=args.app_self_exit)
        return core.update_to_latest(args.root, cancel=cancelled, pid=args.pid,
                                     hwnd_hex=args.hwnd_hex, reason=args.exit_reason)
    if args.action == 'prepare-update':
        from refactor.desktop.delivery.per_user_update_core import prepare_update
        return prepare_update(args.root)
    if args.action == 'apply-pending':
        from refactor.desktop.delivery.per_user_update_core import apply_update
        return apply_update(args.root, pid=args.pid, hwnd_hex=args.hwnd_hex, reason=args.exit_reason)
    if args.action == 'update-status':
        from refactor.desktop.delivery.per_user_update_core import status
        return status(args.root)
    if args.action == 'uninstall': return uninstall_per_user(args.root)
    if args.action == 'shutdown':
        request_app_shutdown(args.root, args.pid, args.hwnd_hex)
        return dict(status='APP_CLOSED')
    if args.action == 'apply': return coordinate(args.root, args.intent)
    if args.action == 'recover': return dict(recovered=recover(args.root))
    root = target(args.root, probe=True)
    if args.action == 'preflight':
        if is_install(root): installed(root)
        return dict(status='PASS', **validate_target(root), install_uac_count=0)
    if args.source is None or args.release is None: parser.error('source and signed release required')
    envelope = read(args.release)
    if is_install(root) and (root / JOURNAL).exists() and strict_json(read(root / JOURNAL)).get('phase') not in ('HEALTHY', 'ROLLED_BACK'):
        # A process interruption may leave STATE published before health. The
        # shell coordinates the existing signed recovery API before retrying.
        # Reject bad incoming input before performing any recovery mutation.
        public_keys = keys()
        _, release = admit(root, envelope, public_keys, repair=True)
        verify_tree(args.source, release, subset=list(release.rows))
        from refactor.desktop.delivery.update_download import InstallUpdateLock
        with InstallUpdateLock(root):
            require_updater_stopped(root)
            require_app_stopped(root)
            from refactor.desktop.delivery.per_user_update_core import unsafe_teardown
            if unsafe_teardown(): raise RecoverableError('session ending: repair deferred')
            recover(root, public_keys)
    result = transact(root, args.source, envelope, repair=is_install(root), progress=progress, refresh_runtime=True,
                      before_swap=lambda: require_app_stopped(root))
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
    parser.add_argument('action', choices=('preflight', 'install', 'bootstrap-install', 'apply', 'recover',
        'prepare-update', 'apply-pending', 'update-status', 'uninstall', 'shutdown', 'launch',
        'check-latest', 'download-latest', 'update-to-latest', 'apply-latest',
        'startup-state', 'repair-update', 'repair-installation'))
    location = parser.add_mutually_exclusive_group(required=True)
    location.add_argument('--root', type=Path)
    location.add_argument('--parent', type=Path)
    parser.add_argument('--source', type=Path); parser.add_argument('--release', type=Path)
    parser.add_argument('--intent', type=Path)
    parser.add_argument('--result', type=Path)
    parser.add_argument('--status-file', type=Path)
    parser.add_argument('--cancel-file', type=Path)
    parser.add_argument('--close-after-health', action='store_true')
    parser.add_argument('--app-self-exit', action='store_true')
    parser.add_argument('--ready-file', type=Path)
    parser.add_argument('--pid', type=int); parser.add_argument('--hwnd-hex')
    parser.add_argument('--exit-reason', choices=('normal', 'next_launch', 'shutdown', 'logoff', 'unsafe'), default='normal')
    args = parser.parse_args()
    from refactor.desktop.delivery.windows_process_peer import ProcessPeer
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
        from refactor.desktop.delivery.per_user_runtime_session import runtime_session
        pid = args.pid
        if args.action == 'apply': pid = strict_json(read(args.intent))['pid']
        scope = (runtime_session(pid) if args.action in
                 ('install', 'bootstrap-install', 'apply', 'apply-pending', 'apply-latest', 'update-to-latest',
                  'recover', 'uninstall', 'repair-installation') else nullcontext())
        with scope:
            if args.action == 'apply-latest' and args.app_self_exit:
                from refactor.desktop.delivery.startup_window import run_with_native_splash
                with launch_coordination(args.root):
                    result = run_with_native_splash(lambda: _execute(args, parser, progress))
            else:
                result = _execute(args, parser, progress)
        if args.result: atomic(args.result, canonical(result))
        return result
    except Exception as error:
        if args.root is not None and (args.action != 'bootstrap-install' or is_install(args.root)):
            maintenance_log(args.root, 'runtime_operation_failed', action=args.action,
                            error_type=type(error).__name__, detail=str(error))
        if args.status_file:
            atomic(args.status_file, ('[install]\r\nstage=安装未完成\r\npercent=0\r\nerror='+str(error).replace('\n',' ')+'\r\n').encode('utf-16'))
        if args.result: atomic(args.result, canonical(dict(status='FAILED', error=type(error).__name__, detail=str(error))))
        raise


if __name__ == '__main__':
    try: print(canonical(main()).decode(), flush=True)
    except Exception as error:
        print(str(error), file=sys.stderr, flush=True)
        if sys.argv[1:2] == ['bootstrap-install']:
            from refactor.desktop.delivery.update_protocol import DownloadCancelled
            raise SystemExit(2 if isinstance(error, DownloadCancelled) else 1)
        raise SystemExit(3)
