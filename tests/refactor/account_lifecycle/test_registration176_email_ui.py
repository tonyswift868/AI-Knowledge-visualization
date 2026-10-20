import asyncio
import base64
import json
from pathlib import Path
import shutil
import subprocess

import httpx
import pytest

from refactor.desktop.account_protocol import AccountConfig, AccountError
from refactor.desktop.lifecycle_client import LifecycleClient


ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / 'refactor' / 'desktop' / 'resources' / 'account-lifecycle'
CONFIG = AccountConfig('https://identity.invalid/realms/test', 'mentorflow-desktop', 'mentorflow-api', 'https://api.invalid')


def _run_node(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not available')
    subprocess.run([node, '--input-type=module', '-e', script], check=True, capture_output=True, text=True)


def test_email_is_not_checked_or_shown_as_available_but_username_check_remains():
    state = ASSETS / 'resources' / 'js' / 'account-lifecycle-state.js'
    view = ASSETS / 'resources' / 'js' / 'account-lifecycle.js'
    html = ASSETS / 'lifecycle.html'
    state_uri = 'data:text/javascript;base64,' + base64.b64encode(state.read_bytes()).decode()
    view_source = view.read_text(encoding='utf-8')
    html_source = html.read_text(encoding='utf-8')
    assert "scheduleRegistrationCheck('username'" in view_source
    assert "scheduleRegistrationCheck('email'" not in view_source
    assert '邮箱可用' not in view_source
    assert 'mf-email-availability' not in html_source
    assert '如果这个邮箱可以使用，我们已发送验证邮件，请查收。' in view_source
    assert '收到验证码时，请在此输入邮件中的 6 位验证码，10 分钟内有效；若邮件提示邮箱已有账号，可返回登录或找回密码。' in view_source

    script = f"""
import assert from 'node:assert/strict';
const {{AccountLifecycleState}} = await import({json.dumps(state_uri)});
const m = new AccountLifecycleState({{capabilities: {{register: true}}}});
assert.deepEqual(Object.keys(m.registrationAvailability), ['username']);
m.navigate('REGISTER');
const usernameCheck = m.beginRegistrationCheck('username', 'fresh-name');
assert.equal(typeof usernameCheck, 'number');
m.registrationCheckResult('username', {{requestId: usernameCheck, available: true}});
assert.equal(m.registrationAvailability.username.state, 'available');
assert.equal(m.beginRegistrationCheck('email', 'address@example.invalid'), null);
assert.equal(m.registrationCheckResult('email', {{requestId: 1, available: false}}), false);
m.emailEdited();
assert.equal(m.registrationAvailability.email, undefined);
const request = m.requestRegistration();
assert.equal(request, true);
assert.equal(m.registrationResult('verification_pending', {{requestId: m.requestId, email: 'address@example.invalid'}}), true);
assert.equal(m.screen, 'VERIFY_PENDING');
assert.equal(m.maskedEmail, 'a****ss@example.invalid');

const legacy = new AccountLifecycleState({{capabilities: {{register: true}}}});
legacy.navigate('REGISTER'); legacy.requestRegistration();
legacy.registrationResult('email_unavailable', {{requestId: legacy.requestId,
    message: '暂时无法开始验证，请核对填写信息后重试。若邮箱可以使用，我们会发送验证邮件。'}});
assert.equal(legacy.registrationError, 'EMAIL_SUBMISSION_UNAVAILABLE');
assert.ok(!legacy.registrationMessage.includes('已被使用'));
assert.equal(legacy.registrationAvailability.email, undefined);
const username = new AccountLifecycleState({{capabilities: {{register: true}}}});
username.navigate('REGISTER'); username.requestRegistration();
username.registrationResult('username_taken', {{requestId: username.requestId}});
assert.equal(username.registrationError, 'USERNAME_TAKEN');
"""
    _run_node(script)


@pytest.mark.parametrize('code', ['EMAIL_ALREADY_IN_USE', 'ACCOUNT_ALREADY_IN_USE'])
def test_legacy_server_email_conflict_is_sanitized_without_faking_success(code):
    async def run():
        async def handler(request):
            return httpx.Response(409, json={'code': code, 'message': 'account exists'})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = LifecycleClient(CONFIG, http_client=http)
            with pytest.raises(AccountError) as caught:
                await client.registration_code('start', {
                    'username': 'local-test-user', 'password': 'not-a-real-password',
                    'email': 'local-test@example.invalid',
                })
            assert caught.value.code == code
            assert '已被使用' not in str(caught.value)
            assert '已注册' not in str(caught.value)
            assert '暂时无法开始验证' in str(caught.value)

    asyncio.run(run())


def test_current_registration_contract_remains_accepted():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(201, json={
            'state': 'CODE_SENT', 'credential': 't' * 43, 'expires_in': 600, 'resend_after': 60,
        }))) as http:
            client = LifecycleClient(CONFIG, http_client=http)
            result = await client.registration_code('start', {
                'username': 'local-test-user', 'password': 'not-a-real-password',
                'email': 'local-test@example.invalid',
            })
            assert result == {'state': 'CODE_SENT', 'credential': 't' * 43, 'expires_in': 600, 'resend_after': 60}

    asyncio.run(run())
