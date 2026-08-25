from __future__ import annotations

from dataclasses import replace

import pytest
from app.cost.cache_observatory import (
    DEFAULT_CACHE_IDENTITY_POLICY,
    CacheIdentity,
    CacheIdentityPolicyViolation,
    CacheInvalidationPlanner,
    CacheInvalidationReason,
    CacheLayer,
    CacheObservatoryError,
)


def _identity(layer: CacheLayer) -> CacheIdentity:
    return CacheIdentity(
        layer=layer,
        namespace="identity-test",
        artifact_id="artifact-1",
        provider="openai_compatible",
        model="deepseek-v4-flash",
        prompt_version="prompt-v1",
        route_identity="route-a",
        stable_prefix_hash="stable-a",
        dynamic_context_hash="learner-a",
        source_hash="source-a",
        evidence_hash="evidence-a",
        question_hash="question-a",
        question_bank_version="question-bank-v1",
        parser_version="parser-v1",
        evaluation_version="evaluation-v1",
        identity_policy_version=DEFAULT_CACHE_IDENTITY_POLICY.version,
        expires_at_ms=1_000,
    )


def test_formal_identity_policy_defines_versioned_inputs_per_cache_layer() -> None:
    policy = DEFAULT_CACHE_IDENTITY_POLICY
    assert policy.version == "cache-identity-policy-v1"
    assert policy.for_layer(CacheLayer.PROVIDER_PROMPT_CACHE).required_fields == (
        "provider",
        "model",
        "prompt_version",
        "route_identity",
        "stable_prefix_hash",
    )
    assert policy.for_layer(CacheLayer.EVIDENCE_CACHE).required_fields == (
        "source_hash",
        "evidence_hash",
    )
    assert policy.for_layer(CacheLayer.PARSE_CACHE).required_fields == (
        "source_hash",
        "evidence_hash",
        "parser_version",
    )
    assert policy.for_layer(CacheLayer.QUESTION_BANK_CACHE).required_fields == (
        "question_hash",
        "question_bank_version",
    )
    assert policy.for_layer(CacheLayer.EVALUATION_ARTIFACT_CACHE).required_fields == (
        "question_hash",
        "dynamic_context_hash",
        "evidence_hash",
        "evaluation_version",
    )


def test_provider_prefix_key_reuses_dynamic_context_but_changes_on_stable_versions() -> (
    None
):
    policy = DEFAULT_CACHE_IDENTITY_POLICY
    identity = _identity(CacheLayer.PROVIDER_PROMPT_CACHE)
    key = policy.cache_key(identity)
    assert (
        policy.cache_key(
            replace(
                identity,
                dynamic_context_hash="learner-b",
                evidence_hash="evidence-b",
                question_hash="question-b",
            )
        )
        == key
    )
    for changed in (
        replace(identity, provider="different-provider"),
        replace(identity, model="different-model"),
        replace(identity, prompt_version="prompt-v2"),
        replace(identity, route_identity="route-b"),
        replace(identity, stable_prefix_hash="stable-b"),
    ):
        assert policy.cache_key(changed) != key


@pytest.mark.parametrize("layer", tuple(CacheLayer))
def test_policy_rejects_unversioned_or_missing_layer_identity(
    layer: CacheLayer,
) -> None:
    policy = DEFAULT_CACHE_IDENTITY_POLICY
    identity = _identity(layer)
    assert policy.validate(identity).valid
    with pytest.raises(CacheIdentityPolicyViolation):
        policy.cache_key(replace(identity, identity_policy_version=None))
    missing_field = policy.for_layer(layer).required_fields[0]
    with pytest.raises(CacheIdentityPolicyViolation):
        policy.cache_key(replace(identity, **{missing_field: None}))


def test_semantic_evaluation_identity_requires_provider_versions_conditionally() -> (
    None
):
    policy = DEFAULT_CACHE_IDENTITY_POLICY
    local = replace(
        _identity(CacheLayer.EVALUATION_ARTIFACT_CACHE),
        provider=None,
        model=None,
        prompt_version=None,
        route_identity=None,
    )
    assert policy.validate(local).valid
    with pytest.raises(CacheIdentityPolicyViolation):
        policy.require(replace(local, provider="openai_compatible", model=None))


def test_invalidation_decision_reports_every_targeted_reason_without_clear_all() -> (
    None
):
    cached = _identity(CacheLayer.EVALUATION_ARTIFACT_CACHE)
    requested = replace(
        cached,
        source_hash="source-b",
        prompt_version="prompt-v2",
        model="model-b",
        route_identity="route-b",
        evidence_hash="evidence-b",
        expires_at_ms=2_000,
    )
    decision = CacheInvalidationPlanner().decide(
        cached,
        requested,
        observed_at_ms=1_000,
        manual_invalidate=True,
    )
    assert decision.invalidate
    assert decision.reasons == (
        CacheInvalidationReason.SOURCE_CHANGED,
        CacheInvalidationReason.PROMPT_CHANGED,
        CacheInvalidationReason.MODEL_CHANGED,
        CacheInvalidationReason.ROUTE_CHANGED,
        CacheInvalidationReason.EVIDENCE_CHANGED,
        CacheInvalidationReason.TTL,
        CacheInvalidationReason.MANUAL_INVALIDATE,
    )
    assert decision.layer == CacheLayer.EVALUATION_ARTIFACT_CACHE.value
    assert decision.namespace == cached.namespace
    assert decision.artifact_id == cached.artifact_id
    assert decision.target_identity_key
    assert decision.replacement_identity_key
    assert not decision.clear_all
    assert decision.to_public_dict()["scope"]["target_identity_key"]


def test_invalidation_noop_and_cross_artifact_comparison_are_explicit() -> None:
    cached = _identity(CacheLayer.PARSE_CACHE)
    planner = CacheInvalidationPlanner()
    decision = planner.decide(cached, cached, observed_at_ms=999)
    assert not decision.invalidate
    assert decision.reasons == ()
    with pytest.raises(CacheObservatoryError):
        planner.decide(cached, replace(cached, artifact_id="other-artifact"))
