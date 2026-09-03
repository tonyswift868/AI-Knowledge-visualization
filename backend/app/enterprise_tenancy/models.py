"""Provider-neutral contracts for enterprise tenant persistence."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping as MappingABC, Sequence
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
    TenantResourceKind.PLAN: "learning_plans",
    TenantResourceKind.TEACHER_SESSION: "teacher_sessions",
    TenantResourceKind.REVIEW_CAMPAIGN: "review_campaigns",
    TenantResourceKind.STUDENT_EVENT: "student_events",
    TenantResourceKind.COST_EVENT: "cost_events",
}


ACCOUNT_PROGRESS_SNAPSHOT_SCHEMA_VERSION = (
    "mentorflow.account-progress-snapshot.v1"
)
ACCOUNT_PROGRESS_SNAPSHOT_RECORD_TYPE = "account_progress_snapshot"

_ABSOLUTE_LOCAL_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")
_FORBIDDEN_ACCOUNT_METADATA_KEYS = frozenset(
    {
        "absolutepath",
        "filebytes",
        "localdirectorytree",
        "localfoldertree",
        "localparsercache",
        "localpath",
        "localresourcecache",
        "rawbytes",
        "rawresourcebytes",
        "resourcebytes",
    }
)
_MAX_ACCOUNT_PROGRESS_BYTES = 262_144
_MAX_ACCOUNT_PROGRESS_DEPTH = 16


class TenantPersistenceError(RuntimeError):
    """Base error for the enterprise persistence boundary."""


class TenantContextRequiredError(TenantPersistenceError):
    pass


class TenantContextLeakError(TenantPersistenceError):
    pass


class EnterprisePersistenceUnavailableError(TenantPersistenceError):
    pass


class EnterprisePersistenceRequestError(TenantPersistenceError):
    """Safe business error projected by the Enterprise HTTP facade."""

    def __init__(self, code: str, message: str, *, status: int) -> None:
        super().__init__(message)
        self.code = _required_identifier(code, "code", limit=160)
        self.message = message
        self.status = status


class ImportConfirmationError(TenantPersistenceError):
    pass


class ProgressSnapshotVersionError(EnterprisePersistenceRequestError):
    """A stale or conflicting Account Core progress snapshot was rejected."""

    def __init__(self, message: str) -> None:
        super().__init__(
            "ENTERPRISE_PROGRESS_VERSION_CONFLICT",
            message,
            status=409,
        )


def _required_identifier(value: str, field_name: str, *, limit: int = 160) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    if len(normalized) > limit:
        raise ValueError(f"{field_name} must be at most {limit} characters")
    return normalized


def _account_metadata_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _account_progress_metadata(value: Any, *, path: str, depth: int = 0) -> Any:
    """Return canonical JSON metadata while excluding device-local material."""

    if depth > _MAX_ACCOUNT_PROGRESS_DEPTH:
        raise ValueError("account progress snapshot exceeds the nesting limit")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} numeric values must be finite")
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if _ABSOLUTE_LOCAL_PATH.match(normalized) or normalized.casefold().startswith(
            "file:"
        ):
            raise ValueError(f"{path} must not contain an absolute local path")
        if len(value) > 20_000:
            raise ValueError(f"{path} strings must not exceed 20000 characters")
        return value
    if isinstance(value, MappingABC):
        if len(value) > 256:
            raise ValueError(f"{path} objects must not exceed 256 fields")
        normalized_mapping: dict[str, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise ValueError(f"{path} keys must be strings")
            key = raw_key.strip()
            if not key or len(key) > 160:
                raise ValueError(f"{path} contains an invalid field name")
            if _account_metadata_key(key) in _FORBIDDEN_ACCOUNT_METADATA_KEYS:
                raise ValueError(f"{path} contains device-local or raw resource data")
            normalized_mapping[key] = _account_progress_metadata(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return normalized_mapping
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray, memoryview),
    ):
        if len(value) > 1_000:
            raise ValueError(f"{path} lists must not exceed 1000 items")
        return [
            _account_progress_metadata(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
            )
            for index, item in enumerate(value)
        ]
    raise ValueError(f"{path} must contain JSON metadata, not raw resource bytes")


@dataclass(frozen=True, slots=True)
class AccountProgressSnapshotDraft:
    """Account-scoped snapshot stored through the canonical student_events row."""

    progress_id: str
    version: int
    snapshot: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.progress_id, str):
            raise ValueError("progress_id must be a string")
        object.__setattr__(
            self,
            "progress_id",
            _required_identifier(self.progress_id, "progress_id", limit=160),
        )
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or not 1 <= self.version <= 9_223_372_036_854_775_807
        ):
            raise ValueError("version must be a positive signed 64-bit integer")
        normalized = _account_progress_metadata(self.snapshot, path="snapshot")
        if not isinstance(normalized, dict):
            raise ValueError("snapshot must be a JSON object")
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > _MAX_ACCOUNT_PROGRESS_BYTES:
            raise ValueError("account progress snapshot must not exceed 262144 bytes")
        object.__setattr__(self, "snapshot", normalized)


@dataclass(frozen=True, slots=True)
class AccountProgressSnapshotRecord:
    progress_id: str
    version: int
    snapshot: Mapping[str, Any]
    organization_id: str
    workspace_id: str
    owner_subject_id: str
    created_at: datetime | str
    updated_at: datetime | str

    def public_contract(self) -> dict[str, Any]:
        return {
            "schema_version": ACCOUNT_PROGRESS_SNAPSHOT_SCHEMA_VERSION,
            "progress_id": self.progress_id,
            "version": self.version,
            "snapshot": dict(self.snapshot),
            "organization_id": self.organization_id,
            "workspace_id": self.workspace_id,
            "owner_subject_id": self.owner_subject_id,
            "created_at": (
                self.created_at.isoformat()
                if isinstance(self.created_at, datetime)
                else str(self.created_at)
            ),
            "updated_at": (
                self.updated_at.isoformat()
                if isinstance(self.updated_at, datetime)
                else str(self.updated_at)
            ),
        }


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
class PersonalDataImportPreview:
    preview_id: str
    requested_by: str
    item_count: int
    counts_by_kind: Mapping[str, int]
    descriptor_digest: str
    requires_destination_selection: bool = True
    auto_upload_started: bool = False


@dataclass(frozen=True, slots=True)
class EnterpriseImportPlan:
    plan_id: str
    preview_id: str
    confirmation_token: str
    organization_id: str
    workspace_id: str
    requested_by: str
    item_count: int
    descriptor_digest: str
    requires_explicit_confirmation: bool = True
    upload_started: bool = False


@dataclass(frozen=True, slots=True)
class LocalImportResult:
    plan_id: str
    preview_id: str
    imported_count: int
    imported_resource_ids: tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "ACCOUNT_PROGRESS_SNAPSHOT_RECORD_TYPE",
    "ACCOUNT_PROGRESS_SNAPSHOT_SCHEMA_VERSION",
    "AccountProgressSnapshotDraft",
    "AccountProgressSnapshotRecord",
    "EnterprisePersistenceRequestError",
    "EnterprisePersistenceUnavailableError",
    "EnterpriseImportPlan",
    "ImportConfirmationError",
    "LocalImportDescriptor",
    "LocalImportResult",
    "PersonalDataImportPreview",
    "ProgressSnapshotVersionError",
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
