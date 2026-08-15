import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { TeacherTurnV1Renderer } from "../features/active-teaching/ActiveTeachingChainView";

describe("TeacherTurnV1 renderer", () => {
  afterEach(cleanup);
  it("keeps block order, renders code and steps, and skips raw HTML", () => {
    render(<TeacherTurnV1Renderer turn={{
      state: "讲解中",
      concept: "**核心概念**",
      evidence: "资料证据",
      blocks: [
        { type: "markdown", content: "第一块<script>alert('unsafe')</script><img src=x onerror=alert(1)>" },
        { type: "code", language: "python", code: "print('safe')" },
        { type: "worked_example", content: "先说明输入与输出", language: "python", code: "print('observable')" },
        { type: "steps", items: ["步骤一", "步骤二"] },
      ],
      check: "请解释原因",
      expected_learner_action: "提交你的答案",
    }} />);
    const root = screen.getByTestId("teacher-turn-v1");
    expect(root.textContent).toContain("第一块");
    expect(root.textContent).toContain("print('safe')");
    expect(root.textContent).toContain("先说明输入与输出");
    expect(root.textContent).toContain("print('observable')");
    expect(screen.getByTestId("teacher-turn-steps-3")).toHaveTextContent("步骤一");
    expect(root.textContent).toContain("请解释原因");
    expect(root.textContent).toContain("提交你的答案");
    expect(root.querySelector("script")).toBeNull();
    expect(root.querySelector("img")).toBeNull();
  });

  it("does not treat legacy strings as structured blocks", () => {
    render(<TeacherTurnV1Renderer turn={{ blocks: ["legacy **markdown**"] }} />);
    expect(screen.getByTestId("teacher-turn-v1")).toHaveTextContent("legacy markdown");
  });
});
