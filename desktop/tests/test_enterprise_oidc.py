from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from desktop.account_identity import AccountIdentity
from desktop.enterprise_oidc import (
    BackendOidcTokenValidator,
    DesktopOidcConfig,
    EnterpriseOidcClient,
    EnterpriseOidcError,
    OidcLoginTransaction,
    OidcDiscovery,
    ValidatedTokenIdentity,
)


ISSUER = "https://identity.example.test/realms/mentorflow"
API = "https://mentorflow.example.test"


def _config() -> DesktopOidcConfig:
    return DesktopOidcConfig(
        issuer=ISSUER,
        client_id="mentorflow-desktop",
        audience="mentorflow-api",
        api_base_url=API,
        organization_id="org-a",
        workspace_id="workspace-a",
        callback_timeout_seconds=15,
    )


def _discovery() -> dict[str, str]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
        "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
        "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
    }


def _discovery_contract() -> OidcDiscovery:
    value = _discovery()
    return OidcDiscovery(
        issuer=value["issuer"],
        authorization_endpoint=value["authorization_endpoint"],
        token_endpoint=value["token_endpoint"],
        jwks_uri=value["jwks_uri"],
    )


def _state(subject: str = "subject-a") -> dict[str, object]:
    return {
        "schema_version": "mentorflow.enterprise-identity-session.v1",
        "authenticated": True,
        "account": {
            "subject_id": subject,
            "display_name": "Account A",
            "preferred_username": "account-a",
        },
        "context": {
            "contract_version": "enterprise-context.v1",
            "organization_id": "org-a",
            "workspace_id": "workspace-a",
            "roles": ["learner"],
            "context_version": 7,
        },
    }


class _Validator:
    def __init__(self) -> None:
        self.login_calls: list[tuple[str, str, str]] = []
        self.access_calls: list[str] = []

    async def validate_login(
        self,
        *,
        id_token: str,
        access_token: str,
        expected_nonce: str,
    ) -> ValidatedTokenIdentity:
        self.login_calls.append((id_token, access_token, expected_nonce))
        return ValidatedTokenIdentity(ISSUER, "subject-a", "Token Account A")

    async def validate_access(self, access_token: str) -> ValidatedTokenIdentity:
        self.access_calls.append(access_token)
        if access_token == "expired-access":
            raise RuntimeError("expired")
        return ValidatedTokenIdentity(ISSUER, "subject-a", "Token Account A")

    async def aclose(self) -> None:
        return None


def test_pkce_s256_is_unique_and_transaction_is_one_time() -> None:
    first = OidcLoginTransaction(redirect_uri="http://127.0.0.1:43871/oauth/callback")
    second = OidcLoginTransaction(redirect_uri="http://127.0.0.1:43872/oauth/callback")
    first_url = first.authorization_url(
        endpoint=f"{ISSUER}/protocol/openid-connect/auth",
        client_id="mentorflow-desktop",
        scopes=("openid", "profile", "email"),
    )
    first_query = parse_qs(urlsplit(first_url).query)

    assert first_query["response_type"] == ["code"]
    assert first_query["code_challenge_method"] == ["S256"]
    assert len(first_query["code_challenge"][0]) == 43
    assert first.code_challenge != second.code_challenge
    assert first.state != second.state
    assert first.nonce != second.nonce

    callback = (
        f"{first.redirect_uri}?"
        + urlencode({"code": "authorization-code", "state": first.state})
    )
    authorization = first.consume_callback(callback)
    assert authorization.code == "authorization-code"
    assert len(authorization.code_verifier) >= 43
    assert first.verifier_available is False
    with pytest.raises(EnterpriseOidcError) as replay:
        first.consume_callback(callback)
    assert replay.value.code == "ENTERPRISE_OIDC_TRANSACTION_CONSUMED"


def test_production_validator_reuses_frozen_backend_oidc_contract() -> None:
    validator = BackendOidcTokenValidator(
        _config(),
        redirect_uri="http://127.0.0.1:43870/oauth/callback",
        discovery=_discovery_contract(),
    )
    asyncio.run(validator.aclose())


def test_callback_state_mismatch_consumes_transaction_fail_closed() -> None:
    transaction = OidcLoginTransaction(
        redirect_uri="http://127.0.0.1:43873/oauth/callback"
    )
    with pytest.raises(EnterpriseOidcError) as error:
        transaction.consume_callback(
            f"{transaction.redirect_uri}?code=attacker&state=wrong"
        )
    assert error.value.code == "ENTERPRISE_OIDC_STATE_INVALID"
    assert transaction.verifier_available is False

    denied = OidcLoginTransaction(
        redirect_uri="http://127.0.0.1:43876/oauth/callback",
        expected_issuer=ISSUER,
    )
    with pytest.raises(EnterpriseOidcError) as denied_error:
        denied.consume_callback(
            denied.redirect_uri + "?" + urlencode({"error": "access_denied", "state": "wrong"})
        )
    assert denied_error.value.code == "ENTERPRISE_OIDC_STATE_INVALID"


def test_keycloak_response_issuer_and_session_state_are_validated() -> None:
    transaction = OidcLoginTransaction(
        redirect_uri="http://127.0.0.1:43874/oauth/callback",
        expected_issuer=ISSUER,
    )
    callback = transaction.redirect_uri + "?" + urlencode(
        {
            "code": "authorization-code",
            "state": transaction.state,
            "iss": ISSUER,
            "session_state": "opaque-keycloak-session",
        }
    )
    assert transaction.consume_callback(callback).code == "authorization-code"

    mismatch = OidcLoginTransaction(
        redirect_uri="http://127.0.0.1:43875/oauth/callback",
        expected_issuer=ISSUER,
    )
    with pytest.raises(EnterpriseOidcError) as error:
        mismatch.consume_callback(
            mismatch.redirect_uri
            + "?"
            + urlencode(
                {
                    "code": "authorization-code",
                    "state": mismatch.state,
                    "iss": "https://attacker.example.test/realms/other",
                }
            )
        )
    assert error.value.code == "ENTERPRISE_OIDC_ISSUER_INVALID"


def test_login_opens_external_browser_returns_to_loopback_and_refreshes_state_once() -> None:
    validator = _Validator()
    browser_urls: list[str] = []
    requests: list[tuple[str, str]] = []
    token_forms: list[Mapping[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        if str(request.url) == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, json=_discovery())
        if str(request.url) == f"{ISSUER}/protocol/openid-connect/token":
            form = parse_qs(request.content.decode("ascii"))
            token_forms.append(form)
            return httpx.Response(
                200,
                json={
                    "token_type": "Bearer",
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "id_token": "id-secret",
                },
            )
        if str(request.url) == f"{API}/api/enterprise/auth/me":
            assert request.headers["Authorization"] == "Bearer access-secret"
            assert request.headers["X-MentorFlow-Organization-ID"] == "org-a"
            assert request.headers["X-MentorFlow-Workspace-ID"] == "workspace-a"
            return httpx.Response(200, json=_state())
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async def run():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = EnterpriseOidcClient(
            _config(),
            http_client=http,
            token_validator=validator,
        )

        def open_browser(url: str) -> bool:
            browser_urls.append(url)
            query = parse_qs(urlsplit(url).query)
            callback = query["redirect_uri"][0] + "?" + urlencode(
                {"code": "auth-code", "state": query["state"][0]}
            )

            def return_to_desktop() -> None:
                response = httpx.get(callback, timeout=5)
                assert response.status_code == 200
                assert "access-secret" not in response.text

            threading.Thread(target=return_to_desktop, daemon=True).start()
            return True

        try:
            return await client.login(browser_opener=open_browser)
        finally:
            await client.aclose()
            await http.aclose()

    result = asyncio.run(run())

    assert len(browser_urls) == 1
    browser_query = parse_qs(urlsplit(browser_urls[0]).query)
    assert browser_query["code_challenge_method"] == ["S256"]
    assert browser_query["nonce"] == [validator.login_calls[0][2]]
    assert token_forms[0]["grant_type"] == ["authorization_code"]
    assert token_forms[0]["code"] == ["auth-code"]
    assert len(token_forms[0]["code_verifier"][0]) >= 43
    assert requests.count(("GET", f"{API}/api/enterprise/auth/me")) == 1
    assert result.identity == AccountIdentity(
        issuer=ISSUER,
        subject="subject-a",
        display_name="Account A",
        organization_id="org-a",
        workspace_id="workspace-a",
    )
    assert result.sync_cursor == "7"
    serialized = repr(result)
    for secret in ("access-secret", "refresh-secret", "subject-a"):
        assert secret not in serialized


class _Credentials:
    def __init__(self) -> None:
        self.stored: list[tuple[AccountIdentity, str, str]] = []

    def load_oauth_tokens(self, _profile):
        return "expired-access", "refresh-original"

    def store_oauth_tokens(
        self,
        identity: AccountIdentity,
        *,
        access_token: str,
        refresh_token: str,
    ) -> dict[str, str]:
        self.stored.append((identity, access_token, refresh_token))
        return {"access_token_ref": "opaque", "refresh_token_ref": "opaque"}


def test_restart_refreshes_expired_session_and_rejects_cross_account_state() -> None:
    validator = _Validator()
    credentials = _Credentials()
    token_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        if str(request.url) == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, json=_discovery())
        if str(request.url) == f"{ISSUER}/protocol/openid-connect/token":
            token_requests += 1
            form = parse_qs(request.content.decode("ascii"))
            assert form == {
                "grant_type": ["refresh_token"],
                "client_id": ["mentorflow-desktop"],
                "refresh_token": ["refresh-original"],
            }
            return httpx.Response(
                200,
                json={
                    "token_type": "Bearer",
                    "access_token": "fresh-access",
                    "refresh_token": "refresh-rotated",
                },
            )
        if str(request.url) == f"{API}/api/enterprise/auth/me":
            assert request.headers["Authorization"] == "Bearer fresh-access"
            return httpx.Response(200, json=_state())
        raise AssertionError(str(request.url))

    profile = AccountIdentity(ISSUER, "subject-a", "Account A").profile()

    async def run():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = EnterpriseOidcClient(
            _config(),
            http_client=http,
            token_validator=validator,
        )
        try:
            return await client.restore(profile, credentials)  # type: ignore[arg-type]
        finally:
            await client.aclose()
            await http.aclose()

    restored = asyncio.run(run())
    assert restored.restored is True
    assert token_requests == 1
    assert validator.access_calls == ["expired-access", "fresh-access"]
    assert credentials.stored == [
        (restored.identity, "fresh-access", "refresh-rotated")
    ]


def test_server_state_subject_mismatch_never_returns_an_account() -> None:
    validator = _Validator()

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, json=_discovery())
        if str(request.url) == f"{ISSUER}/protocol/openid-connect/token":
            return httpx.Response(
                200,
                json={
                    "token_type": "Bearer",
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "id_token": "id-secret",
                },
            )
        if str(request.url) == f"{API}/api/enterprise/auth/me":
            return httpx.Response(200, json=_state("subject-b"))
        raise AssertionError(str(request.url))

    async def run():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = EnterpriseOidcClient(
            _config(),
            http_client=http,
            token_validator=validator,
        )
        prepared = await client.prepare_login()
        callback = prepared.receiver.redirect_uri + "?" + urlencode(
            {"code": "code", "state": prepared.transaction.state}
        )
        try:
            with pytest.raises(EnterpriseOidcError) as error:
                await client.complete_login(prepared, callback)
            assert error.value.code == "ENTERPRISE_ACCOUNT_STATE_MISMATCH"
        finally:
            prepared.receiver.close()
            await client.aclose()
            await http.aclose()

    asyncio.run(run())
