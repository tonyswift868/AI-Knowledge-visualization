from __future__ import annotations

from typing import Protocol

import networkx as nx

from ..inventory.models import canonical_semantic_key
from .models import (
    KnowledgeEdge,
    KnowledgeEdgeType,
    KnowledgeMapSnapshot,
    KnowledgeNode,
    NodeCriticality,
)


class KnowledgeEffortLookup(Protocol):
    def lookup_effort(self, effort_lookup_key: str) -> float | None: ...


class KnowledgeMapQuery:
    """Read-only, endpoint-neutral query facade over a deterministic map snapshot."""

    def __init__(self, snapshot: KnowledgeMapSnapshot) -> None:
        self.snapshot = snapshot
        self._node_by_id = {item.concept_id: item for item in snapshot.nodes}
        self._reference_index: dict[str, set[str]] = {}
        for node in snapshot.nodes:
            self._reference_index.setdefault(node.concept_id, set()).add(node.concept_id)
            for value in (node.canonical_name, *node.aliases):
                key = canonical_semantic_key(value)
                if key:
                    self._reference_index.setdefault(key, set()).add(node.concept_id)
        self._prerequisite_graph = nx.DiGraph()
        self._prerequisite_graph.add_nodes_from(self._node_by_id)
        self._prerequisite_graph.add_edges_from(
            (edge.source_concept_id, edge.target_concept_id)
            for edge in snapshot.edges
            if edge.edge_type is KnowledgeEdgeType.PREREQUISITE
        )

    def _resolve(self, reference: str) -> str:
        raw = str(reference or "").strip()
        if raw in self._node_by_id:
            return raw
        matches = self._reference_index.get(canonical_semantic_key(raw), set())
        if not matches:
            raise KeyError(f"unknown concept reference: {reference}")
        if len(matches) > 1:
            raise ValueError(f"ambiguous concept reference: {reference}")
        return next(iter(matches))

    def _nodes(self, concept_ids: set[str] | tuple[str, ...]) -> tuple[KnowledgeNode, ...]:
        selected = set(concept_ids)
        return tuple(item for item in self.snapshot.nodes if item.concept_id in selected)

    def all_concepts(self) -> tuple[KnowledgeNode, ...]:
        return self.snapshot.nodes

    def roots(self) -> tuple[KnowledgeNode, ...]:
        return self._nodes(
            {
                node
                for node in self._prerequisite_graph.nodes
                if self._prerequisite_graph.in_degree(node) == 0
            }
        )

    def leaves(self) -> tuple[KnowledgeNode, ...]:
        return self._nodes(
            {
                node
                for node in self._prerequisite_graph.nodes
                if self._prerequisite_graph.out_degree(node) == 0
            }
        )

    def prerequisites_of(self, reference: str) -> tuple[KnowledgeNode, ...]:
        concept_id = self._resolve(reference)
        return self._nodes(nx.ancestors(self._prerequisite_graph, concept_id))

    def dependents_of(self, reference: str) -> tuple[KnowledgeNode, ...]:
        concept_id = self._resolve(reference)
        return self._nodes(nx.descendants(self._prerequisite_graph, concept_id))

    def unverified_or_uncertain_relations(self) -> tuple[KnowledgeEdge, ...]:
        return tuple(
            edge
            for edge in self.snapshot.edges
            if edge.edge_type is KnowledgeEdgeType.POSSIBLE_PREREQUISITE
        )

    def critical_concepts(self) -> tuple[KnowledgeNode, ...]:
        return tuple(
            node
            for node in self.snapshot.nodes
            if node.criticality is NodeCriticality.CRITICAL
        )

    def orphan_concepts(self) -> tuple[KnowledgeNode, ...]:
        return self._nodes(self.snapshot.diagnostics.orphan_concept_ids)

    def disconnected_subgraphs(self) -> tuple[tuple[KnowledgeNode, ...], ...]:
        return tuple(
            self._nodes(component)
            for component in self.snapshot.diagnostics.disconnected_subgraphs
        )

    def effort_for(
        self,
        reference: str,
        lookup: KnowledgeEffortLookup,
    ) -> float | None:
        node = self._node_by_id[self._resolve(reference)]
        if node.effort_lookup_key is None:
            return None
        effort = lookup.lookup_effort(node.effort_lookup_key)
        if effort is not None and effort < 0:
            raise ValueError("effort lookup cannot return a negative value")
        return effort
