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
    TenantDbSession,
    TenantConnection,
    TenantTransactionContext,
    create_enterprise_postgres_engine,
    require_safe_application_role,
)
from .import_seam import LocalToEnterpriseImportService, PersonalLocalImportSource
from .isolation_corpus import (
    IsolationOperation,
    TenantIsolationCorpus,
    TenantIsolationProbe,
    build_tenant_isolation_corpus,
)
from .models import (
    EnterpriseImportPlan,
    LocalImportDescriptor,
    PersonalDataImportPreview,
    TenantResourceDraft,
    TenantResourceKind,
    TenantResourceRecord,
)
from .repository import (
    DualPersistenceSelector,
    PostgresTenantResourceRepository,
    TenantResourceRepository,
    TenantResourceRepositoryFactory,
)
from .schema import detect_enterprise_schema_version


__all__ = [
    "ActorContext",
    "DualPersistenceSelector",
    "EnterpriseImportPlan",
    "IsolationOperation",
    "LocalImportDescriptor",
    "LocalToEnterpriseImportService",
    "PersonalLocalImportSource",
    "PersonalDataImportPreview",
    "PostgresTenantResourceRepository",
    "PostgresTenantTransactionManager",
    "ResourceClassification",
    "ResourceScopeType",
    "TenantConnection",
    "TenantDbSession",
    "TenantContext",
    "TenantIsolationCorpus",
    "TenantIsolationProbe",
    "TenantMode",
    "TenantTransactionContext",
    "TenantResourceDraft",
    "TenantResourceKind",
    "TenantResourceRecord",
    "TenantResourceRepository",
    "TenantResourceRepositoryFactory",
    "build_tenant_isolation_corpus",
    "create_enterprise_postgres_engine",
    "detect_enterprise_schema_version",
    "require_safe_application_role",
]
