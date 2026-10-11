"""A second shortcut must not launch the old App during update handoff."""
import threading

import pytest

from refactor.desktop.delivery import per_user_install as install
from refactor.desktop.delivery import per_user_launcher as launcher
from refactor.desktop.delivery import startup_window as splash


def test_shortcut_defers_while_real_coordinator_gate_is_held(tmp_path, monkeypatch):
    root = tmp_path / 'MentorFlow'
    root.mkdir()
    events = []
    monkeypatch.setattr(install, 'maintenance_log', lambda *a, **kw: events.append(a[1]))
    monkeypatch.setattr(launcher, '_launch_serialized',
                        lambda *a: pytest.fail('must not inspect or launch App while updating'))
    with install.launch_coordination(root):
        assert launcher._launch_checked(root)['status'] == 'UPDATE_IN_PROGRESS'
    assert events == ['launch_deferred_update_in_progress']
    monkeypatch.setattr(launcher, '_launch_serialized', lambda *a: {'status': 'HEALTHY'})
    assert launcher._launch_checked(root)['status'] == 'HEALTHY'


def test_update_splash_pumps_main_thread_and_preserves_runtime_context(monkeypatch):
    from contextvars import ContextVar
    value = ContextVar('test_update_session', default='missing')
    value.set('active-session')
    main = threading.get_ident()
    events = []
    monkeypatch.setattr(splash, 'pump_native_messages', lambda: events.append(('pump', threading.get_ident())))
    monkeypatch.setattr(splash, 'close_native_splash', lambda: events.append(('closed', threading.get_ident())))
    def action():
        threading.Event().wait(.03)
        assert threading.get_ident() != main
        return value.get()
    assert splash.run_with_native_splash(action) == 'active-session'
    assert events and all(ident == main for _, ident in events)
    assert events[-1][0] == 'closed'


def test_update_splash_closes_and_propagates_failure(monkeypatch):
    closed = []
    monkeypatch.setattr(splash, 'pump_native_messages', lambda: None)
    monkeypatch.setattr(splash, 'close_native_splash', lambda: closed.append(True))
    def action():
        raise OSError('test disk failure')
    with pytest.raises(OSError, match='test disk failure'):
        splash.run_with_native_splash(action)
    assert closed == [True]


def test_update_exit_registers_intent_with_desktop_lifetime_guard(monkeypatch):
    from types import SimpleNamespace
    from refactor.desktop import updates
    events = []
    app = SimpleNamespace(_mentorflow_diagnostics=SimpleNamespace(request_exit=events.append),
                          quit=lambda: pytest.fail('unregistered Qt Quit'))
    monkeypatch.setattr(updates, 'QApplication', SimpleNamespace(instance=lambda: app))
    updates.DesktopUpdates._exit_for_update()
    assert events == ['UPDATE_HANDOFF']


def test_background_cleanup_retries_busy_lock_without_waiting_on_gui(tmp_path, monkeypatch):
    (tmp_path / install.STATE).write_text('{}')
    delays, attempts = [], []
    monkeypatch.setattr(install.time, 'sleep', delays.append)
    def cleanup(root, *, retry_busy=False):
        assert retry_busy
        attempts.append(root)
        if len(attempts) < 3:
            raise install.RecoverableError('another update operation is active')
    monkeypatch.setattr(install, 'cleanup_program_files', cleanup)
    thread = install.schedule_program_cleanup(tmp_path)
    thread.join(1)
    assert not thread.is_alive()
    assert delays == [2, 3, 5]
