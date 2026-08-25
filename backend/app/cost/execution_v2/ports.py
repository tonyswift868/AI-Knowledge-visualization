from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.cost.contracts import LocalCacheSavingsBaseline
from app.cost.execution_v2.contracts import BudgetedOperation
from app.providers.telemetry import ProviderUsageReceipt


class PortContractError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RouteMetadata:
    routing_available: bool
    selected_provider: str | None
    selected_model: str | None
    reason: str = ""

    def __post_init__(self) -> None:
        provider = str(self.selected_provider or "").strip() or None
        model = str(self.selected_model or "").strip() or None
        object.__setattr__(self, "selected_provider", provider)
        object.__setattr__(self, "selected_model", model)
        if self.routing_available and (provider is None or model is None):
            raise ValueError("available routing metadata requires Provider and model")


@dataclass(frozen=True, slots=True)
class ProviderOperationOutcome:
    value: object | None
    provider_called: bool
    metadata: object | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider_called, bool):
            raise TypeError("provider_called must be bool")


@dataclass(frozen=True, slots=True)
class CostReceiptRecord:
    receipt: ProviderUsageReceipt
    idempotency_key: str | None = None
    local_cache_baseline: LocalCacheSavingsBaseline | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, ProviderUsageReceipt):
            raise TypeError("receipt must be ProviderUsageReceipt")
        normalized_key = str(self.idempotency_key or "").strip() or None
        object.__setattr__(self, "idempotency_key", normalized_key)


@runtime_checkable
class ProviderOperationPort(Protocol):
    async def execute(
        self,
        operation: BudgetedOperation,
    ) -> ProviderOperationOutcome: ...


@runtime_checkable
class RouteMetadataPort(Protocol):
    async def metadata_for(
        self,
        operation: BudgetedOperation,
    ) -> RouteMetadata: ...


@runtime_checkable
class CostReceiptPort(Protocol):
    def receipt_for(
        self,
        operation: BudgetedOperation,
        *,
        outcome: ProviderOperationOutcome | None,
        error: BaseException | None,
    ) -> CostReceiptRecord | None: ...
