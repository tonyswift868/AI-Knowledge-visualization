from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app.cost.execution_v2.contracts import BudgetedOperation
from app.cost.execution_v2.ports import (
    CostReceiptRecord,
    ProviderOperationOutcome,
    RouteMetadata,
)
from app.provider.runtime_v2 import (
    ProviderExecutionRequest,
    ProviderExecutionResult,
    ProviderExecutionStatus,
    ProviderRuntimeCoordinator,
)
from app.providers.telemetry import ProviderUsageReceipt


@dataclass(frozen=True, slots=True)
class _CompletedProviderExecution:
    request: ProviderExecutionRequest
    result: ProviderExecutionResult
    started_at: str
    latency_ms: int


class ProviderRuntimeV2DurablePorts:
    """Adapt Provider Runtime V2 to the durable execution port trio.

    Runtime V2 currently exposes aggregate input/output usage but no Provider
    cache hit/miss split. The generated usage receipt therefore leaves both
    cache token fields unknown. The cost engine will fail safe instead of
    pricing every input token as a cache miss.
    """

    def __init__(self, coordinator: ProviderRuntimeCoordinator) -> None:
        if not isinstance(coordinator, ProviderRuntimeCoordinator):
            raise TypeError("coordinator must be ProviderRuntimeCoordinator")
        self._coordinator = coordinator
        self._completed: dict[str, _CompletedProviderExecution] = {}
        self._lock = threading.RLock()

    async def metadata_for(self, operation: BudgetedOperation) -> RouteMetadata:
        request = self._request(operation)
        plan = self._coordinator.plan(request)
        selected = plan.selected_runtime_profile
        return RouteMetadata(
            routing_available=bool(plan.valid and selected is not None),
            selected_provider=(
                selected.identity.adapter_identity if selected is not None else None
            ),
            selected_model=(selected.identity.model if selected is not None else None),
            reason=plan.invalid_reason or "provider runtime v2 plan",
        )

    async def execute(self, operation: BudgetedOperation) -> ProviderOperationOutcome:
        request = self._request(operation)
        started_at = datetime.now(timezone.utc).isoformat()
        started_ns = time.perf_counter_ns()
        result = await self._coordinator.execute(request)
        latency_ms = max(0, (time.perf_counter_ns() - started_ns) // 1_000_000)
        with self._lock:
            self._completed[operation.attempt_id] = _CompletedProviderExecution(
                request=request,
                result=result,
                started_at=started_at,
                latency_ms=latency_ms,
            )
        return ProviderOperationOutcome(
            value=result,
            provider_called=result.receipt.usage_metadata.attempt_count > 0,
            metadata=result.receipt,
        )

    def receipt_for(
        self,
        operation: BudgetedOperation,
        *,
        outcome: ProviderOperationOutcome | None,
        error: BaseException | None,
    ) -> CostReceiptRecord | None:
        with self._lock:
            completed = self._completed.pop(operation.attempt_id, None)
        if completed is None:
            return None
        execution_receipt = completed.result.receipt
        usage = execution_receipt.usage_metadata
        identity = execution_receipt.selected_provider_identity
        provider_called = usage.attempt_count > 0
        success = (
            execution_receipt.execution_status == ProviderExecutionStatus.SUCCEEDED
        )
        receipt = ProviderUsageReceipt(
            provider=identity.adapter_identity
            if identity is not None
            else "unresolved",
            model=identity.model if identity is not None else "unresolved",
            task_type=execution_receipt.task_type,
            request_id=completed.request.session.request_id,
            started_at=completed.started_at,
            latency_ms=completed.latency_ms,
            prompt_tokens=usage.prompt_tokens if provider_called else None,
            completion_tokens=usage.completion_tokens if provider_called else None,
            total_tokens=usage.total_tokens if provider_called else None,
            prompt_cache_hit_tokens=None,
            prompt_cache_miss_tokens=None,
            cache_hit_ratio=None,
            local_result_cache_hit=None,
            estimated_cost_usd=None,
            pricing_version=None,
            success=success,
            error_class=None if success else execution_receipt.reason_code,
            prompt_version=execution_receipt.receipt_version,
            provider_called=provider_called,
            local_cache_miss=None,
        )
        return CostReceiptRecord(
            receipt=receipt,
            idempotency_key=operation.idempotency_key,
            local_cache_baseline=operation.local_cache_baseline,
        )

    @staticmethod
    def _request(operation: BudgetedOperation) -> ProviderExecutionRequest:
        if not isinstance(operation.payload, ProviderExecutionRequest):
            raise TypeError(
                "ProviderRuntimeV2DurablePorts requires ProviderExecutionRequest payload"
            )
        return operation.payload
