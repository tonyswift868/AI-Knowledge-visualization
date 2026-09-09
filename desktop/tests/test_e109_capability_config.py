"""Config-only enablement; no SMTP, browser, credentials, installer or build."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from desktop import version, gui_update_runtime
from desktop.login_shell import LoginGateWindow
from desktop.update_controller import UpdateController
from desktop.tests.test_update_ui_flow import Port, until
from scripts import build_e108_user_candidate as build


@pytest.mark.parametrize('values', [None, {}, {'register': 'true', 'forgot_password': 1}, []])
def test_missing_or_invalid_runtime_values_fail_closed(values):
    assert version.account_capabilities({'account_capabilities': values}) == {
        'register': False, 'forgot_password': False}


def test_current_build_config_disables_both():
    assert build.load_build_account_capabilities() == {'register': False, 'forgot_password': False}


@pytest.mark.parametrize('values', [None, {}, {'register': True},
    {'register': 'true', 'forgot_password': True}, {'register': 1, 'forgot_password': False},
    {'register': True, 'forgot_password': True, 'SMTP_SECRET': 'invalid'}])
def test_invalid_build_config_is_rejected_before_build(tmp_path, values):
    path = tmp_path / 'capabilities.json'
    path.write_text(json.dumps({'schema': 'mentorflow.account-capabilities.v1',
                               'account_capabilities': values}), encoding='utf-8')
    with pytest.raises(ValueError): build.load_build_account_capabilities(path)


@pytest.mark.parametrize('enabled', [False, True])
def test_config_only_controls_packaged_buttons_after_security_gate(tmp_path, monkeypatch, enabled):
    path = tmp_path / 'capabilities.json'
    path.write_text(json.dumps({'schema': 'mentorflow.account-capabilities.v1',
        'account_capabilities': {'register': enabled, 'forgot_password': enabled}}), encoding='utf-8')
    monkeypatch.setattr(build, 'ACCOUNT_CAPABILITIES_CONFIG', path)
    manifest = build.version_document('1.5.0', {})
    monkeypatch.setattr(version, 'VERSION_MANIFEST', manifest)
    app = QApplication.instance()
    controller = UpdateController(port=Port())
    monkeypatch.setattr(app, '_mentorflow_updater', controller, raising=False)
    monkeypatch.setattr(gui_update_runtime, 'notify_health', Mock())
    monkeypatch.setattr('scripts.per_user_install.is_install', lambda _: False)
    monkeypatch.setattr('scripts.install_product.configured', lambda: SimpleNamespace(uat=False))
    gate = LoginGateWindow(); startup = SimpleNamespace(results={})
    try:
        gui_update_runtime.attach_updates(app, gate, startup, tmp_path / 'install')
        gate.show()
        assert not gate.register_button.isEnabled() and not gate.forgot_password_button.isEnabled()
        startup.results['SECURITY_GATE'] = True
        until(lambda: gate.update_entry.isEnabled())
        assert gate.register_button.isEnabled() is enabled
        assert gate.forgot_password_button.isEnabled() is enabled
        assert gate.account_capabilities == manifest['account_capabilities']
        startup.results['SECURITY_GATE'] = False
        until(lambda: not gate.register_button.isEnabled() and not gate.forgot_password_button.isEnabled())
    finally:
        gate.close(); controller.shutdown()


def test_runtime_config_cannot_grant_unknown_capabilities():
    assert version.account_capabilities({'account_capabilities': {
        'register': True, 'forgot_password': False, 'admin': True}}) == {
            'register': True, 'forgot_password': False}
