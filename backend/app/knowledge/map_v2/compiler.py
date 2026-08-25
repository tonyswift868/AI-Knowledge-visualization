from __future__ import annotations

import time
from dataclasses import replace
from typing import Callable, Iterable, Mapping

from ..inventory.models import canonical_semantic_key, stable_hash
from ..pipeline_v2.handoff import (
    ConceptVerificationStatus,
    KnowledgeMapConceptCandidate,
    KnowledgeMapHandoffCandidate,
    RepairLineage,
    build_knowledge_map_handoff,
)
from ..pipeline_v2.models import VerifiedKnowledgeBundle
from ..pipeline_v2.progress import PipelineStatus
from .models import (
    HARD_PREREQUISITE_BASES,
    AliasCollision,
    KnowledgeEdge,
    KnowledgeEdgeType,
    KnowledgeGraphCandidate,
    KnowledgeGraphDiagnostics,
    KnowledgeMapApplicationResult,
    KnowledgeMapProgress,
    KnowledgeMapReceipt,
    KnowledgeMapSnapshot,
    KnowledgeMapStage,
    KnowledgeMapStatus,
    KnowledgeNode,
    KnowledgeRelationCandidate,
    NodeCriticality,
    RelationBasis,
    RelationRejection,
    UnresolvedRelationHint,
    KNOWLEDGE_MAP_COMPILER_VERSION,
)
from .validation import validate_graph


ProgressSink = Callable[[KnowledgeMapProgress], None]
Clock = Callable[[], float]


_BASIS_RANK = {
    RelationBasis.VERIFIED_EXPLICIT_RELATION: 5,
    RelationBasis.VALIDATED_SOURCE_STRUCTURE: 4,
    RelationBasis.TRUSTED_EXISTING_PREREQUISITE_HINT: 3,
    RelationBasis.FUTURE_SEPARATELY_VERIFIED_INFERENCE: 2,
    RelationBasis.CANDIDATE_PREREQUISITE_HINT: 1,
}


def _lineage_key(item: RepairLineage) -> tuple[str, str, str]:
    return item.patch_id, item.stable_candidate_id, item.proposed_item_id


def _merge_concepts(
    handoff: KnowledgeMapHandoffCandidate,
    *,
    critical_references: frozenset[str],
) -> tuple[tuple[KnowledgeNode, ...], dict[str, str]]:
    concepts = tuple(handoff.concepts)
    BucketKey = tuple[str, str]
    buckets: dict[BucketKey, list[KnowledgeMapConceptCandidate]] = {}
    aliases: dict[BucketKey, set[str]] = {}
    for concept in concepts:
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

    nodes: list[KnowledgeNode] = []
    upstream_to_node: dict[str, str] = {}
    for _component_key, group in sorted(components.items()):
        preferred = max(
            group,
            key=lambda item: (
                item.confidence,
                len(item.evidence_links),
                canonical_semantic_key(item.canonical_name),
                item.stable_concept_id,
            ),
        )
        canonical_keys = sorted(
            {
                canonical_semantic_key(item.canonical_name)
                for item in group
                if canonical_semantic_key(item.canonical_name)
            }
        )
        identity_name = canonical_keys[0]
        concept_id = stable_hash(
            "kmn",
            {
                "canonical_identity": identity_name,
                "knowledge_type": preferred.knowledge_type.value,
                "source_identity": handoff.source_identity,
            },
        )
        preferred_key = canonical_semantic_key(preferred.canonical_name)
        aliases_by_key: dict[str, str] = {}
        for item in group:
            upstream_to_node[item.stable_concept_id] = concept_id
            for value in (*item.raw_names, item.canonical_name):
                key = canonical_semantic_key(value)
                if key and key != preferred_key:
                    aliases_by_key.setdefault(key, value)
        evidence_ids = tuple(
            sorted(
                {
                    link.evidence_id
                    for item in group
                    for link in item.evidence_links
                }
            )
        )
        lineage_by_key = {
            _lineage_key(lineage): lineage
            for item in group
            for lineage in item.repair_lineage
        }
        all_reference_forms = {
            item.stable_concept_id
            for item in group
        } | {
            canonical_semantic_key(value)
            for item in group
            for value in (item.canonical_name, *item.raw_names)
            if canonical_semantic_key(value)
        }
        critical = bool(all_reference_forms & critical_references)
        node = KnowledgeNode(
            concept_id=concept_id,
            canonical_name=preferred.canonical_name,
            aliases=tuple(aliases_by_key[key] for key in sorted(aliases_by_key)),
            knowledge_type=preferred.knowledge_type,
            evidence_ids=evidence_ids,
            confidence=max(item.confidence for item in group),
            verification_status=(
                ConceptVerificationStatus.VERIFIED_REPAIR_CANDIDATE
                if lineage_by_key
                else ConceptVerificationStatus.VERIFIED_COVERED
            ),
            repair_lineage=tuple(lineage_by_key[key] for key in sorted(lineage_by_key)),
            criticality=NodeCriticality.CRITICAL if critical else NodeCriticality.NORMAL,
            effort_lookup_key=concept_id,
            dependency_hints=tuple(
                sorted({value for item in group for value in item.dependencies})
            ),
            source_hints=tuple(sorted({value for item in group for value in item.hints})),
        )
        nodes.append(node)
    return (
        tuple(
            sorted(
                nodes,
                key=lambda item: (
                    item.knowledge_type.value,
                    canonical_semantic_key(item.canonical_name),
                    item.concept_id,
                ),
            )
        ),
        upstream_to_node,
    )


def _reference_index(
    nodes: tuple[KnowledgeNode, ...],
    upstream_to_node: Mapping[str, str],
) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for node in nodes:
        index.setdefault(node.concept_id, set()).add(node.concept_id)
        for value in (node.canonical_name, *node.aliases):
            key = canonical_semantic_key(value)
            if key:
                index.setdefault(key, set()).add(node.concept_id)
    for upstream_id, node_id in upstream_to_node.items():
        index.setdefault(upstream_id, set()).add(node_id)
    return index


def _alias_collisions(index: Mapping[str, set[str]]) -> tuple[AliasCollision, ...]:
    return tuple(
        AliasCollision(alias=key, concept_ids=tuple(sorted(values)))
        for key, values in sorted(index.items())
        if len(values) > 1 and not key.startswith(("kmn-", "kmhc-"))
    )


def _resolve_reference(
    reference: str,
    index: Mapping[str, set[str]],
) -> tuple[str | None, str | None]:
    raw = str(reference or "").strip()
    matches = index.get(raw)
    if matches is None:
        matches = index.get(canonical_semantic_key(raw), set())
    if not matches:
        return None, "UNKNOWN_CONCEPT_REFERENCE"
    if len(matches) > 1:
        return None, "AMBIGUOUS_CONCEPT_REFERENCE"
    return next(iter(matches)), None


def _edge(
    relation: KnowledgeRelationCandidate,
    source_id: str,
    target_id: str,
) -> KnowledgeEdge:
    evidence_ids = tuple(sorted(set(relation.evidence_ids)))
    return KnowledgeEdge(
        edge_id=stable_hash(
            "kme",
            {
                "basis": relation.basis.value,
                "edge_type": relation.edge_type.value,
                "evidence_ids": evidence_ids,
                "source_concept_id": source_id,
                "target_concept_id": target_id,
            },
        ),
        source_concept_id=source_id,
        target_concept_id=target_id,
        edge_type=relation.edge_type,
        basis=relation.basis,
        evidence_ids=evidence_ids,
        confidence=relation.confidence,
    )


def _merge_edges(edges: Iterable[KnowledgeEdge]) -> tuple[KnowledgeEdge, ...]:
    groups: dict[tuple[str, str, KnowledgeEdgeType], list[KnowledgeEdge]] = {}
    for edge in edges:
        groups.setdefault(
            (edge.source_concept_id, edge.target_concept_id, edge.edge_type),
            [],
        ).append(edge)
    merged = []
    for (source_id, target_id, edge_type), group in sorted(
        groups.items(),
        key=lambda item: (item[0][0], item[0][1], item[0][2].value),
    ):
        preferred = max(
            group,
            key=lambda item: (
                _BASIS_RANK[item.basis],
                item.confidence,
                item.edge_id,
            ),
        )
        evidence_ids = tuple(sorted({value for item in group for value in item.evidence_ids}))
        merged.append(
            KnowledgeEdge(
                edge_id=stable_hash(
                    "kme",
                    {
                        "basis": preferred.basis.value,
                        "edge_type": edge_type.value,
                        "evidence_ids": evidence_ids,
                        "source_concept_id": source_id,
                        "target_concept_id": target_id,
                    },
                ),
                source_concept_id=source_id,
                target_concept_id=target_id,
                edge_type=edge_type,
                basis=preferred.basis,
                evidence_ids=evidence_ids,
                confidence=max(item.confidence for item in group),
            )
        )
    return tuple(merged)


def _status(
    pipeline_status: PipelineStatus,
    diagnostics: KnowledgeGraphDiagnostics,
) -> KnowledgeMapStatus:
    if diagnostics.cycles or diagnostics.cycle_diagnostics_truncated:
        return KnowledgeMapStatus.FAILED
    if pipeline_status is PipelineStatus.PARTIAL or diagnostics.resolution_required:
        return KnowledgeMapStatus.PARTIAL
    return KnowledgeMapStatus.COMPLETE


class KnowledgeMapCompiler:
    def __init__(self, *, clock: Clock = time.perf_counter) -> None:
        self.clock = clock

    def compile(
        self,
        bundle: VerifiedKnowledgeBundle,
        *,
        handoff: KnowledgeMapHandoffCandidate | None = None,
        relations: Iterable[KnowledgeRelationCandidate] = (),
        critical_concept_references: Iterable[str] = (),
        progress_sink: ProgressSink | None = None,
    ) -> KnowledgeMapApplicationResult:
        resolved_handoff = handoff or build_knowledge_map_handoff(bundle)
        critical = {
            addition.proposed_item_id
            for addition in bundle.repair_patch.additions
            if addition.critical
        } | {
            addition.canonical_name
            for addition in bundle.repair_patch.additions
            if addition.critical
        } | set(critical_concept_references)
        return self.compile_handoff(
            resolved_handoff,
            known_evidence_ids=(segment.segment_id for segment in bundle.evidence.segments),
            relations=relations,
            critical_concept_references=critical,
            progress_sink=progress_sink,
        )

    def compile_handoff(
        self,
        handoff: KnowledgeMapHandoffCandidate,
        *,
        known_evidence_ids: Iterable[str],
        relations: Iterable[KnowledgeRelationCandidate] = (),
        critical_concept_references: Iterable[str] = (),
        progress_sink: ProgressSink | None = None,
    ) -> KnowledgeMapApplicationResult:
        relation_items = tuple(relations)
        known_evidence = frozenset(known_evidence_ids)
        critical_items = tuple(critical_concept_references)
        critical_references = frozenset(
            {
                raw
                for value in critical_items
                if (raw := str(value or "").strip())
            }
            | {
                canonical_semantic_key(value)
                for value in critical_items
                if canonical_semantic_key(value)
            }
        )
        if handoff.verification_status not in {
            PipelineStatus.COMPLETE,
            PipelineStatus.PARTIAL,
        }:
            raise ValueError("Knowledge Map compilation requires a final pipeline status")
        started = self.clock()
        progress: list[KnowledgeMapProgress] = []
        dependency_relation_count = sum(len(item.dependencies) for item in handoff.concepts)
        total_relations = len(relation_items) + dependency_relation_count

        def emit(
            stage: KnowledgeMapStage,
            *,
            processed_concepts: int,
            processed_relations: int,
        ) -> None:
            item = KnowledgeMapProgress(
                sequence=len(progress),
                stage=stage,
                processed_concepts=processed_concepts,
                total_concepts=len(handoff.concepts),
                processed_relations=processed_relations,
                total_relations=total_relations,
                elapsed_ms=max(0, int((self.clock() - started) * 1000)),
            )
            progress.append(item)
            if progress_sink is not None:
                progress_sink(item)

        emit(KnowledgeMapStage.PREFLIGHTED, processed_concepts=0, processed_relations=0)
        nodes, upstream_to_node = _merge_concepts(
            handoff,
            critical_references=critical_references,
        )
        emit(
            KnowledgeMapStage.COMPILING,
            processed_concepts=len(handoff.concepts),
            processed_relations=0,
        )
        index = _reference_index(nodes, upstream_to_node)
        collisions = _alias_collisions(index)
        missing_evidence = tuple(sorted(item.concept_id for item in nodes if not item.evidence_ids))
        unknown_evidence = {
            evidence_id
            for item in nodes
            for evidence_id in item.evidence_ids
            if evidence_id not in known_evidence
        }
        edges: list[KnowledgeEdge] = []
        rejections: list[RelationRejection] = []
        unresolved: list[UnresolvedRelationHint] = []

        for node in nodes:
            for dependency in node.dependency_hints:
                source_id, error = _resolve_reference(dependency, index)
                if error is not None:
                    unresolved.append(
                        UnresolvedRelationHint(
                            concept_id=node.concept_id,
                            reference=dependency,
                            code=error,
                        )
                    )
                    continue
                hint_evidence = tuple(
                    value for value in node.evidence_ids if value in known_evidence
                )
                if not hint_evidence:
                    unresolved.append(
                        UnresolvedRelationHint(
                            concept_id=node.concept_id,
                            reference=dependency,
                            code="UNKNOWN_HINT_EVIDENCE",
                        )
                    )
                    continue
                relation = KnowledgeRelationCandidate(
                    relation_id=stable_hash(
                        "kmr-hint",
                        {
                            "dependency": dependency,
                            "source": source_id,
                            "target": node.concept_id,
                        },
                    ),
                    source_reference=source_id or dependency,
                    target_reference=node.concept_id,
                    edge_type=KnowledgeEdgeType.POSSIBLE_PREREQUISITE,
                    basis=RelationBasis.CANDIDATE_PREREQUISITE_HINT,
                    evidence_ids=hint_evidence,
                    confidence=node.confidence,
                )
                edges.append(_edge(relation, source_id or dependency, node.concept_id))

        for relation in sorted(
            relation_items,
            key=lambda item: (item.relation_id, canonical_semantic_key(item.source_reference)),
        ):
            source_id, source_error = _resolve_reference(relation.source_reference, index)
            target_id, target_error = _resolve_reference(relation.target_reference, index)
            code = source_error or target_error
            if code is None and not relation.evidence_ids:
                code = "MISSING_RELATION_EVIDENCE"
            relation_unknown = tuple(
                sorted(set(relation.evidence_ids) - known_evidence)
            )
            if code is None and relation_unknown:
                code = "UNKNOWN_RELATION_EVIDENCE"
                unknown_evidence.update(relation_unknown)
            if (
                code is None
                and relation.edge_type is KnowledgeEdgeType.PREREQUISITE
                and relation.basis not in HARD_PREREQUISITE_BASES
            ):
                code = "UNSUPPORTED_HARD_PREREQUISITE"
            if code is not None or source_id is None or target_id is None:
                rejections.append(
                    RelationRejection(
                        relation_id=relation.relation_id,
                        code=code or "UNRESOLVED_RELATION",
                        source_reference=relation.source_reference,
                        target_reference=relation.target_reference,
                        evidence_ids=relation.evidence_ids,
                    )
                )
                continue
            edges.append(_edge(relation, source_id, target_id))

        compiled_edges = _merge_edges(edges)
        preliminary = KnowledgeGraphDiagnostics(
            alias_collisions=collisions,
            rejected_relations=tuple(
                sorted(rejections, key=lambda item: (item.relation_id, item.code))
            ),
            unresolved_relation_hints=tuple(
                sorted(
                    unresolved,
                    key=lambda item: (item.concept_id, item.reference, item.code),
                )
            ),
            missing_evidence_concept_ids=missing_evidence,
            unknown_evidence_ids=tuple(sorted(unknown_evidence)),
        )
        candidate_id = stable_hash(
            "kmgc",
            {
                "compiler_version": KNOWLEDGE_MAP_COMPILER_VERSION,
                "edges": [item.to_dict() for item in compiled_edges],
                "nodes": [item.to_dict() for item in nodes],
                "pipeline_status": handoff.verification_status.value,
                "source_identity": handoff.source_identity,
                "source_revision": handoff.source_revision,
            },
        )
        candidate = KnowledgeGraphCandidate(
            candidate_id=candidate_id,
            source_identity=handoff.source_identity,
            source_revision=handoff.source_revision,
            pipeline_status=handoff.verification_status,
            handoff_id=handoff.handoff_id,
            nodes=nodes,
            edges=compiled_edges,
            diagnostics=preliminary,
        )
        emit(
            KnowledgeMapStage.VALIDATING,
            processed_concepts=len(handoff.concepts),
            processed_relations=total_relations,
        )
        validation = validate_graph(candidate)
        candidate = replace(candidate, diagnostics=validation.diagnostics)
        status = _status(handoff.verification_status, validation.diagnostics)
        emit(
            KnowledgeMapStage.SERIALIZING,
            processed_concepts=len(handoff.concepts),
            processed_relations=total_relations,
        )
        snapshot_id = stable_hash(
            "kms",
            {
                "candidate_id": candidate.candidate_id,
                "diagnostics": validation.diagnostics.to_dict(),
                "status": status.value,
                "topological_order": validation.topological_order,
            },
        )
        diagnostic_count = (
            len(validation.diagnostics.alias_collisions)
            + len(validation.diagnostics.rejected_relations)
            + len(validation.diagnostics.unresolved_relation_hints)
            + len(validation.diagnostics.cycles)
            + len(validation.diagnostics.missing_evidence_concept_ids)
            + len(validation.diagnostics.unknown_evidence_ids)
        )
        receipt_id = stable_hash(
            "kmr",
            {
                "candidate_id": candidate.candidate_id,
                "pipeline_receipt_id": handoff.pipeline_receipt_id,
                "snapshot_id": snapshot_id,
                "status": status.value,
            },
        )
        receipt = KnowledgeMapReceipt(
            receipt_id=receipt_id,
            candidate_id=candidate.candidate_id,
            source_identity=handoff.source_identity,
            source_revision=handoff.source_revision,
            handoff_id=handoff.handoff_id,
            pipeline_receipt_id=handoff.pipeline_receipt_id,
            status=status,
            snapshot_id=snapshot_id,
            node_count=len(nodes),
            edge_count=len(compiled_edges),
            hard_prerequisite_count=sum(
                edge.edge_type is KnowledgeEdgeType.PREREQUISITE
                for edge in compiled_edges
            ),
            uncertain_relation_count=sum(
                edge.edge_type is KnowledgeEdgeType.POSSIBLE_PREREQUISITE
                for edge in compiled_edges
            ),
            cycle_count=len(validation.diagnostics.cycles),
            diagnostic_count=diagnostic_count,
        )
        snapshot = KnowledgeMapSnapshot(
            snapshot_id=snapshot_id,
            source_identity=handoff.source_identity,
            source_revision=handoff.source_revision,
            status=status,
            nodes=nodes,
            edges=compiled_edges,
            topological_order=validation.topological_order,
            diagnostics=validation.diagnostics,
            receipt=receipt,
        )
        final_stage = KnowledgeMapStage(status.value)
        emit(
            final_stage,
            processed_concepts=len(handoff.concepts),
            processed_relations=total_relations,
        )
        return KnowledgeMapApplicationResult(
            status=status,
            candidate=candidate,
            snapshot=snapshot,
            receipt=receipt,
            progress_history=tuple(progress),
        )
