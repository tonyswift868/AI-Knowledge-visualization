import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@xyflow/react", () => ({
  MarkerType: { ArrowClosed: "arrowclosed" },
  Position: { Left: "left", Right: "right" },
  Background: () => null,
  Controls: () => null,
  MiniMap: () => null,
  ReactFlow: ({ nodes, edges }: {
    nodes: Array<{ id: string }>;
    edges: Array<{ id: string }>;
  }) => (
    <div
      data-testid="react-flow-performance-boundary"
      data-node-count={nodes.length}
      data-edge-count={edges.length}
    />
  ),
}));

import {
  VerifiedKnowledgeMapExperience,
} from "../features/verified-knowledge-map/VerifiedKnowledgeMapExperience";
import {
  createKnowledgeMapFixture,
  LARGE_GRAPH_FIXTURE_SIZES,
} from "../features/verified-knowledge-map/fixtures";
import {
  filterKnowledgeGraph,
  mapKnowledgeGraphToReactFlow,
} from "../features/verified-knowledge-map/graphAdapter";

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
});

afterEach(cleanup);

describe("Verified Knowledge Map large-graph measurements", () => {
  it.each(LARGE_GRAPH_FIXTURE_SIZES)(
    "records layout, mapping, and component mount metrics for %i nodes",
    (size) => {
      const envelope = createKnowledgeMapFixture(size);
      const graph = filterKnowledgeGraph(envelope.snapshot, {
        mode: "all",
        source: "",
        verificationStatus: "all",
      });

      const adapterStarted = performance.now();
      const mapped = mapKnowledgeGraphToReactFlow(graph);
      const adapterTotalMs = performance.now() - adapterStarted;

      const mountStarted = performance.now();
      const view = render(
        <VerifiedKnowledgeMapExperience initialEnvelope={envelope} />,
      );
      const componentMountMs = performance.now() - mountStarted;
      const boundary = view.getByTestId("react-flow-performance-boundary");

      expect(mapped.nodes).toHaveLength(size);
      expect(Number(boundary.getAttribute("data-node-count"))).toBe(size);
      expect(mapped.layoutMs).toBeGreaterThanOrEqual(0);
      expect(mapped.mappingMs).toBeGreaterThanOrEqual(0);
      expect(componentMountMs).toBeGreaterThanOrEqual(0);

      console.log("VERIFIED_KNOWLEDGE_MAP_PERF", JSON.stringify({
        fixture_nodes: size,
        fixture_edges: graph.edges.length,
        dagre_layout_ms: Number(mapped.layoutMs.toFixed(3)),
        react_flow_mapping_ms: Number(mapped.mappingMs.toFixed(3)),
        adapter_total_ms: Number(adapterTotalMs.toFixed(3)),
        component_mount_ms: Number(componentMountMs.toFixed(3)),
        environment: "Vitest jsdom; React component mount; React Flow canvas boundary stubbed",
      }));
    },
    120_000,
  );
});
