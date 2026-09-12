"""E109 source gates: no foreground, real identity server or installed repair."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QDialog, QVBoxLayout

from desktop.login_shell import LoginGateWindow, LoginGateState
from desktop.update_controller import UpdateController, UpdateState
from desktop.update_lifecycle import CoreUpdatePort
from desktop.update_panel import UpdatePanel
from desktop.tests.test_update_ui_flow import Port, until, no_external_launch
from desktop.tests.test_login_smooth import app_context, login
from desktop.tests.test_in_app_oidc import _prepared
from desktop.in_app_oidc import authorization_request_for_intent, InAppOidcNavigationPolicy
from desktop.enterprise_oidc import EnterpriseOidcError


@pytest.fixture
def prelogin(tmp_path, monkeypatch):
    from desktop import gui_update_runtime
    app = QApplication.instance(); port = Port()
    controller = UpdateController(port=port)
    monkeypatch.setattr(app, '_mentorflow_updater', controller, raising=False)
    health = Mock()
    monkeypatch.setattr(gui_update_runtime, 'notify_health', health)
    monkeypatch.setattr('scripts.per_user_install.is_install', lambda _: False)
    monkeypatch.setattr('scripts.install_product.configured', lambda: SimpleNamespace(uat=False))
    gate = LoginGateWindow()
    startup = SimpleNamespace(results={'SECURITY_GATE': True})
    gui_update_runtime.attach_updates(app, gate, startup, tmp_path/'install')
    gate.show(); until(lambda: gate.update_entry.isEnabled())
    yield gate, port, controller, startup, health
    for dialog in gate.findChildren(QDialog): dialog.close()
    gate.close(); controller.shutdown()


def test_prelogin_footer_is_direct_optional_entry_and_login_remains_enabled(prelogin):
    gate, port, controller, _, _ = prelogin
    entry = gate.update_entry
    assert entry.controller is controller and gate.update_controller is controller
    assert entry.version_label.text() == '版本 1.4.0 ·'
    assert entry.later_button.isVisible() and entry.exit_update_button.isVisible()
    assert entry.status.text() == '有新版本 1.4.1，您可以继续登录和使用。'
    assert entry.download_button.text() == '更新并重启'
    assert gate.isVisible() and gate.primary_button.isEnabled() and not gate.login_is_blocked
    requested = Mock(); gate.login_requested.connect(requested)
    gate.primary_button.click(); requested.assert_called_once()


@pytest.mark.parametrize('state,text,button', [
    ('MANDATORY_UPDATE', '此版本已停止支持，必须更新到最新版本后才能继续使用 MentorFlow。', '更新到最新版本'),
    ('REPAIR_REQUIRED', 'MentorFlow 需要修复', '修复并检查更新'),
])
def test_required_states_keep_shell_open_and_deny_login_even_after_auth_state_changes(prelogin, state, text, button):
    gate, port, controller, _, _ = prelogin
    port.snapshot['state'] = state
    gate.update_entry.refresh()
    assert gate.isVisible() and gate.login_is_blocked
    assert text in gate.update_entry.status.text()
    assert gate.update_entry.download_button.text() == button
    gate.set_state(LoginGateState.AUTH_READY)
    assert not gate.primary_button.isEnabled()
    requested = Mock(); gate.login_requested.connect(requested)
    gate._activate(); requested.assert_not_called()
    port.snapshot['state'] = 'RECOVERABLE_ERROR'; gate.update_entry.refresh()
    assert gate.login_is_blocked
    port.snapshot['state'] = 'UP_TO_DATE'; gate.update_entry.refresh()
    assert not gate.login_is_blocked and gate.primary_button.isEnabled()


def test_failed_installation_check_exposes_repair_without_unlocking_startup(prelogin):
    gate, port, controller, startup, health = prelogin
    startup.results['SECURITY_GATE'] = False
    until(lambda: gate.update_entry.property('updateState') == 'REPAIR_REQUIRED')
    assert gate.isVisible() and gate.about_button.isEnabled() and gate.login_is_blocked
    port.calls.clear(); gate.update_entry._run('check'); assert port.calls == []
    # Missing core repair support is an inline failure, never a Setup fallback.
    gate.update_entry.download_button.click(); until(lambda: not controller.busy)
    gate.update_entry.refresh()
    assert '暂时无法修复' in gate.update_entry.status.text()
    assert gate.login_is_blocked


def test_about_and_inapp_card_reuse_identical_controller_and_single_download(prelogin):
    gate, port, controller, _, _ = prelogin
    gate.about_button.click(); QApplication.instance().processEvents()
    about = gate.findChild(QDialog, 'mentorflowRealAboutUpdates')
    about_panel = about.findChild(UpdatePanel)
    assert about_panel.controller is gate.update_entry.controller
    inapp = QDialog(); layout = QVBoxLayout(inapp)
    panel = UpdatePanel(inapp, auto_delay_ms=60000); layout.addWidget(panel); inapp.show()
    try:
        QApplication.instance().processEvents()
        assert panel.controller is controller
        gate.update_entry.download_button.click(); until(port.entered.is_set)
        panel.refresh(); panel.download_button.click()
        assert port.calls.count('download') == 1
        assert panel.property('updateState') == gate.update_entry.property('updateState') or controller.busy
        port.release.set(); until(lambda: port.calls.count('apply') == 1 and not controller.busy)
        assert not hasattr(panel, 'apply_button') and not hasattr(gate.update_entry, 'apply_button')
    finally: panel.shutdown(); inapp.close()


def test_repair_request_uses_same_core_port_and_autocontinuation(prelogin):
    gate, port, controller, _, _ = prelogin
    port.snapshot['state'] = 'REPAIR_REQUIRED'
    def repair(progress, cancel):
        port.calls.append('repair')
        port.snapshot['state'] = 'PREPARING'
        return dict(port.snapshot)
    port.repair_and_check = repair
    gate.update_entry.refresh(); gate.update_entry.download_button.click()
    until(lambda: 'apply' in port.calls and not controller.busy)
    assert port.calls == ['repair', 'apply']


def test_real_logged_in_about_card_shares_prelogin_presenter(prelogin, tmp_path, monkeypatch):
    from desktop.system_page import SystemPage
    gate, port, controller, *_ = prelogin
    monkeypatch.setenv('MENTORFLOW_USER_DATA_ROOT', str(tmp_path/'data'))
    page = SystemPage({}, 'http://127.0.0.1:0', lambda: None, object())
    try:
        page.show(); QApplication.instance().processEvents()
        panel = page.update_panel
        assert panel.controller is gate.update_entry.controller is controller
        assert len(page.findChildren(UpdatePanel)) == 1
        for state, text in [('OPTIONAL_UPDATE', '有新版本 1.4.1，您可以继续登录和使用。'),
                            ('MANDATORY_UPDATE', '此版本已停止支持，必须更新到最新版本后才能继续使用 MentorFlow。'),
                            ('REPAIR_REQUIRED', 'MentorFlow 需要修复')]:
            port.snapshot['state'] = state
            panel.refresh(); gate.update_entry.refresh()
            assert text in panel.status.text()
            assert panel.property('updateState') == gate.update_entry.property('updateState')
        assert panel.heading.text() == '关于 MentorFlow 与更新'
    finally: page.close()


def test_capabilities_default_disabled_exact_boolean_and_click_guard(prelogin):
    gate, *_ = prelogin
    assert not gate.register_button.isEnabled() and not gate.forgot_password_button.isEnabled()
    register, reset = Mock(), Mock()
    gate.register_requested.connect(register); gate.forgot_password_requested.connect(reset)
    gate.set_account_capabilities({'register': 'true', 'forgot_password': 1})
    gate._account_action('register'); gate._account_action('forgot_password')
    register.assert_not_called(); reset.assert_not_called()
    gate.set_account_capabilities({'register': True, 'forgot_password': True})
    gate.register_button.click(); gate.forgot_password_button.click()
    register.assert_called_once(); reset.assert_called_once()


@pytest.mark.parametrize('intent', ['register', 'forgot_password'])
def test_supported_oidc_actions_preserve_pkce_state_nonce_and_loopback(intent):
    prepared = _prepared()
    selected = authorization_request_for_intent(prepared, intent, {intent: True})
    before = parse_qs(urlsplit(prepared.authorization_url).query)
    after = parse_qs(urlsplit(selected.authorization_url).query)
    for field in ('response_type', 'client_id', 'redirect_uri', 'scope', 'state', 'nonce', 'code_challenge', 'code_challenge_method'):
        assert after[field] == before[field]
    assert selected.transaction is prepared.transaction and selected.receiver is prepared.receiver
    assert 'login-actions' not in selected.authorization_url
    # Account-action selection and navigation must receive the same explicit
    # authority. The default policy remains fail-closed after batch01/04.
    assert not InAppOidcNavigationPolicy.from_prepared(selected).allows(selected.authorization_url)
    assert InAppOidcNavigationPolicy.from_prepared(selected, {intent: True}).allows(selected.authorization_url)
    if intent == 'register':
        assert after['prompt'] == ['create'] and urlsplit(selected.authorization_url).path.endswith('/auth')
    else:
        assert urlsplit(selected.authorization_url).path.endswith('/protocol/openid-connect/forgot-credentials')
        assert 'kc_action' not in after


@pytest.mark.parametrize('capabilities', [{}, {'register': False}, {'register': 'true'}, {'register': 1}])
def test_registration_denied_without_explicit_capability(capabilities):
    with pytest.raises(EnterpriseOidcError):
        authorization_request_for_intent(_prepared(), 'register', capabilities)


def test_account_actions_refuse_opaque_url_substitution():
    prepared = _prepared()
    for url in ('https://evil.example.test/auth', prepared.authorization_url.replace('/auth?', '/login-actions?')):
        with pytest.raises(EnterpriseOidcError):
            authorization_request_for_intent(replace(prepared, authorization_url=url), 'forgot_password', {'forgot_password': True})


@pytest.mark.parametrize('intent', ['register', 'forgot_password'])
def test_embedded_oidc_protocol_uses_selected_request_and_original_validator(intent):
    from desktop.in_app_oidc import InAppOidcLoginDialog
    from desktop.tests.test_in_app_oidc import _config
    prepared = _prepared(); events = []; selected = []
    receiver = SimpleNamespace(wait=lambda _: 'http://127.0.0.1:43210/oauth/callback?code=opaque',
                               close=lambda: events.append('closed'))
    prepared = replace(prepared, receiver=receiver)
    class Client:
        async def prepare_login(self, **kwargs):
            assert kwargs == {'force_reauthentication': True}
            return prepared
        async def complete_login(self, value, callback):
            assert value.transaction is prepared.transaction and value.receiver is receiver
            selected.append(value)
            return login()
        async def aclose(self): events.append('client-closed')
    class Pane(InAppOidcLoginDialog):
        def _show_authorization_page(self, value):
            self._prepared = value
            self._page_ready.set()  # No WebEngine page or real HTTP is opened.
    pane = Pane(_config(), embedded=True, intent=intent, capabilities={intent: True}, client_factory=lambda _: Client())
    completed = Mock(); pane.authenticated.connect(completed)
    try:
        pane.start_embedded(); until(lambda: bool(selected) and not pane._worker.is_alive())
        assert ('prompt=create' in selected[0].authorization_url) == (intent == 'register')
        assert ('/forgot-credentials?' in selected[0].authorization_url) == (intent == 'forgot_password')
        completed.assert_called_once(); assert events == ['closed', 'client-closed']
    finally: pane.dispose(); QApplication.instance().processEvents()


def test_repair_capability_delegates_and_never_falls_back_to_download():
    import threading
    service = SimpleNamespace(snapshot={'state': 'REPAIR_REQUIRED'}, current='1.4.0', apply_bridge=None,
        close=Mock(), download=Mock())
    repair = Mock(return_value={'state': 'PREPARING'})
    service.repair_and_check = repair
    port = CoreUpdatePort(service); progress = Mock(); cancel = threading.Event()
    assert port.repair_and_check(progress, cancel)['state'] == 'PREPARING'
    assert repair.call_args.kwargs['progress'] is progress
    assert repair.call_args.kwargs['cancel']() is False
    service.download.assert_not_called(); port.close()


def test_shell_paints_before_service_and_failed_trust_never_configures_network(tmp_path, monkeypatch):
    from desktop import gui_update_runtime
    app = QApplication.instance()
    monkeypatch.setattr(app, '_mentorflow_updater', None, raising=False)
    monkeypatch.setattr('scripts.per_user_install.is_install', lambda _: False)
    factory = Mock(return_value=None)
    monkeypatch.setattr('desktop.update_service.configured_mature_service', factory)
    monkeypatch.setattr('scripts.install_product.configured', lambda: SimpleNamespace(uat=False))
    health = Mock(); monkeypatch.setattr(gui_update_runtime, 'notify_health', health)
    gate = LoginGateWindow(); startup = SimpleNamespace(results={})
    gui_update_runtime.attach_updates(app, gate, startup, tmp_path/'install')
    try:
        gate.show(); QApplication.instance().processEvents()
        assert gate.isVisible() and gate.update_entry.isVisible() and not gate.update_entry.isEnabled()
        factory.assert_not_called()
        startup.results['SECURITY_GATE'] = False
        until(lambda: gate.update_entry.isEnabled())
        assert gate.update_entry.property('updateState') == 'REPAIR_REQUIRED'
        factory.assert_not_called(); health.assert_not_called()
    finally: gate.close(); gate.update_controller.shutdown()


def test_mandatory_return_without_snapshot_mutation_is_enforced(prelogin):
    gate, port, controller, *_ = prelogin
    port.check_latest = lambda: {'state': 'MANDATORY_UPDATE'}
    controller.run('check'); until(lambda: not controller.busy)
    gate.update_entry.refresh()
    assert gate.login_is_blocked and '必须更新到最新版本 1.4.1' in gate.update_entry.status.text()


@pytest.mark.parametrize('action', ['check', 'download'])
def test_pending_operation_does_not_unlock_mandatory_while_old_optional_snapshot_is_visible(prelogin, action):
    import threading
    gate, port, controller, *_ = prelogin
    port.check_latest = lambda: {'state': 'MANDATORY_UPDATE'}
    controller.run('check'); until(lambda: not controller.busy)
    gate.update_entry.refresh(); assert gate.login_is_blocked
    entered, release = threading.Event(), threading.Event()
    def recheck(*_):
        entered.set(); release.wait(3)
        return {'state': 'RECOVERABLE_ERROR'}
    port.check_latest = recheck
    port.download_latest = recheck
    try:
        controller.run(action); until(entered.is_set)
        assert gate.login_is_blocked and not gate.primary_button.isEnabled()
    finally:
        release.set(); until(lambda: not controller.busy)
    gate.update_entry.refresh(); assert gate.login_is_blocked


def test_core_returned_healthy_result_can_release_previous_mandatory_snapshot():
    service = SimpleNamespace(snapshot={'state': 'MANDATORY_UPDATE'}, current='1.4.0', apply_bridge=None,
                              check=Mock(return_value={'state': 'UP_TO_DATE'}), close=Mock())
    port = CoreUpdatePort(service)
    assert port.snapshot['state'] == 'MANDATORY_UPDATE'
    assert port.check_latest()['state'] == 'UP_TO_DATE'
    port.close()


@pytest.mark.parametrize('capability', ['missing', 'raises', 'cancelled'])
def test_repair_unavailable_failed_or_cancelled_never_falls_back(capability):
    import threading
    service = SimpleNamespace(snapshot={'state': 'REPAIR_REQUIRED'}, current='1.4.0',
                              apply_bridge=None, download=Mock(), apply_and_restart=Mock(), close=Mock())
    cancel = threading.Event()
    if capability == 'raises':
        service.repair_and_check = Mock(side_effect=RuntimeError('private path SECRET_SYNTHETIC'))
    elif capability == 'cancelled':
        def operation(**_):
            cancel.set()
            return {'state': 'PREPARING'}
        service.repair_and_check = operation
    port = CoreUpdatePort(service)
    result = port.repair_and_check(Mock(), cancel)
    assert result['state'] == 'RECOVERABLE_ERROR'
    assert 'SECRET_SYNTHETIC' not in repr(result)
    service.download.assert_not_called(); service.apply_and_restart.assert_not_called()
    port.close()


def test_mandatory_result_cancels_inflight_login_and_rejects_late_identity(app_context, monkeypatch):
    target, app, _ = app_context
    target._show_login_gate(); app.processEvents()
    gate = target.login_gate; port = Port(); controller = UpdateController(port=port)
    gate.bind_update_controller(controller)
    accepted = Mock(); monkeypatch.setattr(target._login_session, 'accept', accepted)
    try:
        before = target._login_generation
        port.snapshot['state'] = 'MANDATORY_UPDATE'; controller.refresh_login_policy()
        assert target._login_generation > before and target._auth_pane is None
        target._begin_interactive_login(); target._begin_remembered_login(); target._accept_login_result(login())
        accepted.assert_not_called()
        assert target._auth_pane is None and target.main is None
        late_bootstrap = SimpleNamespace(close=Mock())
        target._background_succeeded('bootstrap', late_bootstrap)
        late_bootstrap.close.assert_called_once()
        service_factory = Mock()
        monkeypatch.setattr('desktop.runtime.LocalService', service_factory)
        target.bootstrap_result = SimpleNamespace(profile=object())
        target._start_enterprise_local_service()
        service_factory.assert_not_called()
        target.bootstrap_result = None
        port.snapshot['state'] = 'OPTIONAL_UPDATE'; controller.refresh_login_policy()
        assert gate.primary_button.isEnabled()
    finally: controller.shutdown()


@pytest.mark.parametrize('intent', ['register', 'forgot_password'])
def test_native_account_entry_passes_intent_into_existing_embedded_flow(app_context, monkeypatch, intent):
    from desktop.tests.test_login_smooth import Pane
    target, app, _ = app_context
    captured = []
    def create(config, **options):
        captured.append(options.copy()); options.pop('intent', None); options.pop('capabilities', None)
        return Pane(config, **options)
    monkeypatch.setattr('desktop.in_app_oidc.InAppOidcLoginDialog', create)
    target._show_login_gate(); app.processEvents()
    gate = target.login_gate; gate.set_account_capabilities({intent: True})
    gate._account_action(intent); app.processEvents()
    assert captured[-1]['intent'] == intent and captured[-1]['capabilities'][intent] is True
    assert captured[-1]['embedded'] and target._auth_pane.window() is gate


def test_source_login_entry_also_uses_application_singleton(app_context):
    target, app, _ = app_context
    target._show_login_gate(); app.processEvents()
    assert target.login_gate.update_entry.controller is app._mentorflow_updater


@pytest.mark.parametrize('state', ['UP_TO_DATE', 'OPTIONAL_UPDATE', 'MANDATORY_UPDATE', 'REPAIR_REQUIRED', 'DOWNLOADING', 'PREPARING', 'RECOVERABLE_ERROR'])
def test_login_offscreen_state_evidence(prelogin, tmp_path, state):
    from PySide6.QtGui import QFontDatabase
    gate, port, *_ = prelogin
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    gate.setStyleSheet(gate.styleSheet() + '\nQWidget {font-family: "Microsoft YaHei UI";font-size: 12px;}')
    port.snapshot.update(state=state, downloaded=4*1024**2)
    gate.update_entry.refresh(); QApplication.instance().processEvents()
    assert gate.grab().save(str(tmp_path/(state+'.png')))
    entry = gate.update_entry
    assert entry.isVisible() and entry.geometry().bottom() <= gate.height()
    for widget in (entry.status, entry.check_button, entry.download_button, entry.cancel_button):
        if widget.isVisible():
            assert widget.height() >= widget.minimumSizeHint().height()
    assert gate.width() <= gate.screen().availableGeometry().width()


def test_one_adapter_and_no_update_launch_implementation_in_views():
    root = Path(__file__).parents[1]
    for path in ('login_shell.py', 'update_panel.py', 'update_controller.py'):
        source = (root/path).read_text(encoding='utf-8')
        assert 'subprocess' not in source and 'webbrowser' not in source
        assert 'login-actions/' not in source
    assert 'def repair_and_check' in (root/'update_lifecycle.py').read_text()
