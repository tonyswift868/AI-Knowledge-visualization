from __future__ import annotations

import ipaddress
import os
import ssl
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlparse

from refactor.server.enterprise.contracts_v1 import TenantMode
from .issuer_migration import IssuerMigration, accepts_issuer, validate_binding, OLD_ISSUER, NEW_ISSUER
from .continuity import SignedIdentityContinuity, from_env as continuity_from_env


class IdentityConfigurationError(ValueError):
    pass


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _required(env: Mapping[str, str], name: str) -> str:
    value = str(env.get(name) or "").strip()
    if not value:
        raise IdentityConfigurationError(f"{name} is required in ENTERPRISE_SERVER mode")
    return value


def _bounded_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None,
) -> int:
    raw = str(env.get(name) or default).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise IdentityConfigurationError(f"{name} must be an integer") from exc
    if value < minimum or (maximum is not None and value > maximum):
        raise IdentityConfigurationError(
            f"{name} must be at least {minimum}" if maximum is None
            else f"{name} must be between {minimum} and {maximum}"
        )
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


def _validated_origin(value: str, *, allow_loopback_http: bool) -> str:
    normalized = _validated_url(
        value,
        field="enterprise identity browser origin",
        allow_loopback_http=allow_loopback_http,
    )
    parsed = urlparse(normalized)
    if parsed.path not in {"", "/"}:
        raise IdentityConfigurationError(
            "enterprise identity browser origins must not contain a path"
        )
    return f"{parsed.scheme}://{parsed.netloc}"


@dataclass(frozen=True, slots=True)
class KeycloakOidcConfig:
    issuer: str
    client_id: str
    audience: str
    redirect_uri: str
    post_logout_redirect_uri: str | None = None
    client_secret: str | None = field(default=None, repr=False)
    allowed_algorithms: tuple[str, ...] = ("RS256",)
    scopes: tuple[str, ...] = ("openid", "profile", "email")
    clock_skew_seconds: int = 30
    introspection_enabled: bool = False
    http_timeout_seconds: float = 10.0
    jwks_cache_seconds: int = 300
    logout_token_max_age_seconds: int = 300
    max_token_lifetime_seconds: int = 86_400
    allow_loopback_http: bool = False
    transport_issuer: str | None = None
    issuer_migration: IssuerMigration | None = None
    identity_issuer: str | None = None
    identity_continuity: SignedIdentityContinuity | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        try:
            validate_binding(self.issuer, self.identity_issuer, self.issuer_migration)
        except ValueError as exc:
            raise IdentityConfigurationError(str(exc)) from exc
        _validated_url(
            self.issuer,
            field="issuer",
            allow_loopback_http=self.allow_loopback_http,
        )
        if self.transport_issuer:
            transport = _validated_url(
                self.transport_issuer,
                field="transport_issuer",
                allow_loopback_http=self.allow_loopback_http,
            )
            public = urlparse(self.issuer)
            routed = urlparse(transport)
            paired_transport = (self.issuer_migration is not None and
                                self.issuer == NEW_ISSUER and transport == OLD_ISSUER)
            if not paired_transport and (
                routed.scheme != public.scheme
                or routed.hostname != public.hostname
                or routed.path.rstrip("/") != public.path.rstrip("/")
            ):
                raise IdentityConfigurationError(
                    "OIDC transport issuer may change only the port of the public issuer"
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
        if self.client_id.strip() == self.audience.strip():
            raise IdentityConfigurationError(
                "OIDC client_id and API audience must be distinct"
            )
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
    def tls_verification(self):
        # Keep platform trust roots as well as an existing private preview CA.
        # Never turn off certificate or hostname verification for migration.
        return ssl.create_default_context() if self.issuer_migration is not None else True

    @property
    def canonical_identity_issuer(self) -> str:
        return self.identity_issuer or self.issuer

    def accepts_issuer(self, value: str, now: float) -> bool:
        return accepts_issuer(value, self.issuer, self.issuer_migration, now)

    @property
    def authorization_endpoint(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/auth"

    @property
    def token_endpoint(self) -> str:
        return f"{self._transport_authority}/protocol/openid-connect/token"

    @property
    def jwks_uri(self) -> str:
        return f"{self._transport_authority}/protocol/openid-connect/certs"

    @property
    def introspection_endpoint(self) -> str:
        return f"{self._transport_authority}/protocol/openid-connect/token/introspect"

    @property
    def revocation_endpoint(self) -> str:
        return f"{self._transport_authority}/protocol/openid-connect/revoke"

    @property
    def end_session_endpoint(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/logout"

    @property
    def _transport_authority(self) -> str:
        return self.transport_issuer or self.issuer


@dataclass(frozen=True, slots=True)
class EnterpriseIdentityConfig:
    mode: TenantMode
    keycloak: KeycloakOidcConfig | None
    session_cookie_name: str = "__Host-mentorflow_enterprise_session"
    session_cookie_secure: bool = True
    session_ttl_seconds: int = 0  # Zero means no application-session deadline.
    pending_login_ttl_seconds: int = 300
    browser_allowed_origins: tuple[str, ...] = ()
    default_organization_id: str | None = None
    default_workspace_id: str | None = None
    session_store_mode: str = "PROCESS_LOCAL_SINGLE_REPLICA"

    def __post_init__(self) -> None:
        if self.mode is TenantMode.PERSONAL_LOCAL and self.keycloak is not None:
            raise IdentityConfigurationError("PERSONAL_LOCAL must not require OIDC configuration")
        if self.mode is TenantMode.ENTERPRISE_SERVER and self.keycloak is None:
            raise IdentityConfigurationError("ENTERPRISE_SERVER requires OIDC configuration")
        if not self.session_cookie_name.strip():
            raise IdentityConfigurationError("session cookie name must not be blank")
        if self.session_cookie_name.startswith("__Host-") and not self.session_cookie_secure:
            raise IdentityConfigurationError("__Host- session cookies must be Secure")
        if self.session_ttl_seconds != 0 and self.session_ttl_seconds < 60:
            raise IdentityConfigurationError("session TTL must be zero (unlimited) or at least 60 seconds")
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
        if len(self.browser_allowed_origins) != len(set(self.browser_allowed_origins)):
            raise IdentityConfigurationError("enterprise identity browser origins must be unique")
        if self.mode is TenantMode.ENTERPRISE_SERVER and not self.browser_allowed_origins:
            keycloak = self.keycloak
            assert keycloak is not None
            parsed_redirect = urlparse(keycloak.redirect_uri)
            object.__setattr__(
                self,
                "browser_allowed_origins",
                (f"{parsed_redirect.scheme}://{parsed_redirect.netloc}",),
            )
        if self.default_workspace_id and not self.default_organization_id:
            raise IdentityConfigurationError(
                "default enterprise workspace requires a default organization"
            )
        if self.session_store_mode != "PROCESS_LOCAL_SINGLE_REPLICA":
            raise IdentityConfigurationError(
                "E1-01 currently supports only PROCESS_LOCAL_SINGLE_REPLICA sessions"
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
            return cls(
                mode=mode,
                keycloak=None,
                session_cookie_name="mentorflow_enterprise_session",
                session_cookie_secure=False,
            )

        allow_http = _truthy(env.get("MENTORFLOW_KEYCLOAK_ALLOW_LOOPBACK_HTTP"))
        issuer = _validated_url(
            _required(env, "MENTORFLOW_KEYCLOAK_ISSUER"),
            field="MENTORFLOW_KEYCLOAK_ISSUER",
            allow_loopback_http=allow_http,
        )
        transport_raw = str(
            env.get("MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER") or ""
        ).strip()
        transport_issuer = (
            _validated_url(
                transport_raw,
                field="MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER",
                allow_loopback_http=allow_http,
            )
            if transport_raw
            else None
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
        skew = _bounded_int(
            env,
            "MENTORFLOW_KEYCLOAK_CLOCK_SKEW_SECONDS",
            30,
            minimum=0,
            maximum=300,
        )
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
            http_timeout_seconds=_bounded_int(
                env,
                "MENTORFLOW_KEYCLOAK_HTTP_TIMEOUT_SECONDS",
                10,
                minimum=1,
                maximum=60,
            ),
            jwks_cache_seconds=_bounded_int(
                env,
                "MENTORFLOW_KEYCLOAK_JWKS_CACHE_SECONDS",
                300,
                minimum=30,
                maximum=3_600,
            ),
            logout_token_max_age_seconds=_bounded_int(
                env,
                "MENTORFLOW_KEYCLOAK_LOGOUT_TOKEN_MAX_AGE_SECONDS",
                300,
                minimum=30,
                maximum=900,
            ),
            max_token_lifetime_seconds=_bounded_int(
                env,
                "MENTORFLOW_KEYCLOAK_MAX_TOKEN_LIFETIME_SECONDS",
                86_400,
                minimum=60,
                maximum=86_400,
            ),
            allow_loopback_http=allow_http,
            transport_issuer=transport_issuer,
            issuer_migration=IssuerMigration.from_json(env.get('MENTORFLOW_KEYCLOAK_ISSUER_MIGRATION_JSON')),
            identity_issuer=env.get('MENTORFLOW_KEYCLOAK_IDENTITY_ISSUER') or None,
            identity_continuity=continuity_from_env(env),
        )
        cookie_secure = not (allow_http and urlparse(redirect_uri).scheme == "http")
        redirect = urlparse(redirect_uri)
        default_origin = f"{redirect.scheme}://{redirect.netloc}"
        origin_values = tuple(
            _validated_origin(item.strip(), allow_loopback_http=allow_http)
            for item in str(
                env.get("MENTORFLOW_IDENTITY_ALLOWED_ORIGINS") or default_origin
            ).split(",")
            if item.strip()
        )
        cookie_name = str(env.get("MENTORFLOW_IDENTITY_SESSION_COOKIE_NAME") or "").strip()
        if not cookie_name:
            cookie_name = (
                "__Host-mentorflow_enterprise_session"
                if cookie_secure
                else "mentorflow_enterprise_session"
            )
        return cls(
            mode=mode,
            keycloak=keycloak,
            session_cookie_name=cookie_name,
            session_cookie_secure=cookie_secure,
            session_ttl_seconds=_bounded_int(
                env,
                "MENTORFLOW_IDENTITY_SESSION_TTL_SECONDS",
                0,
                minimum=0,
                maximum=None,
            ),
            pending_login_ttl_seconds=_bounded_int(
                env,
                "MENTORFLOW_IDENTITY_PENDING_LOGIN_TTL_SECONDS",
                300,
                minimum=30,
                maximum=900,
            ),
            browser_allowed_origins=origin_values,
            default_organization_id=str(
                env.get("MENTORFLOW_ENTERPRISE_DEFAULT_ORGANIZATION_ID") or ""
            ).strip()
            or None,
            default_workspace_id=str(
                env.get("MENTORFLOW_ENTERPRISE_DEFAULT_WORKSPACE_ID") or ""
            ).strip()
            or None,
            session_store_mode=str(
                env.get("MENTORFLOW_IDENTITY_SESSION_STORE")
                or "PROCESS_LOCAL_SINGLE_REPLICA"
            ).strip().upper(),
        )


__all__ = [
    "EnterpriseIdentityConfig",
    "IdentityConfigurationError",
    "KeycloakOidcConfig",
]
