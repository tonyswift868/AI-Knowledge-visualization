"""Tenant-scoped Enterprise product routes backed only by canonical PostgreSQL."""

from __future__ import annotations

from datetime import datetime
import hashlib
import re
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, Body, FastAPI, Path, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.document_import_integration import DocumentBatchStartIn
from app.enterprise.contracts_v1 import (
    ActorContext,
    ResourceClassification,
    ResourceScopeType,
    TenantContext,
)
from app.enterprise.identity.oidc import IdentityError
from app.enterprise.identity.runtime import EnterpriseIdentityRuntime, IdentityBinding
from app.enterprise_tenancy.asset_manifests import (
    ASSET_MANIFEST_READ_CONTRACT,
    MISSING_ASSET_STATE_CONTRACT,
    AssetManifestRecord,
    AssetManifestRegistrationIn,
    AssetManifestSelectionIn,
)
from app.enterprise_tenancy.context import TenantTransactionContext
from app.enterprise_tenancy.document_import import EnterpriseDocumentImportService
from app.enterprise_tenancy.models import (
    EnterprisePersistenceRequestError,
    TenantResourceDraft,
    TenantResourceKind,
    TenantResourceRecord,
)
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
ACCOUNT_CORE_METADATA_SCHEMA_VERSION = "mentorflow.account-core-metadata.v1"
ACCOUNT_PROGRESS_SNAPSHOT_SCHEMA_VERSION = "mentorflow.account-progress-snapshot.v1"
_T = TypeVar("_T")
_OPAQUE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def _metadata_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    if _WINDOWS_ABSOLUTE_PATH.match(normalized) or normalized.casefold().startswith(
        "file:"
    ):
        raise ValueError(f"{field_name} must not contain a Windows absolute path")
    return normalized


def _opaque_identifier(value: str, field_name: str) -> str:
    normalized = _metadata_text(value, field_name)
    if not _OPAQUE_IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{field_name} must be an opaque identifier")
    return normalized


class AccountProgressSnapshotIn(BaseModel):
    """Pathless learner progress metadata; account/tenant fields are never accepted."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    schema_version: Literal["mentorflow.account-progress-snapshot.v1"] = (
        ACCOUNT_PROGRESS_SNAPSHOT_SCHEMA_VERSION
    )
    snapshot_id: str = Field(min_length=1, max_length=160)
    version: int = Field(ge=1)
    plan_id: str | None = Field(default=None, max_length=160)
    material_id: str = Field(min_length=1, max_length=160)
    normalized_name: str = Field(min_length=1, max_length=320)
    name: str = Field(min_length=1, max_length=320)
    mastery: float = Field(ge=0.0, le=1.0)
    completed_task_count: int = Field(ge=0)
    completed_task_minutes: int = Field(ge=0)
    actual_elapsed_minutes: float = Field(ge=0.0)
    completed_phases: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    status: str = Field(min_length=1, max_length=48)

    @field_validator("snapshot_id", "material_id")
    @classmethod
    def validate_required_identifier(cls, value: str, info) -> str:
        return _opaque_identifier(value, info.field_name)

    @field_validator("plan_id")
    @classmethod
    def validate_optional_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _opaque_identifier(value, "plan_id")

    @field_validator("normalized_name", "name", "status")
    @classmethod
    def reject_local_paths(cls, value: str, info) -> str:
        return _metadata_text(value, info.field_name)

    @field_validator("completed_phases", mode="before")
    @classmethod
    def accept_json_phase_array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("completed_phases")
    @classmethod
    def validate_completed_phases(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            _metadata_text(value, "completed_phases") for value in values
        )
        if any(len(value) > 160 for value in normalized):
            raise ValueError("completed_phases entries must not exceed 160 characters")
        if len(normalized) != len(set(normalized)):
            raise ValueError("completed_phases entries must be unique")
        return normalized


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


def _timestamp(value: datetime | str) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _require_record_tenant(
    record: TenantResourceRecord,
    actor: ActorContext,
    tenant: TenantContext,
) -> None:
    if (
        record.organization_id != tenant.organization_id
        or record.workspace_id != tenant.workspace_id
    ):
        raise EnterprisePersistenceRequestError(
            "ACCOUNT_METADATA_SCOPE_VIOLATION",
            "Account metadata storage returned a record outside the selected tenant",
            status=503,
        )
    if record.owner_subject_id != actor.subject_id:
        raise EnterprisePersistenceRequestError(
            "ACCOUNT_METADATA_OWNER_VIOLATION",
            "Account metadata storage returned a record owned by another account",
            status=503,
        )


def _plan_projection(record: TenantResourceRecord) -> dict[str, Any]:
    plan_id = _opaque_identifier(record.resource_id, "plan.plan_id")
    title = _metadata_text(record.title, "plan.title")
    status = _metadata_text(record.status, "plan.status")
    version = record.payload.get("version", 0)
    if type(version) is not int or version < 0:
        version = 0
    return {
        "plan_id": plan_id,
        "title": title,
        "status": status,
        "version": version,
        "updated_at": _timestamp(record.updated_at),
    }


def _progress_projection(record: TenantResourceRecord) -> dict[str, Any] | None:
    if record.payload.get("schema_version") != ACCOUNT_PROGRESS_SNAPSHOT_SCHEMA_VERSION:
        return None
    try:
        snapshot = AccountProgressSnapshotIn.model_validate(dict(record.payload))
    except ValueError as exc:
        raise EnterprisePersistenceRequestError(
            "ACCOUNT_PROGRESS_METADATA_INVALID",
            "Stored Account progress metadata is invalid",
            status=503,
        ) from exc
    return {
        **snapshot.model_dump(mode="json"),
        "updated_at": _timestamp(record.updated_at),
    }


def _manifest_projection(
    record: AssetManifestRecord,
    actor: ActorContext,
    tenant: TenantContext,
) -> dict[str, Any]:
    if (
        record.issuer != actor.issuer
        or record.subject_id != actor.subject_id
        or record.organization_id != tenant.organization_id
        or record.workspace_id != tenant.workspace_id
    ):
        raise EnterprisePersistenceRequestError(
            "ACCOUNT_MANIFEST_SCOPE_VIOLATION",
            "Asset manifest storage returned a record outside the authenticated account",
            status=503,
        )
    try:
        validated = AssetManifestRegistrationIn.model_validate(
            {
                "local_asset_id": record.local_asset_id,
                "filename": record.filename,
                "mime_type": record.mime_type,
                "size_bytes": record.size_bytes,
                "sha256": record.sha256,
                "import_mode": record.import_mode,
                "portability": record.portability,
                "verified_source_url": record.verified_source_url,
                "device_id": record.device_id,
                "derived_artifact_manifest": dict(record.derived_artifact_manifest),
                "availability": record.availability,
            }
        )
    except ValueError as exc:
        raise EnterprisePersistenceRequestError(
            "ACCOUNT_MANIFEST_METADATA_INVALID",
            "Stored Asset Manifest metadata is invalid",
            status=503,
        ) from exc
    payload = {
        "schema_version": ASSET_MANIFEST_READ_CONTRACT,
        "asset_id": _opaque_identifier(record.asset_id, "asset_id"),
        "asset_revision_id": _opaque_identifier(
            record.asset_revision_id, "asset_revision_id"
        ),
        "issuer": record.issuer,
        "subject_id": record.subject_id,
        "organization_id": record.organization_id,
        "workspace_id": record.workspace_id,
        **validated.model_dump(mode="json"),
        "source_kind": _metadata_text(record.source_kind, "source_kind"),
        "created_at": _timestamp(record.created_at),
        "updated_at": _timestamp(record.updated_at),
    }
    if record.availability == "MISSING":
        payload["missing_state_contract"] = MISSING_ASSET_STATE_CONTRACT
    return payload


def _progress_resource_id(
    actor: ActorContext,
    tenant: TenantContext,
    snapshot_id: str,
) -> str:
    digest = hashlib.sha256(
        (
            f"{actor.issuer}\0{actor.subject_id}\0{tenant.organization_id}\0"
            f"{tenant.workspace_id}\0{snapshot_id}"
        ).encode("utf-8")
    ).hexdigest()
    return f"account-progress-{digest[:48]}"


def _read_account_metadata(
    request: Request,
    actor: ActorContext,
    tenant: TenantContext,
) -> dict[str, Any]:
    workspace = _workspace_service(request).get(
        actor=actor,
        tenant=tenant,
        workspace_id=str(tenant.workspace_id),
    )
    if workspace.get("id") != tenant.workspace_id:
        raise EnterprisePersistenceRequestError(
            "ACCOUNT_METADATA_SCOPE_VIOLATION",
            "Workspace metadata does not match the selected tenant",
            status=503,
        )
    workspace_metadata = {
        "workspace_id": str(workspace["id"]),
        "title": _metadata_text(str(workspace.get("title") or ""), "workspace.title"),
        "status": _metadata_text(
            str(workspace.get("status") or ""), "workspace.status"
        ),
    }
    storage = _document_service(request)
    context = TenantTransactionContext(tenant=tenant, actor=actor)
    with storage.transactions.transaction(context) as transaction:
        plan_records = storage.resources.list_records(
            transaction,
            TenantResourceKind.PLAN,
            limit=200,
        )
        progress_records = storage.resources.list_records(
            transaction,
            TenantResourceKind.STUDENT_EVENT,
            limit=10_000,
        )
        asset_records = storage.assets.list_records(transaction, limit=10_000)

    plans: list[dict[str, Any]] = []
    for record in plan_records:
        if record.owner_subject_id != actor.subject_id:
            continue
        _require_record_tenant(record, actor, tenant)
        plans.append(_plan_projection(record))

    progress: list[dict[str, Any]] = []
    for record in progress_records:
        if record.owner_subject_id != actor.subject_id:
            continue
        _require_record_tenant(record, actor, tenant)
        projected = _progress_projection(record)
        if projected is not None:
            progress.append(projected)

    assets = [_manifest_projection(record, actor, tenant) for record in asset_records]
    return {
        "schema_version": ACCOUNT_CORE_METADATA_SCHEMA_VERSION,
        "context_contract": "enterprise-context.v1",
        "account": {
            "issuer": actor.issuer,
            "subject_id": actor.subject_id,
        },
        "tenant": {
            "organization_id": tenant.organization_id,
            "workspace_id": tenant.workspace_id,
        },
        "workspace": workspace_metadata,
        "plans": plans,
        "progress": progress,
        "resource_manifest": {
            "schema_version": ASSET_MANIFEST_READ_CONTRACT,
            "assets": assets,
        },
    }


def _write_progress_snapshot(
    request: Request,
    actor: ActorContext,
    tenant: TenantContext,
    body: AccountProgressSnapshotIn,
) -> dict[str, Any]:
    storage = _document_service(request)
    context = TenantTransactionContext(tenant=tenant, actor=actor)
    resource_id = _progress_resource_id(actor, tenant, body.snapshot_id)
    payload = body.model_dump(mode="json")
    with storage.transactions.transaction(context) as transaction:
        current = storage.resources.get(
            transaction,
            TenantResourceKind.STUDENT_EVENT,
            resource_id,
        )
        if current is not None:
            _require_record_tenant(current, actor, tenant)
            existing = _progress_projection(current)
            if existing is None:
                raise EnterprisePersistenceRequestError(
                    "ACCOUNT_PROGRESS_METADATA_INVALID",
                    "Stored Account progress metadata is invalid",
                    status=503,
                )
            existing_payload = {
                key: value for key, value in existing.items() if key != "updated_at"
            }
            if body.version < int(existing["version"]):
                raise EnterprisePersistenceRequestError(
                    "ACCOUNT_PROGRESS_VERSION_CONFLICT",
                    "Progress snapshot version is stale",
                    status=409,
                )
            if body.version == int(existing["version"]):
                if payload != existing_payload:
                    raise EnterprisePersistenceRequestError(
                        "ACCOUNT_PROGRESS_VERSION_CONFLICT",
                        "Progress snapshot version already identifies different metadata",
                        status=409,
                    )
                return existing
        record = storage.resources.upsert(
            transaction,
            TenantResourceDraft(
                resource_id=resource_id,
                kind=TenantResourceKind.STUDENT_EVENT,
                scope_type=ResourceScopeType.PERSONAL,
                owner_subject_id=actor.subject_id,
                classification=ResourceClassification.CONFIDENTIAL,
                title=body.name,
                status=body.status,
                payload=payload,
                search_text=f"{body.name} {body.normalized_name}",
            ),
        )
    projected = _progress_projection(record)
    if projected is None:
        raise RuntimeError("Account progress upsert did not preserve its schema")
    return projected


def _read_asset_manifest(
    request: Request,
    actor: ActorContext,
    tenant: TenantContext,
) -> dict[str, Any]:
    storage = _document_service(request)
    context = TenantTransactionContext(tenant=tenant, actor=actor)
    with storage.transactions.transaction(context) as transaction:
        records = storage.assets.list_records(transaction, limit=10_000)
    return {
        "schema_version": ASSET_MANIFEST_READ_CONTRACT,
        "assets": [_manifest_projection(record, actor, tenant) for record in records],
    }


def _mark_asset_missing(
    request: Request,
    actor: ActorContext,
    tenant: TenantContext,
    asset_id: str,
) -> dict[str, Any]:
    storage = _document_service(request)
    context = TenantTransactionContext(tenant=tenant, actor=actor)
    with storage.transactions.transaction(context) as transaction:
        record = storage.assets.set_availability(transaction, asset_id, "MISSING")
    if record is None:
        raise EnterprisePersistenceRequestError(
            "ASSET_MANIFEST_NOT_FOUND",
            "Asset manifest was not found",
            status=404,
        )
    return _manifest_projection(record, actor, tenant)


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


@router.get("/account/metadata")
async def enterprise_account_metadata(request: Request) -> dict[str, Any]:
    actor, tenant = _context(await _identity(request))
    return await _invoke(lambda: _read_account_metadata(request, actor, tenant))


@router.put("/account/metadata/progress")
async def enterprise_put_account_progress(
    request: Request,
    body: AccountProgressSnapshotIn = Body(),
) -> dict[str, Any]:
    actor, tenant = _context(await _identity(request))
    return await _invoke(
        lambda: _write_progress_snapshot(request, actor, tenant, body)
    )


@router.get("/account/metadata/assets")
async def enterprise_account_asset_manifest(request: Request) -> dict[str, Any]:
    actor, tenant = _context(await _identity(request))
    return await _invoke(lambda: _read_asset_manifest(request, actor, tenant))


@router.post("/account/metadata/assets/{asset_id}/missing")
async def enterprise_mark_account_asset_missing(
    request: Request,
    asset_id: str = Path(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$",
    ),
) -> dict[str, Any]:
    actor, tenant = _context(await _identity(request))
    return await _invoke(
        lambda: _mark_asset_missing(request, actor, tenant, asset_id)
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


_ACCOUNT_CORE_ROUTE_NAMES = frozenset(
    {
        "enterprise_account_asset_manifest",
        "enterprise_account_metadata",
        "enterprise_create_workspace",
        "enterprise_get_goal",
        "enterprise_get_workspace",
        "enterprise_mark_account_asset_missing",
        "enterprise_patch_goal",
        "enterprise_put_account_progress",
        "enterprise_select_documents",
        "enterprise_set_goal",
    }
)


def _account_core_router() -> APIRouter:
    selected = APIRouter()
    selected.routes.extend(
        route for route in router.routes if route.name in _ACCOUNT_CORE_ROUTE_NAMES
    )
    return selected


def install_enterprise_product_bridge(
    app: FastAPI,
    *,
    account_core_only: bool = False,
) -> None:
    app.include_router(_account_core_router() if account_core_only else router)


__all__ = [
    "ACCOUNT_CORE_METADATA_SCHEMA_VERSION",
    "ACCOUNT_PROGRESS_SNAPSHOT_SCHEMA_VERSION",
    "AccountProgressSnapshotIn",
    "ENTERPRISE_PRODUCT_PREFIX",
    "_ACCOUNT_CORE_ROUTE_NAMES",
    "install_enterprise_product_bridge",
    "router",
]
