import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { TeacherMotherImageReview } from "../features/teachers/TeacherAvatarStudio"

afterEach(cleanup)

describe("original AI teacher mother-image review", () => {
  it("shows the first full-body candidate without claiming that it is rigged", () => {
    render(<TeacherMotherImageReview />)

    expect(screen.getByRole("region", { name: "原创 AI 教师母图审稿" })).toBeInTheDocument()
    expect(screen.getByText("母图候选 · 未绑定骨骼")).toBeInTheDocument()
    expect(screen.getByRole("img", { name: /第一名原创成年 AI 教师全身母图/ })).toHaveAttribute(
      "src",
      "/teacher2d/original-ai-teacher/teacher-master-v1-alpha2.png",
    )
  })

  it("shows the independent blonde fang teaching state with removable outerwear", () => {
    render(<TeacherMotherImageReview candidate="blonde-fang-teaching" />)

    expect(screen.getByRole("heading", { name: "金发虎牙教师 · 兔子教学态" })).toBeInTheDocument()
    expect(screen.getByText(/更白肤色 · 明显虎牙 · 高级兔子套装/)).toBeInTheDocument()
    expect(screen.getByRole("img", { name: /兔耳外套教学态全身母图/ })).toHaveAttribute(
      "src",
      "/teacher2d/original-ai-teacher-blonde-fang/teacher-blonde-fang-v9-teaching-heart-key.png",
    )
  })

  it("shows the synchronized swimsuit state with bare toes", () => {
    render(<TeacherMotherImageReview candidate="blonde-fang-swimsuit" />)

    expect(screen.getByRole("heading", { name: "金发虎牙教师 · 泳装聊天态" })).toBeInTheDocument()
    expect(screen.getByText(/同一身份 · 白蓝泳装 · 胸腿光泽/)).toBeInTheDocument()
    expect(screen.getByRole("img", { name: /白蓝泳装聊天态全身母图，赤足露趾/ })).toHaveAttribute(
      "src",
      "/teacher2d/original-ai-teacher-blonde-fang/teacher-blonde-fang-v9-swimsuit-heart-key.png",
    )
  })
})
