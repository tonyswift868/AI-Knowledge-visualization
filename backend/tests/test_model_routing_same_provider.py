from __future__ import annotations

from app.model_routing import (
    CapabilityStatus,
    ProviderCapabilityProfile,
    TaskType,
    route_provider,
)


def test_different_models_on_same_provider_are_still_self_verification() -> None:
    profiles = [
        ProviderCapabilityProfile(
            provider="openai_compatible",
            model="extract-model",
            text_generation=True,
            structured_output=True,
        ),
        ProviderCapabilityProfile(
            provider="openai-compatible",
            model="verify-model",
            text_generation=True,
            structured_output=True,
        ),
    ]

    decision = route_provider(
        profiles,
        TaskType.KNOWLEDGE_VERIFY,
        prior_provider="openai_compatible",
        prior_model="extract-model",
    )

    assert decision.status is CapabilityStatus.AVAILABLE_WITH_DEGRADATION
    assert decision.quality_metadata.same_provider_self_verification is True
    assert decision.quality_metadata.independent_verification_available is False
