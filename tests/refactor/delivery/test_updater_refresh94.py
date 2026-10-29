"""Updater lifecycle on signed inert fixtures; no installed App or network feed."""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from refactor.desktop.delivery import per_user_install as install
from refactor.desktop.delivery import per_user_update_core as core
from refactor.desktop.delivery.update_protocol import canonical, strict_json, RecoverableError
from tests.refactor.delivery.test_delivery_mature_update import Feed


@pytest.fixture
def runtime94(tmp_path, monkeypatch):
    feed = Feed(tmp_path)
    root = tmp_path / 'MentorFlow'
    monkeypatch.setattr(install, 'keys', lambda: feed.keys)
    monkeypatch.setattr(install, 'require_updater_stopped', lambda root: None)
    monkeypatch.setattr(core, 'unsafe_teardown', lambda *args: False)

    def publish(version, epoch):
        files = {**feed.old_files, 'UserRuntime/UserRuntime.exe': ('MZ runtime ' + version).encode(),
                 'UserRuntime/_internal/dep.dll': ('dependency ' + version).encode()}
        feed.publish(version, files)
        feed.signed['metadata_version'] = epoch
        feed.sign(feed.signed)
        source = tmp_path / ('source-' + version)
        source.mkdir(exist_ok=True)
        for row in feed.signed['full_release']['assets']:
            path = source / row['path']; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(feed.objects['/objects/' + row['sha256']])
        return source, feed.raw

    def ready(root, release): return dict(ready=True, version=release.version)
    old_source, old = publish('2.0.3', 1)
    install.transact(root, old_source, old, health=ready)
    source, raw = publish('2.0.5', 2)
    f = SimpleNamespace(root=root, feed=feed, old=old, source=source, raw=raw, ready=ready)
    yield f
    feed.close()


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def installer_state_snapshot(root):
    """Snapshot install payload/journal while excluding expected attempt logs."""
    diagnostic_logs = {'Data/logs/mentorflow.log', 'Data/logs/launcher-startup.jsonl'}
    return {name: value for name, value in snapshot(root).items()
            if name not in diagnostic_logs}


def updated_app(f):
    return install.transact(f.root, f.source, f.raw, health=f.ready)


def test_updater94_install_upgrade_and_same_version_repair_replace_binding(runtime94):
    f = runtime94
    result = install.transact(f.root, f.source, f.raw, health=f.ready, refresh_runtime=True)
    assert result['updater']['status'] == 'UPDATED'
    assert (f.root / install.UPDATER_RELEASE).read_bytes() == f.raw
    assert (f.root / 'Updater/UserRuntime.exe').read_bytes() == b'MZ runtime 2.0.5'
    assert not (f.root / install.UPDATER_SWAP).exists()
    (f.root / 'Updater/UserRuntime.exe').write_bytes(b'corrupt installed runtime')
    result = install.transact(f.root, f.source, f.raw, health=f.ready,
                              repair=True, refresh_runtime=True)
    assert result['updater']['status'] == 'UPDATED'
    install.verify_recovery_runtime(f.root)


def test_updater94_installer_stops_before_mutating_if_updater_running(runtime94, monkeypatch):
    f = runtime94; before = installer_state_snapshot(f.root)
    def running(_): raise RecoverableError('请先关闭 MentorFlow 修复与更新窗口')
    monkeypatch.setattr(install, 'require_updater_stopped', running)
    with pytest.raises(RecoverableError, match='请先关闭'):
        install.transact(f.root, f.source, f.raw, health=f.ready, refresh_runtime=True)
    assert installer_state_snapshot(f.root) == before


def test_updater94_installer_interrupted_app_recovery_also_waits_for_updater(runtime94, monkeypatch):
    f = runtime94
    journal = strict_json((f.root / install.JOURNAL).read_bytes())
    (f.root / install.JOURNAL).write_bytes(canonical({**journal, 'phase': 'NEW_ACTIVE'}))
    envelope = f.source.parent / 'incoming-release.json'
    envelope.write_bytes(f.raw)
    before = installer_state_snapshot(f.root)
    def running(_): raise RecoverableError('请先关闭 MentorFlow 更新程序')
    monkeypatch.setattr(install, 'require_updater_stopped', running)
    monkeypatch.setattr(install, 'recover', lambda *args: pytest.fail('must not recover App before runtime exits'))
    args = SimpleNamespace(action='install', root=f.root, parent=None, source=f.source,
                           release=envelope, close_after_health=False)
    with pytest.raises(RecoverableError, match='请先关闭'):
        install._execute(args, None, lambda _: None)
    assert installer_state_snapshot(f.root) == before


def test_updater94_in_app_update_waits_for_health_and_process_exit_then_refreshes(runtime94, monkeypatch):
    f = runtime94
    updated_app(f)
    assert (f.root / install.UPDATER_RELEASE).read_bytes() == f.old
    def running(_): raise RecoverableError('updater still running')
    monkeypatch.setattr(install, 'require_updater_stopped', running)
    assert install.refresh_updater(f.root)['status'] == 'DEFERRED'
    assert (f.root / install.UPDATER_RELEASE).read_bytes() == f.old
    monkeypatch.setattr(install, 'require_updater_stopped', lambda _: None)
    assert install.refresh_updater(f.root)['status'] == 'UPDATED'
    assert (f.root / install.UPDATER_RELEASE).read_bytes() == f.raw
    assert install.refresh_updater(f.root)['status'] == 'UP_TO_DATE'
    assert strict_json((f.root / 'updater-refresh.json').read_bytes())['status'] == 'UP_TO_DATE'


@pytest.mark.parametrize('phase', ['VERIFIED', 'OLD_MOVED', 'NEW_ACTIVE', 'BOUND'])
def test_updater94_swap_failure_keeps_old_runtime_and_sidecar(runtime94, phase):
    f = runtime94; updated_app(f)
    before = snapshot(f.root / 'Updater')
    def fail(value):
        if value == phase: raise OSError('injected swap failure')
    with pytest.raises(OSError, match='injected'):
        install.replace_updater(f.root, install.installed(f.root), fault=fail)
    assert snapshot(f.root / 'Updater') == before
    assert (f.root / install.UPDATER_RELEASE).read_bytes() == f.old
    assert not (f.root / install.UPDATER_SWAP).exists()
    install.verify_recovery_runtime(f.root)
    assert install.refresh_updater(f.root)['status'] == 'UPDATED'


def test_updater94_corrupt_original_runtime_restored_on_failed_repair(runtime94):
    f = runtime94; updated_app(f)
    (f.root / 'Updater/UserRuntime.exe').write_bytes(b'original broken runtime')
    before = snapshot(f.root / 'Updater')
    def fail(value):
        if value == 'NEW_ACTIVE': raise OSError('repair failed')
    with pytest.raises(OSError):
        install.replace_updater(f.root, install.installed(f.root), fault=fail)
    assert snapshot(f.root / 'Updater') == before
    assert (f.root / install.UPDATER_RELEASE).read_bytes() == f.old


def test_updater94_invalid_candidate_and_unhealthy_app_never_replace_old(runtime94):
    f = runtime94
    def unhealthy(*_): raise RuntimeError('health failed')
    with pytest.raises(RuntimeError, match='health failed'):
        install.transact(f.root, f.source, f.raw, health=unhealthy, refresh_runtime=True)
    assert (f.root / install.UPDATER_RELEASE).read_bytes() == f.old
    assert install.installed(f.root).version == '2.0.3'
    updated_app(f)
    (f.root / 'App/UserRuntime/UserRuntime.exe').write_bytes(b'bad new runtime')
    assert install.refresh_updater(f.root)['status'] == 'FAILED'
    assert (f.root / install.UPDATER_RELEASE).read_bytes() == f.old


@pytest.mark.parametrize('committed', [False, True])
def test_updater94_interrupted_swap_recovers_fixed_paths(runtime94, committed):
    f = runtime94; updated_app(f)
    work = f.root / install.UPDATER_SWAP; work.mkdir()
    (work / 'old-release.json').write_bytes(f.old)
    (work / 'new-release.json').write_bytes(f.raw)
    (f.root / 'Updater').rename(work / 'old')
    shutil.copytree(f.root / 'App/UserRuntime', f.root / 'Updater')
    state = dict(phase='COMMITTED' if committed else 'PREPARED', had_updater=True, had_binding=True)
    (work / 'state.json').write_bytes(canonical(state))
    if committed: (f.root / install.UPDATER_RELEASE).write_bytes(f.raw)
    install.verify_recovery_runtime(f.root)
    assert not work.exists()
    assert (f.root / install.UPDATER_RELEASE).read_bytes() == (f.raw if committed else f.old)
    assert install.refresh_updater(f.root)['status'] == ('UP_TO_DATE' if committed else 'UPDATED')


def test_updater94_first_runtime_failure_leaves_no_partial_pair(runtime94):
    f = runtime94; updated_app(f)
    shutil.rmtree(f.root / 'Updater'); (f.root / install.UPDATER_RELEASE).unlink()
    def fail(value):
        if value == 'BOUND': raise OSError('failed first runtime')
    with pytest.raises(OSError):
        install.replace_updater(f.root, install.installed(f.root), fault=fail)
    assert not (f.root / 'Updater').exists() and not (f.root / install.UPDATER_RELEASE).exists()
    assert not (f.root / install.UPDATER_SWAP).exists()


def test_updater94_health_pending_is_deferred(runtime94):
    f = runtime94; updated_app(f)
    journal = strict_json((f.root / install.JOURNAL).read_bytes())
    (f.root / install.JOURNAL).write_bytes(canonical({**journal, 'phase': 'NEW_ACTIVE'}))
    assert install.refresh_updater(f.root)['status'] == 'DEFERRED'
    assert (f.root / install.UPDATER_RELEASE).read_bytes() == f.old


def test_updater94_late_background_result_with_missing_root_is_a_logged_failure(tmp_path, caplog):
    missing = tmp_path / 'removed-parent' / 'MentorFlow'
    with caplog.at_level('INFO', logger=install.__name__):
        result = install.refresh_updater(missing)
    assert result['status'] == 'FAILED' and result['error'] == 'ValueError'
    assert 'Updater refresh' in caplog.text
    assert not missing.parent.exists()


def test_updater94_health_probe_does_not_race_startup_classifier(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(install, 'schedule_updater_refresh', lambda root: calls.append('refresh'))
    install.notify_health(tmp_path, None, None, None)
    assert calls == []
    from refactor.desktop.delivery import windows_update_pipe, windows_process_peer
    import threading
    class ImmediateThread:
        def __init__(self, target, **kwargs): self.target = target
        def start(self): self.target()
    monkeypatch.setattr(threading, 'Thread', ImmediateThread)
    monkeypatch.setattr(install, 'installed', lambda _: SimpleNamespace(version='2.0.5', manifest_bytes=b'manifest'))
    class Channel:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def peer_pid(self): return 123
        def send(self, signal): calls.append('send')
        def receive(self, **kwargs): calls.append('ack'); return {'health_received': self.ack}
    class Peer:
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def inspect(self): return dict(integrity_rid=8192, elevated=False, executable=str(tmp_path / 'UserRuntime.exe'))
    channel = Channel()
    monkeypatch.setattr(windows_update_pipe, 'connect', lambda *args, **kwargs: channel)
    monkeypatch.setattr(windows_process_peer, 'ProcessPeer', Peer)
    for ack in (False, True):
        calls.clear(); channel.ack = ack
        install.notify_health(tmp_path, SimpleNamespace(winId=lambda: 42), 'attempt', 'nonce')
        assert calls == ['send', 'ack']
