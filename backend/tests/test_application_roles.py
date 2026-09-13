"""Product roles never grant tenant, identity-provider or SQL privileges."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.account_identity_contract import account_id
from app.enterprise.contracts_v1 import ActorKind
from app.enterprise.identity.application_roles import ApplicationRole, ApplicationRolePolicy, SUPER_ADMIN_IDS_ENV
from app.enterprise.identity.models import MembershipStatus
from test_enterprise_identity_runtime import _runtime, _app, ISSUER, ORIGIN
from test_account_workspace_bootstrap import system, headers, INITIALIZE

ME = "/api/enterprise/auth/me"
ADMIN = "/api/enterprise/auth/admin/check"
HEADERS = {"Authorization": "Bearer bearer-token", "X-MentorFlow-Organization-ID": "org-a", "X-MentorFlow-Workspace-ID": "workspace-a"}


def policy(subject="runtime-user-001"):
    return ApplicationRolePolicy.from_env({SUPER_ADMIN_IDS_ENV: json.dumps([account_id(ISSUER, subject)])})


def test_me_and_admin_authorize_from_server_policy_not_membership_roles():
    runtime, _ = _runtime()
    with TestClient(_app(runtime), base_url=ORIGIN) as client:
        assert client.get(ME, headers=HEADERS).json()["role"] == "LEARNER"
        assert client.get(ADMIN, headers=HEADERS).status_code == 403
        runtime.application_roles = policy()
        result = client.get(ME, headers=HEADERS)
        assert result.status_code == 200, result.text
        assert result.json()["role"] == "SUPER_ADMIN"
        assert result.json()["context"]["roles"] == ["LEARNER"]
        assert client.get(ADMIN, headers=HEADERS).json() == {"authorized": True, "role": "SUPER_ADMIN"}
        # Removing the grant applies to the next request even with the same
        # authenticated identity. No client-side cached role authorizes work.
        runtime.application_roles = ApplicationRolePolicy()
        assert client.get(ADMIN, headers=HEADERS).status_code == 403


@pytest.mark.parametrize("path", [ME, ADMIN])
@pytest.mark.parametrize("claim", ["header", "query"])
def test_client_role_assertion_rejected_even_for_real_admin(path, claim):
    runtime, _ = _runtime()
    runtime.application_roles = policy()
    with TestClient(_app(runtime), base_url=ORIGIN) as client:
        headers = dict(HEADERS)
        params = {}
        if claim == "header": headers["X-MentorFlow-Role"] = "SUPER_ADMIN"
        else: params["role"] = "SUPER_ADMIN"
        result = client.get(path, headers=headers, params=params)
        assert result.status_code == 403
        assert "APPLICATION_ROLE_CLIENT_ASSERTION_REJECTED" in result.text


def test_admin_still_requires_live_authentication_and_active_membership():
    runtime, _ = _runtime()
    runtime.application_roles = policy()
    with TestClient(_app(runtime), base_url=ORIGIN) as client:
        assert client.get(ADMIN).status_code == 401
        assert client.get(ADMIN, headers={**HEADERS, "Authorization": "Bearer forged"}).status_code == 401
        runtime.directory.status = MembershipStatus.REVOKED
        assert client.get(ADMIN, headers=HEADERS).status_code == 403


def test_username_email_idp_and_service_identity_cannot_elevate():
    roles = policy()
    actor = SimpleNamespace(issuer=ISSUER, subject_id="different-subject", actor_kind=ActorKind.HUMAN,
                            preferred_username="tonyswift555", email="2153346834@qq.com", roles=("realm-admin", "SUPER_ADMIN"))
    assert roles.role_for(actor) is ApplicationRole.LEARNER
    actor.subject_id = "runtime-user-001"
    actor.actor_kind = ActorKind.SERVICE
    assert roles.role_for(actor) is ApplicationRole.LEARNER
    actor.actor_kind = ActorKind.HUMAN
    actor.issuer = "https://other.example.test"
    assert roles.role_for(actor) is ApplicationRole.LEARNER


@pytest.mark.parametrize("raw", ['["tonyswift555"]', '["SUPER_ADMIN"]', '{}', 'null', '"mfa1_bad"', '[1]', 'not-json', '["mfa1_' + 'a'*64 + '","mfa1_' + 'a'*64 + '"]'])
def test_invalid_server_grants_fail_closed(raw):
    with pytest.raises(ValueError):
        ApplicationRolePolicy.from_env({SUPER_ADMIN_IDS_ENV: raw})


def test_new_account_is_learner_with_admin_policy_configured(system):
    client, _, engine = system
    runtime = client.app.state.enterprise_identity_runtime
    runtime.application_roles = policy("user-a")
    for who, expected in (("a", "SUPER_ADMIN"), ("b", "LEARNER")):
        created = client.post(INITIALIZE, headers=headers(who), json={})
        assert created.status_code == 200, created.text
        workspace = created.json()["workspaces"][0]
        scoped = headers(who, workspace)
        assert client.get(ME, headers=scoped).json()["role"] == expected
        assert client.get(ADMIN, headers=scoped).status_code == (200 if who == "a" else 403)
    with engine.connect() as connection:
        assert set(connection.execute(text("SELECT role FROM organization_memberships")).scalars()) == {"LEARNER"}
        assert set(connection.execute(text("SELECT role FROM workspace_role_bindings")).scalars()) == {"LEARNER"}
