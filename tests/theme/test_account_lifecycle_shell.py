"""Future presentation contracts only; no provider activation or credentials."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
LOGIN = ROOT / 'deploy/enterprise/themes/mentorflow-desktop-v1/login'


def test_registration_fields_share_the_native_reset_password_pair():
    lifecycle = (LOGIN / 'mentorflow-account-lifecycle.ftl').read_text(encoding='utf-8')
    reset = (LOGIN / 'login-update-password.ftl').read_text(encoding='utf-8')
    for source in (lifecycle, reset):
        assert '<#import "mentorflow-password-pair.ftl" as passwordPair>' in source
    assert lifecycle.count('<@passwordPair.fields') == 2
    assert 'firstName="password" firstLabel="password" confirmLabel="mentorflowRegisterConfirm"' in lifecycle
    username = re.search(r'<input[^>]+id="mf-register-username"[^>]+>', lifecycle).group()
    assert 'required' in username
    assert all(constraint not in username for constraint in ('pattern=', 'minlength=', 'maxlength=', 'type="email"'))
    assert '账号需要唯一。' in lifecycle
    assert '必须字母开头' not in lifecycle and '必须包含数字' not in lifecycle
    assert 'id="mf-register-email" name="email" type="email"' in lifecycle
    assert '8–128 个字符，无需特定字符组合' in lifecycle


def test_future_shell_cannot_submit_network_forms_or_override_native_pages():
    source = (LOGIN / 'mentorflow-account-lifecycle.ftl').read_text(encoding='utf-8')
    forms = re.findall(r'<form\b[^>]+>', source)
    assert len(forms) == 3
    assert all('method="dialog"' in form and 'action=' not in form for form in forms)
    assert not (LOGIN / 'register.ftl').exists()
    assert not (LOGIN / 'login-reset-password.ftl').exists()
    for file in ('account-lifecycle.js', 'account-lifecycle-state.js'):
        script = (LOGIN / 'resources/js' / file).read_text(encoding='utf-8')
        for forbidden in ('fetch(', 'XMLHttpRequest', 'sendBeacon', 'postMessage',
                          'localStorage', 'sessionStorage', 'document.cookie', 'console.',
                          'QSettings', 'CredentialLocker', 'AccountVault', '.submit()',
                          'window.location', 'JSON.stringify', 'innerHTML'):
            assert forbidden not in script


def test_reset_completion_is_still_not_inferred_and_future_preferences_are_local():
    state = (LOGIN / 'resources/js/account-lifecycle-state.js').read_text(encoding='utf-8')
    assert 'minimum: 8, maximum: 128' in state
    assert 'RESET_WAITING' in state
    assert 'RESET_SUCCESS' not in state and 'AUTHENTICATED' not in state
    assert 'this.rememberPassword = false' in state and 'this.autoLogin = false' in state
    assert 'capabilities?.register === true' in state
    assert 'capabilities?.forgot_password === true' in state
    native = (ROOT / 'desktop/login_shell.py').read_text(encoding='utf-8')
    assert '记住我（下次免输密码）' in native  # Existing token-based runtime label remains.
    source = (LOGIN / 'mentorflow-account-lifecycle.ftl').read_text(encoding='utf-8')
    assert '记住密码' in source and '自动登录' in source
    assert '当前账号服务暂未开放注册' in source and '当前账号服务暂未开放密码找回' in source
    assert source.count('data-navigate="REGISTER" disabled') == 1
    assert source.count('data-navigate="FORGOT_REQUEST" disabled') == 1
