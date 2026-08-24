from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from ...sources.video.transcript_evidence import TranscriptEvidenceBundle


INVENTORY_SCHEMA_VERSION = "mentorflow.knowledge-inventory.v1"
EXTRACTOR_VERSION = "mentorflow.high-recall-extractor.v1"


class KnowledgeType(str, Enum):
    CONCEPT = "CONCEPT"
    TERM = "TERM"
    MECHANISM = "MECHANISM"
    RULE = "RULE"
    ALGORITHM = "ALGORITHM"
    API = "API"
    CODE_PATTERN = "CODE_PATTERN"
    FORMULA = "FORMULA"
    WARNING = "WARNING"
    LIMITATION = "LIMITATION"
    BEST_PRACTICE = "BEST_PRACTICE"
    EXAMPLE = "EXAMPLE"
    PREREQUISITE_HINT = "PREREQUISITE_HINT"


class TeachingDepth(str, Enum):
    MENTION_ONLY = "MENTION_ONLY"
    BRIEF_EXPLANATION = "BRIEF_EXPLANATION"
    CORE_EXPLANATION = "CORE_EXPLANATION"
    DEEP_DIVE = "DEEP_DIVE"
    PRACTICE_DEMONSTRATED = "PRACTICE_DEMONSTRATED"


class InventoryStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


TEACHING_DEPTH_RANK: dict[TeachingDepth, int] = {
    TeachingDepth.MENTION_ONLY: 0,
    TeachingDepth.BRIEF_EXPLANATION: 1,
    TeachingDepth.CORE_EXPLANATION: 2,
    TeachingDepth.DEEP_DIVE: 3,
    TeachingDepth.PRACTICE_DEMONSTRATED: 4,
}


def canonical_semantic_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    collapsed = "".join(
        " " if unicodedata.category(char).startswith(("P", "Z")) else char
        for char in normalized
    )
    return " ".join(collapsed.split())


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def stable_hash(prefix: str, value: object) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest}"


def stable_item_id(
    *,
    source_revision: str,
    canonical_name: str,
    knowledge_type: KnowledgeType,
    evidence_ids: Iterable[str],
    extractor_version: str = EXTRACTOR_VERSION,
) -> str:
    return stable_hash(
        "ki",
        {
            "canonical_name": canonical_semantic_key(canonical_name),
            "evidence_ids": sorted(set(evidence_ids)),
            "extractor_version": extractor_version,
            "knowledge_type": knowledge_type.value,
            "source_revision": source_revision,
        },
    )


@dataclass(frozen=True)
class SourceMetadata:
    source_identity: str
    source_revision: str
    source_reference: str
    platform: str
    title: str
    language: str

    def __post_init__(self) -> None:
        for field in (
            "source_identity",
            "source_revision",
            "source_reference",
            "platform",
            "title",
            "language",
        ):
            if not str(getattr(self, field) or "").strip():
                raise ValueError(f"{field} is required")

    @classmethod
    def from_bundle(
        cls,
        bundle: TranscriptEvidenceBundle,
        *,
        title: str,
    ) -> SourceMetadata:
        return cls(
            source_identity=bundle.source_identity,
            source_revision=bundle.source_revision,
            source_reference=bundle.source_reference,
            platform=bundle.platform,
            title=title,
            language=bundle.subtitle_language,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "source_identity": self.source_identity,
            "source_revision": self.source_revision,
            "source_reference": self.source_reference,
            "platform": self.platform,
            "title": self.title,
            "language": self.language,
        }


@dataclass(frozen=True)
class PartChapterContext:
    part_index: int
    part_title: str
    chapter_id: str | None = None
    chapter_title: str | None = None

    def __post_init__(self) -> None:
        if self.part_index <= 0:
            raise ValueError("part_index must be positive")
        if not self.part_title.strip():
            raise ValueError("part_title is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "part_index": self.part_index,
            "part_title": self.part_title,
            "chapter_id": self.chapter_id,
            "chapter_title": self.chapter_title,
        }


@dataclass(frozen=True)
class EvidenceReference:
    segment_id: str
    part_index: int
    part_cid: str | None
    start_ms: int
    end_ms: int
    original_segment_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "part_index": self.part_index,
            "part_cid": self.part_cid,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "original_segment_hash": self.original_segment_hash,
        }


@dataclass(frozen=True)
class TimeRange:
    part_index: int
    start_ms: int
    end_ms: int

    def to_dict(self) -> dict[str, int]:
        return {
            "part_index": self.part_index,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
        }


@dataclass(frozen=True)
class KnowledgeInventoryItem:
    stable_item_id: str
    canonical_name: str
    raw_names: tuple[str, ...]
    knowledge_type: KnowledgeType
    short_description: str
    teaching_depth: TeachingDepth
    confidence: float
    evidence_refs: tuple[EvidenceReference, ...]
    source_parts: tuple[int, ...]
    time_ranges: tuple[TimeRange, ...]
    candidate_prerequisites: tuple[str, ...]
    mentioned_only: bool
    independently_teachable: bool
    extractor_version: str = EXTRACTOR_VERSION

    def __post_init__(self) -> None:
        if not self.stable_item_id or not self.canonical_name.strip():
            raise ValueError("stable_item_id and canonical_name are required")
        if not self.short_description.strip():
            raise ValueError("short_description is required")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not self.evidence_refs:
            raise ValueError("at least one evidence reference is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stable_item_id": self.stable_item_id,
            "canonical_name": self.canonical_name,
            "raw_names": list(self.raw_names),
            "knowledge_type": self.knowledge_type.value,
            "short_description": self.short_description,
            "teaching_depth": self.teaching_depth.value,
            "confidence": self.confidence,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "source_parts": list(self.source_parts),
            "time_ranges": [item.to_dict() for item in self.time_ranges],
            "candidate_prerequisites": list(self.candidate_prerequisites),
            "mentioned_only": self.mentioned_only,
            "independently_teachable": self.independently_teachable,
            "extractor_version": self.extractor_version,
        }


@dataclass(frozen=True)
class CandidateRejection:
    batch_id: str
    candidate_id: str | None
    code: str
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "candidate_id": self.candidate_id,
            "code": self.code,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class FailedBatch:
    batch_id: str
    segment_ids: tuple[str, ...]
    code: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "segment_ids": list(self.segment_ids),
            "code": self.code,
        }


@dataclass(frozen=True)
class CoverageLedger:
    total_segment_count: int
    covered_segment_ids: tuple[str, ...]
    failed_segment_ids: tuple[str, ...]
    low_confidence_ranges: tuple[TimeRange, ...]

    @property
    def coverage_ratio(self) -> float:
        if self.total_segment_count == 0:
            return 1.0
        return len(self.covered_segment_ids) / self.total_segment_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_segment_count": self.total_segment_count,
            "covered_segment_ids": list(self.covered_segment_ids),
            "failed_segment_ids": list(self.failed_segment_ids),
            "low_confidence_ranges": [item.to_dict() for item in self.low_confidence_ranges],
            "coverage_ratio": self.coverage_ratio,
        }


@dataclass(frozen=True)
class KnowledgeInventory:
    source_identity: str
    source_revision: str
    status: InventoryStatus
    items: tuple[KnowledgeInventoryItem, ...]
    coverage: CoverageLedger
    failed_batches: tuple[FailedBatch, ...]
    rejected_candidates: tuple[CandidateRejection, ...]
    batch_count: int
    successful_batches: int
    provider_calls: int
    schema_version: str = INVENTORY_SCHEMA_VERSION
    extractor_version: str = EXTRACTOR_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "extractor_version": self.extractor_version,
            "source_identity": self.source_identity,
            "source_revision": self.source_revision,
            "status": self.status.value,
            "items": [item.to_dict() for item in self.items],
            "coverage": self.coverage.to_dict(),
            "failed_batches": [item.to_dict() for item in self.failed_batches],
            "rejected_candidates": [item.to_dict() for item in self.rejected_candidates],
            "batch_count": self.batch_count,
            "successful_batches": self.successful_batches,
            "provider_calls": self.provider_calls,
        }
