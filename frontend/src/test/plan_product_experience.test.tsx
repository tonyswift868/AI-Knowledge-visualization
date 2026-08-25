import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { PlanProductExperience } from "../features/plan-product/PlanProductExperience"
import type {
  DailyResponse,
  DemoResponse,
  PlanCreationInput,
  PlanProductClient,
  PreviewResponse,
  PublishedResponse,
  ScheduleTask,
  ShiftPreviewResponse,
} from "../features/plan-product/types"

const creation: PlanCreationInput = {
  request_id: "frontend-demo",
  plan_id: "frontend-plan",
  start_date: "2026-09-01",
  deadline: "2026-10-15",
  daily_minutes: 60,
  weekly_learning_days: ["monday", "tuesday", "wednesday", "thursday", "friday"],
  unavailable_dates: ["2026-09-09"],
  review_rounds: 3,
  buffer_minutes: 30,
  tasks: Array.from({ length: 36 }, (_, index) => ({
    task_id: `concept-${index}`,
    title: `大型资料知识点 ${index + 1}`,
    minutes: 20,
  })),
}

const effort = (expected: number) => ({
  low_minutes: Math.max(0, expected - 2),
  expected_minutes: expected,
  high_minutes: expected + 3,
})

const scheduleTasks: ScheduleTask[] = [
  { allocation_id: "a1", task_id: "concept-0", title: "大型资料知识点 1", kind: "task", label: "Initial Learning", minutes: 20, chunk_index: 1, chunk_count: 1 },
  { allocation_id: "r1", task_id: "REVIEW_ROUND_1", title: "Review Round 1", kind: "review", label: "Review Round 1", minutes: 10, chunk_index: 1, chunk_count: 1 },
  { allocation_id: "b1", task_id: "buffer", title: "收尾与意外缓冲", kind: "final_buffer", label: "Buffer", minutes: 30, chunk_index: 1, chunk_count: 1 },
]

const readyPreview = (tasks = scheduleTasks): PreviewResponse => ({
  delivery_id: "mentorflow-live-demo-07-plan-product-experience-v1",
  preview: {
    preview_id: "preview-1",
    plan_id: "frontend-plan",
    status: "ready_for_confirmation",
    initial_learning: effort(720),
    review_rounds: [1, 2, 3].map(index => ({ ...effort(90 - index * 10), round_id: `REVIEW_ROUND_${index}`, round_index: index, review_item_count: 36 })),
    review_total: effort(210),
    effort: { total: effort(930) },
    total_hours: 15.5,
    finish_date: "2026-10-10",
    buffer_total_minutes: 30,
    feasibility: { feasible: true, required_minutes: 960, available_minutes: 1800, explanations: [] },
  },
  schedule: {
    schedule_id: "schedule-1",
    version: 1,
    start_date: "2026-09-01",
    finish_date: "2026-10-10",
    deadline: "2026-10-15",
    immutable: true,
    days: [{ date: "2026-09-01", total_minutes: tasks.reduce((sum, item) => sum + item.minutes, 0), initial_minutes: tasks.filter(item => item.kind === "task").reduce((sum, item) => sum + item.minutes, 0), review_minutes: tasks.filter(item => item.kind === "review").reduce((sum, item) => sum + item.minutes, 0), buffer_minutes: tasks.filter(item => item.kind === "final_buffer").reduce((sum, item) => sum + item.minutes, 0), tasks }],
  },
})

const published: PublishedResponse = {
  delivery_id: "mentorflow-live-demo-07-plan-product-experience-v1",
  published: { publication_id: "published-1", plan_id: "frontend-plan", publication_version: 1, preview_id: "preview-1", immutable: true, published_on: "2026-09-01" },
  current_schedule: readyPreview().schedule!,
  published_schedule_unchanged: true,
}

const daily: DailyResponse = {
  delivery_id: "mentorflow-live-demo-07-plan-product-experience-v1",
  snapshot: { as_of_date: "2026-09-02", deadline: "2026-09-01", status: "expired_with_overdue", schedule_version: 1 },
  groups: {
    OVERDUE: [{ task_id: "old", title: "昨天未完成", allocation_kind: "task", planned_minutes: 20, scheduled_dates: ["2026-09-01"], status: "overdue" }],
    "DUE TODAY": [{ task_id: "today", title: "今天照常学习", allocation_kind: "review", planned_minutes: 15, scheduled_dates: ["2026-09-02"], status: "due_today" }],
    UPCOMING: [{ task_id: "future", title: "未来任务", allocation_kind: "task", planned_minutes: 25, scheduled_dates: ["2026-09-03"], status: "upcoming" }],
    COMPLETED: [{ task_id: "done", title: "已经完成", allocation_kind: "task", planned_minutes: 10, scheduled_dates: ["2026-08-31"], status: "completed" }],
  },
  expired_message: "计划已逾期，但任务不会丢失，你可以继续完成。",
  automatic_shift_applied: false,
}

const shift: ShiftPreviewResponse = {
  delivery_id: "mentorflow-live-demo-07-plan-product-experience-v1",
  shift_preview: { preview_id: "shift-preview-1", old_end: "2026-10-10", new_end: "2026-10-13", calendar_days_added: 3, learning_days_added: 1, tasks_moved: 39, allocations_moved: 44, non_learning_days_skipped: 2 },
  receipt: { operation: "shift", cp_sat_calls: 0, cp_sat_allowed: false, provider_calls: 0 },
}

function client(overrides: Partial<PlanProductClient> = {}): PlanProductClient {
  const demo: DemoResponse = { delivery_id: readyPreview().delivery_id, fixture_name: "large-material-initial-plus-3-reviews", creation }
  return {
    demo: vi.fn().mockResolvedValue(demo),
    createPreview: vi.fn().mockResolvedValue(readyPreview()),
    publish: vi.fn().mockResolvedValue(published),
    getPublished: vi.fn().mockResolvedValue(published),
    daily: vi.fn().mockResolvedValue(daily),
    complete: vi.fn().mockResolvedValue({}),
    previewShift: vi.fn().mockResolvedValue(shift),
    confirmShift: vi.fn().mockResolvedValue({ current_schedule: published.current_schedule }),
    regenerate: vi.fn().mockResolvedValue({ ...readyPreview(), receipt: { operation: "regenerate", cp_sat_calls: 1, cp_sat_allowed: true, provider_calls: 0 } }),
    ...overrides,
  }
}

afterEach(() => cleanup())

describe("learning plan product experience", () => {
  it("offers explicit review round selection and shows every effort component before publish", async () => {
    const api = client()
    render(<PlanProductExperience client={api} />)
    await waitFor(() => expect(screen.getByText(/36 个知识点/)).toBeInTheDocument())

    fireEvent.click(screen.getByTestId("review-round-0"))
    expect(screen.getByTestId("review-round-0")).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByTestId("review-round-12")).toBeInTheDocument()
    fireEvent.click(screen.getByTestId("review-round-3"))
    fireEvent.click(screen.getByRole("button", { name: "生成计划预览" }))

    await waitFor(() => expect(api.createPreview).toHaveBeenCalledOnce())
    expect(screen.getByText("Initial Learning")).toBeInTheDocument()
    expect(screen.getAllByText("Review Round 1").length).toBeGreaterThan(0)
    expect(screen.getByText("Review Round 2")).toBeInTheDocument()
    expect(screen.getByText("Review Round 3")).toBeInTheDocument()
    expect(screen.getByText("Review Total")).toBeInTheDocument()
    expect(screen.getByText("Grand Total")).toBeInTheDocument()
    expect(screen.getByText("初学 · Initial Learning")).toBeInTheDocument()
    expect(screen.getByText("缓冲 · Buffer")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Publish" })).toBeEnabled()
  })

  it("turns infeasibility categories into a human-readable capacity explanation", async () => {
    const infeasible: PreviewResponse = {
      ...readyPreview(),
      preview: {
        ...readyPreview().preview,
        status: "infeasible",
        feasibility: {
          feasible: false,
          required_minutes: 900,
          available_minutes: 300,
          explanations: [
            { category: "deadline_too_short", summary: "short", suggested_action: "延长截止日期。" },
            { category: "weekly_preference_too_restrictive", summary: "weekly", suggested_action: "增加学习日。" },
            { category: "selected_review_rounds_too_many", summary: "review", suggested_action: "调整复习轮次或增加容量。" },
            { category: "unsplittable_task", summary: "atomic", suggested_action: "增加单日容量。" },
            { category: "not_enough_total_capacity", summary: "capacity", suggested_action: "增加总容量。" },
          ],
        },
      },
      schedule: null,
    }
    render(<PlanProductExperience client={client({ createPreview: vi.fn().mockResolvedValue(infeasible) })} />)
    await waitFor(() => screen.getByText(/36 个知识点/))
    fireEvent.click(screen.getByRole("button", { name: "生成计划预览" }))

    expect(await screen.findByText("按每天 60 分钟和 3 轮复习，你的可用时间不够。")).toBeInTheDocument()
    expect(screen.getAllByText("截止日期").length).toBeGreaterThan(1)
    expect(screen.getByText("每周可用时间")).toBeInTheDocument()
    expect(screen.getAllByText(/复习轮次/).length).toBeGreaterThan(1)
    expect(screen.getByText("不可拆任务")).toBeInTheDocument()
    expect(screen.getByText("总容量")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled()
  })

  it("keeps publish, daily states, shift confirmation, and CP-SAT regeneration visibly separate", async () => {
    const api = client()
    render(<PlanProductExperience client={api} />)
    await waitFor(() => screen.getByText(/36 个知识点/))
    fireEvent.click(screen.getByRole("button", { name: "生成计划预览" }))
    fireEvent.click(await screen.findByRole("button", { name: "Publish" }))

    expect(await screen.findByText("计划已逾期，但任务不会丢失，你可以继续完成。")).toBeInTheDocument()
    expect(screen.getByText("OVERDUE")).toBeInTheDocument()
    expect(screen.getByText("DUE TODAY")).toBeInTheDocument()
    expect(screen.getByText("UPCOMING")).toBeInTheDocument()
    expect(screen.getByText("COMPLETED")).toBeInTheDocument()
    expect(screen.getByText("昨天未完成")).toBeInTheDocument()
    expect(screen.getByText("今天照常学习")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "预览整体往后移" }))
    expect(await screen.findByTestId("shift-preview")).toHaveTextContent("CP-SAT 调用：0")
    expect(api.confirmShift).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "确认并应用整体后移" }))
    await waitFor(() => expect(api.confirmShift).toHaveBeenCalledOnce())

    fireEvent.click(screen.getByRole("button", { name: "重新优化计划" }))
    await waitFor(() => expect(api.regenerate).toHaveBeenCalledOnce())
    expect(await screen.findByRole("status")).toHaveTextContent("CP-SAT 调用 1 次")
  })

  it("renders a 1000-allocation schedule within the large-view budget", async () => {
    const tasks: ScheduleTask[] = Array.from({ length: 1_000 }, (_, index) => ({
      allocation_id: `large-${index}`,
      task_id: `large-${index}`,
      title: `Large schedule task ${index}`,
      kind: "task",
      label: "Initial Learning",
      minutes: 1,
      chunk_index: 1,
      chunk_count: 1,
    }))
    const api = client({ createPreview: vi.fn().mockResolvedValue(readyPreview(tasks)) })
    render(<PlanProductExperience client={api} />)
    await waitFor(() => screen.getByText(/36 个知识点/))
    const started = performance.now()
    fireEvent.click(screen.getByRole("button", { name: "生成计划预览" }))
    await waitFor(() => expect(screen.getAllByTestId("schedule-task")).toHaveLength(1_000), { timeout: 5_000 })
    expect(performance.now() - started).toBeLessThan(5_000)
  })
})
