"""BUG-74 ready staging, immutable reuse, cancellation and rollback."""
import os
import json
import time
from pathlib import Path
import pytest

from refactor.desktop.delivery import per_user_install as install
from refactor.desktop.delivery import per_user_update_core as core
from refactor.desktop.delivery import prepared_update as prepared
from refactor.desktop.delivery.update_protocol import DownloadCancelled
from tests.refactor.delivery.test_delivery_per_user_install import case
from tests.refactor.delivery.test_delivery_one_click_update_core import setup


def test_ready_click_does_not_rebuild_hash_or_copy105(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    assert core.prepare_update(root, service=service)['prepared']
    stage = service.staged
    monkeypatch.setattr(service, 'download', lambda **kw: pytest.fail('ready click rebuilt staging'))
    monkeypatch.setattr(install, '_copy', lambda *a: pytest.fail('same-volume update copied files'))
    original = install.verify_tree
    def verify(app, release, **kw):
        if Path(app) == stage or 'staging-' in str(app):
            pytest.fail('ready update rehashed staging')
        return original(app, release, **kw)
    monkeypatch.setattr(install, 'verify_tree', verify)
    assert core.prepare_update(root, service=service)['prepared']
    assert core.resume_pending_on_normal_launch(root, service=service)['state'] == 'CURRENT_HEALTHY'
    assert not stage.exists()
    assert not (root/core.PENDING).exists()


def test_changed_stage_reprepares105(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    core.prepare_update(root, service=service)
    path = service.staged/'MentorFlow.exe'
    old = path.stat()
    content = path.read_bytes()
    path.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert not prepared.ready(service.staged, service.release)
    calls = []
    download = service.download
    monkeypatch.setattr(service, 'download', lambda **kw: (calls.append(True) or download(**kw)))
    assert core.prepare_update(root, service=service)['prepared']
    assert calls and prepared.ready(service.staged, service.release)


def test_cancel_background_then_retry105(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    original = service.cache.reconstruct
    def interrupt(*args, **kwargs):
        # Cancel after an actual completed file has reached the owned partial
        # App, as when the UI closes halfway through reconstruction.
        kwargs['cancel'] = lambda: any(service.cache.runtime_root.glob('*/App/MentorFlow.exe'))
        return original(*args, **kwargs)
    monkeypatch.setattr(service.cache, 'reconstruct', interrupt)
    assert core.prepare_update(root, service=service)['state'] == 'DOWNLOAD_PAUSED_OR_INTERRUPTED'
    assert not (root/core.PENDING).exists()
    assert any(service.cache.runtime_root.glob('*/App/MentorFlow.exe'))
    monkeypatch.setattr(service.cache, 'reconstruct', original)
    assert core.prepare_update(root, service=service)['prepared']


def test_ready_pending_protected_from_cleanup105(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    # Production/App layout, unlike the legacy fixture's metadata cache root.
    service.cache.runtime_root = root.parent/'.MentorFlow-runtime/.mfu/123456789abc'
    core.prepare_update(root, service=service)
    stage = service.staged
    install.cleanup_program_files(root)
    assert stage.exists() and prepared.ready(stage, service.release)
    pending = core.read_pending(root)
    install.atomic(root/core.PENDING, install.canonical({**pending, 'target': install.installed(root).version}))
    install.cleanup_program_files(root)
    assert not (root/core.PENDING).exists() and not stage.exists()


def test_ready_rollback_and_close_timeout105(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    core.prepare_update(root, service=service)
    stage = service.staged
    monkeypatch.setattr(install, 'request_app_shutdown', lambda *a: (_ for _ in ()).throw(install.AppShutdownTimeout('busy')))
    result = core.apply_update(root, service=service, pid=1234, hwnd_hex='0001')
    assert result['state'] == 'PENDING_INSTALL'
    assert stage.exists() and prepared.ready(stage, service.release)
    def health(root, release):
        if release.version == '1.4.1': raise RuntimeError('injected bad health')
        return {'ready': True}
    monkeypatch.setattr(install, 'launch_health', health)
    with pytest.raises(RuntimeError, match='bad health'):
        core.resume_pending_on_normal_launch(root, service=service)
    assert install.installed(root).version == '1.4.0'
    assert json.loads((root/install.JOURNAL).read_bytes())['phase'] == 'ROLLED_BACK'


def test_ready_files_deny_writes_until_activation105(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    core.prepare_update(root, service=service)
    stage = service.staged
    with prepared.lease(stage, service.release) as valid:
        assert valid
        with pytest.raises(PermissionError):
            (stage/'MentorFlow.exe').write_bytes(b'changed after ready check')


def test_click_uses_ready_without_fetch_then_apply_checks_metadata_once105(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    core.prepare_update(root, service=service)
    calls = []
    check = service.check
    monkeypatch.setattr(service, 'check', lambda **kw: (calls.append(True) or check(**kw)))
    assert core.prepare_update(root, service=service, reuse_prepared=True)['prepared']
    assert calls == []
    assert core.resume_pending_on_normal_launch(root, service=service)['state']=='CURRENT_HEALTHY'
    assert len(calls)==1


def test_install_core_also_runs_as_frozen_entry_script105(case):
    import runpy
    _, root, feed, publish = case
    source, raw = publish('1.3.0', 1)
    entry = runpy.run_path(install.__file__)
    result = entry['transact'](root, source, raw, public_keys=feed.keys,
                               health=lambda *a: {'ready': True})
    assert result['status']=='HEALTHY'


def test_installer_can_cover_from_ready_source105(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    core.prepare_update(root, service=service)
    result = install.transact(root, service.staged, service.release.envelope,
                              refresh_runtime=True, health=lambda *a: {'ready': True})
    assert result['status']=='HEALTHY'
    assert json.loads((root/install.UPDATER_RELEASE).read_bytes())['signed']['version']=='1.4.1'
    assert core.discard_stale_pending(root)
