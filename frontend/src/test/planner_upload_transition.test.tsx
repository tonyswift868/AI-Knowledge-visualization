import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

const activeRequestMock = vi.hoisted(() => ({ activeRequest: vi.fn() }))

vi.mock("../features/active-teaching/api", async importOriginal => {
  const actual = await importOriginal<typeof import("../features/active-teaching/api")>()
  return { ...actual, activeRequest: activeRequestMock.activeRequest }
})

import { CasualTeacherChat } from "../features/casual-chat/CasualTeacherChat"
import PlanningCandidatePool from "../features/planning/PlanningCandidatePool"
import { useMentorStore } from "../stores/useMentorStore"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("planner search/upload additive contract", () => {
  it("merges selected search candidates and an uploaded file behind one confirmation", async () => {
    activeRequestMock.activeRequest.mockResolvedValue({
      reply: "找到一份匹配笔记。",
      goal_summary: "完成学习目标",
      planning_session_id: "upload-transition-session",
      revision: 3,
      resources: [{
        candidate_id: "note-1",
        url: "https://notes.example/one",
        title: "匹配笔记",
        media_kind: "document",
        direct_user_reference: false,
      }],
      selection: { selected: [], rejected: [], anchor: null },
    })
    const onStartPlan = vi.fn()
    const file = new File(["local"], "my-notes.md", { type: "text/markdown" })

    render(<CasualTeacherChat workspaceId="upload-transition" mode="planner" onStartPlan={onStartPlan} />)
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), { target: { value: "找匹配笔记" } })
    fireEvent.click(screen.getByTestId("planning-companion-send"))
    const candidate = await screen.findByRole("checkbox", { name: "选择 匹配笔记" })
    fireEvent.click(candidate)
    fireEvent.click(screen.getByTestId("planning-companion-use-own"))
    fireEvent.change(screen.getByTestId("planning-companion-upload"), { target: { files: [file] } })

    expect(candidate).toBeChecked()
    expect(screen.getAllByText("匹配笔记").length).toBeGreaterThan(0)
    const mergedBundle = screen.getByLabelText("推荐资料")
    expect(within(mergedBundle).getByText("my-notes.md")).toBeInTheDocument()
    expect(within(mergedBundle).getByText("辅助资料 · 将与已选资料合并整理")).toBeInTheDocument()
    expect(screen.getAllByTestId("planning-companion-start")).toHaveLength(1)
    expect(screen.getByTestId("planning-companion-start")).toHaveTextContent("采用已选资料与我的文件，合并整理")
    fireEvent.click(screen.getByRole("button", { name: "移除上传文件 my-notes.md" }))
    expect(screen.queryByTestId("planning-companion-merged-upload")).not.toBeInTheDocument()
    expect(candidate).toBeChecked()
    expect(screen.getByTestId("planning-companion-start")).toHaveTextContent("采用已选资料包，开始整理")
    expect(screen.getByText(/已移除“my-notes\.md”，已选候选资料和纲领状态保持不变/)).toBeInTheDocument()
    fireEvent.change(screen.getByTestId("planning-companion-upload"), { target: { files: [file] } })
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    expect(screen.getByText("my-notes.md")).toBeInTheDocument()

    fireEvent.click(screen.getByTestId("planning-companion-start"))
    await waitFor(() => expect(onStartPlan).toHaveBeenCalledOnce())
    const handoff = onStartPlan.mock.calls[0][0]
    expect(handoff.resources).toHaveLength(1)
    expect(handoff.supporting_resources).toHaveLength(1)
    expect(handoff.supporting_resources.map((item: any) => item.source_kind)).toEqual(["uploaded_file"])
    expect(onStartPlan).toHaveBeenCalledWith(expect.objectContaining({
      materialFile: file,
      resources: [expect.objectContaining({ candidate_id: "note-1" })],
      anchor_resource: expect.objectContaining({ candidate_id: "note-1" }),
      supporting_resources: [expect.objectContaining({ title: "my-notes.md", source_kind: "uploaded_file" })],
    }))
  })

  it("rejects an explicitly discovered video from a stale handoff", async () => {
    activeRequestMock.activeRequest.mockResolvedValue({
      reply: "找到候选。",
      goal_summary: "完成学习目标",
      resources: [{
        candidate_id: "stale-video",
        url: "https://video.example/stale",
        title: "过期视频候选",
        media_kind: "video",
        direct_user_reference: false,
      }],
    })

    const onStartPlan = vi.fn()
    render(<CasualTeacherChat workspaceId="stale-video" mode="planner" onStartPlan={onStartPlan} />)
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), { target: { value: "查找资料" } })
    fireEvent.click(screen.getByTestId("planning-companion-send"))
    await waitFor(() => {
      expect(screen.queryByRole("checkbox", { name: "选择 过期视频候选" })).not.toBeInTheDocument()
      expect(screen.getByTestId("planning-companion-candidate-pool-safety-note")).toHaveTextContent("视频")
    })
    expect(onStartPlan).not.toHaveBeenCalled()
  })

  it("blocks direct user videos in the document-only candidate pool", () => {
    render(
      <PlanningCandidatePool
        candidates={[
          { candidate_id: "stale", url: "https://video.example/stale", title: "旧视频", media_kind: "video" },
          { candidate_id: "direct", url: "https://video.example/direct", title: "我的视频", media_kind: "video", direct_user_reference: true },
          { candidate_id: "audio", url: "https://files.example/lecture.mp3", title: "音频讲座", media_kind: "audio", direct_user_reference: true },
          { candidate_id: "subtitle", url: "https://files.example/lecture.vtt", title: "字幕文件", media_kind: "subtitle", direct_user_reference: true },
        ]}
        selectedKeys={[]}
        onSelectionChange={() => undefined}
        testId="media-contract-pool"
      />,
    )
    expect(screen.queryByRole("checkbox", { name: "选择 旧视频" })).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "选择 我的视频" })).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "选择 音频讲座" })).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "选择 字幕文件" })).not.toBeInTheDocument()
    expect(screen.getByTestId("media-contract-pool-safety-note")).toHaveTextContent("视频")
  })

  it("counts one uploaded file once when an add-page request expands a two-item selection", async () => {
    useMentorStore.getState().setProviderSession({ providerSessionId: "provider-fixture", provider: "openai-compatible", verified: true })
    const calls: Array<{ path: string; body: Record<string, any> }> = []
    const resources = [
      { candidate_id: "note-1", url: "https://notes.example/one", title: "匹配笔记一", media_kind: "document" },
      { candidate_id: "note-2", url: "https://notes.example/two", title: "匹配笔记二", media_kind: "document" },
    ]
    activeRequestMock.activeRequest.mockImplementation(async (path: string, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : {}
      calls.push({ path, body })
      return {
        planning_session_id: "upload-count-session",
        revision: calls.length,
        cache: { provider_ref: "prior-cache" },
        resources,
        candidate_page: {
          items: resources,
          page_index: 0,
          page_size: 3,
          total_count: 2,
          page_complete: true,
          server_paged: false,
          has_more: false,
        },
        selection: { selected: [], rejected: [], anchor: null },
        reply: "已找到两份匹配笔记。",
      }
    })
    const file = new File(["local"], "one-note.md", { type: "text/markdown" })
    render(<CasualTeacherChat workspaceId="upload-count" mode="planner" />)
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), { target: { value: "整理匹配笔记" } })
    fireEvent.click(screen.getByTestId("planning-companion-send"))
    await screen.findByText("匹配笔记一")

    for (const title of ["匹配笔记一", "匹配笔记二"]) {
      fireEvent.click(screen.getByRole("checkbox", { name: `选择 ${title}` }))
      await waitFor(() => expect(screen.getByRole("checkbox", { name: `选择 ${title}` })).toBeChecked())
      await waitFor(() => expect(screen.queryByTestId("planning-companion-candidate-pool-busy")).not.toBeInTheDocument())
    }
    fireEvent.click(screen.getByTestId("planning-companion-use-own"))
    fireEvent.change(screen.getByTestId("planning-companion-upload"), { target: { files: [file] } })
    expect(screen.getByTestId("planning-companion-merged-upload")).toHaveTextContent("one-note.md")

    fireEvent.click(screen.getByTestId("planning-companion-candidate-pool-add-page"))
    await waitFor(() => expect(calls.some(item => item.body.candidate_discovery_mode === "add_page")).toBe(true))
    const addPage = calls.find(item => item.body.candidate_discovery_mode === "add_page")
    expect(addPage?.body).toEqual(expect.objectContaining({
      candidate_discovery_mode: "add_page",
      candidate_target_count: 6,
      selected_material_count: 3,
      pool_mode: "append",
    }))
    expect(addPage?.body).not.toHaveProperty("provider_cache_ref")
  })
})
