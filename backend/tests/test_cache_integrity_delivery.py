from __future__ import annotations

from dataclasses import replace

from app.cost.cache_observatory import (
    DELIVERY_ID,
    CacheRuntimeCapability,
    CacheRuntimeDeliveryEvidence,
    evaluate_delivery_fence,
    run_cache_integrity_benchmark,
)


def test_100k_event_integrity_benchmark_and_delivery_fence(tmp_path) -> None:
    result = run_cache_integrity_benchmark(tmp_path / "cache-integrity-100k.db")
    assert result.event_count == 100_000
    assert result.accepted_event_count == 100_000
    assert result.persisted_event_count_after_restart == 100_000
    assert result.duplicate_replay_idempotent
    assert result.conflicting_event_rejected
    assert result.out_of_order_preserved
    assert result.integrity_ok
    assert result.journal_mode == "WAL"
    assert result.checkpoint_busy == 0
    assert result.elapsed_ms > 0
    assert result.events_per_second > 0
    assert result.database_bytes > 0
    assert result.real_provider_calls == 0
    assert "not a real Provider hit rate" in result.disclaimer

    product = result.product_metrics
    for hit_percent in (
        product.provider_cache_hit_percent,
        product.local_result_hit_percent,
        product.evidence_hit_percent,
        product.parse_hit_percent,
        product.question_hit_percent,
        product.evaluation_artifact_hit_percent,
    ):
        assert hit_percent is not None
        assert 74 < hit_percent < 76
    assert product.cost_saved_estimate is not None
    assert product.calls_avoided > 0
    assert product.to_public_dict()["combined_cache_hit_percent"] is None

    evidence = CacheRuntimeDeliveryEvidence(
        verified_capabilities=tuple(CacheRuntimeCapability),
        integrity_benchmark=result,
    )
    receipt = evaluate_delivery_fence(evidence)
    assert receipt.delivery_id == DELIVERY_ID
    assert receipt.ready
    assert receipt.failures == ()
    assert not receipt.automatic_execution_policy_change

    incomplete = evaluate_delivery_fence(
        replace(evidence, verified_capabilities=(CacheRuntimeCapability.DURABLE_STORE,))
    )
    assert not incomplete.ready
    assert any(item.startswith("MISSING_CAPABILITY:") for item in incomplete.failures)
