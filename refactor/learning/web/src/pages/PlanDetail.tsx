import * as React from "react"
import { ArrowLeft, BookOpen, CalendarClock, ExternalLink, FilePlus2, FileText, Loader2, PlayCircle } from "lucide-react"
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
      {detail.plan.state === "active" && <AddMaterialCard detail={detail} onApplied={() =>
        call<Detail>("plan.detail", { plan_id: detail.plan.id }).then(setDetail)} />}
      <div className="section-title"><h2>路线</h2><span>点模块名展开知识点；绿色已掌握、黄色有瑕疵、红色没过</span></div>
      <div className="card"><PlanTree detail={detail} /></div>
      {(detail.plan.state === "active" || detail.plan.state === "checking" || detail.plan.state === "completed") &&
        <PracticeCard detail={detail} onOpen={() => go("classroom")} />}
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

function PracticeCard({ detail, onOpen }: { detail: Detail; onOpen: () => void }) {
  const toast = useApp((s) => s.toast)
  const [moduleId, setModuleId] = React.useState("")
  const [pointId, setPointId] = React.useState("")
  const [count, setCount] = React.useState(5)
  const [busy, setBusy] = React.useState(false)
  const modules = detail.stages.flatMap((s) => s.modules).filter((m) =>
    detail.plan.state === "completed" || m.state !== "untested" || m.points.some((p) => p.taught))
  const points = modules.filter((m) => !moduleId || m.id === moduleId).flatMap((m) => m.points)
  async function start() {
    setBusy(true)
    try {
      await call("classroom.practice", { plan_id: detail.plan.id, module_id: moduleId || null,
        point_id: pointId || null, count })
      onOpen()
    } catch (e) { toast("error", (e as Error).message) } finally { setBusy(false) }
  }
  if (!modules.length) return null
  return <div className="card stack" style={{ marginTop: 12 }}>
    <b>自由检验</b><span className="muted small">练习记录与计划、报告分开保存；答错的点会标记“需要复习”。</span>
    <div className="row wrap" style={{ gap: 10 }}>
      <label className="field">范围<select className="input" value={moduleId} onChange={(e) => { setModuleId(e.target.value); setPointId("") }}>
        <option value="">全部已学模块随机</option>{modules.map((m) => <option key={m.id} value={m.id}>{m.title}</option>)}
      </select></label>
      <label className="field">知识点<select className="input" value={pointId} onChange={(e) => setPointId(e.target.value)}>
        <option value="">随机挑题</option>{points.map((p) => <option key={p.id} value={p.id}>{p.title}{p.needs_review ? " · 需要复习" : ""}</option>)}
      </select></label>
      <label className="field">题数<input className="input" type="number" min={1} max={50} value={count}
        onChange={(e) => setCount(Math.max(1, Math.min(50, Math.round(Number(e.target.value) || 1))))} /></label>
      <Button kind="primary" busy={busy} onClick={start}>开始自由检验</Button>
    </div>
  </div>
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


type AddModule = { key: string; title: string; summary: string; minutes: [number, number]; kind: string; insert_after: string | null }
type Timing = { extra_daily_minutes: number; exceeds_cap: boolean; suggested_end: string | null; daily_minutes: number }
type AddPreview = {
  modules: AddModule[]; supplements: { point_id: string; reason: string }[]; overlaps: unknown[]; ignored: unknown[]
  selected: string[]; new_end: string; added_minutes: [number, number]; timing_low: Timing; timing_high: Timing
  daily_cap_minutes: number; exact: boolean; confirmation_id?: string
}

/** PLAN-10: add material to a started plan; only the new part is analysed, generated and scheduled. */
function AddMaterialCard({ detail, onApplied }: { detail: Detail; onApplied: () => void }) {
  const toast = useApp((s) => s.toast)
  const [names, setNames] = React.useState<string[]>([])
  const [links, setLinks] = React.useState("")
  const [sourceQuality, setSourceQuality] = React.useState<{source_id: string; title: string; credibility: number; relevance: number; note: string}[]>([])
  const [open, setOpen] = React.useState(false)
  const [token, setToken] = React.useState<string | null>(null)
  const [addition, setAddition] = React.useState<string | null>(null)
  const [first, setFirst] = React.useState<AddPreview | null>(null)
  const [view, setView] = React.useState<AddPreview | null>(null)
  const [selected, setSelected] = React.useState<string[]>([])
  const [end, setEnd] = React.useState("")
  const [busy, setBusy] = React.useState<string | null>(null)
  const titles: Record<string, string> = {}
  detail.stages.forEach((s) => s.modules.forEach((m) => { titles[m.id] = m.title }))
  first?.modules.forEach((m) => { titles[m.key] = m.title })

  function reset() { setNames([]); setLinks(""); setSourceQuality([]); setToken(null); setAddition(null); setFirst(null); setView(null); setSelected([]); setEnd("") }
  async function cancelAddition() {
    if (addition) {
      try { await call("plan.add_cancel", { addition_id: addition }) }
      catch (e) { toast("error", (e as Error).message); return }
    }
    reset(); setOpen(false)
  }
  async function step<T>(label: string, fn: () => Promise<T>) {
    setBusy(label)
    try { return await fn() } catch (e) { toast("error", (e as Error).message); return null } finally { setBusy(null) }
  }
  async function pick() {
    const r = await step("", () => call<{ token: string | null; names: string[] }>("plan.add_pick", { plan_id: detail.plan.id }))
    if (r?.token) { setToken(r.token); setNames(r.names) }   // keeps any pasted links
  }
  async function analyze() {
    const urls = links.split(/\s+/).filter((u) => /^https?:\/\//.test(u))
    const r = await step("正在读取资料并分析和现有路线的关系（通常半分钟到一分钟）…",
      () => call<{ addition_id: string; preview: AddPreview; failed: string[]; source_quality?: typeof sourceQuality }>("plan.add_analyze", { plan_id: detail.plan.id, token, urls }))
    if (!r) return
    if (r.failed?.length) toast("error", "有网址没能打开：" + r.failed.join("；"))
    setSourceQuality(r.source_quality ?? [])
    setToken(null); setAddition(r.addition_id); setFirst(r.preview); setView(r.preview)
    setSelected(r.preview.selected ?? r.preview.modules.map((m) => m.key)); setEnd(r.preview.new_end)
  }
  async function repreview(keys: string[], newEnd: string) {
    setSelected(keys); setEnd(newEnd)
    const r = await step("", () => call<AddPreview>("plan.add_preview", { addition_id: addition, selected: keys, new_end: newEnd }))
    if (r) setView(r)
  }
  async function generate() {
    const r = await step("正在为新增的内容生成知识点…",
      () => call<AddPreview>("plan.add_generate", { addition_id: addition, selected, new_end: end }))
    if (r) setView(r)
  }
  async function apply() {
    const r = await step("正在加入计划…", () => call("plan.add_apply", { addition_id: addition, confirmation_id: view?.confirmation_id }))
    if (r) { toast("ok", "已加入计划。已学的内容和记录都没变，只重新安排了还没学的部分。"); reset(); setOpen(false); onApplied() }
  }

  if (!open && !first) return (
    <div className="card row between" style={{ marginTop: 12 }}>
      <span className="row"><FilePlus2 size={16} />想补充资料？只分析新增的部分，已学的内容不动。</span>
      <Button small onClick={() => setOpen(true)}>加资料</Button>
    </div>
  )
  const t = view?.timing_high
  return (
    <div className="card stack" style={{ marginTop: 12 }}>
      <b>加资料{names.length ? `：${names.join("、")}` : ""}</b>
      {!first && !busy && <>
        <div className="row" style={{ gap: 8 }}>
          <Button small onClick={pick}><FilePlus2 />选择文件</Button>
          <span className="muted small">或者贴网址（网页、GitHub 仓库都可以，一行一个）：</span>
        </div>
        <textarea className="input" rows={2} value={links} onChange={(e) => setLinks(e.target.value)}
                  placeholder="https://docs.python.org/zh-cn/3/tutorial/…" />
      </>}
      {busy && <div className="plan-status"><Loader2 className="spin" size={16} />{busy}</div>}
      {!first && !busy && <div className="plan-actions">
        <Button small kind="ghost" onClick={cancelAddition}>取消</Button>
        <Button small kind="primary" disabled={!token && !/https?:\/\//.test(links)} onClick={analyze}>开始分析</Button>
      </div>}
      {view && <>
        {sourceQuality.map((s) => <div className="stack small" key={s.source_id} style={{gap: 4}}>
          <b>{s.title} · 可信 {s.credibility}/5 · 相关 {s.relevance}/5</b>
          <span className="muted">{s.note}</span>
        </div>)}
        {view.supplements.length > 0 && <span className="muted small">有 {view.supplements.length} 处是对已有知识点的补充，会直接挂上，不加时间。</span>}
        {(view.overlaps.length > 0 || view.ignored.length > 0) && <span className="muted small">
          {view.overlaps.length + view.ignored.length} 处和已学内容重合或与目标无关，不加入。</span>}
        {first && first.modules.length > 0 ? <>
          <span className="small">建议新增的模块（可以取消勾选）：</span>
          {first.modules.map((m) => (
            <label key={m.key} className="row" style={{ alignItems: "flex-start", gap: 10 }}>
              <input type="checkbox" checked={selected.includes(m.key)} disabled={!!busy || view.exact}
                     onChange={(e) => repreview(e.target.checked ? [...selected, m.key] : selected.filter((k) => k !== m.key), end)} />
              <span className="stack" style={{ gap: 2 }}>
                <b>{m.title}{m.kind === "supplementary" && <span className="pill warn" style={{ marginLeft: 6 }}>补充前置</span>}</b>
                <span className="muted small">{m.summary}</span>
                <span className="muted small">插在《{m.insert_after ? titles[m.insert_after] ?? "当前进度" : "未学部分最前面"}》之后 · 约 {m.minutes[0]}–{m.minutes[1]} 分钟</span>
              </span>
            </label>
          ))}
        </> : <span className="muted small">没有需要新增的模块。</span>}
        {selected.length > 0 && t && <div className="stack" style={{ gap: 6 }}>
          <span className="small">预计多学 {view.added_minutes[0] === view.added_minutes[1] ? view.added_minutes[0] : `${view.added_minutes[0]}–${view.added_minutes[1]}`} 分钟；
            截止日为 {end} 时每天多约 {t.extra_daily_minutes} 分钟{t.exceeds_cap ? `，会超过每天上限 ${view.daily_cap_minutes} 分钟` : ""}。</span>
          {t.exceeds_cap && t.suggested_end && <Button small disabled={!!busy || view.exact} onClick={() => repreview(selected, t.suggested_end!)}>
            把截止日推迟到 {t.suggested_end}</Button>}
          <label className="field small">截止日期<input className="input" type="date" value={end} disabled={!!busy || view.exact}
            onChange={(e) => e.target.value && repreview(selected, e.target.value)} /></label>
        </div>}
        <div className="plan-actions">
          <Button small kind="ghost" onClick={cancelAddition} disabled={!!busy}>取消</Button>
          {!view.exact
            ? <Button small kind="primary" disabled={!!busy} onClick={generate}>{selected.length ? "生成新增内容" : "确认补充"}</Button>
            : <Button small kind="primary" disabled={!!busy} onClick={apply}>确认加入计划</Button>}
        </div>
        {view.exact && <span className="muted small">新增内容已生成，时间按实际知识点算好了。确认后才会写入计划。</span>}
      </>}
    </div>
  )
}
