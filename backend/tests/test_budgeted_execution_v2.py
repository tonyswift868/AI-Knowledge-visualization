from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.cost.budget import BudgetDecision
from app.cost.contracts import LocalCacheSavingsBaseline
from app.cost.estimation import CostEstimate, EstimationConfidence
from app.cost.execution_v2 import (
    BudgetTraceEventType,
    BudgetedExecutionSession,
    BudgetedOperation,
    CostReceiptPort,
    CostReceiptRecord,
    ExecutionSessionState,
    ExecutionStop,
    OperationExecutionStatus,
    ProviderOperationOutcome,
    ProviderOperationPort,
    RouteMetadata,
    RouteMetadataPort,
)
from app.cost.ledger import ActualCostLedger
from app.cost.runtime_budget import CostAnomalyCode
from app.providers.pricing import (
    InputTokenPricingMode,
    ProviderPricingCatalog,
    ProviderPricingSnapshot,
)
from app.providers.telemetry import ProviderUsageReceipt


def _run(coroutine):
    return asyncio.run(coroutine)


def _estimate(
    minimum: str = "0.05",
    expected: str = "0.10",
    maximum: str = "0.20",
    *,
    pricing_version: str = "deepseek-v4-flash-0731-usd-2026-08-24",
) -> CostEstimate:
    return CostEstimate(
        minimum=Decimal(minimum),
        expected=Decimal(expected),
        maximum=Decimal(maximum),
        currency="USD",
        confidence=EstimationConfidence.MEDIUM,
        estimation_basis="budgeted operation fixture",
        expected_provider_calls=1,
        expected_input_tokens=1_000_000,
        expected_output_tokens=100_000,
        expected_cache_assumption="fixture cache assumption",
        pricing_version=pricing_version,
    )


def _usage_receipt(
    *,
    request_id: str,
    second: int = 0,
    provider: str = "openai_compatible",
    model: str = "deepseek-v4-flash",
    prompt_tokens: int | None = 1_000_000,
    completion_tokens: int | None = 100_000,
    cache_hit_tokens: int | None = 0,
    cache_miss_tokens: int | None = 1_000_000,
    provider_called: bool = True,
    local_result_cache_hit: bool | None = False,
    success: bool = True,
    hour: int = 12,
) -> ProviderUsageReceipt:
    total_tokens = (
        prompt_tokens + completion_tokens
        if prompt_tokens is not None and completion_tokens is not None
        else None
    )
    return ProviderUsageReceipt(
        provider=provider,
        model=model,
        task_type="budgeted-execution-v2-test",
        request_id=request_id,
        started_at=f"2026-08-25T{hour:02d}:00:{second:02d}+00:00",
        latency_ms=10 + second,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_cache_hit_tokens=cache_hit_tokens,
        prompt_cache_miss_tokens=cache_miss_tokens,
        cache_hit_ratio=None,
        local_result_cache_hit=local_result_cache_hit,
        estimated_cost_usd=None,
        pricing_version=None,
        success=success,
        error_class=None if success else "PROVIDER_TEST_FAILURE",
        prompt_version="budgeted-execution-v2-test-v1",
        provider_called=provider_called,
        local_cache_miss=False if local_result_cache_hit else True,
    )


def _operation(
    attempt_id: str,
    *,
    operation_id: str | None = None,
    estimate: CostEstimate | None = None,
    retry_of: str | None = None,
    local_cache: bool = False,
    cached_result: object | None = None,
    baseline: LocalCacheSavingsBaseline | None = None,
    payload: object = "fixture-payload",
) -> BudgetedOperation:
    return BudgetedOperation(
        operation_id=operation_id or "op-" + attempt_id,
        attempt_id=attempt_id,
        idempotency_key="operation-event-" + attempt_id,
        payload=payload,
        estimate=estimate,
        retry_of_attempt_id=retry_of,
        local_result_cache_hit=local_cache,
        cached_result=cached_result,
        local_cache_baseline=baseline,
    )


class StaticRoutePort:
    def __init__(
        self,
        *,
        provider: str = "openai_compatible",
        model: str = "deepseek-v4-flash",
        available: bool = True,
    ) -> None:
        self.provider = provider
        self.model = model
        self.available = available
        self.calls: list[str] = []

    async def metadata_for(self, operation: BudgetedOperation) -> RouteMetadata:
        self.calls.append(operation.attempt_id)
        return RouteMetadata(
            routing_available=self.available,
            selected_provider=self.provider if self.available else None,
            selected_model=self.model if self.available else None,
            reason="fixture route",
        )


class ScriptedProviderPort:
    def __init__(self, actions: dict[str, object]) -> None:
        self.actions = actions
        self.calls: list[str] = []

    async def execute(
        self,
        operation: BudgetedOperation,
    ) -> ProviderOperationOutcome:
        self.calls.append(operation.attempt_id)
        action = self.actions[operation.attempt_id]
        if isinstance(action, BaseException):
            raise action
        if callable(action):
            value = action(operation)
            if hasattr(value, "__await__"):
                value = await value
            return value
        return action  # type: ignore[return-value]


class MappingCostReceiptPort:
    def __init__(self, records: dict[str, CostReceiptRecord | None]) -> None:
        self.records = records
        self.calls: list[tuple[str, str | None]] = []

    def receipt_for(
        self,
        operation: BudgetedOperation,
        *,
        outcome: ProviderOperationOutcome | None,
        error: BaseException | None,
    ) -> CostReceiptRecord | None:
        self.calls.append(
            (operation.attempt_id, type(error).__name__ if error else None)
        )
        return self.records.get(operation.attempt_id)


class BlockingProviderPort:
    def __init__(self, outcomes: dict[str, ProviderOperationOutcome]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(
        self,
        operation: BudgetedOperation,
    ) -> ProviderOperationOutcome:
        self.calls.append(operation.attempt_id)
        self.started.set()
        await self.release.wait()
        return self.outcomes[operation.attempt_id]


def _session(
    *,
    provider_port,
    receipt_port,
    route_port=None,
    ledger: ActualCostLedger | None = None,
    job_estimate: CostEstimate | None = None,
    warning: str = "0.70",
    hard_stop: str = "0.90",
    user_max: str = "1.00",
    run_id: str = "run-v2",
) -> BudgetedExecutionSession:
    return BudgetedExecutionSession(
        session_id="session-" + run_id,
        run_id=run_id,
        job_start_estimate=job_estimate or _estimate(),
        currency="USD",
        user_max_budget=Decimal(user_max),
        warning_threshold=Decimal(warning),
        hard_stop_threshold=Decimal(hard_stop),
        provider_port=provider_port,
        route_port=route_port or StaticRoutePort(),
        cost_receipt_port=receipt_port,
        ledger=ledger,
    )


def test_job_start_stop_prevents_any_operation_port_call() -> None:
    async def scenario() -> None:
        provider = ScriptedProviderPort(
            {"attempt-stop": ProviderOperationOutcome("unused", True)}
        )
        route = StaticRoutePort()
        session = _session(
            provider_port=provider,
            route_port=route,
            receipt_port=MappingCostReceiptPort({}),
            job_estimate=_estimate("0.10", "0.50", "0.90"),
            warning="0.50",
            hard_stop="0.80",
            user_max="1.00",
            run_id="job-start-stop",
        )

        assert session.state == ExecutionSessionState.STOPPED
        with pytest.raises(ExecutionStop):
            await session.execute(
                _operation("attempt-stop", estimate=_estimate())
            )
        assert provider.calls == []
        assert route.calls == []

    _run(scenario())


def test_allow_execute_ingest_and_running_actual_trace() -> None:
    async def scenario() -> None:
        operation = _operation("attempt-allow", estimate=_estimate("0.1", "0.2", "0.3"))
        provider = ScriptedProviderPort(
            {operation.attempt_id: ProviderOperationOutcome("answer", True)}
        )
        receipt_port = MappingCostReceiptPort(
            {
                operation.attempt_id: CostReceiptRecord(
                    _usage_receipt(request_id="req-allow"),
                    idempotency_key="receipt-allow",
                )
            }
        )
        session = _session(
            provider_port=provider,
            receipt_port=receipt_port,
            run_id="allow",
        )

        result = await session.execute(operation)

        assert result.value == "answer"
        assert result.receipt.status == OperationExecutionStatus.SUCCEEDED
        assert result.receipt.budget_before.decision == BudgetDecision.ALLOW
        assert result.receipt.operation_actual_cost == Decimal("0.286")
        assert result.receipt.running_actual_cost == Decimal("0.286")
        assert result.receipt.ledger_accepted is True
        assert session.running_actual_cost == Decimal("0.286")
        events = session.trace.snapshot()
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert BudgetTraceEventType.OPERATION_STARTED in {
            event.event_type for event in events
        }
        assert "fixture-payload" not in str(events)

    _run(scenario())


def test_warning_transition_then_stop_blocks_next_chargeable_permission() -> None:
    async def scenario() -> None:
        first = _operation(
            "attempt-warn",
            estimate=_estimate("0.1", "0.2", "0.31"),
        )
        second = _operation(
            "attempt-stop-next",
            estimate=_estimate("0.1", "0.3", "0.4"),
        )
        provider = ScriptedProviderPort(
            {
                first.attempt_id: ProviderOperationOutcome("first", True),
                second.attempt_id: ProviderOperationOutcome("second", True),
            }
        )
        receipt_port = MappingCostReceiptPort(
            {
                first.attempt_id: CostReceiptRecord(
                    _usage_receipt(request_id="req-warn"),
                    idempotency_key="receipt-warn",
                )
            }
        )
        session = _session(
            provider_port=provider,
            receipt_port=receipt_port,
            job_estimate=_estimate("0.1", "0.2", "0.25"),
            warning="0.30",
            hard_stop="0.60",
            user_max="0.80",
            run_id="warn-stop",
        )

        first_result = await session.execute(first)
        assert first_result.receipt.budget_before.decision == BudgetDecision.WARN
        assert session.state == ExecutionSessionState.WARN

        with pytest.raises(ExecutionStop):
            await session.execute(second)
        assert session.state == ExecutionSessionState.STOPPED
        assert provider.calls == [first.attempt_id]

    _run(scenario())


def test_warning_boundary_is_inclusive_at_permission_time() -> None:
    async def scenario() -> None:
        operation = _operation(
            "attempt-warning-boundary",
            estimate=_estimate("0.1", "0.2", "0.30"),
        )
        provider = ScriptedProviderPort(
            {operation.attempt_id: ProviderOperationOutcome("warn", True)}
        )
        session = _session(
            provider_port=provider,
            receipt_port=MappingCostReceiptPort(
                {
                    operation.attempt_id: CostReceiptRecord(
                        _usage_receipt(
                            request_id="req-warning-boundary",
                            cache_hit_tokens=1_000_000,
                            cache_miss_tokens=0,
                        ),
                        idempotency_key="receipt-warning-boundary",
                    )
                }
            ),
            job_estimate=_estimate("0.1", "0.15", "0.20"),
            warning="0.30",
            hard_stop="0.60",
            user_max="0.80",
            run_id="warning-boundary",
        )

        result = await session.execute(operation)

        assert result.receipt.budget_before.projected_maximum == Decimal("0.30")
        assert result.receipt.budget_before.decision == BudgetDecision.WARN
        assert session.state == ExecutionSessionState.WARN

    _run(scenario())


def test_hard_stop_boundary_is_inclusive_and_never_calls_provider() -> None:
    async def scenario() -> None:
        operation = _operation(
            "attempt-hard-boundary",
            estimate=_estimate("0.1", "0.3", "0.60"),
        )
        provider = ScriptedProviderPort(
            {operation.attempt_id: ProviderOperationOutcome("forbidden", True)}
        )
        session = _session(
            provider_port=provider,
            receipt_port=MappingCostReceiptPort({}),
            job_estimate=_estimate("0.1", "0.15", "0.20"),
            warning="0.30",
            hard_stop="0.60",
            user_max="0.80",
            run_id="hard-boundary",
        )

        with pytest.raises(ExecutionStop) as stopped:
            await session.execute(operation)

        assert stopped.value.budget_receipt is not None
        assert stopped.value.budget_receipt.projected_maximum == Decimal("0.60")
        assert provider.calls == []
        assert session.state == ExecutionSessionState.STOPPED

    _run(scenario())


def test_unavailable_route_stops_before_provider_port() -> None:
    async def scenario() -> None:
        operation = _operation("attempt-route-stop", estimate=_estimate())
        provider = ScriptedProviderPort(
            {operation.attempt_id: ProviderOperationOutcome("forbidden", True)}
        )
        route = StaticRoutePort(available=False)
        session = _session(
            provider_port=provider,
            route_port=route,
            receipt_port=MappingCostReceiptPort({}),
            run_id="route-stop",
        )

        with pytest.raises(ExecutionStop):
            await session.execute(operation)

        assert route.calls == [operation.attempt_id]
        assert provider.calls == []
        assert session.state == ExecutionSessionState.STOPPED

    _run(scenario())


def test_actual_cost_exhausts_budget_mid_job_and_stop_is_sticky() -> None:
    async def scenario() -> None:
        first = _operation(
            "attempt-peak",
            estimate=_estimate("0.2", "0.4", "0.5"),
        )
        future = _operation("attempt-after-stop", estimate=_estimate())
        provider = ScriptedProviderPort(
            {
                first.attempt_id: ProviderOperationOutcome("peak", True),
                future.attempt_id: ProviderOperationOutcome("forbidden", True),
            }
        )
        receipt_port = MappingCostReceiptPort(
            {
                first.attempt_id: CostReceiptRecord(
                    _usage_receipt(request_id="req-peak", hour=1),
                    idempotency_key="receipt-peak",
                )
            }
        )
        route = StaticRoutePort()
        session = _session(
            provider_port=provider,
            route_port=route,
            receipt_port=receipt_port,
            job_estimate=_estimate("0.2", "0.4", "0.5"),
            warning="0.54",
            hard_stop="0.55",
            user_max="0.60",
            run_id="mid-job-stop",
        )

        result = await session.execute(first)
        assert result.receipt.operation_actual_cost == Decimal("0.572")
        assert result.receipt.budget_after.decision == BudgetDecision.STOP
        assert session.state == ExecutionSessionState.STOPPED

        with pytest.raises(ExecutionStop):
            await session.execute(future)
        await session.cancel(reason="late cancellation must not rewrite STOP")
        assert session.state == ExecutionSessionState.STOPPED
        assert provider.calls == [first.attempt_id]
        assert route.calls == [first.attempt_id]

    _run(scenario())


def test_local_cache_only_run_never_calls_provider_and_records_avoided_call() -> None:
    async def scenario() -> None:
        operation = _operation(
            "attempt-local",
            estimate=None,
            local_cache=True,
            cached_result={"cached": True},
            baseline=LocalCacheSavingsBaseline(
                amount=Decimal("0.286"),
                currency="USD",
                estimation_basis="equivalent Provider operation",
            ),
        )
        provider = ScriptedProviderPort({})
        receipt = _usage_receipt(
            request_id="req-local",
            prompt_tokens=None,
            completion_tokens=None,
            cache_hit_tokens=None,
            cache_miss_tokens=None,
            provider_called=False,
            local_result_cache_hit=True,
        )
        session = _session(
            provider_port=provider,
            receipt_port=MappingCostReceiptPort(
                {
                    operation.attempt_id: CostReceiptRecord(
                        receipt,
                        idempotency_key="receipt-local",
                    )
                }
            ),
            job_estimate=_estimate("0", "0", "0"),
            warning="0",
            hard_stop="0",
            user_max="0",
            run_id="local-only",
        )

        result = await session.execute(operation)
        snapshot = session.ledger.snapshot(run_id=session.run_id)

        assert result.value == {"cached": True}
        assert result.receipt.status == OperationExecutionStatus.LOCAL_CACHE_HIT
        assert result.receipt.provider_called is False
        assert provider.calls == []
        assert snapshot.provider_calls == 0
        assert snapshot.local_cache_avoided_calls == 1
        assert snapshot.actual_calculated_provider_cost == Decimal("0")
        assert snapshot.local_cache_savings == Decimal("0.286")

    _run(scenario())


def test_provider_cache_hit_tokens_and_cost_flow_through_ledger() -> None:
    async def scenario() -> None:
        operation = _operation("attempt-hit", estimate=_estimate("0.02", "0.06", "0.10"))
        provider = ScriptedProviderPort(
            {operation.attempt_id: ProviderOperationOutcome("hit", True)}
        )
        session = _session(
            provider_port=provider,
            receipt_port=MappingCostReceiptPort(
                {
                    operation.attempt_id: CostReceiptRecord(
                        _usage_receipt(
                            request_id="req-hit",
                            cache_hit_tokens=1_000_000,
                            cache_miss_tokens=0,
                        ),
                        idempotency_key="receipt-hit",
                    )
                }
            ),
            run_id="provider-hit",
        )

        result = await session.execute(operation)
        snapshot = session.ledger.snapshot(run_id=session.run_id)

        assert result.receipt.operation_actual_cost == Decimal("0.073")
        assert snapshot.reported_input_cache_hit_tokens == 1_000_000
        assert snapshot.reported_input_cache_miss_tokens == 0
        assert snapshot.provider_cache_savings == Decimal("0.213")

    _run(scenario())


def test_unknown_model_cost_never_becomes_zero_and_stops_session() -> None:
    async def scenario() -> None:
        operation = _operation("attempt-unknown", estimate=_estimate())
        provider = ScriptedProviderPort(
            {operation.attempt_id: ProviderOperationOutcome("unknown", True)}
        )
        session = _session(
            provider_port=provider,
            receipt_port=MappingCostReceiptPort(
                {
                    operation.attempt_id: CostReceiptRecord(
                        _usage_receipt(
                            request_id="req-unknown",
                            model="unknown-priced-model",
                        ),
                        idempotency_key="receipt-unknown",
                    )
                }
            ),
            run_id="unknown-model",
        )

        result = await session.execute(operation)
        snapshot = session.ledger.snapshot(run_id=session.run_id)

        assert result.receipt.unknown_cost is True
        assert result.receipt.operation_actual_cost is None
        assert result.receipt.running_actual_cost is None
        assert snapshot.actual_calculated_provider_cost is None
        assert snapshot.known_actual_cost_subtotal == Decimal("0")
        assert snapshot.unpriced_receipts == 1
        assert session.state == ExecutionSessionState.STOPPED

    _run(scenario())


def test_price_snapshot_switch_is_retained_across_operations() -> None:
    async def scenario() -> None:
        old = ProviderPricingSnapshot(
            provider="generic",
            model="versioned-model",
            currency="USD",
            input_cache_hit_per_1m=Decimal("0.10"),
            input_cache_miss_per_1m=Decimal("0.10"),
            output_per_1m=Decimal("0"),
            effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            source="https://provider.example/pricing",
            pricing_version="generic-v1",
            input_pricing_mode=InputTokenPricingMode.TOTAL_INPUT_AT_MISS_RATE,
        )
        new = replace(
            old,
            effective_from=datetime(2026, 6, 1, tzinfo=timezone.utc),
            retrieved_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            pricing_version="generic-v2",
            input_cache_hit_per_1m=Decimal("0.20"),
            input_cache_miss_per_1m=Decimal("0.20"),
        )
        ledger = ActualCostLedger(catalog=ProviderPricingCatalog((old, new)))
        first = _operation("attempt-version-1", estimate=_estimate("0.05", "0.1", "0.15"))
        second = _operation("attempt-version-2", estimate=_estimate("0.1", "0.2", "0.25"))
        provider = ScriptedProviderPort(
            {
                first.attempt_id: ProviderOperationOutcome("old", True),
                second.attempt_id: ProviderOperationOutcome("new", True),
            }
        )
        receipt_port = MappingCostReceiptPort(
            {
                first.attempt_id: CostReceiptRecord(
                    _usage_receipt(
                        request_id="req-version-1",
                        provider="generic",
                        model="versioned-model",
                        completion_tokens=0,
                        cache_hit_tokens=None,
                        cache_miss_tokens=None,
                    ),
                    idempotency_key="receipt-version-1",
                ),
                second.attempt_id: CostReceiptRecord(
                    _usage_receipt(
                        request_id="req-version-2",
                        second=1,
                        provider="generic",
                        model="versioned-model",
                        completion_tokens=0,
                        cache_hit_tokens=None,
                        cache_miss_tokens=None,
                    ),
                    idempotency_key="receipt-version-2",
                ),
            }
        )
        # Move the first receipt before the new snapshot and the second after it.
        receipt_port.records[first.attempt_id] = replace(
            receipt_port.records[first.attempt_id],
            receipt=replace(
                receipt_port.records[first.attempt_id].receipt,
                started_at="2026-03-01T12:00:00+00:00",
            ),
        )
        receipt_port.records[second.attempt_id] = replace(
            receipt_port.records[second.attempt_id],
            receipt=replace(
                receipt_port.records[second.attempt_id].receipt,
                started_at="2026-07-01T12:00:00+00:00",
            ),
        )
        session = _session(
            provider_port=provider,
            receipt_port=receipt_port,
            route_port=StaticRoutePort(provider="generic", model="versioned-model"),
            ledger=ledger,
            job_estimate=_estimate("0.1", "0.3", "0.5"),
            warning="1.0",
            hard_stop="1.5",
            user_max="2.0",
            run_id="price-switch",
        )

        await session.execute(first)
        await session.execute(second)
        snapshot = ledger.snapshot(run_id=session.run_id)

        assert snapshot.actual_calculated_provider_cost == Decimal("0.30")
        assert snapshot.pricing_versions == ("generic-v1", "generic-v2")

    _run(scenario())


def test_mixed_known_and_unknown_pricing_preserves_known_subtotal() -> None:
    async def scenario() -> None:
        known = _operation("attempt-known", estimate=_estimate())
        unknown = _operation("attempt-mixed-unknown", estimate=_estimate())
        provider = ScriptedProviderPort(
            {
                known.attempt_id: ProviderOperationOutcome("known", True),
                unknown.attempt_id: ProviderOperationOutcome("unknown", True),
            }
        )
        receipt_port = MappingCostReceiptPort(
            {
                known.attempt_id: CostReceiptRecord(
                    _usage_receipt(request_id="req-known"),
                    idempotency_key="receipt-known",
                ),
                unknown.attempt_id: CostReceiptRecord(
                    _usage_receipt(
                        request_id="req-mixed-unknown",
                        second=1,
                        model="unknown-priced-model",
                    ),
                    idempotency_key="receipt-mixed-unknown",
                ),
            }
        )
        session = _session(
            provider_port=provider,
            receipt_port=receipt_port,
            warning="1.0",
            hard_stop="1.5",
            user_max="2.0",
            run_id="mixed-pricing",
        )

        await session.execute(known)
        result = await session.execute(unknown)
        snapshot = session.ledger.snapshot(run_id=session.run_id)

        assert result.receipt.running_actual_cost is None
        assert snapshot.actual_calculated_provider_cost is None
        assert snapshot.known_actual_cost_subtotal == Decimal("0.286")
        assert snapshot.unknown_receipts == 1
        assert session.state == ExecutionSessionState.STOPPED

    _run(scenario())


def test_same_attempt_concurrent_execution_calls_provider_and_ledger_once() -> None:
    async def scenario() -> None:
        operation = _operation("attempt-concurrent", estimate=_estimate())
        provider = BlockingProviderPort(
            {operation.attempt_id: ProviderOperationOutcome("once", True)}
        )
        session = _session(
            provider_port=provider,
            receipt_port=MappingCostReceiptPort(
                {
                    operation.attempt_id: CostReceiptRecord(
                        _usage_receipt(request_id="req-concurrent"),
                        idempotency_key="receipt-concurrent",
                    )
                }
            ),
            run_id="concurrent-idempotency",
        )

        first = asyncio.create_task(session.execute(operation))
        await provider.started.wait()
        second = asyncio.create_task(session.execute(operation))
        await asyncio.sleep(0)
        provider.release.set()
        first_result, second_result = await asyncio.gather(first, second)
        snapshot = session.ledger.snapshot(run_id=session.run_id)

        assert provider.calls == [operation.attempt_id]
        assert snapshot.provider_calls == 1
        assert snapshot.receipt_count == 1
        assert first_result.value == second_result.value == "once"
        assert {first_result.receipt.idempotent_replay, second_result.receipt.idempotent_replay} == {
            False,
            True,
        }

    _run(scenario())


def test_completed_attempt_replay_does_not_reexecute_or_reingest() -> None:
    async def scenario() -> None:
        operation = _operation("attempt-replay", estimate=_estimate())
        provider = ScriptedProviderPort(
            {operation.attempt_id: ProviderOperationOutcome("once", True)}
        )
        session = _session(
            provider_port=provider,
            receipt_port=MappingCostReceiptPort(
                {
                    operation.attempt_id: CostReceiptRecord(
                        _usage_receipt(request_id="req-replay"),
                        idempotency_key="receipt-replay",
                    )
                }
            ),
            run_id="completed-replay",
        )

        first = await session.execute(operation)
        replay = await session.execute(operation)

        assert first.receipt.idempotent_replay is False
        assert replay.receipt.idempotent_replay is True
        assert provider.calls == [operation.attempt_id]
        assert session.ledger.snapshot(run_id=session.run_id).receipt_count == 1

    _run(scenario())


def test_concurrent_reservations_prevent_budget_oversubscription() -> None:
    async def scenario() -> None:
        first = _operation(
            "attempt-reservation-1",
            estimate=_estimate("0.1", "0.2", "0.35"),
        )
        second = _operation(
            "attempt-reservation-2",
            estimate=_estimate("0.1", "0.2", "0.35"),
        )
        provider = BlockingProviderPort(
            {
                first.attempt_id: ProviderOperationOutcome("first", True),
                second.attempt_id: ProviderOperationOutcome("second", True),
            }
        )
        receipt_port = MappingCostReceiptPort(
            {
                first.attempt_id: CostReceiptRecord(
                    _usage_receipt(request_id="req-reservation-1"),
                    idempotency_key="receipt-reservation-1",
                )
            }
        )
        session = _session(
            provider_port=provider,
            receipt_port=receipt_port,
            job_estimate=_estimate("0.1", "0.3", "0.5"),
            warning="0.50",
            hard_stop="0.60",
            user_max="0.80",
            run_id="concurrent-reservations",
        )

        first_task = asyncio.create_task(session.execute(first))
        await provider.started.wait()
        with pytest.raises(ExecutionStop):
            await session.execute(second)
        provider.release.set()
        await first_task

        assert provider.calls == [first.attempt_id]
        assert session.state == ExecutionSessionState.STOPPED

    _run(scenario())


def test_retry_lineage_charges_distinct_real_attempts_once_each() -> None:
    async def scenario() -> None:
        first = _operation(
            "attempt-retry-1",
            operation_id="op-retry",
            estimate=_estimate(),
        )
        retry = _operation(
            "attempt-retry-2",
            operation_id="op-retry",
            estimate=_estimate(),
            retry_of=first.attempt_id,
        )
        provider = ScriptedProviderPort(
            {
                first.attempt_id: RuntimeError("fixture Provider failure"),
                retry.attempt_id: ProviderOperationOutcome("retry-ok", True),
            }
        )
        receipt_port = MappingCostReceiptPort(
            {
                first.attempt_id: CostReceiptRecord(
                    _usage_receipt(
                        request_id="req-retry-1",
                        success=False,
                    ),
                    idempotency_key="receipt-retry-1",
                ),
                retry.attempt_id: CostReceiptRecord(
                    _usage_receipt(
                        request_id="req-retry-2",
                        second=1,
                    ),
                    idempotency_key="receipt-retry-2",
                ),
            }
        )
        session = _session(
            provider_port=provider,
            receipt_port=receipt_port,
            warning="1.0",
            hard_stop="1.5",
            user_max="2.0",
            run_id="retry-lineage",
        )

        failed = await session.execute(first)
        succeeded = await session.execute(retry)
        snapshot = session.ledger.snapshot(run_id=session.run_id)

        assert failed.receipt.status == OperationExecutionStatus.FAILED
        assert failed.receipt.operation_actual_cost == Decimal("0.286")
        assert failed.receipt.unknown_cost is False
        assert succeeded.receipt.status == OperationExecutionStatus.SUCCEEDED
        assert succeeded.receipt.retry_of_attempt_id == first.attempt_id
        assert provider.calls == [first.attempt_id, retry.attempt_id]
        assert snapshot.provider_calls == 2
        assert snapshot.receipt_count == 2
        assert snapshot.actual_calculated_provider_cost == Decimal("0.572")
        assert any(
            event.retry_of_attempt_id == first.attempt_id
            for event in session.trace.snapshot()
        )

    _run(scenario())


def test_cancelled_chargeable_operation_without_receipt_stops_future_work() -> None:
    async def scenario() -> None:
        operation = _operation("attempt-cancel", estimate=_estimate())
        provider = BlockingProviderPort(
            {operation.attempt_id: ProviderOperationOutcome("late", True)}
        )
        route = StaticRoutePort()
        session = _session(
            provider_port=provider,
            route_port=route,
            receipt_port=MappingCostReceiptPort({operation.attempt_id: None}),
            run_id="cancel-operation",
        )

        task = asyncio.create_task(session.execute(operation))
        await provider.started.wait()
        cancelled_receipt = await session.cancel_operation(operation.attempt_id)
        result = await task

        assert cancelled_receipt is not None
        assert result.receipt.status == OperationExecutionStatus.CANCELLED
        assert result.receipt.unknown_cost is True
        assert result.receipt.provider_called is True
        assert session.state == ExecutionSessionState.STOPPED
        with pytest.raises(ExecutionStop):
            await session.execute(
                _operation("attempt-after-cancel", estimate=_estimate())
            )
        assert route.calls == [operation.attempt_id]

    _run(scenario())


def test_cancelled_session_never_grants_new_permission() -> None:
    async def scenario() -> None:
        provider = ScriptedProviderPort(
            {"attempt-after-session-cancel": ProviderOperationOutcome("forbidden", True)}
        )
        route = StaticRoutePort()
        session = _session(
            provider_port=provider,
            route_port=route,
            receipt_port=MappingCostReceiptPort({}),
            run_id="session-cancel",
        )

        await session.cancel(reason="user cancelled job")

        assert session.state == ExecutionSessionState.CANCELLED
        with pytest.raises(ExecutionStop):
            await session.execute(
                _operation("attempt-after-session-cancel", estimate=_estimate())
            )
        assert route.calls == []
        assert provider.calls == []

    _run(scenario())


def test_failed_provider_with_usage_receipt_is_charged_not_unknown() -> None:
    async def scenario() -> None:
        operation = _operation("attempt-failed-usage", estimate=_estimate("0.1", "0.2", "0.3"))
        provider = ScriptedProviderPort(
            {operation.attempt_id: RuntimeError("fixture failure after usage")}
        )
        session = _session(
            provider_port=provider,
            receipt_port=MappingCostReceiptPort(
                {
                    operation.attempt_id: CostReceiptRecord(
                        _usage_receipt(
                            request_id="req-failed-usage",
                            success=False,
                        ),
                        idempotency_key="receipt-failed-usage",
                    )
                }
            ),
            run_id="failed-with-usage",
        )

        result = await session.execute(operation)

        assert result.receipt.status == OperationExecutionStatus.FAILED
        assert result.receipt.provider_called is True
        assert result.receipt.operation_actual_cost == Decimal("0.286")
        assert result.receipt.running_actual_cost == Decimal("0.286")
        assert result.receipt.unknown_cost is False
        assert result.receipt.error_class == "RuntimeError"

    _run(scenario())


def test_actual_above_estimated_high_adds_diagnostic_without_double_projection() -> None:
    async def scenario() -> None:
        operation = _operation(
            "attempt-anomaly",
            estimate=_estimate("0.05", "0.10", "0.20"),
        )
        provider = ScriptedProviderPort(
            {operation.attempt_id: ProviderOperationOutcome("answer", True)}
        )
        session = _session(
            provider_port=provider,
            receipt_port=MappingCostReceiptPort(
                {
                    operation.attempt_id: CostReceiptRecord(
                        _usage_receipt(request_id="req-anomaly"),
                        idempotency_key="receipt-anomaly",
                    )
                }
            ),
            run_id="actual-anomaly",
        )

        result = await session.execute(operation)

        anomaly = next(
            item
            for item in result.receipt.anomalies
            if item.code == CostAnomalyCode.ACTUAL_EXCEEDS_EXPECTED_HIGH
        )
        assert anomaly.observed == Decimal("0.286")
        assert anomaly.expected == Decimal("0.20")
        assert anomaly.diagnostics_only is True
        assert result.receipt.budget_after.projected_maximum == Decimal("0.286")

    _run(scenario())


def test_ports_are_structural_and_do_not_require_w2_08_implementation() -> None:
    route = StaticRoutePort()
    provider = ScriptedProviderPort({})
    receipts = MappingCostReceiptPort({})

    assert isinstance(route, RouteMetadataPort)
    assert isinstance(provider, ProviderOperationPort)
    assert isinstance(receipts, CostReceiptPort)
