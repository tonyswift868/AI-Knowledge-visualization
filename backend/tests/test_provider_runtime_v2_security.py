from __future__ import annotations

import asyncio
import json

import pytest

from app.model_routing import (
    BaseURLClass,
    ProviderCapabilityProfile,
    ProviderOrchestrator,
    ProviderProfileRegistration,
    ProviderProfileRegistry,
    TaskType,
)
from app.provider.runtime_v2 import (
    FakeProviderAdapter,
    ProviderExecutionRequest,
    ProviderHealthState,
    ProviderRuntimeCoordinator,
    ProviderRuntimeProfile,
    ProviderRuntimeRegistry,
    ProviderSessionContext,
)


def registered(
    *,
    base_url: str,
    model: str,
) -> tuple[ProviderProfileRegistry, object]:
    provider_registry = ProviderProfileRegistry(
        [
            ProviderProfileRegistration(
                adapter_identity="openai_compatible",
                configured_base_url=base_url,
                profile=ProviderCapabilityProfile(
                    provider="openai_compatible",
                    model=model,
                    text_generation=True,
                ),
            )
        ]
    )
    return provider_registry, provider_registry.entries[0]


def test_runtime_registry_reuses_w2_canonical_identity() -> None:
    provider_registry, entry = registered(
        base_url="https://gateway.example/v1",
        model="deepseek-chat",
    )
    runtime = ProviderRuntimeRegistry.from_provider_registry(provider_registry)

    assert runtime.profiles[0].identity is entry.identity
    assert (
        runtime.profiles[0].identity.capability_profile_id
        == entry.identity.capability_profile_id
    )


def test_same_model_on_different_endpoints_remains_distinct_at_runtime() -> None:
    registrations = [
        ProviderProfileRegistration(
            adapter_identity="openai_compatible",
            configured_base_url=base_url,
            profile=ProviderCapabilityProfile(
                provider="openai_compatible",
                model="shared-model",
                text_generation=True,
            ),
        )
        for base_url in (
            "https://one.example/v1",
            "https://two.example/v1",
        )
    ]
    provider_registry = ProviderProfileRegistry(registrations)
    runtime = ProviderRuntimeRegistry.from_provider_registry(provider_registry)

    assert len(runtime.profiles) == 2
    assert len({item.identity.canonical_id for item in runtime.profiles}) == 2
    assert len({item.identity.provider_scope_id for item in runtime.profiles}) == 2


def test_unknown_compatible_endpoint_stays_sanitized_and_not_official_deepseek() -> None:
    provider_registry, _entry = registered(
        base_url="https://unknown.gateway.example/v1",
        model="deepseek-chat",
    )
    payload = ProviderRuntimeRegistry.from_provider_registry(
        provider_registry
    ).to_public_dict()
    serialized = json.dumps(payload, sort_keys=True).casefold()

    assert payload["profiles"][0]["identity"]["base_url_class"] == (
        BaseURLClass.CUSTOM_REMOTE.value
    )
    assert "unknown.gateway.example" not in serialized
    assert "official_deepseek" not in serialized


def test_url_query_key_payload_and_receipt_never_serialize_secrets() -> None:
    provider_registry, _entry = registered(
        base_url="https://private.gateway.example/v1?api_key=top-secret-key",
        model="deepseek-chat",
    )
    runtime = ProviderRuntimeRegistry.from_provider_registry(provider_registry)
    coordinator = ProviderRuntimeCoordinator(
        orchestrator=ProviderOrchestrator(provider_registry),
        runtime_registry=runtime,
    )
    result = asyncio.run(
        coordinator.execute(
            ProviderExecutionRequest(
                task_requirement=TaskType.TEACHER,
                session=ProviderSessionContext("session-safe", "request-safe"),
                payload="credential=payload-secret api_key=payload-key",
            )
        )
    )
    serialized = json.dumps(
        {
            "registry": runtime.to_public_dict(),
            "receipt": result.receipt.to_public_dict(),
        },
        sort_keys=True,
    ).casefold()

    for forbidden in (
        "api_key",
        "top-secret-key",
        "payload-secret",
        "payload-key",
        "private.gateway.example",
        "secret_headers",
    ):
        assert forbidden not in serialized


def test_runtime_profile_has_no_raw_secret_constructor_fields() -> None:
    provider_registry, entry = registered(
        base_url="https://gateway.example/v1",
        model="deepseek-chat",
    )

    with pytest.raises(TypeError):
        ProviderRuntimeProfile(
            identity=entry.identity,
            capability_profile=entry.profile,
            adapter_port="fake:port",
            api_key="forbidden",  # type: ignore[call-arg]
        )


def test_registry_rejects_non_fake_adapter_port() -> None:
    _provider_registry, entry = registered(
        base_url="https://gateway.example/v1",
        model="deepseek-chat",
    )
    profile = ProviderRuntimeProfile.from_registered_profile(
        entry,
        adapter_port="real:port",
    )

    class RealAdapter:
        port_name = "real:port"
        is_fake = False

    with pytest.raises(ValueError, match="Fake adapter"):
        ProviderRuntimeRegistry().register(profile, RealAdapter())  # type: ignore[arg-type]


def test_fake_execution_never_touches_legacy_provider_factory(monkeypatch) -> None:
    from app import provider_service

    provider_registry, _entry = registered(
        base_url="https://gateway.example/v1",
        model="deepseek-chat",
    )
    runtime = ProviderRuntimeRegistry.from_provider_registry(provider_registry)
    coordinator = ProviderRuntimeCoordinator(
        orchestrator=ProviderOrchestrator(provider_registry),
        runtime_registry=runtime,
    )
    called = 0

    def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called += 1
        raise AssertionError("legacy Provider factory must not be used")

    monkeypatch.setattr(provider_service, "get_provider", fail_if_called)
    result = asyncio.run(
        coordinator.execute(
            ProviderExecutionRequest(
                task_requirement=TaskType.FREE_CHAT,
                session=ProviderSessionContext("session-safe", "request-safe"),
            )
        )
    )

    assert result.receipt.usage_metadata.real_provider_calls == 0
    assert result.receipt.usage_metadata.fake_provider_calls == 1
    assert called == 0


def test_health_reason_is_bounded_code_not_arbitrary_secret_text() -> None:
    provider_registry, entry = registered(
        base_url="https://gateway.example/v1",
        model="deepseek-chat",
    )
    runtime = ProviderRuntimeRegistry.from_provider_registry(provider_registry)

    profile = runtime.update_health(
        entry.identity.canonical_id,
        ProviderHealthState.DEGRADED,
        reason_code="api_key=should-not-serialize",
    )

    assert profile.health_reason_code == "RUNTIME_HEALTH_CHANGED"
    assert "api_key" not in json.dumps(runtime.to_public_dict()).casefold()
