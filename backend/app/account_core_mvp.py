"""Minimal Enterprise Account Core ASGI runtime for the Windows Desktop.

This entry point intentionally excludes teaching, assessment, document
processing, durable workers, Providers, the Web frontend and OpenTelemetry.
It exposes only health, the Desktop bearer principal contract, and the
tenant-scoped Account Core metadata routes.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import re
import secrets
import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app import orm as orm_runtime
from app.enterprise.contracts_v1 import TenantMode
from app.enterprise.account_workspaces import (
    ACCOUNT_WORKSPACE_PATHS, authenticate_account, router as account_workspace_router,
)
from app.enterprise_tenancy.workspace_bootstrap import AccountWorkspaceBootstrap
from app.enterprise.identity.config import EnterpriseIdentityConfig
from app.enterprise.identity.context import IdentityContextError
from app.enterprise.identity.oidc import IdentityError
from app.enterprise.identity.runtime import (
    EnterpriseIdentityRuntime,
    build_enterprise_identity_runtime,
    enterprise_route_is_tenant_ready,
    identity_error_response,
    install_enterprise_identity,
)
from app.enterprise_tenancy.document_import import EnterpriseDocumentImportService
from app.enterprise_tenancy.workspaces import EnterpriseWorkspaceService
from app.enterprise.structured_sync_api import router as structured_sync_router
from app.enterprise.catalog_api import router as catalog_router
from app.enterprise_tenancy.structured_sync import StructuredSyncService
from app.enterprise_tenancy.account_object_storage import AccountObjectStorage
from app.enterprise_tenancy.context import PostgresTenantTransactionManager
from app.enterprise.identity.admin_overview import from_env as overview_from_env
from app.observability.health import (
    ACCOUNT_CORE_MVP_PROFILE,
    HealthRegistry,
    configure_default_health_probes,
)


RUNTIME_PROFILE = ACCOUNT_CORE_MVP_PROFILE
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
account_core_health_registry = HealthRegistry()
configure_default_health_probes(
    runtime_profile=RUNTIME_PROFILE,
    registry=account_core_health_registry,
)


def _request_id(request: Request) -> str:
    supplied = str(request.headers.get("X-Request-ID") or "").strip()
    return supplied if _REQUEST_ID.fullmatch(supplied) else secrets.token_hex(16)


@asynccontextmanager
async def lifespan(app: FastAPI):
    account_core_health_registry.started = False
    config = EnterpriseIdentityConfig.from_env()
    if config.mode is not TenantMode.ENTERPRISE_SERVER:
        raise RuntimeError(
            "ACCOUNT_CORE_MVP requires MENTORFLOW_DEPLOYMENT_MODE=ENTERPRISE_SERVER"
        )
    engine = None
    identity_runtime = None
    try:
        engine = orm_runtime.configure_enterprise_orm()
        orm_runtime.verify_enterprise_runtime_schema()
        app.state.enterprise_workspace_service = EnterpriseWorkspaceService(engine)
        app.state.structured_sync_service = StructuredSyncService(PostgresTenantTransactionManager(engine))
        object_root = os.environ.get("MENTORFLOW_OBJECT_STORAGE_ROOT", "/var/lib/mentorflow/object-storage")
        app.state.account_object_storage = AccountObjectStorage(object_root)
        app.state.account_workspace_bootstrap = AccountWorkspaceBootstrap(
            engine, issuer=config.keycloak.canonical_identity_issuer
        )
        app.state.enterprise_document_import_service = EnterpriseDocumentImportService(
            engine
        )
        identity_runtime = build_enterprise_identity_runtime(engine=engine)
        app.state.enterprise_identity_runtime = identity_runtime
        app.state.admin_overview_service = overview_from_env(
            engine, config, account_core_health_registry, os.environ,
            native_sessions=identity_runtime.native_sessions)
        account_core_health_registry.started = True
        yield
    finally:
        account_core_health_registry.started = False
        if identity_runtime is not None:
            await identity_runtime.aclose()
        overview = getattr(app.state, "admin_overview_service", None)
        if overview is not None:
            await overview.aclose()
        if engine is not None:
            engine.dispose()


app = FastAPI(
    title="MentorFlow Account Core MVP",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
install_enterprise_identity(app, account_core_only=True)
app.include_router(account_workspace_router)
app.include_router(structured_sync_router)
app.include_router(catalog_router)


@app.get("/health/live")
def liveness() -> dict[str, str]:
    return {
        "status": "live",
        "service": "mentorflow-account-core",
        "runtime_profile": RUNTIME_PROFILE,
    }


@app.get("/health/startup")
def startup() -> JSONResponse:
    started = account_core_health_registry.started
    return JSONResponse(
        {
            "status": "started" if started else "starting",
            "runtime_profile": RUNTIME_PROFILE,
        },
        status_code=200 if started else 503,
    )


@app.get("/health/ready")
def readiness() -> JSONResponse:
    payload = account_core_health_registry.readiness()
    return JSONResponse(
        payload,
        status_code=200 if payload["status"] == "ready" else 503,
    )


@app.middleware("http")
async def account_core_identity_boundary(request: Request, call_next):
    request.state.request_id = _request_id(request)
    runtime = getattr(request.app.state, "enterprise_identity_runtime", None)
    try:
        path = request.url.path
        if path.startswith("/api"):
            if not isinstance(runtime, EnterpriseIdentityRuntime):
                raise IdentityError(
                    "ENTERPRISE_IDENTITY_NOT_READY",
                    "Enterprise identity runtime is not ready",
                    status=503,
                )
            if not runtime.enterprise_enabled:
                raise IdentityError(
                    "ENTERPRISE_IDENTITY_DISABLED",
                    "Enterprise identity is not enabled",
                    status=503,
                )
            if path in ACCOUNT_WORKSPACE_PATHS:
                await authenticate_account(request, runtime)
            elif not runtime.is_public_path(path):
                binding = await runtime.authenticate_request(request)
                request.state.enterprise_identity = binding
                request.state.actor_context = (
                    binding.context.actor if binding.context is not None else None
                )
                request.state.tenant_context = (
                    binding.context.tenant if binding.context is not None else None
                )
                if not enterprise_route_is_tenant_ready(path):
                    raise IdentityError(
                        "ENTERPRISE_ROUTE_NOT_TENANT_READY",
                        "This API route is not bound to Enterprise tenant persistence",
                        status=503,
                    )
        response = await call_next(request)
    except (IdentityError, IdentityContextError) as exc:
        response = identity_error_response(request, exc)
        if request.url.path.startswith('/api/enterprise/sync/'):
            payload = json.loads(response.body)
            payload['sync_status'] = 'NOT_AUTHORIZED' if response.status_code in (401, 403) else 'QUERY_FAILED'
            response = JSONResponse(payload, status_code=response.status_code, headers=dict(response.headers))
            # JSON grew; let the new response compute its own Content-Length.
            response.headers['content-length'] = str(len(response.body))
    except SQLAlchemyError:
        if not request.url.path.startswith('/api/enterprise/sync/'):
            raise
        # Membership lookup can fail before the sync router is reached. A DB
        # outage is not an authorization denial and cannot imply empty state.
        response = JSONResponse({'code': 'SYNC_QUERY_FAILED', 'sync_status': 'QUERY_FAILED'},
                                status_code=503, headers={'Cache-Control': 'no-store'})
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-MentorFlow-Runtime-Profile"] = RUNTIME_PROFILE
    if request.url.path in ACCOUNT_WORKSPACE_PATHS:
        response.headers["Cache-Control"] = "no-store"
    return response


async def _identity_error_handler(
    request: Request,
    exc: IdentityError | IdentityContextError,
) -> JSONResponse:
    return identity_error_response(request, exc)


app.add_exception_handler(IdentityError, _identity_error_handler)
app.add_exception_handler(IdentityContextError, _identity_error_handler)


__all__ = [
    "RUNTIME_PROFILE",
    "account_core_health_registry",
    "app",
    "lifespan",
]
