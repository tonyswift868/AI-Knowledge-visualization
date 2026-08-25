import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@xyflow/react", () => ({
  MarkerType: { ArrowClosed: "arrowclosed" },
  Position: { Left: "left", Right: "right" },
  Background: () => null,
  Controls: () => null,
  MiniMap: () => null,
  ReactFlow: ({ nodes, edges, onNodeClick }: any) => (
    <div
      data-testid="react-flow"
      data-node-count={nodes.length}
      data-edge-count={edges.length}
    >
      {nodes.map((node: any) => (
        <button
          type="button"
          key={node.id}
          data-testid={`flow-node-${node.id}`}
          onClick={() => onNodeClick?.({}, node)}
        >
          {node.data.label}
        </button>
      ))}
    </div>
  ),
}));

import {
  VerifiedKnowledgeMapExperience,
} from "../features/verified-knowledge-map/VerifiedKnowledgeMapExperience";
import { createKnowledgeMapFixture } from "../features/verified-knowledge-map/fixtures";

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
});

afterEach(cleanup);

describe("VerifiedKnowledgeMapExperience", () => {
  it("opens the full concept detail panel from a React Flow node", () => {
    const envelope = createKnowledgeMapFixture(20);
    envelope.display = {
      ...envelope.display,
      is_demo: true,
      demo_label: "DEMO DATA",
    };
    render(<VerifiedKnowledgeMapExperience initialEnvelope={envelope} />);

    expect(screen.getByTestId("demo-data-badge")).toHaveTextContent("DEMO DATA");
    fireEvent.click(screen.getByTestId("flow-node-fixture-node-0"));

    const detail = screen.getByRole("complementary", { name: "概念详情面板" });
    expect(within(detail).getByRole("heading", { name: "知识概念 1" })).toBeInTheDocument();
    expect(within(detail).getByText("置信度")).toBeInTheDocument();
    expect(within(detail).getByText("验证状态")).toBeInTheDocument();
    expect(within(detail).getByText("关键程度")).toBeInTheDocument();
    expect(within(detail).getByText("证据数量")).toBeInTheDocument();
    expect(within(detail).getByRole("heading", { name: "资料来源" })).toBeInTheDocument();
    expect(within(detail).getByRole("heading", { name: "修复谱系" })).toBeInTheDocument();
    expect(within(detail).getByText(/FIXTURE_REPAIR/)).toBeInTheDocument();
    expect(within(detail).getByRole("heading", { name: "前置概念" })).toBeInTheDocument();
    expect(within(detail).getByRole("heading", { name: "下游依赖" })).toBeInTheDocument();
    expect(within(detail).getByRole("button", { name: "原资料跳转待接入" })).toBeDisabled();
  });

  it("filters the graph to visibly uncertain relations", () => {
    render(
      <VerifiedKnowledgeMapExperience
        initialEnvelope={createKnowledgeMapFixture(50)}
      />,
    );
    const initialEdges = Number(
      screen.getByTestId("react-flow").getAttribute("data-edge-count"),
    );

    fireEvent.click(screen.getByRole("button", { name: "不确定关系" }));

    const uncertainEdges = Number(
      screen.getByTestId("react-flow").getAttribute("data-edge-count"),
    );
    expect(uncertainEdges).toBeGreaterThan(0);
    expect(uncertainEdges).toBeLessThan(initialEdges);
    expect(screen.getByText("可能前置 · 未验证")).toBeInTheDocument();
  });

  it("marks partial maps without presenting them as complete", () => {
    render(
      <VerifiedKnowledgeMapExperience
        initialEnvelope={createKnowledgeMapFixture(10, { status: "PARTIAL" })}
      />,
    );

    expect(screen.getByText("资料尚未完全验证")).toBeInTheDocument();
    expect(screen.getByText(/请勿视为完整资料/)).toBeInTheDocument();
    expect(screen.getByTestId("react-flow")).toBeInTheDocument();
  });

  it("blocks cycle diagnostics instead of rendering a normal graph", () => {
    render(
      <VerifiedKnowledgeMapExperience
        initialEnvelope={createKnowledgeMapFixture(10, { cycle: true })}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "发现知识依赖冲突，需要检查关系",
    );
    expect(screen.getByText(/已停止渲染/)).toBeInTheDocument();
    expect(screen.queryByTestId("react-flow")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("Traceback");
  });

  it("shows an explicit empty state", () => {
    render(
      <VerifiedKnowledgeMapExperience
        initialEnvelope={createKnowledgeMapFixture(0)}
      />,
    );

    expect(screen.getByText("还没有可显示的知识节点")).toBeInTheDocument();
    expect(screen.queryByTestId("react-flow")).not.toBeInTheDocument();
  });
});
