"""FastAPI BFF boundary for the Enterprise OIDC identity runtime."""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import Engine

from app.core_errors import app_error_body
from app.enterprise.contracts_v1 import ActorContext, TenantContext, TenantMode

from .config import EnterpriseIdentityConfig
from .context import (
    IdentityContextError,
    RequestIdentityContext,
    enterprise_request_context,
)
from .directory import PostgresIdentityDirectory, service_identity_grants_from_env
from .keycloak import KeycloakOidcClient
from .models import EnterpriseSession, IdentityDirectoryPort
from .oidc import IdentityError


AUTH_PREFIX = "/api/enterprise/auth"
CONFIG_PATH = f"{AUTH_PREFIX}/config"
LOGIN_PATH = f"{AUTH_PREFIX}/login"
CALLBACK_PATH = f"{AUTH_PREFIX}/callback"
ME_PATH = f"{AUTH_PREFIX}/me"
CONTEXT_PATH = f"{AUTH_PREFIX}/context"
REFRESH_PATH = f"{AUTH_PREFIX}/refresh"
LOGOUT_PATH = f"{AUTH_PREFIX}/logout"
BACKCHANNEL_LOGOUT_PATH = f"{AUTH_PREFIX}/backchannel-logout"
_PUBLIC_PATHS = {
    CONFIG_PATH,
    LOGIN_PATH,
    CALLBACK_PATH,
    BACKCHANNEL_LOGOUT_PATH,
    "/api/health",
}
_TRANSACTION_COOKIE_SECURE = "__Host-mentorflow_oidc_transaction"
_TRANSACTION_COOKIE_LOOPBACK = "mentorflow_oidc_transaction"
_ORGANIZATION_HEADER = "X-MentorFlow-Organization-ID"
_WORKSPACE_HEADER = "X-MentorFlow-Workspace-ID"


class ContextSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    organization_id: str = Field(min_length=1, max_length=240)
    workspace_id: str | None = Field(default=None, max_length=240)
    expected_context_version: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class IdentityBinding:
    context: RequestIdentityContext | None
    authentication_mode: str
    session: EnterpriseSession | None = None


def _safe_return_path(value: str | None) -> str:
    path = str(value or "/").strip()
    if (
        not path.startswith("/")
        or path.startswith("//")
        or len(path) > 2_048
        or "\r" in path
        or "\n" in path
        or "\\" in path
        or path.startswith(CALLBACK_PATH)
    ):
        return "/"
    return path


def _origin(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def identity_error_response(request: Request, exc: IdentityError | IdentityContextError) -> JSONResponse:
    response = JSONResponse(
        status_code=exc.status,
        content=app_error_body(
            exc.code,
            exc.message,
            {},
            getattr(request.state, "request_id", None),
        ),
    )
    runtime = getattr(request.app.state, "enterprise_identity_runtime", None)
    if (
        exc.code
        in {
            "ENTERPRISE_SESSION_INVALID",
            "ENTERPRISE_SESSION_REFRESH_EXPIRED",
            "IDENTITY_DISABLED_OR_REVOKED",
            "OIDC_TOKEN_EXPIRED",
        }
        and isinstance(runtime, EnterpriseIdentityRuntime)
        and request.cookies.get(runtime.config.session_cookie_name)
    ):
        runtime.clear_session_cookie(response)
    return response


def identity_job_context(binding: IdentityBinding) -> dict[str, object]:
    """Return the frozen Actor/Tenant payload consumed by durable jobs."""

    if binding.context is None or binding.context.actor is None:
        raise IdentityError(
            "ENTERPRISE_AUTH_REQUIRED",
            "Enterprise job context requires authenticated identity",
        )
    return {
        "actor_context": binding.context.actor.model_dump(mode="json"),
        "tenant_context": binding.context.tenant.model_dump(mode="json"),
    }


def enterprise_route_is_tenant_ready(path: str) -> bool:
    """Keep legacy SQLite APIs closed in enterprise mode until tenant-bound."""

    return str(path or "").startswith("/api/enterprise/")


class EnterpriseIdentityRuntime:
    def __init__(
        self,
        config: EnterpriseIdentityConfig,
        *,
        client: KeycloakOidcClient | None = None,
        directory: IdentityDirectoryPort | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.directory = directory
        if config.mode is TenantMode.ENTERPRISE_SERVER and (
            client is None or directory is None
        ):
            raise ValueError("enterprise identity runtime requires client and directory")

    @property
    def enterprise_enabled(self) -> bool:
        return self.config.mode is TenantMode.ENTERPRISE_SERVER

    @property
    def transaction_cookie_name(self) -> str:
        return (
            _TRANSACTION_COOKIE_SECURE
            if self.config.session_cookie_secure
            else _TRANSACTION_COOKIE_LOOPBACK
        )

    def is_public_path(self, path: str) -> bool:
        return path in _PUBLIC_PATHS

    def config_payload(self) -> dict[str, object]:
        if not self.enterprise_enabled:
            return {
                "schema_version": "mentorflow.enterprise-identity-config.v1",
                "mode": TenantMode.PERSONAL_LOCAL.value,
                "oidc_enabled": False,
                "login_required": False,
                "context_contract": "enterprise-context.v1",
            }
        assert self.config.keycloak is not None
        return {
            "schema_version": "mentorflow.enterprise-identity-config.v1",
            "mode": TenantMode.ENTERPRISE_SERVER.value,
            "oidc_enabled": True,
            "login_required": True,
            "issuer": self.config.keycloak.issuer,
            "client_id": self.config.keycloak.client_id,
            "audience": self.config.keycloak.audience,
            "login_path": LOGIN_PATH,
            "callback_path": CALLBACK_PATH,
            "session_path": ME_PATH,
            "context_path": CONTEXT_PATH,
            "logout_path": LOGOUT_PATH,
            "context_contract": "enterprise-context.v1",
            "session_store": self.config.session_store_mode,
        }

    def _require_enterprise(self) -> tuple[KeycloakOidcClient, IdentityDirectoryPort]:
        if not self.enterprise_enabled or self.client is None or self.directory is None:
            raise IdentityError(
                "ENTERPRISE_IDENTITY_DISABLED",
                "Enterprise identity is not enabled in Personal Local mode",
                status=404,
            )
        return self.client, self.directory

    def _set_session_cookie(self, response: Response, session_id: str) -> None:
        response.set_cookie(
            self.config.session_cookie_name,
            session_id,
            max_age=self.config.session_ttl_seconds,
            secure=self.config.session_cookie_secure,
            httponly=True,
            samesite="lax",
            path="/",
        )

    def clear_session_cookie(self, response: Response) -> None:
        response.delete_cookie(
            self.config.session_cookie_name,
            secure=self.config.session_cookie_secure,
            httponly=True,
            samesite="lax",
            path="/",
        )

    def _set_transaction_cookie(self, response: Response, binding: str) -> None:
        response.set_cookie(
            self.transaction_cookie_name,
            binding,
            max_age=self.config.pending_login_ttl_seconds,
            secure=self.config.session_cookie_secure,
            httponly=True,
            samesite="lax",
            path="/",
        )

    def clear_transaction_cookie(self, response: Response) -> None:
        response.delete_cookie(
            self.transaction_cookie_name,
            secure=self.config.session_cookie_secure,
            httponly=True,
            samesite="lax",
            path="/",
        )

    def require_csrf_origin(self, request: Request) -> None:
        supplied = _origin(request.headers.get("Origin"))
        if supplied is None:
            supplied = _origin(request.headers.get("Referer"))
        if supplied not in set(self.config.browser_allowed_origins):
            raise IdentityError(
                "ENTERPRISE_CSRF_REJECTED",
                "Enterprise browser origin is not approved",
                status=403,
            )

    async def _request_context(
        self,
        session: EnterpriseSession,
        *,
        request_id: str,
    ) -> RequestIdentityContext:
        _client, directory = self._require_enterprise()
        return await asyncio.to_thread(
            enterprise_request_context,
            session=session,
            directory=directory,
            request_id=request_id,
        )

    async def validate_selected_session(
        self,
        session: EnterpriseSession,
        *,
        request_id: str,
    ) -> RequestIdentityContext:
        return await self._request_context(session, request_id=request_id)

    async def authenticate_request(self, request: Request) -> IdentityBinding:
        client, _directory = self._require_enterprise()
        authorization = str(request.headers.get("Authorization") or "").strip()
        cookie_session_id = str(
            request.cookies.get(self.config.session_cookie_name) or ""
        ).strip()
        if authorization and cookie_session_id:
            raise IdentityError(
                "AUTH_BOUNDARY_CONFLICT",
                "Cookie and bearer identity modes cannot be mixed",
            )
        request_id = str(getattr(request.state, "request_id", "") or secrets.token_hex(16))
        if authorization:
            if not authorization.startswith("Bearer ") or authorization.count(" ") != 1:
                raise IdentityError(
                    "OIDC_TOKEN_INVALID",
                    "Bearer authorization is malformed",
                )
            token = authorization[7:]
            principal = await client.authenticate_access_token(token)
            organization_id = str(
                request.headers.get(_ORGANIZATION_HEADER) or ""
            ).strip()
            workspace_id = str(request.headers.get(_WORKSPACE_HEADER) or "").strip() or None
            if not organization_id:
                raise IdentityError(
                    "ENTERPRISE_ORGANIZATION_REQUIRED",
                    "Bearer identity requires a selected organization",
                    status=403,
                )
            session = EnterpriseSession(
                session_id=(
                    "bearer-"
                    + hashlib.sha256(
                        f"{principal.issuer}\x1f{principal.token_id}".encode("utf-8")
                    ).hexdigest()
                ),
                principal=principal,
                created_at=principal.issued_at,
                refreshed_at=principal.issued_at,
                session_expires_at=principal.expires_at,
                access_token_expires_at=principal.expires_at,
                selected_organization_id=organization_id,
                selected_workspace_id=workspace_id,
            )
            context = await self._request_context(session, request_id=request_id)
            return IdentityBinding(
                context=context,
                authentication_mode="BEARER",
                session=None,
            )
        if not cookie_session_id:
            raise IdentityError(
                "ENTERPRISE_AUTH_REQUIRED",
                "Enterprise authentication is required",
            )
        if len(cookie_session_id) > 200:
            raise IdentityError(
                "ENTERPRISE_SESSION_INVALID",
                "Enterprise session is invalid",
            )
        session = client.store.get_public(cookie_session_id)
        supplied_organization = str(
            request.headers.get(_ORGANIZATION_HEADER) or ""
        ).strip()
        supplied_workspace = str(request.headers.get(_WORKSPACE_HEADER) or "").strip()
        if supplied_organization or supplied_workspace:
            if (
                supplied_organization != (session.selected_organization_id or "")
                or supplied_workspace != (session.selected_workspace_id or "")
            ):
                raise IdentityError(
                    "ENTERPRISE_CONTEXT_CONFLICT",
                    "Browser headers conflict with the server session context",
                    status=409,
                )
        if request.url.path == CONTEXT_PATH and session.selected_organization_id is None:
            return IdentityBinding(
                context=None,
                authentication_mode="COOKIE",
                session=session,
            )
        try:
            context = await self._request_context(session, request_id=request_id)
        except IdentityContextError as exc:
            if exc.code != "OIDC_TOKEN_EXPIRED":
                raise
            session = await client.refresh_session(session.session_id)
            context = await self._request_context(session, request_id=request_id)
        return IdentityBinding(
            context=context,
            authentication_mode="COOKIE",
            session=session,
        )

    async def revalidate_product_identity(self, request: Request) -> IdentityBinding:
        """Re-resolve the signed subject and active membership for product work.

        The outer middleware authenticates every Enterprise API request.  Product
        routes deliberately perform a second directory-backed resolution at the
        last authorization boundary so a membership revoked while the request was
        entering the application cannot reach a product handler.
        """

        current = _binding(request)
        refreshed = await self.authenticate_request(request)

        def membership_identity(binding: IdentityBinding) -> tuple[str, str, str, str]:
            context = binding.context
            actor = context.actor if context is not None else None
            tenant = context.tenant if context is not None else None
            if (
                actor is None
                or tenant is None
                or not actor.subject_id
                or not actor.membership_id
                or not tenant.organization_id
                or not tenant.workspace_id
                or actor.organization_id != tenant.organization_id
                or actor.workspace_id != tenant.workspace_id
            ):
                raise IdentityError(
                    "ENTERPRISE_MEMBERSHIP_REQUIRED",
                    "An active workspace membership is required for Enterprise product routes",
                    status=403,
                )
            return (
                actor.subject_id,
                actor.membership_id,
                tenant.organization_id,
                tenant.workspace_id,
            )

        if (
            current.authentication_mode != refreshed.authentication_mode
            or membership_identity(current) != membership_identity(refreshed)
        ):
            raise IdentityError(
                "ENTERPRISE_IDENTITY_CONTEXT_CHANGED",
                "Enterprise identity context changed during authorization",
                status=409,
            )
        if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            if refreshed.authentication_mode == "COOKIE":
                self.require_csrf_origin(request)
        request.state.enterprise_identity = refreshed
        request.state.actor_context = refreshed.context.actor
        request.state.tenant_context = refreshed.context.tenant
        return refreshed

    def response_payload(
        self,
        binding: IdentityBinding,
    ) -> dict[str, object]:
        if binding.context is None or binding.context.actor is None:
            raise IdentityError(
                "ENTERPRISE_CONTEXT_REQUIRED",
                "Enterprise organization and workspace context is required",
                status=403,
            )
        actor: ActorContext = binding.context.actor
        tenant: TenantContext = binding.context.tenant
        principal = binding.session.principal if binding.session is not None else None
        return {
            "schema_version": "mentorflow.enterprise-identity-session.v1",
            "authenticated": True,
            "authentication_mode": binding.authentication_mode,
            "account": {
                "subject_id": actor.subject_id,
                "preferred_username": (
                    principal.preferred_username if principal is not None else None
                ),
                "email": principal.email if principal is not None else None,
                "display_name": principal.display_name if principal is not None else None,
            },
            "context": {
                "contract_version": "enterprise-context.v1",
                "organization_id": tenant.organization_id,
                "workspace_id": tenant.workspace_id,
                "roles": list(actor.roles),
                "service_permissions": list(actor.service_permissions),
                "context_version": (
                    binding.session.context_version if binding.session is not None else 0
                ),
            },
        }

    async def aclose(self) -> None:
        if self.client is not None:
            await self.client.aclose()


def build_enterprise_identity_runtime(
    *,
    engine: Engine,
    source: Mapping[str, str] | None = None,
) -> EnterpriseIdentityRuntime:
    env = os.environ if source is None else source
    config = EnterpriseIdentityConfig.from_env(env)
    if config.mode is TenantMode.PERSONAL_LOCAL:
        return EnterpriseIdentityRuntime(config)
    assert config.keycloak is not None
    directory = PostgresIdentityDirectory(
        engine,
        issuer=config.keycloak.issuer,
        service_grants=service_identity_grants_from_env(env),
    )
    return EnterpriseIdentityRuntime(
        config,
        client=KeycloakOidcClient(config),
        directory=directory,
    )


router = APIRouter(prefix=AUTH_PREFIX, tags=["enterprise-identity"])


def _runtime(request: Request) -> EnterpriseIdentityRuntime:
    runtime = getattr(request.app.state, "enterprise_identity_runtime", None)
    if not isinstance(runtime, EnterpriseIdentityRuntime):
        raise IdentityError(
            "ENTERPRISE_IDENTITY_NOT_READY",
            "Enterprise identity runtime is not ready",
            status=503,
        )
    return runtime


def _binding(request: Request) -> IdentityBinding:
    binding = getattr(request.state, "enterprise_identity", None)
    if not isinstance(binding, IdentityBinding):
        raise IdentityError(
            "ENTERPRISE_AUTH_REQUIRED",
            "Enterprise authentication is required",
        )
    return binding


@router.get("/config")
def identity_config(request: Request) -> dict[str, object]:
    return _runtime(request).config_payload()


@router.get("/login")
async def login(
    request: Request,
    organization_id: str | None = Query(default=None, max_length=240),
    workspace_id: str | None = Query(default=None, max_length=240),
    return_to: str | None = Query(default="/", max_length=2_048),
) -> RedirectResponse:
    runtime = _runtime(request)
    client, _directory = runtime._require_enterprise()
    binding = secrets.token_urlsafe(48)
    login_url = await client.begin_login(
        browser_binding=binding,
        organization_id=(organization_id or runtime.config.default_organization_id),
        workspace_id=(workspace_id or runtime.config.default_workspace_id),
        return_path=_safe_return_path(return_to),
    )
    response = RedirectResponse(login_url, status_code=302)
    runtime._set_transaction_cookie(response, binding)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = Query(default=None, max_length=8_192),
    state: str | None = Query(default=None, max_length=1_024),
    error: str | None = Query(default=None, max_length=300),
) -> Response:
    runtime = _runtime(request)
    client, _directory = runtime._require_enterprise()
    if error:
        try:
            client.store.consume_pending(
                state or "",
                browser_binding=request.cookies.get(runtime.transaction_cookie_name),
            )
            login_error: IdentityError | IdentityContextError = IdentityError(
                "OIDC_LOGIN_REJECTED",
                "OIDC login was rejected",
            )
        except (IdentityError, IdentityContextError) as exc:
            login_error = exc
        response = identity_error_response(request, login_error)
        runtime.clear_transaction_cookie(response)
        return response
    try:
        completed = await client.complete_login_transaction(
            code=code or "",
            state=state or "",
            browser_binding=request.cookies.get(runtime.transaction_cookie_name),
        )
    except (IdentityError, IdentityContextError) as exc:
        response = identity_error_response(request, exc)
        runtime.clear_transaction_cookie(response)
        return response
    session = completed.session
    if session.selected_organization_id is not None:
        try:
            await runtime.validate_selected_session(
                session,
                request_id=str(getattr(request.state, "request_id", "") or secrets.token_hex(16)),
            )
        except (IdentityError, IdentityContextError) as exc:
            client.store.revoke(session.session_id)
            response = identity_error_response(request, exc)
            runtime.clear_transaction_cookie(response)
            return response
    response = RedirectResponse(_safe_return_path(completed.return_path), status_code=303)
    runtime._set_session_cookie(response, session.session_id)
    runtime.clear_transaction_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/me")
def me(request: Request) -> dict[str, object]:
    return _runtime(request).response_payload(_binding(request))


@router.post("/context")
async def select_context(request: Request, body: ContextSelection) -> Response:
    runtime = _runtime(request)
    client, _directory = runtime._require_enterprise()
    runtime.require_csrf_origin(request)
    binding = _binding(request)
    if binding.authentication_mode != "COOKIE" or binding.session is None:
        raise IdentityError(
            "ENTERPRISE_SESSION_REQUIRED",
            "Context switching requires a browser session",
            status=403,
        )
    if body.expected_context_version != binding.session.context_version:
        raise IdentityError(
            "ENTERPRISE_CONTEXT_FENCED",
            "Enterprise context version is stale",
            status=409,
        )
    candidate = client.store.preview_context(
        binding.session.session_id,
        organization_id=body.organization_id,
        workspace_id=body.workspace_id,
    )
    await runtime.validate_selected_session(
        candidate,
        request_id=str(getattr(request.state, "request_id", "") or secrets.token_hex(16)),
    )
    rotated = await client.rotate_context(
        binding.session.session_id,
        organization_id=body.organization_id,
        workspace_id=body.workspace_id,
        expected_context_version=body.expected_context_version,
        expected_generation=binding.session.session_generation,
    )
    context = await runtime.validate_selected_session(
        rotated,
        request_id=str(getattr(request.state, "request_id", "") or secrets.token_hex(16)),
    )
    next_binding = IdentityBinding(
        context=context,
        authentication_mode="COOKIE",
        session=rotated,
    )
    response = JSONResponse(runtime.response_payload(next_binding))
    runtime._set_session_cookie(response, rotated.session_id)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/refresh")
async def refresh(request: Request) -> Response:
    runtime = _runtime(request)
    client, _directory = runtime._require_enterprise()
    runtime.require_csrf_origin(request)
    binding = _binding(request)
    if binding.authentication_mode != "COOKIE" or binding.session is None:
        raise IdentityError(
            "ENTERPRISE_SESSION_REQUIRED",
            "Session refresh requires a browser session",
            status=403,
        )
    session = await client.refresh_session(binding.session.session_id)
    context = await runtime.validate_selected_session(
        session,
        request_id=str(getattr(request.state, "request_id", "") or secrets.token_hex(16)),
    )
    next_binding = IdentityBinding(context=context, authentication_mode="COOKIE", session=session)
    response = JSONResponse(runtime.response_payload(next_binding))
    runtime._set_session_cookie(response, session.session_id)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/logout")
async def logout(request: Request) -> Response:
    runtime = _runtime(request)
    client, _directory = runtime._require_enterprise()
    runtime.require_csrf_origin(request)
    binding = _binding(request)
    if binding.authentication_mode != "COOKIE" or binding.session is None:
        raise IdentityError(
            "ENTERPRISE_SESSION_REQUIRED",
            "Logout requires a browser session",
            status=403,
        )
    try:
        provider_logout = await client.logout(binding.session.session_id)
    except IdentityError as exc:
        response = identity_error_response(request, exc)
        runtime.clear_session_cookie(response)
        return response
    response: Response
    if provider_logout:
        response = RedirectResponse(provider_logout, status_code=303)
    else:
        response = Response(status_code=204)
    runtime.clear_session_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/backchannel-logout")
async def backchannel_logout(request: Request) -> Response:
    runtime = _runtime(request)
    client, _directory = runtime._require_enterprise()
    content_type = str(request.headers.get("Content-Type") or "").split(";", 1)[0].strip()
    if content_type != "application/x-www-form-urlencoded":
        raise IdentityError(
            "OIDC_LOGOUT_TOKEN_INVALID",
            "Backchannel logout content type is invalid",
        )
    body = await request.body()
    if len(body) > 32_768:
        raise IdentityError(
            "OIDC_LOGOUT_TOKEN_INVALID",
            "Backchannel logout request is too large",
        )
    try:
        form = parse_qs(
            body.decode("ascii"),
            strict_parsing=True,
            max_num_fields=2,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise IdentityError(
            "OIDC_LOGOUT_TOKEN_INVALID",
            "Backchannel logout request is malformed",
        ) from exc
    tokens = form.get("logout_token")
    if not isinstance(tokens, list) or len(tokens) != 1:
        raise IdentityError(
            "OIDC_LOGOUT_TOKEN_INVALID",
            "Backchannel logout token is required",
        )
    await client.consume_backchannel_logout(tokens[0])
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


def install_enterprise_identity(app: FastAPI) -> None:
    app.include_router(router)
    # Imported lazily to keep the identity runtime independent from product
    # modules while installing the exact tenant-safe facade alongside it.
    from app.enterprise.product_bridge import install_enterprise_product_bridge

    install_enterprise_product_bridge(app)

    async def _identity_error_handler(
        request: Request,
        exc: IdentityError | IdentityContextError,
    ) -> JSONResponse:
        return identity_error_response(request, exc)

    app.add_exception_handler(IdentityError, _identity_error_handler)
    app.add_exception_handler(IdentityContextError, _identity_error_handler)


__all__ = [
    "AUTH_PREFIX",
    "BACKCHANNEL_LOGOUT_PATH",
    "CALLBACK_PATH",
    "CONFIG_PATH",
    "CONTEXT_PATH",
    "EnterpriseIdentityRuntime",
    "IdentityBinding",
    "LOGIN_PATH",
    "LOGOUT_PATH",
    "ME_PATH",
    "REFRESH_PATH",
    "build_enterprise_identity_runtime",
    "enterprise_route_is_tenant_ready",
    "identity_error_response",
    "identity_job_context",
    "install_enterprise_identity",
]
