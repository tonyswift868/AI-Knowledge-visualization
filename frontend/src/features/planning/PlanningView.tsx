import { useEffect, useMemo, useState } from 'react'

import {
  type PlanDiff,
  type PlanningApi,
  type PlanningDay,
  type PlanningPlan,
  type PlanningSnapshot,
  type PlanningTask,
  planningApi,
} from './api'

type Props = {
  workspaceId: string
  api?: PlanningApi
  initialSnapshot?: PlanningSnapshot | null
  onTodayReady?: (day: PlanningDay | null) => void
  onTaskAdvanced?: (task: PlanningTask) => void
}
type PhaseSummary = {
  firstPassDays: number
  reviewDays: number
  totalDays: number
  firstPassRatio: number | null
  reviewRatio: number | null
}

const REVIEW_TYPES = new Set(['review', 'final', 'final_assessment', 'final_task'])

function phaseSummary(plan: PlanningPlan): PhaseSummary {
  const days = plan.study_days || []
  const firstPassDays = days.filter(day => {
    if (day.phase) return day.phase === 'first_pass'
    return day.tasks.some(task => !REVIEW_TYPES.has(task.task_type))
  }).length
  const reviewDays = days.filter(day => {
    if (day.phase) return ['review', 'final'].includes(day.phase)
    return day.tasks.length > 0 && day.tasks.every(task => REVIEW_TYPES.has(task.task_type))
  }).length
  const totalDays = days.length
  return {
    firstPassDays,
    reviewDays,
    totalDays,
    firstPassRatio: plan.phase_policy?.first_pass_ratio ?? (totalDays ? firstPassDays / totalDays : null),
    reviewRatio: plan.phase_policy?.review_ratio ?? (totalDays ? reviewDays / totalDays : null),
  }
}

function ratio(value: number | null): string {
  return value === null ? '待计算' : `${Math.round(value * 100)}%`
}

function DiffPanel({ diff }: { diff?: PlanDiff | null }) {
  if (!diff) return <section aria-label="计划调整记录"><h3>调整记录</h3><p>尚无调整记录。缺席、失败或容量变化后会在这里显示 before/after diff。</p></section>
  return <section aria-label="计划调整记录">
    <h3>调整记录</h3>
    <p>{diff.reason || '计划已根据最新状态调整'}</p>
    <dl>
      <div><dt>移动</dt><dd>{diff.moved_tasks?.length || 0}</dd></div>
      <div><dt>新增</dt><dd>{diff.added_tasks?.length || 0}</dd></div>
      <div><dt>移除</dt><dd>{diff.removed_tasks?.length || 0}</dd></div>
      <div><dt>容量变化</dt><dd>{diff.changed_capacity ? '是' : '否'}</dd></div>
      <div><dt>风险变化</dt><dd>{diff.changed_risk ? '是' : '否'}</dd></div>
    </dl>
  </section>
}

function Capacity({ plan }: { plan: PlanningPlan }) {
  const scheduled = plan.scheduled_minutes
    ?? plan.study_days.reduce((total, day) => total + (day.planned_minutes || 0), 0)
  const unscheduled = plan.unscheduled_minutes
  const known = typeof unscheduled === 'number'
  const status = plan.capacity_status || (known && unscheduled > 0 ? 'insufficient' : 'unknown')
  return <section aria-label="计划容量">
    <h3>容量</h3>
    <dl>
      <div><dt>已安排</dt><dd>{scheduled} 分钟</dd></div>
      <div><dt>未安排</dt><dd>{known ? `${unscheduled} 分钟` : '待后端评估'}</dd></div>
      <div><dt>状态</dt><dd>{status}</dd></div>
    </dl>
    {status === 'insufficient' && <p role="alert">当前容量不足。应缩小范围、延长截止日期或增加每日投入。</p>}
  </section>
}

function PhasePolicy({ plan }: { plan: PlanningPlan }) {
  const summary = phaseSummary(plan)
  const firstPassInRange = summary.firstPassRatio !== null
    && summary.firstPassRatio >= 0.4
    && summary.firstPassRatio <= 0.5
  const reviewInRange = summary.reviewRatio !== null
    && summary.reviewRatio >= 0.5
    && summary.reviewRatio <= 0.6
  return <section aria-label="学习阶段分配">
    <h3>40/50 阶段</h3>
    <div>
      <article>
        <span>第一轮整体学习</span>
        <b>{ratio(summary.firstPassRatio)}</b>
        <small>目标区间 40%–50% · {summary.firstPassDays} 天</small>
      </article>
      <article>
        <span>滚动复习与独立掌握</span>
        <b>{ratio(summary.reviewRatio)}</b>
        <small>目标区间 50%–60% · {summary.reviewDays} 天</small>
      </article>
    </div>
    {(!firstPassInRange || !reviewInRange) && <p role="note">当前阶段比例未落入目标区间，请结合容量和截止日期重新规划。</p>}
  </section>
}

export function PlanningView({
  workspaceId,
  api = planningApi,
  initialSnapshot = null,
  onTodayReady,
  onTaskAdvanced,
}: Props) {
  const [snapshot, setSnapshot] = useState<PlanningSnapshot | null>(initialSnapshot)
  const [loading, setLoading] = useState(!initialSnapshot)
  const [busyTask, setBusyTask] = useState('')
  const [error, setError] = useState('')

  async function load(signal?: AbortSignal) {
    setLoading(true)
    setError('')
    try {
      const next = await api.load(workspaceId, signal)
      setSnapshot(next)
      onTodayReady?.(next.today?.day || null)
    } catch (value) {
      if ((value as Error)?.name !== 'AbortError') setError((value as Error)?.message || '计划读取失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (initialSnapshot) {
      setSnapshot(initialSnapshot)
      setLoading(false)
      onTodayReady?.(initialSnapshot.today?.day || null)
      return
    }
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [workspaceId, initialSnapshot])

  const todayDate = snapshot?.today?.date || new Date().toISOString().slice(0, 10)
  const days = useMemo(() => snapshot?.calendar || snapshot?.plan?.study_days || [], [snapshot])

  async function activate() {
    if (!snapshot?.plan) return
    setError('')
    try {
      const plan = await api.activate(snapshot.plan.id)
      setSnapshot(current => current ? { ...current, plan: { ...current.plan!, ...plan, status: 'active' } } : current)
    } catch (value) {
      setError((value as Error)?.message || '计划激活失败')
    }
  }

  async function advance(task: PlanningTask) {
    setBusyTask(task.id)
    setError('')
    try {
      const next = await api.advance(task.id)
      onTaskAdvanced?.(next)
      await load()
    } catch (value) {
      setError((value as Error)?.message || '提前学习失败')
    } finally {
      setBusyTask('')
    }
  }

  async function replan() {
    if (!snapshot?.plan) return
    setError('')
    try {
      const result = await api.replan(snapshot.plan.id, 'manual_capacity_review')
      setSnapshot(current => current ? {
        ...current,
        plan: { ...result.plan, diff: result.changes || result.plan.diff },
        calendar: result.plan.study_days,
      } : current)
    } catch (value) {
      setError((value as Error)?.message || '重新规划失败')
    }
  }

  if (loading && !snapshot) return <section aria-label="学习计划"><p role="status">正在读取计划…</p></section>
  if (!snapshot?.plan) return <section aria-label="学习计划">
    <h2>学习计划</h2>
    {error
      ? <div role="alert"><b>{error}</b><p>确认目标和可行性后重试。</p><button onClick={() => void load()}>重试</button></div>
      : <p>尚未生成计划。请先完成知识地图与可行性确认。</p>}
  </section>

  const plan = snapshot.plan
  return <section aria-label="学习计划">
    <header>
      <div><span>PLANNING</span><h2>{plan.title || '学习计划'}</h2><p>版本 {plan.version || 1} · {plan.status || 'draft'}</p></div>
      <div>
        {plan.status !== 'active' && <button type="button" onClick={activate}>激活计划</button>}
        <button type="button" onClick={replan}>根据容量重排</button>
      </div>
    </header>

    {error && <div role="alert"><b>{error}</b><p>当前计划保持不变，可以重试或稍后继续。</p></div>}
    <PhasePolicy plan={plan} />
    <Capacity plan={plan} />

    <section aria-label="计划日历">
      <h3>日历</h3>
      {days.map(day => <article key={day.id}>
        <header><b>{day.date}</b><span>{day.date === todayDate ? '今天' : day.phase || '计划日'}</span></header>
        <ul>{day.tasks.map(task => {
          const future = day.date > todayDate
          const locked = task.locked === true
          const canAdvance = future && !locked && task.can_advance !== false && task.status !== 'completed'
          return <li key={task.id}>
            <div><b>{task.title}</b><span>{task.estimated_minutes} 分钟 · {task.status || 'not_started'}</span></div>
            {locked && <p>已锁定：{task.lock_reason || '前置任务尚未完成'}</p>}
            {canAdvance && <button type="button" disabled={busyTask === task.id} onClick={() => void advance(task)}>
              {busyTask === task.id ? '正在开始…' : '提前学习'}
            </button>}
          </li>
        })}</ul>
      </article>)}
    </section>

    <DiffPanel diff={plan.diff} />
  </section>
}
