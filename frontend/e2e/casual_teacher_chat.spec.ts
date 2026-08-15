import { expect, test } from "@playwright/test"

test("home casual teacher lounge keeps chat independent and fast", async ({ page }) => {
  const consoleErrors: string[] = []
  const silentWav = Buffer.from("UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQIAAAAAAA==", "base64")
  await page.route("**/api/casual-chat/voice/synthesize", route => route.fulfill({
    status: 200,
    contentType: "audio/wav",
    body: silentWav,
  }))
  page.on("console", message => { if (message.type() === "error") consoleErrors.push(message.text()) })
  await page.goto("/")
  await expect(page.getByTestId("nav-casual")).toBeVisible()
  await page.getByTestId("nav-casual").click()
  const lounge = page.getByTestId("casual-teacher-chat")
  await expect(lounge).toBeVisible()
  await expect(lounge.getByTestId("casual-teacher-portrait")).toBeVisible()
  await lounge.locator('[data-testid^="casual-teacher-option-"]').first().click()
  await lounge.getByTestId("casual-teacher-setting").fill("更活泼一点，多用生活化比喻")
  await lounge.getByTestId("casual-teacher-input").fill("你好，今天有点累")
  await lounge.getByTestId("casual-teacher-send").click()
  await expect(lounge).toContainText("先不用硬撑", { timeout: 30_000 })
  await expect(lounge.getByTestId("casual-teacher-speak").last()).toBeVisible()
  await expect(lounge.getByTestId("casual-teacher-voice-mode")).toContainText("点击喇叭才朗读")
  await lounge.getByTestId("casual-teacher-speak").last().click()
  await expect(lounge.getByRole("status")).toContainText(
    /自然中文女声|播放|尚未安装并启用离线教师声包|教师语音暂不可用/,
    { timeout: 5_000 },
  )
  // A refresh must restore the same phone-like conversation instead of
  // creating a fresh greeting-only window.
  await page.reload()
  await page.getByTestId("nav-casual").click()
  const restoredLounge = page.getByTestId("casual-teacher-chat")
  await expect(restoredLounge).toContainText("先不用硬撑", { timeout: 30_000 })
  await lounge.screenshot({ path: "output/playwright/casual-teacher-chat.png" })
  await page.screenshot({ path: "output/playwright/casual-teacher-chat-full.png" })
  expect(consoleErrors).toEqual([])
})
