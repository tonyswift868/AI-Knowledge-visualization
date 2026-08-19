import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

const activeRequestMock = vi.hoisted(() => ({
  activeRequest: vi.fn(),
}))

vi.mock("../features/active-teaching/api", async importOriginal => {
  const actual = await importOriginal<typeof import("../features/active-teaching/api")>()
  return { ...actual, activeRequest: activeRequestMock.activeRequest }
})

import { CasualTeacherChat } from "../features/casual-chat/CasualTeacherChat"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("planner QA07 source and recovery paths", () => {
  it("hands a confirmed local file and natural-language goal into the plan flow", async () => {
    const onStartPlan = vi.fn()
    const file = new File(["local notes about vector analysis"], "vector-notes.md", {
      type: "text/markdown",
    })

    render(
      <CasualTeacherChat
        workspaceId="planner-qa07-upload"
        mode="planner"
        onStartPlan={onStartPlan}
      />,
    )

    fireEvent.click(screen.getByTestId("planning-companion-use-own"))
    fireEvent.change(screen.getByTestId("planning-companion-upload"), {
      target: { files: [file] },
    })
    fireEvent.change(screen.getByTestId("planning-companion-input"), {
      target: { value: "我想在一周内掌握向量分析并完成两道练习" },
    })
    fireEvent.click(screen.getByTestId("planning-companion-send"))

    expect(screen.getByText("vector-notes.md")).toBeInTheDocument()
    expect(activeRequestMock.activeRequest).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId("planning-companion-start"))

    await waitFor(() => expect(onStartPlan).toHaveBeenCalledOnce())
    expect(onStartPlan).toHaveBeenCalledWith(expect.objectContaining({
      goal: "我想在一周内掌握向量分析并完成两道练习",
      materialFile: file,
      resource: expect.objectContaining({
        url: "",
        title: "vector-notes.md",
      }),
    }))
  })

  it("offers retry and local-upload actions when AI discovery returns no candidates", async () => {
    activeRequestMock.activeRequest.mockResolvedValue({
      reply: "暂时没有可核对的公开候选。",
      goal_summary: "掌握向量分析",
      resources: [],
      warnings: ["搜索没有返回候选"],
    })

    render(
      <CasualTeacherChat
        workspaceId="planner-qa07-empty"
        mode="planner"
      />,
    )

    fireEvent.click(screen.getByTestId("planning-companion-search"))
    fireEvent.change(screen.getByTestId("planning-companion-input"), {
      target: { value: "请帮我找向量分析公开资料" },
    })
    fireEvent.click(screen.getByTestId("planning-companion-send"))

    await waitFor(() => expect(activeRequestMock.activeRequest).toHaveBeenCalledOnce())
    expect(screen.getByTestId("planning-companion-empty-actions")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "重试搜索" }))
    await waitFor(() => expect(activeRequestMock.activeRequest).toHaveBeenCalledTimes(2))

    fireEvent.click(screen.getByRole("button", { name: "上传我的资料" }))
    expect(screen.getByRole("status")).toHaveTextContent("请选择一个文件")
  })
})
