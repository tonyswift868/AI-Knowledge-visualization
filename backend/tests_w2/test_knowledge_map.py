from __future__ import annotations

import pytest

import app.knowledge_map as knowledge_map_module
from app.knowledge_map import (
    KnowledgeChunk,
    KnowledgeMap,
    KnowledgeMapQuality,
    KnowledgeNode,
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


def _nodes(document: SourceDocument) -> tuple[KnowledgeNode, ...]:
    return tuple(
        knowledge_map_module._node_for_chunk(document, chunk)
        for chunk in document.chunks
    )


def _persisted_map(nodes: tuple[KnowledgeNode, ...]) -> KnowledgeMap:
    active = tuple(node for node in nodes if node.status == "active")
    return KnowledgeMap(
        map_id="km_provider_verified",
        workspace_id="workspace-a",
        revision=1,
        generation_mode="provider_verified",
        provider_used=True,
        source_revisions=tuple(sorted({(node.source_id, 1) for node in nodes})),
        nodes=nodes,
        edges=(),
        quality=KnowledgeMapQuality(
            node_count=len(active),
            edge_count=0,
            sourced_node_ratio=1.0,
            chunk_coverage_ratio=1.0,
            duplicate_node_ratio=0.0,
            orphan_count=max(0, len(active) - 1),
            invalid_prerequisite_count=0,
            superseded_node_count=sum(node.status == "superseded" for node in nodes),
        ),
        fingerprint="provider-verified-fingerprint",
    )


def test_provider_free_builder_fails_closed_even_with_persisted_previous_map() -> None:
    document = _document()
    previous = _persisted_map(_nodes(document))

    with pytest.raises(
        RuntimeError,
        match=r"^PROVIDER_VERIFICATION_REQUIRED: use the canonical knowledge-map jobs API$",
    ):
        build_knowledge_map("workspace-a", [document], previous=previous)


def test_stable_node_ids_are_deterministic_and_source_scoped() -> None:
    first_document = _document("source-a")
    second_document = _document("source-b")
    first = _nodes(first_document)
    repeated = _nodes(first_document)
    second = _nodes(second_document)

    assert [node.node_id for node in first] == [node.node_id for node in repeated]
    assert all(left.node_id != right.node_id for left, right in zip(first, second))
    assert all(reference.source_id == "source-a" for node in first for reference in node.source_refs)


def test_repeated_import_preserves_node_revisions_and_fingerprint_inputs() -> None:
    document = _document()
    first = tuple(knowledge_map_module._merge_same_semantic(_nodes(document))[0])
    repeated = tuple(
        knowledge_map_module._apply_previous(
            knowledge_map_module._merge_same_semantic(_nodes(document))[0],
            _persisted_map(first),
        )
    )
    first_edges = knowledge_map_module._prerequisite_edges([document], first)
    repeated_edges = knowledge_map_module._prerequisite_edges([document], repeated)

    assert repeated == first
    assert knowledge_map_module._fingerprint(
        "workspace-a", [document], first, first_edges
    ) == knowledge_map_module._fingerprint(
        "workspace-a", [document], repeated, repeated_edges
    )


def test_small_edit_preserves_identity_and_records_revision_history() -> None:
    before = _nodes(_document())[0]
    edited_document = _document(
        revision=2,
        first_text="A graph is a set of vertices and directed or undirected edges.",
    )
    candidate = _nodes(edited_document)[0]
    after = knowledge_map_module._apply_previous(
        [candidate], _persisted_map((before,))
    )[0]

    assert before.node_id == after.node_id
    assert after.revision == before.revision + 1
    assert after.prior_content_hashes == (before.content_hash,)
    assert after.prior_summaries == (before.summary,)


def test_removed_semantic_node_is_preserved_as_superseded() -> None:
    previous_nodes = _nodes(_document())
    reduced_nodes = _nodes(_document(revision=2, include_second=False))
    reconciled = knowledge_map_module._apply_previous(
        reduced_nodes, _persisted_map(previous_nodes)
    )

    removed = next(node for node in reconciled if node.label == "Breadth first search")
    assert removed.status == "superseded"
    assert removed.revision == 2
    assert sum(node.status == "active" for node in reconciled) == 1


def test_repeated_heading_merge_is_deterministic_and_keeps_all_evidence() -> None:
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
    merged, duplicate_count = knowledge_map_module._merge_same_semantic(_nodes(document))
    quality = knowledge_map_module._quality([document], merged, (), duplicate_count)

    assert duplicate_count == 1
    assert len(merged) == 1
    assert [reference.chunk_id for reference in merged[0].source_refs] == [
        "chunk-1",
        "chunk-2",
    ]
    assert quality.node_count == 1
    assert quality.duplicate_node_ratio == 0.5
    assert quality.chunk_coverage_ratio == 1.0


def test_prerequisite_edges_keep_page_evidence_and_stable_ids() -> None:
    document = _document()
    nodes = _nodes(document)
    first = knowledge_map_module._prerequisite_edges([document], nodes)
    repeated = knowledge_map_module._prerequisite_edges([document], nodes)

    assert first == repeated
    assert len(first) == 1
    assert first[0].relation == "prerequisite"
    assert {reference.page_start for reference in first[0].source_refs} == {1, 2}


def test_unheaded_chunk_uses_stable_upstream_identity_across_edits() -> None:
    first_document = SourceDocument(
        source_id="source-a",
        title="Plain notes",
        revision=1,
        chunks=(KnowledgeChunk(chunk_id="stable-chunk", ordinal=1, text="Original wording."),),
    )
    second_document = SourceDocument(
        source_id="source-a",
        title="Plain notes",
        revision=2,
        chunks=(KnowledgeChunk(chunk_id="stable-chunk", ordinal=1, text="Slightly edited wording."),),
    )
    before = _nodes(first_document)[0]
    candidate = _nodes(second_document)[0]
    after = knowledge_map_module._apply_previous(
        [candidate], _persisted_map((before,))
    )[0]

    assert after.node_id == before.node_id
    assert after.revision == 2
