"""Provider-neutral contracts for enterprise tenant persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from app.enterprise.contracts_v1 import ResourceClassification, ResourceScopeType


class TenantResourceKind(str, Enum):
    DOCUMENT = "DOCUMENT"
    EVIDENCE = "EVIDENCE"
    KNOWLEDGE_MAP = "KNOWLEDGE_MAP"
    PLAN = "PLAN"
    TEACHER_SESSION = "TEACHER_SESSION"
    REVIEW_CAMPAIGN = "REVIEW_CAMPAIGN"
    STUDENT_EVENT = "STUDENT_EVENT"
    COST_EVENT = "COST_EVENT"


RESOURCE_TABLE_BY_KIND: dict[TenantResourceKind, str] = {
    TenantResourceKind.DOCUMENT: "documents",
    TenantResourceKind.EVIDENCE: "evidence",
    TenantResourceKind.KNOWLEDGE_MAP: "knowledge_maps",
    TenantResourceKind.PLAN: "plans",
    TenantResourceKind.TEACHER_SESSION: "teacher_sessions",
    TenantResourceKind.REVIEW_CAMPAIGN: "review_campaigns",
    TenantResourceKind.STUDENT_EVENT: "student_events",
    TenantResourceKind.COST_EVENT: "cost_events",
}


class TenantPersistenceError(RuntimeError):
    """Base error for the enterprise persistence boundary."""


class TenantContextRequiredError(TenantPersistenceError):
    pass


class TenantContextLeakError(TenantPersistenceError):
    pass


class EnterprisePersistenceUnavailableError(TenantPersistenceError):
    pass


class ImportConfirmationError(TenantPersistenceError):
    pass


def _required_identifier(value: str, field_name: str, *, limit: int = 160) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    if len(normalized) > limit:
        raise ValueError(f"{field_name} must be at most {limit} characters")
    return normalized


@dataclass(frozen=True, slots=True)
class TenantResourceDraft:
    resource_id: str
    kind: TenantResourceKind
    scope_type: ResourceScopeType
    owner_subject_id: str
    classification: ResourceClassification
    title: str
    status: str
    payload: Mapping[str, Any]
    search_text: str = ""
    job_id: str = ""
    cache_key: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "resource_id",
            _required_identifier(self.resource_id, "resource_id", limit=160),
        )
        object.__setattr__(
            self,
            "owner_subject_id",
            _required_identifier(
                self.owner_subject_id,
                "owner_subject_id",
                limit=128,
            ),
        )
        object.__setattr__(
            self,
            "title",
            _required_identifier(self.title, "title", limit=320),
        )
        object.__setattr__(
            self,
            "status",
            _required_identifier(self.status, "status", limit=48),
        )
        if len(self.job_id) > 160:
            raise ValueError("job_id must be at most 160 characters")
        if len(self.cache_key) > 200:
            raise ValueError("cache_key must be at most 200 characters")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True, slots=True)
class TenantResourceRecord:
    resource_id: str
    kind: TenantResourceKind
    organization_id: str
    workspace_id: str
    scope_type: ResourceScopeType
    owner_subject_id: str
    classification: ResourceClassification
    title: str
    status: str
    payload: Mapping[str, Any]
    search_text: str
    job_id: str
    cache_key: str
    created_at: datetime | str
    updated_at: datetime | str


@dataclass(frozen=True, slots=True)
class LocalImportDescriptor:
    local_id: str
    kind: TenantResourceKind
    title: str
    scope_type: ResourceScopeType
    classification: ResourceClassification
    status: str = "ready"
    search_text: str = ""
    job_id: str = ""
    cache_key: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "local_id",
            _required_identifier(self.local_id, "local_id", limit=160),
        )
        object.__setattr__(
            self,
            "title",
            _required_identifier(self.title, "title", limit=320),
        )


@dataclass(frozen=True, slots=True)
class LocalImportPreview:
    preview_id: str
    confirmation_token: str
    organization_id: str
    workspace_id: str
    requested_by: str
    item_count: int
    counts_by_kind: Mapping[str, int]
    descriptor_digest: str
    requires_explicit_confirmation: bool = True
    auto_upload_started: bool = False


@dataclass(frozen=True, slots=True)
class LocalImportResult:
    preview_id: str
    imported_count: int
    imported_resource_ids: tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "EnterprisePersistenceUnavailableError",
    "ImportConfirmationError",
    "LocalImportDescriptor",
    "LocalImportPreview",
    "LocalImportResult",
    "RESOURCE_TABLE_BY_KIND",
    "ResourceClassification",
    "ResourceScopeType",
    "TenantContextLeakError",
    "TenantContextRequiredError",
    "TenantPersistenceError",
    "TenantResourceDraft",
    "TenantResourceKind",
    "TenantResourceRecord",
]
