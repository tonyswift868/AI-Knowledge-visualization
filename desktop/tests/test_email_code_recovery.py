"""Actual local WebEngine pane/channel and HTTP client, isolated synthetic service."""
import json
import threading
import time

import httpx
import pytest
from PySide6.QtTest import QTest
from PySide6.QtCore import QUrl
from pathlib import Path

from desktop.enterprise_oidc import DesktopOidcConfig
from desktop.lifecycle_client import LifecycleClient
from desktop.lifecycle_pane import AccountLifecyclePane, assets
from desktop.tests.test_login_smooth import app_context, login, ISSUER, API
from desktop.tests.test_multi_account import Passwords, add


def wait(predicate):
    end = time.monotonic() + 12
    while time.monotonic() < end:
        QTest.qWait(10)
        time.sleep(.01)
        if predicate(): return
    assert predicate()


def js(pane, expression):
    values = []
    pane.page.runJavaScript(expression, values.append)
    wait(lambda: bool(values))
    return values[0]


def fill(pane, selector, value):
    js(pane, f"(() => {{const e=document.querySelector({json.dumps(selector)});e.value={json.dumps(value)};e.dispatchEvent(new Event('input',{{bubbles:true}}));return true;}})()")


def click(pane, selector):
    assert js(pane, f"!document.querySelector({json.dumps(selector)}).disabled")
    js(pane, f"document.querySelector({json.dumps(selector)}).click()")


def screen(pane):
    return js(pane, "document.querySelector('section[data-screen]:not([hidden])')?.dataset.screen || ''")


@pytest.fixture
def recovery(app_context, monkeypatch):
    app, qt, settings = app_context
    monkeypatch.setattr(app, '_load_account_capabilities', lambda: None)
    monkeypatch.setattr(DesktopOidcConfig, 'from_env', staticmethod(lambda: DesktopOidcConfig(ISSUER, 'mentorflow-desktop', 'mentorflow-api', API)))
    requests = []
    state = {'complete_error': None, 'block': None}

    def service(request):
        assert not any(h in request.headers for h in ('authorization', 'cookie', 'origin'))
        assert request.url.scheme == 'https' and request.method == 'POST'
        operation = request.url.path.rsplit('/', 1)[-1]
        payload = json.loads(request.content)
        requests.append(operation)  # Never retain password/code payload in evidence.
        if state['block']: state['block'].wait(5)
        if operation == 'request':
            assert payload == {'email': 'synthetic@example.test'}
            return httpx.Response(202, json=dict(state='CODE_SENT', transaction_id='transaction-fixture', expires_in=600, resend_after=60, message='generic'))
        if operation == 'verify':
            assert payload['transaction_id'] == 'transaction-fixture'
            if payload['code'] != '123456':
                return httpx.Response(400, json={'code': 'RESET_CODE_INVALID_OR_EXPIRED', 'message': 'untrusted'})
            return httpx.Response(200, json=dict(state='VERIFIED', reset_proof='proof-fixture', expires_in=300))
        assert operation == 'complete' and payload['reset_proof'] == 'proof-fixture'
        assert payload['new_password'] == payload['confirm_password']
        if state['complete_error'] == 'network':
            raise httpx.ReadError('private response', request=request)
        if state['complete_error']:
            return httpx.Response(400, json={'code': state['complete_error'], 'message': 'untrusted'})
        return httpx.Response(200, json=dict(state='COMPLETED', account_id=login('a').identity.account_id, message='done'))

    original = LifecycleClient.__init__
    monkeypatch.setattr(LifecycleClient, '__init__', lambda self, config: original(self, config,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(service),
            headers={'Authorization': 'synthetic', 'Origin': 'https://example.test'}, cookies={'session': 'synthetic'})))
    app._login_session.passwords = Passwords()
    a = add(app._login_session, 'a', True)
    b = add(app._login_session, 'b', True)
    app._show_login_gate()
    app.login_gate.set_account_capabilities({'register': False, 'forgot_password': True})
    app._begin_account_action('forgot_password')
    pane = app._auth_pane
    assert isinstance(pane, AccountLifecyclePane), 'Render the lifecycle assets before running this UI test'
    wait(lambda: 'FORGOT_REQUEST' in screen(pane))
    yield app, pane, requests, state, a, b, settings
    if state['block']: state['block'].set()
    if app._auth_pane is not None: app._cancel_login()
    qt.processEvents()


def request_code(pane):
    fill(pane, '#mf-forgot-identity', 'synthetic@example.test')
    click(pane, '#mf-forgot-submit')
    wait(lambda: pane.bridge.stage == 'code' and not pane.bridge.busy)
    wait(lambda: 'FORGOT_SENT' in screen(pane))
    assert js(pane, "document.querySelector('#mf-reset-form').closest('section').hidden")


def verify(pane):
    fill(pane, '#mf-reset-code', '123456')
    click(pane, '#mf-code-submit')
    wait(lambda: pane.bridge.stage == 'password' and 'RESET_PASSWORD' in screen(pane))
    assert pane.bridge.transaction_id is None


def complete(pane, password='new synthetic password'):
    fill(pane, '#mf-reset-form [data-mf-new-password]', password)
    fill(pane, '#mf-reset-form [data-mf-confirm-password]', password)
    click(pane, '#mf-reset-form [data-mf-pair-submit]')


def test_same_window_success_clears_only_target_credentials(recovery):
    app, pane, requests, state, a, b, settings = recovery
    window = app.login_gate
    events = []; navigation = []; popups = []
    pane.page.urlChanged.connect(lambda url: navigation.append(url.toString()))
    pane.page.newWindowRequested.connect(lambda request: popups.append(True))
    pane.bridge.result.connect(lambda raw: events.append(raw))
    meta = pane.bridge.metaObject()
    exposed = [bytes(meta.method(i).methodSignature()).decode() for i in range(meta.methodOffset(), meta.methodCount())]
    assert all(name not in signature for signature in exposed for name in ('_finish', '_done', 'ready'))
    assert pane.window() is window
    request_code(pane)
    verify(pane)
    assert not pane.page.acceptNavigationRequest(QUrl('https://example.test'), 0, True)
    assert Path(pane.page.url().toLocalFile()).resolve() == (assets() / 'lifecycle.html').resolve()
    complete(pane)
    wait(lambda: app._auth_pane is None)
    assert app.login_gate is window and app.main is None
    assert requests == ['request', 'verify', 'complete']
    assert not navigation and not popups
    assert a.account_id not in app._login_session.passwords.values
    assert b.account_id in app._login_session.passwords.values
    assert app._login_session.registry.get(a.account_id) is not None
    assert not app._login_session.registry.get(a.account_id)['auto_login']
    assert app._login_session.credentials.key(a) not in app._login_session.credentials.values
    assert app._login_session.credentials.key(b) in app._login_session.credentials.values
    assert not window.password_field.text() and app._last_login_result is None
    assert '密码已重置' in window.state_detail.text()
    serialized = json.dumps(settings.values) + ''.join(events)
    assert all(secret not in serialized for secret in ('transaction-fixture', 'proof-fixture', 'new synthetic password', '123456'))


def test_wrong_code_resend_and_expiry(recovery):
    app, pane, requests, state, *_ = recovery
    request_code(pane)
    assert js(pane, "document.querySelector('#mf-code-resend').disabled")
    fill(pane, '#mf-reset-code', '000000')
    click(pane, '#mf-code-submit')
    wait(lambda: not pane.bridge.busy and pane.bridge.stage == 'expired')
    wait(lambda: '错误或已过期' in js(pane, "document.querySelector('#mf-code-status').textContent"))
    assert pane.bridge.transaction_id is None
    pane.bridge.resend_deadline = time.monotonic() - 1
    pane.bridge._publish('expired', code='RESET_CODE_INVALID_OR_EXPIRED')
    wait(lambda: not js(pane, "document.querySelector('#mf-code-resend').disabled"))
    click(pane, '#mf-code-resend')
    wait(lambda: pane.bridge.stage == 'code')
    verify(pane)
    pane.bridge.deadline = time.monotonic() - 1
    pane.bridge._expire()
    wait(lambda: js(pane, "document.querySelector('#mf-reset-form [data-mf-pair-submit]').disabled"))
    assert pane.bridge.reset_proof is None
    click(pane, '#mf-reset-restart')
    wait(lambda: 'FORGOT_REQUEST' in screen(pane))
    assert requests == ['request', 'verify', 'request', 'verify']


@pytest.mark.parametrize('error', ['NEW_PASSWORD_SAME_AS_CURRENT', 'PASSWORD_POLICY_REJECTED', 'PASSWORD_CONFIRMATION_MISMATCH'])
def test_password_error_allows_correction_with_original_proof(recovery, error):
    app, pane, requests, state, *_ = recovery
    request_code(pane); verify(pane)
    deadline = pane.bridge.deadline
    state['complete_error'] = error
    complete(pane, 'current synthetic password')
    wait(lambda: not pane.bridge.busy and 'RESET_PASSWORD' in screen(pane))
    assert pane.bridge.reset_proof == 'proof-fixture' and pane.bridge.deadline == deadline
    if error == 'NEW_PASSWORD_SAME_AS_CURRENT':
        assert js(pane, "document.querySelector('#mf-reset-status').textContent") == '新密码不能与当前密码相同'
    assert js(pane, "document.querySelector('#mf-reset-form [data-mf-confirm-password]').type") == 'password'
    state['complete_error'] = None
    complete(pane)
    wait(lambda: app._auth_pane is None)
    assert requests == ['request', 'verify', 'complete', 'complete']


def test_uncertain_completion_no_replay_and_cancel_no_bleed(recovery):
    app, pane, requests, state, *_ = recovery
    request_code(pane); verify(pane)
    state['complete_error'] = 'network'
    complete(pane)
    wait(lambda: not pane.bridge.busy and pane.bridge.stage == 'expired')
    wait(lambda: '无法确认' in js(pane, "document.querySelector('#mf-reset-status').textContent"))
    assert pane.bridge.reset_proof is None and requests.count('complete') == 1
    pane.bridge.returnLogin()
    app._begin_account_action('forgot_password')
    fresh = app._auth_pane
    wait(lambda: 'FORGOT_REQUEST' in screen(fresh))
    assert fresh.bridge.reset_proof is None and fresh.bridge.transaction_id is None
    state['block'] = threading.Event()
    fill(fresh, '#mf-forgot-identity', 'synthetic@example.test')
    click(fresh, '#mf-forgot-submit')
    wait(lambda: fresh.bridge.busy)
    bridge = fresh.bridge
    bridge.returnLogin()
    state['block'].set()
    assert bridge.cancelled.is_set() and bridge.transaction_id is None
    assert app._auth_pane is None and app._last_login_result is None
