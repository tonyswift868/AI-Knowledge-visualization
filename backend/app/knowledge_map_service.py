"""Persistence adapter for the deterministic knowledge-map core."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from typing import Any

from sqlalchemy import select

from . import db
from .knowledge_map import (
    KnowledgeChunk,
    KnowledgeEdge,
    KnowledgeMap,
    KnowledgeMapQuality,
    KnowledgeNode,
    SourceDocument,
    SourceReference,
    build_knowledge_map,
)
from .orm import (
    KnowledgeEdgeORM,
    KnowledgeMapRevisionORM,
    KnowledgeNodeORM,
    SessionLocal,
    SourceRevisionORM,
    init_orm,
)


class KnowledgeMapServiceError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _reference(payload: dict[str, Any]) -> SourceReference:
    return SourceReference(**payload)


def _load_map(session, record: KnowledgeMapRevisionORM | None) -> KnowledgeMap | None:
    if record is None:
        return None
    nodes = session.scalars(
        select(KnowledgeNodeORM)
        .where(KnowledgeNodeORM.map_revision_id == record.id)
        .order_by(KnowledgeNodeORM.node_id)
    ).all()
    edges = session.scalars(
        select(KnowledgeEdgeORM)
        .where(KnowledgeEdgeORM.map_revision_id == record.id)
        .order_by(KnowledgeEdgeORM.edge_id)
    ).all()
    return KnowledgeMap(
        map_id=record.map_id,
        workspace_id=record.workspace_id,
        revision=record.revision,
        generation_mode=record.generation_mode,
        provider_used=record.provider_used,
        source_revisions=tuple((str(item[0]), int(item[1])) for item in record.source_revisions),
        nodes=tuple(
            KnowledgeNode(
                node_id=item.node_id,
                source_id=item.source_id,
                semantic_key=item.semantic_key,
                label=item.label,
                summary=item.summary,
                kind=item.kind,
                revision=item.revision,
                status=item.status,
                content_hash=item.content_hash,
                source_refs=tuple(_reference(value) for value in item.source_refs),
                prior_content_hashes=tuple(item.history.get("prior_content_hashes", [])),
                prior_summaries=tuple(item.history.get("prior_summaries", [])),
            )
            for item in nodes
        ),
        edges=tuple(
            KnowledgeEdge(
                edge_id=item.edge_id,
                source_node_id=item.source_node_id,
                target_node_id=item.target_node_id,
                relation=item.relation,
                source_refs=tuple(_reference(value) for value in item.source_refs),
            )
            for item in edges
        ),
        quality=KnowledgeMapQuality(**record.quality),
        fingerprint=record.fingerprint,
        warnings=tuple(record.warnings),
    )


def _current_record(session, workspace_id: str) -> KnowledgeMapRevisionORM | None:
    return session.scalar(
        select(KnowledgeMapRevisionORM)
        .where(
            KnowledgeMapRevisionORM.workspace_id == workspace_id,
            KnowledgeMapRevisionORM.is_current.is_(True),
        )
        .order_by(KnowledgeMapRevisionORM.revision.desc())
    )


def _documents(session, workspace_id: str) -> tuple[tuple[SourceDocument, ...], list[SourceRevisionORM]]:
    sources = [
        item
        for item in db.rows("sources", "workspace_id", workspace_id)
        if item.get("status") in {"ready", "container"} and item.get("source_type") != "archive"
    ]
    documents: list[SourceDocument] = []
    pending_revisions: list[SourceRevisionORM] = []
    for source in sources:
        chunks = db.rows("chunks", "source_id", source["id"])
        if not chunks:
            continue
        source_hash = str(source.get("content_hash") or "")
        if not source_hash:
            source_hash = sha256(
                "\x1f".join(str(item.get("content_hash") or item.get("text") or "") for item in chunks).encode("utf-8")
            ).hexdigest()
        latest = session.scalar(
            select(SourceRevisionORM)
            .where(SourceRevisionORM.source_id == source["id"])
            .order_by(SourceRevisionORM.revision.desc())
        )
        if latest and latest.source_hash == source_hash:
            revision = latest.revision
        else:
            revision = 1 if latest is None else latest.revision + 1
            pending_revisions.append(
                SourceRevisionORM(
                    id=f"sr:{source['id']}:r{revision}",
                    workspace_id=workspace_id,
                    source_id=source["id"],
                    revision=revision,
                    source_hash=source_hash,
                    status="ready",
                )
            )
        knowledge_chunks = tuple(
            KnowledgeChunk(
                chunk_id=item["id"],
                ordinal=max(1, int(item.get("ordinal") or 1)),
                text=item["text"],
                heading=item.get("heading") or "",
                page_start=item.get("page_start", item.get("page")),
                page_end=item.get("page_end", item.get("page")),
            )
            for item in chunks
            if str(item.get("text") or "").strip()
        )
        if not knowledge_chunks:
            continue
        documents.append(
            SourceDocument(
                source_id=source["id"],
                title=source["title"],
                revision=revision,
                source_hash=source_hash,
                chunks=knowledge_chunks,
            )
        )
    if not documents:
        raise KnowledgeMapServiceError("KNOWLEDGE_MAP_NO_SOURCES", "没有可用于生成知识地图的有效资料", 409)
    return tuple(documents), pending_revisions


def generate_current_map(workspace_id: str) -> tuple[KnowledgeMap, bool]:
    init_orm()
    with SessionLocal() as session:
        previous_record = _current_record(session, workspace_id)
        previous = _load_map(session, previous_record)
        documents, pending_revisions = _documents(session, workspace_id)
        generated = build_knowledge_map(workspace_id, documents, previous=previous)
        if previous is not None and generated.fingerprint == previous.fingerprint:
            return previous, False

        record_id = f"{generated.map_id}:r{generated.revision}"
        for item in pending_revisions:
            session.add(item)
        if previous_record is not None:
            previous_record.is_current = False
        session.add(
            KnowledgeMapRevisionORM(
                id=record_id,
                map_id=generated.map_id,
                workspace_id=workspace_id,
                revision=generated.revision,
                is_current=True,
                generation_mode=generated.generation_mode,
                provider_used=generated.provider_used,
                source_revisions=[list(item) for item in generated.source_revisions],
                quality=asdict(generated.quality),
                fingerprint=generated.fingerprint,
                warnings=list(generated.warnings),
            )
        )
        for node in generated.nodes:
            session.add(
                KnowledgeNodeORM(
                    id=f"{record_id}:{node.node_id}",
                    map_revision_id=record_id,
                    node_id=node.node_id,
                    source_id=node.source_id,
                    semantic_key=node.semantic_key,
                    label=node.label,
                    summary=node.summary,
                    kind=node.kind,
                    revision=node.revision,
                    status=node.status,
                    content_hash=node.content_hash,
                    source_refs=[asdict(item) for item in node.source_refs],
                    history={
                        "prior_content_hashes": list(node.prior_content_hashes),
                        "prior_summaries": list(node.prior_summaries),
                    },
                )
            )
        for edge in generated.edges:
            session.add(
                KnowledgeEdgeORM(
                    id=f"{record_id}:{edge.edge_id}",
                    map_revision_id=record_id,
                    edge_id=edge.edge_id,
                    source_node_id=edge.source_node_id,
                    target_node_id=edge.target_node_id,
                    relation=edge.relation,
                    source_refs=[asdict(item) for item in edge.source_refs],
                )
            )
        session.commit()
        return generated, True


def get_current_map(workspace_id: str) -> KnowledgeMap:
    init_orm()
    with SessionLocal() as session:
        current = _load_map(session, _current_record(session, workspace_id))
        if current is None:
            raise KnowledgeMapServiceError("KNOWLEDGE_MAP_NOT_FOUND", "知识地图尚未生成", 404)
        return current


def get_map_revision(workspace_id: str, revision: int) -> KnowledgeMap:
    init_orm()
    with SessionLocal() as session:
        record = session.scalar(
            select(KnowledgeMapRevisionORM).where(
                KnowledgeMapRevisionORM.workspace_id == workspace_id,
                KnowledgeMapRevisionORM.revision == revision,
            )
        )
        result = _load_map(session, record)
        if result is None:
            raise KnowledgeMapServiceError("KNOWLEDGE_MAP_NOT_FOUND", "知识地图版本不存在", 404)
        return result
