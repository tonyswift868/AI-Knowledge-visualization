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
from urllib.parse import urlparse

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.engine import Engine

from refactor.server.core_errors import app_error_body
from refactor.server.enterprise.contracts_v1 import ActorContext, TenantContext, TenantMode

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
from .application_roles import ApplicationRole, ApplicationRolePolicy, reject_client_role_assertions


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


@dataclass(frozen=True, slots=True)
class IdentityBinding:
    context: RequestIdentityContext | None
    authentication_mode: str
    session: EnterpriseSession | None = None


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
        native_sessions=None,
        application_roles: ApplicationRolePolicy | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.directory = directory
        self.native_sessions = native_sessions
        self.application_roles = application_roles or ApplicationRolePolicy()
        self.lifecycle = None
        if config.mode is TenantMode.ENTERPRISE_SERVER and (
            client is None or directory is None
        ):
            raise ValueError("enterprise identity runtime requires client and directory")

    @property
    def enterprise_enabled(self) -> bool:
        return self.config.mode is TenantMode.ENTERPRISE_SERVER


    def is_public_path(self, path: str) -> bool:
        from .native import PUBLIC_PATHS
        from .reset_completion import PUBLIC_PATHS as RESET_PATHS
        from .lifecycle_api import PUBLIC_PATHS as LIFECYCLE_PATHS
        from refactor.server.account.closure_api import is_closure_path
        return is_closure_path(path) or path in _PUBLIC_PATHS or path in PUBLIC_PATHS or path in RESET_PATHS or path in LIFECYCLE_PATHS

    async def authenticate_access_token(self, token: str, request: Request):
        from .native import ACCESS_PREFIX, expired
        if token.startswith(ACCESS_PREFIX):
            if request.url.scheme != 'https' or self.native_sessions is None:
                raise expired()
            principal = self.native_sessions.authenticate(token)
        else:
            client, _directory = self._require_enterprise()
            principal = await client.authenticate_access_token(token)
        from refactor.server.account.closure_api import assert_principal_open
        await assert_principal_open(self, principal)
        return principal


    def _require_enterprise(self) -> tuple[KeycloakOidcClient, IdentityDirectoryPort]:
        if not self.enterprise_enabled or self.client is None or self.directory is None:
            raise IdentityError(
                "ENTERPRISE_IDENTITY_DISABLED",
                "Enterprise identity is not enabled in Personal Local mode",
                status=404,
            )
        return self.client, self.directory


    def clear_session_cookie(self, response: Response) -> None:
        response.delete_cookie(
            self.config.session_cookie_name,
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
        from refactor.server.account.closure_api import assert_principal_open
        await assert_principal_open(self, session.principal)
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
            principal = await self.authenticate_access_token(token, request)
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
        from refactor.server.account.closure_api import assert_principal_open
        await assert_principal_open(self, session.principal)
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
        from refactor.server.account_identity_contract import account_id
        return {
            "schema_version": "mentorflow.enterprise-identity-session.v1",
            "authenticated": True,
            "role": self.application_roles.role_for(actor).value,
            "authentication_mode": binding.authentication_mode,
            "account": {
                "account_id": account_id(actor.issuer, actor.subject_id),
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
        if self.lifecycle is not None:
            await self.lifecycle.aclose()
        if self.native_sessions is not None:
            await self.native_sessions.aclose()
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
    from .native import from_env as native_sessions_from_env
    directory = PostgresIdentityDirectory(
        engine,
        issuer=config.keycloak.canonical_identity_issuer,
        service_grants=service_identity_grants_from_env(env),
    )
    runtime = EnterpriseIdentityRuntime(
        config,
        client=KeycloakOidcClient(config),
        directory=directory,
        native_sessions=native_sessions_from_env(env, config.keycloak.canonical_identity_issuer,
            engine=engine, session_ttl_seconds=config.session_ttl_seconds),
        application_roles=ApplicationRolePolicy.from_env(env),
    )
    from .lifecycle_api import from_env as lifecycle_from_env
    runtime.lifecycle = lifecycle_from_env(env)
    from refactor.server.account.closure_api import install_store
    install_store(runtime, engine, config.keycloak.canonical_identity_issuer)
    if runtime.native_sessions is not None:
        from refactor.server.account.closure_api import assert_principal_open
        from .account_lifecycle import LifecycleError
        from .native import expired, unavailable

        async def account_check(principal):
            try:
                await assert_principal_open(runtime, principal)
                if runtime.lifecycle is None or not runtime.lifecycle.active:
                    raise unavailable()
                return await runtime.lifecycle.admin.native_session_state(principal.subject)
            except LifecycleError as exc:
                if exc.code == 'NATIVE_ACCOUNT_REVOKED':
                    raise expired() from None
                raise unavailable() from None
            except IdentityError as exc:
                if exc.status == 403:  # Durable account closure is an explicit revocation.
                    raise expired() from None
                raise

        runtime.native_sessions.account_check = account_check
    return runtime


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


@router.get("/me")
def me(request: Request) -> dict[str, object]:
    reject_client_role_assertions(request)
    return _runtime(request).response_payload(_binding(request))


@router.get("/admin/check")
async def application_admin_check(request: Request) -> dict[str, object]:
    """Read-only authorization gate; grants no tenant or infrastructure access."""
    reject_client_role_assertions(request)
    runtime = _runtime(request)
    binding = await runtime.revalidate_product_identity(request)
    actor = binding.context.actor
    if runtime.application_roles.role_for(actor) is not ApplicationRole.SUPER_ADMIN:
        raise IdentityError("APPLICATION_SUPER_ADMIN_REQUIRED", "Product administrator access is required", status=403)
    return {"authorized": True, "role": ApplicationRole.SUPER_ADMIN.value}


_ACCOUNT_CORE_IDENTITY_ROUTE_NAMES = frozenset({"me", "application_admin_check"})


def _account_core_identity_router() -> APIRouter:
    selected = APIRouter()
    selected.routes.extend(
        route
        for route in router.routes
        if route.name in _ACCOUNT_CORE_IDENTITY_ROUTE_NAMES
    )
    return selected


def install_enterprise_identity(
    app: FastAPI,
    *,
    account_core_only: bool = False,
) -> None:
    app.include_router(_account_core_identity_router())
    from .admin_overview import router as admin_overview_router
    app.include_router(admin_overview_router)
    from .native import router as native_router
    app.include_router(native_router)
    from .reset_completion import router as reset_router
    app.include_router(reset_router)
    from .lifecycle_api import router as lifecycle_router
    app.include_router(lifecycle_router)
    from refactor.server.account.closure_api import router as closure_router
    app.include_router(closure_router)
    async def _identity_error_handler(
        request: Request,
        exc: IdentityError | IdentityContextError,
    ) -> JSONResponse:
        return identity_error_response(request, exc)

    app.add_exception_handler(IdentityError, _identity_error_handler)
    app.add_exception_handler(IdentityContextError, _identity_error_handler)
