"""Provider-, transport-, and persistence-neutral Enterprise Context v1 models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


ENTERPRISE_CONTEXT_CONTRACT_VERSION = "enterprise-context.v1"
ContractVersion = Literal["enterprise-context.v1"]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ActorKind(str, Enum):
    HUMAN = "HUMAN"
    SERVICE = "SERVICE"


class TenantMode(str, Enum):
    PERSONAL_LOCAL = "PERSONAL_LOCAL"
    ENTERPRISE_SERVER = "ENTERPRISE_SERVER"


class ResourceScopeType(str, Enum):
    PERSONAL = "PERSONAL"
    ORGANIZATION = "ORGANIZATION"
    WORKSPACE = "WORKSPACE"


class ResourceClassification(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


def _require_aware_datetime(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value.astimezone(timezone.utc)


def _validate_unique_codes(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        if not value:
            raise ValueError(f"{field_name} must not contain blank values")
        if len(value) > 160:
            raise ValueError(f"{field_name} values must not exceed 160 characters")
        if value in seen:
            raise ValueError(f"{field_name} must not contain duplicate values")
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


class ActorContext(_ContractModel):
    schema_version: ContractVersion = ENTERPRISE_CONTEXT_CONTRACT_VERSION
    subject_id: Identifier
    actor_kind: ActorKind
    issuer: Identifier
    token_id: Identifier
    session_id: Identifier
    request_id: Identifier
    authentication_time: datetime
    authentication_strength: Code
    organization_id: Identifier | None = None
    workspace_id: Identifier | None = None
    membership_id: Identifier | None = None
    roles: tuple[str, ...] = Field(default=(), max_length=128)
    service_permissions: tuple[str, ...] = Field(default=(), max_length=256)

    @field_validator("authentication_time")
    @classmethod
    def validate_authentication_time(cls, value: datetime) -> datetime:
        return _require_aware_datetime(value, "authentication_time")

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_unique_codes(values, "roles")

    @field_validator("service_permissions")
    @classmethod
    def validate_service_permissions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_unique_codes(values, "service_permissions")


class TenantContext(_ContractModel):
    """Tenant boundary without forcing an organization into Personal Local mode."""

    schema_version: ContractVersion = ENTERPRISE_CONTEXT_CONTRACT_VERSION
    organization_id: Identifier | None = None
    workspace_id: Identifier | None = None
    mode: TenantMode

    @model_validator(mode="after")
    def require_enterprise_organization(self) -> "TenantContext":
        if self.mode is TenantMode.ENTERPRISE_SERVER and self.organization_id is None:
            raise ValueError("ENTERPRISE_SERVER requires organization_id")
        if (
            self.mode is TenantMode.ENTERPRISE_SERVER
            and self.workspace_id is not None
            and self.organization_id is None
        ):
            raise ValueError("enterprise workspace context requires organization_id")
        return self


__all__ = [
    'ENTERPRISE_CONTEXT_CONTRACT_VERSION', 'ActorContext', 'ActorKind',
    'ResourceClassification', 'ResourceScopeType', 'TenantContext', 'TenantMode',
]
