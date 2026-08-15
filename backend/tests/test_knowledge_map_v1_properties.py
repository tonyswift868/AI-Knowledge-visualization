from __future__ import annotations

import random

import pytest

from app.knowledge_map_v1 import (
    evidence_fingerprint,
    provider_payload_model,
    reduce_planning_graph_v1,
    validate_graph_v1,
    validate_topic_concept_structure_v1,
)


def test_evidence_fingerprint_is_order_independent_but_revision_exact():
    first = {
        "material_id": "material-a",
        "source_id": "source-a",
        "source_revision_id": "revision-1",
        "chunk_id": "chunk-a",
        "char_start": 4,
        "char_end": 17,
        "quote": "random sentinel",
        "adapter_name": "text",
        "adapter_version": "1.0",
    }
    second = {
        **first,
        "material_id": "material-b",
        "source_id": "source-b",
        "chunk_id": "chunk-b",
    }
    assert evidence_fingerprint([first, second]) == evidence_fingerprint(
        [second, first]
    )
    assert evidence_fingerprint([first]) != evidence_fingerprint(
        [{**first, "source_revision_id": "revision-2"}]
    )
    assert evidence_fingerprint([first]) != evidence_fingerprint(
        [{**first, "quote": "changed random sentinel"}]
    )


def test_random_confirmed_prerequisite_dags_remain_acyclic():
    for seed in range(100):
        generator = random.Random(seed)
        node_count = generator.randint(2, 20)
        nodes = [{"id": f"n-{index}"} for index in range(node_count)]
        edges = [
            {
                "id": f"e-{source}-{target}",
                "source_id": f"n-{source}",
                "target_id": f"n-{target}",
                "relation": "prerequisite",
            }
            for source in range(node_count)
            for target in range(source + 1, node_count)
            if generator.random() < 0.15
        ]

        gate = validate_graph_v1(nodes, edges)
        assert gate["acyclic"] is True
        assert gate["node_count"] == node_count
        assert gate["edge_count"] == len(edges)


def test_random_cycles_are_rejected_when_relationships_are_confirmed():
    for seed in range(50):
        generator = random.Random(seed)
        node_count = generator.randint(2, 15)
        nodes = [{"id": f"n-{index}"} for index in range(node_count)]
        edges = [
            {
                "id": f"chain-{index}",
                "source_id": f"n-{index}",
                "target_id": f"n-{index + 1}",
                "relation": "prerequisite",
            }
            for index in range(node_count - 1)
        ]
        edges.append(
            {
                "id": "closing-edge",
                "source_id": f"n-{node_count - 1}",
                "target_id": "n-0",
                "relation": "prerequisite",
            }
        )

        with pytest.raises(ValueError, match="cycle"):
            validate_graph_v1(nodes, edges)


@pytest.mark.parametrize(
    "edge",
    [
        {"source_id": "n-0", "target_id": "missing", "relation": "prerequisite"},
        {"source_id": "n-0", "target_id": "n-0", "relation": "prerequisite"},
        {"source_id": "n-0", "target_id": "n-1", "relation": "unregistered"},
    ],
)
def test_dangling_self_loop_and_unregistered_relationships_fail_closed(edge):
    with pytest.raises(ValueError):
        validate_graph_v1([{"id": "n-0"}, {"id": "n-1"}], [edge])


def test_material_hierarchy_requires_evidence_backed_theme_contains_concept():
    nodes = [
        {"id": "theme", "node_type": "theme", "layer": "material"},
        {"id": "concept", "node_type": "concept", "layer": "material"},
    ]
    result = validate_topic_concept_structure_v1(
        nodes,
        [
            {
                "source_id": "theme",
                "target_id": "concept",
                "relation": "contains",
                "layer": "material",
            }
        ],
    )
    assert result == {
        "material_theme_count": 1,
        "material_concept_count": 1,
        "material_contains_count": 1,
    }

    with pytest.raises(ValueError, match="contains"):
        validate_topic_concept_structure_v1(nodes, [])


def test_material_edges_require_their_own_evidence_and_common_items_cannot_claim_it():
    base_nodes = [
        {
            "id": "theme",
            "name": "Theme",
            "node_type": "theme",
            "source_chunk_ids": ["chunk-1"],
        },
        {
            "id": "concept",
            "name": "Concept",
            "node_type": "concept",
            "source_chunk_ids": ["chunk-1"],
        },
    ]
    with pytest.raises(ValueError, match="Provider payload"):
        provider_payload_model(
            {
                "knowledge_points": base_nodes,
                "dependencies": [
                    {
                        "prerequisite_id": "theme",
                        "dependent_id": "concept",
                        "relation": "contains",
                        "layer": "material",
                        "source_chunk_ids": [],
                    }
                ],
            }
        )

    with pytest.raises(ValueError, match="Provider payload"):
        provider_payload_model(
            {
                "knowledge_points": [
                    *base_nodes,
                    {
                        "id": "common",
                        "name": "Common",
                        "layer": "model_common",
                        "source_chunk_ids": ["chunk-1"],
                    },
                ],
                "dependencies": [],
            }
        )


def test_planning_graph_uses_stable_topology_and_transitive_reduction():
    result = reduce_planning_graph_v1(
        ["a", "b", "c"],
        [
            {"source_id": "a", "target_id": "b", "relation": "prerequisite"},
            {"source_id": "b", "target_id": "c", "relation": "prerequisite"},
            {"source_id": "a", "target_id": "c", "relation": "prerequisite"},
        ],
    )
    assert result["order"] == ["a", "b", "c"]
    assert result["edges"] == [
        {"source_id": "a", "target_id": "b"},
        {"source_id": "b", "target_id": "c"},
    ]
    assert result["original_edge_count"] == 3
    assert result["reduced_edge_count"] == 2
