"""Fail-closed Keycloak OIDC token validation without a new dependency."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.hashes import SHA256

from app.enterprise.contracts_v1 import ActorKind

from .config import KeycloakOidcConfig
from .models import AuthenticatedPrincipal, EnterpriseSession


_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_BACKCHANNEL_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"


class IdentityError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 401) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class SigningKeyResolver(Protocol):
    async def signing_key(
        self,
        *,
        key_id: str,
        algorithm: str,
    ) -> rsa.RSAPublicKey: ...


class IdentityProviderPort(Protocol):
    async def begin_login(self) -> str: ...

    async def complete_login(self, *, code: str, state: str) -> EnterpriseSession: ...

    async def refresh_session(self, session_id: str) -> EnterpriseSession: ...

    async def logout(self, session_id: str) -> str | None: ...

    async def authenticate_access_token(self, token: str) -> AuthenticatedPrincipal: ...


def _decode_base64url(value: str, *, field: str, max_bytes: int) -> bytes:
    if not value or len(value) > max_bytes * 2 or _BASE64URL.fullmatch(value) is None:
        raise IdentityError("OIDC_TOKEN_INVALID", f"OIDC {field} is malformed")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError) as exc:
        raise IdentityError("OIDC_TOKEN_INVALID", f"OIDC {field} is malformed") from exc
    if len(decoded) > max_bytes:
        raise IdentityError("OIDC_TOKEN_INVALID", f"OIDC {field} is too large")
    return decoded


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _json_object(data: bytes, *, field: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=_reject_non_finite_json,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise IdentityError("OIDC_TOKEN_INVALID", f"OIDC {field} is invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise IdentityError("OIDC_TOKEN_INVALID", f"OIDC {field} must be an object")
    return value


@dataclass(frozen=True, slots=True)
class _ParsedJwt:
    header: Mapping[str, Any]
    claims: Mapping[str, Any]
    signing_input: bytes
    signature: bytes


def _parse_jwt(token: str) -> _ParsedJwt:
    if not token or len(token) > 24_000 or any(character.isspace() for character in token):
        raise IdentityError("OIDC_TOKEN_INVALID", "OIDC token is missing, too large, or malformed")
    parts = token.split(".")
    if len(parts) != 3:
        raise IdentityError("OIDC_TOKEN_INVALID", "OIDC token must have three segments")
    header_segment, claim_segment, signature_segment = parts
    try:
        signing_input = f"{header_segment}.{claim_segment}".encode("ascii")
    except UnicodeEncodeError as exc:
        raise IdentityError("OIDC_TOKEN_INVALID", "OIDC token is not ASCII") from exc
    return _ParsedJwt(
        header=_json_object(
            _decode_base64url(header_segment, field="header", max_bytes=8_192),
            field="header",
        ),
        claims=_json_object(
            _decode_base64url(claim_segment, field="claims", max_bytes=16_384),
            field="claims",
        ),
        signing_input=signing_input,
        signature=_decode_base64url(signature_segment, field="signature", max_bytes=2_048),
    )


def _required_text(claims: Mapping[str, Any], name: str, *, max_length: int = 500) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise IdentityError("OIDC_CLAIM_INVALID", f"OIDC {name} claim is invalid")
    return value.strip()


def _optional_text(claims: Mapping[str, Any], name: str, *, max_length: int = 500) -> str | None:
    value = claims.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise IdentityError("OIDC_CLAIM_INVALID", f"OIDC {name} claim is invalid")
    return value.strip()


def _numeric_date(claims: Mapping[str, Any], name: str, *, required: bool = True) -> datetime | None:
    value = claims.get(name)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IdentityError("OIDC_CLAIM_INVALID", f"OIDC {name} claim is invalid")
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise IdentityError("OIDC_CLAIM_INVALID", f"OIDC {name} claim is invalid") from exc


def _audiences(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    if (
        isinstance(value, list)
        and 1 <= len(value) <= 32
        and all(isinstance(item, str) and item.strip() for item in value)
    ):
        normalized = tuple(item.strip() for item in value)
        if len(normalized) == len(set(normalized)):
            return normalized
    raise IdentityError("OIDC_CLAIM_INVALID", "OIDC aud claim is invalid")


class HttpJwksSigningKeyResolver:
    """Bounded JWKS cache with one forced refresh for Keycloak key rotation."""

    def __init__(
        self,
        config: KeycloakOidcConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._http = http_client or httpx.AsyncClient(
            timeout=config.http_timeout_seconds,
            follow_redirects=False,
        )
        self._owns_http = http_client is None
        self._clock = monotonic_clock
        self._keys: dict[str, rsa.RSAPublicKey] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    @staticmethod
    def _rsa_key(entry: Mapping[str, Any]) -> rsa.RSAPublicKey:
        modulus_raw = entry.get("n")
        exponent_raw = entry.get("e")
        if not isinstance(modulus_raw, str) or not isinstance(exponent_raw, str):
            raise IdentityError("OIDC_JWKS_INVALID", "OIDC RSA key is missing n or e", status=503)
        try:
            modulus = int.from_bytes(
                _decode_base64url(modulus_raw, field="JWK modulus", max_bytes=1_024),
                "big",
            )
            exponent = int.from_bytes(
                _decode_base64url(exponent_raw, field="JWK exponent", max_bytes=16),
                "big",
            )
        except IdentityError as exc:
            raise IdentityError(
                "OIDC_JWKS_INVALID",
                "OIDC RSA key parameters are malformed",
                status=503,
            ) from exc
        if not 2_048 <= modulus.bit_length() <= 8_192 or exponent < 3 or exponent % 2 == 0:
            raise IdentityError("OIDC_JWKS_INVALID", "OIDC RSA key parameters are unsafe", status=503)
        try:
            return rsa.RSAPublicNumbers(exponent, modulus).public_key()
        except ValueError as exc:
            raise IdentityError("OIDC_JWKS_INVALID", "OIDC RSA key parameters are invalid", status=503) from exc

    async def _refresh(self, *, force: bool) -> None:
        async with self._lock:
            if not force and self._keys and self._clock() < self._expires_at:
                return
            try:
                response = await self._http.get(
                    self._config.jwks_uri,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise IdentityError("OIDC_JWKS_UNAVAILABLE", "OIDC signing keys are unavailable", status=503) from exc
            if len(response.content) > 1_048_576:
                raise IdentityError("OIDC_JWKS_INVALID", "OIDC JWKS response is too large", status=503)
            try:
                payload = json.loads(
                    response.content.decode("utf-8"),
                    object_pairs_hook=_reject_duplicate_object,
                    parse_constant=_reject_non_finite_json,
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise IdentityError("OIDC_JWKS_INVALID", "OIDC JWKS response is invalid", status=503) from exc
            entries = payload.get("keys") if isinstance(payload, Mapping) else None
            if not isinstance(entries, list) or not 1 <= len(entries) <= 32:
                raise IdentityError("OIDC_JWKS_INVALID", "OIDC JWKS keys are invalid", status=503)
            keys: dict[str, rsa.RSAPublicKey] = {}
            seen_key_ids: set[str] = set()
            for raw_entry in entries:
                if not isinstance(raw_entry, Mapping):
                    raise IdentityError("OIDC_JWKS_INVALID", "OIDC JWK entry is invalid", status=503)
                key_id = raw_entry.get("kid")
                if not isinstance(key_id, str) or not key_id.strip() or len(key_id) > 300:
                    raise IdentityError("OIDC_JWKS_INVALID", "OIDC JWK key id is invalid", status=503)
                key_id = key_id.strip()
                if key_id in seen_key_ids:
                    raise IdentityError("OIDC_JWKS_INVALID", "OIDC JWK key ids must be unique", status=503)
                seen_key_ids.add(key_id)
                if raw_entry.get("kty") != "RSA":
                    continue
                if raw_entry.get("use") not in {None, "sig"}:
                    continue
                if raw_entry.get("alg") not in {None, "RS256"}:
                    continue
                keys[key_id] = self._rsa_key(raw_entry)
            if not keys:
                raise IdentityError("OIDC_JWKS_INVALID", "OIDC JWKS has no acceptable signing key", status=503)
            self._keys = keys
            self._expires_at = self._clock() + self._config.jwks_cache_seconds

    async def signing_key(
        self,
        *,
        key_id: str,
        algorithm: str,
    ) -> rsa.RSAPublicKey:
        if algorithm != "RS256":
            raise IdentityError("OIDC_ALGORITHM_REJECTED", "OIDC signing algorithm is not allowed")
        await self._refresh(force=False)
        key = self._keys.get(key_id)
        if key is None:
            await self._refresh(force=True)
            key = self._keys.get(key_id)
        if key is None:
            raise IdentityError("OIDC_SIGNING_KEY_NOT_FOUND", "OIDC signing key was not found")
        return key

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()


class OidcIdentityAdapter:
    """Verify signatures and registered claims before producing a principal."""

    def __init__(
        self,
        config: KeycloakOidcConfig,
        *,
        signing_keys: SigningKeyResolver | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._signing_keys = signing_keys or HttpJwksSigningKeyResolver(config)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def _verify(
        self,
        token: str,
        *,
        required_claims: tuple[str, ...],
        require_expiry: bool,
        expected_audience: str,
    ) -> Mapping[str, Any]:
        parsed = _parse_jwt(token)
        algorithm = _required_text(parsed.header, "alg", max_length=20)
        if algorithm not in self.config.allowed_algorithms:
            raise IdentityError("OIDC_ALGORITHM_REJECTED", "OIDC signing algorithm is not allowed")
        if parsed.header.get("crit") is not None:
            raise IdentityError("OIDC_HEADER_UNSUPPORTED", "OIDC critical headers are not supported")
        token_type = parsed.header.get("typ")
        if token_type is not None and (
            not isinstance(token_type, str) or not token_type.strip() or len(token_type) > 100
        ):
            raise IdentityError("OIDC_HEADER_UNSUPPORTED", "OIDC token type is invalid")
        key_id = _required_text(parsed.header, "kid", max_length=300)
        key = await self._signing_keys.signing_key(key_id=key_id, algorithm=algorithm)
        if not isinstance(key, rsa.RSAPublicKey):
            raise IdentityError("OIDC_SIGNING_KEY_INVALID", "OIDC signing key is not RSA")
        try:
            key.verify(parsed.signature, parsed.signing_input, padding.PKCS1v15(), SHA256())
        except InvalidSignature as exc:
            raise IdentityError("OIDC_SIGNATURE_INVALID", "OIDC token signature is invalid") from exc

        for name in required_claims:
            if name not in parsed.claims:
                raise IdentityError("OIDC_CLAIM_REQUIRED", f"OIDC {name} claim is required")
        issuer = _required_text(parsed.claims, "iss")
        if issuer != self.config.issuer:
            raise IdentityError("OIDC_ISSUER_INVALID", "OIDC token issuer is invalid")
        audiences = _audiences(parsed.claims.get("aud"))
        if expected_audience not in audiences:
            raise IdentityError("OIDC_AUDIENCE_INVALID", "OIDC token audience is invalid")
        authorized_party = _optional_text(parsed.claims, "azp", max_length=300)
        if len(audiences) > 1 and authorized_party is None:
            raise IdentityError("OIDC_AUTHORIZED_PARTY_REQUIRED", "OIDC azp is required for multiple audiences")
        if authorized_party is not None and authorized_party != self.config.client_id:
            raise IdentityError("OIDC_AUTHORIZED_PARTY_INVALID", "OIDC authorized party is invalid")

        current = self._clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise RuntimeError("OIDC validation clock must include a UTC offset")
        skew = timedelta(seconds=self.config.clock_skew_seconds)
        issued_at = _numeric_date(parsed.claims, "iat")
        assert issued_at is not None
        if issued_at > current + skew:
            raise IdentityError("OIDC_ISSUED_AT_INVALID", "OIDC token issuance is in the future")
        not_before = _numeric_date(parsed.claims, "nbf", required=False)
        if not_before is not None and not_before > current + skew:
            raise IdentityError("OIDC_TOKEN_NOT_ACTIVE", "OIDC token is not active yet")
        expires_at = _numeric_date(parsed.claims, "exp", required=require_expiry)
        if require_expiry and expires_at is not None and expires_at <= current - skew:
            raise IdentityError("OIDC_TOKEN_EXPIRED", "OIDC token has expired")
        if require_expiry and expires_at is not None:
            if expires_at <= issued_at:
                raise IdentityError("OIDC_CLAIM_INVALID", "OIDC token expiry must follow issuance")
            maximum = timedelta(seconds=self.config.max_token_lifetime_seconds) + skew
            if expires_at - issued_at > maximum:
                raise IdentityError("OIDC_TOKEN_LIFETIME_INVALID", "OIDC token lifetime is too long")
        return parsed.claims

    def _principal(self, claims: Mapping[str, Any]) -> AuthenticatedPrincipal:
        issued_at = _numeric_date(claims, "iat")
        expires_at = _numeric_date(claims, "exp")
        assert issued_at is not None and expires_at is not None
        authentication_time = _numeric_date(claims, "auth_time", required=False) or issued_at
        client_id = _optional_text(claims, "azp", max_length=300) or _optional_text(
            claims,
            "client_id",
            max_length=300,
        )
        preferred_username = _optional_text(claims, "preferred_username", max_length=300)
        is_service = bool(
            client_id
            and preferred_username
            and preferred_username == f"service-account-{client_id}"
        )
        return AuthenticatedPrincipal(
            issuer=_required_text(claims, "iss"),
            audiences=_audiences(claims.get("aud")),
            token_id=_required_text(claims, "jti", max_length=300),
            subject=_required_text(claims, "sub", max_length=300),
            issued_at=issued_at,
            expires_at=expires_at,
            authentication_time=authentication_time,
            authentication_strength=_optional_text(claims, "acr", max_length=160) or "oidc",
            not_before=_numeric_date(claims, "nbf", required=False),
            identity_provider_session_id=_optional_text(claims, "sid", max_length=300),
            preferred_username=preferred_username,
            email=_optional_text(claims, "email", max_length=320),
            display_name=_optional_text(claims, "name", max_length=300),
            client_id=client_id,
            actor_kind=ActorKind.SERVICE if is_service else ActorKind.HUMAN,
        )

    async def validate_access_token(self, token: str) -> AuthenticatedPrincipal:
        claims = await self._verify(
            token,
            required_claims=("iss", "aud", "exp", "iat", "jti", "sub"),
            require_expiry=True,
            expected_audience=self.config.audience,
        )
        return self._principal(claims)

    async def validate_id_token(
        self,
        token: str,
        *,
        expected_nonce: str,
    ) -> AuthenticatedPrincipal:
        claims = await self._verify(
            token,
            required_claims=("iss", "aud", "exp", "iat", "jti", "sub", "nonce"),
            require_expiry=True,
            expected_audience=self.config.client_id,
        )
        if not expected_nonce or claims.get("nonce") != expected_nonce:
            raise IdentityError("OIDC_NONCE_INVALID", "OIDC login nonce is invalid")
        return self._principal(claims)

    async def validate_logout_token(self, token: str) -> Mapping[str, Any]:
        claims = await self._verify(
            token,
            required_claims=("iss", "aud", "iat", "jti", "events"),
            require_expiry=False,
            expected_audience=self.config.client_id,
        )
        events = claims.get("events")
        logout_event = events.get(_BACKCHANNEL_LOGOUT_EVENT) if isinstance(events, Mapping) else None
        if not isinstance(logout_event, Mapping) or logout_event:
            raise IdentityError("OIDC_LOGOUT_EVENT_INVALID", "OIDC logout event is invalid")
        subject = _optional_text(claims, "sub", max_length=300)
        session_id = _optional_text(claims, "sid", max_length=300)
        _required_text(claims, "jti", max_length=300)
        if claims.get("nonce") is not None or not (session_id or subject):
            raise IdentityError("OIDC_LOGOUT_TOKEN_INVALID", "OIDC logout token shape is invalid")
        issued_at = _numeric_date(claims, "iat")
        assert issued_at is not None
        current = self._clock()
        max_age = timedelta(
            seconds=self.config.logout_token_max_age_seconds + self.config.clock_skew_seconds
        )
        if current - issued_at > max_age:
            raise IdentityError("OIDC_LOGOUT_TOKEN_EXPIRED", "OIDC logout token is too old")
        return claims

    async def aclose(self) -> None:
        close = getattr(self._signing_keys, "aclose", None)
        if close is not None:
            await close()


__all__ = [
    "HttpJwksSigningKeyResolver",
    "IdentityError",
    "IdentityProviderPort",
    "OidcIdentityAdapter",
    "SigningKeyResolver",
]
