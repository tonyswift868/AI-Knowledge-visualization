from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .progress import PipelineReceipt


class PipelineMetricScope(str, Enum):
    OFFLINE_FIXTURE = "OFFLINE_FIXTURE"
    PRODUCT_OPERATIONAL_OBSERVATION = "PRODUCT_OPERATIONAL_OBSERVATION"


@dataclass(frozen=True)
class LabeledPipelineMetrics:
    scope: PipelineMetricScope
    metrics: Mapping[str, int | float]
    accuracy_claim_allowed: bool
    label: str

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("metric label is required")
        if self.scope is PipelineMetricScope.OFFLINE_FIXTURE and self.accuracy_claim_allowed:
            raise ValueError("offline fixture metrics cannot claim product accuracy")

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope.value,
            "metrics": dict(self.metrics),
            "accuracy_claim_allowed": self.accuracy_claim_allowed,
            "label": self.label,
        }


def label_fixture_metrics(metrics: Mapping[str, int | float]) -> LabeledPipelineMetrics:
    return LabeledPipelineMetrics(
        scope=PipelineMetricScope.OFFLINE_FIXTURE,
        metrics=dict(metrics),
        accuracy_claim_allowed=False,
        label="offline fixture only; not a product accuracy measurement",
    )


def operational_metrics(receipt: PipelineReceipt) -> LabeledPipelineMetrics:
    return LabeledPipelineMetrics(
        scope=PipelineMetricScope.PRODUCT_OPERATIONAL_OBSERVATION,
        metrics={
            "processed_evidence_count": receipt.processed_evidence_count,
            "total_evidence_count": receipt.total_evidence_count,
            "total_batches": receipt.total_batches,
            "provider_calls": receipt.provider_calls,
            "retry_count": receipt.retry_count,
            "repair_count": receipt.repair_count,
            "elapsed_ms": receipt.elapsed_ms,
        },
        accuracy_claim_allowed=False,
        label="pipeline operational observation; recall and precision are not inferred",
    )
