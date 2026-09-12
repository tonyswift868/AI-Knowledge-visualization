"""Synthetic reset return paths; no passwords, provider, email or live reset."""
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from desktop.in_app_oidc import InAppOidcLoginDialog, RESET_LOGIN_REQUIRED, RESET_LOGIN_MESSAGE
from desktop.login_shell import LoginGateState
from desktop.tests.test_in_app_oidc import _config, _prepared
from desktop.tests.test_login_smooth import app_context, login, Pane
from desktop.tests.test_update_ui_flow import until


@pytest.mark.parametrize('valid', [True, False])
def test_reset_callback_never_exchanges_or_logs_in(valid):
    prepared = _prepared()
    callback = prepared.transaction.redirect_uri + '?' + urlencode({
        'code': 'synthetic-callback-only',
        'state': prepared.transaction.state if valid else 'incorrect-state',
        'iss': prepared.transaction.expected_issuer,
    })
    receiver = SimpleNamespace(wait=lambda _: callback, close=Mock())
    prepared = replace(prepared, receiver=receiver)
    exchange = Mock()
    closed = Mock()
    class Client:
        async def prepare_login(self, **kwargs): return prepared
        async def complete_login(self, *args):
            exchange()
            return login()
        async def aclose(self): closed()
    class Surface(InAppOidcLoginDialog):
        def _show_authorization_page(self, value):
            self._prepared = value
            self._page_ready.set()  # No browser or network.
    pane = Surface(_config(), embedded=True, intent='forgot_password',
                   capabilities={'forgot_password': True}, client_factory=lambda _: Client())
    accepted, failed = Mock(), Mock()
    pane.authenticated.connect(accepted)
    pane.authentication_failed.connect(failed)
    try:
        pane.start_embedded()
        until(lambda: failed.called and not pane._worker.is_alive())
        exchange.assert_not_called()
        accepted.assert_not_called()
        assert pane._result is None
        assert failed.call_args.args[0] == (RESET_LOGIN_REQUIRED if valid else 'ENTERPRISE_OIDC_STATE_INVALID')
        assert prepared.transaction._consumed
        assert receiver.close.called
        closed.assert_called_once()
    finally:
        pane.dispose()
        QApplication.instance().processEvents()


def test_reset_late_success_signal_cannot_authenticate():
    pane = InAppOidcLoginDialog(_config(), embedded=True, intent='forgot_password')
    accepted, failed = Mock(), Mock()
    pane.authenticated.connect(accepted)
    pane.authentication_failed.connect(failed)
    try:
        pane._finish_successfully(login())
        accepted.assert_not_called()
        failed.assert_called_once_with(RESET_LOGIN_REQUIRED, RESET_LOGIN_MESSAGE)
        assert pane._result is None
    finally:
        pane.dispose()
        QApplication.instance().processEvents()


@pytest.mark.parametrize('signal', ['authenticated', 'authentication_failed'])
def test_desktop_returns_to_normal_login_without_session(app_context, monkeypatch, signal):
    target, app, _ = app_context
    def create(config, **options):
        options.pop('intent', None)
        options.pop('capabilities', None)
        return Pane(config, **options)
    monkeypatch.setattr('desktop.in_app_oidc.InAppOidcLoginDialog', create)
    target._show_login_gate()
    app.processEvents()
    gate = target.login_gate
    gate.set_account_capabilities({'forgot_password': True})  # Test seam only.
    accept = Mock()
    monkeypatch.setattr(target, '_accept_login_result', accept)
    target._begin_account_action('forgot_password')
    pane = target._auth_pane
    if signal == 'authenticated': pane.authenticated.emit(login())
    else: pane.authentication_failed.emit(RESET_LOGIN_REQUIRED, 'untrusted provider text')
    app.processEvents()
    accept.assert_not_called()
    assert target._last_login_result is None
    assert target.main is None and target.service is None
    assert target._auth_pane is None and gate.auth_content is None
    assert gate.state == LoginGateState.LOGIN_READY
    assert gate.primary_button.text() == '登录 MentorFlow' and gate.primary_button.isEnabled()
    assert gate.state_detail.text() == RESET_LOGIN_MESSAGE
    assert '密码已重置' not in gate.state_detail.text()  # Callback isn't a reset-success signal.
    gate.primary_button.click()
    app.processEvents()
    assert target._auth_pane.started  # A fresh ordinary login, still no session.
    accept.assert_not_called()


def test_stale_reset_return_cannot_replace_current_auth_surface(app_context):
    target, app, _ = app_context
    target._show_login_gate()
    app.processEvents()
    pane = target._auth_pane
    target._return_from_password_reset(target._login_generation - 1)
    assert target._auth_pane is pane
