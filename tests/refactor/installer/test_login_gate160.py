"""Startup update gate and account-login coordination contracts for batch 160."""
from types import SimpleNamespace
import threading
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from refactor.desktop.updates import DesktopUpdates
from refactor.desktop.account_shell_main import AccountShell


@pytest.fixture
def app160():
    return QApplication.instance() or QApplication([])


def pump160(app, predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    app.processEvents()
    assert predicate()


class Core160:
    def __init__(self, network_state='HEALTHY', *, hold=False):
        self.network_state = network_state
        self.cached_state = 'HEALTHY'
        self.entered = threading.Event()
        self.release = threading.Event()
        if not hold:
            self.release.set()

    def attach_shutdown_guard(self, _app):
        pass

    def get_startup_update_state(self, _root, *, full, check_feed, cancel, on_pointer_policy=None):
        assert full is False
        if check_feed:
            self.entered.set()
            self.release.wait(2)
            state = self.network_state
            feed = 'VERIFIED'
        else:
            state = self.cached_state
            feed = 'NOT_CHECKED'
        return {
            'state': state,
            'login_allowed': state in {'HEALTHY', 'OPTIONAL_UPDATE'},
            'current': '2.0.20', 'latest': '2.0.21', 'target': '2.0.21',
            'feed': feed, 'mandatory_floor': '2.0.21',
            'message': '当前版本需要更新后才能继续' if state == 'MANDATORY_UPDATE' else '可以正常登录。',
        }


def disable_housekeeping(monkeypatch):
    from refactor.desktop.delivery import per_user_install
    monkeypatch.setattr(per_user_install, 'schedule_program_cleanup', lambda *_a, **_k: None)
    monkeypatch.setattr(per_user_install, 'schedule_updater_refresh', lambda *_a, **_k: None)
    # Startup discovery may schedule background download/preparation after an
    # OPTIONAL/MANDATORY result. This unit fake intentionally models only the
    # startup classifier, not the download core.
    monkeypatch.setattr(DesktopUpdates, '_prepare_in_background', lambda _self: None)


def show_gate160(app, gate, monkeypatch):
    # A previous native-window test may leave a visible active window in the
    # shared QApplication. Production intentionally sends the update notice to
    # that main window instead of opening a second native modal over it.
    gate.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    gate.show()
    monkeypatch.setattr(QApplication, 'activeWindow', lambda: gate)
    app.processEvents()


def test_login_gate160_login_continues_during_background_network_check(app160, tmp_path, monkeypatch):
    disable_housekeeping(monkeypatch)
    gate = QWidget()
    show_gate160(app160, gate, monkeypatch)
    core = Core160(hold=True)
    ui = DesktopUpdates(app160, tmp_path, gate, core=core)
    try:
        ui.start()
        pump160(app160, lambda: core.entered.is_set() and ui._startup_cached is not None)
        assert gate.isEnabled()
        assert ui.snapshot()['login_check_pending'] is False
        assert ui.snapshot()['login_check_status'] == 'background'
        assert ui.snapshot()['login_allowed'] is True
        assert ui.snapshot()['cached_login_allowed'] is True
    finally:
        core.release.set()
        ui.close()
        gate.close()


@pytest.mark.parametrize('state,allowed,mandatory', [
    ('MANDATORY_UPDATE', False, True),
    ('OPTIONAL_UPDATE', True, False),
    ('HEALTHY', True, False),
])
def test_login_gate160_network_result_resolves_policy(app160, tmp_path, monkeypatch,
                                                       state, allowed, mandatory):
    disable_housekeeping(monkeypatch)
    gate = QWidget()
    show_gate160(app160, gate, monkeypatch)
    ui = DesktopUpdates(app160, tmp_path, gate, core=Core160(state))
    try:
        ui.start()
        pump160(app160, lambda: ui.snapshot().get('login_check_status') == 'ready')
        assert ui.snapshot()['login_check_status'] == 'ready'
        assert ui.snapshot()['login_allowed'] is allowed
        assert ui.snapshot()['mandatory'] is mandatory
        assert gate.isEnabled() is allowed
        if mandatory:
            assert ui._prompt is not None and ui._prompt.isVisible()
            assert not ui._prompt.later.isVisible()
        if state == 'OPTIONAL_UPDATE':
            assert ui._prompt is None
            assert ui.snapshot()['latest'] == '2.0.21'
            assert gate.isEnabled()
    finally:
        ui.close()
        gate.close()


def test_login_gate160_background_check_can_later_block_with_signed_mandatory(
        app160, tmp_path, monkeypatch):
    disable_housekeeping(monkeypatch)
    gate = QWidget()
    show_gate160(app160, gate, monkeypatch)
    core = Core160('MANDATORY_UPDATE', hold=True)
    ui = DesktopUpdates(app160, tmp_path, gate, core=core)
    try:
        ui.start()
        pump160(app160, lambda: core.entered.is_set() and ui._startup_cached is not None)
        # User 2026-10-20: never wait for network discovery to log in.
        # The signed result still constrains the account once it arrives.
        assert ui.snapshot()['login_check_pending'] is False
        assert ui.snapshot().get('login_allowed', False)
        assert not ui.snapshot().get('offline_fallback', False)
        assert gate.isEnabled()

        core.release.set()
        pump160(app160, lambda: ui.snapshot().get('login_check_status') == 'ready')
        assert ui.snapshot()['mandatory'] is True
        assert ui.snapshot()['login_allowed'] is False
        assert not gate.isEnabled()
        assert ui._prompt is not None and not ui._prompt.later.isVisible()
    finally:
        core.release.set()
        ui.close()
        gate.close()


def test_login_gate160_shell_only_blocks_explicit_verified_restriction():
    adapter = SimpleNamespace(state={'login_check_pending': True})
    gate_calls = []
    shell = SimpleNamespace(app=SimpleNamespace(_mentorflow_updates=adapter),
                            _update_auto_pending=False, _update_interactive_pending=False,
                            _closing=False, gate=SimpleNamespace(
                                set_state=lambda *args, **kwargs: gate_calls.append((args, kwargs))))
    assert AccountShell._updates_allow_login(shell)
    adapter.state.update(login_allowed=False, mandatory=True, state='MANDATORY_UPDATE')
    assert not AccountShell._updates_allow_login(shell)
    AccountShell._begin_interactive_login(shell)
    AccountShell._begin_remembered_login(shell)
    assert shell._update_interactive_pending is False and shell._update_auto_pending is False
    assert gate_calls == []
    adapter.state = {'login_check_pending': False, 'login_allowed': True,
                     'mandatory': False, 'state': 'HEALTHY'}
    assert AccountShell._updates_allow_login(shell)
