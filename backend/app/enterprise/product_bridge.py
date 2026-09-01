"""Tenant-scoped Enterprise product routes backed only by canonical PostgreSQL."""

from __future__ import annotations

from typing import Any, TypeVar

from fastapi import APIRouter, Body, FastAPI, Request
from starlette.concurrency import run_in_threadpool

from app.document_import_integration import DocumentBatchStartIn
from app.enterprise.identity.oidc import IdentityError
from app.enterprise.identity.runtime import EnterpriseIdentityRuntime, IdentityBinding
from app.enterprise_tenancy.asset_manifests import AssetManifestSelectionIn
from app.enterprise_tenancy.document_import import EnterpriseDocumentImportService
from app.enterprise_tenancy.models import EnterprisePersistenceRequestError
from app.enterprise_tenancy.teaching import EnterpriseTeachingService
from app.enterprise_tenancy.workspaces import EnterpriseWorkspaceService
from app.schemas import (
    GoalOut,
    GoalPatch,
    GoalProfile,
    WorkspaceDetailOut,
    WorkspaceIn,
    WorkspaceOut,
)


ENTERPRISE_PRODUCT_PREFIX = "/api/enterprise"
_T = TypeVar("_T")


async def _identity(request: Request) -> IdentityBinding:
    runtime = getattr(request.app.state, "enterprise_identity_runtime", None)
    if not isinstance(runtime, EnterpriseIdentityRuntime):
        raise IdentityError(
            "ENTERPRISE_IDENTITY_NOT_READY",
            "Enterprise identity runtime is not ready",
            status=503,
        )
    if not runtime.enterprise_enabled:
        raise IdentityError(
            "ENTERPRISE_IDENTITY_DISABLED",
            "Enterprise identity is not enabled in Personal Local mode",
            status=404,
        )
    return await runtime.revalidate_product_identity(request)


def _context(binding: IdentityBinding):
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
    ):
        raise IdentityError(
            "ENTERPRISE_MEMBERSHIP_REQUIRED",
            "An active workspace membership is required for Enterprise product routes",
            status=403,
        )
    return actor, tenant


def _configured_service(request: Request, state_name: str, expected: type[_T]) -> _T:
    service = getattr(request.app.state, state_name, None)
    if not isinstance(service, expected):
        raise IdentityError(
            "ENTERPRISE_PERSISTENCE_NOT_READY",
            "Canonical Enterprise persistence is not ready",
            status=503,
        )
    return service


def _workspace_service(request: Request) -> EnterpriseWorkspaceService:
    return _configured_service(
        request,
        "enterprise_workspace_service",
        EnterpriseWorkspaceService,
    )


def _document_service(request: Request) -> EnterpriseDocumentImportService:
    return _configured_service(
        request,
        "enterprise_document_import_service",
        EnterpriseDocumentImportService,
    )


def _teaching_service(request: Request) -> EnterpriseTeachingService:
    return _configured_service(
        request,
        "enterprise_teaching_service",
        EnterpriseTeachingService,
    )


async def _invoke(operation):
    try:
        return await run_in_threadpool(operation)
    except EnterprisePersistenceRequestError as exc:
        raise IdentityError(exc.code, exc.message, status=exc.status) from exc


router = APIRouter(prefix=ENTERPRISE_PRODUCT_PREFIX, tags=["enterprise-product"])


@router.post(
    "/workspaces",
    response_model=WorkspaceOut,
    response_model_exclude_unset=True,
)
async def enterprise_create_workspace(body: WorkspaceIn, request: Request):
    actor, tenant = _context(await _identity(request))
    return await _invoke(
        lambda: _workspace_service(request).create(
            actor=actor,
            tenant=tenant,
            body=body,
        )
    )


@router.get(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceDetailOut,
    response_model_exclude_unset=True,
)
async def enterprise_get_workspace(workspace_id: str, request: Request):
    actor, tenant = _context(await _identity(request))
    return await _invoke(
        lambda: _workspace_service(request).get(
            actor=actor,
            tenant=tenant,
            workspace_id=workspace_id,
        )
    )


@router.post(
    "/workspaces/{workspace_id}/goal",
    response_model=GoalOut,
    response_model_exclude_unset=True,
)
async def enterprise_set_goal(
    workspace_id: str,
    body: GoalProfile,
    request: Request,
):
    actor, tenant = _context(await _identity(request))
    return await _invoke(
        lambda: _workspace_service(request).set_goal(
            actor=actor,
            tenant=tenant,
            workspace_id=workspace_id,
            body=body,
        )
    )


@router.get(
    "/workspaces/{workspace_id}/goal",
    response_model=GoalOut,
    response_model_exclude_unset=True,
)
async def enterprise_get_goal(workspace_id: str, request: Request):
    actor, tenant = _context(await _identity(request))
    return await _invoke(
        lambda: _workspace_service(request).get_goal(
            actor=actor,
            tenant=tenant,
            workspace_id=workspace_id,
        )
    )


@router.patch(
    "/workspaces/{workspace_id}/goal",
    response_model=GoalOut,
    response_model_exclude_unset=True,
)
async def enterprise_patch_goal(
    workspace_id: str,
    body: GoalPatch,
    request: Request,
):
    actor, tenant = _context(await _identity(request))
    return await _invoke(
        lambda: _workspace_service(request).patch_goal(
            actor=actor,
            tenant=tenant,
            workspace_id=workspace_id,
            body=body,
        )
    )


@router.post("/document-import/selections")
async def enterprise_select_documents(
    request: Request,
    body: AssetManifestSelectionIn = Body(),
) -> dict[str, Any]:
    actor, tenant = _context(await _identity(request))
    try:
        return await _document_service(request).select(
            actor=actor,
            tenant=tenant,
            body=body,
        )
    except EnterprisePersistenceRequestError as exc:
        raise IdentityError(exc.code, exc.message, status=exc.status) from exc


@router.post("/document-import/selections/{selection_id}/preflight")
async def enterprise_preflight_documents(
    request: Request,
    selection_id: str,
) -> dict[str, Any]:
    actor, tenant = _context(await _identity(request))
    return await _invoke(
        lambda: _document_service(request).preflight(
            actor=actor,
            tenant=tenant,
            selection_id=selection_id,
        )
    )


@router.post("/document-import/selections/{selection_id}/batches")
async def enterprise_start_document_batch(
    request: Request,
    selection_id: str,
    body: DocumentBatchStartIn = Body(),
) -> dict[str, Any]:
    actor, tenant = _context(await _identity(request))
    return await _invoke(
        lambda: _document_service(request).start_batch(
            actor=actor,
            tenant=tenant,
            selection_id=selection_id,
            body=body,
        )
    )


@router.get("/document-import/batches/{batch_id}/progress")
async def enterprise_document_batch_progress(
    request: Request,
    batch_id: str,
) -> dict[str, Any]:
    actor, tenant = _context(await _identity(request))
    return await _invoke(
        lambda: _document_service(request).progress(
            actor=actor,
            tenant=tenant,
            batch_id=batch_id,
        )
    )


@router.get("/document-import/batches/{batch_id}/result")
async def enterprise_document_batch_result(
    request: Request,
    batch_id: str,
) -> dict[str, Any] | None:
    actor, tenant = _context(await _identity(request))
    return await _invoke(
        lambda: _document_service(request).result(
            actor=actor,
            tenant=tenant,
            batch_id=batch_id,
        )
    )


@router.post("/active-teaching/workspaces/{workspace_id}/sessions/start")
async def enterprise_start_teaching_session(
    workspace_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    actor, tenant = _context(await _identity(request))
    return await _invoke(
        lambda: _teaching_service(request).start(
            actor=actor,
            tenant=tenant,
            workspace_id=workspace_id,
            body=body,
        )
    )


@router.post("/active-teaching/sessions/{session_id}/teach-next")
async def enterprise_teach_next(
    session_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    actor, tenant = _context(await _identity(request))
    try:
        return await _teaching_service(request).teach_next(
            actor=actor,
            tenant=tenant,
            session_id=session_id,
            body=body,
        )
    except EnterprisePersistenceRequestError as exc:
        raise IdentityError(exc.code, exc.message, status=exc.status) from exc


def install_enterprise_product_bridge(app: FastAPI) -> None:
    app.include_router(router)


__all__ = [
    "ENTERPRISE_PRODUCT_PREFIX",
    "install_enterprise_product_bridge",
    "router",
]
