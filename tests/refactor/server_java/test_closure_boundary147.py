"""HTTP boundary parity cases for account closure prepare/recovery paths."""
from __future__ import annotations

import httpx
import pytest

import contract_support147


PREFIX = "/api/enterprise/account/closure"
LOGIN = "/api/enterprise/auth/native/login"


def _pair(clients, method, path, *, headers=None, content=None):
    per_client_headers = headers if isinstance(headers, dict) and set(headers) == set(clients) else None
    responses = [clients[name].request(method, path,
        headers=per_client_headers[name] if per_client_headers is not None else headers, content=content)
        for name in ("python", "java")]
    py, java = responses
    assert (py.status_code, py.content) == (java.status_code, java.content), (
        f"{method} {path}: Python={(py.status_code, py.content)!r}, "
        f"Java={(java.status_code, java.content)!r}")
    for response in responses:
        assert response.headers.get("x-request-id")
        assert response.headers.get("x-mentorflow-runtime-profile") == "ACCOUNT_CORE_MVP"
        assert response.headers.get("content-type", "").startswith("application/json")
    assert py.headers.get("content-length") == java.headers.get("content-length")
    return py


def _tokens(clients, username="boundary"):
    result = {}
    for name in ("python", "java"):
        response = clients[name].post(LOGIN, json={"username": username, "password": "correct"})
        assert response.status_code == 200, response.text
        result[name] = response.json()["access_token"]
    return result


def _auth(tokens):
    # The same token is intentionally not portable across process-local native sessions.
    return {name: {"Authorization": "Bearer " + value} for name, value in tokens.items()}


@pytest.fixture(scope="module")
def boundary(closure147_backends):
    clients = closure147_backends["clients"]
    # Give boundary tests a dedicated identity so the destructive paired closure
    # flow cannot invalidate access tokens or skew admin-overview counts.
    subject = "user-147-boundary"
    contract_support147.SUBJECTS["boundary"] = subject
    try:
        auth = _auth(_tokens(clients))
    finally:
        contract_support147.SUBJECTS.pop("boundary", None)
    yield closure147_backends, clients, auth
    for identity in closure147_backends["identities"].values():
        identity.deleted.add(subject)


def test_wrong_method_returns_same_allow_and_method_body(boundary):
    _, clients, _ = boundary
    response = _pair(clients, "DELETE", PREFIX + "/prepare")
    assert response.status_code == 405
    assert response.headers.get("allow") == "POST"
    assert response.json() == {"detail": "Method Not Allowed"}


@pytest.mark.parametrize("raw,content_type", [
    (b'{"current_password":"correct","current_password":"wrong"}', "application/json"),
    (b'{"current_password":7}', "application/json"),
    (b'{"unexpected":"correct"}', "application/json"),
    (b'{"current_password":"correct"}', "text/plain"),
    (b'{"current_password":""}', "application/json"),
    (b'{"current_password":"' + b"x" * 4100 + b'"}', "application/json"),
])
def test_prepare_rejects_duplicate_oversize_and_invalid_body(boundary, raw, content_type):
    _, clients, auth = boundary
    headers = {name: {**auth[name], "Content-Type": content_type} for name in clients}
    response = _pair(clients, "POST", PREFIX + "/prepare", headers=headers, content=raw)
    assert response.status_code == 400
    assert response.json()["code"] == "CLOSURE_INPUT_INVALID"
    assert response.headers.get("cache-control") == "no-store"


def test_prepare_rejects_conflicting_recovery_header(boundary):
    _, clients, auth = boundary
    headers = {name: {**auth[name], "X-MentorFlow-Closure-Token": "mfc1_" + "x" * 43}
               for name in clients}
    response = _pair(clients, "POST", PREFIX + "/prepare", headers=headers,
                     content=b'{"current_password":"correct"}')
    assert response.status_code == 403
    assert response.json()["code"] == "CLOSURE_AUTH_BOUNDARY_CONFLICT"


def test_prepare_requires_native_authorization(boundary):
    _, clients, _ = boundary
    response = _pair(clients, "POST", PREFIX + "/prepare",
                     content=b'{"current_password":"correct"}')
    assert response.status_code == 401
    assert response.json()["code"] == "CLOSURE_NATIVE_AUTH_REQUIRED"


def test_prepare_rejects_invalid_native_access_token(boundary):
    _, clients, _ = boundary
    headers = {name: {"Authorization": "Bearer mfn1_a_" + "x" * 43,
                      "Content-Type": "application/json"} for name in clients}
    response = _pair(clients, "POST", PREFIX + "/prepare", headers=headers,
                     content=b'{"current_password":"correct"}')
    assert response.status_code == 401


def test_prepare_maps_wrong_current_password(boundary):
    _, clients, auth = boundary
    headers = {name: {**auth[name], "Content-Type": "application/json"} for name in clients}
    response = _pair(clients, "POST", PREFIX + "/prepare", headers=headers,
                     content=b'{"current_password":"wrong"}')
    assert response.status_code == 400
    assert response.json()["code"] == "CURRENT_PASSWORD_INVALID"


def test_commit_requires_explicit_confirmation_before_token_lookup(boundary):
    _, clients, _ = boundary
    headers = {name: {"Content-Type": "application/json"} for name in clients}
    response = _pair(clients, "POST", PREFIX + "/" + "a" * 32 + "/commit",
                     headers=headers, content=b'{"confirmed":false}')
    assert response.status_code == 400
    assert response.json()["code"] == "CLOSURE_CONFIRMATION_REQUIRED"


def test_recovery_routes_reject_authorization_header(boundary):
    _, clients, _ = boundary
    headers = {name: {"Authorization": "Bearer invalid"} for name in clients}
    response = _pair(clients, "GET", PREFIX + "/" + "0" * 32, headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "CLOSURE_AUTH_BOUNDARY_CONFLICT"


@pytest.mark.parametrize("token_header", [None, ""])
def test_missing_or_empty_recovery_token_is_rejected(boundary, token_header):
    _, clients, _ = boundary
    headers = None if token_header is None else {
        name: {"X-MentorFlow-Closure-Token": token_header} for name in clients}
    response = _pair(clients, "GET", PREFIX + "/" + "0" * 32, headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "CLOSURE_CREDENTIAL_INVALID"
    assert response.headers.get("cache-control") == "no-store"


def test_transport_rejects_origin_on_https(boundary):
    _, clients, auth = boundary
    headers = {name: {**auth[name], "Origin": "https://localhost"} for name in clients}
    response = _pair(clients, "POST", PREFIX + "/prepare", headers=headers,
                     content=b'{"current_password":"correct"}')
    assert response.status_code == 403
    assert response.json()["code"] == "CLOSURE_TRANSPORT_REQUIRED"


def test_https_request_reaches_boundary_and_http_cleartext_is_not_accepted(boundary):
    """The fixture serves TLS only; HTTP cleartext must fail before a handler can run."""
    _, clients, auth = boundary
    headers = {name: {**auth[name], "Content-Type": "application/json"} for name in clients}
    for name in ("python", "java"):
        response = clients[name].post(PREFIX + "/prepare", headers=headers[name], content=b"{}")
        assert response.status_code == 400
        assert response.json()["code"] == "CLOSURE_INPUT_INVALID"
        http_url = httpx.URL(str(clients[name].base_url)).copy_with(scheme="http")
        try:
            cleartext = clients[name].post(str(http_url) + PREFIX + "/prepare",
                headers=headers[name], content=b"{}")
        except httpx.HTTPError:
            continue
        assert cleartext.status_code >= 400
        assert "state" not in (cleartext.json() if cleartext.headers.get("content-type", "").startswith("application/json") else {})
