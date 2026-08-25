from __future__ import annotations

import pytest

from app.knowledge.inventory.models import KnowledgeType, TeachingDepth
from app.knowledge.map_v2.models import (
    KnowledgeEdgeType,
    KnowledgeMapStatus,
    KnowledgeRelationCandidate,
    RelationBasis,
)
from app.knowledge.map_v2.query import KnowledgeMapQuery
from app.knowledge.pipeline_v2.handoff import (
    ConceptVerificationStatus,
    KnowledgeMapConceptCandidate,
)
from test_knowledge_map_v2 import _compile, _concept, _handoff, _known, _relation


def test_duplicate_aliases_merge_evidence_union_without_losing_long_tail() -> None:
    concepts = (
        _concept(
            "cas",
            "CAS",
            aliases=("Compare-And-Swap",),
            evidence_ids=("evidence-cas",),
        ),
        _concept(
            "compare-swap",
            "Compare-And-Swap",
            aliases=("CAS",),
            evidence_ids=("evidence-compare-swap",),
        ),
        _concept(
            "stamped-lock",
            "StampedLock",
            evidence_ids=("evidence-long-tail",),
            knowledge_type=KnowledgeType.API,
        ),
    )
    first = _compile(concepts)
    second = _compile(tuple(reversed(concepts)))

    assert first.status is KnowledgeMapStatus.COMPLETE
    assert len(first.snapshot.nodes) == 2
    merged = next(
        item for item in first.snapshot.nodes if item.knowledge_type is KnowledgeType.CONCEPT
    )
    assert merged.evidence_ids == ("evidence-cas", "evidence-compare-swap")
    assert any(item.canonical_name == "StampedLock" for item in first.snapshot.nodes)
    assert first.snapshot.to_json() == second.snapshot.to_json()
    assert first.snapshot.diagnostics.alias_collisions == ()


def test_alias_collision_is_diagnostic_and_does_not_swallow_concepts() -> None:
    concepts = (
        _concept("alpha", "Alpha", aliases=("Shared",)),
        _concept("beta", "Beta", aliases=("Shared",)),
    )
    relation = _relation("ambiguous", "Shared", "Alpha")
    result = _compile(concepts, relations=(relation,))

    assert result.status is KnowledgeMapStatus.PARTIAL
    assert len(result.snapshot.nodes) == 2
    collision = result.snapshot.diagnostics.alias_collisions[0]
    assert collision.alias == "shared"
    assert len(collision.concept_ids) == 2
    assert result.snapshot.diagnostics.rejected_relations[0].code == "AMBIGUOUS_CONCEPT_REFERENCE"


def test_missing_concept_evidence_is_rejected_by_domain_contract() -> None:
    with pytest.raises(ValueError, match="requires evidence links"):
        KnowledgeMapConceptCandidate(
            stable_concept_id="missing-evidence",
            canonical_name="Missing Evidence",
            raw_names=(),
            knowledge_type=KnowledgeType.CONCEPT,
            teaching_depth=TeachingDepth.CORE_EXPLANATION,
            evidence_links=(),
            dependencies=(),
            hints=(),
            confidence=0.9,
            verification_status=ConceptVerificationStatus.VERIFIED_COVERED,
            repair_lineage=(),
        )


def test_unknown_node_evidence_is_diagnostic_but_unique_node_is_preserved() -> None:
    concepts = (_concept("unknown", "Unknown Evidence Concept"),)
    result = _compile(concepts, known_evidence_ids=())

    assert result.status is KnowledgeMapStatus.PARTIAL
    assert len(result.snapshot.nodes) == 1
    assert result.snapshot.diagnostics.unknown_evidence_ids == ("evidence-unknown",)
    assert result.snapshot.diagnostics.resolution_required is True


def test_direct_self_cycle_retains_edge_and_reports_cycle_evidence() -> None:
    concepts = (_concept("a", "A"),)
    relation = _relation("a-a", "A", "A")
    result = _compile(concepts, relations=(relation,))

    assert result.status is KnowledgeMapStatus.FAILED
    assert len(result.snapshot.edges) == 1
    assert result.snapshot.topological_order == ()
    cycle = result.snapshot.diagnostics.cycles[0]
    assert cycle.cycle_path[0] == cycle.cycle_path[-1]
    assert len(cycle.involved_concept_ids) == 1
    assert cycle.edge_evidence[0].evidence_ids == ("relation-evidence",)
    assert cycle.resolution_required is True


def test_multi_node_cycle_retains_all_edges_and_reports_full_path() -> None:
    concepts = (_concept("a", "A"), _concept("b", "B"), _concept("c", "C"))
    relations = (
        _relation("a-b", "A", "B", evidence_ids=("edge-a-b",)),
        _relation("b-c", "B", "C", evidence_ids=("edge-b-c",)),
        _relation("c-a", "C", "A", evidence_ids=("edge-c-a",)),
    )
    result = _compile(concepts, relations=relations)
    cycle = result.snapshot.diagnostics.cycles[0]

    assert result.status is KnowledgeMapStatus.FAILED
    assert len(result.snapshot.edges) == 3
    assert len(cycle.cycle_path) == 4
    assert cycle.cycle_path[0] == cycle.cycle_path[-1]
    assert len(cycle.involved_concept_ids) == 3
    assert {
        evidence_id
        for edge in cycle.edge_evidence
        for evidence_id in edge.evidence_ids
    } == {"edge-a-b", "edge-b-c", "edge-c-a"}


def test_orphan_and_disconnected_subgraph_are_queryable() -> None:
    concepts = (_concept("a", "A"), _concept("b", "B"), _concept("orphan", "Orphan"))
    result = _compile(concepts, relations=(_relation("a-b", "A", "B"),))
    query = KnowledgeMapQuery(result.snapshot)

    assert result.status is KnowledgeMapStatus.COMPLETE
    assert [item.canonical_name for item in query.orphan_concepts()] == ["Orphan"]
    components = query.disconnected_subgraphs()
    assert len(components) == 2
    assert {tuple(item.canonical_name for item in group) for group in components} == {
        ("A", "B"),
        ("Orphan",),
    }


def test_unsupported_hard_prerequisite_is_rejected_not_downgraded_silently() -> None:
    concepts = (_concept("a", "A"), _concept("b", "B"))
    unsupported = _relation(
        "unsupported-hard",
        "A",
        "B",
        edge_type=KnowledgeEdgeType.PREREQUISITE,
        basis=RelationBasis.CANDIDATE_PREREQUISITE_HINT,
    )
    result = _compile(concepts, relations=(unsupported,))

    assert result.status is KnowledgeMapStatus.PARTIAL
    assert result.snapshot.edges == ()
    rejection = result.snapshot.diagnostics.rejected_relations[0]
    assert rejection.code == "UNSUPPORTED_HARD_PREREQUISITE"
    assert rejection.relation_id == "unsupported-hard"


def test_trusted_existing_prerequisite_hint_can_create_supported_hard_edge() -> None:
    concepts = (_concept("a", "A"), _concept("b", "B"))
    trusted = _relation(
        "trusted-hard",
        "A",
        "B",
        basis=RelationBasis.TRUSTED_EXISTING_PREREQUISITE_HINT,
    )
    result = _compile(concepts, relations=(trusted,))

    assert result.status is KnowledgeMapStatus.COMPLETE
    assert len(result.snapshot.edges) == 1
    assert result.snapshot.edges[0].edge_type is KnowledgeEdgeType.PREREQUISITE
    assert result.snapshot.edges[0].basis is RelationBasis.TRUSTED_EXISTING_PREREQUISITE_HINT


def test_missing_and_unknown_relation_evidence_are_rejected() -> None:
    concepts = (_concept("a", "A"), _concept("b", "B"))
    missing = _relation("missing", "A", "B", evidence_ids=())
    unknown = _relation("unknown", "A", "B", evidence_ids=("unknown-edge-evidence",))
    known = _known(concepts)
    result = _compile(
        concepts,
        relations=(missing, unknown),
        known_evidence_ids=known,
    )
    rejection_by_id = {
        item.relation_id: item.code
        for item in result.snapshot.diagnostics.rejected_relations
    }

    assert result.status is KnowledgeMapStatus.PARTIAL
    assert result.snapshot.edges == ()
    assert rejection_by_id == {
        "missing": "MISSING_RELATION_EVIDENCE",
        "unknown": "UNKNOWN_RELATION_EVIDENCE",
    }
    assert result.snapshot.diagnostics.unknown_evidence_ids == ("unknown-edge-evidence",)


def test_supports_related_and_possible_edges_do_not_pollute_prerequisite_dag() -> None:
    concepts = (_concept("a", "A"), _concept("b", "B"), _concept("c", "C"))
    relations = (
        _relation(
            "a-b-supports",
            "A",
            "B",
            edge_type=KnowledgeEdgeType.SUPPORTS,
        ),
        _relation(
            "b-c-related",
            "B",
            "C",
            edge_type=KnowledgeEdgeType.RELATED,
        ),
        _relation(
            "c-a-possible",
            "C",
            "A",
            edge_type=KnowledgeEdgeType.POSSIBLE_PREREQUISITE,
            basis=RelationBasis.CANDIDATE_PREREQUISITE_HINT,
        ),
    )
    result = _compile(concepts, relations=relations)

    assert result.status is KnowledgeMapStatus.COMPLETE
    assert {item.edge_type for item in result.snapshot.edges} == {
        KnowledgeEdgeType.SUPPORTS,
        KnowledgeEdgeType.RELATED,
        KnowledgeEdgeType.POSSIBLE_PREREQUISITE,
    }
    assert len(result.snapshot.topological_order) == 3
    assert result.snapshot.diagnostics.cycles == ()


def test_one_thousand_unique_concepts_are_not_summary_pruned() -> None:
    concepts = tuple(
        _concept(str(index), f"Unique Concept {index}")
        for index in range(1000)
    )
    result = _compile(concepts)

    assert result.status is KnowledgeMapStatus.COMPLETE
    assert len(result.snapshot.nodes) == 1000
    assert len(result.snapshot.topological_order) == 1000
    assert len({item.concept_id for item in result.snapshot.nodes}) == 1000
    assert any(item.canonical_name == "Unique Concept 999" for item in result.snapshot.nodes)
    assert result.receipt.node_count == 1000
    assert result.snapshot.to_json() == result.snapshot.to_json()
