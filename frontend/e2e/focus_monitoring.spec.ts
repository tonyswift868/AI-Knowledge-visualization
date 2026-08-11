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
  await page.getByRole("button", { name: "开始今日课程" }).click();
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
  await expect(page.getByTestId("focus-monitor-finish")).toBeVisible({
    timeout: 45_000,
  });
  await expect(page.getByLabel("专注模式本机摄像头预览")).toBeVisible();
  await expect(page.getByTestId("focus-monitor-panel")).toContainText(
    "检测本身不消耗 AI API",
  );
  await page.getByTestId("focus-monitor-panel").screenshot({
    path: "output/playwright/focus-monitoring-active.png",
  });

  await page.getByTestId("focus-monitor-finish").click();
  await expect(page.getByTestId("focus-monitor-latest")).toContainText(
    "上次专注",
    { timeout: 30_000 },
  );

  await page.getByTestId("focus-monitor-panel").screenshot({
    path: "output/playwright/focus-monitoring-summary.png",
  });
  expect(consoleErrors).toEqual([]);
});
