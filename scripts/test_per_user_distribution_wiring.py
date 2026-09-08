"""Offline adapter checks; never launch App, Setup, UAC or a visible window."""
import os
from types import SimpleNamespace
import pytest

from scripts.test_per_user_install import case, ready
from scripts.test_per_user_core_contract import bootstrap
from scripts import per_user_install as install, per_user_update_core as core
from scripts import per_user_launcher as launcher, per_user_runtime_session as sessions
from desktop.update_protocol import canonical, RecoverableError


def test_stable_launch_resumes_before_app_without_duplicate(case, monkeypatch):
    _, root, _, publish = bootstrap(case, monkeypatch)
    source, raw = publish('1.3.1', 2)
    core.remember_download(root, source, raw)
    calls = []
    monkeypatch.setattr(install, 'require_app_stopped', lambda _: calls.append('stopped'))
    monkeypatch.setattr(install, 'recover', lambda _: calls.append('recover'))
    def resume(path, *, reason):
        assert path == root and reason == 'next_launch'
        calls.append('resume')
        return dict(state='CURRENT_HEALTHY', health=dict(ready=True))
    monkeypatch.setattr(core, 'resume_pending_on_normal_launch', resume)
    monkeypatch.setattr(install, 'launch_health', lambda *_: pytest.fail('TARGET already launched'))
    assert launcher.launch(root)['state'] == 'CURRENT_HEALTHY'
    assert calls == ['stopped', 'recover', 'resume']


def test_offline_pending_preserved_current_launches_once(case, monkeypatch):
    _, root, _, publish = bootstrap(case, monkeypatch)
    source, raw = publish('1.3.1', 2)
    core.remember_download(root, source, raw)
    before = (root / core.PENDING).read_bytes()
    def offline(*_, **__): raise RecoverableError('offline')
    monkeypatch.setattr(core, 'resume_pending_on_normal_launch', offline)
    launches = []
    def health(path, release):
        launches.append(release.version)
        return ready(path, release)
    monkeypatch.setattr(install, 'launch_health', health)
    assert launcher.launch(root)['health']['version'] == '1.3.0'
    assert launches == ['1.3.0'] and (root / core.PENDING).read_bytes() == before


def test_bad_target_recovery_does_not_launch_second_app(case, monkeypatch):
    _, root, _, publish = bootstrap(case, monkeypatch)
    source, raw = publish('1.3.1', 2)
    core.remember_download(root, source, raw)
    def rollback(*_, **__):
        doc = core.read_pending(root)
        install.atomic(root / core.PENDING, canonical({**doc, 'state': 'PREVIOUS_HEALTHY'}))
        raise RecoverableError('target failed, previous restored')
    monkeypatch.setattr(core, 'resume_pending_on_normal_launch', rollback)
    monkeypatch.setattr(launcher, '_recovery_health', lambda _: dict(ready=True, version='1.3.0'))
    monkeypatch.setattr(install, 'launch_health', lambda *_: pytest.fail('previous already running'))
    assert launcher.launch(root)['status'] == 'PREVIOUS_HEALTHY'


@pytest.mark.parametrize('state', ['CURRENT_HEALTHY', 'PREVIOUS_HEALTHY'])
def test_terminal_pending_not_reapplied(case, monkeypatch, state):
    _, root, _, publish = bootstrap(case, monkeypatch)
    source, raw = publish('1.3.1', 2)
    core.remember_download(root, source, raw)
    install.atomic(root / core.PENDING, canonical({**core.read_pending(root), 'state': state}))
    monkeypatch.setattr(core, 'resume_pending_on_normal_launch', lambda *a, **k: pytest.fail('terminal pending retried'))
    monkeypatch.setattr(install, 'launch_health', ready)
    assert launcher.launch(root)['health']['version'] == '1.3.0'


def test_session_ending_denies_stable_launch_before_recovery(case, monkeypatch):
    _, root, _, _ = bootstrap(case, monkeypatch)
    monkeypatch.setattr(core, 'unsafe_teardown', lambda *a: True)
    monkeypatch.setattr(install, 'recover', lambda *_: pytest.fail('recovery during logoff'))
    monkeypatch.setattr(install, 'launch_health', lambda *_: pytest.fail('launch during logoff'))
    with pytest.raises(RecoverableError, match='session ending'): launcher.launch(root)


@pytest.mark.skipif(os.name != 'nt', reason='Windows named event and hidden broadcast receiver')
def test_runtime_retains_gui_session_event_after_gui_handle_closes():
    from desktop.update_session import UpdateSession
    owner = UpdateSession()
    try:
        with sessions.runtime_session(os.getpid()) as held:
            assert not sessions.session_ending()
            owner.defer()
            owner.close()
            assert sessions.session_ending() and core.unsafe_teardown()
            assert held._monitor_window  # hidden top-level receiver survives GUI exit
    finally: owner.close()
    assert not sessions.session_ending()


def test_session_event_also_gates_final_and_rollback_health_launch(monkeypatch, tmp_path):
    from desktop import windows_update_pipe
    class Listener:
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(windows_update_pipe, 'Listener', Listener)
    monkeypatch.setattr(core, 'unsafe_teardown', lambda *args: True)
    monkeypatch.setattr(install.subprocess, 'Popen', lambda *a, **k: pytest.fail('App spawned during session exit'))
    with pytest.raises(RecoverableError, match='session ending'):
        install.launch_health(tmp_path, SimpleNamespace(version='1.3.0'))
