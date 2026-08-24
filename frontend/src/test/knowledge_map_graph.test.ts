import { describe, expect, it } from "vitest";
import {
  buildKnowledgeLearningPath,
  buildKnowledgeMapSearchContext,
  knowledgeMapGraphSignature,
  KnowledgeMapLayoutCache,
  searchKnowledgeMapNodes,
} from "../features/active-teaching/knowledgeMapGraph";

describe("knowledge map graph helpers", () => {
  const nodes = [
    { id: "a", label: "函数基础", summary: "理解参数和返回值", layer: "material", kind: "concept" },
    { id: "b", label: "递归", summary: "用函数调用自身", layer: "material", kind: "concept" },
    { id: "c", label: "算法主题", summary: "模型通识上下文", layer: "model_common", kind: "theme" },
  ];

  it("matches label, summary, and layer context with ranked results", () => {
    expect(searchKnowledgeMapNodes(nodes, "参数").map((node) => node.id)).toEqual(["a"]);
    expect(searchKnowledgeMapNodes(nodes, "theme").map((node) => node.id)).toEqual(["c"]);
    expect(searchKnowledgeMapNodes(nodes, "")).toEqual([]);
  });

  it("indexes contains parents so a theme query returns the theme first and its concepts next", () => {
    const hierarchy = [
      { id: "theme", label: "数据结构", kind: "theme" },
      { id: "concept-a", label: "数组遍历", kind: "concept" },
      { id: "concept-b", label: "栈", kind: "concept" },
      { id: "other", label: "无关概念", kind: "concept" },
    ];
    const edges = [
      { id: "contains-a", source: "theme", target: "concept-a", relation: "contains" },
      { id: "contains-b", source: "theme", target: "concept-b", relation: "contains" },
      { id: "unknown", source: "missing", target: "concept-a", relation: "contains" },
    ];
    expect(searchKnowledgeMapNodes(hierarchy, "数据结构", edges).map((node) => node.id))
      .toEqual(["theme", "concept-a", "concept-b"]);
    const context = buildKnowledgeMapSearchContext(hierarchy, edges);
    expect(searchKnowledgeMapNodes(hierarchy, "数据结构", context).map((node) => node.id))
      .toEqual(["theme", "concept-a", "concept-b"]);
  });

  it("uses canonical theme membership fields and fails closed for unknown parents", () => {
    const hierarchy = [
      { id: "theme", label: "函数主题", kind: "theme" },
      { id: "concept", label: "参数", kind: "concept", theme_id: "theme" },
      { id: "unknown-concept", label: "未知父级", kind: "concept", theme_id: "missing" },
    ];
    expect(searchKnowledgeMapNodes(hierarchy, "函数主题").map((node) => node.id)).toEqual(["theme"]);
    expect(searchKnowledgeMapNodes(hierarchy, "函数主题", buildKnowledgeMapSearchContext(hierarchy)).map((node) => node.id))
      .toEqual(["theme", "concept"]);
  });

  it("preserves case-sensitive theme membership IDs while normalizing search text", () => {
    const hierarchy = [
      { id: "THEME-1", label: "函数主题", kind: "theme" },
      { id: "concept", label: "参数", kind: "concept", theme_id: "THEME-1" },
    ];
    expect(searchKnowledgeMapNodes(hierarchy, "函数主题", buildKnowledgeMapSearchContext(hierarchy)).map((node) => node.id))
      .toEqual(["THEME-1", "concept"]);
  });

  it("builds confirmed prerequisite upstream/current/downstream path", () => {
    const path = buildKnowledgeLearningPath(nodes, [
      { id: "e1", source: "a", target: "b", relation: "prerequisite", reviewStatus: "approved" },
      { id: "e2", source: "b", target: "c", relation: "prerequisite", reviewStatus: "approved" },
      { id: "e3", source: "a", target: "c", relation: "prerequisite", reviewStatus: "needs_review" },
    ], "b");
    expect(path.current?.id).toBe("b");
    expect(path.upstream.map((node) => node.id)).toEqual(["a"]);
    expect(path.downstream.map((node) => node.id)).toEqual(["c"]);
  });

  it("keeps unknown edges out and orders branching ancestors topologically", () => {
    const branchNodes = [
      { id: "root", label: "根" },
      { id: "left", label: "左支" },
      { id: "right", label: "右支" },
      { id: "current", label: "当前" },
    ];
    const path = buildKnowledgeLearningPath(branchNodes, [
      { id: "left-edge", source: "root", target: "left", relation: "prerequisite", reviewStatus: "approved" },
      { id: "right-edge", source: "root", target: "right", relation: "prerequisite", reviewStatus: "approved" },
      { id: "left-current", source: "left", target: "current", relation: "prerequisite", reviewStatus: "approved" },
      { id: "right-current", source: "right", target: "current", relation: "prerequisite", reviewStatus: "legacy" },
    ], "current");
    expect(path.upstream.map((node) => node.id)).toEqual(["root", "left"]);
  });

  it("keeps stable signatures and evicts old layout entries", () => {
    const signature = knowledgeMapGraphSignature(nodes, [], 3);
    const cache = new KnowledgeMapLayoutCache(1);
    cache.set(signature, { a: { x: 1, y: 1 } });
    expect(cache.get(signature)?.a.x).toBe(1);
    cache.set("new", { b: { x: 2, y: 2 } });
    expect(cache.get(signature)).toBeUndefined();
  });
});
