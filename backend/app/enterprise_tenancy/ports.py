"""One tenant-domain repository port, nominally reused by every main domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, TypeAlias

from app.enterprise.contracts_v1 import ResourceScope, TenantContext, TenantMode

from .models import (
    AccountProgressSnapshotDraft,
    AccountProgressSnapshotRecord,
    TenantResourceKind,
)


# Do not create a second mode enum.  The frozen contract is the persistence mode.
PersistenceMode = TenantMode


@dataclass(frozen=True, slots=True)
class TenantDomainWrite:
    resource_id: str
    title: str
    status: str
    payload: Mapping[str, Any]
    search_text: str = ""
    job_id: str = ""
    cache_key: str = ""

    def __post_init__(self) -> None:
        for name, limit in (("resource_id", 160), ("title", 320), ("status", 48)):
            value = str(getattr(self, name)).strip()
            if not value or len(value) > limit:
                raise ValueError(f"{name} must be non-empty and at most {limit} characters")
            object.__setattr__(self, name, value)
        if len(self.job_id) > 160 or len(self.cache_key) > 200:
            raise ValueError("job_id/cache_key exceeds the enterprise schema limit")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True, slots=True)
class TenantDomainRecord:
    resource_id: str
    kind: TenantResourceKind
    scope: ResourceScope
    title: str
    status: str
    payload: Mapping[str, Any]
    search_text: str = ""
    job_id: str = ""
    cache_key: str = ""
    created_at: datetime | str = ""
    updated_at: datetime | str = ""


class TenantDomainRepositoryPort(Protocol):
    """Uniform CRUD shape; adapters require formal tenant and scope contracts."""

    kind: TenantResourceKind

    def upsert(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        value: TenantDomainWrite,
    ) -> TenantDomainRecord: ...

    def get(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        resource_id: str,
    ) -> TenantDomainRecord | None: ...

    def list_records(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        status: str | None = None,
        limit: int = 200,
    ) -> tuple[TenantDomainRecord, ...]: ...

    def search(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        query: str,
        limit: int = 200,
    ) -> tuple[TenantDomainRecord, ...]: ...

    def update_payload(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        resource_id: str,
        payload: Mapping[str, Any],
        status: str,
    ) -> bool: ...

    def delete(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        resource_id: str,
    ) -> bool: ...

    def export_records(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        limit: int = 10_000,
    ) -> tuple[TenantDomainRecord, ...]: ...

    def read_job(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        job_id: str,
    ) -> TenantDomainRecord | None: ...

    def read_cache(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        cache_key: str,
    ) -> TenantDomainRecord | None: ...


class AccountProgressSnapshotRepositoryPort(Protocol):
    """First-class Account Core progress persistence over student_events."""

    def upsert(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        value: AccountProgressSnapshotDraft,
    ) -> AccountProgressSnapshotRecord: ...

    def get(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        progress_id: str,
    ) -> AccountProgressSnapshotRecord | None: ...

    def list_snapshots(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        limit: int = 200,
    ) -> tuple[AccountProgressSnapshotRecord, ...]: ...

    def delete(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        progress_id: str,
    ) -> bool: ...


class PersonalLocalResourceStorePort(Protocol):
    """Small bridge implemented by existing Local stores, not a replacement for them."""

    def put(self, record: TenantDomainRecord) -> TenantDomainRecord: ...

    def get(
        self,
        kind: TenantResourceKind,
        resource_id: str,
    ) -> TenantDomainRecord | None: ...

    def list_records(
        self,
        kind: TenantResourceKind,
    ) -> tuple[TenantDomainRecord, ...]: ...

    def delete(self, kind: TenantResourceKind, resource_id: str) -> bool: ...


# These names converge on one interface instead of cloning seven identical Protocols.
DocumentRepositoryPort: TypeAlias = TenantDomainRepositoryPort
EvidenceRepositoryPort: TypeAlias = TenantDomainRepositoryPort
KnowledgeMapRepositoryPort: TypeAlias = TenantDomainRepositoryPort
PlanRepositoryPort: TypeAlias = TenantDomainRepositoryPort
TeacherSessionRepositoryPort: TypeAlias = TenantDomainRepositoryPort
ReviewRepositoryPort: TypeAlias = TenantDomainRepositoryPort
StudentRepositoryPort: TypeAlias = TenantDomainRepositoryPort
CostRepositoryPort: TypeAlias = TenantDomainRepositoryPort


__all__ = [
    "AccountProgressSnapshotRepositoryPort",
    "CostRepositoryPort",
    "DocumentRepositoryPort",
    "EvidenceRepositoryPort",
    "KnowledgeMapRepositoryPort",
    "PersistenceMode",
    "PersonalLocalResourceStorePort",
    "PlanRepositoryPort",
    "ReviewRepositoryPort",
    "StudentRepositoryPort",
    "TeacherSessionRepositoryPort",
    "TenantDomainRecord",
    "TenantDomainRepositoryPort",
    "TenantDomainWrite",
]
