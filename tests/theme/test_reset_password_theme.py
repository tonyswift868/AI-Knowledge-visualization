"""Source contracts complement the real-JS/offline-FreeMarker browser harness."""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOGIN = ROOT / 'deploy/enterprise/themes/mentorflow-desktop-v1/login'


def test_reset_form_preserves_native_post_and_independent_password_fields():
    template = (LOGIN / 'login-update-password.ftl').read_text(encoding='utf-8')
    for name in ('password-new', 'password-confirm'):
        inputs = re.findall(r'<input\b[^>]*\bid="' + name + r'"[^>]*>', template)
        assert len(inputs) == 1
        assert 'type="password"' in inputs[0]
        assert 'autocomplete="new-password"' in inputs[0]
        assert 'required' in inputs[0]
        assert 'value=' not in inputs[0]
    assert 'action="${url.loginAction}" method="post"' in template
    assert '<@passwordCommons.logoutOtherSessions/>' in template
    assert 'name="cancel-aia" value="true" formnovalidate' in template
    assert 'id="kc-reset-submit" disabled' in template
    assert template.count('aria-controls="password-new"') == 1
    assert 'aria-controls="password-confirm"' not in template
    assert 'data-password-toggle' not in template
    assert 'kcSanitize(messagesPerField.get' in template
    assert 'aria-live="polite" hidden' in template


def test_local_script_has_no_policy_engine_persistence_or_password_bridge():
    script = (LOGIN / 'resources/js/password-pair.js').read_text(encoding='utf-8')
    for forbidden in ('console.', 'fetch(', 'XMLHttpRequest', 'sendBeacon', 'postMessage',
                      'localStorage', 'sessionStorage', 'cookie', 'QSettings', 'JSON.stringify',
                      'window.location', 'navigator.', '.value =', 'confirm.type =',
                      'minLength', 'maxLength', 'regexPattern', 'passwordPolicies'):
        assert forbidden not in script
    assert "addEventListener('paste'" not in script
    assert "addEventListener('copy'" not in script
    assert not (LOGIN / 'error.ftl').exists()  # Native invalid/expired/used action handling.
    assert not (LOGIN / 'info.ftl').exists()  # Native session completion, no invented success.


@pytest.mark.parametrize('locale', ['en', 'zh_CN'])
def test_human_policy_errors_are_distinct_from_matching(locale):
    messages = dict(line.split('=', 1) for line in
                    (LOGIN / f'messages/messages_{locale}.properties').read_text(encoding='utf-8').splitlines()
                    if line and not line.startswith('#'))
    mismatch = '两次输入的密码不一致，请重新确认。'
    assert messages['passwordNew'] == '新密码'
    assert messages['passwordConfirm'] == '确认新密码'
    assert messages['mentorflowPasswordMismatch'] == mismatch
    assert messages['notMatchPasswordMessage'] == mismatch
    for key in ('invalidPasswordMinLengthMessage', 'invalidPasswordMaxLengthMessage',
                'invalidPasswordNotUsernameMessage', 'invalidPasswordNotEmailMessage',
                'invalidPasswordRegexPatternMessage', 'invalidPasswordGenericMessage'):
        assert messages[key] and messages[key] != mismatch
    assert '{0}' in messages['invalidPasswordMinLengthMessage']
    assert '{0}' in messages['invalidPasswordMaxLengthMessage']
    assert messages['accountPasswordUpdatedMessage'] == '密码已重置，请使用新密码登录 MentorFlow。'
    assert 'accountUpdatedMessage' not in messages  # Don't mislabel other account operations.
    for key in ('expiredActionMessage', 'expiredActionTokenNoSessionMessage',
                'expiredActionTokenSessionExistsMessage', 'invalidCodeMessage'):
        assert '请返回 MentorFlow' in messages[key]
