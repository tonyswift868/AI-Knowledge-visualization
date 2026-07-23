export const TEACHER_ACTIONS = [
  'diagnose',
  'explain',
  'analogy',
  'worked_example',
  'guided_question',
  'hint',
  'misconception_feedback',
  'faded_practice',
  'independent_task',
  'variant_test',
  'review',
  'encourage',
  'close_day',
  'offer_self_study',
] as const

export type TeacherAction = typeof TEACHER_ACTIONS[number]

export type StudentState = {
  mastery?: number | null
  confidence?: number | null
  retrievability?: number | null
  hint_dependence?: number | null
  transfer_ability?: number | null
  learning_velocity?: number | null
  misconceptions?: string[]
}

export type TutorMessage = {
  id?: string
  role: string
  phase?: string
  content: string
  grounding?: {
    status?: string
    citations?: Array<{ chunk_id: string; source_id: string; quote: string }>
  }
}

export type TutorSession = {
  run_id: string
  workspace_id?: string
  task_id: string
  phase: string
  status: string
  revision: number
  state?: {
    student_state?: StudentState
    student_model?: StudentState
    [key: string]: unknown
  }
  messages: TutorMessage[]
  last_teacher_action?: TeacherAction
}

export class TutorApiError extends Error {
  code: string
  status: number

  constructor(message: string, code = 'TUTOR_REQUEST_FAILED', status = 0) {
    super(message)
    this.name = 'TutorApiError'
    this.code = code
    this.status = status
  }
}

async function json<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new TutorApiError(
      body?.error?.message || `教学请求失败（HTTP ${response.status}）`,
      body?.error?.code || 'TUTOR_REQUEST_FAILED',
      response.status,
    )
  }
  return body as T
}

const ACTION_PROMPTS: Record<TeacherAction, string> = {
  diagnose: '请先诊断我当前的理解缺口，不要直接给答案。',
  explain: '请基于当前资料解释这个知识点，并标出证据边界。',
  analogy: '请用一个生活类比说明概念，同时指出类比失效的边界。',
  worked_example: '请展示一个完整 worked example，并说明每一步为什么成立。',
  guided_question: '请用递进问题引导我自己得出下一步。',
  hint: '请只给当前阶段所需的最小提示。',
  misconception_feedback: '请指出我回答中的具体误解，并让我修正。',
  faded_practice: '请减少提示，让我补全关键步骤。',
  independent_task: '请给我一个可以独立完成且可验证的小任务。',
  variant_test: '请给我一个不提供提示的变式测试。',
  review: '请根据掌握状态安排一次针对性复习。',
  encourage: '请根据真实进展给出简短鼓励，不要虚构完成情况。',
  close_day: '请总结今天已验证的成果、未解决问题，并自然结束今天的学习。',
  offer_self_study: '我想继续学习。请提供一个受控的自主学习任务，并明确停止条件。',
}

export const tutorApi = {
  start: async (taskId: string, signal?: AbortSignal): Promise<TutorSession> => {
    return fetch(`/api/tasks/${encodeURIComponent(taskId)}/learning-runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
      signal,
    }).then(response => json<TutorSession>(response))
  },
  act: async (session: TutorSession, action: TeacherAction): Promise<TutorSession> => {
    if (action === 'hint') {
      const result = await fetch(`/api/learning-runs/${encodeURIComponent(session.run_id)}/request-hint`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      }).then(response => json<TutorSession>(response))
      return { ...result, last_teacher_action: action }
    }
    const idempotencyKey = `teacher-action:${session.run_id}:${session.revision}:${action}`
    const result = await fetch(`/api/learning-runs/${encodeURIComponent(session.run_id)}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: ACTION_PROMPTS[action],
        teacher_action: action,
        expected_revision: session.revision,
        idempotency_key: idempotencyKey,
      }),
    }).then(response => json<TutorSession>(response))
    return { ...result, last_teacher_action: action }
  },
  submit: async (session: TutorSession, response: string): Promise<TutorSession> => {
    return fetch(`/api/learning-runs/${encodeURIComponent(session.run_id)}/submit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        response,
        evidence: response,
        expected_revision: session.revision,
        idempotency_key: `tutor-submit:${session.run_id}:${session.revision}`,
      }),
    }).then(value => json<TutorSession>(value))
  },
}

export type TutorApi = typeof tutorApi
