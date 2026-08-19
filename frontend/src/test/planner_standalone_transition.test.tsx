import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../features/casual-chat/CasualTeacherChat", () => ({
  CasualTeacherChat: ({ onStartPlan }: { onStartPlan?: (handoff: any) => void }) => (
    <button
      data-testid="confirm-planner-resource"
      onClick={() => onStartPlan?.({
        goal: "掌握线性代数",
        resource: { title: "线性代数原文", url: "https://example.test/linear-algebra" },
        created_at: "2026-08-18T00:00:00Z",
      })}
    >
      采用资料
    </button>
  ),
}));

vi.mock("../features/active-teaching/ActiveTeachingChainView", () => ({
  ActiveTeachingChainView: (props: any) => (
    <div
      data-compact={String(props.compactPlannerFlow)}
      data-create-request={String(props.createRequest)}
      data-testid="final-plan-editor"
    />
  ),
}));

import { StandalonePlannerTool } from "../App";

describe("standalone planner handoff", () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  it("stays inside the desktop planner and opens the compact final plan editor", () => {
    window.history.replaceState({}, "", "/?tool=planner");
    const onStartPlan = vi.fn();

    render(<StandalonePlannerTool workspaceId="general-learning" onStartPlan={onStartPlan} />);
    fireEvent.click(screen.getByTestId("confirm-planner-resource"));

    expect(onStartPlan).toHaveBeenCalledOnce();
    expect(screen.getByTestId("standalone-planner-finalize")).toBeInTheDocument();
    expect(screen.getByTestId("final-plan-editor")).toHaveAttribute("data-compact", "true");
    expect(screen.getByTestId("final-plan-editor")).toHaveAttribute("data-create-request", "1");
    expect(window.location.search).toBe("?tool=planner");
    expect(screen.queryByTestId("confirm-planner-resource")).not.toBeInTheDocument();
  });
});
