from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

from .capabilities import ProviderCapabilityProfile
from .identity import (
    CanonicalProviderIdentity,
    ProviderProfileRegistration,
    normalize_adapter_identity,
)


@dataclass(frozen=True, slots=True)
class RegisteredProviderProfile:
    identity: CanonicalProviderIdentity
    profile: ProviderCapabilityProfile

    def as_router_profile(self) -> ProviderCapabilityProfile:
        """Use canonical identity internally while preserving declared capability data."""

        return ProviderCapabilityProfile(
            provider=self.identity.canonical_id,
            model=self.profile.model,
            text_generation=self.profile.text_generation,
            structured_output=self.profile.structured_output,
            tool_calling=self.profile.tool_calling,
            reasoning=self.profile.reasoning,
            vision=self.profile.vision,
            audio=self.profile.audio,
            streaming=self.profile.streaming,
            context_window=self.profile.context_window,
            routing_classes=self.profile.routing_classes,
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "identity": self.identity.to_public_dict(),
            "capabilities": sorted(
                capability.value for capability in self.profile.capabilities
            ),
            "context_window": self.profile.context_window,
            "routing_classes": [value.value for value in self.profile.routing_classes],
        }


class ProviderProfileRegistry:
    """Stable capability snapshot derived from sanitized Provider registrations."""

    def __init__(
        self,
        registrations: Iterable[ProviderProfileRegistration] = (),
    ) -> None:
        self._entries: dict[str, RegisteredProviderProfile] = {}
        for registration in registrations:
            self.register(registration)

    def register(
        self,
        registration: ProviderProfileRegistration,
    ) -> RegisteredProviderProfile:
        if not isinstance(registration, ProviderProfileRegistration):
            raise TypeError("registration must be a ProviderProfileRegistration")
        identity = CanonicalProviderIdentity.from_registration(registration)
        entry = RegisteredProviderProfile(identity=identity, profile=registration.profile)
        existing = self._entries.get(identity.canonical_id)
        if existing is not None:
            return existing
        self._entries[identity.canonical_id] = entry
        return entry

    @property
    def entries(self) -> tuple[RegisteredProviderProfile, ...]:
        return tuple(self._entries[key] for key in sorted(self._entries))

    @property
    def revision(self) -> str:
        payload = "\n".join(entry.identity.canonical_id for entry in self.entries)
        return "registry_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, canonical_id: str | None) -> RegisteredProviderProfile | None:
        return self._entries.get(str(canonical_id or "").strip())

    def matching(
        self,
        *,
        provider: str | None,
        model: str | None,
    ) -> tuple[RegisteredProviderProfile, ...]:
        provider_value = str(provider or "").strip()
        model_value = str(model or "").strip().casefold()
        normalized_provider = (
            normalize_adapter_identity(provider_value) if provider_value else None
        )
        matches = []
        for entry in self.entries:
            identity = entry.identity
            provider_matches = not provider_value or (
                provider_value == identity.canonical_id
                or normalized_provider == identity.adapter_identity
            )
            model_matches = not model_value or identity.model.casefold() == model_value
            if provider_matches and model_matches:
                matches.append(entry)
        return tuple(matches)

    def to_public_dict(self) -> dict[str, object]:
        return {
            "registry_revision": self.revision,
            "providers": [entry.to_public_dict() for entry in self.entries],
        }
