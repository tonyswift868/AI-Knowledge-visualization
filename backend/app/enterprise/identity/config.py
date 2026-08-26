from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

from app.enterprise.contracts_v1 import TenantMode


class IdentityConfigurationError(ValueError):
    pass


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _required(env: Mapping[str, str], name: str) -> str:
    value = str(env.get(name) or "").strip()
    if not value:
        raise IdentityConfigurationError(f"{name} is required in ENTERPRISE_SERVER mode")
    return value


def _loopback(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _validated_url(value: str, *, field: str, allow_loopback_http: bool) -> str:
    parsed = urlparse(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise IdentityConfigurationError(
            f"{field} must not contain credentials, query, or fragment"
        )
    if parsed.scheme == "https" and parsed.hostname:
        return value.rstrip("/")
    if allow_loopback_http and parsed.scheme == "http" and _loopback(parsed.hostname):
        return value.rstrip("/")
    raise IdentityConfigurationError(
        f"{field} must use HTTPS; loopback HTTP requires explicit opt-in"
    )


@dataclass(frozen=True, slots=True)
class KeycloakOidcConfig:
    issuer: str
    client_id: str
    audience: str
    redirect_uri: str
    post_logout_redirect_uri: str | None = None
    client_secret: str | None = None
    allowed_algorithms: tuple[str, ...] = ("RS256",)
    scopes: tuple[str, ...] = ("openid", "profile", "email")
    clock_skew_seconds: int = 30
    introspection_enabled: bool = False
    http_timeout_seconds: float = 10.0
    jwks_cache_seconds: int = 300
    logout_token_max_age_seconds: int = 300
    max_token_lifetime_seconds: int = 86_400
    allow_loopback_http: bool = False

    def __post_init__(self) -> None:
        _validated_url(
            self.issuer,
            field="issuer",
            allow_loopback_http=self.allow_loopback_http,
        )
        _validated_url(
            self.redirect_uri,
            field="redirect_uri",
            allow_loopback_http=self.allow_loopback_http,
        )
        if self.post_logout_redirect_uri:
            _validated_url(
                self.post_logout_redirect_uri,
                field="post_logout_redirect_uri",
                allow_loopback_http=self.allow_loopback_http,
            )
        if not self.client_id.strip() or not self.audience.strip():
            raise IdentityConfigurationError("OIDC client_id and audience must not be blank")
        if "openid" not in self.scopes or len(self.scopes) != len(set(self.scopes)):
            raise IdentityConfigurationError("OIDC scopes must be unique and include openid")
        if self.allowed_algorithms != ("RS256",):
            raise IdentityConfigurationError("E1-01 currently permits RS256 only")
        if not 0 <= self.clock_skew_seconds <= 300:
            raise IdentityConfigurationError("OIDC clock skew must be between 0 and 300 seconds")
        if not 1 <= self.http_timeout_seconds <= 60:
            raise IdentityConfigurationError("OIDC HTTP timeout must be between 1 and 60 seconds")
        if not 30 <= self.jwks_cache_seconds <= 3_600:
            raise IdentityConfigurationError("OIDC JWKS cache must be between 30 and 3600 seconds")
        if not 30 <= self.logout_token_max_age_seconds <= 900:
            raise IdentityConfigurationError("OIDC logout token age must be between 30 and 900 seconds")
        if not 60 <= self.max_token_lifetime_seconds <= 86_400:
            raise IdentityConfigurationError("OIDC token lifetime limit must be between 60 seconds and 1 day")
        if self.introspection_enabled and not self.client_secret:
            raise IdentityConfigurationError("OIDC introspection requires a confidential client secret")

    @property
    def authorization_endpoint(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/auth"

    @property
    def token_endpoint(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/token"

    @property
    def jwks_uri(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/certs"

    @property
    def introspection_endpoint(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/token/introspect"

    @property
    def revocation_endpoint(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/revoke"

    @property
    def end_session_endpoint(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/logout"


@dataclass(frozen=True, slots=True)
class EnterpriseIdentityConfig:
    mode: TenantMode
    keycloak: KeycloakOidcConfig | None
    session_cookie_name: str = "mentorflow_enterprise_session"
    session_cookie_secure: bool = True
    session_ttl_seconds: int = 28_800
    pending_login_ttl_seconds: int = 300

    def __post_init__(self) -> None:
        if self.mode is TenantMode.PERSONAL_LOCAL and self.keycloak is not None:
            raise IdentityConfigurationError("PERSONAL_LOCAL must not require OIDC configuration")
        if self.mode is TenantMode.ENTERPRISE_SERVER and self.keycloak is None:
            raise IdentityConfigurationError("ENTERPRISE_SERVER requires OIDC configuration")
        if not self.session_cookie_name.strip():
            raise IdentityConfigurationError("session cookie name must not be blank")
        if not 60 <= self.session_ttl_seconds <= 604_800:
            raise IdentityConfigurationError("session TTL must be between 60 seconds and 7 days")
        if not 30 <= self.pending_login_ttl_seconds <= 900:
            raise IdentityConfigurationError("pending-login TTL must be between 30 and 900 seconds")
        if self.mode is TenantMode.ENTERPRISE_SERVER and not self.session_cookie_secure:
            keycloak = self.keycloak
            assert keycloak is not None
            if not (
                keycloak.allow_loopback_http
                and urlparse(keycloak.redirect_uri).scheme == "http"
                and _loopback(urlparse(keycloak.redirect_uri).hostname)
            ):
                raise IdentityConfigurationError(
                    "enterprise session cookies must be Secure outside loopback development"
                )

    @classmethod
    def from_env(cls, source: Mapping[str, str] | None = None) -> "EnterpriseIdentityConfig":
        env = os.environ if source is None else source
        raw_mode = str(
            env.get("MENTORFLOW_DEPLOYMENT_MODE") or TenantMode.PERSONAL_LOCAL.value
        ).strip().upper()
        try:
            mode = TenantMode(raw_mode)
        except ValueError as exc:
            raise IdentityConfigurationError(
                "MENTORFLOW_DEPLOYMENT_MODE must be PERSONAL_LOCAL or ENTERPRISE_SERVER"
            ) from exc
        if mode is TenantMode.PERSONAL_LOCAL:
            return cls(mode=mode, keycloak=None, session_cookie_secure=False)

        allow_http = _truthy(env.get("MENTORFLOW_KEYCLOAK_ALLOW_LOOPBACK_HTTP"))
        issuer = _validated_url(
            _required(env, "MENTORFLOW_KEYCLOAK_ISSUER"),
            field="MENTORFLOW_KEYCLOAK_ISSUER",
            allow_loopback_http=allow_http,
        )
        redirect_uri = _validated_url(
            _required(env, "MENTORFLOW_KEYCLOAK_REDIRECT_URI"),
            field="MENTORFLOW_KEYCLOAK_REDIRECT_URI",
            allow_loopback_http=allow_http,
        )
        logout_raw = str(env.get("MENTORFLOW_KEYCLOAK_POST_LOGOUT_REDIRECT_URI") or "").strip()
        logout_uri = (
            _validated_url(
                logout_raw,
                field="MENTORFLOW_KEYCLOAK_POST_LOGOUT_REDIRECT_URI",
                allow_loopback_http=allow_http,
            )
            if logout_raw
            else None
        )
        algorithms = tuple(
            item.strip().upper()
            for item in str(env.get("MENTORFLOW_KEYCLOAK_ALGORITHMS") or "RS256").split(",")
            if item.strip()
        )
        if algorithms != ("RS256",):
            raise IdentityConfigurationError("E1-01 currently permits RS256 only")
        try:
            skew = int(env.get("MENTORFLOW_KEYCLOAK_CLOCK_SKEW_SECONDS") or "30")
        except ValueError as exc:
            raise IdentityConfigurationError(
                "MENTORFLOW_KEYCLOAK_CLOCK_SKEW_SECONDS must be an integer"
            ) from exc
        client_id = _required(env, "MENTORFLOW_KEYCLOAK_CLIENT_ID")
        client_secret = str(env.get("MENTORFLOW_KEYCLOAK_CLIENT_SECRET") or "").strip() or None
        keycloak = KeycloakOidcConfig(
            issuer=issuer,
            client_id=client_id,
            audience=_required(env, "MENTORFLOW_KEYCLOAK_AUDIENCE"),
            redirect_uri=redirect_uri,
            post_logout_redirect_uri=logout_uri,
            client_secret=client_secret,
            allowed_algorithms=algorithms,
            clock_skew_seconds=skew,
            introspection_enabled=_truthy(env.get("MENTORFLOW_KEYCLOAK_INTROSPECTION_ENABLED")),
            allow_loopback_http=allow_http,
        )
        cookie_secure = not (allow_http and urlparse(redirect_uri).scheme == "http")
        return cls(mode=mode, keycloak=keycloak, session_cookie_secure=cookie_secure)


__all__ = [
    "EnterpriseIdentityConfig",
    "IdentityConfigurationError",
    "KeycloakOidcConfig",
]
