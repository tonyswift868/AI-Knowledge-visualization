"""Tenant-safe Enterprise facade over the existing Preview product handlers."""

from __future__ import annotations

import inspect
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Body, FastAPI, File, Request, UploadFile
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from app.document_import_integration import DocumentBatchStartIn
from app.enterprise.contracts_v1 import (
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
)
from app.enterprise.identity.oidc import IdentityError
from app.enterprise.identity.runtime import (
    EnterpriseIdentityRuntime,
    IdentityBinding,
)
from app.schemas import (
    GoalOut,
    GoalPatch,
    GoalProfile,
    WorkspaceDetailOut,
    WorkspaceIn,
    WorkspaceOut,
)


ENTERPRISE_PRODUCT_PREFIX = "/api/enterprise"
_SCOPE_REGISTRY_STATE = "enterprise_product_scope_registry"
_WORKSPACE = "workspace"
_DOCUMENT_SELECTION = "document-selection"
_DOCUMENT_BATCH = "document-batch"
_TEACHING_SESSION = "teaching-session"


@dataclass(frozen=True, slots=True)
class _LegacyHandler:
    method: str
    path: str
    name: str


_CREATE_WORKSPACE = _LegacyHandler("POST", "/api/workspaces", "create_workspace")
_GET_WORKSPACE = _LegacyHandler("GET", "/api/workspaces/{workspace_id}", "get_workspace")
_SET_GOAL = _LegacyHandler("POST", "/api/workspaces/{workspace_id}/goal", "set_goal")
_GET_GOAL = _LegacyHandler("GET", "/api/workspaces/{workspace_id}/goal", "get_goal")
_PATCH_GOAL = _LegacyHandler("PATCH", "/api/workspaces/{workspace_id}/goal", "patch_goal")
_SELECT_DOCUMENTS = _LegacyHandler(
    "POST", "/api/document-import/selections", "select_documents"
)
_PREFLIGHT_DOCUMENTS = _LegacyHandler(
    "POST",
    "/api/document-import/selections/{selection_id}/preflight",
    "preflight_documents",
)
_START_DOCUMENT_BATCH = _LegacyHandler(
    "POST",
    "/api/document-import/selections/{selection_id}/batches",
    "start_document_batch",
)
_DOCUMENT_BATCH_PROGRESS = _LegacyHandler(
    "GET",
    "/api/document-import/batches/{batch_id}/progress",
    "document_batch_progress",
)
_DOCUMENT_BATCH_RESULT = _LegacyHandler(
    "GET",
    "/api/document-import/batches/{batch_id}/result",
    "document_batch_result",
)
_START_TEACHING_SESSION = _LegacyHandler(
    "POST",
    "/api/active-teaching/workspaces/{workspace_id}/sessions/start",
    "start_session",
)
_TEACH_NEXT = _LegacyHandler(
    "POST",
    "/api/active-teaching/sessions/{session_id}/teach-next",
    "teach_next",
)


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 240 or any(ord(char) < 32 for char in normalized):
        raise IdentityError(
            "ENTERPRISE_PRODUCT_RESOURCE_INVALID",
            f"{field} is not a valid product resource identifier",
            status=422,
        )
    return normalized


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


class EnterpriseProductScopeRegistry:
    """Bounded authorization metadata for process-local Preview product ids.

    This registry does not contain product payloads and never replaces an
    existing workspace, ingestion, or teaching store.  It only fences opaque
    identifiers returned by those handlers to the frozen ``ResourceScope`` in
    which they were created.  Missing state fails closed after a restart.
    """

    def __init__(self, *, max_entries: int = 10_000) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: dict[tuple[str, str], ResourceScope] = {}
        self._parent_workspaces: dict[tuple[str, str], str] = {}
        self._reservations: set[object] = set()
        self._lock = threading.RLock()

    def reserve_capacity(self) -> object:
        reservation = object()
        with self._lock:
            if len(self._entries) + len(self._reservations) >= self._max_entries:
                raise IdentityError(
                    "ENTERPRISE_PRODUCT_SCOPE_CAPACITY",
                    "Enterprise product authorization registry is at capacity",
                    status=503,
                )
            self._reservations.add(reservation)
        return reservation

    def release_capacity(self, reservation: object) -> None:
        with self._lock:
            self._reservations.discard(reservation)

    def bind(
        self,
        kind: str,
        resource_id: object,
        binding: IdentityBinding,
        *,
        parent_workspace_id: object | None = None,
        reservation: object | None = None,
    ) -> str:
        resolved_id = _identifier(resource_id, f"{kind}_id")
        _actor, tenant = _context(binding)
        resolved_parent = (
            _identifier(parent_workspace_id, "workspace_id")
            if parent_workspace_id is not None
            else None
        )
        scope = ResourceScope(
            scope_type=ResourceScopeType.WORKSPACE,
            owner_subject_id=None,
            organization_id=tenant.organization_id,
            workspace_id=tenant.workspace_id,
            classification=ResourceClassification.CONFIDENTIAL,
        )
        key = (kind, resolved_id)
        with self._lock:
            if reservation is not None and reservation not in self._reservations:
                raise RuntimeError("product scope capacity reservation is invalid")
            current = self._entries.get(key)
            current_parent = self._parent_workspaces.get(key)
            if (
                (current is not None and current != scope)
                or (
                    resolved_parent is not None
                    and current_parent is not None
                    and current_parent != resolved_parent
                )
            ):
                raise IdentityError(
                    "ENTERPRISE_PRODUCT_RESOURCE_SCOPE_CONFLICT",
                    "Product resource is already bound to another tenant scope",
                    status=409,
                )
            if (
                current is None
                and len(self._entries) + len(self._reservations) > self._max_entries
            ):
                raise IdentityError(
                    "ENTERPRISE_PRODUCT_SCOPE_CAPACITY",
                    "Enterprise product authorization registry is at capacity",
                    status=503,
                )
            if reservation is not None:
                self._reservations.remove(reservation)
            self._entries[key] = scope
            if resolved_parent is not None:
                self._parent_workspaces[key] = resolved_parent
        return resolved_id

    def require(self, kind: str, resource_id: object, binding: IdentityBinding) -> str:
        resolved_id = _identifier(resource_id, f"{kind}_id")
        _actor, tenant = _context(binding)
        with self._lock:
            scope = self._entries.get((kind, resolved_id))
        if (
            scope is None
            or scope.scope_type is not ResourceScopeType.WORKSPACE
            or scope.organization_id != tenant.organization_id
            or scope.workspace_id != tenant.workspace_id
        ):
            raise IdentityError(
                "ENTERPRISE_PRODUCT_RESOURCE_FORBIDDEN",
                "Product resource is not available in the selected tenant scope",
                status=403,
            )
        return resolved_id

    def require_parent_workspace(
        self,
        kind: str,
        resource_id: object,
        binding: IdentityBinding,
    ) -> str:
        resolved_id = self.require(kind, resource_id, binding)
        with self._lock:
            parent = self._parent_workspaces.get((kind, resolved_id))
        if parent is None:
            raise IdentityError(
                "ENTERPRISE_PRODUCT_RESOURCE_FORBIDDEN",
                "Product resource is not available in the selected tenant scope",
                status=403,
            )
        return parent

    def require_workspace(self, workspace_id: object, binding: IdentityBinding) -> str:
        resolved_id = _identifier(workspace_id, "workspace_id")
        _actor, tenant = _context(binding)
        # A canonical Enterprise workspace can be used directly.  Product
        # workspaces created by the legacy handler are admitted only through a
        # scope record created by this facade.
        if resolved_id == tenant.workspace_id:
            return resolved_id
        return self.require(_WORKSPACE, resolved_id, binding)


def _registry(request: Request) -> EnterpriseProductScopeRegistry:
    registry = getattr(request.app.state, _SCOPE_REGISTRY_STATE, None)
    if not isinstance(registry, EnterpriseProductScopeRegistry):
        raise IdentityError(
            "ENTERPRISE_PRODUCT_BRIDGE_NOT_READY",
            "Enterprise product route bridge is not ready",
            status=503,
        )
    return registry


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


def _endpoint(request: Request, handler: _LegacyHandler):
    pending = list(request.app.routes)
    visited: set[int] = set()
    while pending:
        route = pending.pop(0)
        if id(route) in visited:
            continue
        visited.add(id(route))
        if (
            isinstance(route, APIRoute)
            and route.path == handler.path
            and route.name == handler.name
            and handler.method in route.methods
        ):
            return route.endpoint
        included = getattr(route, "original_router", None)
        nested_routes = getattr(included, "routes", None)
        if nested_routes is not None:
            pending.extend(nested_routes)
    raise IdentityError(
        "ENTERPRISE_PRODUCT_HANDLER_NOT_READY",
        "The existing product handler is not installed",
        status=503,
    )


async def _delegate(
    bridge_request: Request,
    handler: _LegacyHandler,
    **kwargs: Any,
) -> Any:
    endpoint = _endpoint(bridge_request, handler)
    if inspect.iscoroutinefunction(endpoint):
        return await endpoint(**kwargs)
    return await run_in_threadpool(endpoint, **kwargs)


def _result_identifier(result: object, field: str) -> str:
    if not isinstance(result, Mapping):
        raise IdentityError(
            "ENTERPRISE_PRODUCT_RESULT_INVALID",
            "Existing product handler returned an invalid resource envelope",
            status=502,
        )
    return _identifier(result.get(field), field)


router = APIRouter(prefix=ENTERPRISE_PRODUCT_PREFIX, tags=["enterprise-product"])


@router.post(
    "/workspaces",
    response_model=WorkspaceOut,
    response_model_exclude_unset=True,
)
async def enterprise_create_workspace(body: WorkspaceIn, request: Request):
    binding = await _identity(request)
    registry = _registry(request)
    if body.provider_session_id is not None:
        _identifier(body.provider_session_id, "provider_session_id")
    reservation = registry.reserve_capacity()
    try:
        result = await _delegate(
            request,
            _CREATE_WORKSPACE,
            body=body,
            request=request,
        )
        registry.bind(
            _WORKSPACE,
            _result_identifier(result, "id"),
            binding,
            reservation=reservation,
        )
    except BaseException:
        registry.release_capacity(reservation)
        raise
    return result


@router.get(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceDetailOut,
    response_model_exclude_unset=True,
)
async def enterprise_get_workspace(workspace_id: str, request: Request):
    binding = await _identity(request)
    workspace_id = _registry(request).require_workspace(workspace_id, binding)
    result = await _delegate(request, _GET_WORKSPACE, workspace_id=workspace_id)
    if _result_identifier(result, "id") != workspace_id:
        raise IdentityError(
            "ENTERPRISE_PRODUCT_RESULT_SCOPE_MISMATCH",
            "Existing workspace handler returned a different resource",
            status=502,
        )
    return result


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
    binding = await _identity(request)
    workspace_id = _registry(request).require_workspace(workspace_id, binding)
    result = await _delegate(
        request,
        _SET_GOAL,
        workspace_id=workspace_id,
        body=body,
    )
    if _result_identifier(result, "workspace_id") != workspace_id:
        raise IdentityError(
            "ENTERPRISE_PRODUCT_RESULT_SCOPE_MISMATCH",
            "Existing goal handler returned a different workspace scope",
            status=502,
        )
    return result


@router.get(
    "/workspaces/{workspace_id}/goal",
    response_model=GoalOut,
    response_model_exclude_unset=True,
)
async def enterprise_get_goal(workspace_id: str, request: Request):
    binding = await _identity(request)
    workspace_id = _registry(request).require_workspace(workspace_id, binding)
    result = await _delegate(request, _GET_GOAL, workspace_id=workspace_id)
    if result and _result_identifier(result, "workspace_id") != workspace_id:
        raise IdentityError(
            "ENTERPRISE_PRODUCT_RESULT_SCOPE_MISMATCH",
            "Existing goal handler returned a different workspace scope",
            status=502,
        )
    return result


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
    binding = await _identity(request)
    workspace_id = _registry(request).require_workspace(workspace_id, binding)
    result = await _delegate(
        request,
        _PATCH_GOAL,
        workspace_id=workspace_id,
        body=body,
    )
    if _result_identifier(result, "workspace_id") != workspace_id:
        raise IdentityError(
            "ENTERPRISE_PRODUCT_RESULT_SCOPE_MISMATCH",
            "Existing goal handler returned a different workspace scope",
            status=502,
        )
    return result


@router.post("/document-import/selections")
async def enterprise_select_documents(
    request: Request,
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    binding = await _identity(request)
    registry = _registry(request)
    reservation = registry.reserve_capacity()
    try:
        result = await _delegate(
            request,
            _SELECT_DOCUMENTS,
            request=request,
            files=files,
        )
        registry.bind(
            _DOCUMENT_SELECTION,
            _result_identifier(result, "selection_id"),
            binding,
            reservation=reservation,
        )
    except BaseException:
        registry.release_capacity(reservation)
        raise
    return result


@router.post("/document-import/selections/{selection_id}/preflight")
async def enterprise_preflight_documents(
    request: Request,
    selection_id: str,
) -> dict[str, Any]:
    binding = await _identity(request)
    selection_id = _registry(request).require(
        _DOCUMENT_SELECTION,
        selection_id,
        binding,
    )
    result = await _delegate(
        request,
        _PREFLIGHT_DOCUMENTS,
        request=request,
        selection_id=selection_id,
    )
    if _result_identifier(result, "selection_id") != selection_id:
        raise IdentityError(
            "ENTERPRISE_PRODUCT_RESULT_SCOPE_MISMATCH",
            "Existing document handler returned a different selection resource",
            status=502,
        )
    return result


@router.post("/document-import/selections/{selection_id}/batches")
async def enterprise_start_document_batch(
    request: Request,
    selection_id: str,
    body: DocumentBatchStartIn = Body(),
) -> dict[str, Any]:
    binding = await _identity(request)
    registry = _registry(request)
    selection_id = registry.require(_DOCUMENT_SELECTION, selection_id, binding)
    reservation = registry.reserve_capacity()
    try:
        result = await _delegate(
            request,
            _START_DOCUMENT_BATCH,
            request=request,
            selection_id=selection_id,
            body=body,
        )
        if _result_identifier(result, "selection_id") != selection_id:
            raise IdentityError(
                "ENTERPRISE_PRODUCT_RESULT_SCOPE_MISMATCH",
                "Existing document handler returned a different selection resource",
                status=502,
            )
        registry.bind(
            _DOCUMENT_BATCH,
            _result_identifier(result, "batch_id"),
            binding,
            reservation=reservation,
        )
    except BaseException:
        registry.release_capacity(reservation)
        raise
    return result


@router.get("/document-import/batches/{batch_id}/progress")
async def enterprise_document_batch_progress(
    request: Request,
    batch_id: str,
) -> dict[str, Any]:
    binding = await _identity(request)
    batch_id = _registry(request).require(_DOCUMENT_BATCH, batch_id, binding)
    result = await _delegate(
        request,
        _DOCUMENT_BATCH_PROGRESS,
        request=request,
        batch_id=batch_id,
    )
    if _result_identifier(result, "batch_id") != batch_id:
        raise IdentityError(
            "ENTERPRISE_PRODUCT_RESULT_SCOPE_MISMATCH",
            "Existing document handler returned a different batch resource",
            status=502,
        )
    return result


@router.get("/document-import/batches/{batch_id}/result")
async def enterprise_document_batch_result(
    request: Request,
    batch_id: str,
) -> dict[str, Any] | None:
    binding = await _identity(request)
    batch_id = _registry(request).require(_DOCUMENT_BATCH, batch_id, binding)
    result = await _delegate(
        request,
        _DOCUMENT_BATCH_RESULT,
        request=request,
        batch_id=batch_id,
    )
    if result is not None and _result_identifier(result, "batch_id") != batch_id:
        raise IdentityError(
            "ENTERPRISE_PRODUCT_RESULT_SCOPE_MISMATCH",
            "Existing document handler returned a different batch resource",
            status=502,
        )
    return result


@router.post("/active-teaching/workspaces/{workspace_id}/sessions/start")
async def enterprise_start_teaching_session(
    workspace_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    binding = await _identity(request)
    registry = _registry(request)
    workspace_id = registry.require_workspace(workspace_id, binding)
    reservation = registry.reserve_capacity()
    try:
        result = await _delegate(
            request,
            _START_TEACHING_SESSION,
            workspace_id=workspace_id,
            body=body,
        )
        returned_workspace_id = _result_identifier(result, "workspace_id")
        if returned_workspace_id != workspace_id:
            raise IdentityError(
                "ENTERPRISE_PRODUCT_RESULT_SCOPE_MISMATCH",
                "Existing teaching handler returned a different workspace scope",
                status=502,
            )
        registry.bind(
            _TEACHING_SESSION,
            _result_identifier(result, "session_id"),
            binding,
            parent_workspace_id=workspace_id,
            reservation=reservation,
        )
    except BaseException:
        registry.release_capacity(reservation)
        raise
    return result


@router.post("/active-teaching/sessions/{session_id}/teach-next")
async def enterprise_teach_next(
    session_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    binding = await _identity(request)
    registry = _registry(request)
    session_id = registry.require(
        _TEACHING_SESSION,
        session_id,
        binding,
    )
    expected_workspace_id = registry.require_parent_workspace(
        _TEACHING_SESSION,
        session_id,
        binding,
    )
    result = await _delegate(
        request,
        _TEACH_NEXT,
        session_id=session_id,
        body=body,
    )
    returned_session_id = _result_identifier(result, "session_id")
    if returned_session_id != session_id:
        raise IdentityError(
            "ENTERPRISE_PRODUCT_RESULT_SCOPE_MISMATCH",
            "Existing teaching handler returned a different session resource",
            status=502,
        )
    if _result_identifier(result, "workspace_id") != expected_workspace_id:
        raise IdentityError(
            "ENTERPRISE_PRODUCT_RESULT_SCOPE_MISMATCH",
            "Existing teaching handler returned a different workspace resource",
            status=502,
        )
    return result


def install_enterprise_product_bridge(app: FastAPI) -> None:
    if not isinstance(
        getattr(app.state, _SCOPE_REGISTRY_STATE, None),
        EnterpriseProductScopeRegistry,
    ):
        setattr(app.state, _SCOPE_REGISTRY_STATE, EnterpriseProductScopeRegistry())
    app.include_router(router)


__all__ = [
    "ENTERPRISE_PRODUCT_PREFIX",
    "EnterpriseProductScopeRegistry",
    "install_enterprise_product_bridge",
    "router",
]
