"""Pydantic contracts and graph quality gates for generic knowledge maps v1."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .knowledge_policy import (
    MASTERY_ESTIMATION_POLICY,
    POLICY_VERSION,
    PROVIDER_POLICY,
    TEXT_LIMITS_POLICY,
    get_policy,
)


class EvidenceSegmentV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    material_id: str
    source_id: str = ""
    source_revision_id: str = ""
    chunk_id: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    quote: str = ""
    source_file: str | None = None
    heading: str = ""
    page_start: int | None = None
    page_end: int | None = None
    paragraph_start: int | None = None
    paragraph_end: int | None = None
    content_hash: str = ""
    bbox: list[float] | None = None
    audio_start_ms: int | None = Field(default=None, ge=0)
    audio_end_ms: int | None = Field(default=None, ge=0)
    quote_hash: str = ""
    adapter_name: str = ""
    adapter_version: str = ""

    @field_validator("char_end")
    @classmethod
    def end_not_before_start(cls, value: int, info: Any) -> int:
        start = info.data.get("char_start")
        if start is not None and value < start:
            raise ValueError("char_end must be >= char_start")
        return value


class ProviderKnowledgePointV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    name: str = Field(
        min_length=1,
        max_length=TEXT_LIMITS_POLICY.concept_name_characters,
    )
    explanation: str = ""
    difficulty: str = "medium"
    estimated_mastery_minutes: int | None = Field(
        default=None,
        ge=MASTERY_ESTIMATION_POLICY.minimum_total_minutes,
        le=MASTERY_ESTIMATION_POLICY.maximum_total_minutes,
    )
    acceptance_method: str = ""
    node_type: str = "concept"
    layer: str = "material"
    source_chunk_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(
        default=PROVIDER_POLICY.default_knowledge_confidence,
        ge=0,
        le=1,
    )

    @model_validator(mode="after")
    def grounded_material_or_reviewable_common(self) -> "ProviderKnowledgePointV1":
        self.layer = str(self.layer or "material").strip().casefold()
        if self.layer not in {"material", "model_common"}:
            raise ValueError("knowledge point layer must be material or model_common")
        self.node_type = str(self.node_type or "concept").strip().casefold()
        if self.node_type not in {"theme", "concept"}:
            raise ValueError("knowledge point node_type must be theme or concept")
        if self.layer == "material" and not self.source_chunk_ids:
            raise ValueError("material knowledge points require source_chunk_ids")
        if self.layer == "model_common" and self.source_chunk_ids:
            raise ValueError("model_common knowledge points must not claim material evidence")
        return self


class ProviderDependencyV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prerequisite_id: str
    dependent_id: str
    reason: str = ""
    relation: str = "prerequisite"
    layer: str = "material"
    status: str = "pending"
    confirmed: bool = False
    source_chunk_ids: list[str] = Field(default_factory=list)

    @field_validator("relation")
    @classmethod
    def registered_relation(cls, value: str) -> str:
        relation = str(value or "").strip().casefold()
        if relation not in get_policy().accepted_relation_types:
            raise ValueError("relation is not registered by the active knowledge-map policy")
        return relation

    @field_validator("layer")
    @classmethod
    def registered_layer(cls, value: str) -> str:
        layer = str(value or "material").strip().casefold()
        if layer not in {"material", *get_policy().relation_layers}:
            raise ValueError("layer is not registered by the active knowledge-map policy")
        return layer

    @model_validator(mode="after")
    def grounded_material_or_reviewable_common(self) -> "ProviderDependencyV1":
        if self.layer == "material" and not self.source_chunk_ids:
            raise ValueError("material relations require their own source_chunk_ids")
        if self.layer == "model_common" and self.source_chunk_ids:
            raise ValueError("model_common relations must not claim material evidence")
        return self


class ProviderPayloadV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    knowledge_points: list[ProviderKnowledgePointV1] = Field(min_length=1)
    dependencies: list[ProviderDependencyV1] = Field(default_factory=list)


def provider_payload_model(payload: Any) -> ProviderPayloadV1:
    """Validate untrusted provider output before any persistence."""

    try:
        return ProviderPayloadV1.model_validate(payload)
    except ValidationError as exc:
        raise ValueError("Provider payload failed KnowledgeMapProviderV1 validation") from exc


def validate_graph_v1(
    nodes: Iterable[Mapping[str, Any]],
    edges: Iterable[Mapping[str, Any]],
    *,
    enforce_acyclic: bool = True,
) -> dict[str, Any]:
    """Validate graph references and relations, optionally gating proposed cycles.

    Provider relationships initially remain review candidates.  Their cycles
    are reported but may be persisted while unconfirmed; activated planning
    relationships always call this gate with ``enforce_acyclic=True``.
    NetworkX is a pinned core dependency for the v1 graph contract.
    """

    node_list = [dict(item) for item in nodes]
    edge_list = [dict(item) for item in edges]
    node_ids = [str(item.get("id") or item.get("local_id") or "") for item in node_list]
    if not node_ids or any(not node_id for node_id in node_ids) or len(node_ids) != len(set(node_ids)):
        raise ValueError("knowledge map node ids must be present and unique")
    prerequisite_pairs: list[tuple[str, str]] = []
    for edge in edge_list:
        source = str(edge.get("prerequisite_id") or edge.get("source_id") or "")
        target = str(edge.get("dependent_id") or edge.get("target_id") or "")
        if source not in node_ids or target not in node_ids or source == target:
            raise ValueError("knowledge map contains an invalid graph edge")
        relation = str(edge.get("relation") or "prerequisite").casefold()
        if relation not in get_policy().accepted_relation_types:
            raise ValueError("knowledge map contains an unregistered relation")
        if relation in {"contains", "prerequisite"}:
            prerequisite_pairs.append((source, target))
    graph = nx.DiGraph()
    graph.add_nodes_from(node_ids)
    graph.add_edges_from(prerequisite_pairs)
    cycles = list(nx.simple_cycles(graph))
    if cycles and enforce_acyclic:
        raise ValueError("knowledge map dependency graph contains a cycle")
    return {
        "node_count": len(node_ids),
        "edge_count": len(edge_list),
        "acyclic": not bool(cycles),
        "cycle_count": len(cycles),
        "policy_version": POLICY_VERSION,
    }


def reduce_planning_graph_v1(
    node_ids: Iterable[str],
    edges: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return stable topology and transitive reduction for confirmed plan edges."""

    nodes = [str(value) for value in node_ids]
    if not nodes or any(not value for value in nodes) or len(nodes) != len(set(nodes)):
        raise ValueError("planning graph node ids must be present and unique")
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes)
    for edge in edges:
        relation = str(edge.get("relation") or "prerequisite")
        if relation not in get_policy().plan_relation_types:
            continue
        source = str(edge.get("source_id") or edge.get("prerequisite_id") or "")
        target = str(edge.get("target_id") or edge.get("dependent_id") or "")
        if source not in graph or target not in graph or source == target:
            raise ValueError("planning graph contains an invalid edge")
        graph.add_edge(source, target)
    if not nx.is_directed_acyclic_graph(graph):
        raise ValueError("confirmed prerequisite graph contains a cycle")
    reduced = nx.transitive_reduction(graph)
    order = list(nx.lexicographical_topological_sort(reduced, key=str))
    return {
        "order": order,
        "edges": [
            {"source_id": source, "target_id": target}
            for source, target in sorted(reduced.edges())
        ],
        "original_edge_count": graph.number_of_edges(),
        "reduced_edge_count": reduced.number_of_edges(),
    }


def validate_topic_concept_structure_v1(
    nodes: Iterable[Mapping[str, Any]],
    edges: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Require an explicit material theme-to-concept hierarchy.

    This gate validates Provider output; it never creates a missing theme or
    invents a ``contains`` relation.  Model-common items remain outside this
    material hierarchy until explicitly reviewed.
    """

    node_list = [dict(item) for item in nodes]
    edge_list = [dict(item) for item in edges]
    node_by_id = {
        str(item.get("id") or item.get("local_id") or ""): item
        for item in node_list
    }
    material_themes = {
        node_id
        for node_id, item in node_by_id.items()
        if str(item.get("layer") or "material") == "material"
        and str(item.get("node_type") or "concept") == "theme"
    }
    material_concepts = {
        node_id
        for node_id, item in node_by_id.items()
        if str(item.get("layer") or "material") == "material"
        and str(item.get("node_type") or "concept") == "concept"
    }
    if not material_themes:
        raise ValueError("knowledge map requires at least one material theme node")
    if not material_concepts:
        raise ValueError("knowledge map requires at least one material concept node")
    contained: set[str] = set()
    contains_count = 0
    for edge in edge_list:
        if str(edge.get("layer") or "material") != "material":
            continue
        if str(edge.get("relation") or "") != "contains":
            continue
        source = str(edge.get("source_id") or edge.get("prerequisite_id") or "")
        target = str(edge.get("target_id") or edge.get("dependent_id") or "")
        if source not in material_themes or target not in material_concepts:
            raise ValueError("material contains relations must connect theme to concept")
        contained.add(target)
        contains_count += 1
    missing = sorted(material_concepts - contained)
    if missing:
        raise ValueError(
            "every material concept requires an evidence-backed theme contains relation: "
            + ",".join(missing)
        )
    return {
        "material_theme_count": len(material_themes),
        "material_concept_count": len(material_concepts),
        "material_contains_count": contains_count,
    }


def evidence_fingerprint(evidence: Iterable[Mapping[str, Any]]) -> str:
    """Hash the exact immutable evidence identity, independent of list order."""

    canonical: list[dict[str, Any]] = []
    for item in evidence:
        quote = str(item.get("quote") or "")
        canonical.append(
            {
                "material_id": str(item.get("material_id") or ""),
                "source_id": str(item.get("source_id") or ""),
                "source_revision_id": str(item.get("source_revision_id") or ""),
                "chunk_id": str(item.get("chunk_id") or ""),
                "char_start": int(item.get("char_start") or 0),
                "char_end": int(item.get("char_end") or 0),
                "page_start": item.get("page_start"),
                "page_end": item.get("page_end"),
                "bbox": item.get("bbox"),
                "audio_start_ms": item.get("audio_start_ms"),
                "audio_end_ms": item.get("audio_end_ms"),
                "content_hash": str(item.get("content_hash") or ""),
                "quote_hash": str(item.get("quote_hash") or "")
                or hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                "adapter_name": str(item.get("adapter_name") or ""),
                "adapter_version": str(item.get("adapter_version") or ""),
            }
        )
    canonical.sort(
        key=lambda value: json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    payload = {
        "schema": "mentorflow.evidence-fingerprint.v1",
        "segments": canonical,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "EvidenceSegmentV1",
    "ProviderDependencyV1",
    "ProviderKnowledgePointV1",
    "ProviderPayloadV1",
    "evidence_fingerprint",
    "get_policy",
    "provider_payload_model",
    "reduce_planning_graph_v1",
    "validate_graph_v1",
    "validate_topic_concept_structure_v1",
]
