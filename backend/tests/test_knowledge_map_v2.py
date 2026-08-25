from __future__ import annotations

import asyncio
from typing import Any

from app.knowledge.inventory.models import KnowledgeType, TeachingDepth
from app.knowledge.map_v2.compiler import KnowledgeMapCompiler
from app.knowledge.map_v2.models import (
    KnowledgeEdgeType,
    KnowledgeMapStage,
    KnowledgeMapStatus,
    KnowledgeRelationCandidate,
    NodeCriticality,
    RelationBasis,
)
from app.knowledge.map_v2.query import KnowledgeMapQuery
from app.knowledge.pipeline_v2.handoff import (
    ConceptVerificationStatus,
    KnowledgeMapConceptCandidate,
    KnowledgeMapEvidenceLink,
    KnowledgeMapHandoffCandidate,
    RepairLineage,
)
from app.knowledge.pipeline_v2.pipeline import KnowledgePipelineV2
from app.knowledge.pipeline_v2.progress import PipelineStatus
from app.sources.video.transcript_normalizer import TerminologyGlossary
from test_knowledge_inventory import StructuredFixtureProvider, _bundle, _candidate, _metadata
from test_knowledge_pipeline_v2 import StepClock, _covered_response
from test_knowledge_verification import StructuredVerificationFake


def _link(evidence_id: str, ordinal: int = 1) -> KnowledgeMapEvidenceLink:
    return KnowledgeMapEvidenceLink(
        evidence_id=evidence_id,
        part_index=1,
        part_cid="1001",
        start_ms=(ordinal - 1) * 1000,
        end_ms=ordinal * 1000,
        original_evidence_hash=f"hash-{evidence_id}",
    )


def _concept(
    key: str,
    name: str,
    *,
    aliases: tuple[str, ...] = (),
    evidence_ids: tuple[str, ...] | None = None,
    dependencies: tuple[str, ...] = (),
    hints: tuple[str, ...] = (),
    confidence: float = 0.9,
    repair: bool = False,
    knowledge_type: KnowledgeType = KnowledgeType.CONCEPT,
) -> KnowledgeMapConceptCandidate:
    resolved_evidence = evidence_ids or (f"evidence-{key}",)
    lineage = (
        RepairLineage(
            patch_id=f"patch-{key}",
            stable_candidate_id=f"repair-candidate-{key}",
            proposed_item_id=f"upstream-{key}",
            classification="ANCHOR_MISS",
            verifier_version="fixture-verifier-v1",
        ),
    ) if repair else ()
    return KnowledgeMapConceptCandidate(
        stable_concept_id=f"upstream-{key}",
        canonical_name=name,
        raw_names=aliases,
        knowledge_type=knowledge_type,
        teaching_depth=TeachingDepth.CORE_EXPLANATION,
        evidence_links=tuple(
            _link(evidence_id, index)
            for index, evidence_id in enumerate(resolved_evidence, start=1)
        ),
        dependencies=dependencies,
        hints=hints,
        confidence=confidence,
        verification_status=(
            ConceptVerificationStatus.VERIFIED_REPAIR_CANDIDATE
            if repair
            else ConceptVerificationStatus.VERIFIED_COVERED
        ),
        repair_lineage=lineage,
    )


def _handoff(
    concepts: tuple[KnowledgeMapConceptCandidate, ...],
    *,
    status: PipelineStatus = PipelineStatus.COMPLETE,
) -> KnowledgeMapHandoffCandidate:
    return KnowledgeMapHandoffCandidate(
        handoff_id="handoff-fixture-v2",
        source_identity="fixture:knowledge-map",
        source_revision="fixture-revision-v1",
        verification_status=status,
        concepts=concepts,
        pipeline_receipt_id="pipeline-receipt-fixture-v2",
    )


def _relation(
    relation_id: str,
    source: str,
    target: str,
    *,
    edge_type: KnowledgeEdgeType = KnowledgeEdgeType.PREREQUISITE,
    basis: RelationBasis = RelationBasis.VERIFIED_EXPLICIT_RELATION,
    evidence_ids: tuple[str, ...] = ("relation-evidence",),
    confidence: float = 0.95,
) -> KnowledgeRelationCandidate:
    return KnowledgeRelationCandidate(
        relation_id=relation_id,
        source_reference=source,
        target_reference=target,
        edge_type=edge_type,
        basis=basis,
        evidence_ids=evidence_ids,
        confidence=confidence,
    )


def _known(concepts: tuple[KnowledgeMapConceptCandidate, ...], *extra: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                *(link.evidence_id for concept in concepts for link in concept.evidence_links),
                *extra,
            }
        )
    )


def _compile(
    concepts: tuple[KnowledgeMapConceptCandidate, ...],
    *,
    status: PipelineStatus = PipelineStatus.COMPLETE,
    relations: tuple[KnowledgeRelationCandidate, ...] = (),
    known_evidence_ids: tuple[str, ...] | None = None,
    critical_references: tuple[str, ...] = (),
):
    return KnowledgeMapCompiler(clock=StepClock(0.02)).compile_handoff(
        _handoff(concepts, status=status),
        known_evidence_ids=(
            known_evidence_ids
            if known_evidence_ids is not None
            else _known(concepts, *(value for item in relations for value in item.evidence_ids))
        ),
        relations=relations,
        critical_concept_references=critical_references,
    )


def test_verified_bundle_compiles_to_deterministic_prerequisite_dag() -> None:
    evidence = _bundle(["Variables store values.", "Functions use variables."])
    evidence_before = evidence.to_dict()

    def inventory_handler(payload: dict[str, Any], _index: int) -> dict[str, Any]:
        first, second = payload["core_segment_ids"]
        return {
            "batch_id": payload["batch_id"],
            "items": [
                _candidate("variables", "Variables", [first]),
                _candidate("functions", "Functions", [second]),
            ],
        }

    pipeline_result = asyncio.run(
        KnowledgePipelineV2(
            StructuredFixtureProvider(inventory_handler),
            StructuredVerificationFake(_covered_response),
            clock=StepClock(),
        ).run(
            evidence,
            TerminologyGlossary(()),
            source_metadata=_metadata(evidence),
        )
    )
    relation = _relation(
        "variables-before-functions",
        "Variables",
        "Functions",
        evidence_ids=tuple(segment.segment_id for segment in evidence.segments),
    )
    progress = []
    result = KnowledgeMapCompiler(clock=StepClock()).compile(
        pipeline_result.verified_bundle,
        relations=(relation,),
        progress_sink=progress.append,
    )

    assert evidence.to_dict() == evidence_before
    assert result.status is KnowledgeMapStatus.COMPLETE
    assert len(result.snapshot.nodes) == 2
    assert len(result.snapshot.edges) == 1
    assert result.snapshot.edges[0].edge_type is KnowledgeEdgeType.PREREQUISITE
    assert result.snapshot.topological_order == tuple(
        node.concept_id
        for name in ("Variables", "Functions")
        for node in result.snapshot.nodes
        if node.canonical_name == name
    )
    assert [item.stage for item in progress] == [
        KnowledgeMapStage.PREFLIGHTED,
        KnowledgeMapStage.COMPILING,
        KnowledgeMapStage.VALIDATING,
        KnowledgeMapStage.SERIALIZING,
        KnowledgeMapStage.COMPLETE,
    ]
    assert result.snapshot.to_json() == result.snapshot.to_json()


def test_query_runtime_supports_roots_leaves_relatives_uncertain_critical_and_effort() -> None:
    concepts = (
        _concept("a", "A"),
        _concept("b", "B"),
        _concept("c", "C"),
        _concept("d", "D"),
    )
    relations = (
        _relation("a-b", "A", "B"),
        _relation("b-c", "B", "C"),
        _relation(
            "a-d-possible",
            "A",
            "D",
            edge_type=KnowledgeEdgeType.POSSIBLE_PREREQUISITE,
            basis=RelationBasis.CANDIDATE_PREREQUISITE_HINT,
        ),
    )
    result = _compile(concepts, relations=relations, critical_references=("A",))
    query = KnowledgeMapQuery(result.snapshot)

    assert [item.canonical_name for item in query.all_concepts()] == ["A", "B", "C", "D"]
    assert {item.canonical_name for item in query.roots()} == {"A", "D"}
    assert {item.canonical_name for item in query.leaves()} == {"C", "D"}
    assert [item.canonical_name for item in query.prerequisites_of("C")] == ["A", "B"]
    assert [item.canonical_name for item in query.dependents_of("A")] == ["B", "C"]
    assert len(query.unverified_or_uncertain_relations()) == 1
    assert [item.canonical_name for item in query.critical_concepts()] == ["A"]

    class Effort:
        def lookup_effort(self, effort_lookup_key: str) -> float | None:
            assert effort_lookup_key.startswith("kmn-")
            return 2.5

    assert query.effort_for("A", Effort()) == 2.5


def test_candidate_dependency_is_possible_not_hard_prerequisite() -> None:
    concepts = (
        _concept("a", "A"),
        _concept("b", "B", dependencies=("A",)),
    )
    result = _compile(concepts)

    assert result.status is KnowledgeMapStatus.COMPLETE
    assert len(result.snapshot.edges) == 1
    edge = result.snapshot.edges[0]
    assert edge.edge_type is KnowledgeEdgeType.POSSIBLE_PREREQUISITE
    assert edge.basis is RelationBasis.CANDIDATE_PREREQUISITE_HINT
    assert len(result.snapshot.topological_order) == 2
    assert len(KnowledgeMapQuery(result.snapshot).unverified_or_uncertain_relations()) == 1


def test_partial_pipeline_input_cannot_become_complete_map() -> None:
    concepts = (_concept("partial", "Partial Concept"),)
    result = _compile(concepts, status=PipelineStatus.PARTIAL)

    assert result.status is KnowledgeMapStatus.PARTIAL
    assert result.snapshot.status is KnowledgeMapStatus.PARTIAL
    assert result.receipt.status is KnowledgeMapStatus.PARTIAL
    assert result.progress_history[-1].stage is KnowledgeMapStage.PARTIAL


def test_input_order_variation_has_identical_snapshot_serialization() -> None:
    concepts = (
        _concept("a", "A"),
        _concept("b", "B"),
        _concept("c", "C"),
    )
    relations = (
        _relation("a-b", "A", "B"),
        _relation("b-c", "B", "C"),
    )
    first = _compile(concepts, relations=relations)
    second = _compile(tuple(reversed(concepts)), relations=tuple(reversed(relations)))

    assert first.candidate.candidate_id == second.candidate.candidate_id
    assert first.snapshot.snapshot_id == second.snapshot.snapshot_id
    assert first.snapshot.to_json() == second.snapshot.to_json()


def test_repair_lineage_and_criticality_are_preserved() -> None:
    concepts = (_concept("repair", "Recovered Critical Concept", repair=True),)
    result = _compile(concepts, critical_references=("upstream-repair",))
    node = result.snapshot.nodes[0]

    assert node.verification_status is ConceptVerificationStatus.VERIFIED_REPAIR_CANDIDATE
    assert node.repair_lineage == concepts[0].repair_lineage
    assert node.criticality is NodeCriticality.CRITICAL
    assert node.evidence_ids == ("evidence-repair",)
