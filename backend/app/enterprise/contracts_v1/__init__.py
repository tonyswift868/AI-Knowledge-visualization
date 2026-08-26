"""Canonical enterprise context contract v1.

This package is the single shared definition for identity context, tenant
context, resource scope, authorization decisions, and audit envelopes.  It is
intentionally independent from any OIDC vendor, HTTP framework, ORM, or
database driver.
"""

from .manifest import (
    CONTRACT_HASH_MANIFEST_FILENAME,
    compute_contract_hashes,
    load_frozen_contract_hashes,
    verify_frozen_contract_hashes,
)
from .models import (
    ENTERPRISE_CONTEXT_CONTRACT_VERSION,
    AccessDecision,
    AccessDecisionKind,
    ActorContext,
    ActorKind,
    AuditEnvelope,
    AuditMetadataEntry,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)

__all__ = [
    "CONTRACT_HASH_MANIFEST_FILENAME",
    "ENTERPRISE_CONTEXT_CONTRACT_VERSION",
    "AccessDecision",
    "AccessDecisionKind",
    "ActorContext",
    "ActorKind",
    "AuditEnvelope",
    "AuditMetadataEntry",
    "ResourceClassification",
    "ResourceScope",
    "ResourceScopeType",
    "TenantContext",
    "TenantMode",
    "compute_contract_hashes",
    "load_frozen_contract_hashes",
    "verify_frozen_contract_hashes",
]
