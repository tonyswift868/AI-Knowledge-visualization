from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.cost.cache_observatory import (
    CacheEvent,
    CacheIdentity,
    CacheLayer,
    CacheMetricsStore,
    CacheMissReason,
    CostCacheReadService,
    ReviewAction,
    ReviewActionCostTrace,
    TeacherCostMode,
    TeacherTurnCostTrace,
    cache_miss_message,
)
from app.cost.durable_execution import SQLiteExecutionStore
from app.cost.estimation import CostEstimate, EstimationConfidence
from app.cost.execution_v2 import (
    BudgetedOperation,
    OperationExecutionStatus,
    RouteMetadata,
)
from app.providers.telemetry import ProviderUsageReceipt


def _estimate() -> CostEstimate:
    return CostEstimate(
        minimum=Decimal("0.01"),
        expected=Decimal("0.02"),
        maximum=Decimal("0.05"),
        currency="USD",
        confidence=EstimationConfidence.MEDIUM,
        estimation_basis="control center fixture",
        expected_provider_calls=1,
        expected_input_tokens=1_000_000,
        expected_output_tokens=100_000,
        expected_cache_assumption="fixture",
        pricing_version="deepseek-v4-flash-0731-usd-2026-08-24",
    )


def _create_session(store: SQLiteExecutionStore, session_id: str = "session-1") -> None:
    store.create_session(
        session_id=session_id,
        run_id="run-1",
        currency="USD",
        user_max_budget=Decimal("2.00"),
        warning_threshold=Decimal("1.20"),
        hard_stop_threshold=Decimal("1.80"),
        job_start_estimate=_estimate(),
    )


def _account_provider_call(
    store: SQLiteExecutionStore,
    *,
    session_id: str = "session-1",
    suffix: str = "1",
    model: str = "deepseek-v4-flash",
) -> None:
    operation = BudgetedOperation(
        operation_id=f"operation-{suffix}",
        attempt_id=f"attempt-{suffix}",
        idempotency_key=f"idempotency-{suffix}",
        payload="not persisted",
        estimate=_estimate(),
    )
    permission = store.authorize_operation(
        session_id=session_id,
        operation=operation,
        route=RouteMetadata(True, "openai_compatible", model),
    )
    assert permission.allowed
    assert store.claim_provider_start(
        session_id=session_id, attempt_id=operation.attempt_id
    ).claimed
    receipt = ProviderUsageReceipt(
        provider="openai_compatible",
        model=model,
        task_type="control-center-test",
        request_id=f"request-{suffix}",
        started_at=datetime.now(timezone.utc).isoformat(),
        latency_ms=20,
        prompt_tokens=1_000_000,
        completion_tokens=100_000,
        total_tokens=1_100_000,
        prompt_cache_hit_tokens=700_000,
        prompt_cache_miss_tokens=300_000,
        cache_hit_ratio=0.7,
        local_result_cache_hit=False,
        estimated_cost_usd=None,
        pricing_version=None,
        success=True,
        error_class=None,
        prompt_version="fixture-v1",
        provider_called=True,
        local_cache_miss=True,
    )
    store.stage_receipt(
        receipt_id=f"receipt-{suffix}",
        session_id=session_id,
        attempt_id=operation.attempt_id,
        receipt=receipt,
        execution_status=OperationExecutionStatus.SUCCEEDED,
    )
    store.commit_receipt(
        receipt_id=f"receipt-{suffix}",
        execution_status=OperationExecutionStatus.SUCCEEDED,
    )


def _identity(layer: CacheLayer, suffix: str) -> CacheIdentity:
    return CacheIdentity(
        layer=layer,
        namespace="control-center-test",
        artifact_id=f"artifact-{suffix}",
        provider="openai_compatible",
        model="deepseek-v4-flash",
        prompt_version="teacher-v1",
        route_identity="route-a",
        stable_prefix_hash="stable-a",
        dynamic_context_hash="learner-a",
        source_hash="source-a",
        evidence_hash="evidence-a",
        question_hash="question-a",
        question_bank_version="question-bank-v1",
        parser_version="parser-v1",
        evaluation_version="evaluation-v1",
        identity_policy_version="cache-identity-policy-v1",
    )


def _cache_event(
    layer: CacheLayer,
    suffix: str,
    *,
    hit: bool,
    miss_reason: CacheMissReason | None = None,
) -> CacheEvent:
    return CacheEvent(
        event_id=f"cache-{suffix}",
        trace_id=f"trace-{suffix}",
        identity=_identity(layer, suffix),
        hit=hit,
        miss_reason=miss_reason,
        bytes_reused=2_048 if hit else 0,
        tokens_reused=700 if hit else 0,
        avoided_provider_calls=(
            1 if hit and layer == CacheLayer.LOCAL_RESULT_CACHE else 0
        ),
        estimated_cost_avoided=Decimal("0.02") if hit else Decimal(0),
        currency="USD",
        lookup_ms=Decimal("1.5"),
        occurred_at_ms=100,
    )


def _populate_cache_profiles(store: CacheMetricsStore) -> None:
    events = [
        _cache_event(
            CacheLayer.PROVIDER_PROMPT_CACHE,
            "provider-hit",
            hit=True,
        ),
        _cache_event(
            CacheLayer.PROVIDER_PROMPT_CACHE,
            "provider-miss",
            hit=False,
            miss_reason=CacheMissReason.DYNAMIC_CONTEXT_CHANGED,
        ),
    ]
    events.extend(
        _cache_event(layer, layer.value.casefold(), hit=True)
        for layer in CacheLayer
        if layer != CacheLayer.PROVIDER_PROMPT_CACHE
    )
    store.extend(events)
    store.extend_teacher_traces(
        (
            TeacherTurnCostTrace(
                trace_id="teacher-fast",
                mode=TeacherCostMode.FAST,
                prompt_tokens=1_000,
                stable_prefix_tokens=700,
                dynamic_tokens=300,
                cached_input_tokens=700,
                output_tokens=200,
                provider_call_count=1,
                estimated_actual_cost=Decimal("0.12"),
                currency="USD",
                stable_prefix_estimated_cost=Decimal("0.01"),
                dynamic_input_estimated_cost=Decimal("0.03"),
                output_estimated_cost=Decimal("0.08"),
                ttft_ms=Decimal(90),
            ),
            TeacherTurnCostTrace(
                trace_id="teacher-deep",
                mode=TeacherCostMode.DEEP,
                prompt_tokens=2_000,
                stable_prefix_tokens=1_200,
                dynamic_tokens=800,
                cached_input_tokens=1_200,
                output_tokens=500,
                provider_call_count=1,
                estimated_actual_cost=None,
                currency=None,
                ttft_ms=Decimal(150),
            ),
        )
    )
    store.extend_review_traces(
        (
            ReviewActionCostTrace(
                trace_id="review-objective",
                evaluation_id="review-1",
                action=ReviewAction.OBJECTIVE_EVALUATION,
            ),
            ReviewActionCostTrace(
                trace_id="review-semantic",
                evaluation_id="review-2",
                action=ReviewAction.SEMANTIC_EVALUATION,
                provider_call_count=1,
                input_tokens=500,
                output_tokens=50,
                estimated_cost=Decimal("0.03"),
                currency="USD",
            ),
        )
    )


def test_read_model_separates_six_layers_and_projects_cost_breakdowns(
    tmp_path: Path,
) -> None:
    execution = SQLiteExecutionStore(tmp_path / "execution.db")
    cache = CacheMetricsStore(tmp_path / "cache.db")
    _create_session(execution)
    _account_provider_call(execution)
    _populate_cache_profiles(cache)

    before = (
        execution.list_sessions(),
        execution.list_operations("session-1"),
        execution.list_receipts("session-1"),
        cache.count(),
    )
    payload = (
        CostCacheReadService(
            execution_store=execution,
            cache_store=cache,
        )
        .read()
        .to_public_dict()
    )
    after = (
        execution.list_sessions(),
        execution.list_operations("session-1"),
        execution.list_receipts("session-1"),
        cache.count(),
    )

    assert payload["read_only"] is True
    assert len(payload["cache"]["layers"]) == 6
    assert payload["cache"]["combined_cache_hit_percent"] is None
    provider_layer = payload["cache"]["layers"][0]
    assert provider_layer["hit_percent"] == "50.0"
    assert provider_layer["miss_diagnostics"] == [
        {"message": "学习上下文发生变化", "count": 1}
    ]
    assert payload["cache"]["calls_avoided"] == 1
    assert payload["cache"]["savings_classification"] == "estimated"
    assert payload["current_session"]["provider_calls"] == 1
    assert payload["scopes"]["run"]["provider_calls"] == 1
    assert payload["scopes"]["today"]["provider_calls"] == 1
    assert payload["budget"]["execution_authority"] is False
    assert payload["budget"]["control_seam"] == "existing-budget-gate"
    assert payload["teacher"]["modes"][0]["turns"] == 1
    assert payload["teacher"]["modes"][0]["ttft"]["p50_ms"] == "90"
    assert payload["teacher"]["modes"][1]["cost_unknown_turns"] == 1
    objective = next(
        item
        for item in payload["review"]["actions"]
        if item["id"] == "objective-evaluation"
    )
    assert objective["observed_zero_provider"] is True
    assert objective["provider_free_by_design"] is True
    assert payload["review"]["zero_provider_evaluation_count"] == 1
    assert before == after

    serialized = json.dumps(payload, ensure_ascii=False).casefold()
    for forbidden in ("raw_prompt", "api_key", "secret_headers", "private_credentials"):
        assert forbidden not in serialized


def test_unknown_pricing_keeps_cost_and_remaining_budget_unknown(
    tmp_path: Path,
) -> None:
    execution = SQLiteExecutionStore(tmp_path / "execution.db")
    cache = CacheMetricsStore(tmp_path / "cache.db")
    _create_session(execution)
    _account_provider_call(execution, model="unknown-model")

    payload = (
        CostCacheReadService(
            execution_store=execution,
            cache_store=cache,
        )
        .read()
        .to_public_dict()
    )

    assert payload["current_session"]["cost"]["complete"] is False
    assert payload["current_session"]["cost"]["unknown_items"] == 1
    assert payload["budget"]["estimated_remaining_budget"] is None
    assert payload["budget"]["visual_status"] == "unknown"
    assert any(item["id"] == "unpriced-cost" for item in payload["recent_anomalies"])


def test_dashboard_metrics_survive_both_store_restarts(tmp_path: Path) -> None:
    execution_path = tmp_path / "execution.db"
    cache_path = tmp_path / "cache.db"
    execution = SQLiteExecutionStore(execution_path)
    cache = CacheMetricsStore(cache_path)
    _create_session(execution)
    _account_provider_call(execution)
    _populate_cache_profiles(cache)
    first = CostCacheReadService(execution_store=execution, cache_store=cache).read()
    execution.close()
    cache.close()

    restarted_execution = SQLiteExecutionStore(execution_path)
    restarted_cache = CacheMetricsStore(cache_path)
    second = CostCacheReadService(
        execution_store=restarted_execution,
        cache_store=restarted_cache,
    ).read()

    assert second.current_session == first.current_session
    assert second.scopes == first.scopes
    assert second.budget == first.budget
    assert second.cache == first.cache
    assert second.teacher == first.teacher
    assert second.review == first.review


def test_all_cache_miss_reasons_have_user_facing_messages() -> None:
    messages = {cache_miss_message(reason) for reason in CacheMissReason}
    assert {
        "学习上下文发生变化",
        "资料已更新",
        "模型发生变化",
        "首次请求",
        "该内容不适合缓存",
    } <= messages
    assert all(reason.value not in messages for reason in CacheMissReason)
