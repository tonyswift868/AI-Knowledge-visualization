"""Actual ASGI denial checks; native Keycloak email acceptance is separate."""
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app import account_core_mvp
from test_enterprise_account_core_api import _headers, _runtime


ROOT = Path(__file__).resolve().parents[2]
DISABLED_PATHS = [
    '/api/enterprise/auth/register', '/api/enterprise/auth/forgot_password',
    '/api/enterprise/auth/forgot-password', '/api/enterprise/auth/reset-password',
    '/api/enterprise/auth/verify-email', '/api/enterprise/auth/login?prompt=create',
    '/api/auth/register', '/api/auth/forgot-password', '/auth/register',
    '/auth/forgot-password', '/realms/mentorflow/protocol/openid-connect/registrations',
    '/realms/mentorflow/protocol/openid-connect/forgot-credentials',
]


def test_release_capabilities_remain_exactly_closed():
    document = json.loads((ROOT / 'release/account-capabilities.json').read_text())
    assert document == {'schema': 'mentorflow.account-capabilities.v1',
                        'account_capabilities': {'register': False, 'forgot_password': False}}
    routes = {getattr(route, 'path', '') for route in account_core_mvp.app.routes}
    assert not routes.intersection(path.split('?', 1)[0] for path in DISABLED_PATHS)


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('authenticated', [False, True])
def test_disabled_account_routes_never_redirect_or_accept_email(monkeypatch, method, authenticated):
    monkeypatch.setattr(account_core_mvp.app.state, 'enterprise_identity_runtime', _runtime(), raising=False)
    # Without lifespan: no configured database or remote identity service opens.
    with_client = TestClient(account_core_mvp.app)
    try:
        for path in DISABLED_PATHS:
            response = with_client.request(method, path, follow_redirects=False,
                headers=_headers('a') if authenticated else {},
                json={'email': 'synthetic@example.invalid'} if method == 'POST' else None)
            assert response.status_code in {401, 403, 404, 405, 503}, path
            assert 'location' not in response.headers
    finally:
        with_client.close()
