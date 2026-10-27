"""Bounded directory-rename retry tests, including real Windows child handles."""
import ctypes
import os
from pathlib import Path
import shutil
import threading
import time
from types import SimpleNamespace

import pytest

from refactor import filesystem_ops as ops
from refactor.desktop.delivery import per_user_install as install
from refactor.desktop.delivery import per_user_update_core as core
from refactor.desktop.delivery.update_protocol import canonical
from tests.refactor.delivery.test_delivery_mature_update import Feed


def _hold_child_directory(path):
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                            ctypes.c_void_p)
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int
    handle = create_file(str(path), 0x0001, 0x00000007, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle, close_handle


def _signed_case(tmp_path, monkeypatch):
    feed = Feed(tmp_path)
    root = tmp_path / 'MentorFlow'
    monkeypatch.setattr(install, 'keys', lambda: feed.keys)
    monkeypatch.setattr(install, 'require_updater_stopped', lambda _root: None)
    monkeypatch.setattr(core, 'unsafe_teardown', lambda *_args: False)

    def publish(version, epoch):
        files = {**feed.old_files,
                 'UserRuntime/UserRuntime.exe': ('MZ ' + version).encode(),
                 'UserRuntime/_internal/dep.dll': ('runtime ' + version).encode()}
        feed.publish(version, files)
        feed.signed['metadata_version'] = epoch
        feed.sign(feed.signed)
        source = tmp_path / ('source-' + version)
        source.mkdir()
        for row in feed.signed['full_release']['assets']:
            path = source / row['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(feed.objects['/objects/' + row['sha256']])
        return source, feed.raw

    old_source, old_envelope = publish('2.0.3', 1)
    ready = lambda _root, release: {'ready': True, 'version': release.version}
    install.transact(root, old_source, old_envelope, health=ready)
    new_source, new_envelope = publish('2.0.5', 2)
    return feed, root, old_envelope, new_source, new_envelope, ready


def _tree_bytes(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def _release_on_nth_sleep(monkeypatch, source_for, component_to_lock, release_after):
    """Open a real child-directory handle at the selected product rename."""
    real_sleep = time.sleep
    real_rename = install.rename_with_retry
    state = {'handle': None, 'close': None, 'closed': False, 'sleeps': 0, 'opened': False}

    def controlled_sleep(delay):
        real_sleep(delay)
        state['sleeps'] += 1
        if release_after is not None and state['sleeps'] == release_after:
            state['close'](state['handle'])
            state['closed'] = True

    def controlled_rename(source, target, *, component, replace=False, **kwargs):
        if component == component_to_lock and not state['opened']:
            handle_path = source_for(Path(source), component)
            state['handle'], state['close'] = _hold_child_directory(handle_path)
            state['opened'] = True
        return real_rename(source, target, component=component, replace=replace, **kwargs)

    monkeypatch.setattr(ops.time, 'sleep', controlled_sleep)
    monkeypatch.setattr(install, 'rename_with_retry', controlled_rename)

    def close_if_needed():
        if state['opened'] and not state['closed']:
            state['close'](state['handle'])
            state['closed'] = True

    return state, close_if_needed


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows directory sharing semantics')
@pytest.mark.parametrize('release_delay', [.004, .055])
def test_child_share_handle_release_allows_directory_rename(tmp_path, release_delay):
    source = tmp_path / 'staging' / 'App'
    (source / '_internal').mkdir(parents=True)
    (source / 'payload.bin').write_bytes(b'complete verified tree')
    target = tmp_path / 'MentorFlow' / 'App'
    target.parent.mkdir()
    handle, close_handle = _hold_child_directory(source / '_internal')

    released = threading.Event()
    def release():
        time.sleep(release_delay)
        close_handle(handle)
        released.set()
    worker = threading.Thread(target=release, daemon=True)
    worker.start()
    ops.rename_with_retry(source, target, component='TEST_APP_ACTIVATE')
    worker.join(timeout=1)

    assert released.is_set()
    assert not source.exists()
    assert (target / 'payload.bin').read_bytes() == b'complete verified tree'


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows directory sharing semantics')
def test_child_share_handle_persistent_denial_preserves_both_paths(tmp_path):
    source = tmp_path / 'staging' / 'App'
    (source / '_internal').mkdir(parents=True)
    (source / 'payload.bin').write_bytes(b'old bytes')
    target = tmp_path / 'MentorFlow' / 'App'
    target.parent.mkdir()
    handle, close_handle = _hold_child_directory(source / '_internal')
    try:
        started = time.monotonic()
        with pytest.raises(PermissionError) as caught:
            ops.rename_with_retry(source, target, component='TEST_APP_ACTIVATE')
        assert caught.value.winerror == 5
        assert time.monotonic() - started >= .6
        assert source.is_dir() and not target.exists()
        assert (source / 'payload.bin').read_bytes() == b'old bytes'
    finally:
        close_handle(handle)


@pytest.mark.skipif(os.name != 'nt', reason='retry policy is Windows-specific')
@pytest.mark.parametrize('changed_endpoint', ['source', 'target'])
def test_endpoint_change_during_backoff_aborts_before_next_move(tmp_path, monkeypatch, changed_endpoint):
    source = tmp_path / 'staging' / 'App'
    source.mkdir(parents=True)
    (source / 'payload.bin').write_bytes(b'original')
    target = tmp_path / 'MentorFlow' / 'App'
    target.parent.mkdir()
    calls = []

    def denied(*_paths):
        calls.append('denied')
        error = PermissionError(5, 'sharing denied')
        error.winerror = 5
        raise error

    monkeypatch.setattr(ops, 'os', SimpleNamespace(name='nt', rename=denied, replace=denied))

    def mutate_endpoint(_delay):
        if changed_endpoint == 'source':
            source.rename(source.with_name('App-original'))
            source.mkdir()
            (source / 'payload.bin').write_bytes(b'replaced')
        else:
            target.mkdir()
            (target / 'foreign.bin').write_bytes(b'preserve target')

    monkeypatch.setattr(ops.time, 'sleep', mutate_endpoint)
    with pytest.raises(ops.RenameStateChangedError):
        ops.rename_with_retry(source, target, component='TEST_STATE_CHANGE')

    assert calls == ['denied']
    if changed_endpoint == 'source':
        assert (source / 'payload.bin').read_bytes() == b'replaced'
        assert (source.with_name('App-original') / 'payload.bin').read_bytes() == b'original'
    else:
        assert (source / 'payload.bin').read_bytes() == b'original'
        assert (target / 'foreign.bin').read_bytes() == b'preserve target'


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows directory sharing semantics')
def test_updater_recovery_preserves_backup_and_binding_until_next_start(tmp_path, monkeypatch):
    feed = Feed(tmp_path)
    root = tmp_path / 'MentorFlow'
    monkeypatch.setattr(install, 'keys', lambda: feed.keys)
    monkeypatch.setattr(install, 'require_updater_stopped', lambda _root: None)
    monkeypatch.setattr(core, 'unsafe_teardown', lambda *_args: False)

    def publish(version, epoch):
        files = {**feed.old_files,
                 'UserRuntime/UserRuntime.exe': ('MZ ' + version).encode(),
                 'UserRuntime/_internal/dep.dll': ('runtime ' + version).encode()}
        feed.publish(version, files)
        feed.signed['metadata_version'] = epoch
        feed.sign(feed.signed)
        source = tmp_path / ('source-' + version)
        source.mkdir()
        for row in feed.signed['full_release']['assets']:
            path = source / row['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(feed.objects['/objects/' + row['sha256']])
        return source, feed.raw

    old_source, old_binding = publish('2.0.3', 1)
    install.transact(root, old_source, old_binding,
                     health=lambda _root, release: {'ready': True, 'version': release.version})
    new_source, new_binding = publish('2.0.5', 2)
    attempt = 'a' * 32
    stage = root / ('staging-' + attempt)
    backup = stage / 'previous-updater'
    import shutil
    shutil.copytree(root / 'Updater', backup)
    (stage / 'previous-updater-release.json').write_bytes(old_binding)
    (stage / 'previous-updater.json').write_bytes(canonical({'had_binding': True}))
    (root / install.JOURNAL).write_bytes(canonical(dict(
        schema='mentorflow.per-user-transaction.v1', attempt=attempt, phase='NEW_ACTIVE',
        target='2.0.5', base='2.0.3', base_manifest=install.digest(
            install.installed(root, feed.keys).manifest_bytes))))
    # Emulate step one already completed: the candidate is kept aside, the old
    # backup remains named by this transaction and the live binding is new.
    failed = stage / 'failed-updater'
    (root / 'Updater').rename(failed)
    (root / install.UPDATER_RELEASE).write_bytes(new_binding)
    data_file = root / 'Data' / 'learning' / 'keep.bin'
    data_file.parent.mkdir(parents=True)
    data_file.write_bytes(b'user data stays byte-for-byte')

    handle, close_handle = _hold_child_directory(backup / '_internal')
    try:
        with pytest.raises(install.RecoverableError, match='恢复没完成，下次启动再试'):
            install.recover(root, feed.keys)
        assert backup.is_dir() and failed.is_dir()
        assert (backup / '_internal/dep.dll').read_bytes() == b'runtime 2.0.3'
        assert (failed / '_internal/dep.dll').read_bytes() == b'runtime 2.0.3'
        assert (root / install.UPDATER_RELEASE).read_bytes() == new_binding
        assert data_file.read_bytes() == b'user data stays byte-for-byte'
    finally:
        close_handle(handle)

    assert install.recover(root, feed.keys)
    assert not backup.exists()
    assert (root / 'Updater/_internal/dep.dll').read_bytes() == b'runtime 2.0.3'
    assert (root / install.UPDATER_RELEASE).read_bytes() == old_binding
    assert data_file.read_bytes() == b'user data stays byte-for-byte'

    # If the backup has already moved but the active runtime is not the signed
    # old generation, restart recovery must refuse to bind it as the old one.
    shutil.rmtree(root / 'Updater')
    shutil.copytree(new_source / 'UserRuntime', root / 'Updater')
    attempt = 'b' * 32
    stage = root / ('staging-' + attempt)
    stage.mkdir()
    (stage / 'previous-updater-release.json').write_bytes(old_binding)
    (stage / 'previous-updater.json').write_bytes(canonical({'had_binding': True}))
    (root / install.UPDATER_RELEASE).write_bytes(new_binding)
    (root / install.JOURNAL).write_bytes(canonical(dict(
        schema='mentorflow.per-user-transaction.v1', attempt=attempt, phase='NEW_ACTIVE',
        target='2.0.5', base='2.0.3', base_manifest=install.digest(
            install.installed(root, feed.keys).manifest_bytes))))
    candidate_bytes = (root / 'Updater/_internal/dep.dll').read_bytes()
    with pytest.raises(install.SecurityError):
        install.recover(root, feed.keys)
    assert (root / 'Updater/_internal/dep.dll').read_bytes() == candidate_bytes
    assert (root / install.UPDATER_RELEASE).read_bytes() == new_binding
    assert (stage / 'previous-updater.json').is_file()
    feed.close()


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows directory sharing semantics')
@pytest.mark.parametrize('release_after', [1, 3, None], ids=['first-retry', 'third-retry', 'persistent'])
def test_app_activation_real_child_handle_release_or_recovery(tmp_path, monkeypatch, release_after):
    feed, root, old_envelope, source, new_envelope, ready = _signed_case(tmp_path, monkeypatch)
    old_app = _tree_bytes(root / 'App')
    data = root / 'Data' / 'learning' / 'stable.bin'
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_bytes(b'plan bytes stay intact')
    source_for = lambda source, _component: source / '_internal'
    state, close_handle = _release_on_nth_sleep(
        monkeypatch, source_for, 'APP_ACTIVATE', release_after)
    try:
        if release_after is None:
            with pytest.raises(PermissionError) as caught:
                install.transact(root, source, new_envelope, health=ready)
            assert caught.value.winerror == 5
            assert state['sleeps'] == 7
            # Activation exhausted retries; the prior signed App was restored,
            # its release binding and transaction marker reflect rollback.
            assert install.installed(root, feed.keys).envelope == old_envelope
            assert (root / install.CURRENT).read_bytes() == old_envelope
            assert _tree_bytes(root / 'App') == old_app
            assert (root / 'Data/learning/stable.bin').read_bytes() == b'plan bytes stay intact'
            assert (root / 'per-user-transaction.json').is_file()
            assert (root / 'App/MentorFlow.exe').is_file()
            assert install.recover(root, feed.keys) is False
        else:
            result = install.transact(root, source, new_envelope, health=ready)
            assert result['status'] == 'HEALTHY'
            assert state['sleeps'] == release_after
            assert install.installed(root, feed.keys).version == '2.0.5'
            assert _tree_bytes(root / 'previous/App') == old_app
            assert (root / 'Data/learning/stable.bin').read_bytes() == b'plan bytes stay intact'
    finally:
        close_handle()
        feed.close()


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows directory sharing semantics')
@pytest.mark.parametrize('blocked_step', ['active-to-discarded', 'backup-to-active'])
@pytest.mark.parametrize('release_after', [1, 3, None], ids=['first-retry', 'third-retry', 'persistent'])
def test_updater_recovery_each_real_rename_step_retry_or_resume(
        tmp_path, monkeypatch, blocked_step, release_after):
    feed, root, old_envelope, _source, _new_envelope, _ready = _signed_case(tmp_path, monkeypatch)
    old_runtime = _tree_bytes(root / 'Updater')
    work = root / install.UPDATER_SWAP
    work.mkdir()
    shutil.copytree(root / 'Updater', work / 'old')
    (work / 'old-release.json').write_bytes(old_envelope)
    (work / 'new-release.json').write_bytes(old_envelope)
    (work / 'state.json').write_bytes(canonical(
        {'phase': 'PREPARED', 'had_updater': True, 'had_binding': True}))
    if blocked_step == 'backup-to-active':
        (root / 'Updater').rename(work / 'discarded')
    lock_component = ('UPDATER_RECOVER_DISCARD' if blocked_step == 'active-to-discarded'
                      else 'UPDATER_RECOVER_RESTORE')
    def source_for(source, component):
        return source / '_internal'
    state, close_handle = _release_on_nth_sleep(
        monkeypatch, source_for, lock_component, release_after)
    try:
        if release_after is None:
            with pytest.raises(install.RecoverableError, match='恢复没完成，下次启动再试'):
                install._recover_updater_swap(root, feed.keys)
            assert state['sleeps'] == 7
            assert work.is_dir() and (work / 'old').is_dir()
            assert (work / 'state.json').is_file()
            assert _tree_bytes(work / 'old') == old_runtime
            if blocked_step == 'active-to-discarded':
                assert (root / 'Updater').is_dir()
                assert _tree_bytes(root / 'Updater') == old_runtime
                assert not (work / 'discarded').exists()
            else:
                assert not (root / 'Updater').exists()
                assert (work / 'discarded').is_dir()
                assert _tree_bytes(work / 'discarded') == old_runtime
            close_handle()
            install._recover_updater_swap(root, feed.keys)
        else:
            install._recover_updater_swap(root, feed.keys)
            assert state['sleeps'] == release_after
        assert not work.exists()
        assert _tree_bytes(root / 'Updater') == old_runtime
        assert (root / 'Updater/UserRuntime.exe').read_bytes() == b'MZ 2.0.3'
        assert (root / install.UPDATER_RELEASE).read_bytes() == old_envelope
    finally:
        close_handle()
        feed.close()


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows directory sharing semantics')
def test_app_recovery_after_previous_save_rename_stays_pending_and_resumes(tmp_path, monkeypatch):
    feed, root, old_envelope, source, new_envelope, ready = _signed_case(tmp_path, monkeypatch)
    old_app = _tree_bytes(root / 'App')
    data_file = root / 'Data' / 'learning' / 'held.bin'
    data_file.parent.mkdir(parents=True, exist_ok=True)
    data_file.write_bytes(b'unchanged data')
    state, close_handle = _release_on_nth_sleep(
        monkeypatch, lambda source_path, _component: source_path / '_internal',
        'APP_PREVIOUS_SAVE', None)
    try:
        with pytest.raises(install.RecoverableError, match='恢复没完成，下次启动再试'):
            install.transact(root, source, new_envelope, health=ready)
        assert state['sleeps'] == 7
        assert _tree_bytes(root / 'App') == old_app
        assert (root / 'previous/release.json').is_file()
        assert not (root / 'previous/App').exists()
        assert (root / install.CURRENT).read_bytes() == old_envelope
        assert data_file.read_bytes() == b'unchanged data'
    finally:
        close_handle()
    # A new process can retry after the old App's child handle disappears.
    assert install.recover(root, feed.keys)
    assert _tree_bytes(root / 'App') == old_app
    assert (root / install.CURRENT).read_bytes() == old_envelope
    assert data_file.read_bytes() == b'unchanged data'
    feed.close()


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows directory sharing semantics')
def test_app_recovery_finishes_if_previous_move_succeeded_before_current_write(tmp_path, monkeypatch):
    feed, root, old_envelope, _source, new_envelope, _ready = _signed_case(tmp_path, monkeypatch)
    old_app = _tree_bytes(root / 'App')
    previous = root / 'previous'
    previous.mkdir()
    (previous / 'release.json').write_bytes(old_envelope)
    base_manifest = install.digest(install.historical(old_envelope, feed.keys).manifest_bytes)
    # Model restart after previous/App -> App succeeded, but writing CURRENT
    # did not. The signed previous App is now at the live fixed path.
    (root / install.CURRENT).write_bytes(new_envelope)
    attempt = 'c' * 32
    (root / install.JOURNAL).write_bytes(canonical(dict(
        schema='mentorflow.per-user-transaction.v1', attempt=attempt,
        phase='OLD_MOVED', target='2.0.5', base='2.0.3',
        base_manifest=base_manifest)))
    assert install.recover(root, feed.keys)
    assert _tree_bytes(root / 'App') == old_app
    assert (root / install.CURRENT).read_bytes() == old_envelope
    assert canonical(__import__('json').loads((root / install.JOURNAL).read_bytes())) == canonical(
        {'schema': 'mentorflow.per-user-transaction.v1', 'attempt': attempt,
         'phase': 'ROLLED_BACK', 'target': '2.0.5', 'base': '2.0.3',
         'base_manifest': base_manifest})
    feed.close()


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows directory sharing semantics')
def test_inprocess_runtime_restore_without_backup_does_not_rebind_unverified_active(tmp_path, monkeypatch):
    feed, root, old_envelope, new_source, new_envelope, _ready = _signed_case(tmp_path, monkeypatch)
    attempt = 'd' * 32
    stage = root / ('staging-' + attempt)
    stage.mkdir()
    (stage / 'previous-updater-release.json').write_bytes(old_envelope)
    (stage / 'previous-updater.json').write_bytes(canonical({'had_binding': True}))
    shutil.rmtree(root / 'Updater')
    shutil.copytree(new_source / 'UserRuntime', root / 'Updater')
    (root / install.UPDATER_RELEASE).write_bytes(new_envelope)
    with pytest.raises(install.SecurityError):
        install._restore_transaction_runtime(root, attempt, feed.keys)
    assert (root / install.UPDATER_RELEASE).read_bytes() == new_envelope
    assert (stage / 'previous-updater.json').is_file()
    assert (root / 'Updater/UserRuntime.exe').read_bytes() == b'MZ 2.0.5'
    feed.close()


def test_transaction_runtime_restore_rejects_modified_backup_before_discarding_active(tmp_path, monkeypatch):
    feed, root, old_envelope, _source, _new_envelope, _ready = _signed_case(tmp_path, monkeypatch)
    attempt = 'e' * 32
    stage = root / ('staging-' + attempt)
    backup = stage / 'previous-updater'
    shutil.copytree(root / 'Updater', backup)
    (stage / 'previous-updater-release.json').write_bytes(old_envelope)
    (stage / 'previous-updater.json').write_bytes(canonical({'had_binding': True}))
    (backup / '_internal/dep.dll').write_bytes(b'changed after backup was created')
    active_before, backup_before = _tree_bytes(root / 'Updater'), _tree_bytes(backup)
    with pytest.raises(install.SecurityError):
        install._restore_transaction_runtime(root, attempt, feed.keys)
    assert _tree_bytes(root / 'Updater') == active_before
    assert _tree_bytes(backup) == backup_before
    assert (root / install.UPDATER_RELEASE).read_bytes() == old_envelope
    assert (stage / 'previous-updater.json').is_file()
    feed.close()


@pytest.mark.parametrize('failing_callback', ['health-marker', 'progress'])
def test_committed211_callback_failure_never_mixes_app_and_runtime_generations(
        tmp_path, monkeypatch, failing_callback):
    feed, root, _old_envelope, source, new_envelope, ready = _signed_case(tmp_path, monkeypatch)

    def fault(phase):
        if failing_callback == 'health-marker' and phase == 'HEALTHY':
            raise RuntimeError('post-commit observer failed')

    def progress(value):
        if failing_callback == 'progress' and value == 100:
            raise RuntimeError('post-commit progress failed')

    result = install.transact(root, source, new_envelope, health=ready,
                              refresh_runtime=True, fault=fault, progress=progress)
    assert result['status'] == 'HEALTHY'
    assert _tree_bytes(root / 'App') == _tree_bytes(source)
    assert _tree_bytes(root / 'Updater') == _tree_bytes(source / 'UserRuntime')
    assert (root / install.CURRENT).read_bytes() == new_envelope
    assert (root / install.UPDATER_RELEASE).read_bytes() == new_envelope
    assert install.strict_json((root / install.JOURNAL).read_bytes())['phase'] == 'HEALTHY'
    assert install.recover(root, feed.keys) is False
    feed.close()
