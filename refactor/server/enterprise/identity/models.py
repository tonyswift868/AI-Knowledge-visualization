"""Identity-runtime models built around the frozen Enterprise Context v1 contract."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from refactor.server.enterprise.contracts_v1 import ActorKind


class _IdentityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class MembershipStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class BusinessRole(str, Enum):
    OWNER = "OWNER"
    ORGANIZATION_OWNER = "ORGANIZATION_OWNER"
    ADMIN = "ADMIN"
    CONTENT_MANAGER = "CONTENT_MANAGER"
    INSTRUCTOR = "INSTRUCTOR"
    LEARNER = "LEARNER"
    AUDITOR = "AUDITOR"


class ServicePermission(str, Enum):
    DOCUMENT_READ = "DOCUMENT_READ"
    DOCUMENT_PROCESS = "DOCUMENT_PROCESS"
    JOB_READ = "JOB_READ"
    JOB_EXECUTE = "JOB_EXECUTE"


class AuthenticatedPrincipal(_IdentityModel):
    issuer: str = Field(min_length=8, max_length=500)
    audiences: tuple[str, ...] = Field(min_length=1, max_length=32)
    token_id: str = Field(min_length=1, max_length=300)
    subject: str = Field(min_length=1, max_length=300)
    issued_at: datetime
    expires_at: datetime
    authentication_time: datetime
    authentication_strength: str = Field(min_length=1, max_length=160)
    not_before: datetime | None = None
    identity_provider_session_id: str | None = Field(default=None, max_length=300)
    preferred_username: str | None = Field(default=None, max_length=300)
    email: str | None = Field(default=None, max_length=320)
    display_name: str | None = Field(default=None, max_length=300)
    client_id: str | None = Field(default=None, max_length=300)
    actor_kind: ActorKind = ActorKind.HUMAN

    @field_validator(
        "issued_at",
        "expires_at",
        "authentication_time",
        "not_before",
    )
    @classmethod
    def require_aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("identity timestamps must include a UTC offset")
        return value

    @field_validator("audiences")
    @classmethod
    def require_unique_audiences(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("audiences must be non-blank and unique")
        return normalized

    @model_validator(mode="after")
    def validate_times_and_service_shape(self) -> "AuthenticatedPrincipal":
        if self.expires_at <= self.issued_at:
            raise ValueError("token expiry must be after issuance")
        if self.authentication_time > self.issued_at:
            raise ValueError("authentication time must not be after token issuance")
        if self.actor_kind is ActorKind.SERVICE and not self.client_id:
            raise ValueError("service principals require client_id")
        return self


class MembershipGrant(_IdentityModel):
    """Trusted projection returned by the E1-02 identity/tenant persistence port."""

    membership_id: str = Field(min_length=1, max_length=240)
    issuer: str = Field(min_length=8, max_length=500)
    subject: str = Field(min_length=1, max_length=300)
    organization_id: str = Field(min_length=1, max_length=240)
    workspace_id: str | None = Field(default=None, max_length=240)
    roles: tuple[BusinessRole, ...] = Field(min_length=1, max_length=128)
    status: MembershipStatus

    @field_validator("roles")
    @classmethod
    def require_unique_roles(cls, values: tuple[BusinessRole, ...]) -> tuple[BusinessRole, ...]:
        if len(values) != len(set(values)):
            raise ValueError("membership roles must be unique")
        return values


class ServiceIdentityGrant(_IdentityModel):
    """Trusted service-account projection returned by the E1-02 persistence port."""

    service_id: str = Field(min_length=1, max_length=240)
    issuer: str = Field(min_length=8, max_length=500)
    subject: str = Field(min_length=1, max_length=300)
    client_id: str = Field(min_length=1, max_length=300)
    organization_id: str = Field(min_length=1, max_length=240)
    workspace_id: str | None = Field(default=None, max_length=240)
    permissions: tuple[ServicePermission, ...] = Field(min_length=1, max_length=256)
    status: MembershipStatus

    @field_validator("permissions")
    @classmethod
    def require_unique_permissions(
        cls,
        values: tuple[ServicePermission, ...],
    ) -> tuple[ServicePermission, ...]:
        if len(values) != len(set(values)):
            raise ValueError("service permissions must be unique")
        return values


class EnterpriseSession(_IdentityModel):
    session_id: str = Field(min_length=32, max_length=200)
    principal: AuthenticatedPrincipal
    created_at: datetime
    refreshed_at: datetime
    session_expires_at: datetime | None = None
    access_token_expires_at: datetime
    refresh_token_expires_at: datetime | None = None
    selected_organization_id: str | None = Field(default=None, max_length=240)
    selected_workspace_id: str | None = Field(default=None, max_length=240)
    context_version: int = Field(default=0, ge=0)
    session_generation: int = Field(default=0, ge=0)
    revoked: bool = False

    @field_validator(
        "created_at",
        "refreshed_at",
        "session_expires_at",
        "access_token_expires_at",
        "refresh_token_expires_at",
    )
    @classmethod
    def require_session_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("session timestamps must include a UTC offset")
        return value

    @model_validator(mode="after")
    def validate_selected_scope(self) -> "EnterpriseSession":
        if self.session_expires_at is not None and self.session_expires_at <= self.created_at:
            raise ValueError("session expiry must be after creation")
        if self.selected_workspace_id is not None and self.selected_organization_id is None:
            raise ValueError("selected workspace requires selected organization")
        return self


class IdentityDirectoryPort(Protocol):
    """Read-only persistence boundary; E1-01 owns no tenant database tables."""

    def resolve_membership(
        self,
        *,
        issuer: str,
        subject: str,
        organization_id: str,
        workspace_id: str | None,
    ) -> MembershipGrant | None: ...

    def resolve_service_identity(
        self,
        *,
        issuer: str,
        subject: str,
        client_id: str,
    ) -> ServiceIdentityGrant | None: ...


__all__ = [
    "AuthenticatedPrincipal",
    "BusinessRole",
    "EnterpriseSession",
    "IdentityDirectoryPort",
    "MembershipGrant",
    "MembershipStatus",
    "ServiceIdentityGrant",
    "ServicePermission",
]
