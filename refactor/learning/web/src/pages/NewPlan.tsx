import * as React from "react"
import {
  ArrowDown, ArrowUp, BookOpen, Check, ChevronLeft, ChevronRight, CircleAlert, ExternalLink, FileUp, GitBranch, Loader2,
  RefreshCw, Rocket, Search, SendHorizontal, Sparkles, Trash2, Wand2, X,
} from "lucide-react"
import { BridgeFailure, call, on } from "../bridge"
import { useApp } from "../store"
import { Button, Field, Segmented, TASK_TIME_TIP, Tip } from "../ui"

// ------------------------------------------------------------------ types
type Card = {
  card_id: string; kind: "url" | "file"; url: string | null; title: string; site: string; tier: string; summary: string
  relevance: number | null; credibility: number | null; note: string; status: "new" | "adopted" | "rejected"; chars: number
  quality?: { source_type?: string; community?: boolean; source_group?: string; covered_modules?: string[] }
  user_provided?: boolean
}
type Citation = { chunk_id: string; title: string; url: string | null; locator: string; quote: string }
type MapPoint = { title: string; summary: string; citations: Citation[]; source_general?: boolean }
type OModule = { points?: MapPoint[]; id?: string; key: string; title: string; summary: string; refs: string[]; ref_titles: string[]; prerequisites: string[]; minutes: [number, number] }
type Outline = { stages: { title: string; modules: OModule[] }[]; ranges: { total: [number, number]; daily: [number, number]; cap: number; over_cap: boolean; over_300?: boolean; points?: number; learning?: number; suggested_days?: number | null; suggested_end: string | null }; version: number }
type Msg = { id: string; role: "ai" | "user"; kind: string; text: string; cards?: string[]; version?: number; options?: string[] }
type Coverage = { covered: boolean; blocked?: boolean; missing: { content: string; why: string; points: number }[]; message: string; missing_points?: number }
type FormData = {
  goal: string; final_goal: string; level: string; level_note: string; notes: string; start: string; end: string
  daily_cap: number; weekday_weights: number[]; special_days: { date: string; weight: number }[]; only_mine: boolean
}
export type Draft = {
  plan_id: string; phase: "form" | "questions" | "preview" | "sources" | "material_preview" | "outline" | "generating" | "ready"; form: FormData | null
  name?: string; goal: string; messages: Msg[]; sources: Card[]; outline: Outline | null; generation: Record<string, string>; coverage?: Coverage
  estimate?: { modules: number; input_tokens: [number, number]; output_tokens: [number, number]; amount: { low: string; high: string; currency: string } | null; model: string; blocked?: boolean; points?: number }
  preview?: { summary: string; modules: { title: string; summary: string }[]; version: number }
  recommendations?: { card_id: string; title: string; type: string; url: string; why: string; modules: string[] }[]
  material_structure?: { version: number; missing: string[] }
  busy: string | null
}
type Detail = {
  plan: { id: string; title?: string; goal: string; state: string; window: { start: string; end: string } | null; total_minutes: number }
  stages: { id: string; title: string; modules: { id: string; title: string; summary: string; learning_minutes: number; check_minutes: number; state?: string | null; cloze_heavy?: boolean
    points: { citations?: Citation[]; source_general?: boolean; id: string; title: string; summary: string; minutes: number; state?: string; level?: number; errors?: number; needs_review?: boolean; taught?: boolean }[] }[] }[]
  sources: { id: string; title: string; url: string | null; kind: string }[]
  daily?: { full_day: number; cap: number; ok: boolean; suggested_end: string | null }
}

const STEPS: [Draft["phase"][], string][] = [
  [["form", "questions", "preview"], "说清目标与模块"], [["sources"], "选择并提供资料"], [["material_preview"], "按资料确认结构"], [["outline"], "知识地图"], [["generating"], "保存与准备"], [["ready"], "确认开始"],
]
const LEVELS = [{ value: "zero", label: "零基础" }, { value: "beginner", label: "入门" }, { value: "intermediate", label: "有一定基础" }, { value: "advanced", label: "比较熟练" }]
const WEEK = "一二三四五六日"

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
const hours = (m: number) => (m >= 90 ? `${Math.round(m / 6) / 10} 小时` : `${m} 分钟`)

// ------------------------------------------------------------------ page
export function NewPlan() {
  const toast = useApp((s) => s.toast)
  const go = useApp((s) => s.go)
  const planId = useApp((s) => s.planId)
  const set = useApp((s) => s.set)
  const [draft, setDraft] = React.useState<Draft | null>(null)
  const [status, setStatus] = React.useState<string | null>(null)
  const [pending, setPending] = React.useState(false)
  const [text, setText] = React.useState("")
  const [confirmDiscard, setConfirmDiscard] = React.useState(false)
  const scroller = React.useRef<HTMLDivElement>(null)
  const idRef = React.useRef<string | null>(null)

  const accept = React.useCallback((d: Draft) => { idRef.current = d.plan_id; setDraft(d); setStatus(d.busy) }, [])

  React.useEffect(() => {
    const load = planId ? call<{ draft: Draft }>("plan.get", { plan_id: planId }) : call<{ draft: Draft }>("plan.current")
    load.then((r) => accept(r.draft)).catch((e) => { toast("error", e.message); set({ planId: null }) })
    const offs = [
      on("plan_draft", (d: Draft) => { if (d.plan_id === idRef.current) accept(d) }),
      on("plan_status", (d: { plan_id: string; text: string }) => { if (d.plan_id === idRef.current) setStatus(d.text || null) }),
    ]
    return () => offs.forEach((off) => off())
  }, [planId, accept, toast, set])

  React.useLayoutEffect(() => {
    const el = scroller.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" })
  }, [draft?.messages.length, status])

  async function act(method: string, params: Record<string, unknown> = {}) {
    if (!draft) return
    setPending(true)
    try {
      const r = await call<{ draft?: Draft }>(method, { plan_id: draft.plan_id, ...params })
      if (r?.draft) accept(r.draft)
      return true
    } catch (e) {
      toast("error", (e as BridgeFailure).message)
      return false
    } finally { setPending(false) }
  }

  async function say() {
    const value = text.trim()
    if (!value) return
    setText("")
    if (!(await act("plan.say", { text: value }))) setText(value)
  }

  async function discard() {
    if (!draft) return
    try {
      await call("plan.discard", { plan_id: draft.plan_id })
      toast("ok", "已删除这份计划。")
      set({ planId: null })
      go("plans")
    } catch (e) { toast("error", (e as Error).message) }
  }

  if (!draft) return <div className="page"><div className="card"><Loader2 className="spin" /> 正在打开…</div></div>

  const busy = pending || !!status
  const phaseIndex = STEPS.findIndex(([phases]) => phases.includes(draft.phase))
  const cards = Object.fromEntries(draft.sources.map((c) => [c.card_id, c]))
  const adopted = draft.sources.filter((c) => c.status === "adopted").length
  const lastOutline = [...draft.messages].reverse().find((m) => m.kind === "outline")?.id
  const canType = ["questions", "preview", "sources", "material_preview", "outline", "ready"].includes(draft.phase)
  const placeholder = {
    questions: "说说你的想法；也可以说“就这样吧”，先看大致模块…",
    preview: "大致模块这样可以吗？想加、删或调整什么，继续告诉我…",
    sources: "可贴资料网址或 GitHub 仓库，或要求更多推荐、换一批…",
    material_preview: "按资料调整的结构可以吗？可调整模块，或补充资料…",
    outline: "想怎么改？比如：第二阶段拆细一点、截止日期推迟两周、每天改成 90 分钟",
    ready: "还想调整？比如：删掉第 3 个模块、把实战挪到最后、结束日期推迟一周",
  }[draft.phase as string] ?? ""

  return (
    <div className="page plan-page">
      <header className="plan-head card">
        <div className="row between">
          <div style={{ minWidth: 0 }}>
            <b className="plan-title">{draft.name || draft.form?.goal || "新建计划"}</b>
            <span className="muted small">{draft.goal && draft.goal !== draft.form?.goal ? draft.goal : "和 AI 一起把目标变成每天学得完的路线"}</span>
          </div>
          {draft.phase !== "form" && (
            <Button small kind="ghost" className={confirmDiscard ? "danger" : ""} disabled={busy}
                    onClick={() => (confirmDiscard ? discard() : (setConfirmDiscard(true), setTimeout(() => setConfirmDiscard(false), 3000)))}>
              <Trash2 />{confirmDiscard ? "确认删除这份计划" : "放弃"}
            </Button>
          )}
        </div>
        <div className="stepper">
          {STEPS.map(([, label], i) => (
            <div key={label} className={`step-dot ${i < phaseIndex ? "done" : i === phaseIndex ? "on" : ""}`}>
              <span>{i < phaseIndex ? <Check size={13} /> : i + 1}</span><em>{label}</em>
            </div>
          ))}
        </div>
      </header>

      <section className="card plan-main">
        <div className="chat-scroll" ref={scroller}>
          {draft.messages.map((m) => {
            if (m.kind === "form") return <FormCard key={m.id} draft={draft} text={m.text} busy={busy} onSubmit={(form) => act("plan.submit", { form })} />
            if (m.role === "user") return <div key={m.id} className="bubble user" data-selectable>{m.text}</div>
            if (m.kind === "preview") return <div key={m.id} className="plan-block">
              <div className="bubble bot" data-selectable style={{ whiteSpace: "pre-wrap" }}>{m.text}</div>
              {draft.phase === "preview" && m.version === draft.preview?.version && <div className="plan-actions">
                <Button disabled={busy} onClick={() => setText("我想调整大致模块：")} >我想调整</Button>
                <Button kind="primary" disabled={busy} onClick={() => act("plan.say", { text: "确认并找资料" })}>确认，开始找资料</Button>
              </div>}
            </div>
            if (m.kind === "questions") return <div key={m.id} className="plan-block">
              <div className="bubble bot" data-selectable style={{ whiteSpace: "pre-wrap" }}>{m.text}</div>
              {draft.phase === "questions" && m.id === draft.messages[draft.messages.length - 1]?.id && <div className="plan-actions">
                {m.options?.map((option) => <Button key={option} disabled={busy} onClick={() => act("plan.say", { text: option })}>{option}</Button>)}
                <Button kind="ghost" disabled={busy} onClick={() => act("plan.say", { text: "就这样吧" })}>就这样吧，先看大致模块</Button>
              </div>}
            </div>
            if (m.kind === "recommendations") return <div key={m.id} className="plan-block">
              <div className="bubble bot">{m.text}</div>
              {(draft.recommendations ?? []).filter(c => m.cards?.includes(c.card_id)).map(c => <div className="card" key={c.card_id}>
                <strong>{c.title}</strong><span className="muted small"> · {c.type}</span>
                <p>{c.why}</p><p className="small">大致覆盖：{c.modules.join("、")}</p>
                <div className="plan-actions">
                  <a className="btn ghost" href={c.url} target="_blank" rel="noreferrer"><ExternalLink />打开完整资料</a>
                  <Button kind="primary" disabled={busy} onClick={() => act("plan.say", { text: c.url })}>用这份资料</Button>
                </div>
                <div className="muted small">“用这份资料”直接按网址导入；也可以打开后下载上传。</div>
              </div>)}
            </div>
            if (m.kind === "material_preview") return <div key={m.id} className="plan-block">
              <div className="bubble bot" style={{whiteSpace:"pre-wrap"}} data-selectable>{m.text}</div>
              {draft.phase === "material_preview" && m.version === draft.material_structure?.version && <div className="plan-actions">
                <Button disabled={busy} onClick={() => setText("我想调整资料结构：")}>调整结构</Button>
                {(draft.material_structure?.missing.length ?? 0) > 0 && <>
                  <Button disabled={busy} onClick={() => act("plan.missing", {action:"add"})}>再传一份资料</Button>
                  <Button disabled={busy} onClick={() => act("plan.missing", {action:"delete"})}>删掉没讲到的部分并继续</Button>
                </>}
                <Button kind="primary" disabled={busy} onClick={() => act("plan.missing", {action:"continue"})}>确认结构，就这样继续</Button>
              </div>}
            </div>
            if (m.kind === "sources") return (
              <div key={m.id} className="plan-block fade-in">
                <div className="bubble bot">{m.text}</div>
                <GroupedSources cards={(m.cards ?? []).map(id => cards[id]).filter(Boolean)}
                  locked={busy || !["questions", "sources", "material_preview", "outline"].includes(draft.phase)}
                  onDecide={(id,adopt) => act("plan.decide", {card_id:id,adopt})} />
              </div>
            )
            if (m.kind === "outline") return (
              <div key={m.id} className="plan-block fade-in">
                <div className="bubble bot">{m.text}</div>
                {m.id === lastOutline && draft.outline ? (
                  <OutlineCard outline={draft.outline} />
                ) : <div className="muted small outline-old">第 {m.version} 版轮廓（已被新版替换）</div>}
                {m.id === lastOutline && draft.phase === "outline" && (
                  <div className="plan-actions">
                    <Button disabled={busy} onClick={() => act("plan.more")}><Search />补充资料或更多推荐</Button>
                    <Button kind="primary" disabled={busy} onClick={() => act("plan.estimate")}><Wand2 />确认地图，查看时间预估</Button>
                  </div>
                )}
              </div>
            )
            if (m.kind === "estimate") return <EstimateCard key={m.id} draft={draft} text={m.text} busy={busy} active={draft.phase === "outline"}
                                                            onGo={() => act("plan.generate")} onExtend={(end) => act("plan.extend", { end })} onShrink={() => setText("暂时不学：")} />
            if (m.kind === "progress") return <ProgressCard key={m.id} draft={draft} text={m.text} busy={busy} onRetry={() => act("plan.generate")} />
            if (m.kind === "ready") return <ReadyCard key={m.id} draft={draft} text={m.text} busy={busy} act={act}
                                                      onStarted={() => { toast("ok", "计划已开始！首页会显示今天要学多久。"); set({ planId: null }); go("home") }} />
            return <div key={m.id} className={`bubble bot ${m.kind === "error" ? "warn" : ""}`} data-selectable>
              {m.kind === "error" && <CircleAlert size={16} style={{ verticalAlign: -3, marginRight: 6 }} />}{m.text}</div>
          })}
          {draft.phase === "sources" && !status && (
            <div className="plan-actions fade-in">
              <Button disabled={busy} onClick={() => act("plan.more")}><Search />让 AI 推荐 / 换一批</Button>
              <Button disabled={busy} onClick={() => act("plan.upload")}><FileUp />上传资料</Button>
              <Button kind="primary" disabled={busy || adopted === 0} onClick={() => act("plan.outline")}>
                <GitBranch />{adopted ? `按已提供的 ${adopted} 份资料调整结构` : "先上传资料或贴网址"}
              </Button>
            </div>
          )}
          {status && <div className="plan-status fade-in"><Loader2 className="spin" size={16} />{status}</div>}
        </div>
        <div className="composer">
          <textarea className="input" rows={1} value={text} disabled={!canType} placeholder={canType ? placeholder : draft.phase === "form" ? "先填好上面的表单" : "用上面的按钮继续"}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); if (!busy) say() } }} />
          <Button kind="primary" onClick={say} disabled={!canType || busy || !text.trim()}><SendHorizontal />发送</Button>
        </div>
      </section>
    </div>
  )
}

// ------------------------------------------------------------------ form
function defaultForm(): FormData {
  const today = new Date()
  const end = new Date(today); end.setDate(end.getDate() + 27)
  return { goal: "", final_goal: "", level: "beginner", level_note: "", notes: "", start: iso(today), end: iso(end), daily_cap: 60,
           weekday_weights: [1, 1, 1, 1, 1, 1, 1], special_days: [], only_mine: false }
}

function FormCard({ draft, text, busy, onSubmit }: { draft: Draft; text: string; busy: boolean; onSubmit: (f: FormData) => void }) {
  const [form, setForm] = React.useState<FormData>(() => draft.form ?? defaultForm())
  const editable = draft.phase === "form"
  const patch = (p: Partial<FormData>) => setForm((f) => ({ ...f, ...p }))
  if (!editable) return <div className="bubble bot">{text}</div>
  const cycle = (w: number) => (w === 1 ? 0.5 : w === 0.5 ? 0 : 1)
  return (
    <div className="plan-block fade-in">
      <div className="bubble bot">{text}</div>
      <div className="form-card">
        <div className="grid two">
          <Field label="想学什么" hint="越具体越好，例如“Python 面向对象和异常处理”"><input className="input" value={form.goal} maxLength={300} onChange={(e) => patch({ goal: e.target.value })} /></Field>
          <Field label="最终要达到什么程度" hint="能检验的结果，例如“能独立写一个命令行记账工具”"><input className="input" value={form.final_goal} maxLength={300} onChange={(e) => patch({ final_goal: e.target.value })} /></Field>
        </div>
        <Field label="现在的水平">
          <div className="row wrap">
            <Segmented value={form.level} options={LEVELS} onChange={(level) => patch({ level })} />
            <input className="input grow" style={{ flex: 1, minWidth: 200 }} placeholder="补充说明（可选），例如：学过 C，没写过 Python" value={form.level_note} maxLength={300}
                   onChange={(e) => patch({ level_note: e.target.value })} />
          </div>
        </Field>
        <div className="grid three">
          <Field label="开始日期"><input className="input" type="date" value={form.start} onChange={(e) => patch({ start: e.target.value })} /></Field>
          <Field label="结束日期"><input className="input" type="date" value={form.end} onChange={(e) => patch({ end: e.target.value })} /></Field>
          <Field label="每日任务时长上限（分钟）" tip={TASK_TIME_TIP}>
            <div className="row">
              <input className="input" type="number" min={10} max={720} value={form.daily_cap} style={{ width: 96 }} onChange={(e) => patch({ daily_cap: Number(e.target.value) })} />
              {[30, 60, 90, 120, 300].map((m) => <button key={m} className={`tag-btn ${form.daily_cap === m ? "on" : ""}`} onClick={() => patch({ daily_cap: m })}>{m}</button>)}
            </div>
          </Field>
        </div>
        <Field label="每周哪几天能学" hint="点一下切换：全天 → 半天 → 没空">
          <div className="row wrap">
            {form.weekday_weights.map((w, i) => (
              <button key={i} className={`day-chip w${String(w).replace(".", "")}`}
                      onClick={() => patch({ weekday_weights: form.weekday_weights.map((x, j) => (j === i ? cycle(x) : x)) })}>
                周{WEEK[i]}<small>{w === 1 ? "全天" : w === 0.5 ? "半天" : "没空"}</small>
              </button>
            ))}
          </div>
        </Field>
        <Field label="特殊日期" hint="在日历上点某一天：没空 → 半天 → 恢复正常">
          <MiniCalendar start={form.start} end={form.end} special={form.special_days} weekday={form.weekday_weights}
                        onChange={(special_days) => patch({ special_days })} />
        </Field>
        <Field label="还想告诉 AI 的（可选）"><textarea className="input" rows={2} maxLength={1000} value={form.notes} placeholder="例如：想多做练习；偏好中文资料；已有的资料网址也可以贴在这里"
                                                    onChange={(e) => patch({ notes: e.target.value })} /></Field>
        <div className="row between wrap">
          <label className="row small" style={{ cursor: "pointer" }}>
            <input type="checkbox" checked={form.only_mine} onChange={(e) => patch({ only_mine: e.target.checked })} />
            只用我自己上传的资料，不另外搜索（提交后可以上传）
          </label>
          <Button kind="primary" busy={busy} onClick={() => onSubmit(form)}><Sparkles />提交，开始规划</Button>
        </div>
      </div>
    </div>
  )
}

function MiniCalendar({ start, end, special, weekday, onChange }:
  { start: string; end: string; special: { date: string; weight: number }[]; weekday: number[]; onChange: (s: { date: string; weight: number }[]) => void }) {
  const s = new Date(start + "T00:00"), e = new Date(end + "T00:00")
  const valid = !isNaN(s.getTime()) && !isNaN(e.getTime()) && e >= s
  const [month, setMonth] = React.useState(() => (valid ? new Date(s.getFullYear(), s.getMonth(), 1) : new Date()))
  if (!valid) return <span className="muted small">先填好开始和结束日期。</span>
  const map = Object.fromEntries(special.map((d) => [d.date, d.weight]))
  const first = new Date(month.getFullYear(), month.getMonth(), 1)
  const offset = (first.getDay() + 6) % 7
  const days = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate()
  const cells = [...Array(offset).fill(null), ...Array.from({ length: days }, (_, i) => new Date(month.getFullYear(), month.getMonth(), i + 1))]
  const toggle = (d: Date) => {
    const key = iso(d)
    const current = map[key]
    const next = current === undefined ? 0 : current === 0 ? 0.5 : undefined
    const rest = special.filter((x) => x.date !== key)
    onChange(next === undefined ? rest : [...rest, { date: key, weight: next }].sort((a, b) => a.date.localeCompare(b.date)))
  }
  const canPrev = first > new Date(s.getFullYear(), s.getMonth(), 1)
  const canNext = new Date(month.getFullYear(), month.getMonth() + 1, 1) <= e
  return (
    <div className="mini-cal">
      <div className="row between">
        <button className="icon-btn" disabled={!canPrev} onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}><ChevronLeft /></button>
        <b>{month.getFullYear()} 年 {month.getMonth() + 1} 月</b>
        <button className="icon-btn" disabled={!canNext} onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}><ChevronRight /></button>
      </div>
      <div className="cal-grid">
        {[...WEEK].map((w) => <span key={w} className="cal-head">{w}</span>)}
        {cells.map((d, i) => {
          if (!d) return <span key={i} />
          const inRange = d >= s && d <= e
          const w = map[iso(d)] ?? weekday[(d.getDay() + 6) % 7]
          return <button key={i} disabled={!inRange} onClick={() => toggle(d)}
                         className={`cal-day ${inRange ? `w${String(w).replace(".", "")}` : "out"} ${map[iso(d)] !== undefined ? "special" : ""}`}>{d.getDate()}</button>
        })}
      </div>
      <div className="row small muted" style={{ gap: 14 }}><span><i className="dot-leg w1" />全天</span><span><i className="dot-leg w05" />半天</span><span><i className="dot-leg w0" />没空</span></div>
    </div>
  )
}

// ------------------------------------------------------------------ sources
function Stars({ n, label }: { n: number | null; label: string }) {
  if (n == null) return null
  return <span className="stars" title={`${label} ${n}/5`}>{label}<i>{"●".repeat(n)}<em>{"●".repeat(5 - n)}</em></i></span>
}

function SourceCard({ card, locked, onDecide }: { card: Card; locked: boolean; onDecide: (adopt: boolean) => void }) {
  const general = !card.user_provided && card.kind !== "file" && card.credibility === 3
  const tier = card.kind === "file" ? ["上传", "file"] : (card.credibility ?? 0) >= 5 ? ["权威来源", "ok"] : (card.credibility ?? 0) >= 4 ? ["成熟平台", "ok"] : ["一般网站", "muted"]
  return (
    <div className={`source-card ${card.status}`}>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div style={{ minWidth: 0 }}>
          <b className="source-title" title={card.title}>{card.kind === "file" ? <BookOpen size={15} /> : null}{card.title}</b>
          <span className="muted small">{card.site}{card.chars ? ` · 约 ${Math.round(card.chars / 1000)} 千字` : ""}</span>
        </div>
        <span className={`pill ${tier[1]}`}>{tier[0]}</span>
      </div>
      <p className="source-note">来源类型：{card.kind === "file" ? "上传文档" : ({ official_docs: "官方文档", academic: "大学与学术资料", education: "教材与教育平台", encyclopedia: "百科", standards: "标准", government: "官方机构", reference: "参考资料", official_repository: "官方代码仓库" } as Record<string, string>)[card.quality?.source_type ?? ""] ?? "网页资料"} · 用于讲解；带答案的题目在准备时核实。</p>
      <p className="source-note">{card.quality?.covered_modules?.length ? `覆盖：${card.quality.covered_modules.join("、")}` : card.note || "由你提供，正文已读取；请确认适合本次学习。"}</p>
      {general && <p className="source-note" role="note">一般网站，可能有错，请自行判断；不默认采用。</p>}
      <div className="row between wrap">
        <div className="row small" style={{ gap: 12 }}>{!card.user_provided && <><Stars n={card.relevance} label="相关" /><Stars n={card.credibility} label="可信" /></>}</div>
        <div className="row" style={{ gap: 6 }}>
          {card.url && <a className="btn small ghost" href={card.url} target="_blank" rel="noreferrer"><ExternalLink />打开</a>}
          <Button small kind={card.status === "rejected" ? "danger" : "ghost"} disabled={locked} onClick={() => onDecide(false)}><X />不要</Button>
          <label className="row small"><input type="checkbox" checked={card.status === "adopted"} disabled={locked} onChange={(e) => onDecide(e.target.checked)} />认可并采用</label>
        </div>
      </div>
    </div>
  )
}

export function GroupedSources({cards,locked,onDecide}: {cards:Card[];locked:boolean;onDecide:(id:string,adopt:boolean)=>void}) {
  const own=cards.filter(c=>c.user_provided)
  const ordered=cards.filter(c=>!c.user_provided).sort((a,b)=>(b.credibility??0)-(a.credibility??0) || (b.relevance??0)-(a.relevance??0)
    || Number(!!(a.quality?.community || a.quality?.source_type==="community"))-Number(!!(b.quality?.community || b.quality?.source_type==="community")))
  const groups=[{name:"你提供的资料",cards:own},{name:"权威（官方/教材）",cards:ordered.filter(c=>(c.credibility??0)>=5)},
    {name:"成熟平台",cards:ordered.filter(c=>c.credibility===4)},
    {name:"一般网站",cards:ordered.filter(c=>(c.credibility??0)<4)}]
  return <>{groups.filter(g=>g.cards.length).map(g=><section key={g.name} aria-label={g.name}>
    <h3 className="small muted">{g.name}</h3><div className="source-grid">{g.cards.map(c=><SourceCard key={c.card_id} card={c} locked={locked} onDecide={adopt=>onDecide(c.card_id,adopt)} />)}</div>
  </section>)}</>
}

// ------------------------------------------------------------------ outline
function OutlineCard({ outline }: { outline: Outline }) {
  const r = outline.ranges
  const all = outline.stages.flatMap((s) => s.modules)
  const titleOf = Object.fromEntries(all.map((m) => [m.key, m.title]))
  let n = 0
  return (
    <div className="outline-card">
      <div className="outline-sum">
        <div><span>预计总时长</span><b>{hours(r.total[0])} – {hours(r.total[1])}</b></div>
        <div><span>每日任务时长<Tip text={TASK_TIME_TIP} /></span><b>{r.daily[0]} – {r.daily[1]} 分钟</b></div>
        <div><span>模块</span><b>{all.length} 个 · {outline.stages.length} 个阶段</b></div>
      </div>
      {r.over_cap && <div className="outline-warn"><CircleAlert size={16} />每日任务时长可能超过你设的上限（{r.cap} 分钟）。{r.suggested_end ? `可以考虑把结束日期延后到 ${r.suggested_end}，` : ""}或者让我精简路线。</div>}
      <div className="outline-tree">
        {outline.stages.map((stage, si) => (
          <div key={si} className="stage">
            <div className="stage-label"><span>阶段 {si + 1}</span><b>{stage.title}</b></div>
            <div className="stage-modules">
              {stage.modules.map((m) => {
                n += 1
                return (
                  <div key={m.key} className="module-node" style={{ animationDelay: `${n * 0.04}s` }}>
                    <div className="row between"><b><em>{n}</em>{m.title}</b><span className="pill muted">{m.minutes[0]}–{m.minutes[1]} 分钟</span></div>
                    <p>{m.summary}</p>
                    <ol className="point-list">{m.points?.map((p, i) => <li key={i}><b>{p.title}</b><span>{p.summary}</span>{p.source_general && <small className="muted">来源一般</small>}<PointCitations citations={p.citations} /></li>)}</ol>
                    {m.prerequisites.length > 0 && <span className="small muted">先学：{m.prerequisites.map((p) => titleOf[p]).join("、")}</span>}
                    <span className="small refs" title={m.ref_titles.join("\n")}><BookOpen size={12} />依据：{m.ref_titles.slice(0, 2).join("；")}{m.ref_titles.length > 2 ? ` 等 ${m.ref_titles.length} 处` : ""}</span>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>
      <p className="muted small" style={{ margin: "10px 2px 0" }}>每个点均已核验原文出处。想增删、合并或放弃哪部分，直接在下面告诉我。</p>
    </div>
  )
}

// ------------------------------------------------------------------ estimate / progress / ready
function EstimateCard({ draft, text, busy, active, onGo, onExtend, onShrink }:
  { draft: Draft; text: string; busy: boolean; active: boolean; onGo: () => void; onExtend: (end: string) => void; onShrink: () => void }) {
  const r = draft.outline?.ranges
  if (!draft.estimate || !r) return null
  const blocked = !!r.over_300
  return <div className="plan-block fade-in">
    <div className="bubble bot">{text}</div>
    <div className="estimate-card">
      <div className="outline-sum">
        <div><span>知识点 × 平均 15 分钟</span><b>{r.points} 个 · 学习 {hours(r.learning ?? 0)}</b></div>
        <div><span>含模块检测与最终检验</span><b>约 {hours(r.total[1])}</b></div>
        <div><span>每天 / 你的时间上限</span><b>{r.daily[1]} / {r.cap} 分钟</b></div>
      </div>
      <p className="sub">这是预估；检测仍计入总时间。备题采用现成带答案题，找不到时从原文挖空。</p>
      {r.over_cap && <p className="outline-warn">每天超过了你填写的时间上限，可以延长日期或缩小范围。</p>}
      {blocked && <p className="outline-warn">每天超过 300 分钟，请先延长计划{r.suggested_days ? `到共 ${r.suggested_days} 天` : ""}或缩小范围。</p>}
      {active && <div className="plan-actions">
        {(blocked || r.over_cap) && r.suggested_end && <Button disabled={busy} onClick={() => onExtend(r.suggested_end!)}>延长到 {r.suggested_end}</Button>}
        {(blocked || r.over_cap) && <Button disabled={busy} onClick={onShrink}>缩小范围，在聊天里说明</Button>}
        <Button kind="primary" disabled={busy || blocked} onClick={onGo}><Rocket />确认，保存并准备第 1 模块</Button>
      </div>}
    </div>
  </div>
}

export function PointCitations({ citations }: { citations?: Citation[] }) {
  if (!citations?.length) return null
  return <div className="small refs" style={{ display: "block", overflowWrap: "anywhere" }}>
    {citations.map((c, i) => <details key={i} style={{ marginTop: 6 }}>
      <summary>依据：{c.title} · {c.locator}</summary>
      <blockquote style={{ whiteSpace: "pre-wrap", margin: "8px 0" }}>{c.quote}</blockquote>
      {c.url && <a href={c.url} target="_blank" rel="noreferrer">打开原文 <ExternalLink size={12} /></a>}
    </details>)}
  </div>
}

function ProgressCard({ draft, text, busy, onRetry }: { draft: Draft; text: string; busy: boolean; onRetry: () => void }) {
  const modules = (draft.outline?.stages ?? []).flatMap((s) => s.modules)
  const states = modules.map((m) => draft.generation[m.id ?? ""] ?? "pending")
  const done = states.filter((s) => s === "done").length
  const failed = states.filter((s) => s === "failed").length
  return (
    <div className="plan-block fade-in">
      <div className="bubble bot">{text}</div>
      <div className="progress-card">
        <div className="row between"><b>{done} / {modules.length} 个模块完成</b>{failed > 0 && <span className="pill warn">{failed} 个失败</span>}</div>
        <div className="bar"><i style={{ width: `${modules.length ? (done / modules.length) * 100 : 0}%` }} /></div>
        <div className="gen-list">
          {modules.map((m, i) => (
            <span key={m.key} className={`gen-item ${states[i]}`}>
              {states[i] === "done" ? <Check size={13} /> : states[i] === "running" ? <Loader2 size={13} className="spin" /> : states[i] === "failed" ? <X size={13} /> : <i />}
              {m.title}
            </span>
          ))}
        </div>
        {draft.phase === "generating" && !busy && (failed > 0 || done < modules.length) &&
          <div className="plan-actions"><Button kind="primary" onClick={onRetry}><RefreshCw />{failed > 0 ? "重试失败的模块" : "继续生成"}</Button></div>}
      </div>
    </div>
  )
}

const MASTERY: Record<string, [string, string]> = { green: ["已掌握", "ok"], weak: ["有瑕疵", "warn"], red: ["没过", "bad"], untested: ["未检测", "none"] }
const MODULE_STATE: Record<string, [string, string]> = { passed: ["已通过", "ok"], skipped: ["直接考通过", "ok"], failed: ["有点没过", "warn"] }

export function PlanTree({ detail, editable, busy, onDelete, onMove }:
  { detail: Detail; editable?: boolean; busy?: boolean; onDelete?: (id: string) => void; onMove?: (id: string, d: number) => void }) {
  const [open, setOpen] = React.useState<string | null>(null)
  const [confirm, setConfirm] = React.useState<string | null>(null)
  let n = 0
  return (
    <div className="outline-tree">
      {detail.stages.map((stage, si) => (
        <div key={stage.id} className="stage">
          <div className="stage-label"><span>阶段 {si + 1}</span><b>{stage.title}</b></div>
          <div className="stage-modules">
            {stage.modules.map((m) => {
              n += 1
              return (
                <div key={m.id} className={`module-node ${open === m.id ? "open" : ""}`}>
                  <div className="row between">
                    <button className="module-toggle" onClick={() => setOpen(open === m.id ? null : m.id)}>
                      <b><em>{n}</em>{m.title}</b>
                    </button>
                    <div className="row" style={{ gap: 6 }}>
                      {m.state && MODULE_STATE[m.state] && <span className={`pill ${MODULE_STATE[m.state][1]}`}>{MODULE_STATE[m.state][0]}</span>}
                      <span className="pill muted">{m.points.length} 个点 · {m.learning_minutes + m.check_minutes} 分钟</span>
                      {editable && <>
                        <button className="icon-btn sm" disabled={busy} title="上移" onClick={() => onMove?.(m.id, -1)}><ArrowUp /></button>
                        <button className="icon-btn sm" disabled={busy} title="下移" onClick={() => onMove?.(m.id, 1)}><ArrowDown /></button>
                        <button className={`icon-btn sm ${confirm === m.id ? "danger" : ""}`} disabled={busy} title={confirm === m.id ? "再点一次删除" : "删除模块"}
                                onClick={() => (confirm === m.id ? onDelete?.(m.id) : (setConfirm(m.id), setTimeout(() => setConfirm(null), 3000)))}><Trash2 /></button>
                      </>}
                    </div>
                  </div>
                  <p>{m.summary}</p>
                  {m.cloze_heavy && <small className="muted">这个模块有较多知识点用的是原文填空题</small>}
                  {open === m.id && (
                    <ol className="point-list fade-in">
                      {m.points.map((p) => <li key={p.id} className={p.state ? `mastery-${MASTERY[p.state]?.[1] ?? "none"}` : ""}>
                        <b>{p.state && <i className="mastery-dot" title={MASTERY[p.state]?.[0]} />}{p.title}</b><span>{p.summary}</span>
                        <em>{p.minutes} 分钟{p.state && MASTERY[p.state] ? ` · ${MASTERY[p.state][0]}` : ""}{p.errors ? ` · 错过 ${p.errors} 次` : ""}{p.needs_review ? " · 需要复习" : ""}</em>{p.source_general && <small className="muted">来源一般</small>}<PointCitations citations={p.citations} /></li>)}
                    </ol>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}

function ReadyCard({ draft, text, busy, act, onStarted }:
  { draft: Draft; text: string; busy: boolean; act: (m: string, p?: Record<string, unknown>) => Promise<boolean | undefined>; onStarted: () => void }) {
  const [detail, setDetail] = React.useState<Detail | null>(null)
  const toast = useApp((s) => s.toast)
  const reload = React.useCallback(() => {
    call<Detail>("plan.detail", { plan_id: draft.plan_id }).then(setDetail).catch((e) => toast("error", e.message))
  }, [draft.plan_id, toast])
  React.useEffect(() => { reload() }, [reload, draft])
  if (draft.phase !== "ready") return <div className="bubble bot">{text}</div>
  const d = detail?.daily
  return (
    <div className="plan-block fade-in">
      <div className="bubble bot">{text}</div>
      {detail && (
        <div className="outline-card">
          <div className="outline-sum">
            <div><span>总时长（含检验）</span><b>{hours(detail.plan.total_minutes)}</b></div>
            <div><span>每日任务时长<Tip text={TASK_TIME_TIP} /></span><b>{d ? `${d.full_day} 分钟` : "—"}</b></div>
            <div><span>日期</span><b>{detail.plan.window ? `${detail.plan.window.start} → ${detail.plan.window.end}` : "—"}</b></div>
          </div>
          {d && !d.ok && (
            <div className="outline-warn"><CircleAlert size={16} />每日任务时长 {d.full_day} 分钟，超过了你设的上限 {d.cap} 分钟。
              {d.suggested_end && <Button small disabled={busy} onClick={() => act("plan.extend", { end: d.suggested_end })}>把结束日期延后到 {d.suggested_end}</Button>}
              <span>也可以删掉一些模块。</span>
            </div>
          )}
          <PlanTree detail={detail} editable busy={busy}
                    onDelete={(id) => act("plan.module", { action: "delete", module_id: id })}
                    onMove={(id, direction) => act("plan.module", { action: "move", module_id: id, direction })} />
          <div className="plan-actions">
            <span className="muted small">开始后只能改日期或添加资料，不能再删模块、调顺序。</span>
            <Button kind="primary" disabled={busy} onClick={async () => { if (await act("plan.start")) onStarted() }}><Rocket />开始计划</Button>
          </div>
        </div>
      )}
    </div>
  )
}
