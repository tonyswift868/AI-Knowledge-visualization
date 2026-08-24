from __future__ import annotations

import json

import pytest

from app.model_routing import (
    Capability,
    CapabilityRouter,
    CapabilityStatus,
    ProviderCapabilityProfile,
    RoutingMode,
    RoutingPreference,
    TaskCapabilityRequirement,
    TaskType,
    route_provider,
)


def text_profile(
    provider: str = "openai_compatible",
    model: str = "deepseek-chat",
    **overrides: object,
) -> ProviderCapabilityProfile:
    values: dict[str, object] = {
        "provider": provider,
        "model": model,
        "text_generation": True,
    }
    values.update(overrides)
    return ProviderCapabilityProfile(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "task_type",
    [
        TaskType.TEACHER,
        TaskType.KNOWLEDGE_EXTRACT,
        TaskType.FREE_CHAT,
        TaskType.PLANNING,
        TaskType.ASSESSMENT,
        TaskType.CONFLICT_ANALYSIS,
    ],
)
def test_one_text_provider_keeps_every_core_text_task_available(task_type) -> None:
    decision = route_provider([text_profile()], task_type)

    assert decision.available is True
    assert decision.selected_provider == "openai_compatible"
    assert decision.selected_model == "deepseek-chat"
    assert decision.status is not CapabilityStatus.UNSUPPORTED


def test_no_vision_does_not_block_text_core() -> None:
    profile = text_profile(vision=False)

    assert route_provider([profile], TaskType.TEACHER).available is True
    assert route_provider([profile], TaskType.PLANNING).available is True


def test_vision_only_task_is_optional_enhancement_when_vision_is_missing() -> None:
    decision = route_provider([text_profile()], TaskType.VISION_DIAGRAM)

    assert decision.status is CapabilityStatus.OPTIONAL_ENHANCEMENT_UNAVAILABLE
    assert decision.selected_provider is None
    assert decision.missing_capabilities == (Capability.VISION,)


def test_two_providers_select_the_one_satisfying_required_capabilities() -> None:
    text_only = text_profile("deepseek", "text-model")
    multimodal = text_profile("compatible", "vision-model", vision=True)

    decision = route_provider(
        [text_only, multimodal],
        TaskType.VISION_DIAGRAM,
    )

    assert decision.status is CapabilityStatus.AVAILABLE
    assert decision.selected_model == "vision-model"


def test_preferred_provider_missing_capability_falls_back() -> None:
    profiles = [
        text_profile("preferred", "text-model"),
        text_profile("fallback", "vision-model", vision=True),
    ]

    decision = route_provider(
        profiles,
        TaskType.VISION_DIAGRAM,
        preference=RoutingPreference(preferred_provider="preferred"),
    )

    assert decision.selected_provider == "fallback"
    assert decision.status is CapabilityStatus.AVAILABLE_WITH_DEGRADATION
    assert "PREFERRED_PROVIDER_FALLBACK" in {
        degradation.code for degradation in decision.degradations
    }


def test_no_provider_satisfying_core_text_is_unsupported() -> None:
    vision_only = ProviderCapabilityProfile(
        provider="image_service",
        model="image-only",
        vision=True,
    )

    decision = route_provider([vision_only], TaskType.TEACHER)

    assert decision.status is CapabilityStatus.UNSUPPORTED
    assert decision.selected_provider is None
    assert decision.missing_capabilities == (Capability.TEXT_GENERATION,)


def test_same_provider_extract_and_verify_is_allowed_and_quality_is_explicit() -> None:
    profile = text_profile(structured_output=True)
    extraction = route_provider([profile], TaskType.KNOWLEDGE_EXTRACT)
    verification = route_provider(
        [profile],
        TaskType.KNOWLEDGE_VERIFY,
        prior_provider=extraction.selected_provider,
        prior_model=extraction.selected_model,
    )

    assert extraction.status is CapabilityStatus.AVAILABLE
    assert verification.status is CapabilityStatus.AVAILABLE_WITH_DEGRADATION
    assert verification.available is True
    assert verification.quality_metadata.same_provider_self_verification is True
    assert "SAME_PROVIDER_SELF_VERIFICATION" in {
        degradation.code for degradation in verification.degradations
    }


def test_degradation_reports_structured_output_fallback_and_pipeline() -> None:
    decision = route_provider([text_profile()], TaskType.KNOWLEDGE_EXTRACT)

    assert decision.status is CapabilityStatus.AVAILABLE_WITH_DEGRADATION
    assert decision.missing_capabilities == (Capability.STRUCTURED_OUTPUT,)
    assert decision.degradations[0].code == "PROMPTED_STRUCTURE_WITH_LOCAL_VALIDATION"
    assert decision.preprocessing == ("prompted_json", "local_schema_validation")


def test_user_provider_preference_is_respected_when_capable() -> None:
    profiles = [
        text_profile("alpha", "model-a", structured_output=True),
        text_profile("beta", "model-b", structured_output=True),
    ]

    decision = route_provider(
        profiles,
        TaskType.PLANNING,
        preference=RoutingPreference(preferred_provider="beta"),
    )

    assert decision.status is CapabilityStatus.AVAILABLE
    assert decision.selected_provider == "beta"


def test_routing_mode_is_respected_without_price_calculation() -> None:
    profiles = [
        text_profile(
            "alpha",
            "balanced-model",
            routing_classes=(RoutingMode.BALANCED,),
        ),
        text_profile(
            "beta",
            "quality-model",
            routing_classes=(RoutingMode.QUALITY,),
        ),
    ]

    decision = route_provider(
        profiles,
        TaskType.FREE_CHAT,
        preference=RoutingPreference(mode=RoutingMode.QUALITY),
    )

    assert decision.selected_model == "quality-model"
    assert decision.routing_class is RoutingMode.QUALITY


def test_routing_is_deterministic_even_if_input_order_changes() -> None:
    alpha = text_profile("alpha", "same-class", structured_output=True)
    beta = text_profile("beta", "same-class", structured_output=True)
    router = CapabilityRouter()

    first = router.route([beta, alpha], TaskType.PLANNING)
    second = router.route([alpha, beta], TaskType.PLANNING)

    assert first == second
    assert first.selected_provider == "alpha"


def test_public_result_contains_no_api_key_or_credential_fields() -> None:
    payload = route_provider([text_profile()], TaskType.TEACHER).to_public_dict()
    serialized = json.dumps(payload, sort_keys=True).casefold()

    assert "api_key" not in serialized
    assert "credential" not in serialized
    assert "secret" not in serialized


def test_routing_does_not_invoke_provider_factory(monkeypatch) -> None:
    from app import provider_service

    called = 0

    def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called += 1
        raise AssertionError("routing must not create or call a Provider")

    monkeypatch.setattr(provider_service, "get_provider", fail_if_called)

    decision = route_provider([text_profile()], TaskType.TEACHER)

    assert decision.available is True
    assert called == 0


def test_router_public_contract_has_no_pricing_fields() -> None:
    payload = route_provider([text_profile()], TaskType.TEACHER).to_public_dict()

    assert not ({"price", "pricing", "estimated_cost_usd"} & payload.keys())


def test_ocr_only_path_keeps_image_knowledge_extraction_available() -> None:
    decision = route_provider([text_profile()], TaskType.IMAGE_KNOWLEDGE_EXTRACT)

    assert decision.available is True
    assert "LOCAL_OCR_ONLY" in {
        degradation.code for degradation in decision.degradations
    }
    assert "local_ocr" in decision.preprocessing


def test_local_text_adapters_keep_pdf_subtitle_and_code_tasks_available() -> None:
    profile = text_profile(structured_output=True)

    expected = {
        TaskType.PDF_KNOWLEDGE_EXTRACT: "local_pdf_parser_or_ocr",
        TaskType.SUBTITLED_VIDEO_ANALYSIS: "subtitle_to_text",
        TaskType.CODE_ANALYSIS: "local_code_parser",
    }
    for task_type, preprocessing in expected.items():
        decision = route_provider([profile], task_type)
        assert decision.status is CapabilityStatus.AVAILABLE
        assert preprocessing in decision.preprocessing


def test_independent_provider_is_preferred_for_verification_when_available() -> None:
    extract_model = text_profile("alpha", "extract", structured_output=True)
    verify_model = text_profile("beta", "verify", structured_output=True)

    decision = route_provider(
        [extract_model, verify_model],
        TaskType.KNOWLEDGE_VERIFY,
        prior_provider="alpha",
        prior_model="extract",
    )

    assert decision.selected_provider == "beta"
    assert decision.status is CapabilityStatus.AVAILABLE
    assert decision.quality_metadata.independent_verification_available is True


def test_custom_requirement_can_require_context_window() -> None:
    requirement = TaskCapabilityRequirement(
        task_type="large_context_task",
        required_capabilities=frozenset({Capability.TEXT_GENERATION}),
        minimum_context_window=64_000,
    )
    profiles = [
        text_profile("small", "small", context_window=32_000),
        text_profile("large", "large", context_window=128_000),
    ]

    decision = route_provider(profiles, requirement)

    assert decision.selected_provider == "large"
    assert Capability.CONTEXT_WINDOW not in decision.missing_capabilities


def test_telemetry_metadata_stays_inside_existing_integration_boundary() -> None:
    decision = route_provider([text_profile()], TaskType.FREE_CHAT)

    assert decision.telemetry_metadata() == {
        "task_type": "free_chat",
        "selected_model": "deepseek-chat",
        "routing_class": "balanced",
        "capability_status": "AVAILABLE",
    }
