import { useEffect, useMemo, useState } from 'react'

import {
  TEACHER_ACTIONS,
  type StudentState,
  type TeacherAction,
  type TutorApi,
  type TutorSession,
  tutorApi,
} from './api'

type Props = {
  workspaceId: string
  task: { id: string; title: string } | null
  api?: TutorApi
  initialSession?: TutorSession | null
  onSessionChange?: (session: TutorSession) => void
  onDayClosed?: (session: TutorSession) => void
  onSelfStudy?: (session: TutorSession) => void
}
const ACTION_LABELS: Record<TeacherAction, string> = {
  diagnose: '诊断缺口',
  explain: '解释',
  analogy: '生活类比',
  worked_example: '示范例题',
  guided_question: '引导提问',
  hint: '最小提示',
  misconception_feedback: '纠正误解',
  faded_practice: '提示消退',
  independent_task: '独立任务',
  variant_test: '变式测试',
  review: '针对复习',
  encourage: '真实鼓励',
  close_day: '结束今天',
  offer_self_study: '自主学习',
}

const STUDENT_DIMENSIONS: Array<[keyof StudentState, string, boolean]> = [
  ['mastery', '掌握度', false],
  ['confidence', '信心', false],
  ['retrievability', '可提取性', false],
  ['hint_dependence', '提示依赖', false],
  ['transfer_ability', '迁移能力', false],
  ['learning_velocity', '学习速度', false],
]

function score(value: number | null | undefined, inverse = false): string {
  if (typeof value !== 'number') return '待评估'
  const normalized = value <= 1 ? value * 100 : value
  const shown = inverse ? 100 - normalized : normalized
  return `${Math.round(Math.max(0, Math.min(100, shown)))}%`
}

function actionErrorMessage(value: unknown): string {
  const message = (value as Error)?.message || '教学动作执行失败'
  return `${message}。当前学习状态未被清空，可以重试或选择其他动作。`
}

export function TutorView({
  workspaceId,
  task,
  api = tutorApi,
  initialSession = null,
  onSessionChange,
  onDayClosed,
  onSelfStudy,
}: Props) {
  const [session, setSession] = useState<TutorSession | null>(initialSession)
  const [loading, setLoading] = useState(Boolean(task) && !initialSession)
  const [busyAction, setBusyAction] = useState<TeacherAction | ''>('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!task) {
      setSession(null)
      setLoading(false)
      return
    }
    if (initialSession?.task_id === task.id) {
      setSession(initialSession)
      setLoading(false)
      return
    }
    const controller = new AbortController()
    setLoading(true)
    setError('')
    api.start(task.id, controller.signal)
      .then(next => {
        setSession(next)
        onSessionChange?.(next)
      })
      .catch(value => {
        if ((value as Error)?.name !== 'AbortError') setError(actionErrorMessage(value))
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [task?.id, initialSession])

  const student = useMemo(
    () => {
      const value = session?.state?.student_state || session?.state?.student_model || {}
      return { ...value, transfer_ability: value.transfer_ability ?? (value as StudentState & { transfer?: number }).transfer }
    },
    [session],
  )

  async function act(action: TeacherAction) {
    if (!session) return
    setBusyAction(action)
    setError('')
    try {
      const next = await api.act(session, action)
      setSession(next)
      onSessionChange?.(next)
      if (action === 'close_day') onDayClosed?.(next)
      if (action === 'offer_self_study') onSelfStudy?.(next)
    } catch (value) {
      setError(actionErrorMessage(value))
    } finally {
      setBusyAction('')
    }
  }

  if (!task) return <section aria-label="AI 教师"><h2>AI 教师</h2><p>今天没有可开始的任务。请先查看计划或处理容量问题。</p></section>
  if (loading && !session) return <section aria-label="AI 教师"><p role="status">正在恢复学习回合…</p></section>

  return <section aria-label="AI 教师">
    <header>
      <div aria-label="教师文字头像">AI</div>
      <div>
        <span>TUTOR</span>
        <h2>AI 教师</h2>
        <p>{task.title}</p>
      </div>
      <small>3D 教师不可用时仍可完整学习</small>
    </header>

    {error && <div role="alert">{error}</div>}
    {!session && <div role="alert">
      <b>学习回合尚未建立</b>
      <p>确认后端可用后重新进入今日任务。</p>
    </div>}

    {session && <>
      <section aria-label="学习回合状态">
        <h3>当前回合</h3>
        <p>{session.phase} · {session.status} · revision {session.revision}</p>
        {session.last_teacher_action && <p>最近动作：{ACTION_LABELS[session.last_teacher_action]}</p>}
      </section>

      <section aria-label="学生状态">
        <h3>学生状态</h3>
        <dl>{STUDENT_DIMENSIONS.map(([key, label, inverse]) => <div key={key}>
          <dt>{label}</dt>
          <dd>{score(student[key] as number | null | undefined, inverse)}</dd>
        </div>)}</dl>
        {(student.misconceptions || []).length > 0 && <div>
          <b>待纠正误解</b>
          <ul>{student.misconceptions?.map(item => <li key={item}>{item}</li>)}</ul>
        </div>}
      </section>

      <section aria-label="教师动作">
        <h3>教师动作</h3>
        <div>{TEACHER_ACTIONS.filter(action => !['close_day', 'offer_self_study'].includes(action)).map(action =>
          <button type="button" key={action} disabled={Boolean(busyAction)} onClick={() => void act(action)}>
            {busyAction === action ? '执行中…' : ACTION_LABELS[action]}
          </button>)}</div>
      </section>

      <section aria-label="教学消息">
        <h3>教学记录</h3>
        {session.messages.length === 0
          ? <p>选择一个教师动作开始本回合。</p>
          : <ol>{session.messages.map((message, index) => <li key={message.id || `${message.role}-${index}`}>
            <b>{message.role}</b>
            <p>{message.content}</p>
            {message.grounding?.status && <small>
              {message.grounding.status} · {message.grounding.citations?.length || 0} 条引用
            </small>}
          </li>)}</ol>}
      </section>

      <footer>
        <button type="button" disabled={Boolean(busyAction)} onClick={() => void act('close_day')}>
          {busyAction === 'close_day' ? '正在收尾…' : '结束今天'}
        </button>
        <button type="button" disabled={Boolean(busyAction)} onClick={() => void act('offer_self_study')}>
          {busyAction === 'offer_self_study' ? '正在准备…' : '继续自主学习'}
        </button>
      </footer>
    </>}
  </section>
}
