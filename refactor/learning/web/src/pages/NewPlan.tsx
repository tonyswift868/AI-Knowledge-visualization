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
}
type OModule = { id?: string; key: string; title: string; summary: string; refs: string[]; ref_titles: string[]; prerequisites: string[]; minutes: [number, number] }
type Outline = { stages: { title: string; modules: OModule[] }[]; ranges: { total: [number, number]; daily: [number, number]; cap: number; over_cap: boolean; suggested_end: string | null }; version: number }
type Msg = { id: string; role: "ai" | "user"; kind: string; text: string; cards?: string[]; version?: number }
type FormData = {
  goal: string; final_goal: string; level: string; level_note: string; notes: string; start: string; end: string
  daily_cap: number; weekday_weights: number[]; special_days: { date: string; weight: number }[]; only_mine: boolean
}
export type Draft = {
  plan_id: string; phase: "form" | "questions" | "sources" | "outline" | "generating" | "ready"; form: FormData | null
  goal: string; messages: Msg[]; sources: Card[]; outline: Outline | null; generation: Record<string, string>
  estimate?: { modules: number; input_tokens: [number, number]; output_tokens: [number, number]; amount: { low: string; high: string; currency: string } | null; model: string }
  busy: string | null
}
type Detail = {
  plan: { id: string; goal: string; state: string; window: { start: string; end: string } | null; total_minutes: number }
  stages: { id: string; title: string; modules: { id: string; title: string; summary: string; learning_minutes: number; check_minutes: number; points: { id: string; title: string; summary: string; minutes: number }[] }[] }[]
  sources: { id: string; title: string; url: string | null; kind: string }[]
  daily?: { full_day: number; cap: number; ok: boolean; suggested_end: string | null }
}

const STEPS: [Draft["phase"][], string][] = [
  [["form", "questions"], "说清目标"], [["sources"], "找资料"], [["outline"], "路线轮廓"], [["generating"], "正式生成"], [["ready"], "确认开始"],
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
  const canType = ["questions", "sources", "outline", "ready"].includes(draft.phase)
  const placeholder = {
    questions: "回答上面的问题，也可以直接说要改的设置…",
    sources: "想要什么样的资料？比如：要中文的、只学前四章、采用第二份；也可以直接贴资料网址",
    outline: "想怎么改？比如：第二阶段拆细一点、截止日期推迟两周、每天改成 90 分钟",
    ready: "还想调整？比如：删掉第 3 个模块、把实战挪到最后、结束日期推迟一周",
  }[draft.phase as string] ?? ""

  return (
    <div className="page plan-page">
      <header className="plan-head card">
        <div className="row between">
          <div style={{ minWidth: 0 }}>
            <b className="plan-title">{draft.form?.goal || "新建计划"}</b>
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
            if (m.kind === "sources") return (
              <div key={m.id} className="plan-block fade-in">
                <div className="bubble bot">{m.text}</div>
                <div className="source-grid">
                  {(m.cards ?? []).map((id) => cards[id] && (
                    <SourceCard key={id} card={cards[id]} locked={busy || !["questions", "sources", "outline"].includes(draft.phase)}
                                onDecide={(adopt) => act("plan.decide", { card_id: id, adopt })} />
                  ))}
                </div>
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
                    <Button disabled={busy} onClick={() => act("plan.more")}><Search />资料不够，再找找</Button>
                    <Button kind="primary" disabled={busy} onClick={() => act("plan.estimate")}><Wand2 />满意了，准备正式生成</Button>
                  </div>
                )}
              </div>
            )
            if (m.kind === "estimate") return <EstimateCard key={m.id} draft={draft} text={m.text} busy={busy} active={draft.phase === "outline"}
                                                            onGo={() => act("plan.generate")} />
            if (m.kind === "progress") return <ProgressCard key={m.id} draft={draft} text={m.text} busy={busy} onRetry={() => act("plan.generate")} />
            if (m.kind === "ready") return <ReadyCard key={m.id} draft={draft} text={m.text} busy={busy} act={act}
                                                      onStarted={() => { toast("ok", "计划已开始！首页会显示今天要学多久。"); set({ planId: null }); go("home") }} />
            return <div key={m.id} className={`bubble bot ${m.kind === "error" ? "warn" : ""}`} data-selectable>
              {m.kind === "error" && <CircleAlert size={16} style={{ verticalAlign: -3, marginRight: 6 }} />}{m.text}</div>
          })}
          {draft.phase === "sources" && !status && (
            <div className="plan-actions fade-in">
              <Button disabled={busy} onClick={() => act("plan.more")}><Search />再找找</Button>
              <Button disabled={busy} onClick={() => act("plan.upload")}><FileUp />上传资料</Button>
              <Button kind="primary" disabled={busy || adopted === 0} onClick={() => act("plan.outline")}>
                <GitBranch />{adopted ? `用已采用的 ${adopted} 份资料生成路线轮廓` : "先采用至少一份资料"}
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
  const tier = card.kind === "file" ? ["上传", "file"] : card.tier === "whitelist" ? ["权威来源", "ok"] : ["一般来源", "muted"]
  return (
    <div className={`source-card ${card.status}`}>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div style={{ minWidth: 0 }}>
          <b className="source-title">{card.kind === "file" ? <BookOpen size={15} /> : null}{card.title}</b>
          <span className="muted small">{card.site}{card.chars ? ` · 约 ${Math.round(card.chars / 1000)} 千字` : ""}</span>
        </div>
        <span className={`pill ${tier[1]}`}>{tier[0]}</span>
      </div>
      {card.note && <p className="source-note">{card.note}</p>}
      <div className="row between wrap">
        <div className="row small" style={{ gap: 12 }}><Stars n={card.relevance} label="相关" /><Stars n={card.credibility} label="可信" /></div>
        <div className="row" style={{ gap: 6 }}>
          {card.url && <a className="btn small ghost" href={card.url} target="_blank" rel="noreferrer"><ExternalLink />打开</a>}
          <Button small kind={card.status === "rejected" ? "danger" : "ghost"} disabled={locked} onClick={() => onDecide(false)}><X />不要</Button>
          <Button small kind={card.status === "adopted" ? "primary" : undefined} disabled={locked} onClick={() => onDecide(true)}><Check />{card.status === "adopted" ? "已采用" : "采用"}</Button>
        </div>
      </div>
    </div>
  )
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
                    {m.prerequisites.length > 0 && <span className="small muted">先学：{m.prerequisites.map((p) => titleOf[p]).join("、")}</span>}
                    <span className="small refs" title={m.ref_titles.join("\n")}><BookOpen size={12} />依据：{m.ref_titles.slice(0, 2).join("；")}{m.ref_titles.length > 2 ? ` 等 ${m.ref_titles.length} 处` : ""}</span>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>
      <p className="muted small" style={{ margin: "10px 2px 0" }}>轮廓只到模块，具体知识点在正式生成时从资料里提炼。想改哪里，直接在下面告诉我。</p>
    </div>
  )
}

// ------------------------------------------------------------------ estimate / progress / ready
function EstimateCard({ draft, text, busy, active, onGo }: { draft: Draft; text: string; busy: boolean; active: boolean; onGo: () => void }) {
  const e = draft.estimate
  if (!e) return null
  const k = (n: number) => `${Math.round(n / 1000)}k`
  return (
    <div className="plan-block fade-in">
      <div className="bubble bot">{text}</div>
      <div className="estimate-card">
        <div className="outline-sum">
          <div><span>要生成的模块</span><b>{e.modules} 个</b></div>
          <div><span>预计用量</span><b>输入 {k(e.input_tokens[0])}–{k(e.input_tokens[1])} · 输出 {k(e.output_tokens[0])}–{k(e.output_tokens[1])} token</b></div>
          <div><span>预计花费</span><b>{e.amount ? `${e.amount.currency === "CNY" ? "¥" : e.amount.currency + " "}${e.amount.low} – ${e.amount.high}` : "当前模型没有价格表，无法估算金额"}</b></div>
        </div>
        {active && <div className="plan-actions"><Button kind="primary" disabled={busy} onClick={onGo}><Rocket />开始生成</Button></div>}
      </div>
    </div>
  )
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
                  {open === m.id && (
                    <ol className="point-list fade-in">
                      {m.points.map((p) => <li key={p.id}><b>{p.title}</b><span>{p.summary}</span><em>{p.minutes} 分钟</em></li>)}
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
