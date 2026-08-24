import { expect, test } from "@playwright/test"

test("local licensed Live2D model exposes real internal actions without resizing the stage", async ({ page }, testInfo) => {
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  page.on("console", message => {
    if (message.type() === "error") consoleErrors.push(message.text())
  })
  page.on("pageerror", error => pageErrors.push(error.message))

  await page.route("**/*", route => {
    const url = new URL(route.request().url())
    if (url.protocol === "data:" || url.hostname === "127.0.0.1" || url.hostname === "localhost") {
      void route.continue()
      return
    }
    void route.abort("blockedbyclient")
  })

  await page.route("**/api/active-teaching/workspaces/live2d-local-acceptance/snapshot", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      workspace_id: "live2d-local-acceptance",
      materials: [],
      receipt: null,
      knowledge_graph: { knowledge_points: [], edges: [] },
      plan: null,
      session: {
        state: {
          teacher_persona_name: "本机 Live2D 验收教师",
          teacher_visual_manifest_url: "/teacher2d/tsubaki-live2d/manifest.json",
        },
      },
    }),
  }))

  await page.goto("/?tool=teacher-stage&workspaceId=live2d-local-acceptance&state=idle")
  const stage = page.getByTestId("teacher-2d-stage")
  await expect(stage).toHaveAttribute("data-renderer-kind", "live2d2d", { timeout: 30_000 })
  await expect(stage).toHaveAttribute("data-runtime-status", "ready", { timeout: 30_000 })
  await expect(stage).toHaveAttribute("data-fallback-used", "false")
  // Use the product's camera control to make the facial parameter change
  // legible in the evidence pair.  The stage box itself must remain fixed.
  await page.getByRole("button", { name: "脸部" }).click()
  await expect(stage).toHaveAttribute("data-camera-preset", "face")
  const before = await stage.boundingBox()
  expect(before).not.toBeNull()

  await page.getByRole("button", { name: "显示或隐藏教师调试信息" }).click()
  const inspector = page.getByTestId("live2d-motion-inspector")
  await expect(inspector).toBeVisible()
  await inspector.locator("summary").click()
  await expect(inspector.getByRole("group", { name: "Live2D capability summary" })).toContainText("Parameters:")
  await expect(inspector.getByRole("list", { name: "Native motions" })).toContainText("Cry:0")
  await expect(inspector.getByRole("list", { name: "Expressions" })).toContainText("blush")
  await expect(inspector.getByRole("button", { name: "Play BLINK" })).toBeVisible()
  await expect(inspector.getByRole("button", { name: "Play NOD" })).toBeVisible()
  await expect(inspector.getByRole("button", { name: "Play CRY" })).toBeVisible()
  await expect(inspector.getByRole("button", { name: "Play NO_HIGHLIGHTS" })).toBeVisible()

  const after = await stage.boundingBox()
  expect(after).toEqual(before)

  const baselinePath = testInfo.outputPath("live2d-motion-inspector-idle-baseline.png")
  await page.screenshot({ path: baselinePath, fullPage: true })
  await testInfo.attach("live2d-motion-inspector-idle-baseline", { path: baselinePath, contentType: "image/png" })

  await inspector.getByRole("button", { name: "Play BLINK" }).click()
  await expect(page.getByTestId("teacher-2d-debug")).toContainText('"animation": "BLINK"')
  // BLINK's normalized parameter curve reaches its closed-eye keyframe at
  // 50% of the 500 ms action.  Freeze there so the visual receipt is directly
  // comparable with the idle baseline above.
  await page.waitForTimeout(210)
  await inspector.getByRole("button", { name: "Freeze" }).click()
  const frozen = await stage.boundingBox()
  expect(frozen).toEqual(before)
  const screenshotPath = testInfo.outputPath("live2d-motion-inspector-blink-center-frozen.png")
  await page.screenshot({ path: screenshotPath, fullPage: true })
  await testInfo.attach("live2d-motion-inspector-blink-center-frozen", { path: screenshotPath, contentType: "image/png" })
  await inspector.getByRole("button", { name: "Resume" }).click()
  await inspector.getByRole("button", { name: "Restore authoritative state" }).click()
  await expect(stage).toHaveAttribute("data-semantic-state", "idle")
  const restored = await stage.boundingBox()
  expect(restored).toEqual(before)

  expect(pageErrors).toEqual([])
  expect(consoleErrors.filter(message => !message.includes("ERR_BLOCKED_BY_CLIENT"))).toEqual([])
})
