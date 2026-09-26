"""Offline private administration contract; no SMTP or real identity claims."""
import asyncio
import json

import httpx
import pytest

from app.enterprise.identity.account_lifecycle import KeycloakAccountAdmin, LifecycleError

ORIGIN = 'https://identity.example.test'
BASE = ORIGIN + '/admin/realms/fixture'


def exercise(handler, operation):
    async def run():
        async def resolve():
            return 'synthetic-admin-authority'
        admin = KeycloakAccountAdmin(ORIGIN, 'fixture', resolve,
                                    transport=httpx.MockTransport(handler))
        try:
            return await operation(admin)
        finally:
            await admin.aclose()
    return asyncio.run(run())


@pytest.mark.parametrize('password', ['a' * 8, 'a' * 128, '无需任何字符组合规则'])
def test_registration_uses_official_create_and_unverified_permanent_password(password):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == 'POST' and str(request.url) == BASE + '/users'
        assert json.loads(request.content) == {
            'username': 'chosen-username', 'email': 'disposable@example.invalid',
            'enabled': True, 'emailVerified': False, 'requiredActions': ['VERIFY_EMAIL'],
            'credentials': [{'type': 'password', 'value': password, 'temporary': False}],
        }
        return httpx.Response(201, headers={'location': BASE + '/users/test-subject'})
    assert exercise(handler, lambda a: a.create_account(username='chosen-username',
        password=password, email='disposable@example.invalid')) == 'test-subject'
    assert len(calls) == 1  # No implicit email send, token grant or role elevation.


def test_same_password_is_allowed_for_distinct_synthetic_users():
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(201, headers={
            'location': BASE + '/users/' + payload['username'],
        })

    async def operation(admin):
        first = await admin.create_account(username='first-user',
                                           password='same-password',
                                           email='first@example.invalid')
        second = await admin.create_account(username='second-user',
                                            password='same-password',
                                            email='second@example.invalid')
        return first, second

    assert exercise(handler, operation) == ('first-user', 'second-user')
    assert [payload['credentials'][0]['value'] for payload in calls] == [
        'same-password', 'same-password',
    ]
    assert [payload['username'] for payload in calls] == ['first-user', 'second-user']


@pytest.mark.parametrize('password', ['', 'a' * 7, 'a' * 129, None])
def test_invalid_password_has_no_network_side_effect(password):
    def forbidden(request):
        pytest.fail('Network should not be used')
    with pytest.raises(LifecycleError, match='REGISTRATION_INPUT_INVALID'):
        exercise(forbidden, lambda a: a.create_account(username='chosen', password=password,
                                                       email='disposable@example.invalid'))


@pytest.mark.parametrize('method,path,body', [
    ('send_verification', '/send-verify-email', None),
    ('send_password_reset', '/execute-actions-email', ['UPDATE_PASSWORD']),
])
def test_official_email_methods_do_not_create_or_return_action_tokens(method, path, body):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == 'PUT'
        assert request.headers['content-type'] == 'application/json'
        assert request.url.path == '/admin/realms/fixture/users/test-subject' + path
        assert dict(request.url.params) == {'lifespan': '600'}
        assert (json.loads(request.content) if request.content else None) == body
        return httpx.Response(204)
    assert exercise(handler, lambda a: getattr(a, method)('test-subject')) is None
    assert len(calls) == 1


def test_change_password_uses_official_reset_credentials_and_clears_payload():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == 'PUT'
        assert request.url.path == '/admin/realms/fixture/users/test-subject/reset-password'
        assert json.loads(request.content) == {
            'type': 'password', 'value': 'new-password', 'temporary': False,
        }
        return httpx.Response(204)
    assert exercise(handler, lambda a: a.change_password('test-subject', 'new-password')) is None
    assert len(calls) == 1


@pytest.mark.parametrize('password', ['', 'a' * 7, 'a' * 129, None])
def test_change_password_rejects_invalid_length_without_network(password):
    with pytest.raises(LifecycleError, match='PASSWORD_INPUT_INVALID'):
        exercise(lambda r: pytest.fail('Network should not be used'),
                 lambda a: a.change_password('test-subject', password))


@pytest.mark.parametrize('response', [
    httpx.Response(302, headers={'location': 'https://foreign.example.test'}),
    httpx.Response(400, text='sensitive-provider-input'),
    httpx.Response(403, text='sensitive-provider-input'),
    httpx.Response(500, text='sensitive-provider-input'),
])
def test_redirects_and_provider_details_never_escape(response):
    calls = []
    def handler(request):
        calls.append(request)
        return response
    with pytest.raises(LifecycleError) as caught:
        exercise(handler, lambda a: a.send_verification('test-subject'))
    assert str(caught.value) == 'KEYCLOAK_OPERATION_REJECTED'
    assert len(calls) == 1


def test_timeout_after_commit_is_unknown_and_never_automatically_retried():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('sensitive-provider-input')
    with pytest.raises(LifecycleError, match='KEYCLOAK_OUTCOME_UNKNOWN'):
        exercise(handler, lambda a: a.send_password_reset('test-subject'))
    assert len(calls) == 1


@pytest.mark.parametrize(
    ('username_rows', 'email_rows', 'outcome', 'paths'),
    [
        ([{'id': 'subject-u', 'username': 'chosen'}], [], 'USERNAME_ALREADY_IN_USE',
         ['/users', '/users']),
        ([], [{'id': 'subject-e', 'email': 'disposable@example.invalid'}],
         'EMAIL_ALREADY_IN_USE', ['/users', '/users', '/users']),
        ([{'id': 'subject-u', 'username': 'chosen'}],
         [{'id': 'subject-e', 'email': 'disposable@example.invalid'}],
         'USERNAME_ALREADY_IN_USE', ['/users', '/users']),
        ([], [], 'REGISTRATION_CONFLICT_UNRESOLVED', ['/users', '/users', '/users']),
    ],
)
def test_registration_conflict_is_resolved_after_create_409(
    username_rows, email_rows, outcome, paths,
):
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == 'POST':
            return httpx.Response(409, text='provider detail is never surfaced')
        assert request.method == 'GET'
        if request.url.params.get('username') is not None:
            return httpx.Response(200, json=username_rows)
        assert request.url.params.get('email') == 'disposable@example.invalid'
        return httpx.Response(200, json=email_rows)

    with pytest.raises(LifecycleError, match=outcome):
        exercise(handler, lambda a: a.create_account(
            username='chosen', password='a' * 8, email='disposable@example.invalid'))
    assert [request.url.path for request in calls] == ['/admin/realms/fixture' + path for path in paths]


@pytest.mark.parametrize('failure', ['unauthorized', 'timeout'])
def test_registration_conflict_lookup_failure_stays_unresolved(failure):
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == 'POST':
            return httpx.Response(409)
        if failure == 'unauthorized':
            return httpx.Response(401)
        raise httpx.ReadTimeout('synthetic provider timeout', request=request)

    with pytest.raises(LifecycleError, match='REGISTRATION_CONFLICT_UNRESOLVED'):
        exercise(handler, lambda a: a.create_account(
            username='chosen', password='a' * 8, email='disposable@example.invalid'))
    # Username is always reconciled first; no email cause is invented after a
    # failed first lookup.
    assert [request.url.path for request in calls] == ['/admin/realms/fixture/users'] * 2


@pytest.mark.parametrize('failure', ['unauthorized', 'timeout'])
def test_registration_create_failure_never_becomes_a_conflict(failure):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == 'unauthorized':
            return httpx.Response(401)
        raise httpx.ReadTimeout('synthetic provider timeout', request=request)

    expected = 'KEYCLOAK_OPERATION_REJECTED' if failure == 'unauthorized' else 'KEYCLOAK_OUTCOME_UNKNOWN'
    with pytest.raises(LifecycleError, match=expected):
        exercise(handler, lambda a: a.create_account(
            username='chosen', password='a' * 8, email='disposable@example.invalid'))
    assert len(calls) == 1


def test_send_verification_409_is_an_operation_failure_not_account_conflict():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(409, text='synthetic provider detail')

    with pytest.raises(LifecycleError, match='KEYCLOAK_OPERATION_REJECTED'):
        exercise(handler, lambda a: a.send_verification('test-subject'))
    assert len(calls) == 1


@pytest.mark.parametrize('location', [None, 'https://foreign.example.test/users/id',
    BASE + '/users/../roles', BASE + '/users/id?secret=hidden'])
def test_created_location_is_not_followed(location):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(201, headers={} if location is None else {'location': location})
    with pytest.raises(LifecycleError):
        exercise(handler, lambda a: a.create_account(username='chosen', password='a' * 8,
                                                       email='disposable@example.invalid'))
    assert len(calls) == 1


@pytest.mark.parametrize('subject', ['../other', 'id?x=1', 'id/roles', '', None])
def test_subject_cannot_change_operation_path(subject):
    with pytest.raises(LifecycleError, match='INVALID_PROVIDER_IDENTIFIER'):
        exercise(lambda r: pytest.fail('unexpected network'), lambda a: a.send_password_reset(subject))


@pytest.mark.parametrize('rows,outcome', [([], None),
    ([{'id': 'subject', 'username': 'CHOSEN'}], 'subject')])
def test_private_exact_reconciliation(rows, outcome):
    def handler(request):
        assert dict(request.url.params) == {'username': 'chosen', 'exact': 'true', 'max': '2'}
        return httpx.Response(200, json=rows)
    assert exercise(handler, lambda a: a.find_username('chosen')) == outcome


def test_private_email_exact_reconciliation_rejects_ambiguous_result():
    def handler(request):
        assert dict(request.url.params) == {
            'email': 'chosen@example.invalid', 'exact': 'true', 'max': '2',
        }
        return httpx.Response(200, json=[
            {'id': 'subject-a', 'email': 'chosen@example.invalid'},
            {'id': 'subject-b', 'email': 'chosen@example.invalid'},
        ])

    with pytest.raises(LifecycleError, match='KEYCLOAK_RESPONSE_INVALID'):
        exercise(handler, lambda a: a.find_account('chosen@example.invalid'))


def test_oversized_response_fails_closed():
    with pytest.raises(LifecycleError, match='KEYCLOAK_RESPONSE_INVALID'):
        exercise(lambda r: httpx.Response(200, content=b'x' * 65537),
                 lambda a: a.find_username('chosen'))
