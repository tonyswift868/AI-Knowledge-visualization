import { expect, test } from "@playwright/test"

type Candidate = {
  candidate_id: string
  url: string
  title: string
  media_kind: "video" | "document"
  direct_user_reference?: boolean
  retained?: boolean
}

const providedCandidates: Candidate[] = [
  { candidate_id: "video-1", url: "https://www.bilibili.com/video/BVfixture1", title: "B站视频 BVfixture1", media_kind: "video", direct_user_reference: true, retained: true },
  { candidate_id: "video-2", url: "https://www.bilibili.com/video/BVfixture2", title: "B站视频 BVfixture2", media_kind: "video", direct_user_reference: true, retained: true },
]

const initialCandidates: Candidate[] = [
  { candidate_id: "note-1", url: "https://notes.example.test/linear-algebra-1", title: "配套笔记 1", media_kind: "document" },
  { candidate_id: "note-2", url: "https://notes.example.test/linear-algebra-2", title: "配套笔记 2", media_kind: "document" },
  { candidate_id: "note-3", url: "https://notes.example.test/linear-algebra-3", title: "配套笔记 3", media_kind: "document" },
]

const appendedCandidates: Candidate[] = [
  { candidate_id: "note-4", url: "https://notes.example.test/linear-algebra-4", title: "配套笔记 4", media_kind: "document" },
  { candidate_id: "note-5", url: "https://notes.example.test/linear-algebra-5", title: "配套笔记 5", media_kind: "document" },
  { candidate_id: "note-6", url: "https://notes.example.test/linear-algebra-6", title: "配套笔记 6", media_kind: "document" },
]

test("planner pages all cards in groups of three, recovers stale cursor, and preserves BV selections", async ({ page }, testInfo) => {
  const requests: Record<string, any>[] = []
  let candidates = [...providedCandidates, initialCandidates[0]]
  let revision = 1
  let discoveryCount = 0
  let stalePageOnce = true
  let pageIndex = 0
  const selected = new Set(["video-1", "video-2"])

  const poolPayload = (reply = "已按你的要求整理候选资料。") => {
    const items = candidates.slice(pageIndex * 3, pageIndex * 3 + 3)
    const pages = Math.max(1, Math.ceil(candidates.length / 3))
    return {
      schema: "mentorflow.planning-resource-bundle.v1",
      planning_session_id: "pool-browser-session",
      revision,
      reply,
      goal_summary: "使用两个 B 站视频学习线性代数，并搭配可靠笔记",
      resources: items,
      candidate_page: {
        items,
        protected_items: providedCandidates,
        provided_items: providedCandidates,
        page: pageIndex + 1,
        page_number: pageIndex + 1,
        page_index: pageIndex,
        page_size: 3,
        total_count: candidates.length,
        qualified_candidate_count: candidates.length,
        held_incomplete_count: 0,
        direct_reference_count: providedCandidates.length,
        new_candidate_count: candidates.length - providedCandidates.length,
        has_more: pageIndex + 1 < pages,
        next_cursor: pageIndex + 1 < pages ? "cursor-page-2" : null,
        previous_cursor: pageIndex > 0 ? "cursor-page-1" : null,
        mode: "server_paged",
        server_paged: true,
      },
      selection: {
        selected: [...selected],
        anchor: "video-1",
        support: [...selected].filter(id => id !== "video-1"),
        rejected: [],
      },
    }
  }

  await page.route("**/api/**", async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname === "/api/provider-sessions/current") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          provider_session_id: "ps-browser-fixture",
          provider: "openai_compatible",
          verified: true,
          base_url: "https://provider.example/v1",
          model_reported: "fixture-model",
        }),
      })
      return
    }
    if (url.pathname === "/api/workspaces") {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
      return
    }
    if (url.pathname.endsWith("/planning-assistant")) {
      const body = request.postDataJSON() as Record<string, any>
      requests.push(body)
      if (!body.pool_action) {
        discoveryCount += 1
        pageIndex = 0
        if (discoveryCount === 2) {
          candidates = [...providedCandidates, initialCandidates[0], ...appendedCandidates]
          revision += 1
        }
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(poolPayload(discoveryCount === 1 ? "已把你提供的两个 BV 与一份新笔记组成第一页。" : "已追加三份配套资料，可以翻到下一页继续选择。")) })
        return
      }
      if (body.pool_action === "page") {
        if (stalePageOnce && (body.cursor || body.next_cursor) === "cursor-page-2") {
          stalePageOnce = false
          await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: { code: "STALE_CURSOR", message: "planning resource pool cursor is stale" } }) })
          return
        }
        pageIndex = (body.cursor || body.next_cursor) === "cursor-page-2" ? 1 : 0
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(poolPayload()) })
        return
      }
      if (body.pool_action === "refresh") {
        pageIndex = 0
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(poolPayload()) })
        return
      }
      const candidateId = String(body.candidate_ids?.[0] || "")
      if (body.pool_action === "select") selected.add(candidateId)
      if (body.pool_action === "deselect") selected.delete(candidateId)
      revision += 1
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(poolPayload()) })
      return
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) })
  })

  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  page.on("console", message => { if (message.type() === "error") consoleErrors.push(message.text()) })
  page.on("pageerror", error => pageErrors.push(error.message))

  await page.goto("/?tool=planner")
  await page.getByTestId("planning-companion-search").click()
  await page.getByTestId("planning-companion-input").fill("我提供 BVfixture1 和 BVfixture2，请自动补一份配套笔记")
  await page.getByTestId("planning-companion-send").click()

  const pool = page.getByTestId("planning-companion-candidate-pool")
  await expect(pool.getByRole("listitem")).toHaveCount(3)
  await expect(pool).toContainText("B站视频 BVfixture1")
  await expect(pool).toContainText("B站视频 BVfixture2")
  await expect(pool).toContainText("配套笔记 1")
  await expect(page.getByTestId("planning-companion-candidate-pool-pinned")).toContainText("已固定选择 · 2 份")
  await expect(page.getByTestId("planning-companion-candidate-pool-pinned")).toContainText("你提供的资料")
  await expect(page.getByTestId("planning-companion-candidate-pool-page-status")).toContainText("共 3 份候选")
  await expect(page.getByTestId("planning-companion-candidate-pool-page-status")).toContainText("2 份由你提供")
  await expect(page.getByTestId("planning-companion-candidate-pool-page-status")).toContainText("1 份新资料")
  await expect(page.getByTestId("planning-companion-candidate-pool-next")).toBeDisabled()

  await page.getByTestId("planning-companion-input").fill("继续找三份配套笔记，不要替换已选视频")
  await page.getByTestId("planning-companion-send").click()
  await expect(page.getByTestId("planning-companion-candidate-pool-page-status")).toContainText("第 1 / 2 页")
  await expect(page.getByTestId("planning-companion-candidate-pool-next")).toBeEnabled()
  const secondDiscovery = requests.filter(body => !body.pool_action)[1]
  expect(secondDiscovery.pool_mode).toBe("append")
  expect(secondDiscovery.selected_resources).toHaveLength(2)

  await page.getByTestId("planning-companion-candidate-pool-next").click()
  await expect(pool.getByRole("listitem")).toHaveCount(3)
  await expect(pool).toContainText("配套笔记 4")
  await expect(pool).toContainText("配套笔记 6")
  await expect(page.getByTestId("planning-companion-candidate-pool-page-status")).toContainText("第 2 / 2 页")
  expect(requests.filter(body => ["page", "refresh"].includes(body.pool_action)).map(body => body.pool_action)).toEqual(["page", "refresh", "page"])
  await expect(page.getByText(/stale/i)).toHaveCount(0)

  await page.getByRole("checkbox", { name: "选择 配套笔记 4" }).check()
  await expect(page.getByTestId("planning-companion-candidate-pool-pinned")).toContainText("已固定选择 · 3 份")
  await expect(page.getByTestId("planning-companion-candidate-pool-pinned")).toContainText("配套笔记 4")

  const screenshotPath = testInfo.outputPath("planning-candidate-pool-page-2.png")
  await page.screenshot({ path: screenshotPath, fullPage: true })
  await testInfo.attach("planning-candidate-pool-page-2", { path: screenshotPath, contentType: "image/png" })

  expect(pageErrors).toEqual([])
  const injectedConflicts = consoleErrors.filter(message => /status of 409/i.test(message))
  expect(injectedConflicts).toHaveLength(1)
  expect(consoleErrors.filter(message => !/status of 409/i.test(message))).toEqual([])
})
