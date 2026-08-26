"""Enterprise PostgreSQL tenancy foundation with fail-closed RLS."""

from app.enterprise.contracts_v1 import (
    ActorContext,
    ResourceClassification,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)

from .context import (
    PostgresTenantTransactionManager,
    TenantConnection,
    create_enterprise_postgres_engine,
    require_safe_application_role,
)
from .import_seam import LocalToEnterpriseImportService, PersonalLocalImportSource
from .models import (
    LocalImportDescriptor,
    TenantResourceDraft,
    TenantResourceKind,
    TenantResourceRecord,
)
from .repository import (
    DualPersistenceSelector,
    PostgresTenantResourceRepository,
    TenantResourceRepository,
)


__all__ = [
    "ActorContext",
    "DualPersistenceSelector",
    "LocalImportDescriptor",
    "LocalToEnterpriseImportService",
    "PersonalLocalImportSource",
    "PostgresTenantResourceRepository",
    "PostgresTenantTransactionManager",
    "ResourceClassification",
    "ResourceScopeType",
    "TenantConnection",
    "TenantContext",
    "TenantMode",
    "TenantResourceDraft",
    "TenantResourceKind",
    "TenantResourceRecord",
    "TenantResourceRepository",
    "create_enterprise_postgres_engine",
    "require_safe_application_role",
]
