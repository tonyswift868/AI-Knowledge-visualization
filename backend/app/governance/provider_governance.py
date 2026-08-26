from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4

from app.enterprise.contracts_v1 import (
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
)
from app.governance.authorization import AuthorizationService
from app.governance.models import (
    Action,
    GovernanceActorAdapter,
    PolicyRequest,
    RelationshipContext,
    Resource,
)


_SECRET_REFERENCE = re.compile(
    r"^(?:mfcred_[A-Za-z0-9_-]{8,152}|(?:secret|vault)://[A-Za-z0-9._~:/-]{3,150})$"
)


def _required(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


class ProviderScope(str, Enum):
    ORGANIZATION = "ORGANIZATION"
    PERSONAL = "PERSONAL"


class ProviderConfigStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ROTATING = "ROTATING"
    REVOKED = "REVOKED"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class ProviderCredentialRef:
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        normalized = str(self.value or "").strip()
        if not _SECRET_REFERENCE.fullmatch(normalized):
            raise ValueError("provider secret must be an opaque secret reference")
        object.__setattr__(self, "value", normalized)


ProviderSecretReference = ProviderCredentialRef


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("provider governance timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    config_id: str
    organization_id: str | None
    provider: str
    secret_reference: ProviderCredentialRef = field(repr=False)
    approved_models: frozenset[str] = field(default_factory=frozenset)
    scope: ProviderScope = ProviderScope.ORGANIZATION
    owner_actor_id: str | None = None
    status: ProviderConfigStatus = ProviderConfigStatus.ACTIVE
    revision: int = 1
    rotated_at: datetime | None = None
    status_changed_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("config_id", "provider"):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        if self.organization_id is not None:
            object.__setattr__(
                self,
                "organization_id",
                _required("organization_id", self.organization_id),
            )
        models = frozenset(_required("approved_model", item) for item in self.approved_models)
        if not models:
            raise ValueError("approved_models must not be empty")
        object.__setattr__(self, "approved_models", models)
        if self.scope == ProviderScope.PERSONAL:
            object.__setattr__(
                self,
                "owner_actor_id",
                _required("owner_actor_id", self.owner_actor_id or ""),
            )
        else:
            if self.organization_id is None:
                raise ValueError("organization-scoped provider requires organization_id")
            if self.owner_actor_id is not None:
                raise ValueError("organization-scoped config cannot have a personal owner")
        if self.revision < 1:
            raise ValueError("revision must be positive")
        for name in ("rotated_at", "status_changed_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _utc(value))

    def to_api_dict(self) -> dict[str, object]:
        """API/UI-safe representation: neither raw secret nor secret reference."""

        return {
            "configured": self.status in {
                ProviderConfigStatus.ACTIVE,
                ProviderConfigStatus.ROTATING,
            },
            "provider": self.provider,
            "model": sorted(self.approved_models),
            "status": self.status.value,
            "rotation_time": (
                self.rotated_at.isoformat() if self.rotated_at is not None else None
            ),
        }

    def cache_identity(self, model: str) -> str:
        normalized_model = _required("model", model)
        if normalized_model not in self.approved_models:
            raise ValueError("model is not approved for this provider config")
        payload = {
            "schema": "mentorflow.provider-cache-identity.v1",
            "config_id": self.config_id,
            "provider": self.provider,
            "model": normalized_model,
            "scope": self.scope.value,
            "organization_id": self.organization_id,
            "owner_actor_id": self.owner_actor_id,
            "revision": self.revision,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class ProviderGovernanceError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ProviderSessionGrant:
    """Secret-free authorization token fenced to one config revision."""

    session_id: str
    config_id: str
    provider: str
    model: str
    config_revision: int
    issued_at: datetime

    def __post_init__(self) -> None:
        for name in ("session_id", "config_id", "provider", "model"):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        if self.config_revision < 1:
            raise ValueError("config_revision must be positive")
        object.__setattr__(self, "issued_at", _utc(self.issued_at))


class ProviderGovernanceService:
    """Reference-only Provider configuration with authorization at every mutation."""

    def __init__(self, authorization: AuthorizationService) -> None:
        self._authorization = authorization
        self._configs: dict[str, ProviderConfig] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _relationships(config: ProviderConfig) -> RelationshipContext:
        return RelationshipContext(
            resource_owner_id=config.owner_actor_id,
            personal_resource=config.scope == ProviderScope.PERSONAL,
        )

    @staticmethod
    def _resource_scope(config: ProviderConfig, tenant: TenantContext) -> ResourceScope:
        """Represent the config's real tenant so mismatches become audited DENY."""

        if config.organization_id is None:
            return ResourceScope(
                scope_type=ResourceScopeType.PERSONAL,
                owner_subject_id=config.owner_actor_id,
                classification=ResourceClassification.RESTRICTED,
            )
        if tenant.workspace_id is not None:
            return ResourceScope(
                scope_type=ResourceScopeType.WORKSPACE,
                owner_subject_id=config.owner_actor_id,
                organization_id=config.organization_id,
                workspace_id=tenant.workspace_id,
                classification=ResourceClassification.RESTRICTED,
            )
        return ResourceScope(
            scope_type=ResourceScopeType.ORGANIZATION,
            owner_subject_id=config.owner_actor_id,
            organization_id=config.organization_id,
            classification=ResourceClassification.RESTRICTED,
        )

    def _authorize(
        self,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        action: Action,
        config: ProviderConfig,
        trace_id: str,
    ) -> None:
        scope = self._resource_scope(config, tenant)
        result = self._authorization.authorize(
            PolicyRequest(
                subject=subject,
                tenant=tenant,
                action=action,
                resource=Resource.PROVIDER_CONFIG,
                resource_id=config.config_id,
                resource_scope=scope,
                relationships=self._relationships(config),
                trace_id=trace_id,
            )
        )
        if not result.allowed:
            raise ProviderGovernanceError(result.reason_code)

    def register(
        self,
        config: ProviderConfig,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> Mapping[str, object]:
        self._authorize(
            subject=subject,
            tenant=tenant,
            action=Action.CREATE,
            config=config,
            trace_id=trace_id,
        )
        with self._lock:
            if config.config_id in self._configs:
                raise ProviderGovernanceError("PROVIDER_CONFIG_ALREADY_EXISTS")
            self._configs[config.config_id] = config
        return MappingProxyType(config.to_api_dict())

    def get_internal(self, config_id: str) -> ProviderConfig:
        with self._lock:
            try:
                return self._configs[config_id]
            except KeyError as exc:
                raise ProviderGovernanceError("PROVIDER_CONFIG_NOT_FOUND") from exc

    def api_view(
        self,
        config_id: str,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> Mapping[str, object]:
        config = self.get_internal(config_id)
        self._authorize(
            subject=subject,
            tenant=tenant,
            action=Action.READ,
            config=config,
            trace_id=trace_id,
        )
        return MappingProxyType(config.to_api_dict())

    def begin_rotation(
        self,
        config_id: str,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
        now: datetime | None = None,
    ) -> Mapping[str, object]:
        config = self.get_internal(config_id)
        self._authorize(
            subject=subject,
            tenant=tenant,
            action=Action.UPDATE,
            config=config,
            trace_id=trace_id,
        )
        changed_at = _utc(now or datetime.now(timezone.utc))
        updated = replace(
            config,
            status=ProviderConfigStatus.ROTATING,
            status_changed_at=changed_at,
        )
        with self._lock:
            self._configs[config_id] = updated
        return MappingProxyType(updated.to_api_dict())

    def complete_rotation(
        self,
        config_id: str,
        new_secret_reference: ProviderCredentialRef,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
        now: datetime | None = None,
    ) -> Mapping[str, object]:
        config = self.get_internal(config_id)
        self._authorize(
            subject=subject,
            tenant=tenant,
            action=Action.UPDATE,
            config=config,
            trace_id=trace_id,
        )
        if config.status != ProviderConfigStatus.ROTATING:
            raise ProviderGovernanceError("PROVIDER_CONFIG_NOT_ROTATING")
        changed_at = _utc(now or datetime.now(timezone.utc))
        updated = replace(
            config,
            secret_reference=new_secret_reference,
            status=ProviderConfigStatus.ACTIVE,
            revision=config.revision + 1,
            rotated_at=changed_at,
            status_changed_at=changed_at,
        )
        with self._lock:
            self._configs[config_id] = updated
        return MappingProxyType(updated.to_api_dict())

    def revoke(
        self,
        config_id: str,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
        now: datetime | None = None,
    ) -> Mapping[str, object]:
        config = self.get_internal(config_id)
        self._authorize(
            subject=subject,
            tenant=tenant,
            action=Action.DELETE,
            config=config,
            trace_id=trace_id,
        )
        updated = replace(
            config,
            status=ProviderConfigStatus.REVOKED,
            revision=config.revision + 1,
            status_changed_at=_utc(now or datetime.now(timezone.utc)),
        )
        with self._lock:
            self._configs[config_id] = updated
        return MappingProxyType(updated.to_api_dict())

    def mark_invalid(
        self,
        config_id: str,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
        now: datetime | None = None,
    ) -> Mapping[str, object]:
        config = self.get_internal(config_id)
        self._authorize(
            subject=subject,
            tenant=tenant,
            action=Action.UPDATE,
            config=config,
            trace_id=trace_id,
        )
        updated = replace(
            config,
            status=ProviderConfigStatus.INVALID,
            revision=config.revision + 1,
            status_changed_at=_utc(now or datetime.now(timezone.utc)),
        )
        with self._lock:
            self._configs[config_id] = updated
        return MappingProxyType(updated.to_api_dict())

    def assert_model_approved(self, config_id: str, model: str) -> ProviderConfig:
        config = self.get_internal(config_id)
        if config.status != ProviderConfigStatus.ACTIVE:
            raise ProviderGovernanceError("PROVIDER_CONFIG_NOT_ACTIVE")
        if model not in config.approved_models:
            raise ProviderGovernanceError("MODEL_NOT_APPROVED")
        return config

    def authorize_new_session(
        self,
        config_id: str,
        model: str,
        *,
        now: datetime | None = None,
    ) -> ProviderSessionGrant:
        config = self.assert_model_approved(config_id, model)
        return ProviderSessionGrant(
            session_id="provider_session_" + uuid4().hex,
            config_id=config.config_id,
            provider=config.provider,
            model=model,
            config_revision=config.revision,
            issued_at=_utc(now or datetime.now(timezone.utc)),
        )

    def validate_session(self, grant: ProviderSessionGrant) -> ProviderConfig:
        config = self.get_internal(grant.config_id)
        if config.status is not ProviderConfigStatus.ACTIVE:
            raise ProviderGovernanceError("PROVIDER_CONFIG_NOT_ACTIVE")
        if config.revision != grant.config_revision:
            raise ProviderGovernanceError("PROVIDER_SESSION_STALE")
        if config.provider != grant.provider or grant.model not in config.approved_models:
            raise ProviderGovernanceError("PROVIDER_SESSION_INVALID")
        return config


__all__ = [
    "ProviderConfig",
    "ProviderConfigStatus",
    "ProviderCredentialRef",
    "ProviderGovernanceError",
    "ProviderGovernanceService",
    "ProviderScope",
    "ProviderSessionGrant",
    "ProviderSecretReference",
]
