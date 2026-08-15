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
  it("renders up to three candidates and hands the selected resource to the plan flow", async () => {
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
    expect(screen.getAllByRole("radio")).toHaveLength(3)
    expect(screen.getByText("资料一")).toBeInTheDocument()
    expect(screen.getByText("资料二")).toBeInTheDocument()
    expect(screen.getByText("资料三")).toBeInTheDocument()

    const candidates = screen.getAllByRole("radio")
    fireEvent.click(candidates[1])
    expect(candidates[1]).toBeChecked()
    fireEvent.click(screen.getByTestId("planning-companion-start"))

    await waitFor(() => expect(onStartPlan).toHaveBeenCalledTimes(1))
    expect(onStartPlan).toHaveBeenCalledWith(expect.objectContaining({
      goal: "在截止日期前完成一项可验证的学习成果",
      resource: expect.objectContaining({
        url: "https://example.test/resource-two",
        title: "资料二",
      }),
      created_at: expect.any(String),
    }))
  })
})
