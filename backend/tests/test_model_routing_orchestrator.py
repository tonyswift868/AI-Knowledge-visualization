from __future__ import annotations

import json

import pytest

from app.model_routing import (
    BaseURLClass,
    Capability,
    CapabilityStatus,
    ProviderCapabilityProfile,
    ProviderOrchestrator,
    ProviderProfileRegistration,
    ProviderProfileRegistry,
    ProviderProfileSource,
    RoutingPreference,
    TaskCapabilityRequirement,
    TaskType,
)


def registration(
    *,
    adapter: str = "openai_compatible",
    base_url: str = "https://gateway.example/v1",
    model: str = "deepseek-chat",
    source: ProviderProfileSource = ProviderProfileSource.USER_DECLARED,
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
        source=source,
    )


def orchestrator(*registrations: ProviderProfileRegistration) -> ProviderOrchestrator:
    return ProviderOrchestrator(ProviderProfileRegistry(registrations))


def test_unknown_openai_compatible_model_is_not_misidentified_as_deepseek() -> None:
    entry = ProviderProfileRegistry([registration()]).entries[0]

    assert entry.identity.adapter_identity == "openai_compatible"
    assert entry.identity.base_url_class is BaseURLClass.CUSTOM_REMOTE
    assert "deepseek" not in entry.identity.base_url_class.value


def test_official_identity_comes_from_endpoint_not_model_name() -> None:
    entry = ProviderProfileRegistry(
        [registration(base_url="https://api.deepseek.com/v1", model="generic-model")]
    ).entries[0]

    assert entry.identity.adapter_identity == "openai_compatible"
    assert entry.identity.base_url_class is BaseURLClass.OFFICIAL_DEEPSEEK


def test_same_model_on_different_endpoint_has_different_canonical_identity() -> None:
    registry = ProviderProfileRegistry(
        [
            registration(base_url="https://one.example/v1", model="shared-model"),
            registration(base_url="https://two.example/v1", model="shared-model"),
        ]
    )

    first, second = registry.entries
    assert first.identity.canonical_id != second.identity.canonical_id
    assert first.identity.endpoint_scope != second.identity.endpoint_scope


def test_capability_profile_and_source_are_identity_dimensions() -> None:
    base = registration()
    structured = registration(structured_output=True)
    catalog = registration(source=ProviderProfileSource.TRUSTED_CATALOG)

    identities = {
        ProviderProfileRegistry([item]).entries[0].identity.canonical_id
        for item in (base, structured, catalog)
    }
    assert len(identities) == 3


def test_registry_is_deterministic_and_deduplicates_identical_registration() -> None:
    alpha = registration(base_url="https://alpha.example/v1", model="alpha")
    beta = registration(base_url="https://beta.example/v1", model="beta")
    first = ProviderProfileRegistry([beta, alpha, alpha])
    second = ProviderProfileRegistry([alpha, beta])

    assert len(first) == 2
    assert first.entries == second.entries
    assert first.revision == second.revision


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        (TaskType.TEACHER, CapabilityStatus.AVAILABLE),
        (TaskType.KNOWLEDGE_EXTRACT, CapabilityStatus.AVAILABLE_WITH_DEGRADATION),
        (TaskType.KNOWLEDGE_VERIFY, CapabilityStatus.AVAILABLE_WITH_DEGRADATION),
        (TaskType.PLANNING, CapabilityStatus.AVAILABLE_WITH_DEGRADATION),
        (TaskType.FREE_CHAT, CapabilityStatus.AVAILABLE),
    ],
)
def test_single_deepseek_like_text_provider_keeps_core_available(task, expected) -> None:
    receipt = orchestrator(registration()).route(task)

    assert receipt.available is True
    assert receipt.capability_status is expected
    assert receipt.selected_provider == "openai_compatible"


def test_single_provider_verification_has_self_verification_metadata() -> None:
    runtime = orchestrator(registration(structured_output=True))
    extraction = runtime.route(TaskType.KNOWLEDGE_EXTRACT)
    verification = runtime.route(
        TaskType.KNOWLEDGE_VERIFY,
        prior_receipt=extraction,
    )

    assert verification.available is True
    assert verification.same_provider_self_verification is True
    assert verification.self_verification_warning
    assert "SAME_PROVIDER_SELF_VERIFICATION" in {
        item.code for item in verification.degradations
    }


def test_optional_non_text_provider_does_not_hide_single_text_self_verification() -> None:
    runtime = orchestrator(
        registration(structured_output=True),
        registration(
            adapter="vision_adapter",
            base_url="https://vision.example/v1",
            model="vision-only",
            text_generation=False,
            vision=True,
        ),
    )

    verification = runtime.route(TaskType.KNOWLEDGE_VERIFY)

    assert verification.self_verification_warning
    assert "SAME_PROVIDER_SELF_VERIFICATION" in {
        item.code for item in verification.degradations
    }


def test_missing_vision_is_optional_and_never_core_blocked() -> None:
    runtime = orchestrator(registration())

    optional = runtime.route(TaskType.VISION_DIAGRAM)
    teacher = runtime.route(TaskType.TEACHER)
    assert optional.capability_status is CapabilityStatus.OPTIONAL_ENHANCEMENT_UNAVAILABLE
    assert teacher.available is True
    assert "CORE_BLOCKED" not in json.dumps(optional.to_public_dict())


def test_unavailable_preferred_provider_falls_back_with_degradation() -> None:
    receipt = orchestrator(registration()).route(
        TaskType.TEACHER,
        preference=RoutingPreference(preferred_provider="not-configured"),
    )

    assert receipt.available is True
    assert receipt.capability_status is CapabilityStatus.AVAILABLE_WITH_DEGRADATION
    assert "PREFERRED_PROVIDER_FALLBACK" in {
        item.code for item in receipt.degradations
    }


def test_missing_structured_output_and_local_preprocessing_are_auditable() -> None:
    runtime = orchestrator(registration())
    extract = runtime.route(TaskType.KNOWLEDGE_EXTRACT)
    image = runtime.route(TaskType.IMAGE_KNOWLEDGE_EXTRACT)

    assert "PROMPTED_STRUCTURE_WITH_LOCAL_VALIDATION" in {
        item.code for item in extract.degradations
    }
    assert "local_schema_validation" in extract.required_local_preprocessing
    assert "LOCAL_OCR_ONLY" in {item.code for item in image.degradations}
    assert "local_ocr" in image.required_local_preprocessing


def test_independent_canonical_provider_is_preferred_for_verification() -> None:
    runtime = orchestrator(
        registration(
            base_url="https://extract.example/v1",
            model="extract",
            structured_output=True,
        ),
        registration(
            base_url="https://verify.example/v1",
            model="verify",
            structured_output=True,
        ),
    )
    extraction = runtime.route(
        TaskType.KNOWLEDGE_EXTRACT,
        preference=RoutingPreference(preferred_model="extract"),
    )
    verification = runtime.route(
        TaskType.KNOWLEDGE_VERIFY,
        prior_route_identity=extraction.route_identity,
    )

    assert verification.selected_model == "verify"
    assert verification.same_provider_self_verification is False
    assert verification.self_verification_warning is None
    assert verification.capability_status is CapabilityStatus.AVAILABLE


def test_same_endpoint_with_different_model_is_still_self_verification() -> None:
    runtime = orchestrator(
        registration(
            base_url="https://same.example/v1",
            model="extract",
            structured_output=True,
        ),
        registration(
            base_url="https://same.example/v1",
            model="verify",
            structured_output=True,
            source=ProviderProfileSource.TRUSTED_CATALOG,
        ),
    )
    extraction = runtime.route(
        TaskType.KNOWLEDGE_EXTRACT,
        preference=RoutingPreference(preferred_model="extract"),
    )
    verification = runtime.route(
        TaskType.KNOWLEDGE_VERIFY,
        preference=RoutingPreference(preferred_model="verify"),
        prior_receipt=extraction,
    )

    assert verification.selected_model == "verify"
    assert verification.same_provider_self_verification is True
    assert verification.self_verification_warning
    assert "SAME_PROVIDER_SELF_VERIFICATION" in {
        item.code for item in verification.degradations
    }


def test_route_receipt_is_deterministic_and_contains_audit_identity() -> None:
    alpha = registration(base_url="https://alpha.example/v1", model="alpha")
    beta = registration(base_url="https://beta.example/v1", model="beta")
    first = orchestrator(beta, alpha).route(TaskType.FREE_CHAT)
    second = orchestrator(alpha, beta).route(TaskType.FREE_CHAT)

    assert first.to_public_dict() == second.to_public_dict()
    assert first.route_identity.startswith("route_")
    assert first.registry_revision.startswith("registry_")
    assert first.selected_provider_identity is not None


def test_route_identity_includes_task_requirement_contract() -> None:
    runtime = orchestrator(registration(structured_output=True))
    text_requirement = TaskCapabilityRequirement(
        task_type="custom",
        required_capabilities=frozenset({Capability.TEXT_GENERATION}),
    )
    structured_requirement = TaskCapabilityRequirement(
        task_type="custom",
        required_capabilities=frozenset(
            {Capability.TEXT_GENERATION, Capability.STRUCTURED_OUTPUT}
        ),
    )

    assert (
        runtime.route(text_requirement).route_identity
        != runtime.route(structured_requirement).route_identity
    )


def test_identity_and_receipt_never_expose_url_query_or_secret_fields() -> None:
    runtime = orchestrator(
        registration(
            base_url="https://private.gateway.example/v1?api_key=top-secret-value"
        )
    )
    payload = {
        "registry": runtime.registry.to_public_dict(),
        "receipt": runtime.route(TaskType.TEACHER).to_public_dict(),
    }
    serialized = json.dumps(payload, sort_keys=True).casefold()

    for forbidden in (
        "api_key",
        "top-secret-value",
        "private.gateway.example",
        "credential",
    ):
        assert forbidden not in serialized


def test_orchestration_makes_no_provider_call(monkeypatch) -> None:
    from app import provider_service

    called = 0

    def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called += 1
        raise AssertionError("orchestration must not call a Provider")

    monkeypatch.setattr(provider_service, "get_provider", fail_if_called)
    receipt = orchestrator(registration()).route(TaskType.PLANNING)

    assert receipt.available is True
    assert called == 0


def test_receipt_outputs_cost_lookup_key_without_calculating_price() -> None:
    payload = orchestrator(registration()).route(TaskType.TEACHER).to_public_dict()

    assert str(payload["cost_lookup_key"]).startswith("provider_cost_lookup_v1:")
    assert not ({"price", "pricing", "estimated_cost_usd"} & payload.keys())


def test_task_routing_matrix_exposes_future_ui_diagnostics() -> None:
    matrix = orchestrator(registration()).diagnostics().to_public_dict()
    tasks = {row["task"]: row for row in matrix["tasks"]}

    assert tasks["teacher"]["primary_route"]
    assert tasks["knowledge_extract"]["degradation"]
    assert tasks["vision_diagram"]["primary_route"] is None
    assert "vision" in tasks["vision_diagram"]["optional_enhancements"]
    assert "vision" in tasks["vision_diagram"]["missing_capabilities"]
