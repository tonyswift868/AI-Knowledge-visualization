"""BUG-71 regressions: legacy pending stages and App handoff semantics."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.desktop.delivery import per_user_install as install
from refactor.desktop.delivery import per_user_update_core as core
from refactor.desktop.delivery.update_protocol import canonical
from tests.refactor.delivery.test_delivery_per_user_install import case
from tests.refactor.delivery.test_delivery_one_click_update_core import setup, click_update


def _legacy_pending(root, source, raw):
    legacy = root.parent / '.MentorFlow-runtime' / '.mfu' / 'legacy-2.0.12'
    app = legacy / 'App'
    app.mkdir(parents=True)
    for item in source.rglob('*'):
        target = app / item.relative_to(source)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.read_bytes())
    (legacy / 'release.json').write_bytes(raw)
    pending = dict(schema=core.SCHEMA, state='PENDING_INSTALL', stage=str(app),
                   target='1.4.1', release_sha256=install.digest(raw))
    (root / core.PENDING).write_bytes(canonical(pending))
    return legacy


def test_legacy_pending_stage_is_reverified_in_fixed_legacy_cache(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    service.check(manual=True); service.download()
    source, raw = service.staged, service.release.envelope
    _legacy_pending(root, source, raw)
    click_update(root, service)  # Legacy downloads require a fresh explicit click.
    monkeypatch.setattr(install, 'launch_health', lambda r, release: dict(ready=True, version=release.version))
    result = core.resume_pending_on_normal_launch(root, service=service)
    assert result['state'] == 'CURRENT_HEALTHY'
    assert install.installed(root).version == '1.4.1'
    assert not (root / core.PENDING).exists()
    assert service.staged.is_relative_to(service.cache.runtime_root)


def test_self_exit_timeout_keeps_pending_install(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    service.check(manual=True); service.download()
    source, raw = service.staged, service.release.envelope
    _legacy_pending(root, source, raw)
    click_update(root, service)
    monkeypatch.setattr(install, 'wait_for_app_exit',
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            install.AppShutdownTimeout('app self-exit timed out')))
    result = core.apply_update(root, pid=9876, hwnd_hex='0001', reason='normal',
                               service=service, wait_for_exit=True)
    assert result['state'] == 'PENDING_INSTALL'
    assert result['message'] == '新版已下载好，下次打开软件时完成更新。'
    assert core.read_pending(root)['state'] == 'PENDING_INSTALL'


def test_confirmed_self_exit_timeout_terminates_stale_app(monkeypatch, tmp_path):
    from refactor.desktop.delivery import windows_process_peer
    events=[]
    class Peer:
        def __init__(self, pid): self.pid=pid; self.states=iter([True, True, False])
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def require(self, **kwargs): events.append(('require', self.pid))
        def visible_responsive_window(self, hwnd): events.append(('window', hwnd))
        def visible_windows(self): return []
        def alive(self): return next(self.states, False)
        def terminate(self): events.append(('terminate', self.pid))
    monkeypatch.setattr(windows_process_peer, 'ProcessPeer', Peer)
    monkeypatch.setattr(install, 'app_exit_confirmed', lambda root, pid: True)
    monkeypatch.setattr(install, 'maintenance_log', lambda root, phase, **fields: events.append((phase, fields)))
    install.wait_for_app_exit(tmp_path / 'MentorFlow', 321, '0000000000000001', timeout=0,
                              ready=lambda: events.append('ack'))
    assert events == [('require', 321), 'ack',
                      ('terminate', 321), ('stale_app_terminated', {'pid': 321,
                       'reason': 'CONFIRMED_EXIT_TIMEOUT', 'timeout_seconds': 0})]


def test_confirmed_self_exit_timeout_does_not_terminate_visible_app(monkeypatch, tmp_path):
    from refactor.desktop.delivery import windows_process_peer
    events = []
    class Peer:
        def __init__(self, pid): self.pid = pid
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def require(self, **kwargs): events.append(('require', self.pid))
        def alive(self): return True
        def visible_windows(self): return ['0x1234']
        def terminate(self): events.append(('terminate', self.pid))
    monkeypatch.setattr(windows_process_peer, 'ProcessPeer', Peer)
    monkeypatch.setattr(install, 'app_exit_confirmed', lambda root, pid: True)
    with pytest.raises(install.AppShutdownTimeout, match='visible window'):
        install.wait_for_app_exit(tmp_path / 'MentorFlow', 321, '0001', timeout=0)
    assert events == [('require', 321)]


def test_direct_exit_does_not_enter_logging_shutdown(monkeypatch):
    from refactor.desktop import account_shell_main
    events=[]
    monkeypatch.setattr(account_shell_main, 'diagnostic_event',
                        lambda name, **fields: events.append((name, fields)))
    monkeypatch.setattr(account_shell_main.os, '_exit', lambda code: events.append(('exit', code)))
    account_shell_main._exit_after_shutdown(0, reason='UPDATE_EXIT_DIRECT')
    assert events == [('exit', {'reason': 'UPDATE_EXIT_DIRECT'}), ('exit', 0)]


def test_in_app_handoff_passes_self_exit_flag(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    click_update(root, service)
    from refactor.desktop.delivery.windows_process_peer import ProcessPeer
    monkeypatch.setattr(ProcessPeer, 'require', lambda *a, **kw: {})
    monkeypatch.setattr(ProcessPeer, 'visible_responsive_window', lambda *a: None)
    commands = []
    monkeypatch.setattr(install, 'refresh_updater', lambda *_: {'status': 'UP_TO_DATE'})
    def start(args, **kwargs):
        commands.append(args)
        Path(args[args.index('--ready-file') + 1]).write_bytes(canonical(
            dict(app_pid=install.os.getpid(), coordinator_pid=54321)))
        return SimpleNamespace(pid=54321, poll=lambda: None)
    monkeypatch.setattr(install.subprocess, 'Popen', start)
    result = core.update_to_latest(root, hwnd_hex='0001', service=service)
    assert result['state'] == 'APPLYING'
    assert '--app-self-exit' in commands[0]


def test_failed_coordinator_result_is_deferred_in_chinese(tmp_path):
    from refactor.desktop.updates import DesktopUpdates
    result_path = tmp_path / 'one-click.result.json'
    result_path.write_text(json.dumps({'status': 'FAILED', 'detail': 'internal'}), encoding='utf-8')
    (tmp_path / core.PENDING).write_text('{}', encoding='utf-8')
    ui = SimpleNamespace(root=tmp_path, _cancel=__import__('threading').Event())
    result = DesktopUpdates._coordinator_result(ui, {'result_path': str(result_path)})
    assert result['state'] == 'PENDING_INSTALL'
    assert result['message'] == '新版已下载好，下次打开软件时完成更新。'


def test_next_launch_pending_failure_is_logged(monkeypatch, tmp_path):
    from refactor.desktop.delivery import per_user_launcher as launcher
    root = tmp_path / 'MentorFlow'; root.mkdir()
    phases = []
    monkeypatch.setattr('refactor.desktop.delivery.startup_window.log_phase',
                        lambda *args, **kwargs: phases.append((args, kwargs)))
    launcher._log_pending_install_failure(root, {'target': '2.0.12'},
                                          install.SecurityError('pending staging path is outside the approved cache'))
    assert phases and phases[0][0][1] == 'pending_install_failed'
    assert phases[0][1]['error_code'] == 'PENDING_STAGE_OUTSIDE_CACHE'
    assert 'PENDING_STAGE_OUTSIDE_CACHE' in (root / 'Data/logs/mentorflow.log').read_text(encoding='utf-8')
    assert 'outside the approved cache' in phases[0][1]['detail']


def test_stale_pending_invalidated_without_network(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    service.check(manual=True); service.download()
    _legacy_pending(root, service.staged, service.release.envelope)
    pending = core.read_pending(root)
    (root / core.PENDING).write_bytes(canonical({**pending, 'target': install.installed(root).version}))
    monkeypatch.setattr(core, 'one_click_service', lambda *_: pytest.fail('obsolete pending must not check network'))
    assert core.resume_pending_on_normal_launch(root)['obsolete']
    assert not (root / core.PENDING).exists()


def test_cleanup_preserves_data_current_rollback_pending_and_retries_locked(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    service.check(manual=True); service.download()
    legacy = _legacy_pending(root, service.staged, service.release.envelope)
    keep = root / 'Data' / 'plans.db'; keep.parent.mkdir(exist_ok=True); keep.write_bytes(b'learning')
    previous = root / 'previous'; previous.mkdir(exist_ok=True)
    (previous / 'rollback').write_bytes(b'rollback')
    junk = []
    for i, prefix in enumerate(('staging', 'retired', 'failed')):
        path = root / f'{prefix}-{i:032x}'; path.mkdir(); (path / 'old.exe').write_bytes(b'old')
        junk.append(path)
    resources = junk[0] / 'App/_internal/refactor/learning/course/data'
    resources.mkdir(parents=True); (resources / 'source_lists.json').write_bytes(b'{}')
    protected_data = root / ('retired-' + 'f' * 32)
    (protected_data / 'Data').mkdir(parents=True); (protected_data / 'Data/x').write_bytes(b'keep')
    real_remove = install.shutil.rmtree
    def locked(path):
        if path == junk[1]: raise PermissionError('busy')
        real_remove(path)
    monkeypatch.setattr(install.shutil, 'rmtree', locked)
    install.cleanup_program_files(root)
    assert not junk[0].exists() and junk[1].exists() and not junk[2].exists()
    assert legacy.exists() and keep.read_bytes() == b'learning'
    assert (previous / 'rollback').exists() and protected_data.exists()
    monkeypatch.setattr(install.shutil, 'rmtree', real_remove)
    install.cleanup_program_files(root)
    assert not junk[1].exists()
    assert install.installed(root).version == '1.4.0'


def test_transaction_failure_removes_own_staging(case, monkeypatch):
    root, service = setup(case, monkeypatch)
    service.check(manual=True); service.download()
    def fail(): raise install.AppShutdownTimeout('modal blocked')
    with pytest.raises(install.AppShutdownTimeout):
        install.transact(root, service.staged, service.release.envelope, before_swap=fail)
    assert not list(root.glob('staging-*'))
    assert service.staged.exists()


def test_native_handle_is_open_before_ack_and_exit_wait(monkeypatch, tmp_path):
    from refactor.desktop.delivery.windows_process_peer import ProcessPeer
    events = []
    monkeypatch.setattr(ProcessPeer, 'require', lambda *a, **kw: events.append('validated'))
    monkeypatch.setattr(ProcessPeer, 'visible_responsive_window', lambda *a: None)
    monkeypatch.setattr(ProcessPeer, 'alive', lambda *_: False)
    install.wait_for_app_exit(tmp_path / 'MentorFlow', install.os.getpid(), '0001', ready=lambda: events.append('ack'))
    assert events == ['validated', 'ack']


@pytest.mark.parametrize('mandatory', [False, True])
def test_failed_save_restores_deferred_state_instead_of_quitting(tmp_path, mandatory):
    from refactor.desktop.updates import DesktopUpdates
    class Shell:
        def prepare_update_shutdown(self): raise OSError('save busy')
    shown = []; published = []
    ui = SimpleNamespace(_closed=False, root=tmp_path, _prompt=None, _login_handoff=Shell(),
        _login_blocked=mandatory,
        state={'state': 'APPLYING'}, _clear_login_handoff=lambda: None,
        _publish=published.append, _show_prompt=lambda: shown.append(True))
    DesktopUpdates._finish_handoff(ui)
    assert bool(shown) is mandatory
    assert published[0]['state'] == 'PENDING_INSTALL'
    assert published[0]['message'] == '新版已下载好，下次打开软件时完成更新。'


def test_cleanup_retries_cache_without_release_sidecar(case, monkeypatch):
    root, _service = setup(case, monkeypatch)
    cache = root.parent / '.MentorFlow-runtime/.mfu' / ('a' * 12) / ('b' * 12)
    cache.mkdir(parents=True); (cache / 'leftover.dll').write_bytes(b'locked last time')
    assert install.cleanup_program_files(root)
    assert not cache.exists()


def test_cleanup_starts_on_cached_classification_before_network(tmp_path, monkeypatch):
    from refactor.desktop.updates import DesktopUpdates
    starts = []
    monkeypatch.setattr(install, 'schedule_program_cleanup', lambda root, **kw: starts.append((root, kw)))
    ui = SimpleNamespace(_closed=False, root=tmp_path, _cleanup_started=False, _startup_started=None,
        gate=SimpleNamespace(setEnabled=lambda value: None), state={},
        _publish=lambda value: None, _show_prompt=lambda: None)
    DesktopUpdates._receive(ui, {'cached': dict(state='HEALTHY', login_allowed=True)})
    DesktopUpdates._receive(ui, {'cached': dict(state='HEALTHY', login_allowed=True)})
    assert starts == [(tmp_path, {'initial_delay': 4})]


def test_cleanup_defers_while_download_cache_lock_is_held(case, monkeypatch):
    from refactor.desktop.delivery.update_download import InstallUpdateLock
    from refactor.desktop.delivery.update_service import install_cache_identity
    root, _service = setup(case, monkeypatch)
    local = root.parent / '.MentorFlow-runtime'
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    cache = local / install.configured().app_id / 'Updates' / install_cache_identity(root)
    cache.mkdir(parents=True)
    junk = root / ('staging-' + 'c' * 32)
    junk.mkdir(); (junk / 'old.dll').write_bytes(b'old')
    with InstallUpdateLock(cache):
        assert install.cleanup_program_files(root) == []
        assert junk.exists()
    install.cleanup_program_files(root)
    assert not junk.exists()


def test_invalid_pending_hint_is_logged_without_blocking_current_app(case, monkeypatch):
    from refactor.desktop.delivery import per_user_launcher as launcher
    root, _service = setup(case, monkeypatch)
    (root / core.PENDING).write_bytes(b'{invalid')
    monkeypatch.setattr(install, 'require_app_stopped', lambda *_: None)
    monkeypatch.setattr(install, 'recover', lambda *_: False)
    monkeypatch.setattr(core, 'get_startup_update_state', lambda *a, **kw: {'state': 'HEALTHY'})
    monkeypatch.setattr(install, 'launch_health', lambda *a: {'ready': True})
    assert launcher._launch_checked(root)['status'] == 'HEALTHY'
    assert 'pending_install_failed' in (root / 'Data/logs/mentorflow.log').read_text(encoding='utf-8')
