"""Real child-handle activation budget and next-start recovery contract."""
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
from refactor.desktop.delivery import per_user_launcher as launcher
from refactor.desktop.delivery import per_user_update_core as core
from refactor.desktop.delivery.update_protocol import canonical
from tests.refactor.delivery.test_directory_rename211 import _signed_case, _tree_bytes


def _open_child(path):
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                       ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                       ctypes.c_void_p)
    create.restype = ctypes.c_void_p
    close = kernel32.CloseHandle
    close.argtypes = (ctypes.c_void_p,)
    close.restype = ctypes.c_int
    handle = create(str(path), 1, 7, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle, close


class _Service:
    def __init__(self, feed, root, envelope, cache_root):
        self.release = install.historical(envelope, feed.keys)
        self.cache = SimpleNamespace(root=cache_root, runtime_root=cache_root)
        self.cache.root.mkdir(parents=True, exist_ok=True)
        self.staged = None

    def check(self, *, manual=False):
        assert manual is True
        return {'state': 'UPDATE_AVAILABLE'}


def _queue_update(feed, root, source, envelope, tmp_path):
    cache_root = tmp_path / 'prepared-cache'
    staged = cache_root / 'release' / 'App'
    staged.parent.mkdir(parents=True)
    shutil.copytree(source, staged)
    (staged.parent / 'release.json').write_bytes(envelope)
    core.remember_download(root, staged, envelope)
    return _Service(feed, root, envelope, cache_root)


def _healthy_launch(_root, release):
    return {'ready': True, 'version': release.version}


def _lock_each_activation(monkeypatch, release_after=None):
    real_rename = ops.rename_with_retry
    opened = []
    handle_lock = threading.Lock()

    def intercepted(source, target, *, component, replace=False, **kwargs):
        if component == 'APP_ACTIVATE':
            handle, close = _open_child(Path(source) / '_internal')
            item = {'handle': handle, 'close': close, 'closed': False,
                    'opened_at': time.monotonic()}
            opened.append(item)
            if release_after is not None:
                def release_later():
                    time.sleep(release_after)
                    with handle_lock:
                        if not item['closed']:
                            close(handle)
                            item['closed'] = True
                            item['released_at'] = time.monotonic()
                worker = threading.Thread(target=release_later, daemon=True)
                item['thread'] = worker
                worker.start()
        return real_rename(source, target, component=component,
                           replace=replace, **kwargs)

    monkeypatch.setattr(install, 'rename_with_retry', intercepted)
    # Keep the test focused on bounded activation behavior; the separate
    # Restart Manager tests cover the native process query.
    monkeypatch.setattr(ops, '_query_rename_users',
                        lambda *_args, **_kwargs: {'status': 'unknown', 'processes': []})

    def close_all():
        for item in opened:
            with handle_lock:
                if not item['closed']:
                    item['close'](item['handle'])
                    item['closed'] = True
    return opened, close_all


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows directory sharing semantics')
def test_activation_handle_released_after_one_second_succeeds_within_budget(tmp_path, monkeypatch):
    feed, root, _old, source, envelope, _ready = _signed_case(tmp_path, monkeypatch)
    monkeypatch.setattr(install, 'launch_health', _healthy_launch)
    service = _queue_update(feed, root, source, envelope, tmp_path)
    handles, close_all = _lock_each_activation(monkeypatch, release_after=1.0)
    try:
        started = time.monotonic()
        result = core.apply_update(root, reason='next_launch', service=service)
        elapsed = time.monotonic() - started
        assert result['state'] == 'CURRENT_HEALTHY'
        assert elapsed >= .8 and elapsed < 4.0
        assert handles[0]['closed']
        assert install.installed(root, feed.keys).version == '2.0.5'
        assert not (root / core.PENDING).exists()
    finally:
        close_all()
        feed.close()


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows directory sharing semantics')
def test_busy_activation_retains_signed_stage_and_retries_next_launch_after_two_deferrals(
        tmp_path, monkeypatch):
    feed, root, old_envelope, source, envelope, _ready = _signed_case(tmp_path, monkeypatch)
    monkeypatch.setattr(install, 'launch_health', _healthy_launch)
    old_app = _tree_bytes(root / 'App')
    service = _queue_update(feed, root, source, envelope, tmp_path)
    first_handles, close_first = _lock_each_activation(monkeypatch, release_after=10.0)
    try:
        started = time.monotonic()
        with pytest.raises(install.DeferredAppActivation):
            core.apply_update(root, reason='next_launch', service=service)
        assert 2.5 <= time.monotonic() - started < 5.0
        pending_a = core.read_pending(root)
        stage_a = Path(pending_a['stage'])
        assert pending_a['state'] == 'PREVIOUS_HEALTHY'
        assert pending_a['deferred_reason'] == 'app_directory_busy'
        assert stage_a.is_dir()
        assert install.installed(root, feed.keys).envelope == old_envelope
        assert _tree_bytes(root / 'App') == old_app
        prepared = core.prepare_update(root, service=service, reuse_prepared=True)
        assert prepared['state'] == 'UPDATE_DOWNLOADED'
        assert service.staged == stage_a
        assert stage_a.is_dir()  # background prepare may reuse, never consume it
        # Background cleanup must keep the exact package source referenced by
        # the deferred pending state.
        install.cleanup_program_files(root)
        assert stage_a.is_dir()
        first_handles[0]['thread'].join(timeout=8)
        assert first_handles[0]['closed']
        assert first_handles[0]['released_at'] - first_handles[0]['opened_at'] >= 9.8
    finally:
        close_first()

    # Repeated activation denial safely moves the pending source binding to the
    # new transaction generation; the lock remains held through this attempt.
    second_handles, close_second = _lock_each_activation(monkeypatch)
    try:
        with pytest.raises(install.DeferredAppActivation):
            core.resume_pending_on_normal_launch(root, service=service,
                                                reason='next_launch')
        pending_b = core.read_pending(root)
        stage_b = Path(pending_b['stage'])
        journal_b = __import__('json').loads((root / install.JOURNAL).read_text('utf-8'))
        assert stage_b.is_dir() and stage_b != stage_a
        assert journal_b['attempt'] in stage_b.parent.name
        assert journal_b['deferred_source_attempt'] in stage_a.parent.name
        assert pending_b['deferred_reason'] == 'app_directory_busy'
    finally:
        close_second()

    # Simulate process death after the next transaction wrote its rollback
    # journal but before it rewrote pending.stage: B remains the source while
    # journal C binds it as deferred_source_attempt.
    journal_c_attempt = 'c' * 32
    install.atomic(root / install.JOURNAL, canonical(dict(
        schema='mentorflow.per-user-transaction.v1', attempt=journal_c_attempt,
        phase='ROLLED_BACK', target='2.0.5', base='2.0.3',
        base_manifest=install.digest(install.installed(root, feed.keys).manifest_bytes),
        deferred_source_attempt=stage_b.parent.name.removeprefix('staging-'))))
    assert core.read_pending(root)['stage'] == str(stage_b)

    # The old holder has now released. The next normal launch re-hashes the
    # signed retained tree, copies it into a fresh transaction, and succeeds.
    third_handles, close_third = _lock_each_activation(monkeypatch, release_after=.8)
    try:
        result = core.resume_pending_on_normal_launch(root, service=service,
                                                     reason='next_launch')
        assert result['state'] == 'CURRENT_HEALTHY'
        assert third_handles[0]['closed']
        assert install.installed(root, feed.keys).version == '2.0.5'
        assert not (root / core.PENDING).exists()
        assert not stage_b.parent.exists()
    finally:
        close_third()
        feed.close()


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows directory sharing semantics')
def test_normal_health_failure_does_not_become_busy_auto_retry(tmp_path, monkeypatch):
    feed, root, _old, source, envelope, _ready = _signed_case(tmp_path, monkeypatch)
    service = _queue_update(feed, root, source, envelope, tmp_path)
    monkeypatch.setattr(install, 'launch_health',
                        lambda _root, release: {'ready': release.version == '2.0.3'})
    resume = core.resume_pending_on_normal_launch
    monkeypatch.setattr(core, 'resume_pending_on_normal_launch',
                        lambda path, **options: resume(path, service=service, **options))
    monkeypatch.setattr(launcher, '_recovery_health',
                        lambda _root: {'ready': True, 'version': '2.0.3'})
    try:
        result = launcher._launch_serialized(root, lambda _phase: None)
        assert result['status'] == 'PREVIOUS_HEALTHY'
        assert result['deferred_update'] is False
        pending = core.read_pending(root)
        assert pending['state'] == 'PREVIOUS_HEALTHY'
        assert pending.get('deferred_reason') != 'app_directory_busy'
        assert core.get_startup_update_state(root, check_feed=False)['deferred_update'] is False
    finally:
        feed.close()


@pytest.mark.skipif(os.name != 'nt', reason='uses the Windows per-user launch path')
def test_stale_rollback_proof_falls_through_to_healthy_old_app_launch(tmp_path, monkeypatch):
    feed, root, _old, source, envelope, _ready = _signed_case(tmp_path, monkeypatch)
    service = _queue_update(feed, root, source, envelope, tmp_path)
    pending = core.read_pending(root)
    install.atomic(root / core.PENDING, core.canonical({
        **pending, 'state': 'PREVIOUS_HEALTHY', 'deferred_reason': 'app_directory_busy'}))
    monkeypatch.setattr(core, 'resume_pending_on_normal_launch',
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            install.RecoverableError('offline')))
    monkeypatch.setattr(launcher, '_recovery_health',
                        lambda _root: (_ for _ in ()).throw(install.SecurityError('stale process proof')))
    launched = []
    def launch_old(_root, release):
        launched.append(release.version)
        return {'ready': True, 'version': release.version}
    monkeypatch.setattr(install, 'launch_health', launch_old)
    try:
        result = launcher._launch_serialized(root, lambda _phase: None)
        assert result['status'] == 'HEALTHY'
        assert launched == ['2.0.3']
        assert core.read_pending(root)['deferred_reason'] == 'app_directory_busy'
    finally:
        feed.close()
