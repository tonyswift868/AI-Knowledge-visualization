from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Generic, TypeVar

from app.enterprise.contracts_v1 import (
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
)
from app.governance.authorization import AuthorizationService
from app.governance.models import Action, PolicyRequest, ResourceType
from app.governance.runtime import (
    AuthorizedQueryPolicy,
    AuthorizedResourceCandidate,
    ResourceVisibilityFilter,
)
from app.governance.surface_enforcement import TrustedEnterpriseContext
from app.governance.surface_manifest import (
    ENTERPRISE_PROTECTED_SURFACE_MANIFEST,
    ProtectedSurfaceManifest,
)


T = TypeVar("T")


_SUPPORTED_EXPORT_OPERATIONS = frozenset(
    {
        "document.export",
        "audit.export",
        "student_progress.export",
        "plan.export",
    }
)


@dataclass(frozen=True, slots=True)
class TenantExportRequest(Generic[T]):
    context: TrustedEnterpriseContext
    operation_id: str
    items: tuple[AuthorizedResourceCandidate[T], ...]
    trace_id: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.operation_id not in _SUPPORTED_EXPORT_OPERATIONS:
            raise ValueError("unsupported tenant-aware export operation")
        if not self.trace_id.strip():
            raise ValueError("trace_id must not be empty")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("export timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class TenantExportReceipt(Generic[T]):
    operation_id: str
    candidate_count: int
    request_allowed: bool
    reason_code: str
    exported: tuple[T, ...]
    exported_resource_ids: tuple[str, ...]
    denied: tuple[tuple[str, str], ...]
    audit_sequence: int


class TenantAwareExportService:
    """Export authorization is independent from list/read authorization."""

    def __init__(
        self,
        authorization: AuthorizationService,
        *,
        manifest: ProtectedSurfaceManifest = ENTERPRISE_PROTECTED_SURFACE_MANIFEST,
    ) -> None:
        self._authorization = authorization
        self._manifest = manifest
        self._visibility = ResourceVisibilityFilter(authorization)

    @staticmethod
    def _empty_scope(request: TenantExportRequest[object]) -> ResourceScope:
        tenant = request.context.tenant
        if tenant.workspace_id is not None:
            return ResourceScope(
                scope_type=ResourceScopeType.WORKSPACE,
                organization_id=tenant.organization_id,
                workspace_id=tenant.workspace_id,
                classification=ResourceClassification.RESTRICTED,
            )
        return ResourceScope(
            scope_type=ResourceScopeType.ORGANIZATION,
            organization_id=tenant.organization_id,
            classification=ResourceClassification.RESTRICTED,
        )

    def export(self, request: TenantExportRequest[T]) -> TenantExportReceipt[T]:
        operation = self._manifest.require(request.operation_id)
        if operation.action is not Action.EXPORT:
            raise ValueError("tenant export operation must require Action.EXPORT")
        if not request.items:
            decision = self._authorization.authorize(
                PolicyRequest(
                    subject=request.context.subject,
                    tenant=request.context.tenant,
                    action=Action.EXPORT,
                    resource=operation.resource_type,
                    resource_id=f"empty-export:{operation.operation_id}",
                    resource_scope=self._empty_scope(request),  # type: ignore[arg-type]
                    trace_id=request.trace_id,
                    occurred_at=request.occurred_at,
                    surface_operation_id=operation.operation_id,
                )
            )
            return TenantExportReceipt(
                operation_id=operation.operation_id,
                candidate_count=0,
                request_allowed=decision.allowed,
                reason_code=decision.reason_code,
                exported=(),
                exported_resource_ids=(),
                denied=(),
                audit_sequence=decision.audit_sequence,
            )
        policy = AuthorizedQueryPolicy(
            subject=request.context.subject,
            tenant=request.context.tenant,
            action=Action.EXPORT,
            resource=operation.resource_type,
            trace_id=request.trace_id,
            surface_operation_id=operation.operation_id,
            occurred_at=request.occurred_at,
        )
        decision = self._visibility.authorize(policy, request.items)
        if decision.audit_sequence is None:
            raise RuntimeError("non-empty export authorization did not emit audit")
        allowed = frozenset(decision.allowed_resource_ids)
        if not decision.denied:
            aggregate_reason = "ALL_RESOURCES_ALLOWED"
        elif decision.allowed_resource_ids:
            aggregate_reason = "PARTIAL"
        else:
            aggregate_reason = "ALL_RESOURCES_DENIED"
        return TenantExportReceipt(
            operation_id=operation.operation_id,
            candidate_count=decision.candidate_count,
            request_allowed=not decision.denied,
            reason_code=aggregate_reason,
            exported=tuple(
                item.payload for item in request.items if item.resource_id in allowed
            ),
            exported_resource_ids=decision.allowed_resource_ids,
            denied=decision.denied,
            audit_sequence=decision.audit_sequence,
        )


__all__ = [
    "TenantAwareExportService",
    "TenantExportReceipt",
    "TenantExportRequest",
]
