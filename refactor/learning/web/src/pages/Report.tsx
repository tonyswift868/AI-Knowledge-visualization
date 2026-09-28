import * as React from "react"
import { ArrowLeft, Download, Loader2 } from "lucide-react"
import { call } from "../bridge"
import { useApp } from "../store"
import { Button } from "../ui"

type Report = {
  plan: { goal: string; final_goal: string; start_date: string; end_date: string; actual_start_date: string | null
    actual_completion_date: string | null; early_days: number | null; overdue_days: number | null; replan_count: number
    planned_minutes: number; credited_minutes: number }
  modules: { module_id: string; title: string; state: string; retest_count?: number }[]
  points: { point_id: string; module_id: string; state: string; casual_errors: number; module_errors: number; check_questions: number; check_correct: number }[]
  weakest_points: { point_id: string; score: number }[]
  teacher_profile: { text?: string; check?: string } | null
}
const MODULE: Record<string, string> = { passed: "通过", skipped: "直接考通过", failed: "未通过" }
const hours = (m: number) => (m >= 90 ? `${Math.round(m / 6) / 10} 小时` : `${m} 分钟`)

/** RPT-01: what the whole plan showed, saved once the final check is complete. */
export function Report() {
  const planId = useApp((s) => s.planId)
  const go = useApp((s) => s.go)
  const toast = useApp((s) => s.toast)
  const [data, setData] = React.useState<{ report: Report; titles: Record<string, string> } | null>(null)
  React.useEffect(() => {
    if (!planId) { go("plans"); return }
    call<{ report: Report; titles: Record<string, string> }>("plan.report", { plan_id: planId }).then(setData)
      .catch((e) => { toast("error", e.message); go("plan") })
  }, [planId, go, toast])
  if (!data) return <div className="page"><div className="card"><Loader2 className="spin" /> 正在生成报告…</div></div>
  const { report: r, titles } = data
  const weak = r.weakest_points.filter((w) => w.score > 0)
  const checks = r.points.reduce((a, p) => a + p.check_questions, 0)
  const right = r.points.reduce((a, p) => a + p.check_correct, 0)
  async function save() {
    try {
      const res = await call<{ saved: boolean; path?: string }>("plan.report_export", { plan_id: planId })
      if (res.saved) toast("ok", `已保存到 ${res.path}`)
    } catch (e) { toast("error", (e as Error).message) }
  }
  return (
    <div className="page">
      <div className="row between">
        <Button small kind="ghost" onClick={() => go("plan")}><ArrowLeft />返回计划</Button>
        <Button small onClick={save}><Download />保存到本地</Button>
      </div>
      <div className="card hero" style={{ marginTop: 10, gridTemplateColumns: "1fr" }}>
        <div>
          <div className="eyebrow">学习报告</div>
          <h1 style={{ fontSize: "1.6rem" }}>{r.plan.goal}</h1>
          <p className="muted">{r.plan.final_goal}</p>
          <div className="outline-sum" style={{ marginTop: 14 }}>
            <div><span>学习时长</span><b>{hours(r.plan.credited_minutes)}</b></div>
            <div><span>日期</span><b>{r.plan.actual_start_date ?? r.plan.start_date} → {r.plan.actual_completion_date ?? "—"}</b></div>
            <div><span>完成情况</span><b>{r.plan.early_days ? `提前 ${r.plan.early_days} 天` : r.plan.overdue_days ? `晚了 ${r.plan.overdue_days} 天` : "按时完成"}</b></div>
            <div><span>最终检验</span><b>{checks ? `${right}/${checks} 题答对` : "—"}</b></div>
          </div>
        </div>
      </div>
      {(r.teacher_profile?.check || r.teacher_profile?.text) && <>
        <div className="section-title"><h2>老师的观察</h2><span>根据你的作答整理，不是打分</span></div>
        <div className="card stack">
          {r.teacher_profile?.check && <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{r.teacher_profile.check}</p>}
          {r.teacher_profile?.text && <p className="muted" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{r.teacher_profile.text}</p>}
        </div>
      </>}
      <div className="section-title"><h2>最需要注意的知识点</h2><span>随口问、模块检测和最终检验里出错次数最多的</span></div>
      <div className="card stack">
        {weak.length ? weak.map((w) => (
          <div key={w.point_id} className="row between"><span>{titles[w.point_id] ?? w.point_id}</span><span className="pill warn">出错 {w.score} 次</span></div>
        )) : <span className="muted">没有明显的薄弱点。</span>}
      </div>
      <div className="section-title"><h2>模块</h2><span>{r.modules.length} 个{r.plan.replan_count ? ` · 调整过计划 ${r.plan.replan_count} 次` : ""}</span></div>
      <div className="card stack">
        {r.modules.map((m) => (
          <div key={m.module_id} className="row between"><span>{m.title}</span><span className="pill">{MODULE[m.state] ?? m.state}</span></div>
        ))}
      </div>
    </div>
  )
}
