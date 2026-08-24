import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

const activeRequestMock = vi.hoisted(() => ({
  activeRequest: vi.fn(),
}))

vi.mock("../features/active-teaching/api", async importOriginal => {
  const actual = await importOriginal<typeof import("../features/active-teaching/api")>()
  return {
    ...actual,
    activeRequest: activeRequestMock.activeRequest,
  }
})

import { CasualTeacherChat } from "../features/casual-chat/CasualTeacherChat"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("learning planner", () => {
  it("renders up to three candidates and hands the selected resource bundle to the plan flow", async () => {
    activeRequestMock.activeRequest.mockResolvedValue({
      reply: "我整理了三份可以核对的资料。",
      goal_summary: "在截止日期前完成一项可验证的学习成果",
      resources: [
        {
          url: "https://example.test/resource-one",
          title: "资料一",
          reason: "帮助建立基础概念",
          authority_explanation: "请核对发布机构和内容范围。",
        },
        {
          url: "https://example.test/resource-two",
          title: "资料二",
          reason: "提供可执行的练习",
          authority_explanation: "请核对作者和更新时间。",
        },
        {
          url: "https://example.test/resource-three",
          title: "资料三",
          reason: "补充进阶视角",
          authority_explanation: "请核对发布机构和内容范围。",
        },
      ],
    })
    const onStartPlan = vi.fn()

    render(
      <CasualTeacherChat
        workspaceId="workspace-planner-test"
        mode="planner"
        onStartPlan={onStartPlan}
      />,
    )

    expect(screen.getByTestId("learning-planner")).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "创建计划" })).toBeInTheDocument()
    expect(screen.getByText("需求 → 资料 → 计划")).toBeInTheDocument()
    fireEvent.change(screen.getByTestId("planning-companion-input"), {
      target: { value: "我想在截止日期前完成一项学习成果" },
    })
    fireEvent.click(screen.getByTestId("planning-companion-send"))

    await waitFor(() => expect(activeRequestMock.activeRequest).toHaveBeenCalledTimes(1))
    const requestOptions = activeRequestMock.activeRequest.mock.calls[0][1]
    expect(JSON.parse(String(requestOptions.body))).toEqual(expect.objectContaining({
      resource_search_authorized: true,
    }))
    expect(screen.getAllByRole("checkbox")).toHaveLength(3)
    expect(screen.getByText("资料一")).toBeInTheDocument()
    expect(screen.getByText("资料二")).toBeInTheDocument()
    expect(screen.getByText("资料三")).toBeInTheDocument()

    const candidates = screen.getAllByRole("checkbox")
    fireEvent.click(candidates[0])
    fireEvent.click(candidates[1])
    expect(candidates[0]).toBeChecked()
    expect(candidates[1]).toBeChecked()
    fireEvent.click(screen.getByTestId("planning-companion-start"))

    await waitFor(() => expect(onStartPlan).toHaveBeenCalledTimes(1))
    expect(onStartPlan).toHaveBeenCalledWith(expect.objectContaining({
      goal: "在截止日期前完成一项可验证的学习成果",
      resource: expect.objectContaining({
        url: "https://example.test/resource-one",
        title: "资料一",
      }),
      resources: [
        expect.objectContaining({ url: "https://example.test/resource-one" }),
        expect.objectContaining({ url: "https://example.test/resource-two" }),
      ],
      created_at: expect.any(String),
    }))
  })

  it("retries public search with the previous provider cache reference", async () => {
    activeRequestMock.activeRequest
      .mockResolvedValueOnce({
        reply: "暂时没有找到候选。",
        goal_summary: "学习一个可验证主题",
        resources: [],
        cache: { provider_ref: "a".repeat(64) },
      })
      .mockResolvedValueOnce({
        reply: "仍未找到候选。",
        goal_summary: "学习一个可验证主题",
        resources: [],
        cache: { provider_ref: "a".repeat(64), provider_hit: true },
      })

    render(<CasualTeacherChat workspaceId="workspace-retry-cache" mode="planner" />)
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), {
      target: { value: "帮我查找一份公开学习资料" },
    })
    fireEvent.click(screen.getByTestId("planning-companion-send"))

    await waitFor(() => expect(screen.getByRole("button", { name: "重试搜索" })).toBeInTheDocument())
    fireEvent.click(screen.getByRole("button", { name: "重试搜索" }))
    await waitFor(() => expect(activeRequestMock.activeRequest).toHaveBeenCalledTimes(2))

    const retryBody = JSON.parse(String(activeRequestMock.activeRequest.mock.calls[1][1].body))
    expect(retryBody).toEqual(expect.objectContaining({
      resource_search_authorized: true,
      provider_cache_ref: "a".repeat(64),
    }))
  })

  it("does not publish or hand off a video-only bundle", async () => {
    activeRequestMock.activeRequest.mockResolvedValue({
      reply: "找到一份完整视频合集。",
      goal_summary: "学习一个可验证主题",
      resources: [{
        candidate_id: "video-collection-1",
        url: "https://video.example.test/collection",
        title: "完整视频合集",
        media_kind: "video_collection",
        coverage_role: "primary",
        direct_user_reference: true,
      }],
    })
    const onStartPlan = vi.fn()
    render(<CasualTeacherChat workspaceId="workspace-video-only" mode="planner" onStartPlan={onStartPlan} />)
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), { target: { value: "找完整视频合集" } })
    fireEvent.click(screen.getByTestId("planning-companion-send"))
    await waitFor(() => expect(activeRequestMock.activeRequest).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole("checkbox", { name: "选择 完整视频合集" })).not.toBeInTheDocument()
    expect(screen.queryByText("完整视频合集")).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId("planning-companion-start"))
    expect(onStartPlan).not.toHaveBeenCalled()
    expect(screen.queryByTestId("planning-video-only-confirmation")).not.toBeInTheDocument()
  })

  it("keeps only the selected document while searching for companion documents", async () => {
    let finishRefinement: (value: any) => void = () => undefined
    const refinement = new Promise(resolve => { finishRefinement = resolve })
    activeRequestMock.activeRequest
      .mockResolvedValueOnce({
        reply: "我找到了三份文字资料候选。",
        goal_summary: "学习线性代数",
        resources: [
          { candidate_id: "doc-1", url: "https://docs.example.test/one", title: "课程主讲义", media_kind: "document", direct_user_reference: true },
          { candidate_id: "doc-2", url: "https://docs.example.test/two", title: "练习手册", media_kind: "document", direct_user_reference: false },
          { candidate_id: "doc-3", url: "https://docs.example.test/three", title: "公式速查表", media_kind: "document", direct_user_reference: false },
        ],
      })
      .mockReturnValueOnce(refinement)

    render(<CasualTeacherChat workspaceId="workspace-companion-notes" mode="planner" />)
    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), {
      target: { value: "帮我找线性代数文字资料" },
    })
    fireEvent.click(screen.getByTestId("planning-companion-send"))
    const firstDocument = await screen.findByRole("checkbox", { name: "选择 课程主讲义" })
    fireEvent.click(firstDocument)

    fireEvent.change(screen.getByTestId("planning-companion-input"), {
      target: { value: "我已选择主讲义，再找一份配套练习资料" },
    })
    fireEvent.click(screen.getByTestId("planning-companion-send"))

    expect(await screen.findByText("正在保留已选资料并检索配套内容…")).toBeInTheDocument()
    await waitFor(() => expect(activeRequestMock.activeRequest).toHaveBeenCalledTimes(2))
    const refinementBody = JSON.parse(String(activeRequestMock.activeRequest.mock.calls[1][1].body))
    expect(refinementBody.selected_resources).toEqual([
      expect.objectContaining({ candidate_id: "doc-1", title: "课程主讲义" }),
    ])
    expect(refinementBody.retained_candidates).toEqual(refinementBody.selected_resources)

    finishRefinement({
      reply: "已保留你选中的 1 份资料，并找到 1 份新的文字笔记候选。",
      goal_summary: "学习线性代数",
      resource_bundle: {
        candidates: [
          { candidate_id: "doc-1", url: "https://docs.example.test/one", title: "课程主讲义", media_kind: "document", selected: true, direct_user_reference: true },
          { candidate_id: "notes-1", url: "https://notes.example.test/one", title: "配套练习笔记", media_kind: "document" },
        ],
      },
    })

    expect(await screen.findByText("配套练习笔记")).toBeInTheDocument()
    expect(screen.getByText("已保留选择 · 本轮不会被旧候选替换")).toBeInTheDocument()
    expect(screen.queryByText("练习手册")).not.toBeInTheDocument()
    expect(screen.queryByText("公式速查表")).not.toBeInTheDocument()
  })
})
