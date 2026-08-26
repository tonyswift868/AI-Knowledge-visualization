"""HMAC-bound tenant partitioning for all Enterprise cache identities."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from app.cost.cache_observatory.contracts import CacheLayer
from app.enterprise.contracts_v1 import TenantContext

from .context import require_enterprise_workspace_context
from .models import TenantContextRequiredError


ENTERPRISE_CACHE_LAYERS = frozenset(CacheLayer)


@dataclass(frozen=True, slots=True)
class TenantCacheScope:
    organization_id: str
    workspace_id: str
    partition: str
    proof: str


@dataclass(frozen=True, slots=True)
class TenantScopedCacheIdentity:
    layer: CacheLayer
    tenant_scope: TenantCacheScope
    workload_identity: str
    schema_version: str = "tenant-cache-identity.v1"

    @property
    def cache_key(self) -> str:
        encoded = json.dumps(
            {
                "schema_version": self.schema_version,
                "layer": self.layer.value,
                "tenant_partition": self.tenant_scope.partition,
                "tenant_proof": self.tenant_scope.proof,
                "workload_identity": self.workload_identity,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class TenantCacheIdentityBuilder:
    """Only a holder of the server secret can mint a valid tenant cache scope."""

    def __init__(self, signing_key: bytes) -> None:
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("tenant cache signing key must contain at least 32 bytes")
        self._signing_key = signing_key

    @staticmethod
    def _payload(organization_id: str, workspace_id: str) -> bytes:
        return json.dumps(
            {
                "organization_id": organization_id,
                "workspace_id": workspace_id,
                "schema_version": "tenant-cache-scope.v1",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def scope(self, tenant: TenantContext) -> TenantCacheScope:
        organization_id, workspace_id = require_enterprise_workspace_context(tenant)
        payload = self._payload(organization_id, workspace_id)
        proof = hmac.new(self._signing_key, payload, hashlib.sha256).hexdigest()
        partition = hashlib.sha256(payload + proof.encode("ascii")).hexdigest()
        return TenantCacheScope(
            organization_id=organization_id,
            workspace_id=workspace_id,
            partition=partition,
            proof=proof,
        )

    def build(
        self,
        tenant: TenantContext,
        *,
        layer: CacheLayer | str,
        workload_identity: str,
    ) -> TenantScopedCacheIdentity:
        identity = workload_identity.strip()
        if not identity or len(identity) > 512:
            raise ValueError("workload_identity must be a bounded opaque identifier")
        return TenantScopedCacheIdentity(
            layer=CacheLayer(layer),
            tenant_scope=self.scope(tenant),
            workload_identity=identity,
        )

    def require_for_tenant(
        self,
        tenant: TenantContext,
        identity: TenantScopedCacheIdentity,
    ) -> None:
        expected = self.scope(tenant)
        actual = identity.tenant_scope
        if not hmac.compare_digest(actual.proof, expected.proof):
            raise TenantContextRequiredError("cache identity tenant proof is invalid")
        if actual.partition != expected.partition:
            raise TenantContextRequiredError("cache identity belongs to another tenant")
        if (
            actual.organization_id != expected.organization_id
            or actual.workspace_id != expected.workspace_id
        ):
            raise TenantContextRequiredError("cache identity tenant labels are invalid")


__all__ = [
    "ENTERPRISE_CACHE_LAYERS",
    "TenantCacheIdentityBuilder",
    "TenantCacheScope",
    "TenantScopedCacheIdentity",
]
