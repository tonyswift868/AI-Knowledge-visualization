import * as React from "react"
import { ArrowLeft, BookOpen, CalendarClock, ExternalLink, FileText, PlayCircle } from "lucide-react"
import { call } from "../bridge"
import { useApp } from "../store"
import { Button, Skeleton, TASK_TIME_TIP, Tip } from "../ui"
import { PlanTree } from "./NewPlan"

type Detail = React.ComponentProps<typeof PlanTree>["detail"] & {
  progress?: { remaining: number; overdue_days: number }; has_report?: boolean
  plan: { weekday_weights?: number[] }
}
type Window = { start: string; end: string; daily_cap_minutes: number; weekday_weights: number[] }
const WEEK = "一二三四五六日"

export function PlanDetail() {
  const planId = useApp((s) => s.planId)
  const go = useApp((s) => s.go)
  const toast = useApp((s) => s.toast)
  const [detail, setDetail] = React.useState<Detail | null>(null)

  React.useEffect(() => {
    if (!planId) { go("plans"); return }
    call<Detail>("plan.detail", { plan_id: planId }).then(setDetail).catch((e) => { toast("error", e.message); go("plans") })
  }, [planId, go, toast])

  if (!detail) return <div className="page"><div className="card stack"><Skeleton h={26} w="40%" /><Skeleton h={120} /></div></div>
  const points = detail.stages.reduce((a, s) => a + s.modules.reduce((b, m) => b + m.points.length, 0), 0)
  return (
    <div className="page">
      <Button small kind="ghost" onClick={() => go("plans")}><ArrowLeft />我的计划</Button>
      <div className="card hero" style={{ marginTop: 10, gridTemplateColumns: "1fr" }}>
        <div>
          <div className="eyebrow">{detail.plan.window ? `${detail.plan.window.start} → ${detail.plan.window.end}` : "学习路线"}</div>
          <h1 style={{ fontSize: "1.6rem" }}>{detail.plan.goal}</h1>
          <div className="outline-sum" style={{ marginTop: 14 }}>
            <div><span>模块</span><b>{detail.stages.reduce((a, s) => a + s.modules.length, 0)} 个 · {points} 个知识点</b></div>
            <div><span>总时长（含检验）</span><b>{Math.round(detail.plan.total_minutes / 6) / 10} 小时</b></div>
            <div><span>每日任务时长<Tip text={TASK_TIME_TIP} /></span><b>{detail.daily ? `${detail.daily.full_day} 分钟` : "—"}</b></div>
          </div>
        </div>
      </div>
      <div className="row" style={{ marginTop: 12, gap: 10 }}>
        {(detail.plan.state === "active" || detail.plan.state === "checking") &&
          <Button kind="primary" onClick={() => go("classroom")}><PlayCircle />开课</Button>}
        {detail.has_report && <Button kind="primary" onClick={() => go("report")}><FileText />学习报告</Button>}
        {detail.progress && <span className="muted small">还剩 {detail.progress.remaining} 分钟{detail.progress.overdue_days ? `，已逾期 ${detail.progress.overdue_days} 天` : ""}</span>}
      </div>
      {(detail.plan.state === "active" || detail.plan.state === "checking") && detail.plan.window &&
        <WindowCard planId={detail.plan.id} window={detail.plan.window as unknown as Window} onSaved={setDetail} />}
      <div className="section-title"><h2>路线</h2><span>点模块名展开知识点；绿色已掌握、黄色有瑕疵、红色没过</span></div>
      <div className="card"><PlanTree detail={detail} /></div>
      <div className="section-title"><h2>资料</h2><span>{detail.sources.length} 份</span></div>
      <div className="card stack">
        {detail.sources.map((s) => (
          <div key={s.id} className="row between">
            <span className="row" style={{ minWidth: 0 }}><BookOpen size={16} /><span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.title}</span></span>
            {s.url && <a className="btn small ghost" href={s.url} target="_blank" rel="noreferrer"><ExternalLink />打开</a>}
          </div>
        ))}
      </div>
    </div>
  )
}

/** PLAN-08: change the end date, daily cap and study days; only the unlearnt part is rescheduled. */
function WindowCard({ planId, window, onSaved }: { planId: string; window: Window; onSaved: (d: any) => void }) {
  const toast = useApp((s) => s.toast)
  const [open, setOpen] = React.useState(false)
  const [end, setEnd] = React.useState(window.end)
  const [cap, setCap] = React.useState(window.daily_cap_minutes)
  const [weights, setWeights] = React.useState<number[]>(window.weekday_weights)
  const [busy, setBusy] = React.useState(false)
  async function save() {
    setBusy(true)
    try {
      onSaved(await call("plan.window", { plan_id: planId, end, daily_cap: cap, weekday_weights: weights }))
      toast("ok", "已保存。只重新安排还没学的部分，已学和欠的记录都保留。")
      setOpen(false)
    } catch (e) { toast("error", (e as Error).message) } finally { setBusy(false) }
  }
  if (!open) return (
    <div className="card row between" style={{ marginTop: 12 }}>
      <span className="row"><CalendarClock size={16} />{window.start} → {window.end} · 每天最多 {window.daily_cap_minutes} 分钟</span>
      <Button small onClick={() => setOpen(true)}>修改日期与时长</Button>
    </div>
  )
  return (
    <div className="card stack" style={{ marginTop: 12 }}>
      <div className="row wrap" style={{ gap: 16 }}>
        <label className="field">结束日期<input className="input" type="date" value={end} min={window.start} onChange={(e) => setEnd(e.target.value)} /></label>
        <label className="field">每天最多（分钟）<input className="input" type="number" min={10} max={720} value={cap}
          onChange={(e) => setCap(Math.round(Number(e.target.value) || 0))} /></label>
      </div>
      <div className="row wrap" style={{ gap: 6 }}>
        <span className="muted small">每周可学：</span>
        {weights.map((w, i) => (
          <button key={i} className={`day-chip ${w === 1 ? "w1" : w === 0.5 ? "w05" : "w0"}`}
                  onClick={() => setWeights(weights.map((v, j) => (j === i ? (v === 1 ? 0.5 : v === 0.5 ? 0 : 1) : v)))}>
            周{WEEK[i]}{w === 0.5 ? "半天" : w === 0 ? "休" : ""}</button>
        ))}
      </div>
      <div className="plan-actions">
        <span className="muted small">只重新安排还没学的部分，已学和欠的记录都保留。</span>
        <Button small kind="ghost" onClick={() => setOpen(false)}>取消</Button>
        <Button small kind="primary" busy={busy} onClick={save}>保存</Button>
      </div>
    </div>
  )
}
