"""Native Desktop OIDC Authorization Code + PKCE account flow.

An OIDC user-agent owns interactive authentication.  MentorFlow receives
exactly one loopback callback, validates the OIDC tokens, refreshes
tenant-scoped server state, and only then exposes an account identity to the
Desktop lifecycle. Both E109 OLD and NEW configurations use the system browser;
other legacy providers retain their isolated Qt WebEngine user-agent. The
protocol core remains UI-agnostic.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import ssl
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx

from .account_identity import (
    AccountCredentialVault,
    AccountIdentity,
    AccountProfile,
    ProfileMode,
    canonicalize_issuer,
    issuer_hash,
)


MAX_JSON_BYTES = 1_048_576
DEFAULT_CALLBACK_PATH = "/oauth/callback"


def _migration_contract():
    # Same existing server verifier/config package used by BackendOidcTokenValidator.
    backend_root = Path(__file__).resolve().parents[1] / 'backend'
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    from app.enterprise.identity import issuer_migration
    return issuer_migration


class EnterpriseOidcError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code or "ENTERPRISE_OIDC_FAILED")
        self.message = str(message or "Enterprise OIDC failed")
        super().__init__(self.message)


def _required(value: object, field_name: str, *, limit: int = 2_048) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > limit or any(ord(char) < 32 for char in normalized):
        raise EnterpriseOidcError(
            "ENTERPRISE_OIDC_CONFIG_INVALID",
            f"{field_name} is required",
        )
    return normalized


def _loopback(host: str | None) -> bool:
    return str(host or "").casefold() in {"127.0.0.1", "::1", "localhost"}


def _validated_endpoint(
    value: object,
    field_name: str,
    *,
    allow_loopback_http: bool = False,
) -> str:
    raw = _required(value, field_name)
    parsed = urlsplit(raw)
    allowed_http = (
        allow_loopback_http
        and parsed.scheme.casefold() == "http"
        and _loopback(parsed.hostname)
    )
    if (
        (parsed.scheme.casefold() != "https" and not allowed_http)
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise EnterpriseOidcError(
            "ENTERPRISE_OIDC_CONFIG_INVALID",
            f"{field_name} must be a safe HTTPS URL",
        )
    return parsed.geturl().rstrip("/")


def _trusted_provider_endpoint(value: object, field_name: str, issuer: str) -> str:
    endpoint = _validated_endpoint(value, field_name)
    parsed = urlsplit(endpoint)
    issuer_url = urlsplit(issuer)
    if (
        parsed.scheme.casefold() != issuer_url.scheme.casefold()
        or parsed.hostname.casefold() != issuer_url.hostname.casefold()
        or parsed.port != issuer_url.port
        or parsed.query
    ):
        raise EnterpriseOidcError(
            "ENTERPRISE_OIDC_DISCOVERY_INVALID",
            f"{field_name} is outside the configured issuer",
        )
    return endpoint


def _positive_int(value: object, field_name: str, *, minimum: int, maximum: int) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError) as exc:
        raise EnterpriseOidcError(
            "ENTERPRISE_OIDC_CONFIG_INVALID",
            f"{field_name} is invalid",
        ) from exc
    if not minimum <= resolved <= maximum:
        raise EnterpriseOidcError(
            "ENTERPRISE_OIDC_CONFIG_INVALID",
            f"{field_name} is outside the supported range",
        )
    return resolved


@dataclass(frozen=True, slots=True)
class DesktopOidcConfig:
    issuer: str
    client_id: str
    audience: str
    api_base_url: str
    organization_id: str = ""
    workspace_id: str = ""
    callback_host: str = "127.0.0.1"
    callback_port: int = 0
    callback_path: str = DEFAULT_CALLBACK_PATH
    scopes: tuple[str, ...] = ("openid", "profile", "email")
    request_timeout_seconds: float = 10.0
    callback_timeout_seconds: float = 180.0
    allow_loopback_api_http: bool = False
    issuer_migration: Any = None
    identity_issuer: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "issuer", canonicalize_issuer(self.issuer))
        try:
            _migration_contract().validate_binding(self.issuer, self.identity_issuer, self.issuer_migration)
        except ValueError as exc:
            raise EnterpriseOidcError('ENTERPRISE_OIDC_CONFIG_INVALID', str(exc)) from exc
        object.__setattr__(self, "client_id", _required(self.client_id, "client_id", limit=300))
        object.__setattr__(self, "audience", _required(self.audience, "audience", limit=300))
        if self.client_id == self.audience:
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CONFIG_INVALID",
                "Desktop OIDC client and API audience must be distinct",
            )
        api_base_url = _validated_endpoint(
            self.api_base_url,
            "api_base_url",
            allow_loopback_http=self.allow_loopback_api_http,
        )
        if urlsplit(api_base_url).query:
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CONFIG_INVALID",
                "api_base_url must not include a query",
            )
        object.__setattr__(self, "api_base_url", api_base_url)
        if self.callback_host != "127.0.0.1":
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CONFIG_INVALID",
                "Desktop callback must bind IPv4 loopback",
            )
        if not 0 <= int(self.callback_port) <= 65_535:
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CONFIG_INVALID",
                "callback_port is invalid",
            )
        callback_path = str(self.callback_path or "").strip()
        if (
            not callback_path.startswith("/")
            or callback_path.startswith("//")
            or "?" in callback_path
            or "#" in callback_path
            or len(callback_path) > 200
        ):
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CONFIG_INVALID",
                "callback_path is invalid",
            )
        object.__setattr__(self, "callback_path", callback_path)
        normalized_scopes = tuple(dict.fromkeys(str(item).strip() for item in self.scopes))
        if "openid" not in normalized_scopes or any(not item for item in normalized_scopes):
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CONFIG_INVALID",
                "OIDC scopes must include openid",
            )
        object.__setattr__(self, "scopes", normalized_scopes)
        if not 1 <= float(self.request_timeout_seconds) <= 60:
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CONFIG_INVALID",
                "request timeout is invalid",
            )
        if not 15 <= float(self.callback_timeout_seconds) <= 600:
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CONFIG_INVALID",
                "callback timeout is invalid",
            )
        if self.workspace_id and not self.organization_id:
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CONFIG_INVALID",
                "workspace selection requires an organization",
            )
        if self.organization_id:
            object.__setattr__(
                self,
                "organization_id",
                _required(self.organization_id, "organization_id", limit=240),
            )
        if self.workspace_id:
            object.__setattr__(
                self,
                "workspace_id",
                _required(self.workspace_id, "workspace_id", limit=240),
            )

    @property
    def canonical_identity_issuer(self) -> str:
        return self.identity_issuer or self.issuer

    @property
    def requires_system_browser(self) -> bool:
        # LEGACY-only user-agent selection. The default native credential path
        # never consults this property; issuer trust remains independent.
        contract = _migration_contract()
        return self.issuer in (contract.OLD_ISSUER, contract.NEW_ISSUER)

    def accepts_issuer(self, value: str) -> bool:
        return _migration_contract().accepts_issuer(value, self.issuer, self.issuer_migration, time.time())

    @property
    def discovery_url(self) -> str:
        return f"{self.issuer}/.well-known/openid-configuration"

    @classmethod
    def is_configured(cls, source: Mapping[str, str] | None = None) -> bool:
        env = os.environ if source is None else source
        desktop_configured = any(
            str(env.get(name) or "").strip()
            for name in (
                "MENTORFLOW_DESKTOP_OIDC_ISSUER",
                "MENTORFLOW_DESKTOP_OIDC_CLIENT_ID",
                "MENTORFLOW_DESKTOP_OIDC_AUDIENCE",
                "MENTORFLOW_ENTERPRISE_API_BASE_URL",
            )
        )
        server_compatible = bool(
            str(env.get("MENTORFLOW_ENTERPRISE_API_BASE_URL") or "").strip()
            and str(env.get("MENTORFLOW_KEYCLOAK_ISSUER") or "").strip()
            and str(env.get("MENTORFLOW_KEYCLOAK_CLIENT_ID") or "").strip()
            and str(env.get("MENTORFLOW_KEYCLOAK_AUDIENCE") or "").strip()
        )
        return desktop_configured or server_compatible

    @classmethod
    def from_env(cls, source: Mapping[str, str] | None = None) -> "DesktopOidcConfig":
        env = os.environ if source is None else source
        allow_http = str(
            env.get("MENTORFLOW_DESKTOP_ALLOW_LOOPBACK_API_HTTP") or ""
        ).strip().casefold() in {"1", "true", "yes", "on"}
        return cls(
            issuer=str(
                env.get("MENTORFLOW_DESKTOP_OIDC_ISSUER")
                or env.get("MENTORFLOW_KEYCLOAK_ISSUER")
                or ""
            ),
            client_id=str(
                env.get("MENTORFLOW_DESKTOP_OIDC_CLIENT_ID")
                or env.get("MENTORFLOW_KEYCLOAK_CLIENT_ID")
                or ""
            ),
            audience=str(
                env.get("MENTORFLOW_DESKTOP_OIDC_AUDIENCE")
                or env.get("MENTORFLOW_KEYCLOAK_AUDIENCE")
                or ""
            ),
            api_base_url=str(env.get("MENTORFLOW_ENTERPRISE_API_BASE_URL") or ""),
            organization_id=str(
                env.get("MENTORFLOW_DESKTOP_ORGANIZATION_ID") or ""
            ).strip(),
            workspace_id=str(env.get("MENTORFLOW_DESKTOP_WORKSPACE_ID") or "").strip(),
            callback_port=_positive_int(
                env.get("MENTORFLOW_DESKTOP_OIDC_CALLBACK_PORT") or 0,
                "MENTORFLOW_DESKTOP_OIDC_CALLBACK_PORT",
                minimum=0,
                maximum=65_535,
            ),
            request_timeout_seconds=float(
                env.get("MENTORFLOW_DESKTOP_OIDC_HTTP_TIMEOUT_SECONDS") or 10
            ),
            callback_timeout_seconds=float(
                env.get("MENTORFLOW_DESKTOP_OIDC_CALLBACK_TIMEOUT_SECONDS") or 180
            ),
            allow_loopback_api_http=allow_http,
            issuer_migration=_migration_contract().IssuerMigration.from_json(env.get('MENTORFLOW_DESKTOP_ISSUER_MIGRATION_JSON')),
            identity_issuer=env.get('MENTORFLOW_DESKTOP_IDENTITY_ISSUER') or None,
        )


@dataclass(frozen=True, slots=True)
class OidcDiscovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


def bind_auth_authority(environment, authority):
    """Apply only signed build auth configuration, leaving API/callback untouched."""
    result = dict(environment)
    if authority is None:
        return result
    contract = _migration_contract()
    if (not isinstance(authority, dict) or set(authority) != {'issuer', 'identity_issuer', 'migration'} or
            authority['issuer'] != contract.NEW_ISSUER or authority['identity_issuer'] != contract.OLD_ISSUER):
        raise EnterpriseOidcError('ENTERPRISE_OIDC_CONFIG_INVALID', 'Unapproved auth authority')
    migration = contract.IssuerMigration.from_json(json.dumps(authority['migration']))
    if migration is None:
        raise EnterpriseOidcError('ENTERPRISE_OIDC_CONFIG_INVALID', 'Explicit migration window required')
    result.update(MENTORFLOW_DESKTOP_OIDC_ISSUER=contract.NEW_ISSUER,
                  MENTORFLOW_DESKTOP_IDENTITY_ISSUER=contract.OLD_ISSUER,
                  MENTORFLOW_DESKTOP_ISSUER_MIGRATION_JSON=json.dumps(authority['migration']))
    DesktopOidcConfig.from_env(result)  # Validate before a consumer can use it.
    return result


@dataclass(frozen=True, slots=True)
class ValidatedTokenIdentity:
    issuer: str
    subject: str
    display_name: str


class OidcTokenValidator(Protocol):
    async def validate_login(
        self,
        *,
        id_token: str,
        access_token: str,
        expected_nonce: str,
    ) -> ValidatedTokenIdentity: ...

    async def validate_access(self, access_token: str) -> ValidatedTokenIdentity: ...

    async def aclose(self) -> None: ...


class BackendOidcTokenValidator:
    """Reuse the frozen server JWT/JWKS verifier without copying auth rules."""

    def __init__(
        self,
        config: DesktopOidcConfig,
        *,
        redirect_uri: str,
        discovery: OidcDiscovery,
    ) -> None:
        backend_root = Path(__file__).resolve().parents[1] / "backend"
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))
        from app.enterprise.identity.config import KeycloakOidcConfig
        from app.enterprise.identity.oidc import OidcIdentityAdapter

        keycloak = KeycloakOidcConfig(
            issuer=config.issuer,
            client_id=config.client_id,
            audience=config.audience,
            redirect_uri=redirect_uri,
            allow_loopback_http=True,
            http_timeout_seconds=config.request_timeout_seconds,
            issuer_migration=config.issuer_migration,
            identity_issuer=config.identity_issuer,
            transport_issuer=discovery.issuer if discovery.issuer != config.issuer else None,
        )
        if not config.accepts_issuer(discovery.issuer):
            raise EnterpriseOidcError('ENTERPRISE_OIDC_DISCOVERY_INVALID', 'Unapproved discovery issuer')
        if (
            discovery.authorization_endpoint != discovery.issuer + '/protocol/openid-connect/auth'
            or discovery.token_endpoint != discovery.issuer + '/protocol/openid-connect/token'
            or discovery.jwks_uri != discovery.issuer + '/protocol/openid-connect/certs'
        ):
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_DISCOVERY_INVALID",
                "OIDC discovery does not match the configured Keycloak issuer",
            )
        # Desktop verifies external evidence only. The authenticated backend
        # /auth/me projection below owns cross-issuer account continuity.
        self._adapter = OidcIdentityAdapter(keycloak, external_identity_only=True)

    @staticmethod
    def _identity(principal: Any) -> ValidatedTokenIdentity:
        display = (
            str(getattr(principal, "display_name", "") or "").strip()
            or str(getattr(principal, "preferred_username", "") or "").strip()
            or str(getattr(principal, "email", "") or "").strip()
            or "Enterprise Account"
        )
        return ValidatedTokenIdentity(
            issuer=str(principal.issuer),
            subject=str(principal.subject),
            display_name=display,
        )

    async def validate_login(
        self,
        *,
        id_token: str,
        access_token: str,
        expected_nonce: str,
    ) -> ValidatedTokenIdentity:
        id_principal = await self._adapter.validate_id_token(
            id_token,
            expected_nonce=expected_nonce,
        )
        access_principal = await self._adapter.validate_access_token(access_token)
        if (
            id_principal.issuer != access_principal.issuer
            or id_principal.subject != access_principal.subject
        ):
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_SUBJECT_MISMATCH",
                "OIDC ID and access tokens identify different accounts",
            )
        return self._identity(id_principal)

    async def validate_access(self, access_token: str) -> ValidatedTokenIdentity:
        return self._identity(await self._adapter.validate_access_token(access_token))

    async def aclose(self) -> None:
        await self._adapter.aclose()


def _json_object(response: httpx.Response, label: str) -> Mapping[str, Any]:
    if len(response.content) > MAX_JSON_BYTES:
        raise EnterpriseOidcError(
            "ENTERPRISE_OIDC_RESPONSE_INVALID",
            f"{label} response is too large",
        )

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(response.content.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise EnterpriseOidcError(
            "ENTERPRISE_OIDC_RESPONSE_INVALID",
            f"{label} response is invalid",
        ) from exc
    if not isinstance(value, Mapping):
        raise EnterpriseOidcError(
            "ENTERPRISE_OIDC_RESPONSE_INVALID",
            f"{label} response is not an object",
        )
    return value


@dataclass(frozen=True, slots=True)
class AuthorizationCode:
    code: str
    code_verifier: str = field(repr=False)


class OidcLoginTransaction:
    def __init__(self, *, redirect_uri: str, expected_issuer: str | None = None) -> None:
        self.redirect_uri = redirect_uri
        self.expected_issuer = (
            canonicalize_issuer(expected_issuer) if expected_issuer is not None else None
        )
        self.state = secrets.token_urlsafe(48)
        self.nonce = secrets.token_urlsafe(48)
        self._code_verifier: str | None = secrets.token_urlsafe(64)
        self._consumed = False

    @property
    def code_challenge(self) -> str:
        if self._code_verifier is None:
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_TRANSACTION_CONSUMED",
                "OIDC login transaction was already consumed",
            )
        digest = hashlib.sha256(self._code_verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    @property
    def verifier_available(self) -> bool:
        return self._code_verifier is not None and not self._consumed

    def authorization_url(
        self,
        *,
        endpoint: str,
        client_id: str,
        scopes: tuple[str, ...],
        force_reauthentication: bool = False,
    ) -> str:
        parameters = {
            'response_type': 'code',
            'client_id': client_id,
            'redirect_uri': self.redirect_uri,
            'scope': ' '.join(scopes),
            'state': self.state,
            'nonce': self.nonce,
            'code_challenge': self.code_challenge,
            'code_challenge_method': 'S256',
        }
        if force_reauthentication:
            # Account switching must never silently reuse the previous
            # interactive session, even if a platform browser profile is
            # accidentally retained by its host.
            parameters['prompt'] = 'login'
        return f"{endpoint}?{urlencode(parameters)}"

    def consume_callback(self, callback_url: str) -> AuthorizationCode:
        if self._consumed or self._code_verifier is None:
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_TRANSACTION_CONSUMED",
                "OIDC login transaction was already consumed",
            )
        verifier = self._code_verifier
        self._code_verifier = None
        self._consumed = True
        expected = urlsplit(self.redirect_uri)
        callback = urlsplit(str(callback_url or "").strip())
        if (
            callback.scheme != expected.scheme
            or callback.hostname != expected.hostname
            or callback.port != expected.port
            or callback.path != expected.path
            or callback.username is not None
            or callback.password is not None
            or callback.fragment
        ):
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CALLBACK_INVALID",
                "OIDC callback target is invalid",
            )
        try:
            values = parse_qs(callback.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CALLBACK_INVALID",
                "OIDC callback parameters are invalid",
            ) from exc
        states = values.get("state", [])
        if len(states) != 1 or not secrets.compare_digest(states[0], self.state):
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_STATE_INVALID",
                "OIDC callback state is invalid",
            )
        success_fields = {"code", "state", "iss", "session_state"}
        error_fields = {
            "error",
            "error_description",
            "error_uri",
            "state",
            "iss",
            "session_state",
        }
        allowed_fields = error_fields if "error" in values else success_fields
        if not set(values).issubset(allowed_fields):
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CALLBACK_INVALID",
                "OIDC callback parameters are invalid",
            )
        response_issuers = values.get("iss", [])
        if response_issuers:
            try:
                response_issuer = (
                    canonicalize_issuer(response_issuers[0])
                    if len(response_issuers) == 1
                    else ""
                )
            except ValueError:
                response_issuer = ""
            if self.expected_issuer is None or response_issuer != self.expected_issuer:
                raise EnterpriseOidcError(
                    "ENTERPRISE_OIDC_ISSUER_INVALID",
                    "OIDC authorization response issuer is invalid",
                )
        response_sessions = values.get("session_state", [])
        if response_sessions and (
            len(response_sessions) != 1
            or not response_sessions[0].strip()
            or len(response_sessions[0]) > 1_024
        ):
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CALLBACK_INVALID",
                "OIDC authorization session state is invalid",
            )
        if "error" in values:
            errors = values.get("error", [])
            if len(errors) != 1 or not errors[0].strip() or len(errors[0]) > 200:
                raise EnterpriseOidcError(
                    "ENTERPRISE_OIDC_CALLBACK_INVALID",
                    "OIDC authorization error is invalid",
                )
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_AUTHORIZATION_DENIED",
                "Enterprise authorization was denied",
            )
        codes = values.get("code", [])
        if len(codes) != 1:
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CALLBACK_INVALID",
                "OIDC authorization code is invalid",
            )
        code = _required(codes[0], "authorization code", limit=8_192)
        return AuthorizationCode(code=code, code_verifier=verifier)


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server_version = "MentorFlowDesktopCallback/1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        receiver = getattr(self.server, "receiver", None)
        if not isinstance(receiver, LoopbackCallbackReceiver):
            self.send_error(500)
            return
        parsed = urlsplit(self.path)
        if parsed.path != receiver.callback_path:
            self.send_error(404)
            return
        callback = urlunsplit(
            ("http", f"127.0.0.1:{receiver.port}", parsed.path, parsed.query, "")
        )
        receiver._publish(callback)
        payload = (
            "<!doctype html><meta charset=utf-8><title>MentorFlow</title>"
            "<p>登录结果已安全返回 MentorFlow，可以关闭此页面。</p>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'none'")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _LoopbackServer(http.server.HTTPServer):
    allow_reuse_address = False

    def server_bind(self) -> None:
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class LoopbackCallbackReceiver:
    def __init__(self, *, port: int = 0, callback_path: str = DEFAULT_CALLBACK_PATH) -> None:
        self.callback_path = callback_path
        self._event = threading.Event()
        self._closed = threading.Event()
        self._close_lock = threading.Lock()
        self._callback_url = ""
        try:
            self._server = _LoopbackServer(("127.0.0.1", int(port)), _CallbackHandler)
        except OSError as exc:
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CALLBACK_UNAVAILABLE",
                "Desktop loopback callback could not be reserved",
            ) from exc
        self._server.timeout = 0.25
        setattr(self._server, "receiver", self)
        self.port = int(self._server.server_address[1])

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.callback_path}"

    def _publish(self, callback_url: str) -> None:
        if not self._event.is_set():
            self._callback_url = callback_url
            self._event.set()

    def wait(self, timeout_seconds: float) -> str:
        deadline = time.monotonic() + float(timeout_seconds)
        while (
            not self._event.is_set()
            and not self._closed.is_set()
            and time.monotonic() < deadline
        ):
            try:
                self._server.handle_request()
            except OSError:
                if not self._closed.is_set():
                    raise
        if not self._event.is_set():
            if self._closed.is_set():
                raise EnterpriseOidcError(
                    "ENTERPRISE_OIDC_LOGIN_CANCELLED",
                    "Enterprise login was cancelled",
                )
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_CALLBACK_TIMEOUT",
                "Enterprise login did not return to MentorFlow in time",
            )
        return self._callback_url

    def close(self) -> None:
        with self._close_lock:
            if self._closed.is_set():
                return
            self._closed.set()
            self._server.server_close()


@dataclass(frozen=True, slots=True)
class PreparedOidcLogin:
    receiver: LoopbackCallbackReceiver
    discovery: OidcDiscovery
    transaction: OidcLoginTransaction
    authorization_url: str


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    id_token: str = field(repr=False, default="")


@dataclass(frozen=True, slots=True)
class EnterpriseOidcLoginResult:
    identity: AccountIdentity = field(repr=False)
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    server_state: Mapping[str, Any] = field(repr=False)
    restored: bool = False
    rehydration_session: Any = field(default=None, repr=False, compare=False)

    @property
    def sync_cursor(self) -> str:
        context = self.server_state.get("context")
        if not isinstance(context, Mapping):
            return ""
        return str(context.get("context_version") or "")


class EnterpriseOidcClient:
    def __init__(
        self,
        config: DesktopOidcConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        token_validator: OidcTokenValidator | None = None,
    ) -> None:
        self.config = config
        self._http = http_client or httpx.AsyncClient(
            timeout=config.request_timeout_seconds,
            follow_redirects=False,
            # OS/public roots plus the existing pinned preview CA; no TLS bypass.
            verify=ssl.create_default_context() if config.issuer_migration is not None else True,
        )
        self._owns_http = http_client is None
        self._token_validator = token_validator
        self._owns_validator = token_validator is None

    async def _discovery(self) -> OidcDiscovery:
        try:
            response = await self._http.get(
                self.config.discovery_url,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_DISCOVERY_UNAVAILABLE",
                "Enterprise identity discovery is unavailable",
            ) from exc
        value = _json_object(response, "OIDC discovery")
        discovered_issuer = value.get("issuer")
        if not self.config.accepts_issuer(discovered_issuer):
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_DISCOVERY_INVALID",
                "OIDC discovery issuer does not match configuration",
            )
        result = OidcDiscovery(
            issuer=discovered_issuer,
            authorization_endpoint=_trusted_provider_endpoint(
                value.get("authorization_endpoint"),
                "authorization_endpoint",
                discovered_issuer,
            ),
            token_endpoint=_trusted_provider_endpoint(
                value.get("token_endpoint"),
                "token_endpoint",
                discovered_issuer,
            ),
            jwks_uri=_trusted_provider_endpoint(
                value.get("jwks_uri"),
                "jwks_uri",
                discovered_issuer,
            ),
        )
        if self.config.issuer_migration is not None and (
            result.authorization_endpoint != discovered_issuer + '/protocol/openid-connect/auth'
            or result.token_endpoint != discovered_issuer + '/protocol/openid-connect/token'
            or result.jwks_uri != discovered_issuer + '/protocol/openid-connect/certs'
        ):
            raise EnterpriseOidcError('ENTERPRISE_OIDC_DISCOVERY_INVALID', 'Unexpected migration realm endpoint')
        return result

    def _validator(
        self,
        *,
        redirect_uri: str,
        discovery: OidcDiscovery,
    ) -> OidcTokenValidator:
        if self._token_validator is None:
            self._token_validator = BackendOidcTokenValidator(
                self.config,
                redirect_uri=redirect_uri,
                discovery=discovery,
            )
        return self._token_validator

    async def prepare_login(
        self,
        *,
        force_reauthentication: bool = False,
    ) -> PreparedOidcLogin:
        receiver = LoopbackCallbackReceiver(
            port=self.config.callback_port,
            callback_path=self.config.callback_path,
        )
        try:
            discovery = await self._discovery()
            transaction = OidcLoginTransaction(
                redirect_uri=receiver.redirect_uri,
                expected_issuer=discovery.issuer,
            )
            authorization_url = transaction.authorization_url(
                endpoint=discovery.authorization_endpoint,
                client_id=self.config.client_id,
                scopes=self.config.scopes,
                force_reauthentication=force_reauthentication,
            )
            return PreparedOidcLogin(
                receiver=receiver,
                discovery=discovery,
                transaction=transaction,
                authorization_url=authorization_url,
            )
        except BaseException:
            receiver.close()
            raise

    async def _token_request(
        self,
        endpoint: str,
        data: Mapping[str, str],
        *,
        require_id_token: bool,
    ) -> OAuthTokens:
        try:
            response = await self._http.post(
                endpoint,
                data=dict(data),
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if data.get("grant_type") == "refresh_token" and exc.response.status_code in {400, 401, 403}:
                raise EnterpriseOidcError("ENTERPRISE_SESSION_REAUTH_REQUIRED", "登录已失效，请重新输入账号密码。") from exc
            raise EnterpriseOidcError("ENTERPRISE_OIDC_TOKEN_EXCHANGE_FAILED", "登录服务暂不可用。") from exc
        except httpx.HTTPError as exc:
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_TOKEN_EXCHANGE_FAILED",
                "Enterprise token exchange failed",
            ) from exc
        value = _json_object(response, "OIDC token")
        if str(value.get("token_type") or "").strip().casefold() != "bearer":
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_TOKEN_RESPONSE_INVALID",
                "OIDC token type is invalid",
            )
        access = _required(value.get("access_token"), "access_token", limit=65_536)
        refresh = str(value.get("refresh_token") or "").strip()
        identity = str(value.get("id_token") or "").strip()
        if len(refresh) > 65_536 or len(identity) > 65_536:
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_TOKEN_RESPONSE_INVALID",
                "OIDC token response is too large",
            )
        if require_id_token and (not refresh or not identity):
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_TOKEN_RESPONSE_INVALID",
                "OIDC login requires ID and refresh tokens",
            )
        return OAuthTokens(access_token=access, refresh_token=refresh, id_token=identity)

    async def _discover_workspace(self, access_token: str) -> Mapping[str, object]:
        """Consume the canonical subject-only workspace bootstrap contract."""
        headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}

        async def request(initialize: bool = False) -> Mapping[str, object]:
            try:
                if initialize:
                    response = await self._http.post(
                        f"{self.config.api_base_url}/api/enterprise/account/workspaces/initialize",
                        headers=headers, json={},
                    )
                else:
                    response = await self._http.get(
                        f"{self.config.api_base_url}/api/enterprise/account/workspaces", headers=headers,
                    )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise EnterpriseOidcError(
                    "ENTERPRISE_WORKSPACE_DISCOVERY_FAILED", "学习空间暂不可用，请检查网络或账号权限后重试。",
                ) from exc
            payload = _json_object(response, "Account workspaces")
            rows = payload.get("workspaces")
            if not isinstance(rows, list) or type(payload.get("initialization_required")) is not bool:
                raise EnterpriseOidcError("ENTERPRISE_WORKSPACE_RESPONSE_INVALID", "学习空间响应无法验证。")
            seen = set()
            for row in rows:
                if not isinstance(row, Mapping) or any(
                    not isinstance(row.get(key), str) or not row[key].strip()
                    for key in ("id", "organization_id", "title")
                ) or row.get("status") != "active" or row.get("initialization_status") not in {"READY", "PROFILE_REQUIRED"}:
                    raise EnterpriseOidcError("ENTERPRISE_WORKSPACE_RESPONSE_INVALID", "学习空间响应无法验证。")
                if row["id"] in seen:
                    raise EnterpriseOidcError("ENTERPRISE_WORKSPACE_RESPONSE_INVALID", "学习空间响应无法验证。")
                seen.add(row["id"])
            default = payload.get("default_workspace_id")
            if default is not None and (not isinstance(default, str) or default not in seen):
                raise EnterpriseOidcError("ENTERPRISE_WORKSPACE_RESPONSE_INVALID", "学习空间响应无法验证。")
            return payload

        payload = await request()
        if not payload["workspaces"] or payload["initialization_required"]:
            payload = await request(initialize=True)
        rows = payload["workspaces"]
        if not rows or payload["initialization_required"] or any(row["initialization_status"] != "READY" for row in rows):
            raise EnterpriseOidcError("ENTERPRISE_WORKSPACE_INITIALIZATION_REQUIRED", "学习空间尚未准备好，请重试。")
        # A configured ID is only a hint and must be in this fresh allowed list.
        selected = next((row for row in rows if row["id"] == self.config.workspace_id), None)
        return selected or next((row for row in rows if row["id"] == payload.get("default_workspace_id")), rows[0])

    async def _server_state(
        self,
        validated: ValidatedTokenIdentity,
        access_token: str,
    ) -> tuple[AccountIdentity, Mapping[str, Any]]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        }
        if (canonicalize_issuer(validated.issuer) != self.config.canonical_identity_issuer
                and not self.config.accepts_issuer(validated.issuer)):
            raise EnterpriseOidcError("ENTERPRISE_OIDC_ISSUER_INVALID", "登录身份来源不匹配。")
        selected = await self._discover_workspace(access_token)
        headers["X-MentorFlow-Organization-ID"] = selected["organization_id"]
        headers["X-MentorFlow-Workspace-ID"] = selected["id"]
        try:
            response = await self._http.get(
                f"{self.config.api_base_url}/api/enterprise/auth/me",
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EnterpriseOidcError(
                "ENTERPRISE_ACCOUNT_STATE_UNAVAILABLE",
                "Enterprise account state could not be refreshed",
            ) from exc
        state = _json_object(response, "Enterprise account state")
        account = state.get("account")
        context = state.get("context")
        if (
            state.get("authenticated") is not True
            or not isinstance(account, Mapping)
            or not isinstance(context, Mapping)
        ):
            raise EnterpriseOidcError(
                "ENTERPRISE_ACCOUNT_STATE_INVALID",
                "Enterprise account state is invalid",
            )
        server_subject = _required(account.get("subject_id"), "account.subject_id", limit=300)
        expected_account_id = AccountIdentity(self.config.canonical_identity_issuer, validated.subject, '').account_id
        migration = validated.issuer != self.config.canonical_identity_issuer
        if (migration or 'account_id' in account) and account.get('account_id') != expected_account_id:
            raise EnterpriseOidcError('ENTERPRISE_ACCOUNT_STATE_MISMATCH',
                                      'Enterprise server account anchor does not match')
        if not secrets.compare_digest(server_subject, validated.subject):
            raise EnterpriseOidcError(
                "ENTERPRISE_ACCOUNT_STATE_MISMATCH",
                "Enterprise server state belongs to another account",
            )
        organization_id = _required(
            context.get("organization_id"),
            "context.organization_id",
            limit=240,
        )
        workspace_id = _required(
            context.get("workspace_id"),
            "context.workspace_id",
            limit=240,
        )
        if organization_id != selected["organization_id"] or workspace_id != selected["id"]:
            raise EnterpriseOidcError("ENTERPRISE_ACCOUNT_STATE_MISMATCH", "服务端返回的空间与已验证选择不一致。")
        display = (
            str(account.get("display_name") or "").strip()
            or str(account.get("preferred_username") or "").strip()
            or validated.display_name
        )
        return (
            AccountIdentity(
                issuer=self.config.canonical_identity_issuer,
                subject=validated.subject,
                display_name=display,
                organization_id=organization_id,
                workspace_id=workspace_id,
            ),
            state,
        )

    async def complete_login(
        self,
        prepared: PreparedOidcLogin,
        callback_url: str,
    ) -> EnterpriseOidcLoginResult:
        authorization = prepared.transaction.consume_callback(callback_url)
        tokens = await self._token_request(
            prepared.discovery.token_endpoint,
            {
                "grant_type": "authorization_code",
                "client_id": self.config.client_id,
                "code": authorization.code,
                "redirect_uri": prepared.transaction.redirect_uri,
                "code_verifier": authorization.code_verifier,
            },
            require_id_token=True,
        )
        validator = self._validator(
            redirect_uri=prepared.transaction.redirect_uri,
            discovery=prepared.discovery,
        )
        try:
            validated = await validator.validate_login(
                id_token=tokens.id_token,
                access_token=tokens.access_token,
                expected_nonce=prepared.transaction.nonce,
            )
        except EnterpriseOidcError:
            raise
        except Exception as exc:
            raise EnterpriseOidcError(
                "ENTERPRISE_OIDC_TOKEN_INVALID",
                "Enterprise identity token validation failed",
            ) from exc
        identity, state = await self._server_state(validated, tokens.access_token)
        return EnterpriseOidcLoginResult(
            identity=identity,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            server_state=state,
        )

    async def restore(
        self,
        profile: AccountProfile,
        credentials: AccountCredentialVault,
        *,
        refresh_session: bool = False,
    ) -> EnterpriseOidcLoginResult:
        if profile.mode is not ProfileMode.ENTERPRISE_ACCOUNT:
            raise EnterpriseOidcError(
                "ENTERPRISE_ACCOUNT_PROFILE_REQUIRED",
                "Enterprise Account profile is required",
            )
        if profile.issuer_hash != issuer_hash(self.config.canonical_identity_issuer):
            raise EnterpriseOidcError(
                "ENTERPRISE_ACCOUNT_ISSUER_CHANGED", "登录服务已变更，请重新登录。",
            )
        access_token, refresh_token = credentials.load_oauth_tokens(profile)
        discovery = await self._discovery()
        redirect = f"http://127.0.0.1:{self.config.callback_port or 1}{self.config.callback_path}"
        validator = self._validator(redirect_uri=redirect, discovery=discovery)
        effective_access = access_token
        effective_refresh = refresh_token
        try:
            validated = await validator.validate_access(access_token)
            if refresh_session:
                raise EnterpriseOidcError("ENTERPRISE_REFRESH_REQUIRED", "Refresh current server session")
        except Exception:
            refreshed = await self._token_request(
                discovery.token_endpoint,
                {
                    "grant_type": "refresh_token",
                    "client_id": self.config.client_id,
                    "refresh_token": refresh_token,
                },
                require_id_token=False,
            )
            effective_access = refreshed.access_token
            effective_refresh = refreshed.refresh_token or refresh_token
            try:
                validated = await validator.validate_access(effective_access)
            except Exception as exc:
                raise EnterpriseOidcError(
                    "ENTERPRISE_OIDC_TOKEN_INVALID",
                    "Restored Enterprise session token is invalid",
                ) from exc
        external_identity = AccountIdentity(validated.issuer, validated.subject, validated.display_name)
        if external_identity.subject_hash != profile.subject_hash:
            raise EnterpriseOidcError(
                "ENTERPRISE_ACCOUNT_RESTORE_MISMATCH",
                "Restored session belongs to another Enterprise Account",
            )
        # A NEW issuer token is external evidence, not permission to select a
        # canonical Vault/Locker namespace. Confirm Backend's mapping first.
        identity, state = await self._server_state(validated, effective_access)
        if identity.account_id != profile.account_id:
            raise EnterpriseOidcError('ENTERPRISE_ACCOUNT_RESTORE_MISMATCH',
                                      'Restored account anchor does not match')
        if (effective_access, effective_refresh) != (access_token, refresh_token):
            credentials.store_oauth_tokens(
                identity,
                access_token=effective_access,
                refresh_token=effective_refresh,
            )
        return EnterpriseOidcLoginResult(
            identity=identity,
            access_token=effective_access,
            refresh_token=effective_refresh,
            server_state=state,
            restored=True,
        )

    async def end_session(self, refresh_token: str) -> bool:
        """End this Keycloak client session, without a browser or global logout."""
        if not refresh_token:
            return False
        # Discovery has already constrained all endpoints to the configured
        # Keycloak issuer; never accept a runtime-provided logout URL.
        await self._discovery()
        try:
            response = await self._http.post(
                f"{self.config.issuer}/protocol/openid-connect/logout",
                data={"client_id": self.config.client_id, "refresh_token": refresh_token},
                headers={"Accept": "application/json"},
            )
            return response.status_code in {200, 204}
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        if self._token_validator is not None and self._owns_validator:
            await self._token_validator.aclose()
        if self._owns_http:
            await self._http.aclose()


def restore_enterprise_account_from_env(
    profile: AccountProfile,
    credentials: AccountCredentialVault,
    *,
    refresh_session: bool = False,
) -> EnterpriseOidcLoginResult:
    async def run() -> EnterpriseOidcLoginResult:
        # Noninteractive compatibility entry also accepts the native session
        # namespace. Never send an opaque native refresh to an OIDC endpoint.
        from .native_auth import NativeCredentialClient, REFRESH_PREFIX
        config = DesktopOidcConfig.from_env()
        if profile.issuer_hash != issuer_hash(config.canonical_identity_issuer):
            raise EnterpriseOidcError('ENTERPRISE_ACCOUNT_ISSUER_CHANGED', '登录服务已变更，请重新登录。')
        _, refresh = credentials.load_oauth_tokens(profile)
        client_type = NativeCredentialClient if refresh.startswith(REFRESH_PREFIX) else EnterpriseOidcClient
        client = client_type(config)
        try:
            return await client.restore(profile, credentials, refresh_session=refresh_session)
        finally:
            await client.aclose()

    return asyncio.run(run())


__all__ = [
    "BackendOidcTokenValidator",
    "DesktopOidcConfig",
    "EnterpriseOidcClient",
    "EnterpriseOidcError",
    "EnterpriseOidcLoginResult",
    "LoopbackCallbackReceiver",
    "OAuthTokens",
    "OidcDiscovery",
    "OidcLoginTransaction",
    "PreparedOidcLogin",
    "ValidatedTokenIdentity",
    "restore_enterprise_account_from_env",
]
