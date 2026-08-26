"""Tenant context envelope consumed by E1-05 without implementing a queue."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.enterprise.contracts_v1 import (
    ActorContext,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
)

from .context import require_matching_actor_context
from .models import TenantContextRequiredError, TenantResourceKind


@dataclass(frozen=True, slots=True)
class TenantJobResourceReference:
    resource_kind: TenantResourceKind
    resource_id: str
    scope: ResourceScope

    def __post_init__(self) -> None:
        resource_id = self.resource_id.strip()
        if not resource_id or len(resource_id) > 160:
            raise ValueError("resource_id must be non-empty and at most 160 characters")
        object.__setattr__(self, "resource_id", resource_id)


@dataclass(frozen=True, slots=True)
class TenantJobContext:
    """Composite seam built only from the frozen tenant/actor/scope contracts."""

    tenant: TenantContext
    actor: ActorContext
    request_id: str
    trace_id: str
    resources: tuple[TenantJobResourceReference, ...]
    schema_version: str = "tenant-job-context.v1"

    def __post_init__(self) -> None:
        organization_id, workspace_id, subject_id = require_matching_actor_context(
            self.tenant,
            self.actor,
        )
        request_id = self.request_id.strip()
        trace_id = self.trace_id.strip()
        if request_id != self.actor.request_id:
            raise TenantContextRequiredError("job request_id must match ActorContext")
        if not trace_id or len(trace_id) > 240:
            raise ValueError("trace_id must be non-empty and at most 240 characters")
        seen: set[tuple[TenantResourceKind, str]] = set()
        for reference in self.resources:
            key = (reference.resource_kind, reference.resource_id)
            if key in seen:
                raise ValueError("TenantJobContext resource references must be unique")
            seen.add(key)
            scope = reference.scope
            if scope.scope_type is ResourceScopeType.PERSONAL:
                if scope.owner_subject_id != subject_id:
                    raise TenantContextRequiredError(
                        "job PERSONAL resource owner must match the actor"
                    )
            elif scope.scope_type is ResourceScopeType.ORGANIZATION:
                if scope.organization_id != organization_id:
                    raise TenantContextRequiredError(
                        "job organization resource must match the tenant"
                    )
            elif (
                scope.organization_id != organization_id
                or scope.workspace_id != workspace_id
            ):
                raise TenantContextRequiredError(
                    "job workspace resource must match the tenant"
                )
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "trace_id", trace_id)

    @property
    def context_id(self) -> str:
        encoded = json.dumps(
            self.to_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"tenant-job:{hashlib.sha256(encoded).hexdigest()}"

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "tenant": self.tenant.model_dump(mode="json"),
            "actor": self.actor.model_dump(mode="json"),
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "resources": [
                {
                    "resource_kind": item.resource_kind.value,
                    "resource_id": item.resource_id,
                    "scope": item.scope.model_dump(mode="json"),
                }
                for item in self.resources
            ],
        }


__all__ = ["TenantJobContext", "TenantJobResourceReference"]
