from __future__ import annotations

import asyncio

import pytest

from app.model_routing import (
    CapabilityStatus,
    ProviderCapabilityProfile,
    ProviderOrchestrator,
    ProviderProfileRegistration,
    ProviderProfileRegistry,
    RoutingMode,
    TaskType,
)
from app.provider.runtime_v2 import (
    AdapterExecutionResult,
    FakeProviderAdapter,
    ProviderExecutionRequest,
    ProviderExecutionStatus,
    ProviderHealthState,
    ProviderRuntimeCoordinator,
    ProviderRuntimeProfile,
    ProviderRuntimeRegistry,
    ProviderSessionContext,
)


def registration(
    *,
    adapter: str = "openai_compatible",
    base_url: str = "https://gateway.example/v1",
    model: str = "deepseek-chat",
    **capabilities: object,
) -> ProviderProfileRegistration:
    values: dict[str, object] = {
        "provider": adapter,
        "model": model,
        "text_generation": True,
    }
    values.update(capabilities)
    return ProviderProfileRegistration(
        adapter_identity=adapter,
        configured_base_url=base_url,
        profile=ProviderCapabilityProfile(**values),  # type: ignore[arg-type]
    )


def build_runtime(
    *registrations: ProviderProfileRegistration,
    health_by_model: dict[str, ProviderHealthState] | None = None,
    scripts_by_model: dict[str, tuple[AdapterExecutionResult, ...]] | None = None,
    attempts_by_model: dict[str, int] | None = None,
) -> tuple[
    ProviderRuntimeCoordinator,
    ProviderRuntimeRegistry,
    dict[str, FakeProviderAdapter],
]:
    provider_registry = ProviderProfileRegistry(registrations)
    runtime_registry = ProviderRuntimeRegistry()
    adapters: dict[str, FakeProviderAdapter] = {}
    for entry in provider_registry.entries:
        adapter = FakeProviderAdapter(
            port_name="fake:" + entry.identity.canonical_id,
            script=(scripts_by_model or {}).get(entry.profile.model, ()),
        )
        adapters[entry.profile.model] = adapter
        runtime_registry.register(
            ProviderRuntimeProfile.from_registered_profile(
                entry,
                adapter_port=adapter.port_name,
                health=(health_by_model or {}).get(
                    entry.profile.model,
                    ProviderHealthState.AVAILABLE,
                ),
                max_attempts=(attempts_by_model or {}).get(entry.profile.model, 2),
            ),
            adapter,
        )
    return (
        ProviderRuntimeCoordinator(
            orchestrator=ProviderOrchestrator(provider_registry),
            runtime_registry=runtime_registry,
        ),
        runtime_registry,
        adapters,
    )


def request(
    task: TaskType | str,
    *,
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    payload: str = "bounded fake payload",
    max_total_attempts: int = 3,
    prior_provider_canonical_id: str | None = None,
    routing_mode: RoutingMode = RoutingMode.BALANCED,
) -> ProviderExecutionRequest:
    return ProviderExecutionRequest(
        task_requirement=task,
        session=ProviderSessionContext(
            session_id="session-test",
            request_id="request-test",
            prior_provider_canonical_id=prior_provider_canonical_id,
        ),
        payload=payload,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        routing_mode=routing_mode,
        max_total_attempts=max_total_attempts,
    )


@pytest.mark.parametrize(
    ("task", "expected_status"),
    [
        (TaskType.TEACHER, CapabilityStatus.AVAILABLE),
        (TaskType.KNOWLEDGE_EXTRACT, CapabilityStatus.AVAILABLE_WITH_DEGRADATION),
        (TaskType.KNOWLEDGE_VERIFY, CapabilityStatus.AVAILABLE_WITH_DEGRADATION),
        (TaskType.PLANNING, CapabilityStatus.AVAILABLE_WITH_DEGRADATION),
        (TaskType.FREE_CHAT, CapabilityStatus.AVAILABLE),
    ],
)
def test_single_text_provider_executes_every_core_task(task, expected_status) -> None:
    coordinator, _registry, _adapters = build_runtime(registration())

    result = asyncio.run(coordinator.execute(request(task)))

    assert result.receipt.execution_status is ProviderExecutionStatus.SUCCEEDED
    assert result.receipt.capability_status is expected_status
    assert result.receipt.usage_metadata.real_provider_calls == 0


def test_single_provider_verification_has_explicit_quality_warning() -> None:
    coordinator, registry, _adapters = build_runtime(
        registration(structured_output=True)
    )
    canonical_id = registry.profiles[0].identity.canonical_id

    result = asyncio.run(
        coordinator.execute(
            request(
                TaskType.KNOWLEDGE_VERIFY,
                prior_provider_canonical_id=canonical_id,
            )
        )
    )

    assert result.receipt.same_provider_self_verification is True
    assert result.receipt.self_verification_warning
    assert "SAME_PROVIDER_SELF_VERIFICATION" in {
        item.code for item in result.receipt.degradations
    }


def test_missing_vision_is_optional_and_makes_no_adapter_call() -> None:
    coordinator, _registry, adapters = build_runtime(registration())

    result = asyncio.run(coordinator.execute(request(TaskType.VISION_DIAGRAM)))

    assert (
        result.receipt.execution_status
        is ProviderExecutionStatus.OPTIONAL_ENHANCEMENT_UNAVAILABLE
    )
    assert result.receipt.capability_status is CapabilityStatus.OPTIONAL_ENHANCEMENT_UNAVAILABLE
    assert sum(adapter.call_count for adapter in adapters.values()) == 0
    assert "CORE_BLOCKED" not in result.receipt.reason_code


def test_invalid_task_returns_structured_invalid_route_without_call() -> None:
    coordinator, _registry, adapters = build_runtime(registration())

    result = asyncio.run(coordinator.execute(request("not_a_registered_task")))

    assert result.receipt.execution_status is ProviderExecutionStatus.INVALID_ROUTE
    assert result.receipt.reason_code == "INVALID_TASK_REQUIREMENT"
    assert result.receipt.attempts == ()
    assert sum(adapter.call_count for adapter in adapters.values()) == 0


def test_unavailable_preferred_provider_falls_back_deterministically() -> None:
    coordinator, _registry, _adapters = build_runtime(registration())

    plan = coordinator.plan(
        request(TaskType.TEACHER, preferred_provider="not-configured")
    )

    assert plan.valid is True
    assert "PREFERRED_PROVIDER_FALLBACK" in {
        item.code for item in plan.degradations
    }


def test_temp_unavailable_routed_profile_uses_healthy_runtime_fallback() -> None:
    coordinator, _registry, adapters = build_runtime(
        registration(base_url="https://primary.example/v1", model="primary"),
        registration(base_url="https://fallback.example/v1", model="fallback"),
        health_by_model={"primary": ProviderHealthState.TEMP_UNAVAILABLE},
    )

    result = asyncio.run(
        coordinator.execute(
            request(TaskType.TEACHER, preferred_model="primary")
        )
    )

    assert result.receipt.execution_status is ProviderExecutionStatus.SUCCEEDED
    assert result.receipt.selected_provider_identity.model == "fallback"
    assert "RUNTIME_PROVIDER_FALLBACK" in {
        item.code for item in result.receipt.degradations
    }
    assert "RUNTIME_PROVIDER_FALLBACK" in {
        item.code for item in coordinator.plan(
            request(TaskType.TEACHER, preferred_model="primary")
        ).degradations
    }
    assert adapters["primary"].call_count == 0
    assert adapters["fallback"].call_count == 1


def test_runtime_fallback_preserves_routing_mode_when_possible() -> None:
    coordinator, _registry, _adapters = build_runtime(
        registration(base_url="https://primary.example/v1", model="primary"),
        registration(
            base_url="https://balanced.example/v1",
            model="balanced",
            routing_classes=(RoutingMode.BALANCED,),
        ),
        registration(
            base_url="https://quality.example/v1",
            model="quality",
            routing_classes=(RoutingMode.QUALITY,),
        ),
        health_by_model={"primary": ProviderHealthState.TEMP_UNAVAILABLE},
    )

    plan = coordinator.plan(
        request(
            TaskType.TEACHER,
            preferred_model="primary",
            routing_mode=RoutingMode.QUALITY,
        )
    )

    assert plan.selected_runtime_profile.identity.model == "quality"


def test_prompted_json_degradation_is_parsed_and_locally_validated() -> None:
    coordinator, _registry, _adapters = build_runtime(registration())

    result = asyncio.run(
        coordinator.execute(request(TaskType.KNOWLEDGE_EXTRACT))
    )

    assert result.structured_output == {
        "status": "fake",
        "transport": "prompted_json",
        "task_type": "knowledge_extract",
    }
    assert "PROMPTED_STRUCTURE_WITH_LOCAL_VALIDATION" in {
        item.code for item in result.receipt.degradations
    }
    assert "local_schema_validation" in result.receipt.required_local_preprocessing


def test_local_ocr_preprocessing_is_preserved_in_execution_receipt() -> None:
    coordinator, _registry, _adapters = build_runtime(registration())

    result = asyncio.run(
        coordinator.execute(request(TaskType.IMAGE_KNOWLEDGE_EXTRACT))
    )

    assert result.receipt.execution_status is ProviderExecutionStatus.SUCCEEDED
    assert "LOCAL_OCR_ONLY" in {
        item.code for item in result.receipt.degradations
    }
    assert "local_ocr" in result.receipt.required_local_preprocessing


def test_degraded_health_remains_executable_and_is_reported() -> None:
    coordinator, _registry, _adapters = build_runtime(
        registration(),
        health_by_model={"deepseek-chat": ProviderHealthState.DEGRADED},
    )

    result = asyncio.run(coordinator.execute(request(TaskType.TEACHER)))

    assert result.receipt.execution_status is ProviderExecutionStatus.SUCCEEDED
    assert result.receipt.runtime_health is ProviderHealthState.DEGRADED
    assert "RUNTIME_HEALTH_DEGRADED" in {
        item.code for item in result.receipt.degradations
    }


@pytest.mark.parametrize(
    "health",
    [ProviderHealthState.TEMP_UNAVAILABLE, ProviderHealthState.DISABLED],
)
def test_non_executable_health_returns_runtime_unavailable(health) -> None:
    coordinator, _registry, adapters = build_runtime(
        registration(),
        health_by_model={"deepseek-chat": health},
    )

    result = asyncio.run(coordinator.execute(request(TaskType.TEACHER)))

    assert result.receipt.execution_status is ProviderExecutionStatus.RUNTIME_UNAVAILABLE
    assert result.receipt.attempts == ()
    assert adapters["deepseek-chat"].call_count == 0


def test_retry_is_bounded_then_falls_back_once() -> None:
    retryable_failure = AdapterExecutionResult(
        success=False,
        error_code="TEMP_FAILURE",
        retryable=True,
        prompt_tokens=1,
    )
    coordinator, _registry, adapters = build_runtime(
        registration(base_url="https://primary.example/v1", model="primary"),
        registration(base_url="https://fallback.example/v1", model="fallback"),
        scripts_by_model={"primary": (retryable_failure,)},
        attempts_by_model={"primary": 2, "fallback": 2},
    )

    result = asyncio.run(
        coordinator.execute(
            request(
                TaskType.TEACHER,
                preferred_model="primary",
                max_total_attempts=3,
            )
        )
    )

    assert result.receipt.execution_status is ProviderExecutionStatus.SUCCEEDED
    assert result.receipt.usage_metadata.attempt_count == 3
    assert adapters["primary"].call_count == 2
    assert adapters["fallback"].call_count == 1
    assert "EXECUTION_PROVIDER_FALLBACK" in {
        item.code for item in result.receipt.degradations
    }


def test_total_attempt_bound_is_never_exceeded() -> None:
    retryable_failure = AdapterExecutionResult(
        success=False,
        error_code="TEMP_FAILURE",
        retryable=True,
    )
    coordinator, _registry, adapters = build_runtime(
        registration(base_url="https://one.example/v1", model="one"),
        registration(base_url="https://two.example/v1", model="two"),
        scripts_by_model={
            "one": (retryable_failure,),
            "two": (retryable_failure,),
        },
        attempts_by_model={"one": 3, "two": 3},
    )

    result = asyncio.run(
        coordinator.execute(request(TaskType.TEACHER, max_total_attempts=3))
    )

    assert result.receipt.execution_status is ProviderExecutionStatus.FAILED
    assert result.receipt.usage_metadata.attempt_count == 3
    assert sum(adapter.call_count for adapter in adapters.values()) == 3


def test_failed_receipt_names_last_provider_actually_attempted() -> None:
    retryable_failure = AdapterExecutionResult(
        success=False,
        error_code="TEMP_FAILURE",
        retryable=True,
    )
    coordinator, _registry, adapters = build_runtime(
        registration(base_url="https://primary.example/v1", model="primary"),
        registration(base_url="https://fallback.example/v1", model="fallback"),
        scripts_by_model={"primary": (retryable_failure,)},
    )

    result = asyncio.run(
        coordinator.execute(
            request(
                TaskType.TEACHER,
                preferred_model="primary",
                max_total_attempts=1,
            )
        )
    )

    assert result.receipt.execution_status is ProviderExecutionStatus.FAILED
    assert result.receipt.selected_provider_identity.model == "primary"
    assert adapters["primary"].call_count == 1
    assert adapters["fallback"].call_count == 0


def test_execution_plan_is_deterministic_across_registration_order() -> None:
    alpha = registration(base_url="https://alpha.example/v1", model="alpha")
    beta = registration(base_url="https://beta.example/v1", model="beta")
    first, _registry, _adapters = build_runtime(beta, alpha)
    second, _registry2, _adapters2 = build_runtime(alpha, beta)

    first_plan = first.plan(request(TaskType.FREE_CHAT)).to_public_dict()
    second_plan = second.plan(request(TaskType.FREE_CHAT)).to_public_dict()

    assert first_plan == second_plan


def test_usage_and_cost_metadata_are_forwarded_without_price_calculation() -> None:
    coordinator, _registry, _adapters = build_runtime(registration())

    receipt = asyncio.run(
        coordinator.execute(request(TaskType.TEACHER))
    ).receipt
    payload = receipt.to_public_dict()

    assert str(payload["cost_lookup_key"]).startswith("provider_cost_lookup_v1:")
    assert payload["usage_metadata"]["fake_provider_calls"] == 1
    assert payload["usage_metadata"]["real_provider_calls"] == 0
    assert not ({"price", "pricing", "estimated_cost_usd"} & payload.keys())
