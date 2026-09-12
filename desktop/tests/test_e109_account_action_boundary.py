"""Offline regression: disabled account actions cannot reopen inside login."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from desktop.enterprise_oidc import EnterpriseOidcError
from desktop.in_app_oidc import (
    InAppOidcLoginDialog, InAppOidcNavigationPolicy,
    authorization_request_for_intent,
)
from desktop.login_shell import LoginGateWindow
from desktop.tests.test_in_app_oidc import _config, _prepared


PROVIDER = 'https://identity.example.test/realms/mentorflow/'
ACTIONS = [
    ('register', 'protocol/openid-connect/auth?prompt=create'),
    ('register', 'protocol/openid-connect/auth?prompt=login&prompt=create'),
    ('register', 'protocol/openid-connect/auth?prompt=login%20create'),
    ('register', 'protocol/openid-connect/registrations'),
    ('register', 'login-actions/registration'),
    ('register', 'login-actions/%2572egistration'),
    ('register', 'login-actions/registration;ignored=value'),
    ('forgot_password', 'protocol/openid-connect/forgot-credentials'),
    ('forgot_password', 'login-actions/reset-credentials'),
    ('forgot_password', 'login-actions/%72eset-credentials'),
]


@pytest.mark.parametrize('action,path', ACTIONS)
@pytest.mark.parametrize('value', [None, False, 1, 'true'])
def test_provider_links_respect_each_disabled_capability(action, path, value):
    capabilities = {'register': True, 'forgot_password': True, action: value}
    policy = InAppOidcNavigationPolicy.from_prepared(_prepared(), capabilities)
    assert not policy.allows(PROVIDER + path)


@pytest.mark.parametrize('action,path', ACTIONS)
def test_future_explicit_capability_allows_only_its_own_action(action, path):
    policy = InAppOidcNavigationPolicy.from_prepared(_prepared(), {action: True})
    assert policy.allows(PROVIDER + path)
    assert not policy.allows('https://foreign.example.test/' + path)


@pytest.mark.parametrize('path', [
    'protocol/openid-connect/auth?prompt=login',
    'login-actions/authenticate', 'login-actions/required-action?execution=UPDATE_PASSWORD',
    'login-actions/required-action?execution=VERIFY_EMAIL', 'login-actions/restart',
])
def test_disabled_optional_actions_preserve_existing_account_login(path):
    assert InAppOidcNavigationPolicy.from_prepared(_prepared()).allows(PROVIDER + path)


@pytest.mark.parametrize('action', ['register', 'forgot_password'])
@pytest.mark.parametrize('capabilities', [None, {}, [], {'register': 1, 'forgot_password': 'true'}])
def test_direct_intent_invocation_cannot_bypass_capabilities(action, capabilities):
    with pytest.raises(EnterpriseOidcError) as error:
        authorization_request_for_intent(_prepared(), action, capabilities)
    assert error.value.code == 'ENTERPRISE_ACCOUNT_ACTION_UNAVAILABLE'


def test_direct_signal_entry_cannot_bypass_disabled_buttons():
    gate = LoginGateWindow()
    registered, recovered = Mock(), Mock()
    gate.register_requested.connect(registered)
    gate.forgot_password_requested.connect(recovered)
    try:
        gate.set_account_capabilities({'register': False, 'forgot_password': False})
        gate._account_action('register')
        gate._account_action('forgot_password')
        registered.assert_not_called()
        recovered.assert_not_called()
    finally:
        gate.close()


@pytest.mark.parametrize('enabled', [False, True])
def test_dialog_installs_capability_policy_for_both_request_and_navigation(monkeypatch, enabled):
    dialog = InAppOidcLoginDialog(_config(), capabilities={
        'register': enabled, 'forgot_password': enabled})
    interceptor = SimpleNamespace(policy=None)
    view = SimpleNamespace(setUrl=Mock())
    monkeypatch.setattr(dialog, '_ensure_browser', lambda: None)
    dialog._interceptor, dialog.view = interceptor, view
    try:
        dialog._show_authorization_page(_prepared())
        assert interceptor.policy is not None
        assert interceptor.policy.allows(PROVIDER + 'login-actions/reset-credentials') is enabled
        assert interceptor.policy.allows(PROVIDER + 'login-actions/registration') is enabled
        view.setUrl.assert_called_once()
    finally:
        # No browser or receiver was allocated in this offline wiring test.
        dialog._prepared = None
        dialog.view = None
        dialog._interceptor = None
        dialog.close()
