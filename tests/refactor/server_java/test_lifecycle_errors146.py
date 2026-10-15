"""Lifecycle and account-workspace error HTTP parity for the 146 contract."""
from __future__ import annotations

import json
import ssl

import httpx
import pytest

from contract_support144 import OLD_ISSUER, ORG, WORKSPACE, seed_tenant144
from lifecycle_support146 import build_pair146, shutdown_pair146
from test_lifecycle_http146 import _http_projection, _json_body


LIFECYCLE = "/api/enterprise/auth/lifecycle"
NATIVE = "/api/enterprise/auth/native"
WORKSPACES = "/api/enterprise/account/workspaces"


@pytest.fixture
def errors_pair146(tmp_path_factory):
    pair = build_pair146(tmp_path_factory.mktemp("lifecycle-errors146"))
    try:
        yield pair
    finally:
        shutdown_pair146(pair)


def _client(pair, backend):
    return httpx.Client(base_url=pair["servers"][backend]["base_url"],
        verify=ssl.create_default_context(cafile=str(pair["tls"]["ca"])),
        timeout=25, trust_env=False, headers={"X-Request-ID": "contract146-errors"})


def _tenant_headers(token):
    return {"Authorization": "Bearer " + token,
        "X-MentorFlow-Organization-ID": ORG, "X-MentorFlow-Workspace-ID": WORKSPACE}


def test_lifecycle146_registration_input_and_resend_error_contract(errors_pair146):
    traces = {}
    for backend in ("python", "java"):
        authority = errors_pair146["identities"][backend]
        trace = []
        with _client(errors_pair146, backend) as client:
            # Lifecycle capability discovery is deliberately unavailable when
            # a browser/native bearer is mixed into its anonymous transport.
            capability = client.get(LIFECYCLE + "/capabilities",
                headers={"Authorization": "Bearer ignored"})
            assert capability.status_code == 200
            assert _json_body(capability) == {"register": False, "forgot_password": False}
            trace.append(_http_projection(capability))

            malformed = [
                ({"username": "user", "password": "valid-password-146"}, "missing email"),
                ({"username": "user", "email": "not-an-email", "password": "valid-password-146"}, "email format"),
                ({"username": "user", "email": "user@fixture.invalid", "password": "short"}, "password policy"),
            ]
            for body, _reason in malformed:
                response = client.post(LIFECYCLE + "/register", json=body)
                assert response.status_code == 400
                assert set(_json_body(response)) == {"code", "message"}
                assert _json_body(response)["code"] == "REGISTRATION_INPUT_INVALID"
                trace.append(_http_projection(response))
            duplicate_json = client.post(LIFECYCLE + "/register",
                content=b'{"username":"u","username":"v","email":"u@fixture.invalid","password":"valid-password-146"}',
                headers={"Content-Type": "application/json"})
            assert duplicate_json.status_code == 400
            assert _json_body(duplicate_json) == {"code": "REGISTRATION_INPUT_INVALID", "message": "请检查账号名、邮箱和密码。"}
            trace.append(_http_projection(duplicate_json))
            wrong_content_type = client.post(LIFECYCLE + "/register", content=b"{}",
                headers={"Content-Type": "text/plain"})
            assert wrong_content_type.status_code == 400
            assert _json_body(wrong_content_type)["code"] == "REGISTRATION_INPUT_INVALID"
            trace.append(_http_projection(wrong_content_type))

            started = client.post(LIFECYCLE + "/register", json={"username": "resend-user146",
                "email": "resend146@fixture.invalid", "password": "valid-password-146"})
            assert started.status_code == 201
            credential = _json_body(started)["credential"]
            trace.append(_http_projection(started))
            for provider_status, expected_code in (("COOLDOWN", "REGISTRATION_COOLDOWN"),
                                                  ("SEND_LIMIT", "REGISTRATION_SEND_LIMIT")):
                authority.next_registration_resend_status = provider_status
                response = client.post(LIFECYCLE + "/register/resend", json={"credential": credential})
                assert response.status_code == 429
                assert _json_body(response)["code"] == expected_code
                trace.append(_http_projection(response))
            assert all(set(json.loads(item[2])) == {"code", "message"}
                for item in trace[1:6])
        traces[backend] = trace
    assert traces["python"] == traces["java"]


def test_lifecycle146_password_write_unknown_and_provider_logout_failure(errors_pair146):
    # Existing tenant data is seeded through the local PostgreSQL owner, never
    # through a fake persistence layer, so protected lifecycle calls exercise RLS.
    pgport = errors_pair146["pg"]["port"]
    subject = "subject146-password-failure146"
    for backend in ("python", "java"):
        db_name = "py146" if backend == "python" else "java146"
        seed_tenant144(f"postgresql+psycopg://postgres@127.0.0.1:{pgport}/{db_name}",
            OLD_ISSUER, subject)
        errors_pair146["identities"][backend].add_user("password-failure146",
            "password-failure146@fixture.invalid", "old-password-146")

    traces = {}
    for backend in ("python", "java"):
        authority = errors_pair146["identities"][backend]
        trace = []
        with _client(errors_pair146, backend) as client:
            login = client.post(NATIVE + "/login", json={"username": "password-failure146",
                "password": "old-password-146"})
            assert login.status_code == 200
            token = _json_body(login)["access_token"]
            scoped = _tenant_headers(token)
            authority.admin_username_override = ""
            failed_lookup = client.post(LIFECYCLE + "/change-password",
                json={"current_password": "old-password-146", "new_password": "new-password-146"}, headers=scoped)
            assert failed_lookup.status_code == 503
            assert _json_body(failed_lookup)["error"]["code"] == "ACCOUNT_ACTION_UNAVAILABLE"
            assert _json_body(failed_lookup)["error"]["message"] == "密码暂未修改，请稍后重试。"
            trace.append(_http_projection(failed_lookup))
            authority.admin_username_override = None
            authority.admin_reset_password_status = 500
            failed_write = client.post(LIFECYCLE + "/change-password",
                json={"current_password": "old-password-146", "new_password": "new-password-146"},
                headers=scoped)
            assert failed_write.status_code == 503
            assert _json_body(failed_write)["state"] == "PASSWORD_CHANGE_OUTCOME_UNKNOWN"
            trace.append(_http_projection(failed_write))
            authority.admin_reset_password_status = 204

            re_login = client.post(NATIVE + "/login", json={"username": "password-failure146",
                "password": "old-password-146"})
            assert re_login.status_code == 200
            revoke_headers = _tenant_headers(_json_body(re_login)["access_token"])
            authority.admin_logout_status = 503
            pending = client.post(LIFECYCLE + "/revoke-sessions", json={}, headers=revoke_headers)
            assert pending.status_code == 503
            assert _json_body(pending)["state"] == "SESSION_REVOCATION_PENDING"
            trace.append(_http_projection(pending))
            authority.admin_logout_status = 204
        traces[backend] = trace
    assert traces["python"] == traces["java"]


def test_lifecycle146_workspace_method_tenant_and_cookie_boundaries(errors_pair146):
    traces = {}
    for backend in ("python", "java"):
        with _client(errors_pair146, backend) as client:
            login = client.post(NATIVE + "/login", json={"username": "alice", "password": "correct"})
            assert login.status_code == 200
            token = _json_body(login)["access_token"]
            cases = [
                (client.post(WORKSPACES, headers={"Authorization": "Bearer " + token}), 405),
                (client.get(WORKSPACES + "/initialize", headers={"Authorization": "Bearer " + token}), 405),
                (client.get(WORKSPACES, headers={"Authorization": "Bearer " + token,
                    "X-MentorFlow-Organization-ID": ORG}), 422),
                (client.get(WORKSPACES, headers={"Authorization": "Bearer " + token,
                    "Cookie": "__Host-mentorflow_enterprise_session=opaque"}), 401),
            ]
            for raw in (b"", b"null", b"[]", b"true", b'{"extra":null}', b"  ", b"{broken"):
                cases.append((client.post(WORKSPACES + "/initialize", content=raw,
                    headers={"Authorization": "Bearer " + token,
                        "Content-Type": "application/json"}), 422))
            trace = []
            for response, expected_status in cases:
                assert response.status_code == expected_status, response.text
                trace.append(_http_projection(response))
            traces[backend] = trace
    differences = [f"workspace validation request {index}: Python={python!r}, Java={java!r}"
        for index, (python, java) in enumerate(zip(traces["python"], traces["java"], strict=True)) if python != java]
    assert not differences, "\n".join(differences)
