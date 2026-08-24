from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Mapping

from .capabilities import (
    Capability,
    CapabilityStatus,
    ProviderCapabilityProfile,
    RoutingMode,
)
from .identity import CanonicalProviderIdentity
from .registry import ProviderProfileRegistry, RegisteredProviderProfile
from .requirements import (
    DEFAULT_TASK_REQUIREMENTS,
    TaskCapabilityRequirement,
    TaskType,
    requirement_for,
)
from .router import (
    CapabilityDegradation,
    CapabilityRouter,
    RoutingPreference,
)


@dataclass(frozen=True, slots=True)
class RoutingQualityPolicy:
    prefer_independent_verification: bool = True


@dataclass(frozen=True, slots=True)
class RouteDecisionReceipt:
    route_identity: str
    registry_revision: str
    task_type: str
    selected_provider: str | None
    selected_model: str | None
    selected_provider_identity: CanonicalProviderIdentity | None
    capability_status: CapabilityStatus
    degradations: tuple[CapabilityDegradation, ...]
    same_provider_self_verification: bool
    self_verification_warning: str | None
    required_local_preprocessing: tuple[str, ...]
    reason: str
    routing_class: RoutingMode
    matched_capabilities: tuple[Capability, ...]
    missing_capabilities: tuple[Capability, ...]
    cost_lookup_key: str | None
    quality_policy: RoutingQualityPolicy
    receipt_version: str = "provider_route_receipt_v1"

    @property
    def available(self) -> bool:
        return self.capability_status in {
            CapabilityStatus.AVAILABLE,
            CapabilityStatus.AVAILABLE_WITH_DEGRADATION,
        }

    def to_public_dict(self) -> dict[str, object]:
        return {
            "receipt_version": self.receipt_version,
            "route_identity": self.route_identity,
            "registry_revision": self.registry_revision,
            "task_type": self.task_type,
            "selected_provider": self.selected_provider,
            "selected_model": self.selected_model,
            "selected_provider_identity": (
                self.selected_provider_identity.to_public_dict()
                if self.selected_provider_identity
                else None
            ),
            "capability_status": self.capability_status.value,
            "degradation": [item.to_public_dict() for item in self.degradations],
            "same_provider_self_verification": (
                self.same_provider_self_verification
            ),
            "self_verification_warning": self.self_verification_warning,
            "required_local_preprocessing": list(
                self.required_local_preprocessing
            ),
            "reason": self.reason,
            "routing_class": self.routing_class.value,
            "matched_capabilities": [item.value for item in self.matched_capabilities],
            "missing_capabilities": [item.value for item in self.missing_capabilities],
            "cost_lookup_key": self.cost_lookup_key,
            "quality_policy": {
                "prefer_independent_verification": (
                    self.quality_policy.prefer_independent_verification
                )
            },
        }

    def telemetry_metadata(self) -> dict[str, str | None]:
        return {
            "task_type": self.task_type,
            "selected_model": self.selected_model,
            "routing_class": self.routing_class.value,
            "capability_status": self.capability_status.value,
            "route_identity": self.route_identity,
            "cost_lookup_key": self.cost_lookup_key,
        }


@dataclass(frozen=True, slots=True)
class TaskRoutingDiagnostic:
    task: str
    primary_route: str | None
    selected_provider: str | None
    selected_model: str | None
    capability_status: CapabilityStatus
    degradation: tuple[str, ...]
    optional_enhancements: tuple[str, ...]
    missing_capabilities: tuple[Capability, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "task": self.task,
            "primary_route": self.primary_route,
            "selected_provider": self.selected_provider,
            "selected_model": self.selected_model,
            "capability_status": self.capability_status.value,
            "degradation": list(self.degradation),
            "optional_enhancements": list(self.optional_enhancements),
            "missing_capabilities": [item.value for item in self.missing_capabilities],
        }


@dataclass(frozen=True, slots=True)
class TaskRoutingMatrix:
    registry_revision: str
    tasks: tuple[TaskRoutingDiagnostic, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "registry_revision": self.registry_revision,
            "tasks": [task.to_public_dict() for task in self.tasks],
        }


class ProviderOrchestrator:
    """Build auditable routes from the current credential-free registry snapshot."""

    def __init__(
        self,
        registry: ProviderProfileRegistry,
        *,
        router: CapabilityRouter | None = None,
    ) -> None:
        if not isinstance(registry, ProviderProfileRegistry):
            raise TypeError("registry must be a ProviderProfileRegistry")
        self.registry = registry
        self.router = router or CapabilityRouter()
        self._route_history: dict[str, str | None] = {}

    def route(
        self,
        task_requirement: TaskCapabilityRequirement | TaskType | str,
        *,
        preference: RoutingPreference | None = None,
        quality_policy: RoutingQualityPolicy | None = None,
        prior_receipt: RouteDecisionReceipt | None = None,
        prior_route_identity: str | None = None,
    ) -> RouteDecisionReceipt:
        requirement = (
            task_requirement
            if isinstance(task_requirement, TaskCapabilityRequirement)
            else requirement_for(task_requirement)
        )
        preference = preference or RoutingPreference()
        quality_policy = quality_policy or RoutingQualityPolicy()
        prior_canonical_id = self._prior_canonical_id(
            prior_receipt=prior_receipt,
            prior_route_identity=prior_route_identity,
        )
        router_requirement = requirement
        if requirement.independent_verification_preferred:
            router_requirement = replace(
                requirement,
                independent_verification_preferred=False,
            )

        entries = self.registry.entries
        routing_entries = self._routing_entries(
            entries,
            requirement,
            quality_policy,
            prior_canonical_id,
        )
        router_profiles = tuple(
            entry.as_router_profile() for entry in routing_entries
        )
        translated_preference = self._translate_preference(
            router_profiles,
            router_requirement,
            preference,
            prior_canonical_id,
        )
        decision = self.router.route(
            router_profiles,
            router_requirement,
            preference=translated_preference,
        )
        selected_entry = self.registry.get(decision.selected_provider)
        degradations = list(decision.degradations)
        self_warning = self._self_verification_warning(
            selected_entry,
            entries,
            requirement,
            prior_canonical_id,
        )
        if self_warning and not any(
            item.code == "SAME_PROVIDER_SELF_VERIFICATION" for item in degradations
        ):
            degradations.append(
                CapabilityDegradation(
                    code="SAME_PROVIDER_SELF_VERIFICATION",
                    reason=self_warning,
                )
            )
        capability_status = decision.status
        if degradations and capability_status is CapabilityStatus.AVAILABLE:
            capability_status = CapabilityStatus.AVAILABLE_WITH_DEGRADATION
        selected_identity = selected_entry.identity if selected_entry else None
        selected_provider = (
            selected_identity.adapter_identity if selected_identity else None
        )
        selected_model = selected_identity.model if selected_identity else None
        reason = (
            f"Selected {selected_provider}/{selected_model} as "
            f"{selected_identity.canonical_id} for {requirement.task_type} with "
            f"status {capability_status.value}."
            if selected_identity
            else decision.reason
        )
        route_identity = self._route_identity(
            requirement=requirement,
            preference=preference,
            quality_policy=quality_policy,
            selected_identity=selected_identity,
            capability_status=capability_status,
            degradations=tuple(degradations),
            preprocessing=decision.preprocessing,
        )
        receipt = RouteDecisionReceipt(
            route_identity=route_identity,
            registry_revision=self.registry.revision,
            task_type=requirement.task_type,
            selected_provider=selected_provider,
            selected_model=selected_model,
            selected_provider_identity=selected_identity,
            capability_status=capability_status,
            degradations=tuple(degradations),
            same_provider_self_verification=bool(self_warning),
            self_verification_warning=self_warning,
            required_local_preprocessing=decision.preprocessing,
            reason=reason,
            routing_class=decision.routing_class,
            matched_capabilities=decision.matched_capabilities,
            missing_capabilities=decision.missing_capabilities,
            cost_lookup_key=(
                selected_identity.cost_lookup_key if selected_identity else None
            ),
            quality_policy=quality_policy,
        )
        self._route_history[route_identity] = (
            selected_identity.canonical_id if selected_identity else None
        )
        return receipt

    def diagnostics(
        self,
        *,
        requirements: Mapping[
            str, TaskCapabilityRequirement
        ] = DEFAULT_TASK_REQUIREMENTS,
        preference: RoutingPreference | None = None,
        quality_policy: RoutingQualityPolicy | None = None,
    ) -> TaskRoutingMatrix:
        tasks = []
        for task_type in sorted(requirements):
            requirement = requirements[task_type]
            receipt = self.route(
                requirement,
                preference=preference,
                quality_policy=quality_policy,
            )
            optional_enhancements = (
                tuple(item.value for item in receipt.missing_capabilities)
                if requirement.optional_enhancement
                else ()
            )
            tasks.append(
                TaskRoutingDiagnostic(
                    task=task_type,
                    primary_route=(
                        receipt.route_identity if receipt.available else None
                    ),
                    selected_provider=receipt.selected_provider,
                    selected_model=receipt.selected_model,
                    capability_status=receipt.capability_status,
                    degradation=tuple(item.code for item in receipt.degradations),
                    optional_enhancements=optional_enhancements,
                    missing_capabilities=receipt.missing_capabilities,
                )
            )
        return TaskRoutingMatrix(
            registry_revision=self.registry.revision,
            tasks=tuple(tasks),
        )

    def _translate_preference(
        self,
        router_profiles: tuple[ProviderCapabilityProfile, ...],
        requirement: TaskCapabilityRequirement,
        preference: RoutingPreference,
        prior_canonical_id: str | None,
    ) -> RoutingPreference:
        if not preference.is_explicit:
            return RoutingPreference(mode=preference.mode)
        matching_entries = self.registry.matching(
            provider=preference.preferred_provider,
            model=preference.preferred_model,
        )
        if not matching_entries:
            return RoutingPreference(
                preferred_provider="provider_preference_unavailable",
                mode=preference.mode,
            )
        matching_ids = {entry.identity.canonical_id for entry in matching_entries}
        matching_profiles = tuple(
            profile for profile in router_profiles if profile.provider in matching_ids
        )
        preferred_decision = self.router.route(
            matching_profiles,
            requirement,
            preference=RoutingPreference(mode=preference.mode),
            prior_provider=prior_canonical_id,
        )
        if not preferred_decision.available:
            return RoutingPreference(
                preferred_provider="provider_preference_unavailable",
                mode=preference.mode,
            )
        return RoutingPreference(
            preferred_provider=preferred_decision.selected_provider,
            preferred_model=preferred_decision.selected_model,
            mode=preference.mode,
        )

    @staticmethod
    def _entry_satisfies_required(
        entry: RegisteredProviderProfile,
        requirement: TaskCapabilityRequirement,
    ) -> bool:
        if not requirement.required_capabilities <= entry.profile.capabilities:
            return False
        return requirement.minimum_context_window is None or (
            entry.profile.context_window is not None
            and entry.profile.context_window >= requirement.minimum_context_window
        )

    def _routing_entries(
        self,
        entries: tuple[RegisteredProviderProfile, ...],
        requirement: TaskCapabilityRequirement,
        quality_policy: RoutingQualityPolicy,
        prior_canonical_id: str | None,
    ) -> tuple[RegisteredProviderProfile, ...]:
        prior_entry = self.registry.get(prior_canonical_id)
        if (
            not requirement.independent_verification_preferred
            or not quality_policy.prefer_independent_verification
            or prior_entry is None
        ):
            return entries
        independent_eligible = tuple(
            entry
            for entry in entries
            if entry.identity.provider_scope_id
            != prior_entry.identity.provider_scope_id
            and self._entry_satisfies_required(entry, requirement)
        )
        if not independent_eligible:
            return entries
        independent_scopes = {
            entry.identity.provider_scope_id for entry in independent_eligible
        }
        return tuple(
            entry
            for entry in entries
            if entry.identity.provider_scope_id in independent_scopes
        )

    def _prior_canonical_id(
        self,
        *,
        prior_receipt: RouteDecisionReceipt | None,
        prior_route_identity: str | None,
    ) -> str | None:
        if prior_receipt and prior_receipt.selected_provider_identity:
            return prior_receipt.selected_provider_identity.canonical_id
        route_identity = str(prior_route_identity or "").strip()
        if self.registry.get(route_identity):
            return route_identity
        return self._route_history.get(route_identity)

    @staticmethod
    def _self_verification_warning(
        selected_entry: RegisteredProviderProfile | None,
        entries: tuple[RegisteredProviderProfile, ...],
        requirement: TaskCapabilityRequirement,
        prior_canonical_id: str | None,
    ) -> str | None:
        if not requirement.independent_verification_preferred or not selected_entry:
            return None
        prior_entry = next(
            (
                entry
                for entry in entries
                if entry.identity.canonical_id == prior_canonical_id
            ),
            None,
        )
        same_as_prior = bool(
            prior_entry
            and selected_entry.identity.provider_scope_id
            == prior_entry.identity.provider_scope_id
        )
        eligible_identities = {
            entry.identity.provider_scope_id
            for entry in entries
            if ProviderOrchestrator._entry_satisfies_required(entry, requirement)
        }
        self_verification = same_as_prior or len(eligible_identities) == 1
        if not self_verification:
            return None
        return (
            "Verification uses the same canonical Provider scope as extraction; "
            "the route remains available but is not an independent Provider check."
        )

    def _route_identity(
        self,
        *,
        requirement: TaskCapabilityRequirement,
        preference: RoutingPreference,
        quality_policy: RoutingQualityPolicy,
        selected_identity: CanonicalProviderIdentity | None,
        capability_status: CapabilityStatus,
        degradations: tuple[CapabilityDegradation, ...],
        preprocessing: tuple[str, ...],
    ) -> str:
        payload = {
            "receipt_version": "provider_route_receipt_v1",
            "registry_revision": self.registry.revision,
            "task_type": requirement.task_type,
            "required_capabilities": sorted(
                item.value for item in requirement.required_capabilities
            ),
            "preferred_capabilities": sorted(
                item.value for item in requirement.preferred_capabilities
            ),
            "minimum_context_window": requirement.minimum_context_window,
            "optional_enhancement": requirement.optional_enhancement,
            "independent_verification_preferred": (
                requirement.independent_verification_preferred
            ),
            "selected_provider_identity": (
                selected_identity.canonical_id if selected_identity else None
            ),
            "capability_status": capability_status.value,
            "degradation": [item.code for item in degradations],
            "preprocessing": list(preprocessing),
            "preferred_provider": preference.preferred_provider,
            "preferred_model": preference.preferred_model,
            "routing_mode": preference.mode.value,
            "prefer_independent_verification": (
                quality_policy.prefer_independent_verification
            ),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "route_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
