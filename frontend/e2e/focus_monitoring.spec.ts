import { expect, test } from "@playwright/test";
import path from "node:path";


test("focus mode asks first, monitors locally, and saves only a compact result", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const materialPath = path.resolve(
    process.cwd(),
    "../demo/generic_python_learning_material.md",
  );
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto("/");
  await page.getByTestId("nav-active").click();
  await page.getByTestId("active-new-task").click();
  const goal = `验证浏览器本地专注模式 ${Date.now()}`;
  await page.getByTestId("active-goal").fill(goal);
  await page.getByTestId("active-material-file").setInputFiles(materialPath);
  await page.getByTestId("active-upload").click();
  await expect(page.getByRole("heading", { name: goal })).toBeVisible({
    timeout: 60_000,
  });
  const planPreview = page.getByTestId("active-plan-preview");
  const providerGate = page
    .getByRole("alert")
    .filter({ hasText: "知识地图 v1 需要已验证的真实 Provider" });
  await expect
    .poll(async () => (await planPreview.count()) > 0 || (await providerGate.count()) > 0, {
      timeout: 60_000,
    })
    .toBeTruthy();
  if (await providerGate.count()) {
    test.info().annotations.push({
      type: "provider-gate",
      description: "Plan preview requires a verified local Credential Locker Provider reference before focus consent can be reached.",
    });
    expect(consoleErrors.every((message) => /status of (404|428) \(/.test(message))).toBeTruthy();
    return;
  }
  // A generated plan is only a preview until the learner explicitly activates
  // it. Starting the teacher before activation is intentionally blocked.
  await expect(page.getByTestId("active-plan-preview")).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByTestId("active-plan-activation-blockers")).toHaveCount(0);
  const activatePlan = page.getByTestId("active-plan-activate");
  await expect(activatePlan).toBeEnabled();
  await activatePlan.click();
  await expect(page.getByTestId("active-plan-activation-result")).toBeVisible({
    timeout: 60_000,
  });
  const startTeacher = page.getByTestId("active-start-teacher");
  await expect(startTeacher).toBeEnabled({ timeout: 30_000 });
  await startTeacher.click();
  await expect(page.getByTestId("focus-monitor-panel")).toBeVisible({
    timeout: 30_000,
  });

  await expect(page.locator("video")).toHaveCount(0);
  await page.getByTestId("focus-monitor-open-consent").click();
  await expect(page.getByTestId("focus-monitor-consent")).toContainText(
    "不录屏、不录音、不上传图片或视频",
  );
  await expect(page.getByTestId("focus-monitor-consent")).toContainText(
    "只保存离开/恢复等关键节点",
  );

  await page.getByTestId("focus-monitor-confirm").click();
  const finishButton = page.getByTestId("focus-monitor-finish");
  const cameraError = page.getByTestId("focus-monitor-error");
  await expect
    .poll(async () => (await finishButton.isVisible()) || (await cameraError.isVisible()), {
      timeout: 45_000,
    })
    .toBeTruthy();
  if (await cameraError.isVisible()) {
    // Playwright's isolated browser has no user authorization to grant. Keep
    // the consent and plan-activation assertions machine-verifiable, and make
    // the remaining human camera gate explicit instead of skipping it.
    await expect(cameraError).toContainText("没有授予摄像头权限");
    test.info().annotations.push({
      type: "human-gate",
      description: "Camera permission must be granted by a human before the local focus session can start.",
    });
  } else {
    await expect(page.getByLabel("专注模式本机摄像头预览")).toBeVisible();
    await expect(page.getByTestId("focus-monitor-panel")).toContainText(
      "检测本身不消耗 AI API",
    );
    await page.getByTestId("focus-monitor-panel").screenshot({
      path: "output/playwright/focus-monitoring-active.png",
    });

    await finishButton.click();
    await expect(page.getByTestId("focus-monitor-latest")).toContainText(
      "上次专注",
      { timeout: 30_000 },
    );

    await page.getByTestId("focus-monitor-panel").screenshot({
      path: "output/playwright/focus-monitoring-summary.png",
    });
  }
  expect(consoleErrors).toEqual([]);
});
