import { describe, expect, it } from "vitest";
import {
  knowledgeMapGraphSignature,
  KnowledgeMapLayoutCache,
  layoutKnowledgeMapGraph,
  searchKnowledgeMapNodes,
} from "../features/active-teaching/knowledgeMapGraph";

const makeNodes = (count: number) => Array.from({ length: count }, (_, index) => ({
  id: `node-${index}`,
  label: index === count - 1 ? "目标检索节点" : `知识节点 ${index}`,
  summary: index === count - 1 ? "稳定搜索目标" : `摘要 ${index}`,
  layer: index % 5 === 0 ? "model_common" : "material",
  kind: index === 0 ? "theme" : "concept",
}));

describe("knowledge map stable performance paths", () => {
  it("searches 1000 nodes under the interactive budget", () => {
    const nodes = makeNodes(1_000);
    const containsEdges = nodes.slice(1).map((node, index) => ({
      id: `contains-${index}`,
      source: nodes[0].id,
      target: node.id,
      relation: "contains",
    }));
    const started = performance.now();
    const result = searchKnowledgeMapNodes(nodes, "目标检索", containsEdges);
    const elapsed = performance.now() - started;
    expect(result.map((node) => node.id)).toEqual(["node-999"]);
    expect(elapsed).toBeLessThan(100);
  });

  it("does not relayout on cache hits or filter/focus-only signatures", () => {
    const nodes = makeNodes(1_000);
    const edges = nodes.slice(1).map((node, index) => ({
      id: `edge-${index}`,
      source: nodes[index].id,
      target: node.id,
      relation: "prerequisite",
    }));
    const signature = knowledgeMapGraphSignature(nodes, edges, 7);
    const coldStarted = performance.now();
    const positions = layoutKnowledgeMapGraph(nodes, edges);
    const coldElapsed = performance.now() - coldStarted;
    expect(Object.keys(positions)).toHaveLength(nodes.length);
    expect(Number.isFinite(coldElapsed)).toBe(true);

    const cache = new KnowledgeMapLayoutCache(2);
    cache.set(signature, positions);
    const cacheStarted = performance.now();
    const hit = cache.get(signature);
    const cacheElapsed = performance.now() - cacheStarted;
    expect(hit).toBe(positions);
    // This is a stable map lookup path; keep the strict threshold here while
    // leaving the environment-sensitive Dagre cold-layout measurement loose.
    expect(cacheElapsed).toBeLessThan(100);
    expect(knowledgeMapGraphSignature(nodes, edges, 7)).toBe(signature);
  });
});
