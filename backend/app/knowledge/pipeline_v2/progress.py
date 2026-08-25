from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class PipelineStatus(str, Enum):
    PREFLIGHTED = "PREFLIGHTED"
    INVENTORY_RUNNING = "INVENTORY_RUNNING"
    VERIFYING = "VERIFYING"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class PipelineCoverageLedger:
    total_evidence_count: int
    inventory_covered_evidence_ids: tuple[str, ...]
    inventory_failed_evidence_ids: tuple[str, ...]
    verified_evidence_ids: tuple[str, ...]
    verification_failed_evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.total_evidence_count < 0:
            raise ValueError("total_evidence_count cannot be negative")
        for field in (
            "inventory_covered_evidence_ids",
            "inventory_failed_evidence_ids",
            "verified_evidence_ids",
            "verification_failed_evidence_ids",
        ):
            values = tuple(getattr(self, field))
            if len(values) != len(set(values)):
                raise ValueError(f"{field} must contain unique evidence IDs")
            if len(values) > self.total_evidence_count:
                raise ValueError(f"{field} exceeds total evidence count")

    @property
    def inventory_coverage_ratio(self) -> float:
        if self.total_evidence_count == 0:
            return 1.0
        return len(self.inventory_covered_evidence_ids) / self.total_evidence_count

    @property
    def verification_coverage_ratio(self) -> float:
        if self.total_evidence_count == 0:
            return 1.0
        return len(self.verified_evidence_ids) / self.total_evidence_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_evidence_count": self.total_evidence_count,
            "inventory_covered_evidence_ids": list(self.inventory_covered_evidence_ids),
            "inventory_failed_evidence_ids": list(self.inventory_failed_evidence_ids),
            "verified_evidence_ids": list(self.verified_evidence_ids),
            "verification_failed_evidence_ids": list(self.verification_failed_evidence_ids),
            "inventory_coverage_ratio": self.inventory_coverage_ratio,
            "verification_coverage_ratio": self.verification_coverage_ratio,
        }


@dataclass(frozen=True)
class FailedEvidenceRange:
    stage: str
    batch_id: str
    evidence_ids: tuple[str, ...]
    code: str
    attempts: int
    final_attempt_id: str | None
    part_index: int | None
    start_ms: int | None
    end_ms: int | None

    def __post_init__(self) -> None:
        if not self.stage.strip() or not self.batch_id.strip() or not self.code.strip():
            raise ValueError("failed evidence range stage, batch_id, and code are required")
        if not self.evidence_ids:
            raise ValueError("failed evidence range requires evidence IDs")
        if self.attempts <= 0:
            raise ValueError("failed evidence range attempts must be positive")
        timed = (self.part_index, self.start_ms, self.end_ms)
        if any(value is None for value in timed) and not all(value is None for value in timed):
            raise ValueError("failed evidence range location must be complete or absent")
        if self.start_ms is not None and (
            self.part_index is None
            or self.part_index <= 0
            or self.start_ms < 0
            or self.end_ms is None
            or self.end_ms <= self.start_ms
        ):
            raise ValueError("failed evidence range location is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "batch_id": self.batch_id,
            "evidence_ids": list(self.evidence_ids),
            "code": self.code,
            "attempts": self.attempts,
            "final_attempt_id": self.final_attempt_id,
            "part_index": self.part_index,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
        }


@dataclass(frozen=True)
class PipelineProgress:
    sequence: int
    status: PipelineStatus
    processed_evidence_count: int
    total_evidence_count: int
    completed_batches: int
    remaining_batches: int
    repair_count: int
    coverage: PipelineCoverageLedger
    elapsed_ms: int
    batch_throughput_per_second: float | None
    estimated_remaining_ms: int | None
    estimate_basis: str | None

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("progress sequence cannot be negative")
        if not 0 <= self.processed_evidence_count <= self.total_evidence_count:
            raise ValueError("processed evidence count is outside the total")
        if self.completed_batches < 0 or self.remaining_batches < 0:
            raise ValueError("batch counts cannot be negative")
        if self.repair_count < 0 or self.elapsed_ms < 0:
            raise ValueError("repair count and elapsed time cannot be negative")
        if self.coverage.total_evidence_count != self.total_evidence_count:
            raise ValueError("progress and coverage totals do not match")
        if self.estimated_remaining_ms is not None and self.estimated_remaining_ms < 0:
            raise ValueError("estimated remaining time cannot be negative")
        if (self.estimated_remaining_ms is None) != (self.estimate_basis is None):
            raise ValueError("estimate value and basis must be present together")

    @property
    def total_batches(self) -> int:
        return self.completed_batches + self.remaining_batches

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "status": self.status.value,
            "processed_evidence_count": self.processed_evidence_count,
            "total_evidence_count": self.total_evidence_count,
            "completed_batches": self.completed_batches,
            "remaining_batches": self.remaining_batches,
            "total_batches": self.total_batches,
            "repair_count": self.repair_count,
            "coverage": self.coverage.to_dict(),
            "elapsed_ms": self.elapsed_ms,
            "batch_throughput_per_second": self.batch_throughput_per_second,
            "estimated_remaining_ms": self.estimated_remaining_ms,
            "estimate_basis": self.estimate_basis,
        }


@dataclass(frozen=True)
class PipelineReceipt:
    receipt_id: str
    pipeline_version: str
    source_identity: str
    source_revision: str
    cache_identity: str
    status: PipelineStatus
    original_evidence_hash: str
    normalized_hash: str
    inventory_fingerprint: str
    repair_patch_id: str
    processed_evidence_count: int
    total_evidence_count: int
    total_batches: int
    provider_calls: int
    retry_count: int
    repair_count: int
    elapsed_ms: int
    failed_evidence_ranges: tuple[FailedEvidenceRange, ...]
    progress_history: tuple[PipelineProgress, ...]

    def __post_init__(self) -> None:
        for field in (
            "receipt_id",
            "pipeline_version",
            "source_identity",
            "source_revision",
            "cache_identity",
            "original_evidence_hash",
            "normalized_hash",
            "inventory_fingerprint",
            "repair_patch_id",
        ):
            if not str(getattr(self, field) or "").strip():
                raise ValueError(f"{field} is required")
        if not 0 <= self.processed_evidence_count <= self.total_evidence_count:
            raise ValueError("receipt processed evidence count is outside the total")
        if min(
            self.total_batches,
            self.provider_calls,
            self.retry_count,
            self.repair_count,
            self.elapsed_ms,
        ) < 0:
            raise ValueError("receipt counts cannot be negative")
        if not self.progress_history:
            raise ValueError("pipeline receipt requires progress history")
        if self.progress_history[-1].status is not self.status:
            raise ValueError("final progress status does not match receipt status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "pipeline_version": self.pipeline_version,
            "source_identity": self.source_identity,
            "source_revision": self.source_revision,
            "cache_identity": self.cache_identity,
            "status": self.status.value,
            "original_evidence_hash": self.original_evidence_hash,
            "normalized_hash": self.normalized_hash,
            "inventory_fingerprint": self.inventory_fingerprint,
            "repair_patch_id": self.repair_patch_id,
            "processed_evidence_count": self.processed_evidence_count,
            "total_evidence_count": self.total_evidence_count,
            "total_batches": self.total_batches,
            "provider_calls": self.provider_calls,
            "retry_count": self.retry_count,
            "repair_count": self.repair_count,
            "elapsed_ms": self.elapsed_ms,
            "failed_evidence_ranges": [item.to_dict() for item in self.failed_evidence_ranges],
            "progress_history": [item.to_dict() for item in self.progress_history],
        }
