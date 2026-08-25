from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from app.model_routing import (
    Capability,
    CapabilityDegradation,
    CapabilityStatus,
    CanonicalProviderIdentity,
    ProviderCapabilityProfile,
    RegisteredProviderProfile,
    RouteDecisionReceipt,
    RoutingMode,
    RoutingQualityPolicy,
    TaskCapabilityRequirement,
    TaskType,
    capability_profile_id,
)


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def safe_identifier(value: object, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{field_name} must be an opaque identifier")
    return normalized


def safe_code(value: object, *, fallback: str) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if _CODE.fullmatch(normalized) else fallback


class ProviderHealthState(str, Enum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    TEMP_UNAVAILABLE = "TEMP_UNAVAILABLE"
    DISABLED = "DISABLED"

    @property
    def executable(self) -> bool:
        return self in {ProviderHealthState.AVAILABLE, ProviderHealthState.DEGRADED}


class ProviderExecutionStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INVALID_ROUTE = "INVALID_ROUTE"
    RUNTIME_UNAVAILABLE = "RUNTIME_UNAVAILABLE"
    OPTIONAL_ENHANCEMENT_UNAVAILABLE = "OPTIONAL_ENHANCEMENT_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ProviderSessionContext:
    session_id: str
    request_id: str
    prior_route_identity: str | None = None
    prior_provider_canonical_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "session_id",
            safe_identifier(self.session_id, field_name="session_id"),
        )
        object.__setattr__(
            self,
            "request_id",
            safe_identifier(self.request_id, field_name="request_id"),
        )
        if self.prior_route_identity is not None:
            object.__setattr__(
                self,
                "prior_route_identity",
                safe_identifier(
                    self.prior_route_identity,
                    field_name="prior_route_identity",
                ),
            )
        if self.prior_provider_canonical_id is not None:
            object.__setattr__(
                self,
                "prior_provider_canonical_id",
                safe_identifier(
                    self.prior_provider_canonical_id,
                    field_name="prior_provider_canonical_id",
                ),
            )


@dataclass(frozen=True, slots=True)
class ProviderRuntimeProfile:
    identity: CanonicalProviderIdentity
    capability_profile: ProviderCapabilityProfile
    adapter_port: str
    health: ProviderHealthState = ProviderHealthState.AVAILABLE
    health_reason_code: str | None = None
    max_attempts: int = 2

    def __post_init__(self) -> None:
        health = self.health
        if not isinstance(health, ProviderHealthState):
            health = ProviderHealthState(str(health).strip().upper())
        adapter_port = safe_identifier(self.adapter_port, field_name="adapter_port")
        if self.identity.capability_profile_id != capability_profile_id(
            self.capability_profile
        ):
            raise ValueError("runtime capability profile must match canonical identity")
        if (
            self.capability_profile.provider.strip().lower().replace("-", "_")
            != self.identity.adapter_identity
        ):
            raise ValueError("runtime profile adapter must match canonical identity")
        if not 1 <= int(self.max_attempts) <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        reason = (
            safe_code(self.health_reason_code, fallback="RUNTIME_HEALTH_DEGRADED")
            if self.health_reason_code
            else None
        )
        object.__setattr__(self, "health", health)
        object.__setattr__(self, "adapter_port", adapter_port)
        object.__setattr__(self, "health_reason_code", reason)
        object.__setattr__(self, "max_attempts", int(self.max_attempts))

    @classmethod
    def from_registered_profile(
        cls,
        registered: RegisteredProviderProfile,
        *,
        adapter_port: str,
        health: ProviderHealthState = ProviderHealthState.AVAILABLE,
        health_reason_code: str | None = None,
        max_attempts: int = 2,
    ) -> ProviderRuntimeProfile:
        return cls(
            identity=registered.identity,
            capability_profile=registered.profile,
            adapter_port=adapter_port,
            health=health,
            health_reason_code=health_reason_code,
            max_attempts=max_attempts,
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "runtime_profile_id": "runtime_" + self.identity.canonical_id,
            "identity": self.identity.to_public_dict(),
            "capabilities": sorted(
                value.value for value in self.capability_profile.capabilities
            ),
            "context_window": self.capability_profile.context_window,
            "adapter_port": self.adapter_port,
            "health": self.health.value,
            "health_reason_code": self.health_reason_code,
            "max_attempts": self.max_attempts,
        }


@dataclass(frozen=True, slots=True)
class ProviderExecutionRequest:
    task_requirement: TaskCapabilityRequirement | TaskType | str
    session: ProviderSessionContext
    payload: str = field(default="", repr=False)
    preferred_provider: str | None = None
    preferred_model: str | None = None
    routing_mode: RoutingMode = RoutingMode.BALANCED
    quality_policy: RoutingQualityPolicy = RoutingQualityPolicy()
    max_total_attempts: int = 3

    def __post_init__(self) -> None:
        mode = self.routing_mode
        if not isinstance(mode, RoutingMode):
            mode = RoutingMode(str(mode).strip().lower())
        if not 1 <= int(self.max_total_attempts) <= 4:
            raise ValueError("max_total_attempts must be between 1 and 4")
        provider = str(self.preferred_provider or "").strip() or None
        model = str(self.preferred_model or "").strip() or None
        object.__setattr__(self, "routing_mode", mode)
        object.__setattr__(self, "max_total_attempts", int(self.max_total_attempts))
        object.__setattr__(self, "preferred_provider", provider)
        object.__setattr__(self, "preferred_model", model)
        object.__setattr__(self, "payload", str(self.payload or ""))


@dataclass(frozen=True, slots=True)
class ProviderUsageMetadata:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    attempt_count: int = 0
    fake_provider_calls: int = 0
    real_provider_calls: int = 0

    def __post_init__(self) -> None:
        values = (
            self.prompt_tokens,
            self.completion_tokens,
            self.total_tokens,
            self.attempt_count,
            self.fake_provider_calls,
            self.real_provider_calls,
        )
        if any(int(value) < 0 for value in values):
            raise ValueError("usage metadata must be non-negative")
        if self.real_provider_calls != 0:
            raise ValueError("runtime_v2 foundation permits no real Provider calls")

    @classmethod
    def aggregate(
        cls,
        attempts: tuple[ProviderAttemptReceipt, ...],
    ) -> ProviderUsageMetadata:
        return cls(
            prompt_tokens=sum(item.usage.prompt_tokens for item in attempts),
            completion_tokens=sum(item.usage.completion_tokens for item in attempts),
            total_tokens=sum(item.usage.total_tokens for item in attempts),
            attempt_count=len(attempts),
            fake_provider_calls=len(attempts),
            real_provider_calls=0,
        )

    def to_public_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "attempt_count": self.attempt_count,
            "fake_provider_calls": self.fake_provider_calls,
            "real_provider_calls": self.real_provider_calls,
        }


@dataclass(frozen=True, slots=True)
class ProviderAttemptReceipt:
    provider_identity: CanonicalProviderIdentity
    adapter_port: str
    attempt_number: int
    health_state: ProviderHealthState
    success: bool
    error_code: str | None
    retryable: bool
    usage: ProviderUsageMetadata

    def to_public_dict(self) -> dict[str, object]:
        return {
            "provider_identity": self.provider_identity.to_public_dict(),
            "adapter_port": self.adapter_port,
            "attempt_number": self.attempt_number,
            "health_state": self.health_state.value,
            "success": self.success,
            "error_code": self.error_code,
            "retryable": self.retryable,
            "usage": self.usage.to_public_dict(),
        }


@dataclass(frozen=True, slots=True)
class ProviderExecutionPlan:
    plan_identity: str
    task_type: str
    requirement: TaskCapabilityRequirement | None
    route_receipt: RouteDecisionReceipt | None
    capability_status: CapabilityStatus
    candidates: tuple[ProviderRuntimeProfile, ...]
    degradations: tuple[CapabilityDegradation, ...]
    self_verification_warning: str | None
    required_local_preprocessing: tuple[str, ...]
    cost_lookup_key: str | None
    max_total_attempts: int
    valid: bool
    invalid_reason: str | None = None

    @property
    def selected_runtime_profile(self) -> ProviderRuntimeProfile | None:
        return self.candidates[0] if self.candidates else None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "plan_identity": self.plan_identity,
            "task_type": self.task_type,
            "route_identity": (
                self.route_receipt.route_identity if self.route_receipt else None
            ),
            "capability_status": self.capability_status.value,
            "selected_runtime_profile": (
                self.selected_runtime_profile.to_public_dict()
                if self.selected_runtime_profile
                else None
            ),
            "fallback_provider_identities": [
                profile.identity.canonical_id for profile in self.candidates[1:]
            ],
            "degradation": [item.to_public_dict() for item in self.degradations],
            "self_verification_warning": self.self_verification_warning,
            "required_local_preprocessing": list(
                self.required_local_preprocessing
            ),
            "cost_lookup_key": self.cost_lookup_key,
            "max_total_attempts": self.max_total_attempts,
            "valid": self.valid,
            "invalid_reason": self.invalid_reason,
        }


@dataclass(frozen=True, slots=True)
class ProviderExecutionReceipt:
    receipt_identity: str
    execution_status: ProviderExecutionStatus
    task_type: str
    plan_identity: str
    route_identity: str
    selected_provider_identity: CanonicalProviderIdentity | None
    capability_status: CapabilityStatus
    runtime_health: ProviderHealthState | None
    degradations: tuple[CapabilityDegradation, ...]
    same_provider_self_verification: bool
    self_verification_warning: str | None
    required_local_preprocessing: tuple[str, ...]
    attempts: tuple[ProviderAttemptReceipt, ...]
    usage_metadata: ProviderUsageMetadata
    cost_lookup_key: str | None
    reason_code: str
    receipt_version: str = "provider_execution_receipt_v2"

    def to_public_dict(self) -> dict[str, object]:
        return {
            "receipt_version": self.receipt_version,
            "receipt_identity": self.receipt_identity,
            "execution_status": self.execution_status.value,
            "task_type": self.task_type,
            "plan_identity": self.plan_identity,
            "route_identity": self.route_identity,
            "selected_provider_identity": (
                self.selected_provider_identity.to_public_dict()
                if self.selected_provider_identity
                else None
            ),
            "capability_status": self.capability_status.value,
            "runtime_health": self.runtime_health.value if self.runtime_health else None,
            "degradation": [item.to_public_dict() for item in self.degradations],
            "same_provider_self_verification": (
                self.same_provider_self_verification
            ),
            "self_verification_warning": self.self_verification_warning,
            "required_local_preprocessing": list(
                self.required_local_preprocessing
            ),
            "attempts": [item.to_public_dict() for item in self.attempts],
            "usage_metadata": self.usage_metadata.to_public_dict(),
            "cost_lookup_key": self.cost_lookup_key,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class ProviderExecutionResult:
    output_text: str | None
    structured_output: dict[str, object] | None
    receipt: ProviderExecutionReceipt
