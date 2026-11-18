import pytest

from refactor.desktop.delivery import per_user_install as install
from refactor.desktop.delivery import per_user_launcher as launcher
from refactor.desktop.delivery import windows_process_peer
from refactor.desktop.delivery.update_protocol import RecoverableError


class _Peer:
    visible = []
    focused = []
    def __init__(self, pid): self.pid=pid
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def visible_windows(self): return list(self.visible)
    def foreground(self): self.focused.append(self.pid)


def test_launcher_ends_confirmed_windowless_app_and_continues(monkeypatch, tmp_path):
    marks=[]; terminated=[]
    tmp_path = tmp_path / 'MentorFlow'; tmp_path.mkdir()
    monkeypatch.setattr(install, 'require_app_stopped',
                        lambda root: (_ for _ in ()).throw(
                            RecoverableError('close the current App before continuing')))
    monkeypatch.setattr(install, '_matching_app_pids', lambda image: [77])
    monkeypatch.setattr(windows_process_peer, 'ProcessPeer', _Peer)
    monkeypatch.setattr(install, 'app_exit_confirmed', lambda root, pid: True)
    monkeypatch.setattr(install, 'terminate_confirmed_app',
                        lambda root, pid: terminated.append((root, pid)))
    launcher._ensure_app_stopped(tmp_path, marks.append)
    assert terminated == [(tmp_path, 77)]
    assert marks == ['stale_app_terminated']


def test_launcher_reports_unknown_busy_app_instead_of_silent_exit(monkeypatch, tmp_path):
    messages=[]; maintenance=[]
    tmp_path = tmp_path / 'MentorFlow'; tmp_path.mkdir()
    monkeypatch.setattr(install, 'require_app_stopped',
                        lambda root: (_ for _ in ()).throw(
                            RecoverableError('close the current App before continuing')))
    monkeypatch.setattr(install, '_matching_app_pids', lambda image: [88])
    monkeypatch.setattr(windows_process_peer, 'ProcessPeer', _Peer)
    monkeypatch.setattr(install, 'app_exit_confirmed', lambda root, pid: False)
    monkeypatch.setattr('refactor.desktop.delivery.startup_window.set_startup_text',
                        lambda text, **kwargs: messages.append(text))
    monkeypatch.setattr(install, 'maintenance_log',
                        lambda root, phase, **fields: maintenance.append((phase, fields)))
    with pytest.raises(launcher._AppBusy, match='还没完全退出'):
        launcher._ensure_app_stopped(tmp_path, lambda phase: None)
    assert messages and '重试' in messages[0]
    assert maintenance[0][0] == 'launch_blocked_app_running'


def test_launcher_brings_visible_busy_app_forward(monkeypatch, tmp_path):
    messages=[]
    tmp_path = tmp_path / 'MentorFlow'; tmp_path.mkdir()
    _Peer.visible=[123]; _Peer.focused=[]
    monkeypatch.setattr(install, 'require_app_stopped',
                        lambda root: (_ for _ in ()).throw(
                            RecoverableError('close the current App before continuing')))
    monkeypatch.setattr(install, '_matching_app_pids', lambda image: [99])
    monkeypatch.setattr(windows_process_peer, 'ProcessPeer', _Peer)
    monkeypatch.setattr(install, 'app_exit_confirmed', lambda root, pid: False)
    monkeypatch.setattr('refactor.desktop.delivery.startup_window.set_startup_text',
                        lambda text, **kwargs: messages.append(text))
    monkeypatch.setattr(install, 'maintenance_log', lambda *args, **kwargs: None)
    with pytest.raises(launcher._AppBusy):
        launcher._ensure_app_stopped(tmp_path, lambda phase: None)
    assert _Peer.focused == [99]
    assert messages and '还在退出' in messages[0]
    _Peer.visible=[]
