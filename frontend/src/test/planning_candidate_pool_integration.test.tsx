import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const activeRequestMock = vi.hoisted(() => ({
  activeRequest: vi.fn(),
}))

vi.mock("../features/active-teaching/api", async importOriginal => {
  const actual = await importOriginal<typeof import("../features/active-teaching/api")>()
  return { ...actual, activeRequest: activeRequestMock.activeRequest }
})

vi.mock("../features/voice/localTeacherVoice", () => ({
  speakLocalTeacherVoice: vi.fn(),
  stopLocalTeacherVoice: vi.fn(),
}))

import { ActiveTeachingChainView } from "../features/active-teaching/ActiveTeachingChainView"
import { CasualTeacherChat } from "../features/casual-chat/CasualTeacherChat"
import { planningPoolStorageKey } from "../features/planning/planningCandidatePoolClient"
import { useMentorStore } from "../stores/useMentorStore"

type Fixture = {
  candidate_id: string
  url: string
  title: string
  media_kind: "video" | "document"
  direct_user_reference?: boolean
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

const fixture: Fixture[] = [
  { candidate_id: "note-3", url: "https://fixture.test/note-3", title: "文字笔记 3", media_kind: "document" },
  { candidate_id: "note-4", url: "https://fixture.test/note-4", title: "文字笔记 4", media_kind: "document" },
  { candidate_id: "note-1", url: "https://fixture.test/note-1", title: "文字笔记 1", media_kind: "document" },
  { candidate_id: "note-5", url: "https://fixture.test/note-5", title: "文字笔记 5", media_kind: "document" },
  { candidate_id: "note-6", url: "https://fixture.test/note-6", title: "文字笔记 6", media_kind: "document" },
  { candidate_id: "note-2", url: "https://fixture.test/note-2", title: "文字笔记 2", media_kind: "document" },
]

const pagePayload = (
  pageIndex: number,
  revision: number,
  session = "pool-integration-session",
  selected: Set<string> = new Set(),
  anchor = "",
  rejected: Set<string> = new Set(),
) => {
  const visible = fixture.filter(item => !rejected.has(item.candidate_id))
  const items = visible.slice(pageIndex * 3, pageIndex * 3 + 3)
  const selectedIds = [...selected]
  return {
    planning_session_id: session,
    revision,
    resources: items,
    candidate_page: {
      items,
      page: pageIndex + 1,
      page_number: pageIndex + 1,
      page_index: pageIndex,
      page_size: 3,
      total_count: visible.length,
      has_more: pageIndex === 0,
      next_cursor: pageIndex === 0 ? "cursor-page-2" : undefined,
      previous_cursor: pageIndex > 0 ? "cursor-page-1" : undefined,
      mode: "server_paged",
      server_paged: true,
    },
    selection: {
      selected: selectedIds,
      anchor: anchor || null,
      support: selectedIds.filter(id => id !== anchor),
      rejected: [...rejected],
    },
  }
}

function installPoolMock(options: {
  delayFirstSelect?: ReturnType<typeof deferred<void>>
  delayFirstAnchor?: ReturnType<typeof deferred<void>>
  staleFirstPage?: boolean
} = {}) {
  let revision = 1
  let pageIndex = 0
  let anchor = ""
  const selected = new Set<string>()
  const rejected = new Set<string>()
  const calls: Array<{ path: string; body: Record<string, any> }> = []
  activeRequestMock.activeRequest.mockImplementation(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : {}
    calls.push({ path, body })
    if (path.includes("/planning-assistant")) {
      if (!body.pool_action) {
        pageIndex = 0
        return {
          ...pagePayload(pageIndex, revision),
          reply: "已整理候选资料。",
          goal_summary: "完成候选池集成测试",
        }
      }
      if (body.pool_action === "page") {
        if (options.staleFirstPage) {
          options.staleFirstPage = false
          const error = new Error("planning resource pool cursor is stale") as Error & { code?: string }
          error.code = "STALE_CURSOR"
          throw error
        }
        pageIndex = body.cursor === "cursor-page-2" ? 1 : 0
        return pagePayload(pageIndex, revision, "pool-integration-session", selected, anchor, rejected)
      }
      const candidateId = String(body.candidate_ids?.[0] || "")
      if (body.pool_action === "select") {
        if (options.delayFirstSelect) {
          const pending = options.delayFirstSelect
          options.delayFirstSelect = undefined
          await pending.promise
        }
        selected.add(candidateId)
      }
      if (body.pool_action === "set_anchor") {
        if (options.delayFirstAnchor) {
          const pending = options.delayFirstAnchor
          options.delayFirstAnchor = undefined
          await pending.promise
        }
      }
      if (body.pool_action === "deselect") {
        selected.delete(candidateId)
        if (anchor === candidateId) anchor = ""
      }
      if (body.pool_action === "set_anchor") {
        anchor = String(body.anchor_candidate_id || candidateId)
        selected.add(anchor)
      }
      if (body.pool_action === "reject") rejected.add(candidateId)
      if (body.pool_action === "undo") rejected.delete(candidateId)
      revision += 1
      return pagePayload(pageIndex, revision, "pool-integration-session", selected, anchor, rejected)
    }
    if (path.includes("/snapshot")) return { workspace_id: "active-pool", materials: [], receipt: null, knowledge_graph: { knowledge_points: [], edges: [] }, plan: null, session: null }
    if (path === "/workspaces?include_archived=false" || path.startsWith("/workspaces?")) return { items: [] }
    if (path.endsWith("/teacher-personas")) return { items: [] }
    if (path.endsWith("/material-capabilities")) return { capabilities: [] }
    if (path.includes("/knowledge-map/candidates")) return { candidates: [] }
    if (path.includes("/knowledge-map?revision=active")) return { knowledge_points: [], edges: [], candidates: [], warnings: [] }
    return {}
  })
  return calls
}

type ExplicitPoolMockOptions = {
  initialCount?: number
  initialShortfall?: number
  initialQualifiedCount?: number
  initialHeldIncompleteCount?: number
}

const explicitFixture: Fixture[] = Array.from({ length: 6 }, (_, index) => ({
  candidate_id: `resource-${index + 1}`,
  url: `https://resources.example/${index + 1}`,
  title: `资料 ${index + 1}`,
  media_kind: "document",
}))

function explicitPoolPayload({
  count,
  pageIndex = 0,
  revision,
  selected = new Set<string>(),
  shortfall = 0,
  qualifiedCount,
  heldIncompleteCount = 0,
}: {
  count: number
  pageIndex?: number
  revision: number
  selected?: Set<string>
  shortfall?: number
  qualifiedCount?: number
  heldIncompleteCount?: number
}) {
  const items = explicitFixture.slice(0, count)
  const selectedIds = [...selected]
  return {
    planning_session_id: "explicit-pool-session",
    revision,
    resources: items,
    cache: { provider_ref: "cache-from-first-turn" },
    candidate_page: {
      items,
      page: pageIndex + 1,
      page_number: pageIndex + 1,
      page_index: pageIndex,
      page_size: 3,
      total_count: count,
      shortfall_count: shortfall,
      ...(qualifiedCount === undefined ? {} : { qualified_candidate_count: qualifiedCount }),
      held_incomplete_count: heldIncompleteCount,
      page_complete: shortfall === 0 && heldIncompleteCount === 0,
      has_more: false,
      mode: "append",
      server_paged: false,
    },
    selection: {
      selected: selectedIds,
      anchor: selectedIds[0] || null,
      support: selectedIds.slice(1),
      rejected: [],
    },
    reply: "已更新候选资料。",
    goal_summary: "完成候选池回归测试",
  }
}

function installExplicitPoolMock(options: ExplicitPoolMockOptions = {}) {
  let revision = 1
  let selected = new Set<string>()
  const calls: Array<{ path: string; body: Record<string, any> }> = []
  const initialCount = options.initialCount ?? 2
  const initialShortfall = options.initialShortfall ?? 2
  activeRequestMock.activeRequest.mockImplementation(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : {}
    calls.push({ path, body })
    if (path.includes("/planning-assistant")) {
      if (!body.pool_action && !body.candidate_discovery_mode) {
        return explicitPoolPayload({
          count: initialCount,
          revision,
          shortfall: initialShortfall,
          qualifiedCount: options.initialQualifiedCount,
          heldIncompleteCount: options.initialHeldIncompleteCount,
        })
      }
      if (body.pool_action === "select" || body.pool_action === "set_anchor") {
        const id = String(body.candidate_ids?.[0] || body.anchor_candidate_id || "")
        if (id) selected = new Set([...selected, id])
        revision += 1
        return explicitPoolPayload({ count: 6, revision, selected })
      }
      if (body.pool_action === "deselect") {
        const id = String(body.candidate_ids?.[0] || "")
        selected = new Set([...selected].filter(item => item !== id))
        revision += 1
        return explicitPoolPayload({ count: 6, revision, selected })
      }
      if (body.pool_action === "page") {
        revision += 1
        return explicitPoolPayload({ count: 6, pageIndex: Number(body.page_index || body.page || 0), revision, selected })
      }
      if (body.candidate_discovery_mode === "fill_page") {
        revision += 1
        return explicitPoolPayload({ count: 4, revision, selected })
      }
      if (body.candidate_discovery_mode === "add_page") {
        revision += 1
        return explicitPoolPayload({ count: 6, revision, selected })
      }
    }
    if (path.includes("/snapshot")) return { workspace_id: "explicit-pool", materials: [], receipt: null, knowledge_graph: { knowledge_points: [], edges: [] }, plan: null, session: null }
    if (path === "/workspaces?include_archived=false" || path.startsWith("/workspaces?")) return { items: [] }
    if (path.endsWith("/teacher-personas")) return { items: [] }
    if (path.endsWith("/material-capabilities")) return { capabilities: [] }
    if (path.includes("/knowledge-map/candidates")) return { candidates: [] }
    if (path.includes("/knowledge-map?revision=active")) return { knowledge_points: [], edges: [], candidates: [], warnings: [] }
    return {}
  })
  return calls
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  useMentorStore.getState().clearProvider()
})

describe("planning candidate pool integration", () => {
  it("ignores a late casual discovery response after switching workspaces", async () => {
    const oldResponse = deferred<any>()
    const calls: Array<{ path: string; body: Record<string, any> }> = []
    activeRequestMock.activeRequest.mockImplementation(async (path: string, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : {}
      calls.push({ path, body })
      if (path.includes("/planning-assistant")) {
        const workspace = decodeURIComponent(path.split("/workspaces/")[1]?.split("/")[0] || "")
        if (!body.pool_action && workspace === "casual-race-old") return oldResponse.promise
        if (!body.pool_action && workspace === "casual-race-new") {
          const payload = pagePayload(0, 1, "casual-race-new-session")
          const resources = [{ ...fixture[2], title: "新空间资料" }]
          return {
            ...payload,
            resources,
            candidate_page: { ...payload.candidate_page, items: resources },
            reply: "新空间回复",
          }
        }
      }
      return {}
    })

    const { rerender } = render(<CasualTeacherChat workspaceId="casual-race-old" mode="planner" />)
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), { target: { value: "旧空间查询" } })
    fireEvent.click(screen.getByTestId("planning-companion-send"))
    await waitFor(() => expect(calls.some(item => item.path.includes("/workspaces/casual-race-old/"))).toBe(true))

    rerender(<CasualTeacherChat workspaceId="casual-race-new" mode="planner" />)
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), { target: { value: "新空间查询" } })
    fireEvent.click(screen.getByTestId("planning-companion-send"))
    await screen.findByText("新空间资料")

    const oldPayload = pagePayload(0, 99, "casual-race-old-session")
    const oldResources = [{ ...fixture[0], title: "旧空间迟到资料" }]
    oldResponse.resolve({
      ...oldPayload,
      resources: oldResources,
      candidate_page: { ...oldPayload.candidate_page, items: oldResources },
      reply: "旧空间迟到回复",
    })
    await waitFor(() => {
      expect(screen.getByText("新空间资料")).toBeInTheDocument()
      expect(screen.queryByText("旧空间迟到资料")).not.toBeInTheDocument()
      expect(screen.queryByText("旧空间迟到回复")).not.toBeInTheDocument()
    })
  })

  it("keeps discovery, paging, and durable actions provider-free after the first turn", async () => {
    const calls = installPoolMock()
    render(<CasualTeacherChat workspaceId="pool-casual" mode="planner" />)
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), { target: { value: "验证候选池分页" } })
    fireEvent.click(screen.getByTestId("planning-companion-send"))

    await screen.findByText("文字笔记 3")
    expect(screen.getByText("文字笔记 4")).toBeInTheDocument()
    expect(screen.getByText("文字笔记 1")).toBeInTheDocument()
    expect(screen.getByTestId("planning-companion-candidate-pool-page-status")).toHaveTextContent("第 1 / 2 页")
    expect(screen.getByTestId("planning-companion-candidate-pool-mode")).toHaveTextContent("服务端分页")

    fireEvent.click(screen.getByTestId("planning-companion-candidate-pool-next"))
    await screen.findByText("文字笔记 2")
    const nextCall = calls.find(item => item.body.pool_action === "page" && item.body.cursor === "cursor-page-2")
    expect(nextCall?.body).toEqual(expect.objectContaining({
      pool_action: "page",
      planning_session_id: "pool-integration-session",
      next_cursor: "cursor-page-2",
      message: "",
    }))
    expect(nextCall?.body).not.toHaveProperty("provider_session_id")
    expect(nextCall?.body).not.toHaveProperty("resource_search_authorized")
    expect(screen.getByText("文字笔记 5")).toBeInTheDocument()
    expect(screen.getByText("文字笔记 6")).toBeInTheDocument()
    expect(screen.getByTestId("planning-companion-candidate-pool-page-status")).toHaveTextContent("第 2 / 2 页")

    fireEvent.click(screen.getByTestId("planning-companion-candidate-pool-previous"))
    await screen.findByText("文字笔记 3")
    const previousCall = calls.find(item => item.body.pool_action === "page" && item.body.cursor === "cursor-page-1")
    expect(previousCall?.body).toEqual(expect.objectContaining({ previous_cursor: "cursor-page-1" }))

    fireEvent.click(screen.getByRole("checkbox", { name: "选择 文字笔记 3" }))
    await waitFor(() => expect(calls.filter(item => ["select", "set_anchor"].includes(item.body.pool_action)).length).toBe(1))
    fireEvent.click(screen.getByTestId("planning-companion-candidate-pool-next"))
    await screen.findByText("文字笔记 2")
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 文字笔记 2" }))
    await waitFor(() => expect(calls.some(item => item.body.pool_action === "select" && item.body.candidate_ids?.[0] === "note-2")).toBe(true))
    expect(screen.getByTestId("planning-companion-candidate-pool-pinned")).toHaveTextContent("文字笔记 3")
    expect(screen.getByTestId("planning-companion-candidate-pool-pinned")).toHaveTextContent("文字笔记 2")

    fireEvent.click(screen.getByTestId("planning-companion-candidate-pool-previous"))
    await screen.findByText("文字笔记 3")
    fireEvent.click(screen.getByRole("button", { name: "暂不使用 文字笔记 3" }))
    await waitFor(() => expect(calls.some(item => item.body.pool_action === "reject" && item.body.candidate_ids?.[0] === "note-3")).toBe(true))
    fireEvent.click(screen.getByRole("button", { name: /撤销/ }))
    await waitFor(() => expect(calls.some(item => item.body.pool_action === "undo" && item.body.candidate_ids?.[0] === "note-3")).toBe(true))

    const durable = calls.filter(item => ["select", "deselect", "set_anchor", "reject", "undo"].includes(item.body.pool_action))
    expect(durable.length).toBeGreaterThanOrEqual(5)
    durable.forEach(item => {
      expect(item.body.client_request_id).toEqual(expect.any(String))
      expect(item.body.expected_revision).toEqual(expect.any(Number))
    })
    const decisions = durable.map(item => item.body.pool_action)
    expect(decisions).toEqual(expect.arrayContaining(["select", "deselect", "set_anchor", "reject", "undo"]))
    expect(decisions.indexOf("deselect")).toBeLessThan(decisions.indexOf("reject"))
  })

  it("remounts a persisted session with refresh only and renders the active teaching pool", async () => {
    const calls = installPoolMock()
    localStorage.setItem(planningPoolStorageKey("pool-refresh"), JSON.stringify({ planning_session_id: "pool-integration-session", revision: 1 }))
    const { unmount } = render(<CasualTeacherChat workspaceId="pool-refresh" mode="planner" />)
    await waitFor(() => expect(calls.some(item => item.body.pool_action === "refresh")).toBe(true))
    const refresh = calls.find(item => item.body.pool_action === "refresh")
    expect(refresh?.body).not.toHaveProperty("provider_session_id")
    expect(calls.filter(item => item.body.pool_action === "refresh")).toHaveLength(1)
    unmount()

    useMentorStore.getState().setProviderSession({ providerSessionId: "provider-fixture", provider: "openai-compatible", verified: true })
    const activeCalls = installPoolMock()
    render(<ActiveTeachingChainView workspaceId="active-pool" createRequest={0} />)
    await screen.findByTestId("planning-assistant")
    fireEvent.click(screen.getByTestId("active-find-material"))
    fireEvent.change(screen.getByTestId("planning-assistant-input"), { target: { value: "验证 active 候选池" } })
    fireEvent.click(screen.getByTestId("planning-assistant-send"))
    await screen.findByText("文字笔记 3")
    fireEvent.click(screen.getByTestId("planning-assistant-candidate-pool-next"))
    await screen.findByText("文字笔记 2")
    const activePage = activeCalls.find(item => item.body.pool_action === "page")
    expect(activePage?.body).toEqual(expect.objectContaining({ pool_action: "page", planning_session_id: "pool-integration-session", message: "" }))
    expect(activePage?.body).not.toHaveProperty("provider_session_id")
  })

  it("waits for delayed selection and anchor writes before enabling the next page", async () => {
    const delayedAnchor = deferred<void>()
    useMentorStore.getState().setProviderSession({ providerSessionId: "provider-fixture", provider: "openai-compatible", verified: true })
    const calls = installPoolMock({ delayFirstAnchor: delayedAnchor })
    render(<ActiveTeachingChainView workspaceId="active-pool-race" createRequest={0} />)
    await screen.findByTestId("planning-assistant")
    fireEvent.click(screen.getByTestId("active-find-material"))
    fireEvent.change(screen.getByTestId("planning-assistant-input"), { target: { value: "验证选择后翻页串行" } })
    fireEvent.click(screen.getByTestId("planning-assistant-send"))
    await screen.findByText("文字笔记 3")

    fireEvent.click(screen.getByRole("checkbox", { name: "选择 文字笔记 3" }))
    await screen.findByTestId("planning-assistant-candidate-pool-busy")
    expect(screen.getByRole("checkbox", { name: "选择 文字笔记 3" })).toBeChecked()
    expect(screen.getByTestId("planning-assistant-candidate-pool-next")).toBeDisabled()
    expect(calls.filter(item => item.body.pool_action === "page")).toHaveLength(0)

    delayedAnchor.resolve()
    await waitFor(() => expect(calls.filter(item => ["select", "set_anchor"].includes(item.body.pool_action))).toHaveLength(1))
    await waitFor(() => expect(screen.getByTestId(/planning-assistant-candidate-pool-pinned-/)).toHaveTextContent("文字笔记 3"))
    expect(screen.getByRole("checkbox", { name: "选择 文字笔记 3" })).toBeChecked()
    await waitFor(() => expect(screen.getByTestId("planning-assistant-candidate-pool-next")).not.toBeDisabled())
    fireEvent.click(screen.getByTestId("planning-assistant-candidate-pool-next"))
    await screen.findByText("文字笔记 2")
    expect(screen.getByTestId(/planning-assistant-candidate-pool-pinned-/)).toHaveTextContent("文字笔记 3")

    const pageCall = calls.find(item => item.body.pool_action === "page")
    const anchorCall = calls.find(item => item.body.pool_action === "set_anchor")
    expect(pageCall?.body.expected_revision).toBe(anchorCall?.body.expected_revision + 1)
    expect(calls.indexOf(pageCall!)).toBeGreaterThan(calls.indexOf(anchorCall!))
  })

  it("recovers a stale page cursor with a provider-free refresh and one retry", async () => {
    const calls = installPoolMock({ staleFirstPage: true })
    render(<CasualTeacherChat workspaceId="pool-stale-recovery" mode="planner" />)
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), { target: { value: "验证旧游标恢复" } })
    fireEvent.click(screen.getByTestId("planning-companion-send"))
    await screen.findByText("文字笔记 3")

    fireEvent.click(screen.getByTestId("planning-companion-candidate-pool-next"))
    await screen.findByText("文字笔记 2")

    const poolCalls = calls.filter(item => ["page", "refresh"].includes(item.body.pool_action))
    expect(poolCalls.map(item => item.body.pool_action)).toEqual(["page", "refresh", "page"])
    poolCalls.forEach(item => {
      expect(item.body).not.toHaveProperty("provider_session_id")
      expect(item.body).not.toHaveProperty("resource_search_authorized")
    })
    expect(screen.queryByText(/stale/i)).not.toBeInTheDocument()
    expect(screen.getByTestId("planning-companion-candidate-pool-page-status")).toHaveTextContent("第 2 / 2 页")
  })

  it("sends an explicit fill-page request for a two-item shortfall without a provider cache ref", async () => {
    useMentorStore.getState().setProviderSession({ providerSessionId: "provider-fixture", provider: "openai-compatible", verified: true })
    const calls = installExplicitPoolMock({ initialCount: 2, initialShortfall: 2 })
    render(<CasualTeacherChat workspaceId="explicit-fill" mode="planner" />)
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), { target: { value: "整理公开资料" } })
    fireEvent.click(screen.getByTestId("planning-companion-send"))
    await screen.findByText("资料 1")

    fireEvent.click(screen.getByTestId("planning-companion-candidate-pool-replenish-shortfall"))
    await waitFor(() => expect(calls.some(item => item.body.candidate_discovery_mode === "fill_page")).toBe(true))
    const fill = calls.find(item => item.body.candidate_discovery_mode === "fill_page")
    expect(fill?.body).toEqual(expect.objectContaining({
      candidate_discovery_mode: "fill_page",
      candidate_target_count: 3,
      selected_material_count: 0,
      candidate_search_round: 1,
      pool_mode: "append",
    }))
    expect(fill?.body).toEqual(expect.objectContaining({ existing_candidate_summaries: expect.any(Array) }))
    expect(fill?.body).not.toHaveProperty("provider_cache_ref")
  })

  it("uses qualified and held counts when filling a published page", async () => {
    useMentorStore.getState().setProviderSession({ providerSessionId: "provider-fixture", provider: "openai-compatible", verified: true })
    const calls = installExplicitPoolMock({ initialCount: 3, initialQualifiedCount: 5, initialHeldIncompleteCount: 2 })
    render(<CasualTeacherChat workspaceId="explicit-fill-qualified" mode="planner" />)
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), { target: { value: "整理公开资料" } })
    fireEvent.click(screen.getByTestId("planning-companion-send"))
    await screen.findByText("资料 1")

    fireEvent.click(screen.getByTestId("planning-companion-candidate-pool-replenish-shortfall"))
    await waitFor(() => expect(calls.some(item => item.body.candidate_discovery_mode === "fill_page")).toBe(true))
    const fill = calls.find(item => item.body.candidate_discovery_mode === "fill_page")
    expect(fill?.body).toEqual(expect.objectContaining({
      candidate_discovery_mode: "fill_page",
      candidate_target_count: 6,
      selected_material_count: 0,
      pool_mode: "append",
    }))
  })

  it("sends a complete fill refresh without a provider from the casual planner", async () => {
    useMentorStore.getState().setProviderSession({ providerSessionId: "provider-fixture", provider: "openai-compatible", verified: true })
    const calls = installExplicitPoolMock({ initialCount: 3, initialShortfall: 1 })
    render(<CasualTeacherChat workspaceId="explicit-fill-noop-casual" mode="planner" />)
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), { target: { value: "刷新完整候选页" } })
    fireEvent.click(screen.getByTestId("planning-companion-send"))
    await screen.findByText("资料 1")

    useMentorStore.getState().clearProvider()
    fireEvent.click(screen.getByTestId("planning-companion-candidate-pool-replenish-shortfall"))
    await waitFor(() => expect(calls.some(item => item.body.candidate_discovery_mode === "fill_page")).toBe(true))
    const fill = calls.find(item => item.body.candidate_discovery_mode === "fill_page")
    expect(fill?.body).toEqual(expect.objectContaining({
      candidate_discovery_mode: "fill_page",
      candidate_target_count: 3,
      pool_mode: "append",
    }))
    expect(fill?.body).not.toHaveProperty("provider_session_id")
    await waitFor(() => expect(screen.queryByTestId("planning-companion-candidate-pool-busy")).not.toBeInTheDocument())
    expect(screen.queryByText("模型连接仍在验证，请稍候重试。")).not.toBeInTheDocument()
  })

  it("shows the exact choose-enough notice and makes no request when two materials try to add a page", async () => {
    useMentorStore.getState().setProviderSession({ providerSessionId: "provider-fixture", provider: "openai-compatible", verified: true })
    const calls = installExplicitPoolMock({ initialCount: 3, initialShortfall: 0 })
    render(<CasualTeacherChat workspaceId="explicit-blocked" mode="planner" />)
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), { target: { value: "整理三份公开资料" } })
    fireEvent.click(screen.getByTestId("planning-companion-send"))
    await screen.findByText("资料 1")

    fireEvent.click(screen.getByRole("checkbox", { name: "选择 资料 1" }))
    await waitFor(() => expect(calls.filter(item => ["select", "set_anchor"].includes(item.body.pool_action)).length).toBe(1))
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "选择 资料 1" })).toBeChecked())
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 资料 2" }))
    await waitFor(() => expect(calls.filter(item => item.body.pool_action === "select").length).toBe(1))
    await waitFor(() => expect(screen.queryByTestId("planning-companion-candidate-pool-busy")).not.toBeInTheDocument())

    const before = calls.filter(item => item.body.candidate_discovery_mode).length
    fireEvent.click(screen.getByTestId("planning-companion-candidate-pool-add-page"))
    expect(screen.getByTestId("planning-companion-candidate-pool-action-notice")).toHaveTextContent("请先至少选择 3 份材料，再尝试扩充候选")
    expect(calls.filter(item => item.body.candidate_discovery_mode)).toHaveLength(before)
  })

  it("uses append targets 6 and 9 for three and four selected materials with increasing search rounds", async () => {
    useMentorStore.getState().setProviderSession({ providerSessionId: "provider-fixture", provider: "openai-compatible", verified: true })
    const calls = installExplicitPoolMock({ initialCount: 3, initialShortfall: 0 })
    render(<CasualTeacherChat workspaceId="explicit-add" mode="planner" />)
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), { target: { value: "整理候选资料" } })
    fireEvent.click(screen.getByTestId("planning-companion-send"))
    await screen.findByText("资料 1")

    for (const title of ["资料 1", "资料 2", "资料 3"]) {
      fireEvent.click(screen.getByRole("checkbox", { name: `选择 ${title}` }))
      await waitFor(() => expect(screen.getByRole("checkbox", { name: `选择 ${title}` })).toBeChecked())
      await waitFor(() => expect(screen.queryByTestId("planning-companion-candidate-pool-busy")).not.toBeInTheDocument())
    }
    fireEvent.click(screen.getByTestId("planning-companion-candidate-pool-add-page"))
    await waitFor(() => expect(calls.some(item => item.body.candidate_discovery_mode === "add_page")).toBe(true))
    const addThree = calls.find(item => item.body.candidate_discovery_mode === "add_page")
    expect(addThree?.body).toEqual(expect.objectContaining({
      candidate_discovery_mode: "add_page",
      candidate_target_count: 6,
      selected_material_count: 3,
      candidate_search_round: 1,
      pool_mode: "append",
      existing_candidate_summaries: expect.any(Array),
    }))
    expect(addThree?.body).not.toHaveProperty("provider_cache_ref")
    await waitFor(() => expect(screen.queryByTestId("planning-companion-candidate-pool-busy")).not.toBeInTheDocument())

    fireEvent.click(screen.getByTestId("planning-companion-candidate-pool-next"))
    await screen.findByText("资料 4")
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 资料 4" }))
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "选择 资料 4" })).toBeChecked())
    await waitFor(() => expect(screen.queryByTestId("planning-companion-candidate-pool-busy")).not.toBeInTheDocument())
    fireEvent.click(screen.getByTestId("planning-companion-candidate-pool-add-page"))
    await waitFor(() => expect(calls.filter(item => item.body.candidate_discovery_mode === "add_page")).toHaveLength(2))
    const addFour = calls.filter(item => item.body.candidate_discovery_mode === "add_page")[1]
    expect(addFour.body).toEqual(expect.objectContaining({
      candidate_discovery_mode: "add_page",
      candidate_target_count: 9,
      selected_material_count: 4,
      candidate_search_round: 2,
      pool_mode: "append",
    }))
    expect(addFour.body.candidate_search_round).toBeGreaterThan(addThree?.body.candidate_search_round)
    expect(addFour.body).not.toHaveProperty("provider_cache_ref")
  })

  it("uses the same explicit add-page contract from the active-teaching surface", async () => {
    useMentorStore.getState().setProviderSession({ providerSessionId: "provider-fixture", provider: "openai-compatible", verified: true })
    const calls = installExplicitPoolMock({ initialCount: 3, initialShortfall: 0 })
    render(<ActiveTeachingChainView workspaceId="active-explicit-add" createRequest={0} />)
    await screen.findByTestId("planning-assistant")
    fireEvent.click(screen.getByTestId("active-find-material"))
    fireEvent.change(screen.getByTestId("planning-assistant-input"), { target: { value: "整理主动候选资料" } })
    fireEvent.click(screen.getByTestId("planning-assistant-send"))
    await screen.findByText("资料 1")

    for (const title of ["资料 1", "资料 2", "资料 3"]) {
      fireEvent.click(screen.getByRole("checkbox", { name: `选择 ${title}` }))
      await waitFor(() => expect(screen.getByRole("checkbox", { name: `选择 ${title}` })).toBeChecked())
      await waitFor(() => expect(screen.queryByTestId("planning-assistant-candidate-pool-busy")).not.toBeInTheDocument())
    }
    fireEvent.click(screen.getByTestId("planning-assistant-candidate-pool-add-page"))
    await waitFor(() => expect(calls.some(item => item.body.candidate_discovery_mode === "add_page")).toBe(true))
    const add = calls.find(item => item.body.candidate_discovery_mode === "add_page")
    expect(add?.body).toEqual(expect.objectContaining({
      candidate_discovery_mode: "add_page",
      candidate_target_count: 6,
      selected_material_count: 3,
      candidate_search_round: 1,
      existing_candidate_summaries: expect.any(Array),
      pool_mode: "append",
    }))
    expect(add?.body).not.toHaveProperty("provider_cache_ref")
  })

  it("uses qualified and held counts when filling from the active-teaching surface", async () => {
    useMentorStore.getState().setProviderSession({ providerSessionId: "provider-fixture", provider: "openai-compatible", verified: true })
    const calls = installExplicitPoolMock({ initialCount: 3, initialQualifiedCount: 5, initialHeldIncompleteCount: 2 })
    render(<ActiveTeachingChainView workspaceId="active-explicit-fill-qualified" createRequest={0} />)
    await screen.findByTestId("planning-assistant")
    fireEvent.click(screen.getByTestId("active-find-material"))
    fireEvent.change(screen.getByTestId("planning-assistant-input"), { target: { value: "整理主动候选资料" } })
    fireEvent.click(screen.getByTestId("planning-assistant-send"))
    await screen.findByText("资料 1")

    fireEvent.click(screen.getByTestId("planning-assistant-candidate-pool-replenish-shortfall"))
    await waitFor(() => expect(calls.some(item => item.body.candidate_discovery_mode === "fill_page")).toBe(true))
    const fill = calls.find(item => item.body.candidate_discovery_mode === "fill_page")
    expect(fill?.body).toEqual(expect.objectContaining({
      candidate_discovery_mode: "fill_page",
      candidate_target_count: 6,
      selected_material_count: 0,
      pool_mode: "append",
    }))
  })

  it("sends a complete fill refresh without a provider from the active planner", async () => {
    useMentorStore.getState().setProviderSession({ providerSessionId: "provider-fixture", provider: "openai-compatible", verified: true })
    const calls = installExplicitPoolMock({ initialCount: 3, initialShortfall: 1 })
    render(<ActiveTeachingChainView workspaceId="explicit-fill-noop-active" createRequest={0} />)
    await screen.findByTestId("planning-assistant")
    fireEvent.click(screen.getByTestId("active-find-material"))
    fireEvent.change(screen.getByTestId("planning-assistant-input"), { target: { value: "刷新主动完整候选页" } })
    fireEvent.click(screen.getByTestId("planning-assistant-send"))
    await screen.findByText("资料 1")

    useMentorStore.getState().clearProvider()
    fireEvent.click(screen.getByTestId("planning-assistant-candidate-pool-replenish-shortfall"))
    await waitFor(() => expect(calls.some(item => item.body.candidate_discovery_mode === "fill_page")).toBe(true))
    const fill = calls.find(item => item.body.candidate_discovery_mode === "fill_page")
    expect(fill?.body).toEqual(expect.objectContaining({
      candidate_discovery_mode: "fill_page",
      candidate_target_count: 3,
      pool_mode: "append",
    }))
    expect(fill?.body).not.toHaveProperty("provider_session_id")
    await waitFor(() => expect(screen.queryByTestId("planning-assistant-candidate-pool-busy")).not.toBeInTheDocument())
    expect(screen.queryByText("资料助手需要已验证的真实模型连接，请先在“模型连接”中保存并验证 Provider。")).not.toBeInTheDocument()
  })
})
