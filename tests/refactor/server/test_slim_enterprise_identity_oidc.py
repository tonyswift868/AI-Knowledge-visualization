from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.hashes import SHA256

from refactor.server.enterprise.contracts_v1 import ActorKind, TenantMode
from refactor.server.enterprise.identity.config import (
    EnterpriseIdentityConfig,
    IdentityConfigurationError,
    KeycloakOidcConfig,
)
from refactor.server.enterprise.identity.keycloak import (
    InMemoryEnterpriseSessionStore,
    KeycloakOidcClient,
    PendingAuthorization,
)
from refactor.server.enterprise.identity.models import AuthenticatedPrincipal, EnterpriseSession
from refactor.server.enterprise.identity.oidc import (
    HttpJwksSigningKeyResolver,
    IdentityError,
    OidcIdentityAdapter,
)


NOW = datetime(2026, 8, 26, 10, 30, tzinfo=timezone.utc)
ISSUER = "https://identity.example.test/realms/mentorflow"
CLIENT_ID = "mentorflow-web"
AUDIENCE = "mentorflow-api"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _claims(**overrides):
    values = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "azp": CLIENT_ID,
        "sub": "account-001",
        "jti": "token-001",
        "iat": int((NOW - timedelta(seconds=1)).timestamp()),
        "auth_time": int((NOW - timedelta(minutes=1)).timestamp()),
        "nbf": int((NOW - timedelta(seconds=1)).timestamp()),
        "exp": int((NOW + timedelta(minutes=5)).timestamp()),
        "sid": "keycloak-session-001",
        "preferred_username": "learner@example.test",
        "email": "learner@example.test",
        "name": "Learner One",
        "acr": "mfa",
    }
    values.update(overrides)
    return values


def _token(private_key, *, header: dict | None = None, claims: dict | None = None) -> str:
    protected = {"alg": "RS256", "kid": "kid-001", "typ": "JWT"}
    if header:
        protected.update(header)
    payload = _claims() if claims is None else claims
    encoded_header = _b64(json.dumps(protected, separators=(",", ":")).encode("utf-8"))
    encoded_claims = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), SHA256())
    return f"{encoded_header}.{encoded_claims}.{_b64(signature)}"


class _StaticSigningKey:
    def __init__(self, key) -> None:
        self.key = key

    async def signing_key(self, *, key_id: str, algorithm: str):
        assert key_id == "kid-001"
        assert algorithm == "RS256"
        return self.key


@pytest.fixture(scope="module")
def signing_keys():
    trusted = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    forged = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    return trusted, forged


def _config(**overrides) -> KeycloakOidcConfig:
    values = {
        "issuer": ISSUER,
        "client_id": CLIENT_ID,
        "audience": AUDIENCE,
        "redirect_uri": "https://mentorflow.example/api/enterprise/auth/callback",
        "post_logout_redirect_uri": "https://mentorflow.example/",
    }
    values.update(overrides)
    return KeycloakOidcConfig(**values)


def _adapter(public_key) -> OidcIdentityAdapter:
    return OidcIdentityAdapter(
        _config(),
        signing_keys=_StaticSigningKey(public_key),
        clock=lambda: NOW,
    )


def test_oidc_adapter_validates_signature_and_registered_claims(signing_keys) -> None:
    trusted, forged = signing_keys
    adapter = _adapter(trusted.public_key())
    principal = asyncio.run(adapter.validate_access_token(_token(trusted)))

    assert principal.issuer == ISSUER
    assert principal.audiences == (AUDIENCE,)
    assert principal.token_id == "token-001"
    assert principal.subject == "account-001"
    assert principal.actor_kind is ActorKind.HUMAN
    assert principal.authentication_strength == "mfa"

    invalid_cases = [
        (_token(forged), "OIDC_SIGNATURE_INVALID"),
        (_token(trusted, claims=_claims(iss="https://forged.example.test")), "OIDC_ISSUER_INVALID"),
        (_token(trusted, claims=_claims(aud="wrong-audience")), "OIDC_AUDIENCE_INVALID"),
        (
            _token(
                trusted,
                claims=_claims(
                    iat=int((NOW - timedelta(minutes=3)).timestamp()),
                    auth_time=int((NOW - timedelta(minutes=4)).timestamp()),
                    exp=int((NOW - timedelta(minutes=2)).timestamp()),
                ),
            ),
            "OIDC_TOKEN_EXPIRED",
        ),
        (
            _token(
                trusted,
                claims=_claims(nbf=int((NOW + timedelta(minutes=2)).timestamp())),
            ),
            "OIDC_TOKEN_NOT_ACTIVE",
        ),
        (_token(trusted, header={"alg": "HS256"}), "OIDC_ALGORITHM_REJECTED"),
        (
            _token(
                trusted,
                claims=_claims(exp=int((NOW + timedelta(days=2)).timestamp())),
            ),
            "OIDC_TOKEN_LIFETIME_INVALID",
        ),
    ]
    for token, expected in invalid_cases:
        with pytest.raises(IdentityError) as caught:
            asyncio.run(adapter.validate_access_token(token))
        assert caught.value.code == expected

    missing_jti = _claims()
    missing_jti.pop("jti")
    with pytest.raises(IdentityError) as caught:
        asyncio.run(adapter.validate_access_token(_token(trusted, claims=missing_jti)))
    assert caught.value.code == "OIDC_CLAIM_REQUIRED"


def test_account_core_desktop_azp_preserves_all_token_validation_gates(
    signing_keys,
) -> None:
    trusted, forged = signing_keys
    desktop_client_id = "mentorflow-desktop"
    adapter = OidcIdentityAdapter(
        _config(client_id=desktop_client_id),
        signing_keys=_StaticSigningKey(trusted.public_key()),
        clock=lambda: NOW,
    )

    principal = asyncio.run(
        adapter.validate_access_token(
            _token(trusted, claims=_claims(azp=desktop_client_id))
        )
    )
    assert principal.subject == "account-001"
    assert principal.client_id == desktop_client_id
    assert principal.audiences == (AUDIENCE,)

    missing_sub_claims = _claims(azp=desktop_client_id)
    missing_sub_claims.pop("sub")
    invalid_cases = [
        (
            _token(trusted, claims=missing_sub_claims),
            "OIDC_CLAIM_REQUIRED",
        ),
        (
            _token(trusted, claims=_claims(azp="unknown-client")),
            "OIDC_AUTHORIZED_PARTY_INVALID",
        ),
        (
            _token(
                trusted,
                claims=_claims(azp=desktop_client_id, aud="wrong-audience"),
            ),
            "OIDC_AUDIENCE_INVALID",
        ),
        (
            _token(
                trusted,
                claims=_claims(
                    azp=desktop_client_id,
                    iss="https://forged.example.test/realms/mentorflow",
                ),
            ),
            "OIDC_ISSUER_INVALID",
        ),
        (
            _token(forged, claims=_claims(azp=desktop_client_id)),
            "OIDC_SIGNATURE_INVALID",
        ),
        (
            _token(
                trusted,
                claims=_claims(
                    azp=desktop_client_id,
                    iat=int((NOW - timedelta(minutes=3)).timestamp()),
                    auth_time=int((NOW - timedelta(minutes=4)).timestamp()),
                    nbf=int((NOW - timedelta(minutes=3)).timestamp()),
                    exp=int((NOW - timedelta(minutes=2)).timestamp()),
                ),
            ),
            "OIDC_TOKEN_EXPIRED",
        ),
    ]
    for token, expected_code in invalid_cases:
        with pytest.raises(IdentityError) as caught:
            asyncio.run(adapter.validate_access_token(token))
        assert caught.value.code == expected_code


def test_service_account_classification_requires_keycloak_service_shape(signing_keys) -> None:
    trusted, _ = signing_keys
    claims = _claims(
        azp="mentorflow-worker",
        preferred_username="service-account-mentorflow-worker",
    )
    adapter = OidcIdentityAdapter(
        _config(client_id="mentorflow-worker"),
        signing_keys=_StaticSigningKey(trusted.public_key()),
        clock=lambda: NOW,
    )
    principal = asyncio.run(adapter.validate_access_token(_token(trusted, claims=claims)))
    assert principal.actor_kind is ActorKind.SERVICE
    assert principal.client_id == "mentorflow-worker"


def test_http_jwks_resolver_accepts_only_bounded_rs256_signing_keys(signing_keys) -> None:
    trusted, _ = signing_keys
    numbers = trusted.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": "kid-001",
        "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert str(request.url) == f"{ISSUER}/protocol/openid-connect/certs"
        return httpx.Response(200, json={"keys": [jwk]})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            resolver = HttpJwksSigningKeyResolver(_config(), http_client=http)
            first = await resolver.signing_key(key_id="kid-001", algorithm="RS256")
            second = await resolver.signing_key(key_id="kid-001", algorithm="RS256")
            return first, second

    first, second = asyncio.run(scenario())
    assert first.public_numbers() == trusted.public_key().public_numbers()
    assert second.public_numbers() == first.public_numbers()
    assert requests == 1


def test_http_jwks_resolver_uses_public_issuer_and_default_tls_verification(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class _Client:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("refactor.server.enterprise.identity.oidc.httpx.AsyncClient", _Client)
    config = _config(issuer="https://localhost:18080/realms/mentorflow")

    HttpJwksSigningKeyResolver(config)

    assert config.jwks_uri == (
        "https://localhost:18080/realms/mentorflow/protocol/openid-connect/certs"
    )
    assert captured["follow_redirects"] is False
    assert captured.get("verify", True) is not False


def test_http_jwks_unavailable_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline JWKS fixture", request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            resolver = HttpJwksSigningKeyResolver(_config(), http_client=http)
            with pytest.raises(IdentityError) as caught:
                await resolver.signing_key(key_id="kid-001", algorithm="RS256")
            assert caught.value.code == "OIDC_JWKS_UNAVAILABLE"
            assert caught.value.status == 503

    asyncio.run(scenario())


def test_http_jwks_unknown_kids_share_a_bounded_forced_refresh_backoff(signing_keys) -> None:
    trusted, _ = signing_keys
    numbers = trusted.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": "kid-001",
        "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }
    requests = 0
    clock = [0.0]

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"keys": [jwk]})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            resolver = HttpJwksSigningKeyResolver(
                _config(),
                http_client=http,
                monotonic_clock=lambda: clock[0],
            )
            for key_id in ("attacker-kid-a", "attacker-kid-b"):
                with pytest.raises(IdentityError) as missing:
                    await resolver.signing_key(key_id=key_id, algorithm="RS256")
                assert missing.value.code == "OIDC_SIGNING_KEY_NOT_FOUND"
            assert requests == 1
            clock[0] = 31.0
            with pytest.raises(IdentityError):
                await resolver.signing_key(key_id="attacker-kid-c", algorithm="RS256")
            with pytest.raises(IdentityError):
                await resolver.signing_key(key_id="attacker-kid-d", algorithm="RS256")

    asyncio.run(scenario())
    assert requests == 2


def test_oidc_client_and_api_audience_must_be_distinct() -> None:
    with pytest.raises(IdentityConfigurationError, match="must be distinct"):
        _config(audience=CLIENT_ID)


def test_authorization_code_login_uses_pkce_nonce_and_one_time_state(signing_keys) -> None:
    trusted, _ = signing_keys
    adapter = _adapter(trusted.public_key())
    seen_form: dict[str, str] = {}
    authorization: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode("utf-8"))
        seen_form.update({key: values[0] for key, values in form.items()})
        return httpx.Response(
            200,
            json={
                "access_token": _token(trusted),
                "id_token": _token(
                    trusted,
                    claims=_claims(aud=CLIENT_ID, nonce=authorization["nonce"]),
                ),
                "refresh_token": "opaque-refresh-token",
                "refresh_expires_in": 1_800,
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = KeycloakOidcClient(
                EnterpriseIdentityConfig(mode=TenantMode.ENTERPRISE_SERVER, keycloak=_config()),
                oidc=adapter,
                http_client=http,
            )
            login_url = await client.begin_login()
            query = parse_qs(urlparse(login_url).query)
            authorization.update({key: values[0] for key, values in query.items()})
            session = await client.complete_login(
                code="one-time-code",
                state=authorization["state"],
            )
            with pytest.raises(IdentityError) as reused:
                await client.complete_login(code="replayed-code", state=authorization["state"])
            return session, reused.value.code

    session, replay_code = asyncio.run(scenario())
    assert authorization["response_type"] == "code"
    assert authorization["code_challenge_method"] == "S256"
    expected_challenge = _b64(hashlib.sha256(seen_form["code_verifier"].encode("ascii")).digest())
    assert authorization["code_challenge"] == expected_challenge
    assert seen_form["grant_type"] == "authorization_code"
    assert session.principal.subject == "account-001"
    assert replay_code == "OIDC_STATE_INVALID"


def test_worker_token_is_rejected_while_supported_user_pkce_token_is_accepted(
    signing_keys,
) -> None:
    trusted, _ = signing_keys
    adapter = _adapter(trusted.public_key())
    service_token = _token(
        trusted,
        claims=_claims(
            azp="mentorflow-worker",
            preferred_username="service-account-mentorflow-worker",
        ),
    )
    with pytest.raises(IdentityError) as rejected:
        asyncio.run(adapter.validate_access_token(service_token))
    assert rejected.value.code == "OIDC_AUTHORIZED_PARTY_INVALID"

    authorization: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode("utf-8"))
        assert form["grant_type"] == ["authorization_code"]
        assert form["code_verifier"][0]
        return httpx.Response(
            200,
            json={
                "access_token": _token(trusted),
                "id_token": _token(
                    trusted,
                    claims=_claims(aud=CLIENT_ID, nonce=authorization["nonce"]),
                ),
                "refresh_token": "opaque-refresh-token",
                "refresh_expires_in": 1_800,
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = KeycloakOidcClient(
                EnterpriseIdentityConfig(
                    mode=TenantMode.ENTERPRISE_SERVER,
                    keycloak=_config(),
                ),
                oidc=adapter,
                http_client=http,
            )
            query = parse_qs(urlparse(await client.begin_login()).query)
            authorization.update({key: value[0] for key, value in query.items()})
            return await client.complete_login(
                code="supported-user-code",
                state=authorization["state"],
            )

    session = asyncio.run(scenario())
    assert authorization["response_type"] == "code"
    assert authorization["code_challenge_method"] == "S256"
    assert session.principal.actor_kind is ActorKind.HUMAN
    assert session.principal.subject == "account-001"
    assert session.principal.client_id == CLIENT_ID
    realm = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "refactor"
            / "server"
            / "keycloak"
            / "keycloak-realm.json"
        ).read_text(encoding="utf-8")
    )
    assert sum(bool(client["directAccessGrantsEnabled"]) for client in realm["clients"]) == 0


def test_pending_login_capacity_evicts_oldest_transaction() -> None:
    store = InMemoryEnterpriseSessionStore(max_pending=1)
    current = datetime.now(timezone.utc)
    first = PendingAuthorization(
        state="first-pending-state",
        nonce="first-pending-nonce",
        code_verifier="first-private-verifier",
        redirect_uri=_config().redirect_uri,
        created_at=current,
        expires_at=current + timedelta(minutes=5),
    )
    second = PendingAuthorization(
        state="second-pending-state",
        nonce="second-pending-nonce",
        code_verifier="second-private-verifier",
        redirect_uri=_config().redirect_uri,
        created_at=current + timedelta(seconds=1),
        expires_at=current + timedelta(minutes=5),
    )
    store.add_pending(first)
    store.add_pending(second)
    with pytest.raises(IdentityError) as evicted:
        store.consume_pending(first.state, now=current + timedelta(seconds=2))
    assert evicted.value.code == "OIDC_STATE_INVALID"
    assert store.consume_pending(
        second.state,
        now=current + timedelta(seconds=2),
    ) == second


def test_id_token_requires_client_audience(signing_keys) -> None:
    trusted, _ = signing_keys
    adapter = _adapter(trusted.public_key())
    with pytest.raises(IdentityError) as caught:
        asyncio.run(
            adapter.validate_id_token(
                _token(trusted, claims=_claims(nonce="nonce-1")),
                expected_nonce="nonce-1",
            )
        )
    assert caught.value.code == "OIDC_AUDIENCE_INVALID"


def test_refresh_invalid_grant_revokes_local_session(signing_keys) -> None:
    trusted, _ = signing_keys
    adapter = _adapter(trusted.public_key())
    store = InMemoryEnterpriseSessionStore()
    authorization: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode("utf-8"))
        if form["grant_type"][0] == "authorization_code":
            return httpx.Response(
                200,
                json={
                    "access_token": _token(trusted),
                    "id_token": _token(
                        trusted,
                        claims=_claims(aud=CLIENT_ID, nonce=authorization["nonce"]),
                    ),
                    "refresh_token": "refresh-token",
                },
            )
        return httpx.Response(400, json={"error": "invalid_grant"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = KeycloakOidcClient(
                EnterpriseIdentityConfig(mode=TenantMode.ENTERPRISE_SERVER, keycloak=_config()),
                oidc=adapter,
                store=store,
                http_client=http,
            )
            login_url = await client.begin_login()
            authorization.update(
                {key: values[0] for key, values in parse_qs(urlparse(login_url).query).items()}
            )
            session = await client.complete_login(code="code", state=authorization["state"])
            with pytest.raises(IdentityError) as refresh_error:
                await client.refresh_session(session.session_id)
            with pytest.raises(IdentityError) as local_error:
                store.get(session.session_id)
            return refresh_error.value.code, local_error.value.code

    assert asyncio.run(scenario()) == (
        "IDENTITY_DISABLED_OR_REVOKED",
        "ENTERPRISE_SESSION_INVALID",
    )


def test_logout_accepts_empty_revocation_response_and_exposes_no_tokens(signing_keys) -> None:
    trusted, _ = signing_keys
    adapter = _adapter(trusted.public_key())
    authorization: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/revoke"):
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={
                "access_token": _token(trusted),
                "id_token": _token(
                    trusted,
                    claims=_claims(aud=CLIENT_ID, nonce=authorization["nonce"]),
                ),
                "refresh_token": "server-side-refresh-token",
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = KeycloakOidcClient(
                EnterpriseIdentityConfig(mode=TenantMode.ENTERPRISE_SERVER, keycloak=_config()),
                oidc=adapter,
                http_client=http,
            )
            login_url = await client.begin_login()
            authorization.update(
                {key: values[0] for key, values in parse_qs(urlparse(login_url).query).items()}
            )
            session = await client.complete_login(code="code", state=authorization["state"])
            logout_url = await client.logout(session.session_id)
            with pytest.raises(IdentityError) as local_error:
                client.store.get(session.session_id)
            return logout_url, local_error.value.code

    logout_url, local_code = asyncio.run(scenario())
    assert logout_url is not None
    assert "server-side-refresh-token" not in logout_url
    assert "id_token_hint" not in logout_url
    assert local_code == "ENTERPRISE_SESSION_INVALID"


def test_backchannel_logout_is_signed_fresh_and_replay_safe(signing_keys) -> None:
    trusted, _ = signing_keys
    adapter = _adapter(trusted.public_key())
    authorization: dict[str, str] = {}

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": _token(trusted),
                "id_token": _token(
                    trusted,
                    claims=_claims(aud=CLIENT_ID, nonce=authorization["nonce"]),
                ),
                "refresh_token": "refresh-token",
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = KeycloakOidcClient(
                EnterpriseIdentityConfig(mode=TenantMode.ENTERPRISE_SERVER, keycloak=_config()),
                oidc=adapter,
                http_client=http,
            )
            login_url = await client.begin_login()
            authorization.update(
                {key: values[0] for key, values in parse_qs(urlparse(login_url).query).items()}
            )
            session = await client.complete_login(code="code", state=authorization["state"])
            claims = _claims(
                aud=CLIENT_ID,
                events={"http://schemas.openid.net/event/backchannel-logout": {}},
                sid="keycloak-session-001",
                jti="logout-token-001",
            )
            claims.pop("exp")
            claims.pop("nbf")
            logout_token = _token(trusted, header={"typ": "logout+jwt"}, claims=claims)
            first = await client.consume_backchannel_logout(logout_token)
            second = await client.consume_backchannel_logout(logout_token)
            with pytest.raises(IdentityError) as local_error:
                client.store.get(session.session_id)
            return first, second, local_error.value.code

    assert asyncio.run(scenario()) == (1, 0, "ENTERPRISE_SESSION_INVALID")


def test_logout_token_enforces_purpose_nonce_absence_and_strict_future_iat(signing_keys) -> None:
    trusted, _ = signing_keys
    adapter = _adapter(trusted.public_key())
    base = _claims(
        aud=CLIENT_ID,
        events={"http://schemas.openid.net/event/backchannel-logout": {}},
        sid="keycloak-session-001",
        jti="logout-token-purpose-001",
    )
    base.pop("exp")
    base.pop("nbf")

    invalid = [
        (_token(trusted, header={"typ": "JWT"}, claims=base), "OIDC_TOKEN_TYPE_INVALID"),
        (
            _token(
                trusted,
                header={"typ": "logout+jwt"},
                claims={**base, "nonce": None},
            ),
            "OIDC_LOGOUT_TOKEN_INVALID",
        ),
        (
            _token(
                trusted,
                header={"typ": "logout+jwt"},
                claims={**base, "iat": int((NOW + timedelta(seconds=1)).timestamp())},
            ),
            "OIDC_ISSUED_AT_INVALID",
        ),
    ]
    for token, code in invalid:
        with pytest.raises(IdentityError) as caught:
            asyncio.run(adapter.validate_logout_token(token))
        assert caught.value.code == code


def test_secret_bearing_identity_objects_are_redacted_from_repr() -> None:
    config = _config(client_secret="client-secret-material")
    pending = PendingAuthorization(
        state="state-001",
        nonce="nonce-001",
        code_verifier="private-code-verifier",
        redirect_uri=config.redirect_uri,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        browser_binding_hash="browser-binding-hash",
    )
    assert "client-secret-material" not in repr(config)
    assert "private-code-verifier" not in repr(pending)
    assert "browser-binding-hash" not in repr(pending)


def test_logout_generation_fences_a_stale_refresh_write() -> None:
    store = InMemoryEnterpriseSessionStore()
    current = datetime.now(timezone.utc)
    session = EnterpriseSession(
        session_id="enterprise-session-generation-fence-001",
        principal=AuthenticatedPrincipal(
            issuer=ISSUER,
            audiences=(AUDIENCE,),
            token_id="generation-token-001",
            subject="account-001",
            issued_at=current - timedelta(minutes=1),
            expires_at=current + timedelta(minutes=5),
            authentication_time=current - timedelta(minutes=2),
            authentication_strength="mfa",
            client_id=CLIENT_ID,
        ),
        created_at=current - timedelta(minutes=1),
        refreshed_at=current - timedelta(minutes=1),
        session_expires_at=current + timedelta(hours=1),
        access_token_expires_at=current + timedelta(minutes=5),
    )
    store.put(session, refresh_token="private-refresh-token")
    snapshot = store.get(session.session_id)
    store.revoke(session.session_id)
    with pytest.raises(IdentityError) as fenced:
        store.put(
            snapshot.public.model_copy(update={"session_generation": 1}),
            refresh_token="rotated-private-refresh-token",
            expected_generation=snapshot.public.session_generation,
        )
    assert fenced.value.code == "ENTERPRISE_SESSION_FENCED"


def _refreshable_session(
    *,
    session_id: str = "refresh-session-001-opaque-identifier",
) -> EnterpriseSession:
    current = datetime.now(timezone.utc)
    return EnterpriseSession(
        session_id=session_id,
        principal=AuthenticatedPrincipal(
            issuer=ISSUER,
            audiences=(AUDIENCE,),
            token_id="refresh-token-001",
            subject="account-001",
            issued_at=current - timedelta(minutes=1),
            expires_at=current + timedelta(minutes=5),
            authentication_time=current - timedelta(minutes=2),
            authentication_strength="mfa",
            client_id=CLIENT_ID,
        ),
        created_at=current - timedelta(minutes=1),
        refreshed_at=current - timedelta(minutes=1),
        session_expires_at=current + timedelta(hours=1),
        access_token_expires_at=current + timedelta(minutes=5),
    )


def test_concurrent_refreshes_serialize_one_time_refresh_token_rotation(signing_keys) -> None:
    trusted, _ = signing_keys
    adapter = _adapter(trusted.public_key())
    store = InMemoryEnterpriseSessionStore()
    session = _refreshable_session()
    store.put(session, refresh_token="refresh-1")
    submitted_refresh_tokens: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        submitted_refresh_tokens.append(form["refresh_token"])
        await asyncio.sleep(0.01)
        sequence = len(submitted_refresh_tokens)
        return httpx.Response(
            200,
            json={
                "access_token": _token(
                    trusted,
                    claims=_claims(jti=f"refreshed-access-{sequence}"),
                ),
                "refresh_token": f"refresh-{sequence + 1}",
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = KeycloakOidcClient(
                EnterpriseIdentityConfig(mode=TenantMode.ENTERPRISE_SERVER, keycloak=_config()),
                oidc=adapter,
                store=store,
                http_client=http,
            )
            return await asyncio.gather(
                client.refresh_session(session.session_id),
                client.refresh_session(session.session_id),
            )

    first, second = asyncio.run(scenario())
    assert submitted_refresh_tokens == ["refresh-1", "refresh-2"]
    assert (first.session_generation, second.session_generation) == (1, 2)
    assert store.get_for_refresh(session.session_id).refresh_token == "refresh-3"


def test_context_rotation_waits_for_refresh_and_preserves_rotated_authority(signing_keys) -> None:
    trusted, _ = signing_keys
    adapter = _adapter(trusted.public_key())
    store = InMemoryEnterpriseSessionStore()
    session = _refreshable_session(
        session_id="refresh-context-session-001-opaque-identifier"
    )
    store.put(session, refresh_token="refresh-before-context")
    provider_entered = asyncio.Event()
    release_provider = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        provider_entered.set()
        await release_provider.wait()
        return httpx.Response(
            200,
            json={
                "access_token": _token(
                    trusted,
                    claims=_claims(jti="refresh-context-access"),
                ),
                "refresh_token": "refresh-after-context-race",
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = KeycloakOidcClient(
                EnterpriseIdentityConfig(mode=TenantMode.ENTERPRISE_SERVER, keycloak=_config()),
                oidc=adapter,
                store=store,
                http_client=http,
            )
            refresh_task = asyncio.create_task(client.refresh_session(session.session_id))
            await provider_entered.wait()
            context_task = asyncio.create_task(
                client.rotate_context(
                    session.session_id,
                    organization_id="org-a",
                    workspace_id="workspace-a",
                    expected_context_version=0,
                    expected_generation=0,
                )
            )
            await asyncio.sleep(0)
            release_provider.set()
            refreshed = await refresh_task
            with pytest.raises(IdentityError) as fenced:
                await context_task
            return refreshed, fenced.value.code

    refreshed, fence_code = asyncio.run(scenario())
    assert refreshed.session_generation == 1
    assert fence_code == "ENTERPRISE_CONTEXT_FENCED"
    assert store.get_for_refresh(session.session_id).refresh_token == (
        "refresh-after-context-race"
    )


def test_context_rotation_replaces_superseded_session_without_capacity_growth() -> None:
    store = InMemoryEnterpriseSessionStore(max_sessions=1)
    current = _refreshable_session(
        session_id="context-capacity-session-001-opaque-identifier"
    )
    store.put(current, refresh_token="refresh-context-capacity")
    for version in range(64):
        old_session_id = current.session_id
        current = store.rotate_context(
            old_session_id,
            organization_id="org-a",
            workspace_id=f"workspace-{version}",
            expected_context_version=version,
            expected_generation=0,
        )
        with pytest.raises(IdentityError):
            store.get(old_session_id)
        assert len(store._sessions) == 1
