from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from app.cost.budget import BudgetDecision
from app.cost.contracts import LocalCacheSavingsBaseline
from app.cost.durable_execution import (
    DurableExecutionRuntime,
    DurableIdentityConflict,
    ExecutionSchemaMissing,
    ExecutionSessionRecord,
    ExecutionStoreClosed,
    ExecutionStoreCorruption,
    OperationState,
    ProviderRuntimeV2DurablePorts,
    ReceiptAccountingState,
    RecoveryOutcome,
    SQLiteExecutionStore,
)
from app.cost.estimation import CostEstimate, EstimationConfidence
from app.cost.execution_v2 import (
    BudgetedOperation,
    CostReceiptRecord,
    ExecutionSessionState,
    OperationExecutionStatus,
    ProviderOperationOutcome,
    RouteMetadata,
)
from app.cost.runtime_budget import UnknownCostPolicy
from app.providers.telemetry import ProviderUsageReceipt


class SimulatedCrash(RuntimeError):
    pass


class CrashOnce:
    def __init__(self, target: str) -> None:
        self.target = target
        self.triggered = False

    def __call__(self, phase: str) -> None:
        if phase == self.target and not self.triggered:
            self.triggered = True
            raise SimulatedCrash(phase)


class StaticRoutePort:
    def __init__(
        self,
        *,
        available: bool = True,
        provider: str = "openai_compatible",
        model: str = "deepseek-v4-flash",
    ) -> None:
        self.available = available
        self.provider = provider
        self.model = model
        self.calls: list[str] = []

    async def metadata_for(self, operation: BudgetedOperation) -> RouteMetadata:
        self.calls.append(operation.attempt_id)
        return RouteMetadata(
            routing_available=self.available,
            selected_provider=self.provider if self.available else None,
            selected_model=self.model if self.available else None,
            reason="durable fixture route",
        )


class ScriptedProviderPort:
    def __init__(self, actions: dict[str, object] | None = None) -> None:
        self.actions = actions or {}
        self.calls: list[str] = []

    async def execute(self, operation: BudgetedOperation) -> ProviderOperationOutcome:
        self.calls.append(operation.attempt_id)
        action = self.actions.get(
            operation.attempt_id,
            ProviderOperationOutcome(
                value="provider-result-" + operation.attempt_id,
                provider_called=True,
            ),
        )
        if isinstance(action, BaseException):
            raise action
        return action  # type: ignore[return-value]


class MappingReceiptPort:
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


def _estimate(
    minimum: str = "0.01",
    expected: str = "0.02",
    maximum: str = "0.05",
) -> CostEstimate:
    return CostEstimate(
        minimum=Decimal(minimum),
        expected=Decimal(expected),
        maximum=Decimal(maximum),
        currency="USD",
        confidence=EstimationConfidence.MEDIUM,
        estimation_basis="durable execution test estimate",
        expected_provider_calls=1,
        expected_input_tokens=1_000_000,
        expected_output_tokens=100_000,
        expected_cache_assumption="fixture assumption",
        pricing_version="deepseek-v4-flash-0731-usd-2026-08-24",
    )


def _operation(
    attempt_id: str,
    *,
    operation_id: str | None = None,
    retry_of: str | None = None,
    local_cache: bool = False,
    baseline: LocalCacheSavingsBaseline | None = None,
    estimate: CostEstimate | None = None,
    payload: object = "fixture-payload",
) -> BudgetedOperation:
    return BudgetedOperation(
        operation_id=operation_id or "op-" + attempt_id,
        attempt_id=attempt_id,
        idempotency_key="idem-" + attempt_id,
        payload=payload,
        estimate=estimate or _estimate(),
        retry_of_attempt_id=retry_of,
        local_result_cache_hit=local_cache,
        cached_result="local-result-" + attempt_id if local_cache else None,
        local_cache_baseline=baseline,
    )


def _usage(
    attempt_id: str,
    *,
    request_id: str | None = None,
    model: str = "deepseek-v4-flash",
    cache_hit_tokens: int | None = 0,
    cache_miss_tokens: int | None = 1_000_000,
    prompt_tokens: int | None = 1_000_000,
    completion_tokens: int | None = 100_000,
    provider_called: bool = True,
    local_cache_hit: bool | None = False,
    success: bool = True,
) -> ProviderUsageReceipt:
    total_tokens = (
        prompt_tokens + completion_tokens
        if prompt_tokens is not None and completion_tokens is not None
        else None
    )
    return ProviderUsageReceipt(
        provider="openai_compatible",
        model=model,
        task_type="durable-execution-test",
        request_id=request_id or "request-" + attempt_id,
        started_at="2026-08-25T12:00:00+00:00",
        latency_ms=25,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_cache_hit_tokens=cache_hit_tokens,
        prompt_cache_miss_tokens=cache_miss_tokens,
        cache_hit_ratio=None,
        local_result_cache_hit=local_cache_hit,
        estimated_cost_usd=None,
        pricing_version=None,
        success=success,
        error_class=None if success else "FIXTURE_PROVIDER_FAILURE",
        prompt_version="durable-v1",
        provider_called=provider_called,
        local_cache_miss=not local_cache_hit,
    )


def _receipt_record(
    attempt_id: str,
    *,
    receipt: ProviderUsageReceipt | None = None,
    baseline: LocalCacheSavingsBaseline | None = None,
) -> CostReceiptRecord:
    return CostReceiptRecord(
        receipt=receipt or _usage(attempt_id),
        idempotency_key="idem-" + attempt_id,
        local_cache_baseline=baseline,
    )


def _store(
    tmp_path: Path, name: str = "durable.sqlite3", **kwargs
) -> SQLiteExecutionStore:
    return SQLiteExecutionStore(tmp_path / name, **kwargs)


def _session(
    store: SQLiteExecutionStore,
    *,
    session_id: str = "session-1",
    run_id: str = "run-1",
    policy: UnknownCostPolicy = UnknownCostPolicy.STOP,
    maximum: str = "2.00",
    warning: str = "1.50",
    hard_stop: str = "1.80",
    estimate: CostEstimate | None = None,
) -> ExecutionSessionRecord:
    return store.create_session(
        session_id=session_id,
        run_id=run_id,
        currency="USD",
        user_max_budget=Decimal(maximum),
        warning_threshold=Decimal(warning),
        hard_stop_threshold=Decimal(hard_stop),
        job_start_estimate=estimate or _estimate(),
        unknown_cost_policy=policy,
    )


def _runtime(
    store: SQLiteExecutionStore,
    *,
    records: dict[str, CostReceiptRecord | None],
    provider: ScriptedProviderPort | None = None,
    crash_hook=None,
) -> tuple[DurableExecutionRuntime, ScriptedProviderPort]:
    provider_port = provider or ScriptedProviderPort()
    return (
        DurableExecutionRuntime(
            store=store,
            provider_port=provider_port,
            route_port=StaticRoutePort(),
            cost_receipt_port=MappingReceiptPort(records),
            crash_hook=crash_hook,
        ),
        provider_port,
    )


def _authorize_and_claim(
    store: SQLiteExecutionStore,
    operation: BudgetedOperation,
    *,
    session_id: str = "session-1",
) -> None:
    permission = store.authorize_operation(
        session_id=session_id,
        operation=operation,
        route=RouteMetadata(True, "openai_compatible", "deepseek-v4-flash"),
    )
    assert permission.allowed
    assert store.claim_provider_start(
        session_id=session_id, attempt_id=operation.attempt_id
    ).claimed


def test_sqlite_wal_integrity_checkpoint_missing_schema_and_clean_shutdown(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    assert store.database_path.is_absolute()
    assert store.journal_mode == "WAL"
    assert store.busy_timeout_ms == 5_000
    assert store.integrity_check().ok
    checkpoint = store.checkpoint("PASSIVE")
    assert checkpoint.mode == "PASSIVE"
    assert checkpoint.busy == 0
    store.close()
    store.close()
    with pytest.raises(ExecutionStoreClosed):
        store.get_session("session-1")

    missing_path = tmp_path / "missing.sqlite3"
    missing_path.touch()
    missing = SQLiteExecutionStore(missing_path, auto_bootstrap=False)
    with pytest.raises(ExecutionSchemaMissing):
        missing.get_session("session-1")
    missing.close(checkpoint=False)


def test_refuses_relative_memory_uri_and_network_share_paths() -> None:
    with pytest.raises(ValueError):
        SQLiteExecutionStore("relative.sqlite3")
    with pytest.raises(ValueError):
        SQLiteExecutionStore(":memory:")
    with pytest.raises(ValueError):
        SQLiteExecutionStore("file:E:/unsafe.sqlite3")
    with pytest.raises(ValueError):
        SQLiteExecutionStore(r"\\server\share\unsafe.sqlite3")


def test_session_bootstrap_is_idempotent_but_rejects_conflicting_identity(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = _session(store)
    store.transition_session(
        session_id=first.session_id,
        state=ExecutionSessionState.WARN,
        reason="fixture warning",
    )
    restored = _session(store)
    assert restored.state == ExecutionSessionState.WARN
    with pytest.raises(DurableIdentityConflict):
        _session(store, maximum="3.00", warning="1.50", hard_stop="1.80")


def test_successful_execution_is_accounted_once_and_payload_is_not_persisted(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    runtime, provider = _runtime(
        store, records={"attempt-1": _receipt_record("attempt-1")}
    )
    runtime.create_session(
        session_id="session-1",
        run_id="run-1",
        job_start_estimate=_estimate(),
        currency="USD",
        user_max_budget=Decimal(2),
        warning_threshold=Decimal("1.5"),
        hard_stop_threshold=Decimal("1.8"),
    )
    secret_payload = "sk-secret-should-never-be-in-sqlite"
    operation = _operation("attempt-1", payload=secret_payload)
    first = asyncio.run(runtime.execute(session_id="session-1", operation=operation))
    second = asyncio.run(runtime.execute(session_id="session-1", operation=operation))

    assert first.value == "provider-result-attempt-1"
    assert first.recovery.outcome == RecoveryOutcome.ALREADY_ACCOUNTED
    assert second.value is None
    assert second.recovery.outcome == RecoveryOutcome.ALREADY_ACCOUNTED
    assert provider.calls == ["attempt-1"]
    assert (
        store.list_receipts("session-1")[0].accounting_state
        == ReceiptAccountingState.ACCOUNTED
    )
    assert secret_payload.encode() not in store.database_path.read_bytes()
    assert b"provider-result-attempt-1" not in store.database_path.read_bytes()


def test_crash_before_authorization_is_retry_safe(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _session(store)
    decision = store.recover_operation(
        session_id="session-1",
        operation_id="op-before",
        attempt_id="attempt-before",
    )
    assert decision.outcome == RecoveryOutcome.RETRY_SAFE
    assert decision.reason == "OPERATION_NOT_AUTHORIZED"


def test_crash_after_authorization_before_provider_is_retry_safe(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _session(store)
    provider = ScriptedProviderPort()
    operation = _operation("attempt-authorized")
    crashing, _ = _runtime(
        store,
        records={operation.attempt_id: _receipt_record(operation.attempt_id)},
        provider=provider,
        crash_hook=CrashOnce("AFTER_AUTHORIZATION"),
    )
    with pytest.raises(SimulatedCrash):
        asyncio.run(crashing.execute(session_id="session-1", operation=operation))
    assert provider.calls == []
    assert (
        store.recover_operation(
            session_id="session-1", attempt_id=operation.attempt_id
        ).outcome
        == RecoveryOutcome.RETRY_SAFE
    )

    resumed, _ = _runtime(
        store,
        records={operation.attempt_id: _receipt_record(operation.attempt_id)},
        provider=provider,
    )
    result = asyncio.run(resumed.execute(session_id="session-1", operation=operation))
    assert result.recovery.outcome == RecoveryOutcome.ALREADY_ACCOUNTED
    assert provider.calls == [operation.attempt_id]


def test_provider_start_without_receipt_never_retries_chargeable_call(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _session(store)
    provider = ScriptedProviderPort()
    operation = _operation("attempt-started")
    crashing, _ = _runtime(
        store,
        records={operation.attempt_id: _receipt_record(operation.attempt_id)},
        provider=provider,
        crash_hook=CrashOnce("AFTER_PROVIDER_START"),
    )
    with pytest.raises(SimulatedCrash):
        asyncio.run(crashing.execute(session_id="session-1", operation=operation))
    decision = store.recover_operation(
        session_id="session-1", attempt_id=operation.attempt_id
    )
    assert decision.outcome == RecoveryOutcome.DO_NOT_RETRY_CHARGEABLE_CALL

    resumed, _ = _runtime(
        store,
        records={operation.attempt_id: _receipt_record(operation.attempt_id)},
        provider=provider,
    )
    result = asyncio.run(resumed.execute(session_id="session-1", operation=operation))
    assert result.recovery.outcome == RecoveryOutcome.DO_NOT_RETRY_CHARGEABLE_CALL
    assert provider.calls == []


def test_crash_after_receipt_before_ledger_commit_requires_replay_after_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "receipt-replay.sqlite3"
    store = SQLiteExecutionStore(database)
    _session(store)
    provider = ScriptedProviderPort()
    operation = _operation("attempt-staged")
    crashing, _ = _runtime(
        store,
        records={operation.attempt_id: _receipt_record(operation.attempt_id)},
        provider=provider,
        crash_hook=CrashOnce("AFTER_RECEIPT_STAGED"),
    )
    with pytest.raises(SimulatedCrash):
        asyncio.run(crashing.execute(session_id="session-1", operation=operation))
    decision = store.recover_operation(
        session_id="session-1", attempt_id=operation.attempt_id
    )
    assert decision.outcome == RecoveryOutcome.RECEIPT_REPLAY_REQUIRED
    receipt_id = decision.receipt_id
    assert receipt_id is not None
    store.close()

    restored_store = SQLiteExecutionStore(database)
    restored_runtime, _ = _runtime(
        restored_store,
        records={operation.attempt_id: _receipt_record(operation.attempt_id)},
        provider=provider,
    )
    result = restored_runtime.replay_receipt(receipt_id)
    assert result.recovery.outcome == RecoveryOutcome.ALREADY_ACCOUNTED
    assert result.receipt is not None
    assert result.receipt.accounting_state == ReceiptAccountingState.ACCOUNTED
    assert provider.calls == [operation.attempt_id]
    assert (
        restored_store.restore_ledger("session-1")
        .snapshot(run_id="run-1")
        .receipt_count
        == 1
    )


def test_crash_after_ledger_commit_before_response_is_already_accounted(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _session(store)
    provider = ScriptedProviderPort()
    operation = _operation("attempt-committed")
    crashing, _ = _runtime(
        store,
        records={operation.attempt_id: _receipt_record(operation.attempt_id)},
        provider=provider,
        crash_hook=CrashOnce("AFTER_LEDGER_COMMIT"),
    )
    with pytest.raises(SimulatedCrash):
        asyncio.run(crashing.execute(session_id="session-1", operation=operation))
    assert (
        store.recover_operation(
            session_id="session-1", attempt_id=operation.attempt_id
        ).outcome
        == RecoveryOutcome.ALREADY_ACCOUNTED
    )

    resumed, _ = _runtime(
        store,
        records={operation.attempt_id: _receipt_record(operation.attempt_id)},
        provider=provider,
    )
    result = asyncio.run(resumed.execute(session_id="session-1", operation=operation))
    assert result.recovery.outcome == RecoveryOutcome.ALREADY_ACCOUNTED
    assert provider.calls == [operation.attempt_id]


def test_true_retry_crash_preserves_lineage_and_is_retry_safe(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _session(store)
    first = _operation("attempt-failed", operation_id="op-retry")
    second = _operation(
        "attempt-retry",
        operation_id="op-retry",
        retry_of="attempt-failed",
    )
    provider = ScriptedProviderPort(
        {"attempt-failed": RuntimeError("fixture provider failure")}
    )
    records = {
        "attempt-failed": _receipt_record(
            "attempt-failed",
            receipt=_usage("attempt-failed", success=False),
        ),
        "attempt-retry": _receipt_record("attempt-retry"),
    }
    runtime, _ = _runtime(store, records=records, provider=provider)
    failed = asyncio.run(runtime.execute(session_id="session-1", operation=first))
    assert failed.operation is not None
    assert failed.operation.execution_status == OperationExecutionStatus.FAILED

    crashing, _ = _runtime(
        store,
        records=records,
        provider=provider,
        crash_hook=CrashOnce("AFTER_AUTHORIZATION"),
    )
    with pytest.raises(SimulatedCrash):
        asyncio.run(crashing.execute(session_id="session-1", operation=second))
    assert (
        store.recover_operation(
            session_id="session-1", attempt_id="attempt-retry"
        ).outcome
        == RecoveryOutcome.RETRY_SAFE
    )
    lineage = {
        item.attempt_id: item
        for item in runtime.audit_report("session-1").retry_lineage
    }
    assert lineage["attempt-retry"].retry_of_attempt_id == "attempt-failed"


def test_cancellation_and_budget_stop_survive_restart(tmp_path: Path) -> None:
    database = tmp_path / "stop.sqlite3"
    store = SQLiteExecutionStore(database)
    _session(store)
    operation = _operation("attempt-cancelled")
    permission = store.authorize_operation(
        session_id="session-1",
        operation=operation,
        route=RouteMetadata(True, "openai_compatible", "deepseek-v4-flash"),
    )
    assert permission.allowed
    store.cancel_operation(session_id="session-1", attempt_id=operation.attempt_id)
    store.close()

    restored = SQLiteExecutionStore(database)
    assert (
        restored.recover_operation(
            session_id="session-1", attempt_id=operation.attempt_id
        ).outcome
        == RecoveryOutcome.STOPPED
    )

    runtime, provider = _runtime(restored, records={})
    stopped = runtime.create_session(
        session_id="session-stop",
        run_id="run-stop",
        job_start_estimate=_estimate("0.10", "0.50", "0.95"),
        currency="USD",
        user_max_budget=Decimal("1.00"),
        warning_threshold=Decimal("0.60"),
        hard_stop_threshold=Decimal("0.90"),
    )
    assert stopped.state == ExecutionSessionState.STOPPED
    result = asyncio.run(
        runtime.execute(session_id="session-stop", operation=_operation("attempt-stop"))
    )
    assert result.recovery.outcome == RecoveryOutcome.STOPPED
    assert provider.calls == []


def test_cancellation_with_usage_replays_as_cancelled_without_double_charge(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _session(store)
    operation = _operation("attempt-cancelled-usage")
    provider = ScriptedProviderPort({operation.attempt_id: asyncio.CancelledError()})
    records = {
        operation.attempt_id: _receipt_record(
            operation.attempt_id,
            receipt=_usage(operation.attempt_id, success=False),
        )
    }
    crashing, _ = _runtime(
        store,
        records=records,
        provider=provider,
        crash_hook=CrashOnce("AFTER_RECEIPT_STAGED"),
    )
    with pytest.raises(SimulatedCrash):
        asyncio.run(crashing.execute(session_id="session-1", operation=operation))
    staged_operation = store.get_operation(
        session_id="session-1", attempt_id=operation.attempt_id
    )
    assert staged_operation is not None
    assert staged_operation.execution_status == OperationExecutionStatus.CANCELLED
    assert (
        store.recover_operation(
            session_id="session-1", attempt_id=operation.attempt_id
        ).outcome
        == RecoveryOutcome.RECEIPT_REPLAY_REQUIRED
    )

    resumed, _ = _runtime(store, records=records, provider=provider)
    result = resumed.replay_receipt(staged_operation.receipt_id or "")
    assert result.operation is not None
    assert result.operation.execution_status == OperationExecutionStatus.CANCELLED
    assert result.recovery.outcome == RecoveryOutcome.ALREADY_ACCOUNTED
    assert provider.calls == [operation.attempt_id]


def test_duplicate_and_conflicting_receipts_are_safe_under_concurrency(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _session(store)
    operation = _operation("attempt-concurrent")
    _authorize_and_claim(store, operation)
    receipt = _usage(operation.attempt_id)

    def stage_once():
        return store.stage_receipt(
            receipt_id="receipt-concurrent",
            session_id="session-1",
            attempt_id=operation.attempt_id,
            receipt=receipt,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(lambda _value: stage_once(), range(8)))
    assert sum(result.accepted for result in results) == 1
    assert sum(result.duplicate for result in results) == 7
    assert len(store.list_receipts("session-1")) == 1

    with pytest.raises(DurableIdentityConflict):
        store.stage_receipt(
            receipt_id="receipt-concurrent",
            session_id="session-1",
            attempt_id=operation.attempt_id,
            receipt=replace(receipt, request_id="conflicting-request"),
        )


def test_partial_stage_and_commit_transactions_roll_back_atomically(
    tmp_path: Path,
) -> None:
    failing_phases: set[str] = {"STAGE_AFTER_RECEIPT_INSERT"}

    def injector(phase: str) -> None:
        if phase in failing_phases:
            raise SimulatedCrash(phase)

    store = _store(tmp_path, failure_injector=injector)
    _session(store)
    operation = _operation("attempt-rollback")
    _authorize_and_claim(store, operation)
    receipt = _usage(operation.attempt_id)
    with pytest.raises(SimulatedCrash):
        store.stage_receipt(
            receipt_id="receipt-rollback",
            session_id="session-1",
            attempt_id=operation.attempt_id,
            receipt=receipt,
        )
    assert store.get_receipt("receipt-rollback") is None
    assert (
        store.get_operation(
            session_id="session-1", attempt_id=operation.attempt_id
        ).state
        == OperationState.PROVIDER_STARTED
    )

    failing_phases.clear()
    store.stage_receipt(
        receipt_id="receipt-rollback",
        session_id="session-1",
        attempt_id=operation.attempt_id,
        receipt=receipt,
    )
    failing_phases.add("COMMIT_AFTER_LEDGER_UPDATE")
    with pytest.raises(SimulatedCrash):
        store.commit_receipt(
            receipt_id="receipt-rollback",
            execution_status=OperationExecutionStatus.SUCCEEDED,
        )
    durable_receipt = store.get_receipt("receipt-rollback")
    assert durable_receipt is not None
    assert durable_receipt.accounting_state == ReceiptAccountingState.STAGED
    assert (
        store.get_operation(
            session_id="session-1", attempt_id=operation.attempt_id
        ).state
        == OperationState.RECEIPT_STAGED
    )
    assert store.restore_ledger("session-1").snapshot(run_id="run-1").receipt_count == 0


def test_unknown_mixed_pricing_provider_cache_and_local_cache_audit(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _session(store, policy=UnknownCostPolicy.ALLOW)
    baseline = LocalCacheSavingsBaseline(
        amount=Decimal("0.125"),
        currency="USD",
        estimation_basis="saved Provider call estimate",
    )
    known = _operation("attempt-known")
    unknown = _operation("attempt-unknown")
    local = _operation(
        "attempt-local",
        local_cache=True,
        baseline=baseline,
    )
    records = {
        known.attempt_id: _receipt_record(
            known.attempt_id,
            receipt=_usage(
                known.attempt_id,
                cache_hit_tokens=1_000_000,
                cache_miss_tokens=0,
            ),
        ),
        unknown.attempt_id: _receipt_record(
            unknown.attempt_id,
            receipt=_usage(unknown.attempt_id, model="unpriced-model"),
        ),
        local.attempt_id: _receipt_record(
            local.attempt_id,
            receipt=_usage(
                local.attempt_id,
                prompt_tokens=None,
                completion_tokens=None,
                cache_hit_tokens=None,
                cache_miss_tokens=None,
                provider_called=False,
                local_cache_hit=True,
            ),
            baseline=baseline,
        ),
    }
    runtime, provider = _runtime(store, records=records)
    for operation in (known, unknown, local):
        result = asyncio.run(
            runtime.execute(session_id="session-1", operation=operation)
        )
        assert result.recovery.outcome == RecoveryOutcome.ALREADY_ACCOUNTED

    report = runtime.audit_report("session-1")
    assert report.unknown_receipts == 1
    assert report.unpriced_receipts == 1
    assert not report.actual_cost_complete
    assert report.actual_known_cost is not None
    assert report.provider_calls == 2
    assert report.local_cache_avoided_calls == 1
    assert report.provider_cache_hit_tokens == 1_000_000
    assert report.provider_cache_hit_ratio is not None
    assert report.provider_cache_savings > 0
    assert report.local_cache_savings == Decimal("0.125")
    assert report.estimated_savings == (
        report.provider_cache_savings + report.local_cache_savings
    )
    assert report.savings_classification == "estimated_savings"
    public = report.to_public_dict()
    assert public["savings"]["classification"] == "estimated_savings"
    assert provider.calls == [known.attempt_id, unknown.attempt_id]


def test_actual_cost_exhaustion_stops_future_work_and_is_auditable(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _session(
        store,
        maximum="0.05",
        warning="0.03",
        hard_stop="0.04",
        estimate=_estimate("0.001", "0.002", "0.003"),
    )
    operation = _operation(
        "attempt-expensive", estimate=_estimate("0.001", "0.002", "0.003")
    )
    runtime, provider = _runtime(
        store, records={operation.attempt_id: _receipt_record(operation.attempt_id)}
    )
    result = asyncio.run(runtime.execute(session_id="session-1", operation=operation))
    assert result.budget_receipt is not None
    assert result.budget_receipt.decision == BudgetDecision.STOP
    assert store.get_session("session-1").state == ExecutionSessionState.STOPPED

    future = asyncio.run(
        runtime.execute(session_id="session-1", operation=_operation("attempt-future"))
    )
    assert future.recovery.outcome == RecoveryOutcome.STOPPED
    assert provider.calls == [operation.attempt_id]
    report = runtime.audit_report("session-1")
    assert report.stop_reason is not None
    assert "THRESHOLD" in report.stop_reason or "BUDGET" in report.stop_reason


def test_failed_provider_with_usage_is_accounted_not_reexecuted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _session(store)
    operation = _operation("attempt-failed-usage")
    provider = ScriptedProviderPort(
        {operation.attempt_id: RuntimeError("provider failed after usage")}
    )
    runtime, _ = _runtime(
        store,
        provider=provider,
        records={
            operation.attempt_id: _receipt_record(
                operation.attempt_id,
                receipt=_usage(operation.attempt_id, success=False),
            )
        },
    )
    result = asyncio.run(runtime.execute(session_id="session-1", operation=operation))
    assert result.operation is not None
    assert result.operation.execution_status == OperationExecutionStatus.FAILED
    assert result.recovery.outcome == RecoveryOutcome.ALREADY_ACCOUNTED
    replay = asyncio.run(runtime.execute(session_id="session-1", operation=operation))
    assert replay.recovery.outcome == RecoveryOutcome.ALREADY_ACCOUNTED
    assert provider.calls == [operation.attempt_id]


def test_provider_runtime_v2_bridge_preserves_unknown_cache_usage_and_no_real_calls(
    tmp_path: Path,
) -> None:
    from app.model_routing import (
        ProviderCapabilityProfile,
        ProviderOrchestrator,
        ProviderProfileRegistration,
        ProviderProfileRegistry,
        TaskType,
    )
    from app.provider.runtime_v2 import (
        AdapterExecutionResult,
        FakeProviderAdapter,
        ProviderExecutionRequest,
        ProviderRuntimeCoordinator,
        ProviderRuntimeProfile,
        ProviderRuntimeRegistry,
        ProviderSessionContext,
    )

    registration = ProviderProfileRegistration(
        adapter_identity="openai_compatible",
        configured_base_url="https://gateway.example/v1",
        profile=ProviderCapabilityProfile(
            provider="openai_compatible",
            model="deepseek-v4-flash",
            text_generation=True,
        ),
    )
    profiles = ProviderProfileRegistry((registration,))
    runtime_registry = ProviderRuntimeRegistry()
    entry = profiles.entries[0]
    fake = FakeProviderAdapter(
        port_name="fake:" + entry.identity.canonical_id,
        script=(
            AdapterExecutionResult(
                success=True,
                output_text="fake durable result",
                prompt_tokens=100,
                completion_tokens=20,
            ),
        ),
    )
    runtime_registry.register(
        ProviderRuntimeProfile.from_registered_profile(
            entry, adapter_port=fake.port_name
        ),
        fake,
    )
    coordinator = ProviderRuntimeCoordinator(
        orchestrator=ProviderOrchestrator(profiles),
        runtime_registry=runtime_registry,
    )
    bridge = ProviderRuntimeV2DurablePorts(coordinator)
    store = _store(tmp_path)
    _session(store, policy=UnknownCostPolicy.ALLOW)
    runtime = DurableExecutionRuntime(
        store=store,
        provider_port=bridge,
        route_port=bridge,
        cost_receipt_port=bridge,
    )
    request = ProviderExecutionRequest(
        task_requirement=TaskType.TEACHER,
        session=ProviderSessionContext(
            session_id="provider-session", request_id="provider-request"
        ),
        payload="fake-only Provider Runtime V2 payload",
    )
    operation = _operation("attempt-provider-v2", payload=request)

    result = asyncio.run(runtime.execute(session_id="session-1", operation=operation))

    assert result.recovery.outcome == RecoveryOutcome.ALREADY_ACCOUNTED
    assert result.receipt is not None
    assert result.receipt.receipt.prompt_tokens == 100
    assert result.receipt.receipt.prompt_cache_hit_tokens is None
    assert result.receipt.receipt.prompt_cache_miss_tokens is None
    assert result.receipt.cost_status == "COST_UNAVAILABLE"
    assert result.receipt.unavailable_reason == "CACHE_BREAKDOWN_UNAVAILABLE"
    assert result.value.receipt.usage_metadata.real_provider_calls == 0
    assert fake.call_count == 1
    replay = asyncio.run(runtime.execute(session_id="session-1", operation=operation))
    assert replay.recovery.outcome == RecoveryOutcome.ALREADY_ACCOUNTED
    assert fake.call_count == 1


def test_corrupted_database_is_reported_fail_safe(tmp_path: Path) -> None:
    database = tmp_path / "corrupt.sqlite3"
    database.write_bytes(b"this is not a sqlite database")
    with pytest.raises(ExecutionStoreCorruption):
        SQLiteExecutionStore(database)

    unchecked = SQLiteExecutionStore(database, auto_bootstrap=False)
    assert not unchecked.integrity_check().ok
    unchecked.close(checkpoint=False)
