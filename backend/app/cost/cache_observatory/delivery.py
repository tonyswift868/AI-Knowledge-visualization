from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .integrity_benchmark import CacheIntegrityBenchmarkResult

DELIVERY_ID = "mentorflow-w6-03-cache-runtime-v1"
DELIVERY_PREREQUISITE_COMMIT = "be760c6392dcf090103ef01eda2b575657eca786"


class CacheRuntimeCapability(str, Enum):
    DURABLE_STORE = "DURABLE_STORE"
    IDENTITY_POLICY = "IDENTITY_POLICY"
    TARGETED_INVALIDATION = "TARGETED_INVALIDATION"
    RUNTIME_PORTS = "RUNTIME_PORTS"
    COST_EXPERIMENTS = "COST_EXPERIMENTS"
    TEACHER_ANALYSIS = "TEACHER_ANALYSIS"
    REVIEW_ANALYSIS = "REVIEW_ANALYSIS"
    PRODUCT_METRICS = "PRODUCT_METRICS"
    DATA_INTEGRITY = "DATA_INTEGRITY"


@dataclass(frozen=True, slots=True)
class CacheRuntimeDeliveryEvidence:
    verified_capabilities: tuple[CacheRuntimeCapability, ...]
    integrity_benchmark: CacheIntegrityBenchmarkResult


@dataclass(frozen=True, slots=True)
class CacheRuntimeDeliveryReceipt:
    delivery_id: str
    ready: bool
    failures: tuple[str, ...]
    prerequisite_commit: str
    required_capabilities: tuple[CacheRuntimeCapability, ...]
    automatic_execution_policy_change: bool = False

    def to_public_dict(self) -> dict[str, object]:
        return {
            "delivery_id": self.delivery_id,
            "ready": self.ready,
            "failures": list(self.failures),
            "prerequisite_commit": self.prerequisite_commit,
            "required_capabilities": [
                item.value for item in self.required_capabilities
            ],
            "automatic_execution_policy_change": (
                self.automatic_execution_policy_change
            ),
        }


def evaluate_delivery_fence(
    evidence: CacheRuntimeDeliveryEvidence,
) -> CacheRuntimeDeliveryReceipt:
    required = tuple(CacheRuntimeCapability)
    present = set(evidence.verified_capabilities)
    failures = [
        f"MISSING_CAPABILITY:{capability.value}"
        for capability in required
        if capability not in present
    ]
    benchmark = evidence.integrity_benchmark
    checks = (
        (benchmark.event_count >= 100_000, "BENCHMARK_BELOW_100K"),
        (
            benchmark.accepted_event_count == benchmark.event_count,
            "EVENT_COUNT_MISMATCH",
        ),
        (
            benchmark.persisted_event_count_after_restart == benchmark.event_count,
            "RESTART_COUNT_MISMATCH",
        ),
        (benchmark.duplicate_replay_idempotent, "DUPLICATE_REPLAY_FAILED"),
        (benchmark.conflicting_event_rejected, "IDENTITY_CONFLICT_NOT_REJECTED"),
        (benchmark.out_of_order_preserved, "OUT_OF_ORDER_NOT_PRESERVED"),
        (benchmark.integrity_ok, "SQLITE_INTEGRITY_FAILED"),
        (benchmark.journal_mode == "WAL", "WAL_NOT_ACTIVE"),
        (benchmark.real_provider_calls == 0, "REAL_PROVIDER_CALL_DETECTED"),
        (
            benchmark.product_metrics.to_public_dict()["combined_cache_hit_percent"]
            is None,
            "CACHE_HIT_RATES_MERGED",
        ),
    )
    failures.extend(message for passed, message in checks if not passed)
    return CacheRuntimeDeliveryReceipt(
        delivery_id=DELIVERY_ID,
        ready=not failures,
        failures=tuple(failures),
        prerequisite_commit=DELIVERY_PREREQUISITE_COMMIT,
        required_capabilities=required,
    )
