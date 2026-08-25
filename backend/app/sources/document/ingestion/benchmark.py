"""Reporting helpers for explicitly synthetic document-ingestion benchmarks."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .contracts import DocumentBatchResult


SYNTHETIC_BENCHMARK_DISCLAIMER = (
    "Synthetic metadata/fixture benchmark; it is not evidence of real PDF performance."
)


@dataclass(frozen=True, slots=True)
class SyntheticBatchBenchmarkSample:
    document_count: int
    preflight_documents_per_second: float | None
    conversion_fixture_documents_per_second: float | None
    evidence_items_per_second: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_count": self.document_count,
            "preflight_documents_per_second": self.preflight_documents_per_second,
            "conversion_fixture_documents_per_second": (
                self.conversion_fixture_documents_per_second
            ),
            "evidence_items_per_second": self.evidence_items_per_second,
        }


@dataclass(frozen=True, slots=True)
class SyntheticBatchBenchmarkReport:
    samples: tuple[SyntheticBatchBenchmarkSample, ...]
    disclaimer: str = SYNTHETIC_BENCHMARK_DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return {
            "disclaimer": self.disclaimer,
            "samples": [sample.to_dict() for sample in self.samples],
        }


def run_synthetic_batch_benchmark(
    run_case: Callable[[int], DocumentBatchResult],
    *,
    document_counts: Iterable[int] = (10, 50, 100),
) -> SyntheticBatchBenchmarkReport:
    counts = tuple(document_counts)
    if not counts or any(count <= 0 for count in counts):
        raise ValueError("synthetic benchmark document counts must be positive")
    samples: list[SyntheticBatchBenchmarkSample] = []
    for count in counts:
        result = run_case(count)
        metrics = result.metrics.to_dict()
        if metrics["preflight_documents"] != count:
            raise ValueError("benchmark case did not preflight the requested fixture count")
        samples.append(
            SyntheticBatchBenchmarkSample(
                document_count=count,
                preflight_documents_per_second=metrics[
                    "preflight_documents_per_second"
                ],
                conversion_fixture_documents_per_second=metrics[
                    "conversion_fixture_documents_per_second"
                ],
                evidence_items_per_second=metrics["evidence_items_per_second"],
            )
        )
    return SyntheticBatchBenchmarkReport(samples=tuple(samples))


__all__ = [
    "SYNTHETIC_BENCHMARK_DISCLAIMER",
    "SyntheticBatchBenchmarkReport",
    "SyntheticBatchBenchmarkSample",
    "run_synthetic_batch_benchmark",
]
