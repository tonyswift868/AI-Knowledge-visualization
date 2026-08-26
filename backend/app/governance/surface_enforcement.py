from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol

from app.enterprise.contracts_v1 import ResourceScope, TenantContext, TenantMode
from app.governance.authorization import AuthorizationService
from app.governance.models import (
    GovernanceActorAdapter,
    PolicyDecision,
    PolicyRequest,
    RelationshipContext,
)
from app.governance.surface_manifest import (
    ENTERPRISE_PROTECTED_SURFACE_MANIFEST,
    ProtectedSurfaceManifest,
    ProtectedSurfaceOperation,
    RequiredPolicy,
)


def _required(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("surface enforcement timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class ContextTrustSource(str, Enum):
    IDENTITY_RESOLVER = "IDENTITY_RESOLVER"
    SERVICE_ACCOUNT = "SERVICE_ACCOUNT"
    DEMO_FIXTURE = "DEMO_FIXTURE"


class UntrustedContextError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TrustedEnterpriseContext:
    """Canonical context produced by a trusted resolver, never transport claims."""

    subject: GovernanceActorAdapter
    tenant: TenantContext
    source: ContextTrustSource
    validated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "validated_at", _utc(self.validated_at))
        actor = self.subject.actor
        if self.tenant.mode is TenantMode.PERSONAL_LOCAL:
            if actor.organization_id is not None or actor.workspace_id is not None:
                raise UntrustedContextError("personal local trusted context carries tenant claims")
            return
        if actor.organization_id != self.tenant.organization_id:
            raise UntrustedContextError("trusted actor and tenant organization do not match")
        if (
            actor.workspace_id is not None
            and self.tenant.workspace_id is not None
            and actor.workspace_id != self.tenant.workspace_id
        ):
            raise UntrustedContextError("trusted actor and tenant workspace do not match")


@dataclass(frozen=True, slots=True)
class ProtectedResourceReference:
    resource_id: str
    resource_scope: ResourceScope
    relationships: RelationshipContext = field(default_factory=RelationshipContext)

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_id", _required("resource_id", self.resource_id))


@dataclass(frozen=True, slots=True)
class RequestAuthorizationReceipt:
    operation: ProtectedSurfaceOperation
    decision: PolicyDecision
    context_source: ContextTrustSource
    transport_claims_ignored: bool = True

    @property
    def allowed(self) -> bool:
        return self.decision.allowed

    @property
    def reason_code(self) -> str:
        return self.decision.reason_code


def _policy_request(
    *,
    context: TrustedEnterpriseContext,
    operation: ProtectedSurfaceOperation,
    resource: ProtectedResourceReference,
    trace_id: str,
    occurred_at: datetime | None,
) -> PolicyRequest:
    if operation.required_policy is not RequiredPolicy.AUTHORIZATION:
        raise ValueError(
            f"surface operation {operation.operation_id} does not use AuthorizationService"
        )
    return PolicyRequest(
        subject=context.subject,
        tenant=context.tenant,
        action=operation.action,
        resource=operation.resource_type,
        resource_id=resource.resource_id,
        resource_scope=resource.resource_scope,
        relationships=resource.relationships,
        trace_id=_required("trace_id", trace_id),
        occurred_at=_utc(occurred_at or datetime.now(timezone.utc)),
        surface_operation_id=operation.operation_id,
    )


def authorize_request(
    authorization: AuthorizationService,
    *,
    context: TrustedEnterpriseContext,
    operation_id: str,
    resource: ProtectedResourceReference,
    trace_id: str,
    occurred_at: datetime | None = None,
    manifest: ProtectedSurfaceManifest = ENTERPRISE_PROTECTED_SURFACE_MANIFEST,
) -> RequestAuthorizationReceipt:
    """Endpoint-neutral guard using only trusted canonical context."""

    operation = manifest.require(operation_id)
    decision = authorization.authorize(
        _policy_request(
            context=context,
            operation=operation,
            resource=resource,
            trace_id=trace_id,
            occurred_at=occurred_at,
        )
    )
    return RequestAuthorizationReceipt(
        operation=operation,
        decision=decision,
        context_source=context.source,
    )


class FastAPIContextResolver(Protocol):
    def resolve(self, request: object) -> TrustedEnterpriseContext: ...


class FastAPIRequestGuard:
    """FastAPI-compatible adapter; query/header roles and tenant IDs are ignored."""

    def __init__(
        self,
        authorization: AuthorizationService,
        context_resolver: FastAPIContextResolver,
        *,
        manifest: ProtectedSurfaceManifest = ENTERPRISE_PROTECTED_SURFACE_MANIFEST,
    ) -> None:
        self._authorization = authorization
        self._context_resolver = context_resolver
        self._manifest = manifest

    def authorize(
        self,
        request: object,
        *,
        operation_id: str,
        resource: ProtectedResourceReference,
        trace_id: str,
        occurred_at: datetime | None = None,
    ) -> RequestAuthorizationReceipt:
        context = self._context_resolver.resolve(request)
        return authorize_request(
            self._authorization,
            context=context,
            operation_id=operation_id,
            resource=resource,
            trace_id=trace_id,
            occurred_at=occurred_at,
            manifest=self._manifest,
        )


@dataclass(frozen=True, slots=True)
class AuthorizedServiceCall:
    context: TrustedEnterpriseContext
    operation_id: str
    resource: ProtectedResourceReference
    trace_id: str
    required_service_permission: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_id", _required("operation_id", self.operation_id))
        object.__setattr__(self, "trace_id", _required("trace_id", self.trace_id))
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at))
        if self.required_service_permission is not None:
            object.__setattr__(
                self,
                "required_service_permission",
                _required("required_service_permission", self.required_service_permission),
            )
        object.__setattr__(
            self,
            "correlation",
            MappingProxyType(dict(self.correlation)),
        )


@dataclass(frozen=True, slots=True)
class ServiceAuthorizationReceipt:
    call: AuthorizedServiceCall
    operation: ProtectedSurfaceOperation
    decision: PolicyDecision

    @property
    def allowed(self) -> bool:
        return self.decision.allowed

    @property
    def reason_code(self) -> str:
        return self.decision.reason_code


def authorize_service_call(
    authorization: AuthorizationService,
    call: AuthorizedServiceCall,
    *,
    manifest: ProtectedSurfaceManifest = ENTERPRISE_PROTECTED_SURFACE_MANIFEST,
) -> ServiceAuthorizationReceipt:
    operation = manifest.require(call.operation_id)
    relationships = replace(
        call.resource.relationships,
        required_service_permission=call.required_service_permission,
    )
    request = _policy_request(
        context=call.context,
        operation=operation,
        resource=replace(call.resource, relationships=relationships),
        trace_id=call.trace_id,
        occurred_at=call.occurred_at,
    )
    return ServiceAuthorizationReceipt(
        call=call,
        operation=operation,
        decision=authorization.authorize(request),
    )


__all__ = [
    "AuthorizedServiceCall",
    "ContextTrustSource",
    "FastAPIContextResolver",
    "FastAPIRequestGuard",
    "ProtectedResourceReference",
    "RequestAuthorizationReceipt",
    "ServiceAuthorizationReceipt",
    "TrustedEnterpriseContext",
    "UntrustedContextError",
    "authorize_request",
    "authorize_service_call",
]
