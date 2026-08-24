from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .capabilities import (
    Capability,
    CapabilityStatus,
    ProviderCapabilityProfile,
    RoutingMode,
)
from .requirements import TaskCapabilityRequirement, TaskType, requirement_for


def _provider_key(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _same_provider(left: str | None, right: str | None) -> bool:
    return bool(left and right and _provider_key(left) == _provider_key(right))


def _same_model(left: str | None, right: str | None) -> bool:
    return bool(left and right and str(left).strip().casefold() == str(right).strip().casefold())


@dataclass(frozen=True, slots=True)
class RoutingPreference:
    preferred_provider: str | None = None
    preferred_model: str | None = None
    mode: RoutingMode = RoutingMode.BALANCED

    def __post_init__(self) -> None:
        provider = str(self.preferred_provider or "").strip() or None
        model = str(self.preferred_model or "").strip() or None
        mode = self.mode
        if not isinstance(mode, RoutingMode):
            mode = RoutingMode(str(mode).strip().lower())
        object.__setattr__(self, "preferred_provider", provider)
        object.__setattr__(self, "preferred_model", model)
        object.__setattr__(self, "mode", mode)

    @property
    def is_explicit(self) -> bool:
        return bool(self.preferred_provider or self.preferred_model)

    def matches(self, profile: ProviderCapabilityProfile) -> bool:
        if self.preferred_provider and not _same_provider(
            self.preferred_provider, profile.provider
        ):
            return False
        if self.preferred_model and not _same_model(
            self.preferred_model, profile.model
        ):
            return False
        return self.is_explicit


@dataclass(frozen=True, slots=True)
class CapabilityDegradation:
    code: str
    reason: str
    capability: Capability | None = None
    pipeline: tuple[str, ...] = ()

    def to_public_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "capability": self.capability.value if self.capability else None,
            "reason": self.reason,
            "pipeline": list(self.pipeline),
        }


@dataclass(frozen=True, slots=True)
class RoutingQualityMetadata:
    same_provider_self_verification: bool = False
    independent_verification_available: bool | None = None
    notes: tuple[str, ...] = ()

    def to_public_dict(self) -> dict[str, object]:
        return {
            "same_provider_self_verification": self.same_provider_self_verification,
            "independent_verification_available": self.independent_verification_available,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    task_type: str
    selected_provider: str | None
    selected_model: str | None
    status: CapabilityStatus
    degradations: tuple[CapabilityDegradation, ...]
    reason: str
    routing_class: RoutingMode
    matched_capabilities: tuple[Capability, ...] = ()
    missing_capabilities: tuple[Capability, ...] = ()
    preprocessing: tuple[str, ...] = ()
    quality_metadata: RoutingQualityMetadata = RoutingQualityMetadata()

    @property
    def capability_status(self) -> CapabilityStatus:
        return self.status

    @property
    def degradation(self) -> tuple[CapabilityDegradation, ...]:
        return self.degradations

    @property
    def available(self) -> bool:
        return self.status in {
            CapabilityStatus.AVAILABLE,
            CapabilityStatus.AVAILABLE_WITH_DEGRADATION,
        }

    def to_public_dict(self) -> dict[str, object]:
        """Return routing metadata without accepting or exposing credentials."""

        return {
            "task_type": self.task_type,
            "selected_provider": self.selected_provider,
            "selected_model": self.selected_model,
            "capability_status": self.status.value,
            "degradation": [item.to_public_dict() for item in self.degradations],
            "reason": self.reason,
            "routing_class": self.routing_class.value,
            "matched_capabilities": [item.value for item in self.matched_capabilities],
            "missing_capabilities": [item.value for item in self.missing_capabilities],
            "preprocessing": list(self.preprocessing),
            "quality_metadata": self.quality_metadata.to_public_dict(),
        }

    def telemetry_metadata(self) -> dict[str, str | None]:
        """Prepare integration metadata without changing the telemetry contract."""

        return {
            "task_type": self.task_type,
            "selected_model": self.selected_model,
            "routing_class": self.routing_class.value,
            "capability_status": self.status.value,
        }


def _missing_required(
    profile: ProviderCapabilityProfile,
    requirement: TaskCapabilityRequirement,
) -> frozenset[Capability]:
    missing = {
        capability
        for capability in requirement.required_capabilities
        if not profile.supports(capability)
    }
    if requirement.minimum_context_window is not None and (
        profile.context_window is None
        or profile.context_window < requirement.minimum_context_window
    ):
        missing.add(Capability.CONTEXT_WINDOW)
    return frozenset(missing)


def _actual_routing_class(
    profile: ProviderCapabilityProfile,
    preference: RoutingPreference,
) -> RoutingMode:
    if preference.mode in profile.routing_classes:
        return preference.mode
    return profile.routing_classes[0]


def _unique_pipeline(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


class CapabilityRouter:
    """Select a model deterministically from declared, credential-free profiles."""

    def route(
        self,
        available_providers: Iterable[ProviderCapabilityProfile],
        task_requirement: TaskCapabilityRequirement | TaskType | str,
        *,
        preference: RoutingPreference | None = None,
        prior_provider: str | None = None,
        prior_model: str | None = None,
    ) -> RoutingDecision:
        requirement = (
            task_requirement
            if isinstance(task_requirement, TaskCapabilityRequirement)
            else requirement_for(task_requirement)
        )
        preference = preference or RoutingPreference()
        profiles = tuple(available_providers)
        if any(not isinstance(profile, ProviderCapabilityProfile) for profile in profiles):
            raise TypeError("available_providers must contain capability profiles")

        eligible = tuple(
            profile
            for profile in profiles
            if not _missing_required(profile, requirement)
        )
        if not eligible:
            missing = self._closest_missing(profiles, requirement)
            status = (
                CapabilityStatus.OPTIONAL_ENHANCEMENT_UNAVAILABLE
                if requirement.optional_enhancement
                else CapabilityStatus.UNSUPPORTED
            )
            capability_names = ", ".join(item.value for item in missing) or "provider"
            return RoutingDecision(
                task_type=requirement.task_type,
                selected_provider=None,
                selected_model=None,
                status=status,
                degradations=(),
                reason=(
                    f"No configured model satisfies required capabilities: "
                    f"{capability_names}."
                ),
                routing_class=preference.mode,
                missing_capabilities=missing,
                preprocessing=requirement.local_preprocessing,
                quality_metadata=self._unavailable_quality_metadata(requirement),
            )

        fully_capable = tuple(
            profile
            for profile in eligible
            if requirement.preferred_capabilities <= profile.capabilities
        )
        candidate_pool = fully_capable or eligible
        candidate_pool = self._prefer_independent_verifier(
            candidate_pool,
            requirement,
            prior_provider=prior_provider,
            prior_model=prior_model,
        )
        selected = min(
            candidate_pool,
            key=lambda profile: self._rank(profile, requirement, preference),
        )

        degradations: list[CapabilityDegradation] = []
        pipeline = list(requirement.local_preprocessing)
        missing_preferred = tuple(
            sorted(
                requirement.preferred_capabilities - selected.capabilities,
                key=lambda capability: capability.value,
            )
        )
        for capability in missing_preferred:
            fallback = requirement.fallback_for(capability)
            degradation = CapabilityDegradation(
                code=(
                    fallback.code
                    if fallback
                    else f"{capability.value.upper()}_UNAVAILABLE"
                ),
                capability=capability,
                reason=(
                    fallback.description
                    if fallback
                    else f"Preferred capability {capability.value} is unavailable."
                ),
                pipeline=fallback.pipeline if fallback else (),
            )
            degradations.append(degradation)
            pipeline.extend(degradation.pipeline)

        if preference.is_explicit and not preference.matches(selected):
            degradations.append(
                CapabilityDegradation(
                    code="PREFERRED_PROVIDER_FALLBACK",
                    reason=(
                        "The preferred Provider/model could not satisfy the task; "
                        "a capable configured model was selected."
                    ),
                )
            )
        if preference.mode not in selected.routing_classes:
            degradations.append(
                CapabilityDegradation(
                    code="ROUTING_MODE_FALLBACK",
                    reason=(
                        f"The selected model does not declare the {preference.mode.value} "
                        "routing class; its declared routing class was used."
                    ),
                )
            )

        quality_metadata, verification_degradation = self._verification_metadata(
            selected,
            eligible,
            requirement,
            prior_provider=prior_provider,
            prior_model=prior_model,
        )
        if verification_degradation is not None:
            degradations.append(verification_degradation)

        status = (
            CapabilityStatus.AVAILABLE_WITH_DEGRADATION
            if degradations
            else CapabilityStatus.AVAILABLE
        )
        matched = tuple(
            sorted(
                (requirement.required_capabilities | requirement.preferred_capabilities)
                & selected.capabilities,
                key=lambda capability: capability.value,
            )
        )
        actual_class = _actual_routing_class(selected, preference)
        return RoutingDecision(
            task_type=requirement.task_type,
            selected_provider=selected.provider,
            selected_model=selected.model,
            status=status,
            degradations=tuple(degradations),
            reason=(
                f"Selected {selected.provider}/{selected.model} for "
                f"{requirement.task_type} with status {status.value}."
            ),
            routing_class=actual_class,
            matched_capabilities=matched,
            missing_capabilities=missing_preferred,
            preprocessing=_unique_pipeline(pipeline),
            quality_metadata=quality_metadata,
        )

    @staticmethod
    def _closest_missing(
        profiles: tuple[ProviderCapabilityProfile, ...],
        requirement: TaskCapabilityRequirement,
    ) -> tuple[Capability, ...]:
        if not profiles:
            missing = set(requirement.required_capabilities)
            if requirement.minimum_context_window is not None:
                missing.add(Capability.CONTEXT_WINDOW)
            return tuple(sorted(missing, key=lambda capability: capability.value))
        closest = min(
            profiles,
            key=lambda profile: (
                len(_missing_required(profile, requirement)),
                _provider_key(profile.provider),
                profile.model.casefold(),
            ),
        )
        return tuple(
            sorted(
                _missing_required(closest, requirement),
                key=lambda capability: capability.value,
            )
        )

    @staticmethod
    def _rank(
        profile: ProviderCapabilityProfile,
        requirement: TaskCapabilityRequirement,
        preference: RoutingPreference,
    ) -> tuple[object, ...]:
        missing_preferred = len(
            requirement.preferred_capabilities - profile.capabilities
        )
        return (
            missing_preferred,
            0 if preference.matches(profile) else 1,
            0 if preference.mode in profile.routing_classes else 1,
            -(profile.context_window or 0),
            _provider_key(profile.provider),
            profile.model.casefold(),
        )

    @staticmethod
    def _prefer_independent_verifier(
        profiles: tuple[ProviderCapabilityProfile, ...],
        requirement: TaskCapabilityRequirement,
        *,
        prior_provider: str | None,
        prior_model: str | None,
    ) -> tuple[ProviderCapabilityProfile, ...]:
        if not requirement.independent_verification_preferred or not prior_provider:
            return profiles
        independent = tuple(
            profile
            for profile in profiles
            if not _same_provider(profile.provider, prior_provider)
        )
        return independent or profiles

    @staticmethod
    def _unavailable_quality_metadata(
        requirement: TaskCapabilityRequirement,
    ) -> RoutingQualityMetadata:
        if not requirement.independent_verification_preferred:
            return RoutingQualityMetadata()
        return RoutingQualityMetadata(independent_verification_available=False)

    @staticmethod
    def _verification_metadata(
        selected: ProviderCapabilityProfile,
        eligible: tuple[ProviderCapabilityProfile, ...],
        requirement: TaskCapabilityRequirement,
        *,
        prior_provider: str | None,
        prior_model: str | None,
    ) -> tuple[RoutingQualityMetadata, CapabilityDegradation | None]:
        if not requirement.independent_verification_preferred:
            return RoutingQualityMetadata(), None
        provider_identities = {_provider_key(profile.provider) for profile in eligible}
        same_as_prior = bool(
            prior_provider and _same_provider(selected.provider, prior_provider)
        )
        self_verification = same_as_prior or len(provider_identities) == 1
        independent_available = len(provider_identities) > 1 and not self_verification
        if not self_verification:
            return RoutingQualityMetadata(
                independent_verification_available=independent_available,
            ), None
        same_model = bool(
            same_as_prior
            and prior_model
            and _same_model(selected.model, prior_model)
        )
        repeated_scope = "Provider/model" if same_model else "Provider"
        note = (
            f"Verification uses the same configured {repeated_scope} as extraction; "
            "the result remains available but is not an independent Provider check."
        )
        return (
            RoutingQualityMetadata(
                same_provider_self_verification=True,
                independent_verification_available=False,
                notes=(note,),
            ),
            CapabilityDegradation(
                code="SAME_PROVIDER_SELF_VERIFICATION",
                reason=note,
            ),
        )


def route_provider(
    available_providers: Iterable[ProviderCapabilityProfile],
    task_requirement: TaskCapabilityRequirement | TaskType | str,
    *,
    preference: RoutingPreference | None = None,
    prior_provider: str | None = None,
    prior_model: str | None = None,
) -> RoutingDecision:
    return CapabilityRouter().route(
        available_providers,
        task_requirement,
        preference=preference,
        prior_provider=prior_provider,
        prior_model=prior_model,
    )
