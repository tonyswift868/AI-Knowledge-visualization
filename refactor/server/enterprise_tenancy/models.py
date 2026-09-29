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

from refactor.server.enterprise.contracts_v1 import ResourceClassification, ResourceScopeType


def _required_identifier(value: str, field_name: str, *, limit: int = 160) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    if len(normalized) > limit:
        raise ValueError(f"{field_name} must be at most {limit} characters")
    return normalized


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
