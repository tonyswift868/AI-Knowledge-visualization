import { expect, test } from "@playwright/test";
import path from "node:path";


test("unified task hub creates once, switches plans, and enters learning from calendar", async ({ page }) => {
  test.setTimeout(180_000);
  const pythonMaterial = path.resolve(
    process.cwd(),
    "../demo/generic_python_learning_material.md",
  );
  const physicsMaterial = path.resolve(
    process.cwd(),
    "../demo/generic_physics_learning_material.md",
  );
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto("/");
  await page.getByTestId("nav-active").click();

  await page.getByTestId("active-new-task").click();
  const pythonWorkspace = (await page.getByTestId("active-workspace-id").textContent())!;
  await page.getByTestId("active-goal").fill("学会整数列表求和");
  await page.getByTestId("active-material-file").setInputFiles(pythonMaterial);
  await page.getByTestId("active-upload").click();
  await expect(page.getByTestId("active-plan-feasible")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("active-knowledge-map")).toContainText("整数列表求和");
  await expect(page.getByTestId(`active-task-switch-${pythonWorkspace}`)).toBeVisible();

  await page.getByTestId("active-new-task").click();
  const physicsWorkspace = (await page.getByTestId("active-workspace-id").textContent())!;
  expect(physicsWorkspace).not.toBe(pythonWorkspace);
  await page.getByTestId("active-goal").fill("理解牛顿运动定律和受力图");
  await page.getByTestId("active-material-file").setInputFiles(physicsMaterial);
  await page.getByTestId("active-upload").click();
  await expect(page.getByTestId("active-plan-feasible")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("active-knowledge-map")).toContainText("牛顿运动定律");
  await expect(page.getByTestId(`active-task-switch-${physicsWorkspace}`)).toBeVisible();
  await expect(page.getByTestId(`active-task-switch-${pythonWorkspace}`)).toBeVisible();

  await page.getByTestId(`active-task-switch-${pythonWorkspace}`).click();
  await expect(page.getByTestId("active-workspace-id")).toHaveText(pythonWorkspace);
  await expect(page.getByTestId("active-knowledge-map")).toContainText("整数列表求和");
  await page.locator('[data-testid^="active-calendar-task-"]').first().click();
  await expect(page.getByTestId("active-conversation-history")).toContainText(
    "整数列表求和",
    { timeout: 30_000 },
  );
  await expect(page.getByTestId("active-local-knowledge-base")).toContainText(
    "不会检索其他任务",
  );

  await page.getByTestId(`active-task-switch-${physicsWorkspace}`).click();
  await expect(page.getByTestId("active-workspace-id")).toHaveText(physicsWorkspace);
  await expect(page.getByTestId("active-knowledge-map")).toContainText("牛顿运动定律");
  await page.locator('[data-testid^="active-calendar-task-"]').first().click();
  await expect(page.getByTestId("active-conversation-history")).toContainText(
    "牛顿运动定律",
    { timeout: 30_000 },
  );
  expect(consoleErrors).toEqual([]);
});

test("home exposes one task path and archived tasks can be restored", async ({ page }) => {
  test.setTimeout(180_000);
  const materialPath = path.resolve(
    process.cwd(),
    "../demo/generic_physics_learning_material.md",
  );

  await page.goto("/");
  await expect(page.getByTestId("nav-create")).toHaveCount(0);
  await expect(page.getByTestId("nav-plan")).toHaveCount(0);
  await page.getByRole("button", { name: "新建学习任务" }).click();
  await expect(page.getByTestId("active-teaching-chain")).toBeVisible();
  await expect(page.getByRole("heading", { name: "创建一个新的学习任务" })).toBeVisible();
  const draftGoal = `刷新后仍保留的草稿 ${Date.now()}`;
  await page.getByTestId("active-goal").fill(draftGoal);
  await page.getByTestId("active-teacher-persona").fill("耐心主动、每一步都检查理解");
  await expect(page.getByTestId("active-draft-status")).toContainText("草稿已自动保存");
  await page.reload();
  await expect(page.getByTestId("active-goal")).toHaveValue(draftGoal);
  await expect(page.getByTestId("active-teacher-persona")).toHaveValue(
    "耐心主动、每一步都检查理解",
  );

  await page.getByTestId("active-new-task").click();
  const workspaceId = (await page.getByTestId("active-workspace-id").textContent())!;
  await page.getByTestId("active-goal").fill("归档后仍能恢复的物理学习任务");
  await page.getByTestId("active-material-file").setInputFiles(materialPath);
  await page.getByTestId("active-upload").click();
  await expect(page.getByTestId(`active-task-switch-${workspaceId}`)).toBeVisible({
    timeout: 60_000,
  });

  await page.getByTestId(`active-task-archive-${workspaceId}`).click();
  await expect(page.getByTestId(`active-task-switch-${workspaceId}`)).toHaveCount(0);
  await page.getByTestId("active-show-archived").click();
  await expect(page.getByTestId(`active-task-switch-${workspaceId}`)).toContainText("已归档");
  await page.getByTestId(`active-task-archive-${workspaceId}`).click();
  await expect(page.getByTestId(`active-task-switch-${workspaceId}`)).not.toContainText("已归档");
});


test("an indexed-only workspace can finish task creation without reselecting its file", async ({ page }) => {
  const workspaceId = `indexed-only-${Date.now()}`;
  const upload = await page.request.post(
    `/api/active-teaching/workspaces/${workspaceId}/materials`,
    {
      multipart: {
        file: {
          name: "indexed-only.md",
          mimeType: "text/markdown",
          buffer: Buffer.from(
            "# 第一章\n知识点：理解基础定义。\n\n# 第二章\n知识点：应用基础定义解决问题。",
            "utf8",
          ),
        },
      },
    },
  );
  expect(upload.ok()).toBeTruthy();
  await page.addInitScript((activeWorkspaceId) => {
    localStorage.setItem("mentorflow_last_tab_v1", "active");
    localStorage.setItem("mentorflow_active_teaching_workspace_v1", activeWorkspaceId);
  }, workspaceId);

  await page.goto("/");
  await expect(page.getByTestId("active-local-knowledge-base")).toContainText(
    "当前资料库：已索引，可检索",
  );
  await expect(page.getByTestId("active-local-knowledge-base")).toContainText(
    "资料已经保存，但任务尚未建立",
  );
  await expect(page.getByTestId("active-save-receipt")).toHaveCount(0);
  await page.getByRole("button", { name: "使用已保存资料继续创建任务" }).click();

  await expect(page.getByTestId("active-save-receipt")).toBeVisible();
  await expect(page.getByTestId("active-point-count")).not.toHaveText("0");
  await expect(page.getByTestId("active-plan-budget")).toBeVisible();
});


test("arbitrary material drives plan, remediation, verified practice, and persistence", async ({ page }) => {
  test.setTimeout(180_000);
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
  await page.getByTestId("active-new-task").click();
  const generatedWorkspaceId = (await page.getByTestId("active-workspace-id").textContent())!;
  await expect(page.getByTestId("active-system-allocation")).toContainText("系统分配每日投入");
  await expect(page.getByTestId("active-daily-minutes")).toHaveCount(0);
  await page.getByTestId("active-foundation").selectOption("zero_foundation");
  await page.getByTestId("active-teacher-persona").fill("耐心、有趣，先用生活例子再映射回资料");
  await page.getByTestId("active-learner-interests").fill("游戏和做饭");
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
  await expect(page.getByTestId("active-material-assessment")).toContainText(
    "通用资料评估",
  );
  await expect(page.getByTestId("active-material-assessment")).toContainText(
    "证据覆盖 100%",
  );
  await expect(page.getByTestId("active-plan-budget")).toHaveText("20");
  await expect(page.getByTestId("active-plan-feasible")).toBeVisible();
  await expect(page.getByTestId("active-expected-elapsed-range")).toContainText(
    "不是任务完成量",
  );
  await expect(page.getByTestId("active-plan-calendar")).toContainText("首学");

  await page.getByTestId("active-start-teacher").click();
  await expect(page.getByTestId("active-conversation-history")).toContainText("explain", {
    timeout: 30_000,
  });
  await expect(page.getByTestId("active-conversation-history")).toContainText(
    "生活类比（帮助理解，不是资料原文）",
  );
  await expect(page.getByTestId("active-conversation-history")).toContainText(
    "这个类比只帮助理解结构，不会替资料补写事实",
  );
  await expect(page.getByTestId("active-latest-teacher")).toContainText("invite_questions");
  await expect(page.getByTestId("active-latest-teacher")).toContainText("字符");

  await page.getByTestId("active-teacher-input").fill("开始练习");
  await page.getByTestId("active-send-teacher").click();
  await expect(page.getByTestId("active-session-state")).toContainText(
    "check_understanding",
  );

  await page.getByTestId("active-teacher-input").fill("我不理解这一段");
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
  await expect(page.getByTestId("active-workspace-id")).toHaveText(generatedWorkspaceId);
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
