"""Integration seams only; no installed App, network, account or transaction I/O."""
import os
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from desktop.update_lifecycle import CoreUpdatePort


@pytest.fixture
def per_user_port(tmp_path, monkeypatch):
    service = SimpleNamespace(install_root=tmp_path / 'MentorFlow', current='1.5.0',
                              snapshot={'state': 'UPDATE_AVAILABLE', 'latest': '1.5.1'},
                              apply_bridge=None, close=Mock())
    session = SimpleNamespace(require_running=Mock(), defer=Mock(), close=Mock())
    monkeypatch.setattr('scripts.per_user_install.is_install', lambda _: True)
    monkeypatch.setattr('desktop.update_session.UpdateSession', lambda: session)
    port = CoreUpdatePort(service, window_id=lambda: '000000000000002a')
    yield port, service, session
    port.close()


@pytest.mark.parametrize('state,display', [
    ('HEALTHY', 'UP_TO_DATE'), ('OPTIONAL_UPDATE', 'OPTIONAL_UPDATE'),
    ('MANDATORY_UPDATE', 'MANDATORY_UPDATE'), ('REPAIR_REQUIRED', 'REPAIR_REQUIRED'),
])
def test_check_uses_delivered_startup_classifier(per_user_port, monkeypatch, state, display):
    port, service, _ = per_user_port
    classifier = Mock(return_value={'state': state, 'login_allowed': state in ('HEALTHY', 'OPTIONAL_UPDATE')})
    monkeypatch.setattr('scripts.per_user_update_core.get_startup_update_state', classifier)
    assert port.check_latest()['state'] == display
    classifier.assert_called_once_with(service.install_root, service=service)


def test_repair_binds_exact_core_and_does_not_apply_twice(per_user_port, monkeypatch):
    port, service, session = per_user_port
    repair = Mock(return_value={'state': 'APPLYING', 'coordinator_started': True, 'coordinator_pid': 99})
    apply = Mock(side_effect=AssertionError('second apply is forbidden'))
    monkeypatch.setattr('scripts.per_user_update_core.repair_installation', repair)
    monkeypatch.setattr('scripts.per_user_update_core.apply_and_relaunch', apply)
    cancel = threading.Event()
    assert port.repair_and_check(lambda _: None, cancel)['state'] == 'PREPARING'
    repair.assert_called_once()
    args, kwargs = repair.call_args
    assert args == (service.install_root,)
    assert kwargs['service'] is service and kwargs['pid'] == os.getpid()
    assert kwargs['hwnd_hex'] == '000000000000002a'
    assert callable(kwargs['progress']) and kwargs['cancel']() is False
    session.require_running.assert_called_once()
    assert port.apply_and_relaunch()['state'] == 'PREPARING'
    port.repair_and_check(lambda _: None, cancel)
    repair.assert_called_once(); apply.assert_not_called()


@pytest.mark.parametrize('fence', ['cancel', 'window', 'session', 'closed'])
def test_repair_preserves_existing_session_and_cancel_fences(per_user_port, monkeypatch, fence):
    from desktop.update_session import SessionEnding
    port, _, session = per_user_port
    repair = Mock(side_effect=AssertionError('guarded repair must not run'))
    monkeypatch.setattr('scripts.per_user_update_core.repair_installation', repair)
    cancel = threading.Event()
    if fence == 'cancel': cancel.set()
    if fence == 'window': port.can_apply = lambda: False
    if fence == 'session': session.require_running.side_effect = SessionEnding('ending')
    if fence == 'closed': port._closed = True
    port.repair_and_check(lambda _: None, cancel)
    repair.assert_not_called()


def test_failed_per_user_admission_never_uses_protected_factory(tmp_path, monkeypatch):
    from desktop.update_lifecycle import configured_update_service
    from desktop.update_protocol import SecurityError
    root = tmp_path / 'MentorFlow'
    monkeypatch.setattr('scripts.per_user_install.target', lambda value: value)
    monkeypatch.setattr('scripts.per_user_install.is_install', lambda _: True)
    core = Mock(side_effect=SecurityError('invalid signed identity'))
    legacy = Mock(side_effect=AssertionError('legacy fallback forbidden'))
    monkeypatch.setattr('scripts.per_user_update_core.one_click_service', core)
    monkeypatch.setattr('desktop.update_service.configured_mature_service', legacy)
    with pytest.raises(SecurityError): configured_update_service(install_root=root)
    core.assert_called_once_with(root); legacy.assert_not_called()


def test_precomputed_policy_stays_blocked_without_service():
    from desktop.login_shell import LoginGateState, LoginGateWindow
    from desktop.update_controller import UpdateController
    gate = LoginGateWindow()
    controller = UpdateController()
    try:
        gate.bind_update_controller(controller)
        gate.set_startup_policy({'state': 'MANDATORY_UPDATE', 'login_allowed': False})
        gate.set_state(LoginGateState.AUTH_READY)
        assert gate.login_is_blocked and not gate.primary_button.isEnabled()
        assert controller.port.snapshot['state'] == 'MANDATORY_UPDATE'
        assert controller.port.snapshot['available'] is False
    finally:
        gate.close(); controller.shutdown()
