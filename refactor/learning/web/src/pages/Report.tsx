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
  points: { point_id: string; module_id: string; summary: string; state: string; casual_errors: number; module_errors: number; check_questions: number; check_correct: number }[]
  questions?: { question_id: string; module_id: string; check_questions: number; check_correct: number; passed: boolean }[]
  weakest_points: { point_id: string; score: number }[]
  point_comments?: Record<string, string | null>
  teacher_profile: { text?: string; check?: string } | null
  live_points?: { point_id: string; title: string }[]
}
type Views = {
  modules: { module_id: string; title: string; mastered: number; total: number }[]
  history: { day: string; target_minutes: number; actual_minutes: number; confirmed_minutes: number; tentative_minutes: number; debt_minutes: number; ahead_minutes: number }[]
  errors: { casual: number; module: number; check: number }
}
type ReportData = { report: Report; titles: Record<string, string>; views: Views }
const MODULE: Record<string, string> = { passed: "通过", skipped: "直接考通过", failed: "未通过" }
const hours = (m: number) => (m >= 90 ? `${Math.round(m / 6) / 10} 小时` : `${m} 分钟`)
const track: React.CSSProperties = { height: 12, background: "var(--border-strong)", borderRadius: 8, overflow: "hidden", flex: 1 }
function Bar({ value, total, color = "var(--accent)" }: { value: number; total: number; color?: string }) {
  return <div style={track}><div style={{ height: "100%", width: `${100 * value / Math.max(1, total)}%`, background: color }} /></div>
}

/** RPT-01: what the whole plan showed, saved once the final check is complete. */
export function Report() {
  const planId = useApp((s) => s.planId)
  const go = useApp((s) => s.go)
  const toast = useApp((s) => s.toast)
  const [data, setData] = React.useState<ReportData | null>(null)
  React.useEffect(() => {
    if (!planId) { go("plans"); return }
    call<ReportData>("plan.report", { plan_id: planId }).then(setData)
      .catch((e) => { toast("error", e.message); go("plan") })
  }, [planId, go, toast])
  if (!data) return <div className="page"><div className="card"><Loader2 className="spin" /> 正在生成报告…</div></div>
  const { report: r, titles, views } = data
  const weak = r.weakest_points.filter((w) => w.score > 0)
  const checks = (r.questions ?? []).reduce((a, p) => a + p.check_questions, 0)
  const right = (r.questions ?? []).reduce((a, p) => a + p.check_correct, 0)
  const hasQuestions = !!r.questions?.length
  const dailyMax = Math.max(1, ...views.history.flatMap((d) => [d.target_minutes, d.actual_minutes]))
  async function save(method: "plan.report_export" | "plan.report_pdf") {
    try {
      const res = await call<{ saved: boolean; path?: string }>(method, { plan_id: planId })
      if (res.saved) toast("ok", `已保存到 ${res.path}`)
    } catch (e) { toast("error", (e as Error).message) }
  }
  return (
    <div className="page">
      <div className="row between">
        <Button small kind="ghost" onClick={() => go("plan")}><ArrowLeft />返回计划</Button>
        <div className="row"><Button small onClick={() => save("plan.report_export")}><Download />保存 Markdown</Button>
          <Button small onClick={() => save("plan.report_pdf")}><Download />导出 PDF</Button></div>
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
            <div><span>最终检验</span><b>{checks ? `${right}/${checks} 题答对` : hasQuestions ? "尚未进行" : "只学习，不考核"}</b></div>
          </div>
        </div>
      </div>
      {!!r.live_points?.length && <section className="card stack" aria-label="现场讲知识点" style={{ marginTop: 16 }}>
        <b>以下知识点由老师结合资料现场讲解</b>
        <ul style={{ margin: 0, paddingLeft: "1.4em", lineHeight: 1.8 }}>
          {r.live_points.map((point) => <li key={point.point_id}>{point.title}</li>)}
        </ul>
      </section>}
      {hasQuestions && <><div className="section-title"><h2>各模块掌握情况</h2><span>通过最终检验的题数</span></div>
      <div className="card stack" role="img" aria-label="各模块掌握情况柱状图">
        {views.modules.map((m) => <div key={m.module_id} style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ width: "38%" }}>{m.title}</span><Bar value={m.mastered} total={m.total} />
          <b>{m.total ? `${m.mastered}/${m.total}` : "只学不考"}</b>
        </div>)}
      </div></>}
      <div className="section-title"><h2>每日计划与实际时长</h2><span>按当天单独比较，不是累计欠账</span></div>
      <div className="card stack" role="img" aria-label="每天目标和实际分钟数分组柱状图">
        <p className="muted" style={{ margin: 0 }}>上条：目标 · 下条：实际；长度按全部日期的最大值比较。</p>
        {views.history.map((d) => <div key={d.day} style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ width: 95, flexShrink: 0 }}>{d.day}</span>
          <div style={{ flex: 1, display: "grid", gap: 4 }}><Bar value={d.target_minutes} total={dailyMax} /><Bar value={d.actual_minutes} total={dailyMax} color="var(--warn)" /></div>
          <span style={{ width: 100, textAlign: "right" }}>{d.target_minutes} / {d.actual_minutes} 分</span>
        </div>)}
      </div>
      {hasQuestions && <><div className="section-title"><h2>错误分布</h2><span>按判定场景统计，含最终检验通过后的错误</span></div>
      <div className="card stack" role="img" aria-label="随口问、模块检测、最终检验错误次数柱状图">
        {([["随口问", views.errors.casual], ["模块检测", views.errors.module], ["最终检验", views.errors.check]] as [string, number][]).map(([label, value]) =>
          <div key={label} style={{ display: "flex", gap: 12, alignItems: "center" }}><span style={{ width: 90 }}>{label}</span>
            <Bar value={value} total={Math.max(1, views.errors.casual, views.errors.module, views.errors.check)} color="var(--warn)" />
            <b>{value} 次</b></div>)}
      </div></>}
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
      <div className="section-title"><h2>逐点老师评语</h2><span>只依据保存的判定记录</span></div>
      <div className="card stack">
        {r.points.map((p) => <div key={p.point_id}><b>{titles[p.point_id] ?? p.summary}</b>
          <p className="muted" style={{ margin: "4px 0 0" }}>{r.point_comments?.[p.point_id] || "暂无"}</p></div>)}
      </div>
      <div className="section-title"><h2>学习历史明细</h2><span>按当天原始学习日的实际完成统计</span></div>
      <div className="card stack">
        {views.history.map((d) => <div key={d.day} className="row between" style={{ flexWrap: "wrap", gap: 8 }}>
          <b>{d.day}</b><span>目标 {d.target_minutes} 分</span><span>实际 {d.actual_minutes} 分</span>
          <span>比当天目标少 {d.debt_minutes} 分</span><span>比当天目标多 {d.ahead_minutes} 分</span>
        </div>)}
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
