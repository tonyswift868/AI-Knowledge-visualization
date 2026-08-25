from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

from app.cost.cache_observatory import (
    CONTROL_CENTER_INTEGRATION_MANIFEST,
    ControlCenterDeliveryEvidence,
    build_demo_control_center_fixture,
    evaluate_control_center_delivery_fence,
)


def _evidence() -> ControlCenterDeliveryEvidence:
    return ControlCenterDeliveryEvidence(
        read_service_verified=True,
        cache_layer_count=6,
        combined_hit_percent=None,
        budget_execution_authority=False,
        restart_verified=True,
        secret_scan_passed=True,
        demo_marked=True,
        demo_real_provider_calls=0,
        frontend_mapping_verified=True,
    )


def test_demo_fixture_is_deterministic_explicit_and_secret_free() -> None:
    generated_at = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    first = build_demo_control_center_fixture(
        generated_at=generated_at
    ).to_public_dict()
    second = build_demo_control_center_fixture(
        generated_at=generated_at
    ).to_public_dict()

    assert first == second
    assert first["mode"] == "DEMO"
    assert first["demo_metadata"]["real_provider_calls"] == 0
    assert first["cache"]["combined_cache_hit_percent"] is None
    assert len(first["cache"]["layers"]) == 6
    assert first["budget"]["execution_authority"] is False
    serialized = json.dumps(first, ensure_ascii=False).casefold()
    for forbidden in ("api_key", "raw_prompt", "authorization", "secret_headers"):
        assert forbidden not in serialized


def test_delivery_fence_accepts_only_complete_safe_evidence() -> None:
    ready = evaluate_control_center_delivery_fence(_evidence())
    assert ready.ready
    assert ready.failures == ()
    assert ready.automatic_execution_policy_change is False

    unsafe = evaluate_control_center_delivery_fence(
        replace(
            _evidence(),
            cache_layer_count=5,
            combined_hit_percent="71.2",
            budget_execution_authority=True,
            demo_real_provider_calls=1,
        )
    )
    assert not unsafe.ready
    assert set(unsafe.failures) == {
        "CACHE_LAYER_COUNT_NOT_SIX",
        "CACHE_HIT_RATES_MERGED",
        "BUDGET_UI_HAS_EXECUTION_AUTHORITY",
        "DEMO_PROVIDER_CALL_DETECTED",
    }


def test_integration_manifest_keeps_application_boundaries_explicit() -> None:
    manifest = CONTROL_CENTER_INTEGRATION_MANIFEST.to_public_dict()
    assert manifest["api_endpoint"] == "/api/cost-cache-control-center"
    assert manifest["frontend_tool_route"] == "?tool=cost-cache"
    assert manifest["live_requires_explicit_databases"] is True
    assert manifest["demo_requires_explicit_query"] is True
    assert manifest["demo_real_provider_calls"] == 0
    assert manifest["budget_execution_authority"] is False
    assert manifest["combined_cache_hit_rate"] is False
    assert manifest["exposed_secret_fields"] == []
