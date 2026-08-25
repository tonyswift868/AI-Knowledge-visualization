from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Callable, Iterable, Mapping

from app.model_routing import (
    ProviderProfileRegistry,
    RegisteredProviderProfile,
    TaskCapabilityRequirement,
)

from .adapters import FakeProviderAdapter, ProviderAdapterPort
from .models import (
    ProviderHealthState,
    ProviderRuntimeProfile,
    safe_code,
)


FakeAdapterFactory = Callable[[RegisteredProviderProfile], FakeProviderAdapter]


class ProviderRuntimeRegistry:
    """Bounded runtime state keyed by W2 canonical Provider identity."""

    def __init__(self) -> None:
        self._profiles: dict[str, ProviderRuntimeProfile] = {}
        self._adapters: dict[str, ProviderAdapterPort] = {}

    @classmethod
    def from_provider_registry(
        cls,
        provider_registry: ProviderProfileRegistry,
        *,
        health_by_identity: Mapping[str, ProviderHealthState] | None = None,
        max_attempts: int = 2,
        adapter_factory: FakeAdapterFactory | None = None,
    ) -> ProviderRuntimeRegistry:
        runtime = cls()
        health_by_identity = dict(health_by_identity or {})
        for registered in provider_registry.entries:
            adapter = (
                adapter_factory(registered)
                if adapter_factory
                else FakeProviderAdapter(
                    port_name="fake:" + registered.identity.canonical_id
                )
            )
            runtime.register(
                ProviderRuntimeProfile.from_registered_profile(
                    registered,
                    adapter_port=adapter.port_name,
                    health=health_by_identity.get(
                        registered.identity.canonical_id,
                        ProviderHealthState.AVAILABLE,
                    ),
                    max_attempts=max_attempts,
                ),
                adapter,
            )
        return runtime

    def register(
        self,
        profile: ProviderRuntimeProfile,
        adapter: ProviderAdapterPort,
    ) -> ProviderRuntimeProfile:
        if not isinstance(profile, ProviderRuntimeProfile):
            raise TypeError("profile must be a ProviderRuntimeProfile")
        if not isinstance(adapter, FakeProviderAdapter):
            raise ValueError("runtime_v2 foundation accepts Fake adapter ports only")
        if profile.adapter_port != adapter.port_name:
            raise ValueError("runtime profile adapter_port must match adapter")
        canonical_id = profile.identity.canonical_id
        existing = self._profiles.get(canonical_id)
        if existing is not None and existing != profile:
            raise ValueError("canonical Provider identity is already registered")
        self._profiles[canonical_id] = profile
        self._adapters[canonical_id] = adapter
        return profile

    @property
    def profiles(self) -> tuple[ProviderRuntimeProfile, ...]:
        return tuple(self._profiles[key] for key in sorted(self._profiles))

    @property
    def revision(self) -> str:
        payload = "\n".join(
            "|".join(
                (
                    profile.identity.canonical_id,
                    profile.adapter_port,
                    profile.health.value,
                    str(profile.max_attempts),
                )
            )
            for profile in self.profiles
        )
        return "runtime_registry_" + hashlib.sha256(
            payload.encode("utf-8")
        ).hexdigest()[:20]

    def __len__(self) -> int:
        return len(self._profiles)

    def get(self, canonical_id: str | None) -> ProviderRuntimeProfile | None:
        return self._profiles.get(str(canonical_id or "").strip())

    def adapter_for(self, canonical_id: str) -> ProviderAdapterPort:
        try:
            return self._adapters[canonical_id]
        except KeyError as exc:
            raise KeyError("runtime adapter is not registered") from exc

    def update_health(
        self,
        canonical_id: str,
        health: ProviderHealthState,
        *,
        reason_code: str | None = None,
    ) -> ProviderRuntimeProfile:
        profile = self.get(canonical_id)
        if profile is None:
            raise KeyError("runtime profile is not registered")
        normalized_health = (
            health
            if isinstance(health, ProviderHealthState)
            else ProviderHealthState(str(health).strip().upper())
        )
        normalized_reason = (
            safe_code(reason_code, fallback="RUNTIME_HEALTH_CHANGED")
            if reason_code
            else None
        )
        updated = replace(
            profile,
            health=normalized_health,
            health_reason_code=normalized_reason,
        )
        self._profiles[canonical_id] = updated
        return updated

    def eligible(
        self,
        requirement: TaskCapabilityRequirement,
        *,
        exclude_identities: Iterable[str] = (),
    ) -> tuple[ProviderRuntimeProfile, ...]:
        excluded = frozenset(exclude_identities)
        candidates = []
        for profile in self.profiles:
            if profile.identity.canonical_id in excluded or not profile.health.executable:
                continue
            capabilities = profile.capability_profile.capabilities
            if not requirement.required_capabilities <= capabilities:
                continue
            if requirement.minimum_context_window is not None and (
                profile.capability_profile.context_window is None
                or profile.capability_profile.context_window
                < requirement.minimum_context_window
            ):
                continue
            candidates.append(profile)
        return tuple(
            sorted(
                candidates,
                key=lambda profile: (
                    0
                    if profile.health is ProviderHealthState.AVAILABLE
                    else 1,
                    len(
                        requirement.preferred_capabilities
                        - profile.capability_profile.capabilities
                    ),
                    -(profile.capability_profile.context_window or 0),
                    profile.identity.canonical_id,
                ),
            )
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "runtime_registry_revision": self.revision,
            "profiles": [profile.to_public_dict() for profile in self.profiles],
        }
