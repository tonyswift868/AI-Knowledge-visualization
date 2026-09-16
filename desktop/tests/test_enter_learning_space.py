"""Isolated startup contract; no user login, desktop windows or remote service."""
import os
import time
from types import SimpleNamespace

import httpx
import pytest

from desktop.account_identity import AccountIdentity
from desktop.account_vault import AccountVaultManager, VaultLayout
from desktop.runtime import LocalService


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """The Windows organization overload otherwise uses the real registry."""
    import hashlib
    import sys
    from PySide6 import QtCore
    original = QtCore.QSettings
    class Settings(original):
        def __init__(self, *args, **kwargs):
            if len(args) == 2 and all(isinstance(value, str) for value in args):
                name = hashlib.sha256('\0'.join(args).encode()).hexdigest()
                super().__init__(str(tmp_path / (name + '.ini')), original.Format.IniFormat)
            else:
                super().__init__(*args, **kwargs)
    monkeypatch.setattr(QtCore, 'QSettings', Settings)
    for name, module in tuple(sys.modules.items()):
        if name.startswith('desktop.') and getattr(module, 'QSettings', None) is original:
            monkeypatch.setattr(module, 'QSettings', Settings)


def test_empty_account_local_backend_starts(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith('MENTORFLOW_'):
            monkeypatch.delenv(key)
    monkeypatch.setenv('MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY', '1')
    monkeypatch.setenv('MENTORFLOW_ACCOUNT_VAULT_ROOT', str(tmp_path))
    monkeypatch.setenv('MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE', '1')
    identity = AccountIdentity('https://identity.example.test', 'startup-empty', 'Synthetic')
    scope = dict(issuer=identity.issuer, subject_id=identity.subject,
                 organization_id='org-empty', workspace_id='ws-empty')
    snapshot = dict(schema='mentorflow.structured-sync.v1', complete=True,
                    snapshot_authority='POSTGRESQL_SINGLE_STATEMENT_MVCC',
                    snapshot_revision='pg-snapshot:1:1:', snapshot_scope=scope, entities=[])
    class Transport:
        _identity = identity
        context = SimpleNamespace(subject=identity.subject, organization_id='org-empty', workspace_id='ws-empty')
        def bootstrap(self):
            return SimpleNamespace(publish=lambda consume: consume(snapshot, self.context))
        def account_metadata(self):
            return httpx.Response(200, request=httpx.Request('GET', 'https://synthetic.test'))
    service = LocalService(resolved_profile=identity.profile(), product_transport=Transport())
    try:
        service.start()
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            assert service.thread.is_alive(), f'backend exited: {service.thread.exitcode}'
            try:
                response = httpx.get(service.origin + '/api/health', timeout=.5)
                if service._is_expected_health(response):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        else:
            raise AssertionError('empty account backend did not become ready')
        response = httpx.get(service.origin + '/api/workspaces', timeout=3)
        assert response.status_code == 200, response.text
        assert response.json() == []
        from desktop.backend_imports import prepare_backend_imports
        prepare_backend_imports()
        from desktop.api import DesktopApi
        from desktop.main_window import MainWindow
        from desktop.local_asset_lifecycle import LocalAssetLifecycle
        manager = AccountVaultManager(VaultLayout(tmp_path))
        vault = manager.mount(identity.profile())
        lifecycle = LocalAssetLifecycle(vault, account_identity=dict(issuer=identity.issuer, subject=identity.subject))
        api = DesktopApi(service.origin)
        window = None
        try:
            window = MainWindow(api, {'id': 'synthetic', 'username': 'synthetic', 'display_name': 'Synthetic'}, service.origin,
                                account_profile=identity.profile(), account_vault_manager=manager,
                                local_asset_lifecycle=lifecycle, rehydration_session=Transport())
            assert window.pages.count() > 0
        finally:
            if window is not None:
                window._account_switch_in_progress = True
                window.close()
                for name in ('_official_executor', '_analogy_executor', '_provider_executor', '_workspace_executor'):
                    getattr(window, name).shutdown(wait=True, cancel_futures=True)
            api.close()
            manager.unmount()
    finally:
        service.stop()
    assert service.handles_closed


def test_ui_service_handoff_on_event_loop(tmp_path, monkeypatch, desktop_qt_session):
    """Use the real background-start/ready callback, not a manual main-window call."""
    from PySide6.QtTest import QTest
    from desktop.main import MentorFlowApplication
    from desktop.login_shell import LoginGateWindow
    from desktop.local_asset_lifecycle import LocalAssetLifecycle
    from desktop.account_metadata_sync import AccountMetadataSnapshot
    from desktop.tests.test_rehydration_transport import transport, snapshot
    monkeypatch.setenv('MENTORFLOW_ACCOUNT_VAULT_ROOT', str(tmp_path))
    monkeypatch.setenv('MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE', '1')
    monkeypatch.setenv('MENTORFLOW_STARTUP_LOG', str(tmp_path / 'startup.jsonl'))
    monkeypatch.setattr(desktop_qt_session, '_mentorflow_updater', None, raising=False)
    def handler(request):
        data = snapshot()
        data.update(snapshot_authority='POSTGRESQL_SINGLE_STATEMENT_MVCC', entities=[])
        return httpx.Response(200, json=data)
    session = transport(handler)
    identity = session._identity
    manager = AccountVaultManager(VaultLayout(tmp_path))
    lifecycle = LocalAssetLifecycle(manager.mount(identity.profile()),
                                   account_identity=dict(issuer=identity.issuer, subject=identity.subject))
    target = MentorFlowApplication.__new__(MentorFlowApplication)
    target.app = desktop_qt_session
    target.login_gate = LoginGateWindow()
    target._login_generation = 0
    target._closing = False
    target._enterprise_login_required = True
    target.main = target.api = target.service = None
    target.bootstrap_result = SimpleNamespace(profile=identity.profile(), rehydration_session=session,
        vault_manager=manager, local_asset_lifecycle=lifecycle,
        snapshot=AccountMetadataSnapshot({'workspace': {'title': 'Synthetic'}}, ()),
        main_user=dict(id=identity.subject, display_name='Synthetic', username='synthetic'))
    errors = []
    monkeypatch.setattr(target, '_service_failed', errors.append)
    monkeypatch.setattr('desktop.main._prepare_desktop_frontend', lambda: None)
    try:
        target._start_enterprise_local_service()
        deadline = time.monotonic() + 35
        while target.main is None and not errors and time.monotonic() < deadline:
            QTest.qWait(25)
            time.sleep(.01)
        assert not errors, errors
        assert target.main is not None, dict(process=repr(target.service.thread),
            health=repr(target.service.health_thread), ready=httpx.get(target.service.origin+'/api/health', timeout=2).status_code)
        assert 'main_window_open' in (tmp_path / 'startup.jsonl').read_text()
    finally:
        if target.main is not None:
            target.main._account_switch_in_progress = True
            target.main.close()
            for name in ('_official_executor', '_analogy_executor', '_provider_executor', '_workspace_executor'):
                getattr(target.main, name).shutdown(wait=True, cancel_futures=True)
        if target.api is not None: target.api.close()
        if target.service is not None: target.service.stop()
        session.invalidate()
        manager.unmount()
        target.login_gate.close()


@pytest.mark.parametrize('new_device', [True, False])
def test_bootstrap_has_no_manual_recovery_gate(monkeypatch, new_device):
    from desktop.main import MentorFlowApplication
    from desktop.startup_flow import AccountBootstrapResult, DeviceBootstrapState
    from unittest.mock import Mock
    target = MentorFlowApplication.__new__(MentorFlowApplication)
    target.login_gate = None
    identity = AccountIdentity('https://identity.example.test', 'a', 'Synthetic')
    result = AccountBootstrapResult(identity, identity.profile(), {}, None, None, None, None, None,
        DeviceBootstrapState.NEW_DEVICE_OR_EMPTY_LOCAL_STATE if new_device else DeviceBootstrapState.RETURNING_DEVICE_READY,
        rehydration_result=object())
    target._start_enterprise_local_service = Mock()
    target._show_rehydration = Mock(side_effect=AssertionError('no automatic resource window'))
    target._show_device_migration = Mock(side_effect=AssertionError('no migration gate'))
    monkeypatch.setattr('desktop.runtime.bind_desktop_profile_data_root', lambda _: None)
    target._background_succeeded('bootstrap', result)
    target._start_enterprise_local_service.assert_called_once_with()
    assert target.bootstrap_result is result


def test_login_layout_and_internal_resource_entry(desktop_qt_session):
    from PySide6.QtWidgets import QLabel
    from desktop.login_shell import LoginGateWindow
    from desktop.rehydration_page import RehydrationPage
    gate = LoginGateWindow()
    page = RehydrationPage(management_only=True)
    try:
        assert not any('仅勾选记住密码' in label.text() for label in gate.findChildren(QLabel))
        assert gate.register_button.parentWidget() is gate.login_card
        assert gate.forgot_password_button.parentWidget() is gate.login_card
        actions_index = next(i for i in range(gate.content_layout.count())
            if gate.content_layout.itemAt(i).layout() is not None
            and gate.content_layout.itemAt(i).layout().indexOf(gate.register_button) >= 0)
        assert actions_index < gate.content_layout.indexOf(gate.primary_button)
        assert page.enter.text() == '关闭' and page.enter.isEnabled()
        assert page.portable_pack.isHidden()
        assert page.model_cache_button.text() == '手动指定已有模型'
        assert callable(page._resource_action)  # Existing import/download handlers retained.
    finally:
        page.close()
        gate.close()


def test_login_update_summary_hides_retry_but_keeps_required_gate(desktop_qt_session):
    from desktop.update_panel import UpdatePanel
    from desktop.update_controller import UpdateController
    from desktop.tests.test_update_ui_flow import Port
    port = Port()
    controller = UpdateController(port=port)
    panel = UpdatePanel(controller=controller, compact=True, login_summary=True, auto_delay_ms=60000)
    try:
        port.snapshot.update(state='RECOVERABLE_ERROR', error_kind='network', has_update=False)
        panel.refresh()
        assert panel.check_button.isHidden() and panel.status.isHidden()
        port.snapshot.update(state='MANDATORY_UPDATE', latest='9.0.0', has_update=True)
        panel.refresh()
        assert controller.login_blocked
        assert not panel.status.isHidden() and not panel.download_button.isHidden()
    finally:
        panel.shutdown()
        controller.shutdown()
        panel.close()


def test_startup_diagnostic_redacts_exception_content(tmp_path, monkeypatch):
    from desktop.startup_diagnostics import record
    path = tmp_path / 'startup.jsonl'
    monkeypatch.setenv('MENTORFLOW_STARTUP_LOG', str(path))
    try:
        raise RuntimeError('synthetic-password private-user-data')
    except RuntimeError as exc:
        record('main_window_initialize', exc)
    value = path.read_text()
    assert 'RuntimeError' in value and 'test_startup_diagnostic_redacts_exception_content' in value
    assert 'synthetic-password' not in value and 'private-user-data' not in value
