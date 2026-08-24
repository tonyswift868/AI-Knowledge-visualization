import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  isKnowledgeJobReady,
  isKnowledgeJobTerminal,
  KnowledgeMapV1Panel,
  layoutKnowledgeMapV1,
  materialAcceptFromCapabilities,
  normalizeKnowledgeMapV1,
  normalizeMasteryPlanPreview,
} from "../features/active-teaching/ActiveTeachingChainView";

describe("knowledge map v1", () => {
  afterEach(cleanup);
  it("normalizes nodes and dependencies while retaining evidence", () => {
    const map = normalizeKnowledgeMapV1({
      revision: 3,
      nodes: [
        { node_id: "material-1", label: "资料概念", node_type: "theme", layer: "material", source_refs: [{ chunk_id: "c1", quote: "原文" }] },
        { node_id: "common-1", label: "通识补充", layer: "model_common" },
      ],
      edges: [{ edge_id: "edge-1", source_node_id: "material-1", target_node_id: "common-1", relation: "supports" }],
    });
    expect(map.revision).toBe(3);
    expect(map.nodes[0].evidence[0].quote).toBe("原文");
    expect(map.nodes[0].kind).toBe("theme");
    expect(map.edges[0]).toMatchObject({ source: "material-1", target: "common-1", relation: "supports" });
    expect(layoutKnowledgeMapV1(map.nodes, map.edges)).toHaveLength(2);
  });

  it("hides model_common by default and exposes evidence, list navigation, and decisions", () => {
    const onDecision = vi.fn();
    const map = normalizeKnowledgeMapV1({
      status: "needs_review",
      nodes: [{ id: "n1", label: "可审核节点", layer: "material", source_refs: [{ chunk_id: "c1", quote: "可核对的原文" }] }, { id: "n2", label: "通识节点", layer: "model_common" }],
      edges: [],
      candidates: [{ id: "candidate-1", label: "待确认候选", status: "needs_review" }],
    });
    render(<KnowledgeMapV1Panel map={map} onGenerate={vi.fn()} onDecision={onDecision} />);
    expect(screen.getByText("可审核节点")).toBeInTheDocument();
    expect(screen.queryByText("通识节点")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /可审核节点/ }));
    expect(screen.getByTestId("active-knowledge-map-evidence")).toHaveTextContent("可核对的原文");
    fireEvent.change(screen.getByTestId("knowledge-map-search"), { target: { value: "通识" } });
    fireEvent.click(screen.getByRole("button", { name: /通识节点/ }));
    expect(screen.getByRole("region", { name: "知识地图键盘列表" })).toHaveTextContent("通识节点");
    fireEvent.click(screen.getByRole("button", { name: "确认候选" }));
    expect(onDecision).toHaveBeenCalledWith(expect.objectContaining({ id: "candidate-1" }), "confirm");
  });

  it("searches a theme name through its contains concepts and keeps theme ordering", () => {
    const map = normalizeKnowledgeMapV1({
      nodes: [
        { id: "theme-1", label: "函数主题", node_type: "theme", layer: "material" },
        { id: "concept-1", label: "参数", node_type: "concept", layer: "material" },
        { id: "common-1", label: "模型通识", node_type: "concept", layer: "model_common" },
      ],
      edges: [{ id: "contains-1", source: "theme-1", target: "concept-1", relation: "contains" }],
    });
    render(<KnowledgeMapV1Panel map={map} onGenerate={vi.fn()} onDecision={vi.fn()} />);
    fireEvent.change(screen.getByTestId("knowledge-map-search"), { target: { value: "函数主题" } });
    const resultButtons = Array.from(screen.getByTestId("knowledge-map-search-results").querySelectorAll("li button"));
    expect(resultButtons.map((button) => button.textContent?.split("暂无摘要")[0])).toEqual(["函数主题", "参数"]);
    fireEvent.click(resultButtons[1]);
    expect(screen.getByRole("region", { name: "知识地图键盘列表" })).toHaveTextContent("参数");
  });

  it("keeps fallback status copy, revision/error metadata, and retry action semantic", () => {
    const onRetryRead = vi.fn();
    const map = normalizeKnowledgeMapV1({
      revision: 41,
      nodes: [{ id: "fallback-node", label: "回退节点", layer: "material" }],
      edges: [],
    });
    render(
      <KnowledgeMapV1Panel
        map={map}
        isFallbackSnapshot
        snapshotRevision={41}
        error="读取超时"
        onGenerate={vi.fn()}
        onRetryRead={onRetryRead}
        onDecision={vi.fn()}
      />,
    );
    const fallback = screen.getByTestId("knowledge-map-fallback-state");
    expect(fallback).toHaveAttribute("aria-label", "知识地图回退快照状态");
    expect(screen.getByTestId("knowledge-map-fallback-message")).toHaveTextContent("不能当作当前教学依据");
    expect(screen.getByTestId("knowledge-map-fallback-meta")).toHaveTextContent("revision 41 · 读取超时");
    fireEvent.click(screen.getByTestId("knowledge-map-fallback-retry"));
    expect(onRetryRead).toHaveBeenCalledTimes(1);
  });

  it("uses canonical job terminal states and derives upload filters from healthy capabilities", () => {
    expect(isKnowledgeJobTerminal("ready_with_review")).toBe(true);
    expect(isKnowledgeJobReady("ready_with_review")).toBe(true);
    expect(isKnowledgeJobTerminal("extracting")).toBe(false);
    const accept = materialAcceptFromCapabilities({
      registry: {
        capabilities: [
          { available: true, suffixes: [".md"], mime_types: ["text/markdown"] },
          { available: true, suffixes: ["json"], mime_types: ["application/json"] },
          { available: false, rejected: true, suffixes: [".mp4"], mime_types: ["video/mp4"] },
        ],
      },
    });
    expect(accept).toContain(".md");
    expect(accept).toContain(".json");
    expect(accept).toContain("application/json");
    expect(accept).not.toContain(".mp4");
  });

  it("keeps estimated and actual minutes separate in a plan preview", () => {
    const preview = normalizeMasteryPlanPreview({
      preview_id: "preview-1",
      total_estimated_task_minutes: 45,
      days: [{
        date: "2026-08-13",
        tasks: [{ planned_task_minutes: 45, actual_elapsed_minutes: 7 }],
      }],
      activation_blockers: [{ reason: "review required" }],
      extension_draft: { required: true, requires_confirmation: true },
    });
    expect(preview.tasks[0]).toMatchObject({
      estimated_task_minutes: 45,
      actual_elapsed_minutes: 7,
    });
    expect(preview.activation_blockers).toHaveLength(1);
    expect(preview.extension_draft.requires_confirmation).toBe(true);
    expect(preview.feasible).toBe(false);
  });
});
