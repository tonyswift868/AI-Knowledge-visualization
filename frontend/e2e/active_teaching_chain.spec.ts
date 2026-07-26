import { expect, test } from "@playwright/test";
import path from "node:path";


test("arbitrary material drives plan, remediation, verified practice, and persistence", async ({ page }) => {
  test.setTimeout(180_000);
  const workspaceId = `generic-e2e-${Date.now()}`;
  const materialPath = path.resolve(
    process.cwd(),
    "../demo/generic_python_learning_material.md",
  );
  const problemSpecPath = path.resolve(
    process.cwd(),
    "../demo/generic_python_problem_spec.json",
  );
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto("/");
  await page.getByTestId("nav-active").click();
  await expect(page.getByTestId("active-teaching-chain")).toBeVisible();
  await page.getByTestId("active-workspace-id").fill(workspaceId);
  await page.getByTestId("active-daily-minutes").fill("120");
  await page.getByTestId("active-material-file").setInputFiles(materialPath);
  await page.getByTestId("active-upload").click();

  await expect(page.getByTestId("active-material-count")).toContainText("资料 1 份", {
    timeout: 60_000,
  });
  await expect(page.getByTestId("active-point-count")).toHaveText("1");
  await expect(page.getByTestId("active-extraction-mode")).toContainText(
    "structural_fallback",
  );
  await expect(page.getByTestId("active-extraction-mode")).toContainText(
    "不冒充 AI 语义抽取",
  );
  await expect(page.getByTestId("active-plan-budget")).toHaveText("120");
  await expect(page.getByTestId("active-plan-feasible")).toBeVisible();
  await expect(page.getByTestId("active-expected-elapsed-range")).toContainText(
    "不是任务完成量",
  );
  await expect(page.getByTestId("active-plan-calendar")).toContainText("首学");

  await page.getByTestId("active-start-teacher").click();
  await expect(page.getByTestId("active-latest-teacher")).toContainText("explain");
  await expect(page.getByTestId("active-latest-teacher")).toContainText("字符");

  await page.getByTestId("active-teacher-input").fill("我不理解，为什么这样做？");
  await page.getByTestId("active-send-teacher").click();
  await expect(page.getByTestId("active-session-state")).toContainText(
    "remediation_explain",
  );
  await expect(page.getByTestId("active-session-state")).toContainText("补救栈 1");
  await expect(page.getByTestId("active-replan-summary")).toContainText(
    "remediation_inserted",
  );

  await page.getByTestId("active-teacher-input").fill("能再给一个更简单的例子吗？");
  await page.getByTestId("active-send-teacher").click();
  await expect(page.getByTestId("active-session-state")).toContainText(
    "remediation_example",
  );

  await page.getByTestId("active-teacher-input").fill("我明白了，可以继续");
  await page.getByTestId("active-send-teacher").click();
  await expect(page.getByTestId("active-latest-teacher")).toContainText(
    "resume_main_route",
  );
  await expect(page.getByTestId("active-session-state")).toContainText("补救栈 0");

  await page.reload();
  await page.getByTestId("nav-active").click();
  await expect(page.getByTestId("active-workspace-id")).toHaveValue(workspaceId);
  await expect(page.getByTestId("active-session-state")).toContainText(
    "check_understanding",
  );
  await expect(page.getByTestId("active-completion-report")).toContainText(
    "仍在学习或验证中",
  );

  await page.getByTestId("active-teacher-input").fill("量子纠缠的贝尔不等式是什么？");
  await page.getByTestId("active-send-teacher").click();
  await expect(page.getByTestId("active-latest-teacher")).toContainText(
    "资料中无法确认",
  );

  await page.getByTestId("active-material-file").setInputFiles(problemSpecPath);
  await page.getByTestId("active-upload").click();
  await expect(page.getByTestId("active-material-count")).toContainText("资料 2 份", {
    timeout: 60_000,
  });
  await page.getByRole("button", { name: "导入经来源验证的题目与测试" }).click();
  // Rebuilding the graph after adding a new source deliberately invalidates the
  // old route. Start/resume creates a session bound to the new persisted plan.
  await page.getByTestId("active-start-teacher").click();
  await expect(page.getByTestId("active-code-problem")).toContainText(
    "实现整数列表求和",
    { timeout: 30_000 },
  );
  await expect(page.getByTestId("active-code-practice")).toContainText(
    /样例 1\s*·\s*边界 2\s*·\s*隐藏 1/,
  );

  await page.getByTestId("active-code-editor").fill(
    "def solve(values):\n    return 0",
  );
  await page.getByTestId("active-submit-code").click();
  await expect(page.getByTestId("active-code-result")).toContainText("wrong_answer");
  await expect(page.getByTestId("active-code-result")).toContainText("隐藏测试 0/1");

  await page.getByTestId("active-code-editor").fill(
    "def solve(values):\n    return sum(values)",
  );
  await page.getByRole("button", { name: "保存草稿" }).click();
  await page.getByTestId("active-submit-code").click();
  await expect(page.getByTestId("active-code-result")).toContainText("accepted");
  await expect(page.getByTestId("active-code-result")).toContainText("通过 4/4");
  await expect(page.getByTestId("active-code-result")).toContainText("隐藏测试 1/1");

  for (const level of [1, 2, 3]) {
    await page.getByTestId("active-code-hint").click();
    await expect(page.getByTestId("active-code-hint-content")).toContainText(
      `提示 ${level}`,
    );
  }
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByTestId("active-code-hint").click();
  await expect(page.getByTestId("active-code-hint-content")).toContainText("提示 4");
  await expect(page.getByTestId("active-code-hint-content")).toContainText("def solve");

  await page.reload();
  await page.getByTestId("nav-active").click();
  await expect(page.getByTestId("active-code-editor")).toHaveValue(
    "def solve(values):\n    return sum(values)",
  );
  await expect(page.getByTestId("active-code-result")).toContainText("accepted");
  await expect(page.getByTestId("active-mistake-queue")).toContainText(
    "已由后续独立通过解决",
  );
  expect(consoleErrors).toEqual([]);
});
