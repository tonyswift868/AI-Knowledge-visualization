from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Iterable, Sequence


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _stable_id(*parts: str, prefix: str) -> str:
    payload = "\x1f".join(_normalise(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _display_label(chunk: "KnowledgeChunk") -> str:
    if chunk.heading.strip():
        return chunk.heading.strip()
    for line in chunk.text.splitlines():
        candidate = line.strip().lstrip("#").strip()
        if candidate:
            return candidate[:120]
    return f"Section {chunk.ordinal}"


def _semantic_key(chunk: "KnowledgeChunk") -> str:
    # Headings are the preferred semantic anchor. Stable upstream chunk IDs are
    # the fallback, so a small content edit does not silently create a new node.
    return _normalise(chunk.heading) or f"chunk:{_normalise(chunk.chunk_id)}"


@dataclass(frozen=True)
class SourceReference:
    source_id: str
    chunk_id: str
    content_hash: str
    quote: str
    heading: str = ""
    page_start: int | None = None
    page_end: int | None = None


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    ordinal: int
    text: str
    heading: str = ""
    page_start: int | None = None
    page_end: int | None = None

    def __post_init__(self) -> None:
        if not self.chunk_id.strip():
            raise ValueError("chunk_id is required")
        if self.ordinal <= 0:
            raise ValueError("chunk ordinal must be positive")
        if not self.text.strip():
            raise ValueError("chunk text is required")


@dataclass(frozen=True)
class SourceDocument:
    source_id: str
    title: str
    revision: int
    chunks: tuple[KnowledgeChunk, ...]
    source_hash: str = ""

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id is required")
        if not self.title.strip():
            raise ValueError("source title is required")
        if self.revision <= 0:
            raise ValueError("source revision must be positive")
        if not self.chunks:
            raise ValueError("source must contain at least one chunk")
        chunk_ids = [chunk.chunk_id for chunk in self.chunks]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("chunk_id must be unique within a source")


@dataclass(frozen=True)
class KnowledgeNode:
    node_id: str
    source_id: str
    semantic_key: str
    label: str
    summary: str
    kind: str
    revision: int
    status: str
    content_hash: str
    source_refs: tuple[SourceReference, ...]
    prior_content_hashes: tuple[str, ...] = ()
    prior_summaries: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnowledgeEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    relation: str
    source_refs: tuple[SourceReference, ...]


@dataclass(frozen=True)
class KnowledgeMapQuality:
    node_count: int
    edge_count: int
    sourced_node_ratio: float
    chunk_coverage_ratio: float
    duplicate_node_ratio: float
    orphan_count: int
    invalid_prerequisite_count: int
    superseded_node_count: int


@dataclass(frozen=True)
class KnowledgeMap:
    map_id: str
    workspace_id: str
    revision: int
    generation_mode: str
    provider_used: bool
    source_revisions: tuple[tuple[str, int], ...]
    nodes: tuple[KnowledgeNode, ...]
    edges: tuple[KnowledgeEdge, ...]
    quality: KnowledgeMapQuality
    fingerprint: str
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _reference(document: SourceDocument, chunk: KnowledgeChunk) -> SourceReference:
    return SourceReference(
        source_id=document.source_id,
        chunk_id=chunk.chunk_id,
        content_hash=_content_hash(chunk.text),
        quote=chunk.text.strip()[:500],
        heading=chunk.heading.strip(),
        page_start=chunk.page_start,
        page_end=chunk.page_end,
    )


def _node_for_chunk(document: SourceDocument, chunk: KnowledgeChunk) -> KnowledgeNode:
    semantic_key = _semantic_key(chunk)
    node_id = _stable_id(document.source_id, semantic_key, prefix="kn")
    clean_text = re.sub(r"\s+", " ", chunk.text).strip()
    return KnowledgeNode(
        node_id=node_id,
        source_id=document.source_id,
        semantic_key=semantic_key,
        label=_display_label(chunk),
        summary=clean_text[:600],
        kind="concept",
        revision=1,
        status="active",
        content_hash=_content_hash(clean_text),
        source_refs=(_reference(document, chunk),),
    )


def _merge_same_semantic(nodes: Sequence[KnowledgeNode]) -> tuple[list[KnowledgeNode], int]:
    merged: dict[str, KnowledgeNode] = {}
    duplicate_count = 0
    for node in nodes:
        existing = merged.get(node.node_id)
        if not existing:
            merged[node.node_id] = node
            continue
        duplicate_count += 1
        refs = {reference.chunk_id: reference for reference in (*existing.source_refs, *node.source_refs)}
        summaries = [item for item in (existing.summary, node.summary) if item]
        merged[node.node_id] = replace(
            existing,
            summary="\n".join(dict.fromkeys(summaries))[:600],
            source_refs=tuple(sorted(refs.values(), key=lambda item: item.chunk_id)),
            content_hash=_content_hash("\n".join(dict.fromkeys(summaries))),
        )
    return sorted(merged.values(), key=lambda item: (item.source_id, item.semantic_key)), duplicate_count


def _apply_previous(
    current: Sequence[KnowledgeNode],
    previous: KnowledgeMap | None,
) -> list[KnowledgeNode]:
    if previous is None:
        return list(current)
    old = {node.node_id: node for node in previous.nodes}
    output: list[KnowledgeNode] = []
    current_ids = {node.node_id for node in current}
    for node in current:
        prior = old.get(node.node_id)
        if prior is None:
            output.append(node)
        elif prior.content_hash == node.content_hash and prior.status == "active":
            output.append(
                replace(
                    node,
                    revision=prior.revision,
                    prior_content_hashes=prior.prior_content_hashes,
                    prior_summaries=prior.prior_summaries,
                )
            )
        else:
            history = tuple(dict.fromkeys((*prior.prior_content_hashes, prior.content_hash)))
            summaries = tuple(dict.fromkeys((*prior.prior_summaries, prior.summary)))
            output.append(
                replace(
                    node,
                    revision=prior.revision + 1,
                    prior_content_hashes=history,
                    prior_summaries=summaries,
                )
            )
    for prior in previous.nodes:
        if prior.node_id not in current_ids and prior.status != "superseded":
            output.append(replace(prior, revision=prior.revision + 1, status="superseded"))
        elif prior.node_id not in current_ids:
            output.append(prior)
    return sorted(output, key=lambda item: (item.source_id, item.semantic_key, item.status))


def _prerequisite_edges(
    documents: Sequence[SourceDocument],
    active_nodes: Sequence[KnowledgeNode],
) -> list[KnowledgeEdge]:
    by_key = {(node.source_id, node.semantic_key): node for node in active_nodes if node.status == "active"}
    edges: dict[str, KnowledgeEdge] = {}
    for document in documents:
        ordered: list[KnowledgeNode] = []
        for chunk in sorted(document.chunks, key=lambda item: (item.ordinal, item.chunk_id)):
            node = by_key.get((document.source_id, _semantic_key(chunk)))
            if node and (not ordered or ordered[-1].node_id != node.node_id):
                ordered.append(node)
        for source, target in zip(ordered, ordered[1:]):
            edge_id = _stable_id(source.node_id, target.node_id, "prerequisite", prefix="ke")
            refs = tuple(
                sorted(
                    {item.chunk_id: item for item in (*source.source_refs, *target.source_refs)}.values(),
                    key=lambda item: item.chunk_id,
                )
            )
            edges[edge_id] = KnowledgeEdge(
                edge_id=edge_id,
                source_node_id=source.node_id,
                target_node_id=target.node_id,
                relation="prerequisite",
                source_refs=refs,
            )
    return sorted(edges.values(), key=lambda item: item.edge_id)


def _quality(
    documents: Sequence[SourceDocument],
    nodes: Sequence[KnowledgeNode],
    edges: Sequence[KnowledgeEdge],
    duplicate_count: int,
) -> KnowledgeMapQuality:
    active = [node for node in nodes if node.status == "active"]
    active_ids = {node.node_id for node in active}
    covered_chunks = {
        (reference.source_id, reference.chunk_id)
        for node in active
        for reference in node.source_refs
    }
    all_chunks = {
        (document.source_id, chunk.chunk_id)
        for document in documents
        for chunk in document.chunks
    }
    invalid = sum(
        edge.relation == "prerequisite"
        and (edge.source_node_id not in active_ids or edge.target_node_id not in active_ids)
        for edge in edges
    )
    connected = {
        node_id
        for edge in edges
        for node_id in (edge.source_node_id, edge.target_node_id)
        if node_id in active_ids
    }
    orphan_count = sum(node.node_id not in connected for node in active) if len(active) > 1 else 0
    return KnowledgeMapQuality(
        node_count=len(active),
        edge_count=len(edges),
        sourced_node_ratio=round(sum(bool(node.source_refs) for node in active) / max(1, len(active)), 4),
        chunk_coverage_ratio=round(len(covered_chunks & all_chunks) / max(1, len(all_chunks)), 4),
        duplicate_node_ratio=round(duplicate_count / max(1, len(active) + duplicate_count), 4),
        orphan_count=orphan_count,
        invalid_prerequisite_count=int(invalid),
        superseded_node_count=sum(node.status == "superseded" for node in nodes),
    )


def _fingerprint(
    workspace_id: str,
    documents: Sequence[SourceDocument],
    nodes: Sequence[KnowledgeNode],
    edges: Sequence[KnowledgeEdge],
) -> str:
    payload = "|".join(
        [
            _normalise(workspace_id),
            *[
                f"{document.source_id}:{document.revision}:{document.source_hash or _content_hash(''.join(chunk.text for chunk in document.chunks))}"
                for document in documents
            ],
            *[f"{node.node_id}:{node.revision}:{node.status}:{node.content_hash}" for node in nodes],
            *[f"{edge.edge_id}:{edge.source_node_id}:{edge.target_node_id}:{edge.relation}" for edge in edges],
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_knowledge_map(
    workspace_id: str,
    documents: Iterable[SourceDocument],
    *,
    previous: KnowledgeMap | None = None,
) -> KnowledgeMap:
    """Fail closed: v1 generation is owned by the canonical Provider job API.

    The remaining types and readers in this module support old persisted maps.
    Keeping a callable Provider-free builder would allow a future route to
    accidentally recreate an unverified structural map, so the legacy write
    path is intentionally disabled.
    """

    del workspace_id, documents, previous
    raise RuntimeError(
        "PROVIDER_VERIFICATION_REQUIRED: use the canonical knowledge-map jobs API"
    )
