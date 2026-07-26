import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { TeacherCompanionPortrait, TeacherCompanionStage } from "../features/teachers/TeacherCompanionStage"

afterEach(() => cleanup())

describe("2D teacher companion stage", () => {
  it("uses the original 2D illustration path and exposes the Spine runtime boundary", () => {
    render(<TeacherCompanionStage teacherName="艾莉丝" />)
    const stage = screen.getByTestId("teacher-companion-stage")
    expect(stage).toHaveAttribute("data-visual-mode", "2d-illustration")
    expect(stage).toHaveAttribute("data-runtime-target", "spine-player")
    expect(screen.getByAltText("艾莉丝，原创成人 AI 教师立绘")).toHaveAttribute("src", "/assets/teachers/mentor-iris.png")
  })

  it("switches presentation state without invoking the legacy 3D renderer", () => {
    render(<TeacherCompanionStage />)
    fireEvent.click(screen.getByRole("button", { name: /鼓励/ }))
    expect(screen.getByTestId("teacher-companion-stage")).toHaveClass("mood-encouraging")
    expect(screen.getByText(/不用急着一次答对/)).toBeInTheDocument()
    expect(screen.getByAltText("艾莉丝，原创成人 AI 教师立绘")).toHaveAttribute("src", "/assets/teachers/mentor-iris-encouraging.png")
  })

  it("accepts a question and enters explanation mode", () => {
    render(<TeacherCompanionStage teacherName="艾莉丝" />)
    fireEvent.change(screen.getByLabelText("向 AI 教师提问"), { target: { value: "为什么这里要用反证法？" } })
    fireEvent.click(screen.getByRole("button", { name: "发送给 AI 教师" }))
    expect(screen.getByTestId("teacher-companion-stage")).toHaveClass("mood-explaining")
    expect(screen.getByText(/为什么这里要用反证法/)).toBeInTheDocument()
    expect(screen.getByAltText("艾莉丝，原创成人 AI 教师立绘")).toHaveAttribute("src", "/assets/teachers/mentor-iris-explaining.png")
  })

  it("renders a compact learning-session portrait from the real teaching state", () => {
    render(<TeacherCompanionPortrait teacherName="艾莉丝" state="encouraging" />)
    const portrait = screen.getByTestId("teacher-companion-portrait")
    expect(portrait).toHaveAttribute("data-teacher-state", "encouraging")
    expect(screen.getByAltText("艾莉丝，原创成人 AI 教师立绘")).toHaveAttribute("src", "/assets/teachers/mentor-iris-encouraging.png")
    expect(screen.getByText(/鼓励模式/)).toBeInTheDocument()
  })
})
