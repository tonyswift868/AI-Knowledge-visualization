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


@dataclass(frozen=True, slots=True)
class DocumentWorkerPoolBenchmarkObservation:
    elapsed_seconds: float
    succeeded_documents: int
    failed_documents: int
    replayed_documents: int
    completed_after_replay: int
    peak_active_workers: int
    network_attempts: int
    model_download_attempts: int


@dataclass(frozen=True, slots=True)
class DocumentWorkerPoolBenchmarkSample:
    document_count: int
    worker_count: int
    elapsed_seconds: float
    succeeded_documents: int
    failed_documents: int
    replayed_documents: int
    completed_after_replay: int
    peak_active_workers: int
    failure_isolated: bool
    bounded: bool
    network_attempts: int
    model_download_attempts: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_count": self.document_count,
            "worker_count": self.worker_count,
            "elapsed_seconds": self.elapsed_seconds,
            "succeeded_documents": self.succeeded_documents,
            "failed_documents": self.failed_documents,
            "replayed_documents": self.replayed_documents,
            "completed_after_replay": self.completed_after_replay,
            "peak_active_workers": self.peak_active_workers,
            "failure_isolated": self.failure_isolated,
            "bounded": self.bounded,
            "network_attempts": self.network_attempts,
            "model_download_attempts": self.model_download_attempts,
        }


@dataclass(frozen=True, slots=True)
class DocumentWorkerPoolBenchmarkReport:
    samples: tuple[DocumentWorkerPoolBenchmarkSample, ...]
    synthetic_replay: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "synthetic_replay": self.synthetic_replay,
            "samples": [sample.to_dict() for sample in self.samples],
        }


WorkerPoolBenchmarkCase = Callable[
    [int, int],
    DocumentWorkerPoolBenchmarkObservation,
]


def run_document_worker_pool_benchmark(
    run_case: WorkerPoolBenchmarkCase,
    *,
    document_count: int = 100,
    worker_counts: Iterable[int] = (1, 2, 4),
) -> DocumentWorkerPoolBenchmarkReport:
    counts = tuple(worker_counts)
    if document_count <= 0:
        raise ValueError("worker benchmark document_count must be positive")
    if not counts or any(count <= 0 or count > 4 for count in counts):
        raise ValueError("worker benchmark counts must be between 1 and 4")
    samples: list[DocumentWorkerPoolBenchmarkSample] = []
    for worker_count in counts:
        observation = run_case(worker_count, document_count)
        if observation.succeeded_documents + observation.failed_documents != document_count:
            raise ValueError("worker benchmark did not account for every document")
        if observation.completed_after_replay != document_count:
            raise ValueError("worker benchmark replay did not complete every document")
        if observation.replayed_documents != observation.failed_documents:
            raise ValueError("worker benchmark replay count does not match failures")
        if observation.network_attempts != 0:
            raise ValueError("worker benchmark attempted network access")
        if observation.model_download_attempts != 0:
            raise ValueError("worker benchmark attempted a model download")
        bounded = 0 < observation.peak_active_workers <= worker_count
        if not bounded:
            raise ValueError("worker benchmark exceeded its configured bound")
        failure_isolated = (
            observation.failed_documents == 0
            or observation.succeeded_documents > 0
        )
        samples.append(
            DocumentWorkerPoolBenchmarkSample(
                document_count=document_count,
                worker_count=worker_count,
                elapsed_seconds=observation.elapsed_seconds,
                succeeded_documents=observation.succeeded_documents,
                failed_documents=observation.failed_documents,
                replayed_documents=observation.replayed_documents,
                completed_after_replay=observation.completed_after_replay,
                peak_active_workers=observation.peak_active_workers,
                failure_isolated=failure_isolated,
                bounded=bounded,
                network_attempts=observation.network_attempts,
                model_download_attempts=observation.model_download_attempts,
            )
        )
    return DocumentWorkerPoolBenchmarkReport(samples=tuple(samples))


__all__ = [
    "SYNTHETIC_BENCHMARK_DISCLAIMER",
    "SyntheticBatchBenchmarkReport",
    "SyntheticBatchBenchmarkSample",
    "DocumentWorkerPoolBenchmarkObservation",
    "DocumentWorkerPoolBenchmarkReport",
    "DocumentWorkerPoolBenchmarkSample",
    "run_document_worker_pool_benchmark",
    "run_synthetic_batch_benchmark",
]
