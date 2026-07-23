from __future__ import annotations

from app.knowledge_map import (
    KnowledgeChunk,
    SourceDocument,
    build_knowledge_map,
)


def _document(
    source_id: str = "source-a",
    *,
    revision: int = 1,
    first_text: str = "A graph is a set of vertices and edges.",
    include_second: bool = True,
) -> SourceDocument:
    chunks = [
        KnowledgeChunk(
            chunk_id="chunk-graph",
            ordinal=1,
            heading="Graph model",
            text=first_text,
            page_start=1,
            page_end=1,
        )
    ]
    if include_second:
        chunks.append(
            KnowledgeChunk(
                chunk_id="chunk-bfs",
                ordinal=2,
                heading="Breadth first search",
                text="BFS explores a graph one level at a time.",
                page_start=2,
                page_end=2,
            )
        )
    return SourceDocument(
        source_id=source_id,
        title="Graph handbook",
        revision=revision,
        chunks=tuple(chunks),
    )


def test_deterministic_fallback_builds_sourced_nodes_and_prerequisites() -> None:
    result = build_knowledge_map("workspace-a", [_document()])

    assert result.generation_mode == "deterministic_fallback"
    assert result.provider_used is False
    assert result.revision == 1
    assert result.quality.node_count == 2
    assert result.quality.edge_count == 1
    assert result.quality.sourced_node_ratio == 1
    assert result.quality.chunk_coverage_ratio == 1
    assert result.quality.invalid_prerequisite_count == 0
    assert result.quality.orphan_count == 0
    assert result.edges[0].relation == "prerequisite"
    assert {reference.page_start for reference in result.edges[0].source_refs} == {1, 2}
    assert all(node.source_refs[0].source_id == "source-a" for node in result.nodes)


def test_stable_ids_are_source_scoped() -> None:
    first = build_knowledge_map("workspace-a", [_document("source-a")])
    second = build_knowledge_map("workspace-a", [_document("source-b")])

    first_labels = {node.label: node.node_id for node in first.nodes}
    second_labels = {node.label: node.node_id for node in second.nodes}
    assert first_labels.keys() == second_labels.keys()
    assert all(first_labels[label] != second_labels[label] for label in first_labels)


def test_repeated_import_is_idempotent() -> None:
    first = build_knowledge_map("workspace-a", [_document()])
    repeated = build_knowledge_map("workspace-a", [_document()], previous=first)

    assert repeated == first
    assert repeated.revision == 1
    assert repeated.fingerprint == first.fingerprint
    assert [node.node_id for node in repeated.nodes] == [node.node_id for node in first.nodes]


def test_small_edit_preserves_semantic_identity_and_records_revision_history() -> None:
    first = build_knowledge_map("workspace-a", [_document()])
    edited = build_knowledge_map(
        "workspace-a",
        [
            _document(
                revision=2,
                first_text="A graph is a set of vertices and directed or undirected edges.",
            )
        ],
        previous=first,
    )

    before = next(node for node in first.nodes if node.label == "Graph model")
    after = next(node for node in edited.nodes if node.label == "Graph model")
    unchanged_before = next(node for node in first.nodes if node.label == "Breadth first search")
    unchanged_after = next(node for node in edited.nodes if node.label == "Breadth first search")

    assert edited.revision == 2
    assert before.node_id == after.node_id
    assert after.revision == before.revision + 1
    assert before.content_hash in after.prior_content_hashes
    assert before.summary in after.prior_summaries
    assert unchanged_after.node_id == unchanged_before.node_id
    assert unchanged_after.revision == unchanged_before.revision


def test_removed_semantic_node_is_preserved_as_superseded() -> None:
    first = build_knowledge_map("workspace-a", [_document()])
    reduced = build_knowledge_map(
        "workspace-a",
        [_document(revision=2, include_second=False)],
        previous=first,
    )

    removed = next(node for node in reduced.nodes if node.label == "Breadth first search")
    assert removed.status == "superseded"
    assert removed.revision == 2
    assert reduced.quality.node_count == 1
    assert reduced.quality.superseded_node_count == 1
    assert reduced.quality.invalid_prerequisite_count == 0


def test_repeated_heading_merges_deterministically_and_reports_quality() -> None:
    document = SourceDocument(
        source_id="source-a",
        title="Repeated notes",
        revision=1,
        chunks=(
            KnowledgeChunk(
                chunk_id="chunk-1",
                ordinal=1,
                heading="Queue",
                text="A queue is FIFO.",
            ),
            KnowledgeChunk(
                chunk_id="chunk-2",
                ordinal=2,
                heading="Queue",
                text="A queue supports enqueue and dequeue.",
            ),
        ),
    )
    result = build_knowledge_map("workspace-a", [document])

    assert result.quality.node_count == 1
    assert result.quality.duplicate_node_ratio == 0.5
    assert result.quality.chunk_coverage_ratio == 1
    assert len(result.nodes[0].source_refs) == 2
    assert "Repeated semantic headings" in result.warnings[0]


def test_unheaded_chunk_uses_stable_upstream_chunk_identity() -> None:
    first_doc = SourceDocument(
        source_id="source-a",
        title="Plain notes",
        revision=1,
        chunks=(KnowledgeChunk(chunk_id="stable-chunk", ordinal=1, text="Original wording."),),
    )
    second_doc = SourceDocument(
        source_id="source-a",
        title="Plain notes",
        revision=2,
        chunks=(KnowledgeChunk(chunk_id="stable-chunk", ordinal=1, text="Slightly edited wording."),),
    )
    first = build_knowledge_map("workspace-a", [first_doc])
    second = build_knowledge_map("workspace-a", [second_doc], previous=first)

    assert second.nodes[0].node_id == first.nodes[0].node_id
    assert second.nodes[0].revision == 2
