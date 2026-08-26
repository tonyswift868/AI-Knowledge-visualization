"""Audited persistence inventory for the E1-02B domain integration boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import RESOURCE_TABLE_BY_KIND, TenantResourceKind


class PersistenceDomain(str, Enum):
    DOCUMENT = "DOCUMENT"
    EVIDENCE = "EVIDENCE"
    KNOWLEDGE_MAP = "KNOWLEDGE_MAP"
    PLAN = "PLAN"
    TEACHER_SESSION = "TEACHER_SESSION"
    REVIEW = "REVIEW"
    STUDENT = "STUDENT"
    COST = "COST"


class LocalPersistenceKind(str, Enum):
    SQLALCHEMY_SQLITE = "SQLALCHEMY_SQLITE"
    SQLITE = "SQLITE"
    ATOMIC_JSON = "ATOMIC_JSON"
    FILESYSTEM = "FILESYSTEM"
    MEMORY = "MEMORY"
    IMMUTABLE_EVIDENCE = "IMMUTABLE_EVIDENCE"


@dataclass(frozen=True, slots=True)
class PersistenceInventoryItem:
    domain: PersistenceDomain
    resource_kind: TenantResourceKind
    enterprise_table: str
    local_persistence: tuple[LocalPersistenceKind, ...]
    local_implementations: tuple[str, ...]
    existing_ports: tuple[str, ...]
    integration_strategy: str = "ADAPT_EXISTING_LOCAL_AND_TENANT_RESOURCE_TABLE"

    def __post_init__(self) -> None:
        if self.enterprise_table != RESOURCE_TABLE_BY_KIND[self.resource_kind]:
            raise ValueError("enterprise table must match the canonical resource kind")
        if not self.local_persistence or not self.local_implementations:
            raise ValueError("every domain must retain an audited Personal Local backend")


@dataclass(frozen=True, slots=True)
class EnterprisePersistenceInventory:
    items: tuple[PersistenceInventoryItem, ...]
    schema_version: str = "enterprise-persistence-inventory.v1"

    def __post_init__(self) -> None:
        domains = [item.domain for item in self.items]
        kinds = [item.resource_kind for item in self.items]
        if len(domains) != len(set(domains)):
            raise ValueError("persistence inventory domains must be unique")
        if len(kinds) != len(set(kinds)):
            raise ValueError("persistence inventory resource kinds must be unique")
        if set(domains) != set(PersistenceDomain):
            raise ValueError("persistence inventory must cover every E1-02B domain")

    def for_domain(self, domain: PersistenceDomain | str) -> PersistenceInventoryItem:
        resolved = PersistenceDomain(domain)
        return next(item for item in self.items if item.domain is resolved)

    def for_kind(
        self,
        kind: TenantResourceKind | str,
    ) -> PersistenceInventoryItem:
        resolved = TenantResourceKind(kind)
        return next(item for item in self.items if item.resource_kind is resolved)


ENTERPRISE_PERSISTENCE_INVENTORY = EnterprisePersistenceInventory(
    items=(
        PersistenceInventoryItem(
            domain=PersistenceDomain.DOCUMENT,
            resource_kind=TenantResourceKind.DOCUMENT,
            enterprise_table="documents",
            local_persistence=(
                LocalPersistenceKind.SQLALCHEMY_SQLITE,
                LocalPersistenceKind.ATOMIC_JSON,
                LocalPersistenceKind.FILESYSTEM,
            ),
            local_implementations=(
                "backend/app/orm.py:SourceORM/ChunkORM",
                "backend/app/sources/document/ingestion/checkpoint.py:DocumentCheckpointStore",
                "backend/app/document_import_integration.py",
            ),
            existing_ports=(
                "backend/app/sources/document/ingestion/bridge.py:DocumentKnowledgePipelinePort",
            ),
        ),
        PersistenceInventoryItem(
            domain=PersistenceDomain.EVIDENCE,
            resource_kind=TenantResourceKind.EVIDENCE,
            enterprise_table="evidence",
            local_persistence=(
                LocalPersistenceKind.IMMUTABLE_EVIDENCE,
                LocalPersistenceKind.FILESYSTEM,
                LocalPersistenceKind.MEMORY,
            ),
            local_implementations=(
                "backend/app/immutable_evidence.py",
                "backend/app/sources/document/evidence.py",
                "backend/app/sources/video/transcript_evidence.py",
            ),
            existing_ports=(),
        ),
        PersistenceInventoryItem(
            domain=PersistenceDomain.KNOWLEDGE_MAP,
            resource_kind=TenantResourceKind.KNOWLEDGE_MAP,
            enterprise_table="knowledge_maps",
            local_persistence=(
                LocalPersistenceKind.SQLALCHEMY_SQLITE,
                LocalPersistenceKind.MEMORY,
            ),
            local_implementations=(
                "backend/app/orm.py:KnowledgeMapRevisionORM/KnowledgeNodeORM/KnowledgeEdgeORM",
                "backend/app/knowledge/map_v2",
            ),
            existing_ports=(
                "backend/app/knowledge/map_read_api.py:KnowledgeMapSnapshotPort",
            ),
        ),
        PersistenceInventoryItem(
            domain=PersistenceDomain.PLAN,
            resource_kind=TenantResourceKind.PLAN,
            enterprise_table="learning_plans",
            local_persistence=(
                LocalPersistenceKind.SQLALCHEMY_SQLITE,
                LocalPersistenceKind.MEMORY,
            ),
            local_implementations=(
                "backend/app/orm.py:PlanORM/PlanActivationRecordORM/PlanExecutionTaskORM",
                "backend/app/planning/product_runtime",
            ),
            existing_ports=(),
        ),
        PersistenceInventoryItem(
            domain=PersistenceDomain.TEACHER_SESSION,
            resource_kind=TenantResourceKind.TEACHER_SESSION,
            enterprise_table="teacher_sessions",
            local_persistence=(
                LocalPersistenceKind.SQLALCHEMY_SQLITE,
                LocalPersistenceKind.MEMORY,
            ),
            local_implementations=(
                "backend/app/orm.py:SessionORM/TeacherTurnORM/TeacherEventORM",
                "backend/app/teaching/session_v2/stores.py:InMemoryClassroomSessionStore",
            ),
            existing_ports=(
                "backend/app/teaching/session_v2/ports.py:ClassroomSessionStorePort",
            ),
        ),
        PersistenceInventoryItem(
            domain=PersistenceDomain.REVIEW,
            resource_kind=TenantResourceKind.REVIEW_CAMPAIGN,
            enterprise_table="review_campaigns",
            local_persistence=(
                LocalPersistenceKind.SQLALCHEMY_SQLITE,
                LocalPersistenceKind.SQLITE,
                LocalPersistenceKind.ATOMIC_JSON,
            ),
            local_implementations=(
                "backend/app/orm.py:ReviewORM/ReviewAttemptORM/ReviewCandidateORM",
                "backend/app/teaching/review_memory/durable_store.py:SqliteReviewMemoryStore",
                "backend/app/teaching/review_memory/store.py:AtomicJsonReviewMemoryStore",
            ),
            existing_ports=(
                "backend/app/teaching/review_v1/ports.py:ReviewMemoryPort",
                "backend/app/teaching/review_campaign_v2/ports.py:ReviewCampaignMemoryPort",
            ),
        ),
        PersistenceInventoryItem(
            domain=PersistenceDomain.STUDENT,
            resource_kind=TenantResourceKind.STUDENT_EVENT,
            enterprise_table="student_events",
            local_persistence=(
                LocalPersistenceKind.SQLITE,
                LocalPersistenceKind.ATOMIC_JSON,
            ),
            local_implementations=(
                "backend/app/teaching/student_repository/repository.py:StudentEventRepository",
                "backend/app/teaching/learning_state/store.py:AtomicJsonStudentModelStore",
            ),
            existing_ports=(
                "backend/app/teaching/student_model.py:StudentModelStore",
                "backend/app/teaching/session_v2/ports.py:StudentStatePort",
            ),
        ),
        PersistenceInventoryItem(
            domain=PersistenceDomain.COST,
            resource_kind=TenantResourceKind.COST_EVENT,
            enterprise_table="cost_events",
            local_persistence=(LocalPersistenceKind.SQLITE,),
            local_implementations=(
                "backend/app/cost/durable_execution/store.py:SQLiteExecutionStore",
                "backend/app/cost/cache_observatory/persistence.py:CacheMetricsStore",
            ),
            existing_ports=(
                "backend/app/cost/execution_v2/ports.py:CostReceiptPort",
            ),
        ),
    )
)


__all__ = [
    "ENTERPRISE_PERSISTENCE_INVENTORY",
    "EnterprisePersistenceInventory",
    "LocalPersistenceKind",
    "PersistenceDomain",
    "PersistenceInventoryItem",
]
