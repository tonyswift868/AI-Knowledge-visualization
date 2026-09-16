"""Exact 01 HTTP contract through the real session transport, synthetic only."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from desktop.enterprise_oidc import EnterpriseOidcError
from desktop.lifecycle_client import AccountSecurityClient
from desktop.rehydration_transport import RehydrationTransportError, invalidate_active
from desktop.tests.test_rehydration_transport import transport
from desktop.tests.test_account_security_r2 import page, finish

PROFILE = dict(email='fixture@example.invalid', email_verified=False, can_bind=False, can_verify=True)
SENT = dict(state='CODE_SENT', transaction_id='t'*43, expires_in=600, resend_after=60)


@pytest.fixture(autouse=True)
def fence():
    invalidate_active()
    yield
    invalidate_active()


def test_exact_authenticated_email_contract_and_leading_zero_code():
    calls = []
    def handler(request):
        assert request.headers['authorization'].startswith('Bearer mfn1_a_')
        assert request.headers['x-mentorflow-organization-id'] == 'org-a'
        assert request.headers['x-mentorflow-workspace-id'] == 'ws-a'
        assert 'cookie' not in request.headers and 'origin' not in request.headers
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path.endswith('/email'): return httpx.Response(200, json=PROFILE)
        if request.url.path.endswith('/request'):
            assert body == {'email': PROFILE['email']}
            return httpx.Response(202, json=SENT)
        assert body == {'transaction_id':'t'*43, 'code':'001234'}
        return httpx.Response(200, json=dict(PROFILE, state='EMAIL_VERIFIED', email_verified=True, can_verify=False))
    client = AccountSecurityClient(transport(handler))
    assert client.read() == PROFILE
    assert client.request_code(PROFILE['email']) == dict(expires_in=600, resend_after=60)
    assert client.verify_code('001234')['email_verified']
    assert client._transaction is None and len(calls) == 3


@pytest.mark.parametrize('nested', [False, True])
def test_both_error_envelopes_preserve_same_password_feedback(nested):
    value = dict(code='NEW_PASSWORD_SAME_AS_CURRENT', message='新密码不能与当前密码相同')
    client = AccountSecurityClient(transport(lambda _: httpx.Response(400, json={'error':value} if nested else value)))
    with pytest.raises(EnterpriseOidcError) as exc: client.change_password('fixture-old', 'fixture-old')
    assert exc.value.code == 'NEW_PASSWORD_SAME_AS_CURRENT'
    assert str(exc.value) == '新密码不能与当前密码相同'


@pytest.mark.parametrize('failure', ['timeout', 'unauthorized'])
def test_mutation_not_replayed_on_uncertainty_or_401(failure):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if failure == 'timeout': raise httpx.ReadTimeout('fixture timeout')
        return httpx.Response(401, json={'error':{'code':'SESSION_EXPIRED'}})
    client = AccountSecurityClient(transport(handler))
    with pytest.raises(EnterpriseOidcError): client.request_code(PROFILE['email'])
    assert calls == ['/api/enterprise/auth/lifecycle/email-code/request']
    assert client._transaction is None


def test_expiry_resend_delay_cancel_and_no_code_transmission_after_expiry():
    clock = [10.0]; calls = []
    client = AccountSecurityClient(transport(lambda request: calls.append(request.url.path) or httpx.Response(202, json=SENT)), clock=lambda:clock[0])
    client.request_code(PROFILE['email'])
    with pytest.raises(EnterpriseOidcError): client.request_code(PROFILE['email'])
    clock[0] += 601
    with pytest.raises(EnterpriseOidcError): client.verify_code('001234')
    assert len(calls) == 1
    client.cancel()
    assert client._transaction is None


def test_cancel_during_send_cannot_resurrect_transaction():
    entered, release = threading.Event(), threading.Event()
    def handler(_):
        entered.set(); assert release.wait(2)
        return httpx.Response(202, json=SENT)
    client = AccountSecurityClient(transport(handler))
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(client.request_code, PROFILE['email'])
        assert entered.wait(2)
        client.cancel(); release.set()
        with pytest.raises(EnterpriseOidcError): job.result(timeout=2)
    assert client._transaction is None


def test_scope_override_and_wrong_session_are_rejected_before_mail_request():
    calls = []
    first = transport(lambda req: calls.append(req) or httpx.Response(200, json=PROFILE))
    with pytest.raises(RehydrationTransportError):
        first.post('/api/enterprise/auth/lifecycle/email-code/request', json={'email':'a@b.invalid', 'subject':'other'})
    transport(subject='b')
    with pytest.raises(EnterpriseOidcError): AccountSecurityClient(first).request_code(PROFILE['email'])
    assert not calls


def test_ui_empty_bound_and_verified_states_and_post_verify_reread(monkeypatch):
    state = dict(email=None, email_verified=False, can_bind=True, can_verify=False)
    reads = []
    def handler(request):
        if request.method == 'GET': reads.append(True); return httpx.Response(200, json=state)
        if request.url.path.endswith('/request'): return httpx.Response(202, json=SENT)
        state.update(PROFILE, email_verified=True, can_verify=False)
        return httpx.Response(200, json=dict(state, state='EMAIL_VERIFIED'))
    client = AccountSecurityClient(transport(handler))
    view = page(monkeypatch, read_account_security=client.read, request_email_code=client.request_code,
                verify_email_code=client.verify_code, cancel_email_code=client.cancel)
    try:
        view.refresh_security(); finish(view)
        assert not view.email_address.isReadOnly() and '尚未绑定' in view.email_status.text()
        view.email_address.setText(PROFILE['email']); view._request_email_code(); finish(view)
        assert '请求已提交' in view.security_status.text() and not view.send_email_code_button.isEnabled()
        view.email_code.setText('001234'); view._verify_email_code(); finish(view); finish(view)
        assert len(reads) == 2 and '已验证' in view.email_status.text()
        assert view.email_address.isHidden() and not view.send_email_code_button.isEnabled()
    finally: view.close()


def test_late_profile_response_does_not_update_switched_page(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def read():
        entered.set(); assert release.wait(2)
        return PROFILE
    cancelled = []
    view = page(monkeypatch, read_account_security=read, cancel_email_code=lambda:cancelled.append(True))
    try:
        view.refresh_security(); assert entered.wait(2)
        view.set_profile('different-profile')
        release.set(); finish(view)
        assert PROFILE['email'] not in view.email_status.text()
        assert view.read_account_security is None and cancelled
    finally: release.set(); view.close()


def test_close_drops_transaction_and_late_form_result(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def handler(request):
        if request.method == 'GET': return httpx.Response(200, json=PROFILE)
        entered.set(); assert release.wait(2)
        return httpx.Response(202, json=SENT)
    client = AccountSecurityClient(transport(handler))
    view = page(monkeypatch, read_account_security=client.read, request_email_code=client.request_code,
                verify_email_code=client.verify_code, cancel_email_code=client.cancel)
    view.refresh_security(); finish(view)
    view._request_email_code(); assert entered.wait(2)
    view.close(); release.set()
    try: view._security_future.result(timeout=2)
    except EnterpriseOidcError: pass
    view._finish_security()
    assert client._transaction is None and not view._email_code_requested
    assert not view._email_timer.isActive() and not view.email_code.text()


def test_invalid_server_success_never_presents_email_verification():
    responses = iter([httpx.Response(202, json=SENT), httpx.Response(200, json=dict(PROFILE, state='EMAIL_VERIFIED'))])
    client = AccountSecurityClient(transport(lambda _: next(responses)))
    client.request_code(PROFILE['email'])
    with pytest.raises(EnterpriseOidcError): client.verify_code('001234')
