import { describe, expect, it } from "vitest";

import {
  EDGE_VISUAL_SEMANTICS,
  availableSourceFilters,
  buildConceptDetail,
  filterKnowledgeGraph,
  mapKnowledgeGraphToReactFlow,
} from "../features/verified-knowledge-map/graphAdapter";
import { createKnowledgeMapFixture } from "../features/verified-knowledge-map/fixtures";
import type { KnowledgeMapFilters } from "../features/verified-knowledge-map/types";

const allFilters: KnowledgeMapFilters = {
  mode: "all",
  source: "",
  verificationStatus: "all",
};

describe("verified knowledge map graph adapter", () => {
  it("maps every node and all four relation semantics into React Flow", () => {
    const snapshot = createKnowledgeMapFixture(50).snapshot;
    const graph = filterKnowledgeGraph(snapshot, allFilters);

    const flow = mapKnowledgeGraphToReactFlow(graph);

    expect(flow.nodes).toHaveLength(50);
    expect(flow.edges).toHaveLength(snapshot.edges.length);
    expect(Object.values(EDGE_VISUAL_SEMANTICS).map((item) => item.label)).toEqual([
      "前置依赖",
      "支持",
      "相关",
      "可能前置 · 未验证",
    ]);
    const uncertain = flow.edges.find(
      (edge) => edge.data?.visualSemantic === "POSSIBLE_PREREQUISITE",
    );
    const prerequisite = flow.edges.find(
      (edge) => edge.data?.visualSemantic === "PREREQUISITE",
    );
    expect(uncertain).toMatchObject({
      label: "可能前置 · 未验证",
      animated: true,
      data: { verified: false },
      style: { stroke: "#d17a00", strokeDasharray: "10 7" },
    });
    expect(prerequisite).toMatchObject({
      label: "前置依赖",
      animated: false,
      data: { verified: true },
      style: { stroke: "#16845b", strokeDasharray: undefined },
    });
    expect(flow.nodes.every((node) => Number.isFinite(node.position.x))).toBe(true);
    expect(flow.nodes.every((node) => Number.isFinite(node.position.y))).toBe(true);
  });

  it("applies critical, uncertain, orphan, source and verification filters", () => {
    const snapshot = createKnowledgeMapFixture(50).snapshot;
    const critical = filterKnowledgeGraph(snapshot, {
      ...allFilters,
      mode: "critical",
    });
    const uncertain = filterKnowledgeGraph(snapshot, {
      ...allFilters,
      mode: "uncertain",
    });
    const source = filterKnowledgeGraph(snapshot, {
      ...allFilters,
      source: "fixture-source-3",
    });
    const repaired = filterKnowledgeGraph(snapshot, {
      ...allFilters,
      verificationStatus: "VERIFIED_REPAIR_CANDIDATE",
    });
    const orphanSnapshot = createKnowledgeMapFixture(1).snapshot;
    const orphan = filterKnowledgeGraph(orphanSnapshot, {
      ...allFilters,
      mode: "orphan",
    });

    expect(critical.nodes.length).toBeGreaterThan(0);
    expect(critical.nodes.every((node) => node.criticality === "CRITICAL")).toBe(true);
    expect(uncertain.edges.length).toBeGreaterThan(0);
    expect(uncertain.edges.every(
      (edge) => edge.edge_type === "POSSIBLE_PREREQUISITE",
    )).toBe(true);
    expect(source.nodes.every(
      (node) => node.source_hints.includes("fixture-source-3"),
    )).toBe(true);
    expect(repaired.nodes.every(
      (node) => node.verification_status === "VERIFIED_REPAIR_CANDIDATE",
    )).toBe(true);
    expect(orphan.nodes).toHaveLength(1);
    expect(availableSourceFilters(snapshot)).toEqual([
      "fixture-source-0",
      "fixture-source-1",
      "fixture-source-2",
      "fixture-source-3",
      "fixture-source-4",
    ]);
  });

  it("keeps Dagre output deterministic and excludes uncertain edges from detail lineage", () => {
    const snapshot = createKnowledgeMapFixture(50).snapshot;
    const graph = filterKnowledgeGraph(snapshot, allFilters);
    const reversed = {
      nodes: [...graph.nodes].reverse(),
      edges: [...graph.edges].reverse(),
    };

    const forward = mapKnowledgeGraphToReactFlow(graph);
    const backward = mapKnowledgeGraphToReactFlow(reversed);

    expect(forward.nodes.map(({ id, position }) => ({ id, position }))).toEqual(
      backward.nodes.map(({ id, position }) => ({ id, position })),
    );
    expect(forward.edges.map((edge) => edge.id)).toEqual(
      backward.edges.map((edge) => edge.id),
    );
    const possible = snapshot.edges.find(
      (edge) => edge.edge_type === "POSSIBLE_PREREQUISITE",
    );
    expect(possible).toBeDefined();
    const target = snapshot.nodes.find(
      (node) => node.concept_id === possible?.target_concept_id,
    );
    expect(target).toBeDefined();
    const detail = buildConceptDetail(snapshot, target!);
    expect(detail.source_references).toHaveLength(target!.evidence_ids.length);
    expect(detail.prerequisites.some(
      (node) => node.concept_id === possible?.source_concept_id,
    )).toBe(false);
  });
});
