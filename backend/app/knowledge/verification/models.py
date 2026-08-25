from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from ..inventory.models import (
    EvidenceReference,
    KnowledgeInventory,
    KnowledgeType,
    TeachingDepth,
    TimeRange,
    canonical_semantic_key,
    stable_hash,
)


VERIFICATION_SCHEMA_VERSION = "mentorflow.recall-verification.v1"
VERIFIER_VERSION = "mentorflow.recall-verifier.v1"
REPAIR_SCHEMA_VERSION = "mentorflow.anchor-recall-repair.v1"


class VerificationStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class MissClassification(str, Enum):
    ANCHOR_MISS = "ANCHOR_MISS"
    POSSIBLE_ANCHOR_MISS = "POSSIBLE_ANCHOR_MISS"


class ItemFindingCode(str, Enum):
    INVALID_EVIDENCE = "INVALID_EVIDENCE"
    UNSUPPORTED = "UNSUPPORTED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


class SupportAnomalyState(str, Enum):
    POSSIBLE_ANCHOR_MISS = "POSSIBLE_ANCHOR_MISS"
    POSSIBLE_ANCHOR_MISS_UNCONFIRMED = "POSSIBLE_ANCHOR_MISS_UNCONFIRMED"


@dataclass(frozen=True)
class VerificationEvidence:
    evidence_id: str
    source_identity: str
    source_revision: str
    source_kind: str
    part_key: str
    part_index: int | None
    part_cid: str | None
    start_ms: int | None
    end_ms: int | None
    original_text: str
    normalized_text: str
    evidence_hash: str

    def __post_init__(self) -> None:
        for field in (
            "evidence_id",
            "source_identity",
            "source_revision",
            "source_kind",
            "part_key",
            "original_text",
            "normalized_text",
            "evidence_hash",
        ):
            if not str(getattr(self, field) or "").strip():
                raise ValueError(f"{field} is required")
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("start_ms and end_ms must both be present or absent")
        if self.part_index is not None and self.part_index <= 0:
            raise ValueError("part_index must be positive when present")
        if self.start_ms is not None and (
            self.start_ms < 0 or self.end_ms is None or self.end_ms <= self.start_ms
        ):
            raise ValueError("evidence time range is invalid")

    def to_prompt_dict(self, *, is_core: bool) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_kind": self.source_kind,
            "part_key": self.part_key,
            "part_index": self.part_index,
            "part_cid": self.part_cid,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "original_text": self.original_text,
            "normalized_text": self.normalized_text,
            "evidence_hash": self.evidence_hash,
            "is_core": is_core,
        }

    def to_evidence_reference(self) -> EvidenceReference:
        return EvidenceReference(
            segment_id=self.evidence_id,
            part_index=self.part_index or 0,
            part_cid=self.part_cid,
            start_ms=self.start_ms or 0,
            end_ms=self.end_ms or 0,
            original_segment_hash=self.evidence_hash,
        )

    def to_time_range(self) -> TimeRange | None:
        if self.part_index is None or self.start_ms is None or self.end_ms is None:
            return None
        return TimeRange(self.part_index, self.start_ms, self.end_ms)


@dataclass(frozen=True)
class SupportKnowledgeHint:
    hint_id: str
    canonical_name: str
    knowledge_type: KnowledgeType
    support_source_identity: str
    support_evidence_ids: tuple[str, ...]
    confidence: float
    suspected_anchor_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.hint_id.strip() or not self.canonical_name.strip():
            raise ValueError("hint_id and canonical_name are required")
        if not self.support_source_identity.strip() or not self.support_evidence_ids:
            raise ValueError("support hint requires support provenance")
        if not 0 <= self.confidence <= 1:
            raise ValueError("support hint confidence must be between 0 and 1")
        if len(self.support_evidence_ids) != len(set(self.support_evidence_ids)):
            raise ValueError("support hint evidence IDs must be unique")
        if len(self.suspected_anchor_evidence_ids) != len(
            set(self.suspected_anchor_evidence_ids)
        ):
            raise ValueError("suspected anchor evidence IDs must be unique")

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "hint_id": self.hint_id,
            "canonical_name": self.canonical_name,
            "knowledge_type": self.knowledge_type.value,
            "support_source_identity": self.support_source_identity,
            "support_evidence_ids": list(self.support_evidence_ids),
            "confidence": self.confidence,
            "suspected_anchor_evidence_ids": list(self.suspected_anchor_evidence_ids),
            "policy": "trigger_anchor_recheck_only",
        }


@dataclass(frozen=True)
class CriticalConceptRule:
    canonical_name: str
    knowledge_type: KnowledgeType
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not canonical_semantic_key(self.canonical_name):
            raise ValueError("critical concept canonical_name is required")

    @property
    def semantic_names(self) -> frozenset[str]:
        return frozenset(
            canonical_semantic_key(value)
            for value in (self.canonical_name, *self.aliases)
            if canonical_semantic_key(value)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_name": self.canonical_name,
            "knowledge_type": self.knowledge_type.value,
            "aliases": list(self.aliases),
        }


@dataclass(frozen=True)
class CriticalConceptPolicy:
    rules: tuple[CriticalConceptRule, ...] = ()

    def is_critical(
        self,
        canonical_name: str,
        knowledge_type: KnowledgeType,
        aliases: Iterable[str] = (),
    ) -> bool:
        names = {
            canonical_semantic_key(value)
            for value in (canonical_name, *tuple(aliases))
            if canonical_semantic_key(value)
        }
        return any(
            rule.knowledge_type is knowledge_type and bool(rule.semantic_names & names)
            for rule in self.rules
        )

    @property
    def policy_hash(self) -> str:
        return stable_hash("critical-policy", [rule.to_dict() for rule in self.rules])

    def to_dict(self) -> dict[str, Any]:
        return {"rules": [rule.to_dict() for rule in self.rules]}


@dataclass(frozen=True)
class VerificationMissedCandidate:
    stable_candidate_id: str
    canonical_name: str
    raw_names: tuple[str, ...]
    knowledge_type: KnowledgeType
    short_description: str
    teaching_depth: TeachingDepth
    confidence: float
    evidence_refs: tuple[EvidenceReference, ...]
    reason: str
    critical: bool
    support_hint_ids: tuple[str, ...]
    classification: MissClassification
    verifier_version: str = VERIFIER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "stable_candidate_id": self.stable_candidate_id,
            "canonical_name": self.canonical_name,
            "raw_names": list(self.raw_names),
            "knowledge_type": self.knowledge_type.value,
            "short_description": self.short_description,
            "teaching_depth": self.teaching_depth.value,
            "confidence": self.confidence,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "reason": self.reason,
            "critical": self.critical,
            "support_hint_ids": list(self.support_hint_ids),
            "classification": self.classification.value,
            "verifier_version": self.verifier_version,
        }


@dataclass(frozen=True)
class VerificationItemFinding:
    inventory_item_id: str
    code: ItemFindingCode
    reason: str
    evidence_ids: tuple[str, ...]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "inventory_item_id": self.inventory_item_id,
            "code": self.code.value,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class LowConfidenceFinding:
    reference_id: str
    reference_kind: str
    confidence: float
    evidence_ids: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "reference_kind": self.reference_kind,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class VerificationCandidateRejection:
    attempt_id: str
    reference_id: str | None
    code: str
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "reference_id": self.reference_id,
            "code": self.code,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class VerificationFailedBatch:
    batch_id: str
    final_attempt_id: str
    evidence_ids: tuple[str, ...]
    code: str
    attempts: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "final_attempt_id": self.final_attempt_id,
            "evidence_ids": list(self.evidence_ids),
            "code": self.code,
            "attempts": self.attempts,
        }


@dataclass(frozen=True)
class VerificationCoverageLedger:
    total_evidence_count: int
    verified_evidence_ids: tuple[str, ...]
    failed_evidence_ids: tuple[str, ...]
    batch_count: int
    successful_batches: int

    @property
    def coverage_ratio(self) -> float:
        if self.total_evidence_count == 0:
            return 1.0
        return len(self.verified_evidence_ids) / self.total_evidence_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_evidence_count": self.total_evidence_count,
            "verified_evidence_ids": list(self.verified_evidence_ids),
            "failed_evidence_ids": list(self.failed_evidence_ids),
            "batch_count": self.batch_count,
            "successful_batches": self.successful_batches,
            "coverage_ratio": self.coverage_ratio,
        }


@dataclass(frozen=True)
class SupportAnomaly:
    hint_id: str
    canonical_name: str
    state: SupportAnomalyState
    anchor_evidence_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "hint_id": self.hint_id,
            "canonical_name": self.canonical_name,
            "state": self.state.value,
            "anchor_evidence_ids": list(self.anchor_evidence_ids),
        }


@dataclass(frozen=True)
class VerificationDiagnostics:
    missed_concept_classes: tuple[str, ...]
    failed_evidence_ranges: tuple[TimeRange, ...]
    suspicious_support_anomalies: tuple[SupportAnomaly, ...]
    repair_candidate_count: int
    batch_count: int
    provider_calls: int
    retry_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "missed_concept_classes": list(self.missed_concept_classes),
            "failed_evidence_ranges": [item.to_dict() for item in self.failed_evidence_ranges],
            "suspicious_support_anomalies": [item.to_dict() for item in self.suspicious_support_anomalies],
            "repair_candidate_count": self.repair_candidate_count,
            "batch_count": self.batch_count,
            "provider_calls": self.provider_calls,
            "retry_count": self.retry_count,
        }


@dataclass(frozen=True)
class VerificationResult:
    source_identity: str
    source_revision: str
    verification_status: VerificationStatus
    covered: tuple[str, ...]
    missed_candidates: tuple[VerificationMissedCandidate, ...]
    invalid_items: tuple[VerificationItemFinding, ...]
    unsupported_items: tuple[VerificationItemFinding, ...]
    low_confidence: tuple[LowConfidenceFinding, ...]
    rejected_candidates: tuple[VerificationCandidateRejection, ...]
    failed_batches: tuple[VerificationFailedBatch, ...]
    coverage: VerificationCoverageLedger
    diagnostics: VerificationDiagnostics
    schema_version: str = VERIFICATION_SCHEMA_VERSION
    verifier_version: str = VERIFIER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "verifier_version": self.verifier_version,
            "source_identity": self.source_identity,
            "source_revision": self.source_revision,
            "verification_status": self.verification_status.value,
            "covered": list(self.covered),
            "missed_candidates": [item.to_dict() for item in self.missed_candidates],
            "invalid_items": [item.to_dict() for item in self.invalid_items],
            "unsupported_items": [item.to_dict() for item in self.unsupported_items],
            "low_confidence": [item.to_dict() for item in self.low_confidence],
            "rejected_candidates": [item.to_dict() for item in self.rejected_candidates],
            "failed_batches": [item.to_dict() for item in self.failed_batches],
            "coverage": self.coverage.to_dict(),
            "diagnostics": self.diagnostics.to_dict(),
        }


@dataclass(frozen=True)
class RecallRepairAddition:
    stable_candidate_id: str
    proposed_item_id: str
    canonical_name: str
    raw_names: tuple[str, ...]
    knowledge_type: KnowledgeType
    short_description: str
    teaching_depth: TeachingDepth
    confidence: float
    evidence_refs: tuple[EvidenceReference, ...]
    reason: str
    critical: bool
    classification: MissClassification
    verifier_version: str = VERIFIER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": "ADD_CANDIDATE",
            "stable_candidate_id": self.stable_candidate_id,
            "proposed_item_id": self.proposed_item_id,
            "canonical_name": self.canonical_name,
            "raw_names": list(self.raw_names),
            "knowledge_type": self.knowledge_type.value,
            "short_description": self.short_description,
            "teaching_depth": self.teaching_depth.value,
            "confidence": self.confidence,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "reason": self.reason,
            "critical": self.critical,
            "classification": self.classification.value,
            "verifier_version": self.verifier_version,
        }


@dataclass(frozen=True)
class RecallRepairPatch:
    patch_id: str
    source_identity: str
    source_revision: str
    inventory_fingerprint: str
    verification_status: VerificationStatus
    additions: tuple[RecallRepairAddition, ...]
    schema_version: str = REPAIR_SCHEMA_VERSION
    verifier_version: str = VERIFIER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "verifier_version": self.verifier_version,
            "patch_id": self.patch_id,
            "source_identity": self.source_identity,
            "source_revision": self.source_revision,
            "inventory_fingerprint": self.inventory_fingerprint,
            "verification_status": self.verification_status.value,
            "additions": [item.to_dict() for item in self.additions],
        }


def inventory_fingerprint(inventory: KnowledgeInventory) -> str:
    return stable_hash("inventory", inventory.to_dict())
