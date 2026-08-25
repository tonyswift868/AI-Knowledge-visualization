export const WEEKDAYS = [
  ["monday", "周一"],
  ["tuesday", "周二"],
  ["wednesday", "周三"],
  ["thursday", "周四"],
  ["friday", "周五"],
  ["saturday", "周六"],
  ["sunday", "周日"],
] as const

export type Weekday = typeof WEEKDAYS[number][0]

export type PlanTaskInput = {
  task_id: string
  title: string
  minutes: number
  unsplittable?: boolean
}

export type PlanCreationInput = {
  request_id: string
  plan_id: string
  start_date: string
  deadline: string
  daily_minutes: number
  weekly_learning_days: Weekday[]
  unavailable_dates: string[]
  review_rounds: number
  buffer_minutes: number
  tasks: PlanTaskInput[]
}

export type EffortRange = {
  low_minutes: number
  expected_minutes: number
  high_minutes: number
}

export type ReviewRoundEffort = EffortRange & {
  round_id: string
  round_index: number
  review_item_count: number
}

export type FeasibilityExplanation = {
  category: string
  summary: string
  required_minutes?: number | null
  available_minutes?: number | null
  shortfall_minutes?: number | null
  suggested_action: string
}

export type ScheduleTask = {
  allocation_id: string
  task_id: string
  title: string
  kind: "task" | "review" | "final_buffer"
  label: string
  minutes: number
  chunk_index: number
  chunk_count: number
}

export type ScheduleDay = {
  date: string
  total_minutes: number
  initial_minutes: number
  review_minutes: number
  buffer_minutes: number
  tasks: ScheduleTask[]
}

export type ScheduleView = {
  schedule_id: string
  version: number
  start_date: string
  finish_date: string
  deadline: string
  immutable: boolean
  days: ScheduleDay[]
}

export type PreviewResponse = {
  delivery_id: string
  preview: {
    preview_id: string
    plan_id: string
    status: "ready_for_confirmation" | "infeasible" | "not_solved"
    initial_learning: EffortRange
    review_rounds: ReviewRoundEffort[]
    review_total: EffortRange
    effort: { total: EffortRange }
    total_hours: number
    finish_date: string | null
    buffer_total_minutes: number
    feasibility: {
      feasible: boolean
      required_minutes: number
      available_minutes: number
      explanations: FeasibilityExplanation[]
    }
  }
  schedule: ScheduleView | null
  receipt?: {
    operation: string
    cp_sat_calls: number
    cp_sat_allowed: boolean
    provider_calls: number
  }
  source_publication_id?: string
}

export type PublishedResponse = {
  delivery_id: string
  published: {
    publication_id: string
    plan_id: string
    publication_version: number
    preview_id: string
    immutable: true
    published_on: string
  }
  current_schedule: ScheduleView
  published_schedule_unchanged: boolean
}

export type DailyTask = {
  task_id: string
  title: string
  allocation_kind: "task" | "review"
  planned_minutes: number
  scheduled_dates: string[]
  status: "overdue" | "due_today" | "upcoming" | "completed" | "skipped_by_user"
}

export type DailyResponse = {
  delivery_id: string
  snapshot: {
    as_of_date: string
    deadline: string
    status: "active" | "completed" | "expired_with_overdue"
    schedule_version: number
  }
  groups: Record<"OVERDUE" | "DUE TODAY" | "UPCOMING" | "COMPLETED", DailyTask[]>
  expired_message: string | null
  automatic_shift_applied: false
}

export type ShiftPreviewResponse = {
  delivery_id: string
  shift_preview: {
    preview_id: string
    old_end: string
    new_end: string
    calendar_days_added: number
    learning_days_added: number
    tasks_moved: number
    allocations_moved: number
    non_learning_days_skipped: number
  }
  receipt: {
    operation: "shift"
    cp_sat_calls: 0
    cp_sat_allowed: false
    provider_calls: 0
  }
}

export type DemoResponse = {
  delivery_id: string
  fixture_name: string
  creation: PlanCreationInput
}

export type PlanProductClient = {
  demo(): Promise<DemoResponse>
  createPreview(value: PlanCreationInput): Promise<PreviewResponse>
  publish(previewId: string): Promise<PublishedResponse>
  getPublished(publicationId: string): Promise<PublishedResponse>
  daily(publicationId: string, today?: string): Promise<DailyResponse>
  complete(publicationId: string, taskId: string, completedOn?: string): Promise<unknown>
  previewShift(publicationId: string, value: {
    calendar_days_delta: number
    weekly_learning_days: Weekday[]
    unavailable_dates: string[]
  }): Promise<ShiftPreviewResponse>
  confirmShift(publicationId: string, previewId: string): Promise<{ current_schedule: ScheduleView }>
  regenerate(publicationId: string, creation: PlanCreationInput): Promise<PreviewResponse>
}
