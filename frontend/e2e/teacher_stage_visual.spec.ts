import { expect, test } from "@playwright/test"

const states = ["idle", "questioning", "praising", "shy", "playful_annoyed"] as const

test("distributable teacher renders semantic classroom actions", async ({ page }, testInfo) => {
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  page.on("console", message => {
    if (message.type() === "error") consoleErrors.push(message.text())
  })
  page.on("pageerror", error => pageErrors.push(error.message))

  await page.goto("/?tool=teacher-stage&workspaceId=visual-acceptance&state=idle")
  const stage = page.getByTestId("teacher-2d-stage")
  await expect(stage).toBeVisible()
  await expect(stage).toHaveAttribute("data-renderer-kind", "layered2d")
  await expect(stage).toHaveAttribute("data-runtime-status", "ready", { timeout: 20_000 })
  await expect(stage).toHaveAttribute("data-fallback-used", "false")

  for (const state of states) {
    if (state !== "idle") {
      await page.evaluate(nextState => {
        window.postMessage(
          { type: "mentorflow.teacher-state", state: nextState },
          window.location.origin,
        )
      }, state)
    }
    await expect(stage).toHaveAttribute("data-semantic-state", state)
    await expect(page.getByTestId("teacher-companion-portrait")).toHaveAttribute(
      "data-teacher-state",
      state,
    )
    const screenshotPath = testInfo.outputPath(`teacher-${state}.png`)
    await page.screenshot({ fullPage: true, path: screenshotPath })
    await testInfo.attach(`teacher-${state}`, {
      path: screenshotPath,
      contentType: "image/png",
    })
  }

  expect(pageErrors).toEqual([])
  expect(consoleErrors).toEqual([])
})
