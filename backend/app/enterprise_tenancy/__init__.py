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
    require_clean_dbapi_checkout,
    require_safe_application_role,
)
from .adapters import (
    InMemoryPersonalLocalResourceStore,
    PersonalLocalDomainRepository,
    PostgresTenantDomainRepository,
)
from .cache_boundary import (
    ENTERPRISE_CACHE_LAYERS,
    TenantCacheIdentityBuilder,
    TenantCacheScope,
    TenantScopedCacheIdentity,
)
from .factory import EnterpriseRepositoryFactory
from .import_seam import LocalToEnterpriseImportService, PersonalLocalImportSource
from .inventory import (
    ENTERPRISE_PERSISTENCE_INVENTORY,
    EnterprisePersistenceInventory,
    LocalPersistenceKind,
    PersistenceDomain,
    PersistenceInventoryItem,
)
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
from .job_context import TenantJobContext, TenantJobResourceReference
from .object_reference import TenantObjectReference, TenantObjectReferenceFactory
from .ports import (
    CostRepositoryPort,
    DocumentRepositoryPort,
    EvidenceRepositoryPort,
    KnowledgeMapRepositoryPort,
    PersistenceMode,
    PlanRepositoryPort,
    ReviewRepositoryPort,
    StudentRepositoryPort,
    TeacherSessionRepositoryPort,
    TenantDomainRecord,
    TenantDomainRepositoryPort,
    TenantDomainWrite,
)
from .repository import (
    DualPersistenceSelector,
    PostgresTenantResourceRepository,
    TenantResourceRepository,
    TenantResourceRepositoryFactory,
)
from .schema import detect_enterprise_schema_version
from .unit_of_work import (
    EnterpriseUnitOfWork,
    EnterpriseUnitOfWorkSession,
    TenantOutboxIntent,
    TenantTransactionManagerPort,
    TransactionalAuditPort,
    TransactionalOutboxPort,
)


__all__ = [
    "ActorContext",
    "CostRepositoryPort",
    "DocumentRepositoryPort",
    "DualPersistenceSelector",
    "ENTERPRISE_CACHE_LAYERS",
    "ENTERPRISE_PERSISTENCE_INVENTORY",
    "EnterpriseImportPlan",
    "EnterprisePersistenceInventory",
    "EnterpriseRepositoryFactory",
    "EnterpriseUnitOfWork",
    "EnterpriseUnitOfWorkSession",
    "EvidenceRepositoryPort",
    "InMemoryPersonalLocalResourceStore",
    "IsolationOperation",
    "KnowledgeMapRepositoryPort",
    "LocalImportDescriptor",
    "LocalPersistenceKind",
    "LocalToEnterpriseImportService",
    "PersonalLocalImportSource",
    "PersonalDataImportPreview",
    "PersonalLocalDomainRepository",
    "PersistenceDomain",
    "PersistenceInventoryItem",
    "PersistenceMode",
    "PlanRepositoryPort",
    "PostgresTenantDomainRepository",
    "PostgresTenantResourceRepository",
    "PostgresTenantTransactionManager",
    "ResourceClassification",
    "ResourceScopeType",
    "ReviewRepositoryPort",
    "StudentRepositoryPort",
    "TeacherSessionRepositoryPort",
    "TenantCacheIdentityBuilder",
    "TenantCacheScope",
    "TenantConnection",
    "TenantDomainRecord",
    "TenantDomainRepositoryPort",
    "TenantDomainWrite",
    "TenantDbSession",
    "TenantContext",
    "TenantIsolationCorpus",
    "TenantIsolationProbe",
    "TenantJobContext",
    "TenantJobResourceReference",
    "TenantMode",
    "TenantObjectReference",
    "TenantObjectReferenceFactory",
    "TenantOutboxIntent",
    "TenantTransactionContext",
    "TenantResourceDraft",
    "TenantResourceKind",
    "TenantResourceRecord",
    "TenantResourceRepository",
    "TenantResourceRepositoryFactory",
    "TenantScopedCacheIdentity",
    "TenantTransactionManagerPort",
    "TransactionalAuditPort",
    "TransactionalOutboxPort",
    "build_tenant_isolation_corpus",
    "create_enterprise_postgres_engine",
    "detect_enterprise_schema_version",
    "require_clean_dbapi_checkout",
    "require_safe_application_role",
]
