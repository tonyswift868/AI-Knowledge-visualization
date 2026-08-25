import dagre from "@dagrejs/dagre";
import type { CSSProperties } from "react";
import {
  MarkerType,
  Position,
  type Edge,
  type Node,
  type XYPosition,
} from "@xyflow/react";

import type {
  ConceptDetail,
  KnowledgeEdge,
  KnowledgeEdgeType,
  KnowledgeMapFilters,
  KnowledgeMapSnapshot,
  KnowledgeNode,
} from "./types";

const NODE_WIDTH = 236;
const NODE_HEIGHT = 92;

export type KnowledgeFlowNodeData = {
  label: string;
  node: KnowledgeNode;
  [key: string]: unknown;
};

export type KnowledgeFlowNode = Node<KnowledgeFlowNodeData>;
export type KnowledgeFlowEdge = Edge;

export type FilteredKnowledgeGraph = {
  nodes: KnowledgeNode[];
  edges: KnowledgeEdge[];
};

export type GraphLayoutResult = {
  positions: Record<string, XYPosition>;
  layoutMs: number;
};

export type ReactFlowGraphResult = {
  nodes: KnowledgeFlowNode[];
  edges: KnowledgeFlowEdge[];
  layoutMs: number;
  mappingMs: number;
};

export const EDGE_VISUAL_SEMANTICS: Record<KnowledgeEdgeType, {
  label: string;
  stroke: string;
  dash?: string;
  animated: boolean;
  verified: boolean;
}> = {
  PREREQUISITE: {
    label: "前置依赖",
    stroke: "#16845b",
    animated: false,
    verified: true,
  },
  SUPPORTS: {
    label: "支持",
    stroke: "#3976c7",
    animated: false,
    verified: true,
  },
  RELATED: {
    label: "相关",
    stroke: "#778296",
    dash: "3 5",
    animated: false,
    verified: true,
  },
  POSSIBLE_PREREQUISITE: {
    label: "可能前置 · 未验证",
    stroke: "#d17a00",
    dash: "10 7",
    animated: true,
    verified: false,
  },
};

export function availableSourceFilters(snapshot: KnowledgeMapSnapshot): string[] {
  return Array.from(
    new Set(snapshot.nodes.flatMap((node) => node.source_hints)),
  ).sort((left, right) => left.localeCompare(right));
}

export function filterKnowledgeGraph(
  snapshot: KnowledgeMapSnapshot,
  filters: KnowledgeMapFilters,
): FilteredKnowledgeGraph {
  const uncertainEdges = snapshot.edges.filter(
    (edge) => edge.edge_type === "POSSIBLE_PREREQUISITE",
  );
  const uncertainNodeIds = new Set(
    uncertainEdges.flatMap((edge) => [
      edge.source_concept_id,
      edge.target_concept_id,
    ]),
  );
  const orphanIds = new Set(snapshot.diagnostics.orphan_concept_ids);
  const nodes = snapshot.nodes.filter((node) => {
    if (filters.mode === "critical" && node.criticality !== "CRITICAL") {
      return false;
    }
    if (filters.mode === "uncertain" && !uncertainNodeIds.has(node.concept_id)) {
      return false;
    }
    if (filters.mode === "orphan" && !orphanIds.has(node.concept_id)) {
      return false;
    }
    if (filters.source && !node.source_hints.includes(filters.source)) {
      return false;
    }
    if (
      filters.verificationStatus !== "all"
      && node.verification_status !== filters.verificationStatus
    ) {
      return false;
    }
    return true;
  });
  const visible = new Set(nodes.map((node) => node.concept_id));
  const edges = snapshot.edges.filter((edge) => (
    visible.has(edge.source_concept_id)
    && visible.has(edge.target_concept_id)
    && (
      filters.mode !== "uncertain"
      || edge.edge_type === "POSSIBLE_PREREQUISITE"
    )
  ));
  return { nodes, edges };
}

export function layoutKnowledgeGraph(
  graph: FilteredKnowledgeGraph,
): GraphLayoutResult {
  const started = performance.now();
  const layout = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  layout.setGraph({
    rankdir: "LR",
    align: "UL",
    nodesep: 38,
    ranksep: 94,
    edgesep: 18,
    marginx: 30,
    marginy: 30,
  });
  [...graph.nodes]
    .sort((left, right) => left.concept_id.localeCompare(right.concept_id))
    .forEach((node) => {
      layout.setNode(node.concept_id, { width: NODE_WIDTH, height: NODE_HEIGHT });
    });
  [...graph.edges]
    .sort((left, right) => left.edge_id.localeCompare(right.edge_id))
    .forEach((edge) => {
      layout.setEdge(edge.source_concept_id, edge.target_concept_id);
    });
  dagre.layout(layout);
  const positions = Object.fromEntries(
    [...graph.nodes]
      .sort((left, right) => left.concept_id.localeCompare(right.concept_id))
      .map((node) => {
        const position = layout.node(node.concept_id) as { x: number; y: number };
        return [
          node.concept_id,
          {
            x: position.x - NODE_WIDTH / 2,
            y: position.y - NODE_HEIGHT / 2,
          },
        ];
      }),
  );
  return {
    positions,
    layoutMs: performance.now() - started,
  };
}

function nodeStyle(node: KnowledgeNode): CSSProperties {
  const critical = node.criticality === "CRITICAL";
  const repaired = node.verification_status === "VERIFIED_REPAIR_CANDIDATE";
  return {
    width: NODE_WIDTH,
    minHeight: NODE_HEIGHT,
    borderRadius: 14,
    border: `2px solid ${critical ? "#b43b42" : repaired ? "#c98216" : "#318567"}`,
    background: critical ? "#fff2f1" : repaired ? "#fff8e9" : "#effaf5",
    color: "#13251f",
    boxShadow: "0 7px 20px rgba(28, 58, 47, 0.11)",
    fontSize: 13,
  };
}

function toFlowEdge(edge: KnowledgeEdge): KnowledgeFlowEdge {
  const semantics = EDGE_VISUAL_SEMANTICS[edge.edge_type];
  return {
    id: edge.edge_id,
    source: edge.source_concept_id,
    target: edge.target_concept_id,
    label: semantics.label,
    animated: semantics.animated,
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: semantics.stroke,
      width: 18,
      height: 18,
    },
    style: {
      stroke: semantics.stroke,
      strokeWidth: edge.edge_type === "PREREQUISITE" ? 2.4 : 1.8,
      strokeDasharray: semantics.dash,
    },
    labelStyle: {
      fill: semantics.stroke,
      fontWeight: semantics.verified ? 600 : 800,
    },
    ariaLabel: `${semantics.label}，置信度 ${Math.round(edge.confidence * 100)}%`,
    data: {
      edge,
      verified: semantics.verified,
      visualSemantic: edge.edge_type,
    },
  };
}

export function mapKnowledgeGraphToReactFlow(
  graph: FilteredKnowledgeGraph,
): ReactFlowGraphResult {
  const layout = layoutKnowledgeGraph(graph);
  const mappingStarted = performance.now();
  const nodes = [...graph.nodes]
    .sort((left, right) => left.concept_id.localeCompare(right.concept_id))
    .map((node): KnowledgeFlowNode => ({
      id: node.concept_id,
      position: layout.positions[node.concept_id],
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      data: {
        label: node.canonical_name,
        node,
      },
      style: nodeStyle(node),
      ariaLabel: [
        node.canonical_name,
        node.criticality === "CRITICAL" ? "关键概念" : "普通概念",
        `验证状态 ${node.verification_status}`,
        `置信度 ${Math.round(node.confidence * 100)}%`,
      ].join("，"),
    }));
  const edges = [...graph.edges]
    .sort((left, right) => left.edge_id.localeCompare(right.edge_id))
    .map(toFlowEdge);
  return {
    nodes,
    edges,
    layoutMs: layout.layoutMs,
    mappingMs: performance.now() - mappingStarted,
  };
}

export function buildConceptDetail(
  snapshot: KnowledgeMapSnapshot,
  node: KnowledgeNode,
): ConceptDetail {
  const prerequisites = snapshot.edges
    .filter((edge) => (
      edge.edge_type === "PREREQUISITE"
      && edge.target_concept_id === node.concept_id
    ))
    .map((edge) => snapshot.nodes.find(
      (item) => item.concept_id === edge.source_concept_id,
    ))
    .filter((item): item is KnowledgeNode => Boolean(item))
    .map((item) => ({
      concept_id: item.concept_id,
      canonical_name: item.canonical_name,
    }));
  const dependents = snapshot.edges
    .filter((edge) => (
      edge.edge_type === "PREREQUISITE"
      && edge.source_concept_id === node.concept_id
    ))
    .map((edge) => snapshot.nodes.find(
      (item) => item.concept_id === edge.target_concept_id,
    ))
    .filter((item): item is KnowledgeNode => Boolean(item))
    .map((item) => ({
      concept_id: item.concept_id,
      canonical_name: item.canonical_name,
    }));
  const sourceHints = node.source_hints.length
    ? node.source_hints
    : [snapshot.source_identity];
  return {
    snapshot_id: snapshot.snapshot_id,
    concept: node,
    evidence_count: node.evidence_ids.length,
    source_references: node.evidence_ids.map((evidenceId, index) => ({
      evidence_id: evidenceId,
      source_identity: snapshot.source_identity,
      source_reference: sourceHints[index % sourceHints.length],
      href: null,
      link_status: "PENDING_SOURCE_ROUTE",
    })),
    repair_lineage: node.repair_lineage,
    prerequisites,
    dependents,
  };
}
