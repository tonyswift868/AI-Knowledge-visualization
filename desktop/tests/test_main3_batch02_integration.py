"""Batch02 signed update policy + native login + presentation composition.

OIDC is legacy only. Releases contain signed inert bytes; health/process and
credential-provider boundaries are synthetic here, with native SPI tested apart.
"""
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest
from PySide6.QtWidgets import QLineEdit

from desktop.enterprise_oidc import EnterpriseOidcError
from desktop.login_shell import LoginGateState
from desktop.update_controller import UpdateController
from desktop.update_lifecycle import CoreUpdatePort
from desktop.update_panel import UpdatePanel
from desktop.tests.test_login_smooth import app_context
from desktop.tests.test_update_ui_flow import no_external_launch, until
from scripts import per_user_update_core as core
from scripts.test_canonical_identity_recovery import case, recovery_case
from scripts.test_signed_mandatory_floor import new_policy
from scripts.test_mandatory_reason_public_seam import historical_category


@pytest.fixture
def composed(app_context, recovery_case, monkeypatch):
    target, qt, settings = app_context
    root, service, old, feed = recovery_case
    monkeypatch.setattr('desktop.update_session.UpdateSession', lambda: SimpleNamespace(
        require_running=Mock(), defer=Mock(), close=Mock()))
    target._show_login_gate()
    qt.processEvents()
    previous = getattr(qt, '_mentorflow_updater', None)
    if previous is not None:
        previous.shutdown()
    controller = UpdateController(port=CoreUpdatePort(service), settings=settings)
    monkeypatch.setattr(qt, '_mentorflow_updater', controller)
    gate = target.login_gate
    gate.bind_update_controller(controller)
    # These tests drive real policy/check/apply synchronously. A timer-started
    # second check can outlive the disposable HTTP feed during teardown.
    panel = UpdatePanel(gate, controller=controller, compact=True, auto_delay_ms=600_000)
    yield target, gate, controller, panel, root, service, settings
    panel.close()
    controller.shutdown()


@pytest.mark.parametrize('reason,copy', [
    ('security', '安全问题'), ('protocol', '兼容最新服务'),
    ('data_compatibility', '数据兼容性'), (None, '停止支持')])
@pytest.mark.parametrize('failure', ['none', 'offline', 'cancel'])
def test_signed_historical_floor_blocks_native_login_and_cannot_be_skipped(
        composed, recovery_case, monkeypatch, reason, copy, failure):
    target, gate, controller, panel, root, service, _ = composed
    if reason:
        historical_category(recovery_case, reason)
    new_policy(recovery_case)
    policy = core.get_startup_update_state(root, service=service)
    assert policy['latest'] == policy['target'] == '1.6.0'
    assert policy['min_supported_version'] == '1.5.0'
    gate.password_field.setText('synthetic-test-only')
    gate.set_startup_policy(policy)
    panel.refresh()
    assert gate.login_is_blocked and gate.password_field.text() == ''
    assert not gate.login_card.isVisible() and not gate.password_field.isEnabled()
    assert '1.6.0' in panel.status.text() and copy in panel.status.text()
    assert panel.later_button.isHidden() and panel.exit_update_button.isHidden()
    native = Mock(side_effect=AssertionError('mandatory native login forbidden'))
    monkeypatch.setattr('desktop.native_auth.login_native_account_from_env', native)
    if failure == 'offline':
        def offline(*args): raise OSError('synthetic offline')
        monkeypatch.setattr(service.cache.transport, 'fetch_metadata', offline)
        gate.set_startup_policy(core.get_startup_update_state(root, service=service))
    elif failure == 'cancel':
        cancel = threading.Event(); cancel.set()
        controller.port.repair_and_check(lambda _: None, cancel)
    controller.later(); controller.schedule_on_exit()
    target._begin_interactive_login()
    panel.refresh()
    native.assert_not_called()
    assert gate.login_is_blocked and not controller.postponed and not controller.update_on_exit
    assert controller.presentation['latest'] == '1.6.0'
    assert target.main is None and target._auth_pane is None


def test_successful_signed_update_reenables_native_credentials(composed, recovery_case, monkeypatch):
    target, gate, controller, panel, root, service, settings = composed
    new_policy(recovery_case)
    gate.set_startup_policy(core.get_startup_update_state(root, service=service))
    assert gate.login_is_blocked and not gate.password_field.isEnabled()
    repaired = core.repair_installation(root, service=service)
    assert repaired['state'] == 'CURRENT_HEALTHY' and repaired['version'] == '1.6.0'
    gate.set_startup_policy(core.get_startup_update_state(root, service=service, check_feed=False))
    panel.refresh()
    assert not gate.login_is_blocked and gate.login_card.isVisible()
    assert gate.username_field.isEnabled() and gate.password_field.isEnabled()
    assert gate.password_field.echoMode() == QLineEdit.EchoMode.Password
    calls = []
    def native(username, password, **kwargs):
        calls.append(username)
        raise EnterpriseOidcError('NATIVE_CREDENTIALS_INVALID', 'synthetic denial')
    monkeypatch.setattr('desktop.native_auth.login_native_account_from_env', native)
    gate.username_field.setText('synthetic-user'); gate.password_field.setText('synthetic-test-only')
    gate.primary_button.click()
    until(lambda: gate.state == LoginGateState.AUTH_FAILED)
    assert calls == ['synthetic-user'] and gate.password_field.text() == ''
    assert target.main is None and target._auth_pane is None
    assert 'synthetic-test-only' not in repr(settings.values)


@pytest.mark.parametrize('choice', ['later', 'exit_update'])
def test_signed_optional_reason_does_not_block_native_form(composed, recovery_case, choice):
    _, gate, controller, panel, root, service, _ = composed
    new_policy(recovery_case, floor='1.0.0')
    feed = recovery_case[3]
    feed.signed['mandatory_reason'] = 'security'
    feed.sign(feed.signed)
    policy = core.get_startup_update_state(root, service=service)
    assert policy['state'] == 'OPTIONAL_UPDATE' and policy['mandatory_reason'] == ''
    gate.set_startup_policy(policy)
    panel.refresh()
    if choice == 'later': controller.later()
    else: controller.schedule_on_exit()
    assert not gate.login_is_blocked and gate.username_field.isEnabled() and gate.password_field.isEnabled()
    assert controller.presentation['latest'] == '1.6.0'
    assert controller.update_on_exit is (choice == 'exit_update')
    assert not gate.register_button.isEnabled() and not gate.forgot_password_button.isEnabled()
    controller.later()  # No real exit/apply in fixture cleanup.
