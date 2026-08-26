"""Signed database ownership/reference contract for tenant document objects."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.enterprise.contracts_v1 import ResourceScope, ResourceScopeType, TenantContext

from .context import require_enterprise_workspace_context
from .models import TenantContextRequiredError, TenantResourceKind


@dataclass(frozen=True, slots=True)
class TenantObjectReference:
    resource_kind: TenantResourceKind
    resource_id: str
    scope: ResourceScope
    object_key: str
    ownership_proof: str
    schema_version: str = "tenant-object-reference.v1"

    @property
    def organization_id(self) -> str | None:
        return self.scope.organization_id

    @property
    def workspace_id(self) -> str | None:
        return self.scope.workspace_id


class TenantObjectReferenceFactory:
    def __init__(self, signing_key: bytes) -> None:
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("object reference signing key must contain at least 32 bytes")
        self._signing_key = signing_key

    @staticmethod
    def _require_workspace_scope(
        tenant: TenantContext,
        scope: ResourceScope,
    ) -> tuple[str, str]:
        organization_id, workspace_id = require_enterprise_workspace_context(tenant)
        if scope.scope_type is not ResourceScopeType.WORKSPACE:
            raise TenantContextRequiredError("document objects require WORKSPACE scope")
        if (
            scope.organization_id != organization_id
            or scope.workspace_id != workspace_id
        ):
            raise TenantContextRequiredError("object scope must match the tenant")
        return organization_id, workspace_id

    @staticmethod
    def _filename(value: str) -> str:
        normalized = value.strip()
        path = PurePosixPath(normalized)
        if (
            not normalized
            or path.name != normalized
            or normalized in {".", ".."}
            or "\\" in normalized
        ):
            raise ValueError("filename must be one safe basename")
        return normalized

    def create(
        self,
        tenant: TenantContext,
        *,
        scope: ResourceScope,
        resource_kind: TenantResourceKind | str,
        resource_id: str,
        filename: str,
    ) -> TenantObjectReference:
        organization_id, workspace_id = self._require_workspace_scope(tenant, scope)
        resolved_kind = TenantResourceKind(resource_kind)
        resolved_id = resource_id.strip()
        if not resolved_id or len(resolved_id) > 160:
            raise ValueError("resource_id must be non-empty and at most 160 characters")
        safe_name = self._filename(filename)
        partition = hashlib.sha256(
            f"{organization_id}\0{workspace_id}".encode("utf-8")
        ).hexdigest()
        resource_digest = hashlib.sha256(resolved_id.encode("utf-8")).hexdigest()
        object_key = (
            f"organizations/{organization_id}/workspaces/{workspace_id}/"
            f"{partition[:16]}/{resolved_kind.value.lower()}/"
            f"{resource_digest}/{safe_name}"
        )
        payload = self._payload(
            tenant,
            scope=scope,
            resource_kind=resolved_kind,
            resource_id=resolved_id,
            object_key=object_key,
        )
        proof = hmac.new(self._signing_key, payload, hashlib.sha256).hexdigest()
        return TenantObjectReference(
            resource_kind=resolved_kind,
            resource_id=resolved_id,
            scope=scope,
            object_key=object_key,
            ownership_proof=proof,
        )

    @staticmethod
    def _payload(
        tenant: TenantContext,
        *,
        scope: ResourceScope,
        resource_kind: TenantResourceKind,
        resource_id: str,
        object_key: str,
    ) -> bytes:
        return json.dumps(
            {
                "tenant": tenant.model_dump(mode="json"),
                "scope": scope.model_dump(mode="json"),
                "resource_kind": resource_kind.value,
                "resource_id": resource_id,
                "object_key": object_key,
                "schema_version": "tenant-object-reference.v1",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def require_authorized(
        self,
        tenant: TenantContext,
        reference: TenantObjectReference,
    ) -> None:
        self._require_workspace_scope(tenant, reference.scope)
        payload = self._payload(
            tenant,
            scope=reference.scope,
            resource_kind=reference.resource_kind,
            resource_id=reference.resource_id,
            object_key=reference.object_key,
        )
        expected = hmac.new(self._signing_key, payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, reference.ownership_proof):
            raise TenantContextRequiredError("object ownership proof is invalid")


__all__ = ["TenantObjectReference", "TenantObjectReferenceFactory"]
