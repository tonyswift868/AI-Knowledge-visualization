import { expect, test } from "@playwright/test";


test("v1 map and teacher remain evidence-driven, keyboard accessible, and HTML-safe", async ({ page }) => {
  await page.goto("/e2e-fixtures/knowledge-map-v1.html");

  await expect(page.getByTestId("active-knowledge-map-v1")).toBeVisible();
  const list = page.getByRole("region", { name: "知识地图键盘列表" });
  await expect(list.getByRole("button", { name: /Sentinel Material Concept/ })).toBeVisible();
  await expect(list.getByText("Hidden Common Concept")).toHaveCount(0);

  await list.getByRole("button", { name: /Sentinel Material Concept/ }).focus();
  await expect(list.getByRole("button", { name: /Sentinel Material Concept/ })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("active-knowledge-map-evidence")).toContainText("Trusted material excerpt.");

  await page.getByLabel("显示 model_common").check();
  await expect(list.getByRole("button", { name: /Hidden Common Concept/ })).toBeVisible();
  await list.getByRole("button", { name: /prerequisite/ }).click();
  await expect(page.getByTestId("active-knowledge-map-evidence")).toContainText("Edge evidence excerpt.");

  await page.getByRole("button", { name: "确认候选" }).click();
  await expect(page.getByTestId("browser-decision")).toHaveText("candidate-1:confirm");

  const turn = page.getByTestId("teacher-turn-v1");
  await expect(turn).toContainText("安全讲解");
  await expect(turn).toContainText("print('browser-safe')");
  await expect(turn).toContainText("请说明它和证据的关系");
  await expect(turn.locator("script")).toHaveCount(0);
  await expect(turn.locator("img")).toHaveCount(0);
});
