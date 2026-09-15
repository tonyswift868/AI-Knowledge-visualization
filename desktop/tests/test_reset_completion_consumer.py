"""Consumer never interprets HTML, callback URLs or 200 as password success."""
import asyncio
from dataclasses import replace
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from desktop.enterprise_oidc import EnterpriseOidcError
from desktop.reset_completion import ResetCompletionClient, COMPLETED_CODE, COMPLETED_MESSAGE, SCHEMA
from desktop.tests.test_in_app_oidc import _config, _prepared, _application
from desktop.tests.test_login_smooth import app_context, Pane, login

TICKET = 't' * 43


@pytest.mark.parametrize('value', [
    {'success': True}, {'schema': SCHEMA, 'transaction_id': 'o' * 43, 'status': 'COMPLETED'},
    {'schema': SCHEMA, 'transaction_id': TICKET, 'status': 'COMPLETED', 'access_token': 'untrusted'},
    '<html>Password updated</html>',
])
def test_untrusted_responses_cannot_complete(value):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=value))) as transport:
            client = ResetCompletionClient(_config(), http_client=transport)
            with pytest.raises(EnterpriseOidcError):
                await client.status(TICKET)
    asyncio.run(run())


def test_commit_poll_has_no_login_side_effects():
    async def run():
        calls = []
        def server(request):
            calls.append(request.url.path)
            return httpx.Response(200, json={'schema': SCHEMA, 'transaction_id': TICKET, 'status': 'COMPLETED'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as transport:
            client = ResetCompletionClient(_config(), http_client=transport)
            assert await client.wait_completed(TICKET, threading.Event()) is None
        assert calls == ['/api/enterprise/auth/reset/status']
    asyncio.run(run())


def test_cancelled_observer_cannot_finish():
    cancelled = threading.Event(); cancelled.set()
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: pytest.fail('cancelled request'))) as transport:
            with pytest.raises(EnterpriseOidcError):
                await ResetCompletionClient(_config(), http_client=transport).wait_completed(TICKET, cancelled)
    asyncio.run(run())


def test_dialog_only_canonical_completion_returns_login_and_never_exchanges(monkeypatch):
    from desktop.in_app_oidc import InAppOidcLoginDialog
    from desktop.tests.test_update_ui_flow import until
    app = _application()
    prepared = replace(_prepared(), receiver=SimpleNamespace(close=Mock(), wait=Mock(side_effect=AssertionError('callback must not be used'))))
    exchange, begin, close = Mock(), Mock(), Mock()
    class Client:
        async def prepare_login(self, **kwargs): return prepared
        async def complete_login(self, *args): exchange(); raise AssertionError()
        async def aclose(self): pass
    class Reset:
        def __init__(self, config): pass
        async def begin(self, state): begin(state); return TICKET
        async def wait_completed(self, ticket, cancelled, **kwargs): assert ticket == TICKET
        async def aclose(self): close()
    monkeypatch.setattr('desktop.reset_completion.ResetCompletionClient', Reset)
    class Surface(InAppOidcLoginDialog):
        def _show_authorization_page(self, value): self._prepared = value; self._page_ready.set()
    pane = Surface(_config(), embedded=True, intent='forgot_password', capabilities={'forgot_password': True}, client_factory=lambda c: Client())
    accepted, failed = Mock(), Mock()
    pane.authenticated.connect(accepted); pane.authentication_failed.connect(failed)
    try:
        pane.start_embedded()
        until(lambda: failed.called and not pane._worker.is_alive())
        begin.assert_called_once_with(prepared.transaction.state)
        failed.assert_called_once_with(COMPLETED_CODE, COMPLETED_MESSAGE)
        accepted.assert_not_called(); exchange.assert_not_called()
        prepared.receiver.wait.assert_not_called()
        assert pane._result is None and close.called
    finally:
        pane.dispose(); app.processEvents()


@pytest.mark.parametrize('outcome', ['canonical', 'accepted_only', 'generic_error'])
def test_main_returns_to_login_only_after_canonical_signal(app_context, monkeypatch, outcome):
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QWidget
    from PySide6.QtTest import QTest
    from desktop import lifecycle_pane
    from desktop.login_shell import LoginGateState
    target, app, _ = app_context
    class Client:
        def __init__(self, config): pass
        async def action(self, action, body):
            if outcome == 'generic_error': raise RuntimeError('HTTP 200 is not completion')
            return {'observation': {'transaction_id': 'synthetic-reset'}}
        async def aclose(self): pass
    class Observer:
        def __init__(self, config): pass
        async def wait_completed(self, transaction, cancelled):
            assert transaction == 'synthetic-reset'
            if outcome != 'canonical': raise TimeoutError('accepted is not completed')
        async def aclose(self): pass
    class NativePane(QWidget):
        finished = Signal(bool)
        def __init__(self, intent, parent=None):
            super().__init__(parent)
            assert intent == 'forgot_password'
            self.bridge = lifecycle_pane.Bridge(self)
            self.bridge.back.connect(self.finished.emit)
        def cancel(self): self.bridge.cancelled.set()
        def dispose(self): self.cancel(); self.hide()
    monkeypatch.setattr(lifecycle_pane, 'LifecycleClient', Client)
    monkeypatch.setattr(lifecycle_pane, 'ResetCompletionClient', Observer)
    monkeypatch.setattr(lifecycle_pane, 'AccountLifecyclePane', NativePane)
    target._show_login_gate(); app.processEvents()
    gate = target.login_gate
    gate.set_account_capabilities({'forgot_password': True})
    accept = Mock(); monkeypatch.setattr(target, '_accept_login_result', accept)
    target._begin_account_action('forgot_password')
    pane = target._auth_pane
    pane.bridge.forgot('synthetic-user', 1)
    import time
    for _ in range(200):
        app.processEvents(); time.sleep(0.01)
        if not pane.bridge.busy: break
    assert not pane.bridge.busy
    app.processEvents()
    if outcome != 'canonical':
        assert target._auth_pane is pane
        assert gate.state == LoginGateState.AUTHENTICATING
        pane.bridge.returnLogin(); app.processEvents()
    assert gate.state == LoginGateState.LOGIN_READY
    assert target._auth_pane is None and target._last_login_result is None and target.main is None
    assert gate.password_field.text() == ''
    assert (gate.state_detail.text() == COMPLETED_MESSAGE) == (outcome == 'canonical')
    accept.assert_not_called()


def test_stale_reset_completion_cannot_replace_new_login(app_context):
    target, app, _ = app_context
    target._show_login_gate(); app.processEvents()
    gate, generation = target.login_gate, target._login_generation
    previous = gate.state_detail.text()
    target._return_from_password_reset(generation - 1, completed=True)
    assert target._login_generation == generation and gate.state_detail.text() == previous
