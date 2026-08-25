from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace

from app.model_routing import (
    Capability,
    CapabilityDegradation,
    CapabilityStatus,
    ProviderOrchestrator,
    RoutingPreference,
    TaskCapabilityRequirement,
    TaskType,
    requirement_for,
)

from .adapters import AdapterExecutionResult
from .models import (
    ProviderAttemptReceipt,
    ProviderExecutionPlan,
    ProviderExecutionReceipt,
    ProviderExecutionRequest,
    ProviderExecutionResult,
    ProviderExecutionStatus,
    ProviderHealthState,
    ProviderRuntimeProfile,
    ProviderUsageMetadata,
    safe_code,
)
from .registry import ProviderRuntimeRegistry


_TASK_LABEL = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


class ProviderRuntimeCoordinator:
    """Endpoint-neutral execution coordinator over W2 routes and Fake ports."""

    def __init__(
        self,
        *,
        orchestrator: ProviderOrchestrator,
        runtime_registry: ProviderRuntimeRegistry,
    ) -> None:
        if not isinstance(orchestrator, ProviderOrchestrator):
            raise TypeError("orchestrator must be a ProviderOrchestrator")
        if not isinstance(runtime_registry, ProviderRuntimeRegistry):
            raise TypeError("runtime_registry must be a ProviderRuntimeRegistry")
        for profile in runtime_registry.profiles:
            registered = orchestrator.registry.get(profile.identity.canonical_id)
            if registered is None or registered.identity != profile.identity:
                raise ValueError("runtime profile must reuse W2 canonical identity")
        self.orchestrator = orchestrator
        self.runtime_registry = runtime_registry

    def plan(self, request: ProviderExecutionRequest) -> ProviderExecutionPlan:
        try:
            requirement = self._resolve_requirement(request.task_requirement)
        except ValueError:
            return self._invalid_plan(
                task_type=self._task_label(request.task_requirement),
                reason="INVALID_TASK_REQUIREMENT",
                max_total_attempts=request.max_total_attempts,
            )

        prior_route = (
            request.session.prior_provider_canonical_id
            or request.session.prior_route_identity
        )
        route = self.orchestrator.route(
            requirement,
            preference=RoutingPreference(
                preferred_provider=request.preferred_provider,
                preferred_model=request.preferred_model,
                mode=request.routing_mode,
            ),
            quality_policy=request.quality_policy,
            prior_route_identity=prior_route,
        )
        if not route.available or route.selected_provider_identity is None:
            reason = (
                "OPTIONAL_ENHANCEMENT_UNAVAILABLE"
                if route.capability_status
                is CapabilityStatus.OPTIONAL_ENHANCEMENT_UNAVAILABLE
                else "INVALID_PROVIDER_ROUTE"
            )
            return self._invalid_plan(
                task_type=requirement.task_type,
                reason=reason,
                max_total_attempts=request.max_total_attempts,
                route_receipt=route,
                requirement=requirement,
            )

        eligible = self._ordered_runtime_candidates(
            self.runtime_registry.eligible(requirement),
            request,
            requirement,
        )
        selected_id = route.selected_provider_identity.canonical_id
        selected = next(
            (
                profile
                for profile in eligible
                if profile.identity.canonical_id == selected_id
            ),
            None,
        )
        if selected is not None:
            candidates = (selected,) + tuple(
                profile for profile in eligible if profile != selected
            )
        else:
            candidates = eligible
        if not candidates:
            return self._invalid_plan(
                task_type=requirement.task_type,
                reason="NO_EXECUTABLE_RUNTIME_PROFILE",
                max_total_attempts=request.max_total_attempts,
                route_receipt=route,
                requirement=requirement,
            )

        primary = candidates[0]
        runtime_fallback = primary.identity.canonical_id != selected_id
        degradations, preprocessing, self_warning = self._profile_metadata(
            requirement=requirement,
            profile=primary,
            route_degradations=route.degradations,
            prior_provider_canonical_id=(
                request.session.prior_provider_canonical_id
            ),
        )
        if runtime_fallback:
            degradations.append(
                CapabilityDegradation(
                    code="RUNTIME_PROVIDER_FALLBACK",
                    reason=(
                        "The routed Provider has no executable runtime profile; "
                        "a bounded healthy fallback was selected."
                    ),
                )
            )
        if primary.health is ProviderHealthState.DEGRADED:
            degradations.append(
                CapabilityDegradation(
                    code="RUNTIME_HEALTH_DEGRADED",
                    reason="The selected runtime profile is available in degraded mode.",
                )
            )
        capability_status = route.capability_status
        if degradations and capability_status is CapabilityStatus.AVAILABLE:
            capability_status = CapabilityStatus.AVAILABLE_WITH_DEGRADATION
        plan_identity = self._plan_identity(
            requirement=requirement,
            route_identity=route.route_identity,
            candidates=candidates,
            degradations=tuple(degradations),
            preprocessing=preprocessing,
            max_total_attempts=request.max_total_attempts,
        )
        return ProviderExecutionPlan(
            plan_identity=plan_identity,
            task_type=requirement.task_type,
            requirement=requirement,
            route_receipt=route,
            capability_status=capability_status,
            candidates=candidates,
            degradations=tuple(degradations),
            self_verification_warning=self_warning,
            required_local_preprocessing=preprocessing,
            cost_lookup_key=primary.identity.cost_lookup_key,
            max_total_attempts=request.max_total_attempts,
            valid=True,
        )

    async def execute(
        self,
        request: ProviderExecutionRequest,
    ) -> ProviderExecutionResult:
        plan = self.plan(request)
        if not plan.valid:
            return ProviderExecutionResult(
                output_text=None,
                structured_output=None,
                receipt=self._unexecuted_receipt(plan),
            )

        attempts: list[ProviderAttemptReceipt] = []
        final_profile: ProviderRuntimeProfile | None = None
        final_result: AdapterExecutionResult | None = None
        expect_structured = bool(
            plan.requirement
            and Capability.STRUCTURED_OUTPUT
            in (
                plan.requirement.required_capabilities
                | plan.requirement.preferred_capabilities
            )
        )
        for profile in plan.candidates:
            remaining = plan.max_total_attempts - len(attempts)
            if remaining <= 0:
                break
            adapter = self.runtime_registry.adapter_for(profile.identity.canonical_id)
            for _ in range(min(profile.max_attempts, remaining)):
                result = await adapter.execute(
                    task_type=plan.task_type,
                    payload=request.payload,
                    expect_structured=expect_structured,
                    native_structured=profile.capability_profile.structured_output,
                )
                result = self._validate_structured_result(result, expect_structured)
                attempts.append(
                    ProviderAttemptReceipt(
                        provider_identity=profile.identity,
                        adapter_port=profile.adapter_port,
                        attempt_number=len(attempts) + 1,
                        health_state=profile.health,
                        success=result.success,
                        error_code=(
                            safe_code(result.error_code, fallback="FAKE_ADAPTER_ERROR")
                            if result.error_code
                            else None
                        ),
                        retryable=result.retryable,
                        usage=result.usage,
                    )
                )
                if result.success:
                    final_profile = profile
                    final_result = result
                    break
                if not result.retryable:
                    break
            if final_result is not None:
                break

        execution_status = (
            ProviderExecutionStatus.SUCCEEDED
            if final_result is not None
            else ProviderExecutionStatus.FAILED
        )
        last_attempt_profile = (
            self.runtime_registry.get(
                attempts[-1].provider_identity.canonical_id
            )
            if attempts
            else None
        )
        selected_profile = (
            final_profile or last_attempt_profile or plan.selected_runtime_profile
        )
        degradations, preprocessing, self_warning = self._profile_metadata(
            requirement=plan.requirement,
            profile=selected_profile,
            route_degradations=plan.degradations,
            prior_provider_canonical_id=(
                request.session.prior_provider_canonical_id
            ),
        )
        if (
            selected_profile
            and plan.selected_runtime_profile
            and selected_profile.identity.canonical_id
            != plan.selected_runtime_profile.identity.canonical_id
        ):
            degradations.append(
                CapabilityDegradation(
                    code="EXECUTION_PROVIDER_FALLBACK",
                    reason=(
                        "The primary Fake adapter failed within its bound; "
                        "execution continued on the next deterministic fallback."
                    ),
                )
            )
        if selected_profile and selected_profile.health is ProviderHealthState.DEGRADED:
            degradations.append(
                CapabilityDegradation(
                    code="RUNTIME_HEALTH_DEGRADED",
                    reason="The executing runtime profile is in degraded mode.",
                )
            )
        degradations = self._dedupe_degradations(degradations)
        usage = ProviderUsageMetadata.aggregate(tuple(attempts))
        capability_status = plan.capability_status
        if degradations and capability_status is CapabilityStatus.AVAILABLE:
            capability_status = CapabilityStatus.AVAILABLE_WITH_DEGRADATION
        route_identity = (
            plan.route_receipt.route_identity
            if plan.route_receipt
            else plan.plan_identity
        )
        reason_code = (
            "EXECUTION_SUCCEEDED"
            if execution_status is ProviderExecutionStatus.SUCCEEDED
            else "BOUNDED_EXECUTION_FAILED"
        )
        receipt = self._receipt(
            status=execution_status,
            task_type=plan.task_type,
            plan=plan,
            route_identity=route_identity,
            selected_profile=selected_profile,
            capability_status=capability_status,
            degradations=tuple(degradations),
            self_warning=self_warning,
            preprocessing=preprocessing,
            attempts=tuple(attempts),
            usage=usage,
            reason_code=reason_code,
        )
        return ProviderExecutionResult(
            output_text=final_result.output_text if final_result else None,
            structured_output=(
                final_result.structured_output if final_result else None
            ),
            receipt=receipt,
        )

    @staticmethod
    def _ordered_runtime_candidates(
        profiles: tuple[ProviderRuntimeProfile, ...],
        request: ProviderExecutionRequest,
        requirement: TaskCapabilityRequirement,
    ) -> tuple[ProviderRuntimeProfile, ...]:
        preferred_provider = str(request.preferred_provider or "").strip()
        preferred_provider_key = preferred_provider.lower().replace("-", "_")
        preferred_model = str(request.preferred_model or "").strip().casefold()

        def preference_rank(profile: ProviderRuntimeProfile) -> int:
            identity = profile.identity
            provider_matches = not preferred_provider or (
                preferred_provider == identity.canonical_id
                or preferred_provider_key == identity.adapter_identity
            )
            model_matches = (
                not preferred_model or identity.model.casefold() == preferred_model
            )
            return 0 if provider_matches and model_matches else 1

        return tuple(
            sorted(
                profiles,
                key=lambda profile: (
                    0
                    if profile.health is ProviderHealthState.AVAILABLE
                    else 1,
                    preference_rank(profile),
                    0
                    if request.routing_mode
                    in profile.capability_profile.routing_classes
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

    @staticmethod
    def _dedupe_degradations(
        degradations: list[CapabilityDegradation],
    ) -> list[CapabilityDegradation]:
        unique: dict[str, CapabilityDegradation] = {}
        for degradation in degradations:
            unique.setdefault(degradation.code, degradation)
        return list(unique.values())

    @staticmethod
    def _resolve_requirement(
        value: TaskCapabilityRequirement | TaskType | str,
    ) -> TaskCapabilityRequirement:
        return value if isinstance(value, TaskCapabilityRequirement) else requirement_for(value)

    @staticmethod
    def _task_label(value: object) -> str:
        raw = value.value if isinstance(value, TaskType) else str(value or "")
        normalized = raw.strip().lower()
        return normalized if _TASK_LABEL.fullmatch(normalized) else "invalid_task"

    def _invalid_plan(
        self,
        *,
        task_type: str,
        reason: str,
        max_total_attempts: int,
        route_receipt=None,
        requirement: TaskCapabilityRequirement | None = None,
    ) -> ProviderExecutionPlan:
        capability_status = (
            route_receipt.capability_status
            if route_receipt
            else CapabilityStatus.UNSUPPORTED
        )
        payload = {
            "runtime_registry_revision": self.runtime_registry.revision,
            "task_type": task_type,
            "reason": reason,
            "route_identity": (
                route_receipt.route_identity if route_receipt else None
            ),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return ProviderExecutionPlan(
            plan_identity="execution_plan_"
            + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24],
            task_type=task_type,
            requirement=requirement,
            route_receipt=route_receipt,
            capability_status=capability_status,
            candidates=(),
            degradations=(route_receipt.degradations if route_receipt else ()),
            self_verification_warning=(
                route_receipt.self_verification_warning if route_receipt else None
            ),
            required_local_preprocessing=(
                route_receipt.required_local_preprocessing
                if route_receipt
                else ()
            ),
            cost_lookup_key=None,
            max_total_attempts=max_total_attempts,
            valid=False,
            invalid_reason=reason,
        )

    def _profile_metadata(
        self,
        *,
        requirement: TaskCapabilityRequirement | None,
        profile: ProviderRuntimeProfile | None,
        route_degradations: tuple[CapabilityDegradation, ...],
        prior_provider_canonical_id: str | None,
    ) -> tuple[list[CapabilityDegradation], tuple[str, ...], str | None]:
        degradations = [
            item
            for item in route_degradations
            if item.capability is None
            and item.code != "SAME_PROVIDER_SELF_VERIFICATION"
        ]
        preprocessing = list(requirement.local_preprocessing if requirement else ())
        if requirement and profile:
            missing = (
                requirement.preferred_capabilities
                - profile.capability_profile.capabilities
            )
            for capability in sorted(missing, key=lambda item: item.value):
                fallback = requirement.fallback_for(capability)
                degradations.append(
                    CapabilityDegradation(
                        code=(
                            fallback.code
                            if fallback
                            else capability.value.upper() + "_UNAVAILABLE"
                        ),
                        capability=capability,
                        reason=(
                            fallback.description
                            if fallback
                            else f"Preferred capability {capability.value} is unavailable."
                        ),
                        pipeline=fallback.pipeline if fallback else (),
                    )
                )
                if fallback:
                    preprocessing.extend(fallback.pipeline)
        warning = self._self_verification_warning(
            requirement,
            profile,
            prior_provider_canonical_id,
        )
        if warning:
            degradations.append(
                CapabilityDegradation(
                    code="SAME_PROVIDER_SELF_VERIFICATION",
                    reason=warning,
                )
            )
        return degradations, _unique(preprocessing), warning

    def _self_verification_warning(
        self,
        requirement: TaskCapabilityRequirement | None,
        profile: ProviderRuntimeProfile | None,
        prior_provider_canonical_id: str | None,
    ) -> str | None:
        if (
            requirement is None
            or profile is None
            or not requirement.independent_verification_preferred
        ):
            return None
        eligible_scopes = {
            item.identity.provider_scope_id
            for item in self.runtime_registry.eligible(requirement)
        }
        prior = self.runtime_registry.get(prior_provider_canonical_id)
        same_scope = bool(
            prior
            and prior.identity.provider_scope_id
            == profile.identity.provider_scope_id
        )
        if not same_scope and len(eligible_scopes) != 1:
            return None
        return (
            "Verification uses the same runtime Provider scope as extraction; "
            "the execution remains available but is not an independent check."
        )

    @staticmethod
    def _validate_structured_result(
        result: AdapterExecutionResult,
        expect_structured: bool,
    ) -> AdapterExecutionResult:
        if not result.success or not expect_structured or result.structured_output:
            return result
        try:
            parsed = json.loads(str(result.output_text or ""))
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return replace(result, structured_output=parsed)
        return AdapterExecutionResult(
            success=False,
            error_code="PROMPTED_JSON_INVALID",
            retryable=False,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )

    def _plan_identity(
        self,
        *,
        requirement: TaskCapabilityRequirement,
        route_identity: str,
        candidates: tuple[ProviderRuntimeProfile, ...],
        degradations: tuple[CapabilityDegradation, ...],
        preprocessing: tuple[str, ...],
        max_total_attempts: int,
    ) -> str:
        payload = {
            "runtime_registry_revision": self.runtime_registry.revision,
            "task_type": requirement.task_type,
            "route_identity": route_identity,
            "candidates": [item.identity.canonical_id for item in candidates],
            "degradation": [item.code for item in degradations],
            "preprocessing": list(preprocessing),
            "max_total_attempts": max_total_attempts,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "execution_plan_" + hashlib.sha256(
            encoded.encode("utf-8")
        ).hexdigest()[:24]

    def _unexecuted_receipt(
        self,
        plan: ProviderExecutionPlan,
    ) -> ProviderExecutionReceipt:
        if (
            plan.capability_status
            is CapabilityStatus.OPTIONAL_ENHANCEMENT_UNAVAILABLE
        ):
            status = ProviderExecutionStatus.OPTIONAL_ENHANCEMENT_UNAVAILABLE
        elif plan.invalid_reason == "INVALID_TASK_REQUIREMENT":
            status = ProviderExecutionStatus.INVALID_ROUTE
        else:
            status = ProviderExecutionStatus.RUNTIME_UNAVAILABLE
        return self._receipt(
            status=status,
            task_type=plan.task_type,
            plan=plan,
            route_identity=(
                plan.route_receipt.route_identity
                if plan.route_receipt
                else plan.plan_identity
            ),
            selected_profile=None,
            capability_status=plan.capability_status,
            degradations=plan.degradations,
            self_warning=plan.self_verification_warning,
            preprocessing=plan.required_local_preprocessing,
            attempts=(),
            usage=ProviderUsageMetadata(),
            reason_code=plan.invalid_reason or "RUNTIME_UNAVAILABLE",
        )

    def _receipt(
        self,
        *,
        status: ProviderExecutionStatus,
        task_type: str,
        plan: ProviderExecutionPlan,
        route_identity: str,
        selected_profile: ProviderRuntimeProfile | None,
        capability_status: CapabilityStatus,
        degradations: tuple[CapabilityDegradation, ...],
        self_warning: str | None,
        preprocessing: tuple[str, ...],
        attempts: tuple[ProviderAttemptReceipt, ...],
        usage: ProviderUsageMetadata,
        reason_code: str,
    ) -> ProviderExecutionReceipt:
        payload = {
            "plan_identity": plan.plan_identity,
            "route_identity": route_identity,
            "status": status.value,
            "selected_provider": (
                selected_profile.identity.canonical_id if selected_profile else None
            ),
            "attempts": [
                {
                    "provider": item.provider_identity.canonical_id,
                    "success": item.success,
                    "error_code": item.error_code,
                }
                for item in attempts
            ],
            "reason_code": reason_code,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return ProviderExecutionReceipt(
            receipt_identity="execution_receipt_"
            + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24],
            execution_status=status,
            task_type=task_type,
            plan_identity=plan.plan_identity,
            route_identity=route_identity,
            selected_provider_identity=(
                selected_profile.identity if selected_profile else None
            ),
            capability_status=capability_status,
            runtime_health=(selected_profile.health if selected_profile else None),
            degradations=degradations,
            same_provider_self_verification=bool(self_warning),
            self_verification_warning=self_warning,
            required_local_preprocessing=preprocessing,
            attempts=attempts,
            usage_metadata=usage,
            cost_lookup_key=(
                selected_profile.identity.cost_lookup_key
                if selected_profile
                else None
            ),
            reason_code=safe_code(reason_code, fallback="RUNTIME_EXECUTION_STATUS"),
        )
