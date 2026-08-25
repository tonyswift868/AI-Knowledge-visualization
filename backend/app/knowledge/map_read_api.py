from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fastapi import APIRouter, HTTPException

from .inventory.models import canonical_semantic_key
from .map_read_demo import (
    JAVA_DATABASE_DEMO_ALIAS,
    JAVA_DATABASE_DEMO_SOURCE,
    build_java_database_demo_snapshot,
)
from .map_v2.models import KnowledgeMapSnapshot, KnowledgeMapStatus, KnowledgeNode
from .map_v2.query import KnowledgeMapQuery


KNOWLEDGE_MAP_READ_API_VERSION = "mentorflow.knowledge-map-read-api.v1"
CYCLE_USER_MESSAGE = "发现知识依赖冲突，需要检查关系"
PARTIAL_USER_MESSAGE = "资料尚未完全验证"
FAILED_USER_MESSAGE = "知识图验证失败，不能作为完整知识图显示"


class KnowledgeMapSnapshotPort(Protocol):
    def get_snapshot(self, map_reference: str) -> KnowledgeMapSnapshot | None: ...


@dataclass(frozen=True)
class InMemoryKnowledgeMapSnapshotPort:
    snapshots: tuple[KnowledgeMapSnapshot, ...]
    aliases: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        snapshot_ids = tuple(item.snapshot_id for item in self.snapshots)
        alias_names = tuple(item[0] for item in self.aliases)
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("snapshot port IDs must be unique")
        if len(alias_names) != len(set(alias_names)):
            raise ValueError("snapshot port aliases must be unique")

    def get_snapshot(self, map_reference: str) -> KnowledgeMapSnapshot | None:
        alias_map = dict(self.aliases)
        resolved = alias_map.get(map_reference, map_reference)
        return next(
            (item for item in self.snapshots if item.snapshot_id == resolved),
            None,
        )


def demo_snapshot_port() -> InMemoryKnowledgeMapSnapshotPort:
    snapshot = build_java_database_demo_snapshot()
    return InMemoryKnowledgeMapSnapshotPort(
        snapshots=(snapshot,),
        aliases=((JAVA_DATABASE_DEMO_ALIAS, snapshot.snapshot_id),),
    )


def _display_projection(snapshot: KnowledgeMapSnapshot) -> dict[str, object]:
    has_cycle = bool(
        snapshot.diagnostics.cycles
        or snapshot.diagnostics.cycle_diagnostics_truncated
    )
    renderable = snapshot.status is not KnowledgeMapStatus.FAILED and not has_cycle
    if has_cycle:
        message = CYCLE_USER_MESSAGE
        display_state = "CONFLICT"
    elif snapshot.status is KnowledgeMapStatus.PARTIAL:
        message = PARTIAL_USER_MESSAGE
        display_state = "PARTIAL"
    elif snapshot.status is KnowledgeMapStatus.FAILED:
        message = FAILED_USER_MESSAGE
        display_state = "FAILED"
    elif not snapshot.nodes:
        message = "当前没有可显示的知识节点"
        display_state = "EMPTY"
    else:
        message = "知识图已完成验证"
        display_state = "READY"
    return {
        "renderable": renderable,
        "display_state": display_state,
        "user_message": message,
        "is_partial": snapshot.status is KnowledgeMapStatus.PARTIAL,
        "is_demo": snapshot.source_identity == JAVA_DATABASE_DEMO_SOURCE,
        "demo_label": (
            "DEMO DATA"
            if snapshot.source_identity == JAVA_DATABASE_DEMO_SOURCE
            else None
        ),
    }


def _node_summary(node: KnowledgeNode) -> dict[str, object]:
    return {
        "concept_id": node.concept_id,
        "canonical_name": node.canonical_name,
        "confidence": node.confidence,
        "verification_status": node.verification_status.value,
        "criticality": node.criticality.value,
    }


class KnowledgeMapReadService:
    def __init__(self, snapshots: KnowledgeMapSnapshotPort) -> None:
        self.snapshots = snapshots

    def _snapshot(self, map_reference: str) -> KnowledgeMapSnapshot:
        snapshot = self.snapshots.get_snapshot(map_reference)
        if snapshot is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "KNOWLEDGE_MAP_NOT_FOUND",
                    "message": "没有找到指定的知识图",
                },
            )
        return snapshot

    def _queryable(self, map_reference: str) -> tuple[KnowledgeMapSnapshot, KnowledgeMapQuery]:
        snapshot = self._snapshot(map_reference)
        display = _display_projection(snapshot)
        if not display["renderable"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "KNOWLEDGE_MAP_NOT_RENDERABLE",
                    "message": display["user_message"],
                    "display_state": display["display_state"],
                },
            )
        return snapshot, KnowledgeMapQuery(snapshot)

    @staticmethod
    def _resolve_node(
        snapshot: KnowledgeMapSnapshot,
        concept_reference: str,
    ) -> KnowledgeNode:
        raw = concept_reference.strip()
        exact = next(
            (item for item in snapshot.nodes if item.concept_id == raw),
            None,
        )
        if exact is not None:
            return exact
        key = canonical_semantic_key(raw)
        matches = tuple(
            item
            for item in snapshot.nodes
            if key
            in {
                canonical_semantic_key(value)
                for value in (item.canonical_name, *item.aliases)
            }
        )
        if not matches:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "KNOWLEDGE_CONCEPT_NOT_FOUND",
                    "message": "没有找到指定的知识概念",
                },
            )
        if len(matches) > 1:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "KNOWLEDGE_CONCEPT_REFERENCE_AMBIGUOUS",
                    "message": "知识概念名称存在歧义，请使用 concept ID",
                },
            )
        return matches[0]

    def snapshot(self, map_reference: str) -> dict[str, object]:
        snapshot = self._snapshot(map_reference)
        return {
            "api_version": KNOWLEDGE_MAP_READ_API_VERSION,
            "snapshot": snapshot.to_dict(),
            "display": _display_projection(snapshot),
        }

    def concepts(self, map_reference: str, kind: str) -> dict[str, object]:
        snapshot, query = self._queryable(map_reference)
        selectors = {
            "roots": query.roots,
            "leaves": query.leaves,
            "critical": query.critical_concepts,
            "orphans": query.orphan_concepts,
        }
        selected = selectors[kind]()
        return {
            "snapshot_id": snapshot.snapshot_id,
            "kind": kind,
            "items": [_node_summary(item) for item in selected],
        }

    def relatives(
        self,
        map_reference: str,
        concept_reference: str,
        kind: str,
    ) -> dict[str, object]:
        snapshot, query = self._queryable(map_reference)
        try:
            selected = (
                query.prerequisites_of(concept_reference)
                if kind == "prerequisites"
                else query.dependents_of(concept_reference)
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "KNOWLEDGE_CONCEPT_NOT_FOUND",
                    "message": "没有找到指定的知识概念",
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "KNOWLEDGE_CONCEPT_REFERENCE_AMBIGUOUS",
                    "message": "知识概念名称存在歧义，请使用 concept ID",
                },
            ) from exc
        return {
            "snapshot_id": snapshot.snapshot_id,
            "concept_reference": concept_reference,
            "kind": kind,
            "items": [_node_summary(item) for item in selected],
        }

    def concept_detail(
        self,
        map_reference: str,
        concept_reference: str,
    ) -> dict[str, object]:
        snapshot, query = self._queryable(map_reference)
        node = self._resolve_node(snapshot, concept_reference)
        prerequisites = query.prerequisites_of(node.concept_id)
        dependents = query.dependents_of(node.concept_id)
        hints = node.source_hints or (snapshot.source_identity,)
        source_references = [
            {
                "evidence_id": evidence_id,
                "source_identity": snapshot.source_identity,
                "source_reference": hints[index % len(hints)],
                "href": None,
                "link_status": "PENDING_SOURCE_ROUTE",
            }
            for index, evidence_id in enumerate(node.evidence_ids)
        ]
        return {
            "snapshot_id": snapshot.snapshot_id,
            "concept": node.to_dict(),
            "evidence_count": len(node.evidence_ids),
            "source_references": source_references,
            "repair_lineage": [item.to_dict() for item in node.repair_lineage],
            "prerequisites": [_node_summary(item) for item in prerequisites],
            "dependents": [_node_summary(item) for item in dependents],
        }

    def uncertain_edges(self, map_reference: str) -> dict[str, object]:
        snapshot, query = self._queryable(map_reference)
        return {
            "snapshot_id": snapshot.snapshot_id,
            "items": [
                item.to_dict()
                for item in query.unverified_or_uncertain_relations()
            ],
        }

    def diagnostics(self, map_reference: str) -> dict[str, object]:
        snapshot = self._snapshot(map_reference)
        return {
            "snapshot_id": snapshot.snapshot_id,
            "status": snapshot.status.value,
            "display": _display_projection(snapshot),
            "diagnostics": snapshot.diagnostics.to_dict(),
        }


def create_knowledge_map_read_router(
    snapshots: KnowledgeMapSnapshotPort,
) -> APIRouter:
    service = KnowledgeMapReadService(snapshots)
    router = APIRouter(
        prefix="/api/verified-knowledge-map",
        tags=["verified-knowledge-map"],
    )

    @router.get("/demo/java-database")
    def read_java_database_demo() -> dict[str, object]:
        return service.snapshot(JAVA_DATABASE_DEMO_ALIAS)

    @router.get("/maps/{map_reference}")
    def read_map_snapshot(map_reference: str) -> dict[str, object]:
        return service.snapshot(map_reference)

    @router.get("/maps/{map_reference}/concepts/{concept_reference}")
    def read_concept_detail(
        map_reference: str,
        concept_reference: str,
    ) -> dict[str, object]:
        return service.concept_detail(map_reference, concept_reference)

    @router.get("/maps/{map_reference}/roots")
    def read_roots(map_reference: str) -> dict[str, object]:
        return service.concepts(map_reference, "roots")

    @router.get("/maps/{map_reference}/leaves")
    def read_leaves(map_reference: str) -> dict[str, object]:
        return service.concepts(map_reference, "leaves")

    @router.get("/maps/{map_reference}/critical-concepts")
    def read_critical_concepts(map_reference: str) -> dict[str, object]:
        return service.concepts(map_reference, "critical")

    @router.get("/maps/{map_reference}/orphans")
    def read_orphans(map_reference: str) -> dict[str, object]:
        return service.concepts(map_reference, "orphans")

    @router.get("/maps/{map_reference}/concepts/{concept_reference}/prerequisites")
    def read_prerequisites(
        map_reference: str,
        concept_reference: str,
    ) -> dict[str, object]:
        return service.relatives(
            map_reference,
            concept_reference,
            "prerequisites",
        )

    @router.get("/maps/{map_reference}/concepts/{concept_reference}/dependents")
    def read_dependents(
        map_reference: str,
        concept_reference: str,
    ) -> dict[str, object]:
        return service.relatives(
            map_reference,
            concept_reference,
            "dependents",
        )

    @router.get("/maps/{map_reference}/uncertain-edges")
    def read_uncertain_edges(map_reference: str) -> dict[str, object]:
        return service.uncertain_edges(map_reference)

    @router.get("/maps/{map_reference}/diagnostics")
    def read_diagnostics(map_reference: str) -> dict[str, object]:
        return service.diagnostics(map_reference)

    return router


router = create_knowledge_map_read_router(demo_snapshot_port())


__all__ = [
    "CYCLE_USER_MESSAGE",
    "FAILED_USER_MESSAGE",
    "InMemoryKnowledgeMapSnapshotPort",
    "KNOWLEDGE_MAP_READ_API_VERSION",
    "KnowledgeMapReadService",
    "KnowledgeMapSnapshotPort",
    "PARTIAL_USER_MESSAGE",
    "create_knowledge_map_read_router",
    "demo_snapshot_port",
    "router",
]
