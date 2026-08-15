from __future__ import annotations

from copy import deepcopy

from app.generic_knowledge import merge_provider_batches


def test_global_merge_preserves_same_name_nodes_and_deduplicates_each_nodes_chunks() -> None:
    nodes = [
        {
            "local_id": "batch-1-a",
            "name": "Graph Search",
            "source_chunk_ids": ["chunk-1", "chunk-1"],
        },
        {
            "local_id": "batch-2-a",
            "name": " graph_search ",
            "source_chunk_ids": ["chunk-2", "chunk-1"],
        },
        {
            "local_id": "batch-2-b",
            "name": "Shortest Path",
            "source_chunk_ids": ["chunk-3"],
        },
    ]
    edges = [
        {
            "prerequisite_id": "batch-2-a",
            "dependent_id": "batch-2-b",
            "reason": "search before shortest paths",
        }
    ]
    original_nodes = deepcopy(nodes)
    original_edges = deepcopy(edges)

    merged, remapped, warnings = merge_provider_batches(nodes, edges)

    assert nodes == original_nodes
    assert edges == original_edges
    assert [item["local_id"] for item in merged] == [
        "batch-1-a",
        "batch-2-a",
        "batch-2-b",
    ]
    assert merged[0]["source_chunk_ids"] == ["chunk-1"]
    assert merged[1]["source_chunk_ids"] == ["chunk-2", "chunk-1"]
    assert remapped == [
        {
            "prerequisite_id": "batch-2-a",
            "dependent_id": "batch-2-b",
            "reason": "search before shortest paths",
        }
    ]
    assert warnings == []


def test_global_merge_sanitizes_ids_and_leaves_cycles_for_the_quality_gate() -> None:
    nodes = [
        {"local_id": "a-1", "name": "A", "source_chunk_ids": ["c1"]},
        {"local_id": "a-2", "name": " a ", "source_chunk_ids": ["c2"]},
        {"local_id": "b", "name": "B", "source_chunk_ids": ["c3"]},
        {"local_id": "c", "name": "C", "source_chunk_ids": ["c4"]},
    ]
    edges = [
        {"prerequisite_id": "a-1", "dependent_id": "a-2"},
        {"prerequisite_id": "a-2", "dependent_id": "b"},
        {"prerequisite_id": "a-1", "dependent_id": "b"},
        {"prerequisite_id": "b", "dependent_id": "c"},
        {"prerequisite_id": "c", "dependent_id": "a-1"},
        {"prerequisite_id": "missing", "dependent_id": "c"},
    ]

    merged, accepted, warnings = merge_provider_batches(nodes, edges)

    assert [item["local_id"] for item in merged] == ["a-1", "a-2", "b", "c"]
    assert [
        (edge["prerequisite_id"], edge["dependent_id"]) for edge in accepted
    ] == [
        ("a-1", "a-2"),
        ("a-2", "b"),
        ("a-1", "b"),
        ("b", "c"),
        ("c", "a-1"),
    ]
    assert any("悬空依赖边" in warning for warning in warnings)


def test_global_merge_does_not_aggregate_evidence_by_normalized_name() -> None:
    evidence_1 = {"chunk_id": "c1", "char_start": 0, "char_end": 10}
    evidence_2 = {"chunk_id": "c2", "char_start": 10, "char_end": 20}
    nodes = [
        {"local_id": "x1", "name": "事务 原子性", "source_evidence": [evidence_1]},
        {
            "local_id": "x2",
            "name": "事务原子性",
            "source_evidence": [evidence_1, evidence_2],
        },
    ]

    merged, edges, _warnings = merge_provider_batches(nodes, [])

    assert edges == []
    assert len(merged) == 2
    assert merged[0]["source_evidence"] == [evidence_1]
    assert merged[1]["source_evidence"] == [evidence_1, evidence_2]
