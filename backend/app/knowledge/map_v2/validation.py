from __future__ import annotations

from itertools import islice

import networkx as nx

from .models import (
    CycleDiagnostic,
    CycleEdgeEvidence,
    GraphValidationResult,
    KnowledgeEdge,
    KnowledgeEdgeType,
    KnowledgeGraphCandidate,
    KnowledgeGraphDiagnostics,
)


DEFAULT_MAX_REPORTED_CYCLES = 128


def _canonical_cycle(nodes: tuple[str, ...]) -> tuple[str, ...]:
    if not nodes:
        return ()
    rotations = tuple(nodes[index:] + nodes[:index] for index in range(len(nodes)))
    return min(rotations)


def _cycle_diagnostic(
    cycle: tuple[str, ...],
    edge_by_pair: dict[tuple[str, str], KnowledgeEdge],
) -> CycleDiagnostic:
    closed_path = cycle + (cycle[0],)
    edge_evidence = []
    for source, target in zip(closed_path[:-1], closed_path[1:], strict=True):
        edge = edge_by_pair[(source, target)]
        edge_evidence.append(
            CycleEdgeEvidence(
                edge_id=edge.edge_id,
                source_concept_id=edge.source_concept_id,
                target_concept_id=edge.target_concept_id,
                evidence_ids=edge.evidence_ids,
                basis=edge.basis,
            )
        )
    return CycleDiagnostic(
        cycle_path=closed_path,
        involved_concept_ids=tuple(sorted(set(cycle))),
        edge_evidence=tuple(edge_evidence),
    )


def validate_graph(
    candidate: KnowledgeGraphCandidate,
    *,
    max_reported_cycles: int = DEFAULT_MAX_REPORTED_CYCLES,
) -> GraphValidationResult:
    """Validate prerequisite semantics with NetworkX without mutating or deleting edges."""

    if max_reported_cycles <= 0:
        raise ValueError("max_reported_cycles must be positive")
    node_ids = tuple(item.concept_id for item in candidate.nodes)
    full_graph = nx.Graph()
    full_graph.add_nodes_from(node_ids)
    full_graph.add_edges_from(
        (edge.source_concept_id, edge.target_concept_id)
        for edge in candidate.edges
    )
    orphan_ids = tuple(sorted(node for node in full_graph.nodes if full_graph.degree(node) == 0))
    all_components = tuple(
        sorted(
            (tuple(sorted(component)) for component in nx.connected_components(full_graph)),
            key=lambda item: (item[0] if item else "", len(item), item),
        )
    )
    components = all_components if len(all_components) > 1 else ()

    prerequisite_edges = tuple(
        edge
        for edge in candidate.edges
        if edge.edge_type is KnowledgeEdgeType.PREREQUISITE
    )
    prerequisite_graph = nx.DiGraph()
    prerequisite_graph.add_nodes_from(node_ids)
    prerequisite_graph.add_edges_from(
        (edge.source_concept_id, edge.target_concept_id)
        for edge in prerequisite_edges
    )
    edge_by_pair = {
        (edge.source_concept_id, edge.target_concept_id): edge
        for edge in prerequisite_edges
    }
    raw_cycles = tuple(
        tuple(item)
        for item in islice(nx.simple_cycles(prerequisite_graph), max_reported_cycles + 1)
    )
    truncated = len(raw_cycles) > max_reported_cycles
    canonical_cycles = tuple(
        sorted(
            {
                _canonical_cycle(cycle)
                for cycle in raw_cycles[:max_reported_cycles]
                if cycle
            }
        )
    )
    cycles = tuple(
        _cycle_diagnostic(cycle, edge_by_pair)
        for cycle in canonical_cycles
    )
    is_dag = not cycles and not truncated and nx.is_directed_acyclic_graph(prerequisite_graph)
    topological_order = (
        tuple(nx.lexicographical_topological_sort(prerequisite_graph, key=str))
        if is_dag
        else ()
    )
    existing = candidate.diagnostics
    diagnostics = KnowledgeGraphDiagnostics(
        alias_collisions=existing.alias_collisions,
        rejected_relations=existing.rejected_relations,
        unresolved_relation_hints=existing.unresolved_relation_hints,
        cycles=cycles,
        orphan_concept_ids=orphan_ids,
        disconnected_subgraphs=components,
        missing_evidence_concept_ids=existing.missing_evidence_concept_ids,
        unknown_evidence_ids=existing.unknown_evidence_ids,
        cycle_diagnostics_truncated=truncated,
    )
    return GraphValidationResult(
        diagnostics=diagnostics,
        topological_order=topological_order,
        is_prerequisite_dag=is_dag,
    )
