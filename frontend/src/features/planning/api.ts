export type PlanningTask = {
  id: string
  title: string
  task_type: string
  estimated_minutes: number
  status?: string
  locked?: boolean
  lock_reason?: string
  can_advance?: boolean
  learning_run_passed?: boolean
}

export type PlanningDay = {
  id: string
  date: string
  planned_minutes?: number
  status?: string
  phase?: 'first_pass' | 'review' | 'final' | string
  tasks: PlanningTask[]
}

export type PlanDiff = {
  reason?: string
  moved_tasks?: unknown[]
  added_tasks?: unknown[]
  removed_tasks?: unknown[]
  changed_capacity?: boolean
  changed_risk?: boolean
}

export type PlanningPlan = {
  id: string
  version?: number
  title?: string
  status?: string
  start_date?: string
  deadline?: string
  capacity_status?: string
  scheduled_minutes?: number
  unscheduled_minutes?: number
  risk_summary?: string[] | string
  study_days: PlanningDay[]
  diff?: PlanDiff | null
  phase_policy?: {
    first_pass_ratio?: number
    review_ratio?: number
    buffer_ratio?: number
  }
}

export type PlanningProgress = {
  completed_tasks?: number
  validated_completed_tasks?: number
  total_tasks?: number
  planned_minutes?: number
  actual_minutes?: number
  completion_rate?: number
  overdue_tasks?: number
  at_risk?: boolean
}

export type PlanningSnapshot = {
  plan: PlanningPlan | null
  today: { date: string; day: PlanningDay | null; plan_id?: string } | null
  calendar: PlanningDay[]
  progress: PlanningProgress | null
}

export class PlanningApiError extends Error {
  code: string
  status: number

  constructor(message: string, code = 'PLANNING_REQUEST_FAILED', status = 0) {
    super(message)
    this.name = 'PlanningApiError'
    this.code = code
    this.status = status
  }
}

async function json<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new PlanningApiError(
      body?.error?.message || `计划请求失败（HTTP ${response.status}）`,
      body?.error?.code || 'PLANNING_REQUEST_FAILED',
      response.status,
    )
  }
  return body as T
}

function unwrapPlan(value: any): PlanningPlan {
  const plan = (value?.payload || value) as PlanningPlan & { phase_allocation?: { first_pass_ratio?: number } }
  if (!plan.phase_policy && plan.phase_allocation) {
    const firstPass = plan.phase_allocation.first_pass_ratio
    plan.phase_policy = {
      first_pass_ratio: firstPass,
      review_ratio: typeof firstPass === 'number' ? 1 - firstPass : undefined,
    }
  }
  return plan
}

export const planningApi = {
  load: async (workspaceId: string, signal?: AbortSignal): Promise<PlanningSnapshot> => {
    const root = `/api/workspaces/${encodeURIComponent(workspaceId)}`
    const [plans, today, calendar, progress] = await Promise.all([
      fetch(`${root}/plans`, { signal }).then(response => json<any[]>(response)),
      fetch(`${root}/today`, { signal }).then(response => json<PlanningSnapshot['today']>(response)),
      fetch(`${root}/calendar`, { signal }).then(response => json<PlanningDay[]>(response)),
      fetch(`${root}/progress`, { signal }).then(response => json<PlanningProgress>(response)),
    ])
    const normalized = plans.map(unwrapPlan)
    const active = [...normalized].reverse().find(plan => plan.status === 'active')
      || normalized.at(-1)
      || null
    return { plan: active, today, calendar, progress }
  },
  activate: async (planId: string): Promise<PlanningPlan> => {
    return fetch(`/api/plans/${encodeURIComponent(planId)}/activate`, { method: 'POST' })
      .then(response => json<PlanningPlan>(response))
  },
  advance: async (taskId: string): Promise<PlanningTask> => {
    return fetch(`/api/tasks/${encodeURIComponent(taskId)}/start`, { method: 'POST' })
      .then(response => json<PlanningTask>(response))
  },
  replan: async (planId: string, reason: string): Promise<{ plan: PlanningPlan; changes?: PlanDiff }> => {
    return fetch(`/api/plans/${encodeURIComponent(planId)}/replan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    }).then(response => json<{ plan: PlanningPlan; changes?: PlanDiff }>(response))
  },
}

export type PlanningApi = typeof planningApi
