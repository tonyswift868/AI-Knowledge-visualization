from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Mapping

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


@dataclass(frozen=True, slots=True)
class ProviderSecretReference:
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        normalized = str(self.value or "").strip()
        if not _SECRET_REFERENCE.fullmatch(normalized):
            raise ValueError("provider secret must be an opaque secret reference")
        object.__setattr__(self, "value", normalized)


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    config_id: str
    organization_id: str | None
    provider: str
    secret_reference: ProviderSecretReference = field(repr=False)
    approved_models: frozenset[str] = field(default_factory=frozenset)
    scope: ProviderScope = ProviderScope.ORGANIZATION
    owner_actor_id: str | None = None
    status: ProviderConfigStatus = ProviderConfigStatus.ACTIVE
    revision: int = 1

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

    def to_api_dict(self) -> dict[str, object]:
        """API/UI-safe representation: neither raw secret nor secret reference."""

        return {
            "config_id": self.config_id,
            "organization_id": self.organization_id,
            "provider": self.provider,
            "approved_models": sorted(self.approved_models),
            "scope": self.scope.value,
            "owner_actor_id": self.owner_actor_id,
            "status": self.status.value,
            "revision": self.revision,
            "secret_configured": True,
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
    ) -> Mapping[str, object]:
        config = self.get_internal(config_id)
        self._authorize(
            subject=subject,
            tenant=tenant,
            action=Action.UPDATE,
            config=config,
            trace_id=trace_id,
        )
        updated = replace(config, status=ProviderConfigStatus.ROTATING)
        with self._lock:
            self._configs[config_id] = updated
        return MappingProxyType(updated.to_api_dict())

    def complete_rotation(
        self,
        config_id: str,
        new_secret_reference: ProviderSecretReference,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
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
        updated = replace(
            config,
            secret_reference=new_secret_reference,
            status=ProviderConfigStatus.ACTIVE,
            revision=config.revision + 1,
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


__all__ = [
    "ProviderConfig",
    "ProviderConfigStatus",
    "ProviderGovernanceError",
    "ProviderGovernanceService",
    "ProviderScope",
    "ProviderSecretReference",
]
