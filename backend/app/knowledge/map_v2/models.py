from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..inventory.models import KnowledgeType, canonical_json, canonical_semantic_key
from ..pipeline_v2.handoff import ConceptVerificationStatus, RepairLineage
from ..pipeline_v2.progress import PipelineStatus


KNOWLEDGE_MAP_SCHEMA_VERSION = "mentorflow.verified-knowledge-map.v2"
KNOWLEDGE_MAP_COMPILER_VERSION = "mentorflow.knowledge-map-compiler.v2.1"


class KnowledgeEdgeType(str, Enum):
    PREREQUISITE = "PREREQUISITE"
    SUPPORTS = "SUPPORTS"
    RELATED = "RELATED"
    POSSIBLE_PREREQUISITE = "POSSIBLE_PREREQUISITE"


class RelationBasis(str, Enum):
    VERIFIED_EXPLICIT_RELATION = "VERIFIED_EXPLICIT_RELATION"
    VALIDATED_SOURCE_STRUCTURE = "VALIDATED_SOURCE_STRUCTURE"
    TRUSTED_EXISTING_PREREQUISITE_HINT = "TRUSTED_EXISTING_PREREQUISITE_HINT"
    FUTURE_SEPARATELY_VERIFIED_INFERENCE = "FUTURE_SEPARATELY_VERIFIED_INFERENCE"
    CANDIDATE_PREREQUISITE_HINT = "CANDIDATE_PREREQUISITE_HINT"


HARD_PREREQUISITE_BASES = frozenset(
    {
        RelationBasis.VERIFIED_EXPLICIT_RELATION,
        RelationBasis.VALIDATED_SOURCE_STRUCTURE,
        RelationBasis.TRUSTED_EXISTING_PREREQUISITE_HINT,
        RelationBasis.FUTURE_SEPARATELY_VERIFIED_INFERENCE,
    }
)


class NodeCriticality(str, Enum):
    CRITICAL = "CRITICAL"
    NORMAL = "NORMAL"


class KnowledgeMapStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class KnowledgeMapStage(str, Enum):
    PREFLIGHTED = "PREFLIGHTED"
    COMPILING = "COMPILING"
    VALIDATING = "VALIDATING"
    SERIALIZING = "SERIALIZING"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class KnowledgeNode:
    concept_id: str
    canonical_name: str
    aliases: tuple[str, ...]
    knowledge_type: KnowledgeType
    evidence_ids: tuple[str, ...]
    confidence: float
    verification_status: ConceptVerificationStatus
    repair_lineage: tuple[RepairLineage, ...]
    criticality: NodeCriticality
    effort_lookup_key: str | None
    dependency_hints: tuple[str, ...] = ()
    source_hints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.concept_id.strip() or not canonical_semantic_key(self.canonical_name):
            raise ValueError("knowledge node identity and canonical name are required")
        if not self.evidence_ids:
            raise ValueError("knowledge node requires evidence IDs")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("knowledge node evidence IDs must be unique")
        if not 0 <= self.confidence <= 1:
            raise ValueError("knowledge node confidence must be between 0 and 1")
        if (
            self.verification_status is ConceptVerificationStatus.VERIFIED_REPAIR_CANDIDATE
            and not self.repair_lineage
        ):
            raise ValueError("repair candidate node requires repair lineage")

    @property
    def semantic_forms(self) -> frozenset[str]:
        return frozenset(
            key
            for key in (
                canonical_semantic_key(value)
                for value in (self.canonical_name, *self.aliases)
            )
            if key
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "canonical_name": self.canonical_name,
            "aliases": list(self.aliases),
            "knowledge_type": self.knowledge_type.value,
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
            "verification_status": self.verification_status.value,
            "repair_lineage": [item.to_dict() for item in self.repair_lineage],
            "criticality": self.criticality.value,
            "effort_lookup_key": self.effort_lookup_key,
            "dependency_hints": list(self.dependency_hints),
            "source_hints": list(self.source_hints),
        }


@dataclass(frozen=True)
class KnowledgeRelationCandidate:
    relation_id: str
    source_reference: str
    target_reference: str
    edge_type: KnowledgeEdgeType
    basis: RelationBasis
    evidence_ids: tuple[str, ...]
    confidence: float

    def __post_init__(self) -> None:
        if not self.relation_id.strip():
            raise ValueError("relation_id is required")
        if not self.source_reference.strip() or not self.target_reference.strip():
            raise ValueError("relation endpoints are required")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("relation evidence IDs must be unique")
        if not 0 <= self.confidence <= 1:
            raise ValueError("relation confidence must be between 0 and 1")

    def identity_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "source_reference": self.source_reference,
            "target_reference": self.target_reference,
            "edge_type": self.edge_type.value,
            "basis": self.basis.value,
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class KnowledgeEdge:
    edge_id: str
    source_concept_id: str
    target_concept_id: str
    edge_type: KnowledgeEdgeType
    basis: RelationBasis
    evidence_ids: tuple[str, ...]
    confidence: float

    def __post_init__(self) -> None:
        if not self.edge_id.strip():
            raise ValueError("edge_id is required")
        if not self.source_concept_id.strip() or not self.target_concept_id.strip():
            raise ValueError("edge endpoints are required")
        if not self.evidence_ids:
            raise ValueError("knowledge edge requires evidence IDs")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("knowledge edge evidence IDs must be unique")
        if not 0 <= self.confidence <= 1:
            raise ValueError("edge confidence must be between 0 and 1")
        if (
            self.edge_type is KnowledgeEdgeType.PREREQUISITE
            and self.basis not in HARD_PREREQUISITE_BASES
        ):
            raise ValueError("hard prerequisite basis is not trusted")

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "source_concept_id": self.source_concept_id,
            "target_concept_id": self.target_concept_id,
            "edge_type": self.edge_type.value,
            "basis": self.basis.value,
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class AliasCollision:
    alias: str
    concept_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"alias": self.alias, "concept_ids": list(self.concept_ids)}


@dataclass(frozen=True)
class RelationRejection:
    relation_id: str
    code: str
    source_reference: str
    target_reference: str
    evidence_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "code": self.code,
            "source_reference": self.source_reference,
            "target_reference": self.target_reference,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class UnresolvedRelationHint:
    concept_id: str
    reference: str
    code: str

    def to_dict(self) -> dict[str, str]:
        return {
            "concept_id": self.concept_id,
            "reference": self.reference,
            "code": self.code,
        }


@dataclass(frozen=True)
class CycleEdgeEvidence:
    edge_id: str
    source_concept_id: str
    target_concept_id: str
    evidence_ids: tuple[str, ...]
    basis: RelationBasis

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "source_concept_id": self.source_concept_id,
            "target_concept_id": self.target_concept_id,
            "evidence_ids": list(self.evidence_ids),
            "basis": self.basis.value,
        }


@dataclass(frozen=True)
class CycleDiagnostic:
    cycle_path: tuple[str, ...]
    involved_concept_ids: tuple[str, ...]
    edge_evidence: tuple[CycleEdgeEvidence, ...]
    resolution_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_path": list(self.cycle_path),
            "involved_concept_ids": list(self.involved_concept_ids),
            "edge_evidence": [item.to_dict() for item in self.edge_evidence],
            "resolution_required": self.resolution_required,
        }


@dataclass(frozen=True)
class KnowledgeGraphDiagnostics:
    alias_collisions: tuple[AliasCollision, ...] = ()
    rejected_relations: tuple[RelationRejection, ...] = ()
    unresolved_relation_hints: tuple[UnresolvedRelationHint, ...] = ()
    cycles: tuple[CycleDiagnostic, ...] = ()
    orphan_concept_ids: tuple[str, ...] = ()
    disconnected_subgraphs: tuple[tuple[str, ...], ...] = ()
    missing_evidence_concept_ids: tuple[str, ...] = ()
    unknown_evidence_ids: tuple[str, ...] = ()
    cycle_diagnostics_truncated: bool = False

    @property
    def resolution_required(self) -> bool:
        return bool(
            self.alias_collisions
            or self.rejected_relations
            or self.unresolved_relation_hints
            or self.cycles
            or self.cycle_diagnostics_truncated
            or self.missing_evidence_concept_ids
            or self.unknown_evidence_ids
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "alias_collisions": [item.to_dict() for item in self.alias_collisions],
            "rejected_relations": [item.to_dict() for item in self.rejected_relations],
            "unresolved_relation_hints": [
                item.to_dict() for item in self.unresolved_relation_hints
            ],
            "cycles": [item.to_dict() for item in self.cycles],
            "orphan_concept_ids": list(self.orphan_concept_ids),
            "disconnected_subgraphs": [list(item) for item in self.disconnected_subgraphs],
            "missing_evidence_concept_ids": list(self.missing_evidence_concept_ids),
            "unknown_evidence_ids": list(self.unknown_evidence_ids),
            "cycle_diagnostics_truncated": self.cycle_diagnostics_truncated,
            "resolution_required": self.resolution_required,
        }


@dataclass(frozen=True)
class KnowledgeGraphCandidate:
    candidate_id: str
    source_identity: str
    source_revision: str
    pipeline_status: PipelineStatus
    handoff_id: str
    nodes: tuple[KnowledgeNode, ...]
    edges: tuple[KnowledgeEdge, ...]
    diagnostics: KnowledgeGraphDiagnostics
    compiler_version: str = KNOWLEDGE_MAP_COMPILER_VERSION

    def __post_init__(self) -> None:
        node_ids = [item.concept_id for item in self.nodes]
        edge_ids = [item.edge_id for item in self.edges]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("graph candidate node IDs must be unique")
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("graph candidate edge IDs must be unique")
        node_id_set = set(node_ids)
        dangling = {
            endpoint
            for edge in self.edges
            for endpoint in (edge.source_concept_id, edge.target_concept_id)
            if endpoint not in node_id_set
        }
        if dangling:
            raise ValueError("graph candidate contains dangling edge endpoints")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_identity": self.source_identity,
            "source_revision": self.source_revision,
            "pipeline_status": self.pipeline_status.value,
            "handoff_id": self.handoff_id,
            "compiler_version": self.compiler_version,
            "nodes": [item.to_dict() for item in self.nodes],
            "edges": [item.to_dict() for item in self.edges],
            "diagnostics": self.diagnostics.to_dict(),
        }


@dataclass(frozen=True)
class GraphValidationResult:
    diagnostics: KnowledgeGraphDiagnostics
    topological_order: tuple[str, ...]
    is_prerequisite_dag: bool


@dataclass(frozen=True)
class KnowledgeMapReceipt:
    receipt_id: str
    candidate_id: str
    source_identity: str
    source_revision: str
    handoff_id: str
    pipeline_receipt_id: str
    status: KnowledgeMapStatus
    snapshot_id: str
    node_count: int
    edge_count: int
    hard_prerequisite_count: int
    uncertain_relation_count: int
    cycle_count: int
    diagnostic_count: int
    compiler_version: str = KNOWLEDGE_MAP_COMPILER_VERSION

    def __post_init__(self) -> None:
        for field in (
            "receipt_id",
            "candidate_id",
            "source_identity",
            "source_revision",
            "handoff_id",
            "pipeline_receipt_id",
            "snapshot_id",
        ):
            if not str(getattr(self, field) or "").strip():
                raise ValueError(f"{field} is required")
        if min(
            self.node_count,
            self.edge_count,
            self.hard_prerequisite_count,
            self.uncertain_relation_count,
            self.cycle_count,
            self.diagnostic_count,
        ) < 0:
            raise ValueError("Knowledge Map receipt counts cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "candidate_id": self.candidate_id,
            "source_identity": self.source_identity,
            "source_revision": self.source_revision,
            "handoff_id": self.handoff_id,
            "pipeline_receipt_id": self.pipeline_receipt_id,
            "status": self.status.value,
            "snapshot_id": self.snapshot_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "hard_prerequisite_count": self.hard_prerequisite_count,
            "uncertain_relation_count": self.uncertain_relation_count,
            "cycle_count": self.cycle_count,
            "diagnostic_count": self.diagnostic_count,
            "compiler_version": self.compiler_version,
        }


@dataclass(frozen=True)
class KnowledgeMapSnapshot:
    snapshot_id: str
    source_identity: str
    source_revision: str
    status: KnowledgeMapStatus
    nodes: tuple[KnowledgeNode, ...]
    edges: tuple[KnowledgeEdge, ...]
    topological_order: tuple[str, ...]
    diagnostics: KnowledgeGraphDiagnostics
    receipt: KnowledgeMapReceipt
    schema_version: str = KNOWLEDGE_MAP_SCHEMA_VERSION
    compiler_version: str = KNOWLEDGE_MAP_COMPILER_VERSION

    def __post_init__(self) -> None:
        if self.receipt.snapshot_id != self.snapshot_id or self.receipt.status is not self.status:
            raise ValueError("snapshot and receipt identities or statuses do not match")
        node_ids = {item.concept_id for item in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("snapshot node IDs must be unique")
        edge_ids = {item.edge_id for item in self.edges}
        if len(edge_ids) != len(self.edges):
            raise ValueError("snapshot edge IDs must be unique")
        if any(
            endpoint not in node_ids
            for edge in self.edges
            for endpoint in (edge.source_concept_id, edge.target_concept_id)
        ):
            raise ValueError("snapshot contains dangling edge endpoints")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "compiler_version": self.compiler_version,
            "snapshot_id": self.snapshot_id,
            "source_identity": self.source_identity,
            "source_revision": self.source_revision,
            "status": self.status.value,
            "nodes": [item.to_dict() for item in self.nodes],
            "edges": [item.to_dict() for item in self.edges],
            "topological_order": list(self.topological_order),
            "diagnostics": self.diagnostics.to_dict(),
            "receipt": self.receipt.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @property
    def deterministic_hash_payload(self) -> str:
        return canonical_json(self.to_dict())


@dataclass(frozen=True)
class KnowledgeMapProgress:
    sequence: int
    stage: KnowledgeMapStage
    processed_concepts: int
    total_concepts: int
    processed_relations: int
    total_relations: int
    elapsed_ms: int

    def __post_init__(self) -> None:
        if self.sequence < 0 or self.elapsed_ms < 0:
            raise ValueError("progress sequence and elapsed time cannot be negative")
        if not 0 <= self.processed_concepts <= self.total_concepts:
            raise ValueError("processed concept count is outside the total")
        if not 0 <= self.processed_relations <= self.total_relations:
            raise ValueError("processed relation count is outside the total")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "stage": self.stage.value,
            "processed_concepts": self.processed_concepts,
            "total_concepts": self.total_concepts,
            "processed_relations": self.processed_relations,
            "total_relations": self.total_relations,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True)
class KnowledgeMapApplicationResult:
    status: KnowledgeMapStatus
    candidate: KnowledgeGraphCandidate
    snapshot: KnowledgeMapSnapshot
    receipt: KnowledgeMapReceipt
    progress_history: tuple[KnowledgeMapProgress, ...]

    def __post_init__(self) -> None:
        if self.status is not self.snapshot.status or self.status is not self.receipt.status:
            raise ValueError("application result statuses do not match")
        if not self.progress_history:
            raise ValueError("application result requires progress history")
        if self.progress_history[-1].stage is not KnowledgeMapStage(self.status.value):
            raise ValueError("application result final progress does not match status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "candidate": self.candidate.to_dict(),
            "snapshot": self.snapshot.to_dict(),
            "receipt": self.receipt.to_dict(),
            "progress_history": [item.to_dict() for item in self.progress_history],
        }
