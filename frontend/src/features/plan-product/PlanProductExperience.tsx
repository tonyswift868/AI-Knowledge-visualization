import { useEffect, useMemo, useState } from "react"
import { CalendarDays, Check, Clock3, RefreshCw, Rocket, WandSparkles } from "lucide-react"

import { planProductApi } from "./api"
import {
  WEEKDAYS,
  type DailyResponse,
  type PlanCreationInput,
  type PlanProductClient,
  type PreviewResponse,
  type PublishedResponse,
  type ShiftPreviewResponse,
  type Weekday,
} from "./types"
import "./plan-product.css"

const iso = (value: Date) =>
  `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`
const addDays = (count: number) => {
  const value = new Date()
  value.setDate(value.getDate() + count)
  return iso(value)
}

const fallbackCreation = (): PlanCreationInput => ({
  request_id: `plan-ui-${Date.now()}`,
  plan_id: "learning-plan",
  start_date: iso(new Date()),
  deadline: addDays(42),
  daily_minutes: 60,
  weekly_learning_days: ["monday", "tuesday", "wednesday", "thursday", "friday"],
  unavailable_dates: [],
  review_rounds: 3,
  buffer_minutes: 30,
  tasks: [
    { task_id: "material-foundation", title: "理解资料的核心概念", minutes: 90 },
    { task_id: "material-practice", title: "完成例题与主动练习", minutes: 120 },
    { task_id: "material-assessment", title: "独立完成验收任务", minutes: 60 },
  ],
})

const minutes = (value: number) => `${value.toLocaleString("zh-CN")} 分钟`

const reasonCopy: Record<string, { title: string; detail: string }> = {
  deadline_too_short: { title: "截止日期", detail: "当前截止日期之前没有足够的学习日。" },
  weekly_preference_too_restrictive: { title: "每周可用时间", detail: "选择的每周学习日限制了总容量。" },
  selected_review_rounds_too_many: { title: "复习轮次", detail: "初学可以放下，但完整复习轮次放不下。" },
  unsplittable_task: { title: "不可拆任务", detail: "至少一个不可拆任务超过了单日容量。" },
  not_enough_total_capacity: { title: "总容量", detail: "初学、复习和缓冲的总分钟数超过可用容量。" },
  prerequisite_conflict: { title: "任务依赖", detail: "任务依赖或已完成记录互相冲突。" },
  constraint_interaction: { title: "约束组合", detail: "日期、容量和任务约束组合后无法排入日历。" },
  solver_not_solved: { title: "求解时间", detail: "本次求解尚未得到可行性结论，请明确重试。" },
}

function EffortCard({ label, value, tone = "" }: { label: string; value: { low_minutes: number; expected_minutes: number; high_minutes: number }; tone?: string }) {
  return (
    <article className={`plan-product-effort-card ${tone}`}>
      <span>{label}</span>
      <strong>{minutes(value.expected_minutes)}</strong>
      <small>{minutes(value.low_minutes)} – {minutes(value.high_minutes)}</small>
    </article>
  )
}

function SchedulePreview({ data, published = false }: { data: NonNullable<PreviewResponse["schedule"]>; published?: boolean }) {
  return (
    <section className="plan-product-section" aria-label={published ? "已发布日程" : "日程预览"}>
      <div className="plan-product-section-head">
        <div>
          <span className="plan-product-kicker">SCHEDULE PREVIEW</span>
          <h2>每日任务与复习占用</h2>
        </div>
        <div className="plan-product-finish">
          <small>预计完成</small>
          <b>{data.finish_date}</b>
          {published && <em>IMMUTABLE</em>}
        </div>
      </div>
      <div className="plan-product-calendar" data-testid="plan-product-calendar">
        {data.days.map(day => (
          <article className="plan-product-day" key={day.date}>
            <header>
              <time>{day.date}</time>
              <b>{minutes(day.total_minutes)}</b>
            </header>
            <div className="plan-product-load-line">
              <span>初学 {day.initial_minutes}</span>
              <span>复习 {day.review_minutes}</span>
              <span>缓冲 {day.buffer_minutes}</span>
            </div>
            {day.tasks.map(task => (
              <div className={`plan-product-task kind-${task.kind}`} data-testid="schedule-task" key={task.allocation_id}>
                <span className="plan-product-task-label">
                  {task.kind === "task" ? "初学 · Initial Learning" : task.kind === "review" ? `复习 · ${task.label}` : "缓冲 · Buffer"}
                </span>
                <b>{task.title}</b>
                <small>{minutes(task.minutes)}{task.chunk_count > 1 ? ` · ${task.chunk_index}/${task.chunk_count}` : ""}</small>
              </div>
            ))}
          </article>
        ))}
      </div>
    </section>
  )
}

function DailyView({ data, onComplete }: { data: DailyResponse; onComplete(taskId: string): void }) {
  const labels = ["OVERDUE", "DUE TODAY", "UPCOMING", "COMPLETED"] as const
  return (
    <section className="plan-product-section" aria-label="每日计划状态">
      <div className="plan-product-section-head">
        <div>
          <span className="plan-product-kicker">DAILY RUNTIME · {data.snapshot.as_of_date}</span>
          <h2>今天不替昨天挪任务</h2>
        </div>
        <span className="plan-product-no-auto">自动移动：关闭</span>
      </div>
      {data.expired_message && <div className="plan-product-expired" role="alert">{data.expired_message}</div>}
      <div className="plan-product-status-grid">
        {labels.map(label => (
          <article className={`plan-product-status status-${label.toLowerCase().replaceAll(" ", "-")}`} key={label}>
            <header><b>{label}</b><span>{data.groups[label]?.length || 0}</span></header>
            {(data.groups[label] || []).map(task => (
              <div className="plan-product-runtime-task" key={`${label}-${task.task_id}`}>
                <span>{task.allocation_kind === "review" ? "Review" : "Initial"}</span>
                <b>{task.title}</b>
                <small>{minutes(task.planned_minutes)} · {task.scheduled_dates.join(" / ")}</small>
                {(label === "OVERDUE" || label === "DUE TODAY") && (
                  <button type="button" onClick={() => onComplete(task.task_id)}>标记完成</button>
                )}
              </div>
            ))}
            {!data.groups[label]?.length && <p>暂无任务</p>}
          </article>
        ))}
      </div>
    </section>
  )
}

export function PlanProductExperience({ client = planProductApi }: { client?: PlanProductClient }) {
  const [creation, setCreation] = useState<PlanCreationInput>(fallbackCreation)
  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [published, setPublished] = useState<PublishedResponse | null>(null)
  const [daily, setDaily] = useState<DailyResponse | null>(null)
  const [shift, setShift] = useState<ShiftPreviewResponse | null>(null)
  const [shiftDays, setShiftDays] = useState(1)
  const [dailyDate, setDailyDate] = useState(iso(new Date()))
  const [unavailableDate, setUnavailableDate] = useState("")
  const [busy, setBusy] = useState("")
  const [message, setMessage] = useState("")
  const [fixtureName, setFixtureName] = useState("")

  useEffect(() => {
    let cancelled = false
    client.demo().then(result => {
      if (cancelled) return
      setCreation(result.creation)
      setFixtureName(result.fixture_name)
    }).catch(() => {
      if (!cancelled) setMessage("演示资料未载入；仍可使用当前表单预览。")
    })
    return () => { cancelled = true }
  }, [client])

  const taskMinutes = useMemo(
    () => creation.tasks.reduce((sum, item) => sum + item.minutes, 0),
    [creation.tasks],
  )

  function change<K extends keyof PlanCreationInput>(key: K, value: PlanCreationInput[K]) {
    setCreation(current => ({ ...current, [key]: value, request_id: `plan-ui-${Date.now()}-${key}` }))
    setPreview(null)
    setShift(null)
  }

  function toggleWeekday(value: Weekday) {
    const selected = creation.weekly_learning_days.includes(value)
    const next = selected
      ? creation.weekly_learning_days.filter(item => item !== value)
      : [...creation.weekly_learning_days, value]
    if (next.length) change("weekly_learning_days", next)
  }

  async function runPreview() {
    setBusy("preview")
    setMessage("")
    try {
      const result = await client.createPreview(creation)
      setPreview(result)
      setShift(null)
      setMessage(result.preview.status === "ready_for_confirmation" ? "预览已生成；确认明细后再发布。" : "当前约束不可行，请按原因调整。")
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "预览失败")
    } finally {
      setBusy("")
    }
  }

  async function publishPlan() {
    if (!preview || preview.preview.status !== "ready_for_confirmation") return
    setBusy("publish")
    try {
      const result = await client.publish(preview.preview.preview_id)
      setPublished(result)
      setDaily(await client.daily(result.published.publication_id, dailyDate))
      setMessage(`已发布 V${result.published.publication_version}；该发布版本不可变。`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "发布失败")
    } finally {
      setBusy("")
    }
  }

  async function refreshDaily() {
    if (!published) return
    setBusy("daily")
    try {
      setDaily(await client.daily(published.published.publication_id, dailyDate))
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "每日状态刷新失败")
    } finally {
      setBusy("")
    }
  }

  async function completeTask(taskId: string) {
    if (!published) return
    try {
      await client.complete(published.published.publication_id, taskId, dailyDate)
      await refreshDaily()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "完成状态记录失败")
    }
  }

  async function previewShift() {
    if (!published) return
    setBusy("shift")
    try {
      setShift(await client.previewShift(published.published.publication_id, {
        calendar_days_delta: shiftDays,
        weekly_learning_days: creation.weekly_learning_days,
        unavailable_dates: creation.unavailable_dates,
      }))
      setMessage("整体后移仍是预览；尚未应用。")
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "整体后移预览失败")
    } finally {
      setBusy("")
    }
  }

  async function confirmShift() {
    if (!published || !shift) return
    setBusy("shift-confirm")
    try {
      await client.confirmShift(published.published.publication_id, shift.shift_preview.preview_id)
      const next = await client.getPublished(published.published.publication_id)
      setPublished(next)
      setDaily(await client.daily(next.published.publication_id, dailyDate))
      setShift(null)
      setMessage("已确认并应用整体后移；原 PublishedPlan 仍保持不变。")
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "整体后移应用失败")
    } finally {
      setBusy("")
    }
  }

  async function regenerate() {
    if (!published) return
    setBusy("regenerate")
    try {
      const result = await client.regenerate(published.published.publication_id, creation)
      setPreview(result)
      setShift(null)
      setMessage(`重新优化完成：CP-SAT 调用 ${result.receipt?.cp_sat_calls ?? "—"} 次。原发布版本未改动，请检查新预览后再 Publish。`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "重新优化失败")
    } finally {
      setBusy("")
    }
  }

  const explanations = preview?.preview.feasibility.explanations || []
  const infeasible = preview && preview.preview.status !== "ready_for_confirmation"

  return (
    <main className="plan-product-shell">
      <header className="plan-product-hero">
        <div>
          <span>MENTORFLOW · PLAN PRODUCT</span>
          <h1>先看清初学与复习成本，再决定发布。</h1>
          <p>预览不会生成 PublishedPlan。漏学不会自动挪动；整体后移和重新优化是两种不同操作。</p>
        </div>
        <div className="plan-product-fixture">
          <Rocket size={20} />
          <b>{fixtureName || "大资料演示"}</b>
          <span>{creation.tasks.length} 个知识点 · 初学 {minutes(taskMinutes)} · 3 轮复习演示</span>
        </div>
      </header>

      <section className="plan-product-section plan-product-form" aria-label="学习计划创建表单">
        <div className="plan-product-section-head">
          <div><span className="plan-product-kicker">CREATE PREVIEW</span><h2>计划约束</h2></div>
          <span>当前资料：{creation.tasks.length.toLocaleString("zh-CN")} 个任务</span>
        </div>
        <div className="plan-product-fields">
          <label>截止日期<input aria-label="截止日期" type="date" value={creation.deadline} min={creation.start_date} onChange={event => change("deadline", event.target.value)} /></label>
          <label>每天学习分钟<input aria-label="每天学习分钟" type="number" min="1" max="1440" value={creation.daily_minutes} onChange={event => change("daily_minutes", Number(event.target.value))} /></label>
          <label>缓冲分钟<input aria-label="缓冲分钟" type="number" min="0" max="1440" value={creation.buffer_minutes} onChange={event => change("buffer_minutes", Number(event.target.value))} /></label>
        </div>
        <fieldset className="plan-product-choice"><legend>每周学习日</legend><div>{WEEKDAYS.map(([value, label]) => <button type="button" aria-pressed={creation.weekly_learning_days.includes(value)} className={creation.weekly_learning_days.includes(value) ? "selected" : ""} key={value} onClick={() => toggleWeekday(value)}>{label}</button>)}</div></fieldset>
        <fieldset className="plan-product-choice review-round-choice"><legend>复习轮次（明确选择）</legend><div>{Array.from({ length: 13 }, (_, value) => <button type="button" data-testid={`review-round-${value}`} aria-pressed={creation.review_rounds === value} className={creation.review_rounds === value ? "selected" : ""} key={value} onClick={() => change("review_rounds", value)}>{value}</button>)}</div></fieldset>
        <div className="plan-product-unavailable">
          <label>不可学习日期<input aria-label="不可学习日期" type="date" min={creation.start_date} max={creation.deadline} value={unavailableDate} onChange={event => setUnavailableDate(event.target.value)} /></label>
          <button type="button" disabled={!unavailableDate} onClick={() => { if (unavailableDate && !creation.unavailable_dates.includes(unavailableDate)) change("unavailable_dates", [...creation.unavailable_dates, unavailableDate]); setUnavailableDate("") }}>添加</button>
          <div>{creation.unavailable_dates.map(value => <button type="button" key={value} onClick={() => change("unavailable_dates", creation.unavailable_dates.filter(item => item !== value))}>{value} ×</button>)}</div>
        </div>
        <div className="plan-product-actions">
          <button className="plan-product-primary" type="button" disabled={busy === "preview"} onClick={runPreview}><WandSparkles size={16} />{busy === "preview" ? "正在求解…" : "生成计划预览"}</button>
        </div>
      </section>

      {preview && (
        <>
          <section className="plan-product-section" aria-label="投入预览">
            <div className="plan-product-section-head"><div><span className="plan-product-kicker">EFFORT PREVIEW</span><h2>复习不是免费的</h2></div><span>范围显示低 / 预期 / 高</span></div>
            <div className="plan-product-effort-grid">
              <EffortCard label="Initial Learning" value={preview.preview.initial_learning} />
              {preview.preview.review_rounds.map(round => <EffortCard key={round.round_id} label={`Review Round ${round.round_index}`} value={round} />)}
              {!preview.preview.review_rounds.length && <EffortCard label="Review Rounds · 0 selected" value={{ low_minutes: 0, expected_minutes: 0, high_minutes: 0 }} />}
              <EffortCard label="Review Total" value={preview.preview.review_total} tone="review-total" />
              <EffortCard label="Grand Total" value={preview.preview.effort.total} tone="grand-total" />
            </div>
          </section>

          {infeasible && (
            <section className="plan-product-infeasible" role="alert">
              <CalendarDays size={22} />
              <div>
                <h2>按每天 {creation.daily_minutes} 分钟和 {creation.review_rounds} 轮复习，你的可用时间不够。</h2>
                <p>需要 {minutes(preview.preview.feasibility.required_minutes)}，目前可用 {minutes(preview.preview.feasibility.available_minutes)}。</p>
                <div className="plan-product-reasons">
                  {explanations.map(item => {
                    const copy = reasonCopy[item.category] || { title: item.category, detail: item.summary }
                    return <article key={item.category}><b>{copy.title}</b><span>{copy.detail}</span><small>{item.suggested_action}</small></article>
                  })}
                </div>
              </div>
            </section>
          )}

          {preview.schedule && <SchedulePreview data={preview.schedule} />}
          <div className="plan-product-publish-bar">
            <div><b>发布前确认</b><span>只有点击 Publish 才会生成不可变 PublishedPlan。</span></div>
            <button type="button" disabled={preview.preview.status !== "ready_for_confirmation" || busy === "publish"} onClick={publishPlan}><Check size={16} />Publish</button>
          </div>
        </>
      )}

      {published && (
        <>
          <section className="plan-product-published">
            <div><span>PUBLISHED · V{published.published.publication_version}</span><h2>发布版本不可变</h2><p>{published.published.publication_id}</p></div>
            <strong>IMMUTABLE</strong>
          </section>
          <div className="plan-product-daily-controls"><label>查看日期<input aria-label="每日状态日期" type="date" value={dailyDate} onChange={event => setDailyDate(event.target.value)} /></label><button type="button" onClick={refreshDaily} disabled={busy === "daily"}><RefreshCw size={15} />刷新每日状态</button></div>
          {daily && <DailyView data={daily} onComplete={completeTask} />}

          <section className="plan-product-section plan-product-operations" aria-label="计划调整操作">
            <div className="plan-product-operation">
              <span className="plan-product-kicker">SHIFT · 不重新优化</span>
              <h2>整体往后移</h2>
              <p>只移动尚未完成任务的日期，跳过非学习日；预览与确认分开。</p>
              <label>先后移日历天数<input aria-label="整体后移天数" type="number" min="1" value={shiftDays} onChange={event => setShiftDays(Number(event.target.value))} /></label>
              <button type="button" onClick={previewShift} disabled={busy === "shift"}>预览整体往后移</button>
              {shift && <div className="plan-product-shift-preview" data-testid="shift-preview"><dl><div><dt>旧结束日期</dt><dd>{shift.shift_preview.old_end}</dd></div><div><dt>新结束日期</dt><dd>{shift.shift_preview.new_end}</dd></div><div><dt>增加天数</dt><dd>{shift.shift_preview.calendar_days_added}</dd></div><div><dt>跳过非学习日</dt><dd>{shift.shift_preview.non_learning_days_skipped}</dd></div><div><dt>移动任务数</dt><dd>{shift.shift_preview.tasks_moved}</dd></div></dl><small>CP-SAT 调用：{shift.receipt.cp_sat_calls}（整体后移不会重新求解）</small><button type="button" onClick={confirmShift} disabled={busy === "shift-confirm"}>确认并应用整体后移</button></div>}
            </div>
            <div className="plan-product-operation regenerate">
              <span className="plan-product-kicker">REGENERATE · 重新运行 CP-SAT</span>
              <h2>重新优化计划</h2>
              <p>这是新的求解与新预览。原 PublishedPlan 保持不变，仍需再次 Publish 才产生新版本。</p>
              <button type="button" onClick={regenerate} disabled={busy === "regenerate"}><Clock3 size={16} />重新优化计划</button>
            </div>
          </section>
          <SchedulePreview data={published.current_schedule} published />
        </>
      )}

      {message && <div className="plan-product-message" role="status">{message}</div>}
    </main>
  )
}

export default PlanProductExperience
