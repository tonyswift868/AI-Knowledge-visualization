from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from ..inventory.models import (
    TEACHING_DEPTH_RANK,
    EvidenceReference,
    KnowledgeType,
    TeachingDepth,
    canonical_semantic_key,
    stable_hash,
)
from ..verification.models import RecallRepairAddition
from .models import PIPELINE_VERSION, VerifiedKnowledgeBundle
from .progress import PipelineStatus


HANDOFF_SCHEMA_VERSION = "mentorflow.knowledge-map-handoff-candidate.v2"


class ConceptVerificationStatus(str, Enum):
    VERIFIED_COVERED = "VERIFIED_COVERED"
    VERIFIED_REPAIR_CANDIDATE = "VERIFIED_REPAIR_CANDIDATE"


@dataclass(frozen=True)
class KnowledgeMapEvidenceLink:
    evidence_id: str
    part_index: int
    part_cid: str | None
    start_ms: int
    end_ms: int
    original_evidence_hash: str

    @classmethod
    def from_reference(cls, reference: EvidenceReference) -> KnowledgeMapEvidenceLink:
        return cls(
            evidence_id=reference.segment_id,
            part_index=reference.part_index,
            part_cid=reference.part_cid,
            start_ms=reference.start_ms,
            end_ms=reference.end_ms,
            original_evidence_hash=reference.original_segment_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "part_index": self.part_index,
            "part_cid": self.part_cid,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "original_evidence_hash": self.original_evidence_hash,
        }


@dataclass(frozen=True)
class RepairLineage:
    patch_id: str
    stable_candidate_id: str
    proposed_item_id: str
    classification: str
    verifier_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "patch_id": self.patch_id,
            "stable_candidate_id": self.stable_candidate_id,
            "proposed_item_id": self.proposed_item_id,
            "classification": self.classification,
            "verifier_version": self.verifier_version,
        }


@dataclass(frozen=True)
class KnowledgeMapConceptCandidate:
    stable_concept_id: str
    canonical_name: str
    raw_names: tuple[str, ...]
    knowledge_type: KnowledgeType
    teaching_depth: TeachingDepth
    evidence_links: tuple[KnowledgeMapEvidenceLink, ...]
    dependencies: tuple[str, ...]
    hints: tuple[str, ...]
    confidence: float
    verification_status: ConceptVerificationStatus
    repair_lineage: tuple[RepairLineage, ...]

    def __post_init__(self) -> None:
        if not self.stable_concept_id.strip() or not canonical_semantic_key(self.canonical_name):
            raise ValueError("handoff concept identity and canonical name are required")
        if not self.evidence_links:
            raise ValueError("handoff concept requires evidence links")
        if not 0 <= self.confidence <= 1:
            raise ValueError("handoff concept confidence must be between 0 and 1")
        if (
            self.verification_status is ConceptVerificationStatus.VERIFIED_REPAIR_CANDIDATE
            and not self.repair_lineage
        ):
            raise ValueError("repair candidate handoff requires repair lineage")

    @property
    def semantic_forms(self) -> frozenset[str]:
        return frozenset(
            key
            for key in (
                canonical_semantic_key(value)
                for value in (self.canonical_name, *self.raw_names)
            )
            if key
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stable_concept_id": self.stable_concept_id,
            "canonical_name": self.canonical_name,
            "raw_names": list(self.raw_names),
            "knowledge_type": self.knowledge_type.value,
            "teaching_depth": self.teaching_depth.value,
            "evidence_links": [item.to_dict() for item in self.evidence_links],
            "dependencies": list(self.dependencies),
            "hints": list(self.hints),
            "confidence": self.confidence,
            "verification_status": self.verification_status.value,
            "repair_lineage": [item.to_dict() for item in self.repair_lineage],
        }


@dataclass(frozen=True)
class KnowledgeMapHandoffCandidate:
    handoff_id: str
    source_identity: str
    source_revision: str
    verification_status: PipelineStatus
    concepts: tuple[KnowledgeMapConceptCandidate, ...]
    pipeline_receipt_id: str
    schema_version: str = HANDOFF_SCHEMA_VERSION
    pipeline_version: str = PIPELINE_VERSION

    def __post_init__(self) -> None:
        if self.verification_status is PipelineStatus.FAILED:
            raise ValueError("failed verification cannot produce a Knowledge Map handoff")
        if not self.handoff_id.strip() or not self.pipeline_receipt_id.strip():
            raise ValueError("handoff and pipeline receipt identities are required")
        concept_ids = [item.stable_concept_id for item in self.concepts]
        if len(concept_ids) != len(set(concept_ids)):
            raise ValueError("handoff concept IDs must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pipeline_version": self.pipeline_version,
            "handoff_id": self.handoff_id,
            "source_identity": self.source_identity,
            "source_revision": self.source_revision,
            "verification_status": self.verification_status.value,
            "pipeline_receipt_id": self.pipeline_receipt_id,
            "concepts": [item.to_dict() for item in self.concepts],
        }


def _repair_hints(bundle: VerifiedKnowledgeBundle, addition: RecallRepairAddition) -> tuple[str, ...]:
    addition_key = canonical_semantic_key(addition.canonical_name)
    return tuple(
        sorted(
            {
                hint_id
                for candidate in bundle.verification.missed_candidates
                if candidate.knowledge_type is addition.knowledge_type
                and (
                    canonical_semantic_key(candidate.canonical_name) == addition_key
                    or addition_key
                    in {
                        canonical_semantic_key(value)
                        for value in (candidate.canonical_name, *candidate.raw_names)
                    }
                )
                for hint_id in candidate.support_hint_ids
            }
        )
    )


def _provisional_concepts(bundle: VerifiedKnowledgeBundle) -> tuple[KnowledgeMapConceptCandidate, ...]:
    covered_ids = set(bundle.verification.covered)
    concepts = [
        KnowledgeMapConceptCandidate(
            stable_concept_id=item.stable_item_id,
            canonical_name=item.canonical_name,
            raw_names=item.raw_names,
            knowledge_type=item.knowledge_type,
            teaching_depth=item.teaching_depth,
            evidence_links=tuple(
                KnowledgeMapEvidenceLink.from_reference(reference)
                for reference in item.evidence_refs
            ),
            dependencies=item.candidate_prerequisites,
            hints=(),
            confidence=item.confidence,
            verification_status=ConceptVerificationStatus.VERIFIED_COVERED,
            repair_lineage=(),
        )
        for item in bundle.inventory.items
        if item.stable_item_id in covered_ids
    ]
    for addition in bundle.repair_patch.additions:
        concepts.append(
            KnowledgeMapConceptCandidate(
                stable_concept_id=addition.proposed_item_id,
                canonical_name=addition.canonical_name,
                raw_names=addition.raw_names,
                knowledge_type=addition.knowledge_type,
                teaching_depth=addition.teaching_depth,
                evidence_links=tuple(
                    KnowledgeMapEvidenceLink.from_reference(reference)
                    for reference in addition.evidence_refs
                ),
                dependencies=(),
                hints=_repair_hints(bundle, addition),
                confidence=addition.confidence,
                verification_status=ConceptVerificationStatus.VERIFIED_REPAIR_CANDIDATE,
                repair_lineage=(
                    RepairLineage(
                        patch_id=bundle.repair_patch.patch_id,
                        stable_candidate_id=addition.stable_candidate_id,
                        proposed_item_id=addition.proposed_item_id,
                        classification=addition.classification.value,
                        verifier_version=addition.verifier_version,
                    ),
                ),
            )
        )
    return tuple(concepts)


def _merge_group(
    concepts: list[KnowledgeMapConceptCandidate],
    *,
    source_revision: str,
) -> KnowledgeMapConceptCandidate:
    preferred = max(
        concepts,
        key=lambda item: (
            item.confidence,
            len(item.evidence_links),
            canonical_semantic_key(item.canonical_name),
            item.stable_concept_id,
        ),
    )
    preferred_key = canonical_semantic_key(preferred.canonical_name)
    raw_names: dict[str, str] = {}
    for concept in concepts:
        for value in (*concept.raw_names, concept.canonical_name):
            key = canonical_semantic_key(value)
            if key and key != preferred_key:
                raw_names.setdefault(key, value)
    evidence = {
        link.evidence_id: link
        for concept in concepts
        for link in concept.evidence_links
    }
    lineage = {
        (item.patch_id, item.stable_candidate_id, item.proposed_item_id): item
        for concept in concepts
        for item in concept.repair_lineage
    }
    verification_status = (
        ConceptVerificationStatus.VERIFIED_REPAIR_CANDIDATE
        if lineage
        else ConceptVerificationStatus.VERIFIED_COVERED
    )
    evidence_links = tuple(
        sorted(
            evidence.values(),
            key=lambda item: (
                item.part_index,
                item.start_ms,
                item.end_ms,
                item.evidence_id,
            ),
        )
    )
    return KnowledgeMapConceptCandidate(
        stable_concept_id=stable_hash(
            "kmhc",
            {
                "canonical_name": preferred_key,
                "evidence_ids": [item.evidence_id for item in evidence_links],
                "knowledge_type": preferred.knowledge_type.value,
                "source_revision": source_revision,
                "verification_status": verification_status.value,
            },
        ),
        canonical_name=preferred.canonical_name,
        raw_names=tuple(raw_names[key] for key in sorted(raw_names)),
        knowledge_type=preferred.knowledge_type,
        teaching_depth=max(
            (item.teaching_depth for item in concepts),
            key=lambda item: TEACHING_DEPTH_RANK[item],
        ),
        evidence_links=evidence_links,
        dependencies=tuple(sorted({value for item in concepts for value in item.dependencies})),
        hints=tuple(sorted({value for item in concepts for value in item.hints})),
        confidence=max(item.confidence for item in concepts),
        verification_status=verification_status,
        repair_lineage=tuple(lineage[key] for key in sorted(lineage)),
    )


def _merge_duplicate_concepts(
    concepts: Iterable[KnowledgeMapConceptCandidate],
    *,
    source_revision: str,
) -> tuple[KnowledgeMapConceptCandidate, ...]:
    ordered = tuple(concepts)
    BucketKey = tuple[str, str]
    buckets: dict[BucketKey, list[KnowledgeMapConceptCandidate]] = {}
    aliases: dict[BucketKey, set[str]] = {}
    for concept in ordered:
        key = (concept.knowledge_type.value, canonical_semantic_key(concept.canonical_name))
        buckets.setdefault(key, []).append(concept)
        aliases.setdefault(key, set()).update(concept.semantic_forms)

    parent: dict[BucketKey, BucketKey] = {key: key for key in buckets}

    def find(key: BucketKey) -> BucketKey:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: BucketKey, right: BucketKey) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    for key in sorted(buckets):
        knowledge_type, canonical_name = key
        for alias in sorted(aliases[key]):
            other = (knowledge_type, alias)
            if other in buckets and canonical_name in aliases[other]:
                union(key, other)

    components: dict[BucketKey, list[KnowledgeMapConceptCandidate]] = {}
    for key in sorted(buckets):
        components.setdefault(find(key), []).extend(buckets[key])
    merged = [
        _merge_group(group, source_revision=source_revision)
        for _key, group in sorted(components.items())
    ]
    return tuple(
        sorted(
            merged,
            key=lambda item: (
                item.evidence_links[0].part_index,
                item.evidence_links[0].start_ms,
                canonical_semantic_key(item.canonical_name),
                item.stable_concept_id,
            ),
        )
    )


def build_knowledge_map_handoff(
    bundle: VerifiedKnowledgeBundle,
) -> KnowledgeMapHandoffCandidate:
    """Build an endpoint-neutral candidate without touching Knowledge Map runtime state."""

    if bundle.status is PipelineStatus.FAILED:
        raise ValueError("failed pipeline cannot build a Knowledge Map handoff")
    concepts = _merge_duplicate_concepts(
        _provisional_concepts(bundle),
        source_revision=bundle.source_revision,
    )
    handoff_id = stable_hash(
        "kmh",
        {
            "cache_identity": bundle.cache_identity,
            "concept_ids": [item.stable_concept_id for item in concepts],
            "pipeline_receipt_id": bundle.receipt.receipt_id,
            "pipeline_version": PIPELINE_VERSION,
            "verification_status": bundle.status.value,
        },
    )
    return KnowledgeMapHandoffCandidate(
        handoff_id=handoff_id,
        source_identity=bundle.source_identity,
        source_revision=bundle.source_revision,
        verification_status=bundle.status,
        concepts=concepts,
        pipeline_receipt_id=bundle.receipt.receipt_id,
    )
