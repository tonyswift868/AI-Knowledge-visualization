"""Batch01 composition checks; synthetic OIDC and signed inert install bytes only.

Native coordinator/process, health and provider boundaries are substituted. These
checks do not constitute live auth, native installation or final candidate QA.
"""
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from desktop.in_app_oidc import InAppOidcLoginDialog
from desktop.login_shell import LoginGateState, LoginGateWindow
from desktop.update_controller import UpdateController
from desktop.update_lifecycle import CoreUpdatePort
from desktop.tests.test_e109_system_browser import config, prepare, OLD_ISSUER, NEW_ISSUER
from desktop.tests.test_update_ui_flow import no_external_launch, until
from scripts import per_user_install as install, per_user_update_core as core
from scripts import per_user_launcher as launcher
from scripts.test_per_user_install import case
from scripts.test_canonical_identity_recovery import recovery_case, damage, snapshot


@pytest.mark.parametrize('discovered', [OLD_ISSUER, NEW_ISSUER])
@pytest.mark.parametrize('intent', ['register', 'forgot_password'])
@pytest.mark.parametrize('embedded', [False, True])
def test_e109_disabled_intent_stops_before_system_browser(monkeypatch, discovered, intent, embedded):
    monkeypatch.setattr(time, 'time', lambda: 150)
    cfg = config()
    prepared = prepare(cfg, discovered)
    class Client:
        async def prepare_login(self, **kwargs):
            return prepared
        async def aclose(self):
            pass
        async def complete_login(self, *args):
            pytest.fail('disabled action must not exchange a code')
    dialog = InAppOidcLoginDialog(cfg, embedded=embedded, intent=intent,
        capabilities={'register': False, 'forgot_password': False}, client_factory=lambda _: Client())
    try:
        dialog._run_protocol()
        QApplication.instance().processEvents()
        assert dialog._error.code == 'ENTERPRISE_ACCOUNT_ACTION_UNAVAILABLE'
        assert prepared.receiver._closed.is_set()
        assert not dialog._browser_created
    finally:
        prepared.receiver.close()
        dialog.dispose()
        QApplication.instance().processEvents()


@pytest.mark.parametrize('state,allowed', [
    ('MANDATORY_UPDATE', False), ('REPAIR_REQUIRED', False), ('OPTIONAL_UPDATE', True)])
@pytest.mark.parametrize('discovered', [OLD_ISSUER, NEW_ISSUER])
def test_login_update_policy_controls_e109_browser_without_enabling_accounts(monkeypatch, state, allowed, discovered):
    monkeypatch.setattr(time, 'time', lambda: 150)
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url.toString()) or True)
    cfg = config()
    prepared = prepare(cfg, discovered)
    dialog = InAppOidcLoginDialog(cfg, embedded=True, capabilities={})
    gate, controller = LoginGateWindow(), UpdateController()
    actions = []
    gate.login_requested.connect(lambda: dialog._show_authorization_page(prepared))
    gate.register_requested.connect(lambda: actions.append('register'))
    gate.forgot_password_requested.connect(lambda: actions.append('forgot_password'))
    try:
        gate.bind_update_controller(controller)
        gate.set_startup_policy({'state': state, 'login_allowed': allowed})
        gate.set_state(LoginGateState.AUTH_READY)
        gate.set_account_capabilities({'register': False, 'forgot_password': False})
        gate._activate()
        gate._account_action('register')
        gate._account_action('forgot_password')
        assert gate.login_is_blocked is not allowed
        assert opened == ([prepared.authorization_url] if allowed else [])
        assert actions == []
        assert not gate.register_button.isEnabled() and not gate.forgot_password_button.isEnabled()
        if allowed:
            query = parse_qs(urlsplit(opened[0]).query)
            assert query['code_challenge_method'] == ['S256']
            assert query['state'] == [prepared.transaction.state]
            assert query['nonce'] == [prepared.transaction.nonce]
            callback = urlsplit(query['redirect_uri'][0])
            assert callback.hostname == '127.0.0.1' and callback.port == prepared.receiver.port
            assert callback.port != 18080 and prepared.transaction.expected_issuer == discovered
        assert not dialog._browser_created
    finally:
        prepared.receiver.close()
        dialog.dispose()
        gate.close()
        controller.shutdown()
        QApplication.instance().processEvents()


@pytest.mark.parametrize('kind', ['missing_current', 'corrupt_current', 'missing_state', 'corrupt_state'])
@pytest.mark.parametrize('outcome', ['success', 'bad_feed', 'cancel'])
def test_signed_recovery_projects_through_adapter_and_login_gate(recovery_case, monkeypatch, kind, outcome):
    root, service, old, feed = recovery_case
    session = SimpleNamespace(require_running=Mock(), defer=Mock(), close=Mock())
    monkeypatch.setattr('desktop.update_session.UpdateSession', lambda: session)
    # Existing UI service observes subsequent identity damage. Cold-start
    # bootstrap/classification is separately covered by the canonical suite.
    port = CoreUpdatePort(service, window_id=lambda: '000000000000002a')
    gate, controller = LoginGateWindow(), UpdateController(port=port)
    damage(root, kind)
    initial = core.get_startup_update_state(root, check_feed=False)
    before = snapshot(root)
    app_before = {p: h for p, h in before.items() if p.startswith('App/')}
    feed.raw = old.envelope if outcome != 'bad_feed' else b'{"invalid":"synthetic unsigned feed"}'
    real_repair = core.repair_installation
    calls = []
    def in_process_coordinator(path, **kwargs):
        calls.append((kwargs.pop('pid'), kwargs.pop('hwnd_hex')))
        # Only the native process handoff is removed. Real recovery admission,
        # writer lock, signatures, transaction and rollback logic still run.
        return real_repair(path, **kwargs)
    monkeypatch.setattr(core, 'repair_installation', in_process_coordinator)
    try:
        gate.bind_update_controller(controller)
        gate.set_startup_policy(initial)
        gate.set_state(LoginGateState.AUTH_READY)
        assert initial['state'] == 'REPAIR_REQUIRED' and gate.login_is_blocked
        if outcome == 'cancel':
            cancel = threading.Event()
            cancel.set()
            port.repair_and_check(lambda _: None, cancel)
            controller.refresh_login_policy()
            assert calls == [] and snapshot(root) == before
        else:
            controller.run('repair')
            until(lambda: not controller.busy and not controller._operation_pending, seconds=20)
            assert calls == [(os.getpid(), '000000000000002a')]
        if outcome == 'success':
            assert port.snapshot['state'] == 'UP_TO_DATE'
            assert not gate.login_is_blocked and gate.primary_button.isEnabled()
            assert install.installed(root).envelope == old.envelope
            assert launcher.launch(root)['health']['ready']
        else:
            assert gate.login_is_blocked and not gate.primary_button.isEnabled()
            assert {p: h for p, h in snapshot(root).items() if p.startswith('App/')} == app_before
            if outcome == 'bad_feed':
                assert port.snapshot['error_kind'] == 'validation'
        assert not gate.register_button.isEnabled() and not gate.forgot_password_button.isEnabled()
    finally:
        gate.close()
        controller.shutdown()
        QApplication.instance().processEvents()


def test_integrated_release_keeps_live_window_and_account_actions_closed():
    root = Path(__file__).resolve().parents[2]
    authority = json.loads((root / 'release/auth-authority.json').read_text())
    capabilities = json.loads((root / 'release/account-capabilities.json').read_text())
    assert authority['status'] == 'PREPARED_NOT_ACTIVE'
    assert authority['migration'] is None
    assert authority['issuer'] == NEW_ISSUER and authority['identity_issuer'] == OLD_ISSUER
    assert capabilities['account_capabilities'] == {'register': False, 'forgot_password': False}
