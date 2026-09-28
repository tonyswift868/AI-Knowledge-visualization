import * as React from "react"
import ReactMarkdown, { defaultUrlTransform } from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import rehypeKatex from "rehype-katex"
import "katex/dist/katex.min.css"
import {
  ArrowLeft, BookOpen, CheckCircle2, ChevronRight, CircleAlert, ClipboardCheck, ExternalLink, Loader2, RotateCcw,
  SendHorizontal, SkipForward, Square, Volume2, VolumeX, X,
} from "lucide-react"
import { BridgeFailure, call } from "../bridge"
import { useApp } from "../store"
import { Button } from "../ui"
import { MicButton, useVoiceInput } from "../voice"
import { useTeacherVoice } from "../teacher/useTeacherVoice"
import type { TeacherStageHandle } from "../teacher/TeacherStage"

const TeacherStage = React.lazy(() => import("../teacher/TeacherStage").then((m) => ({ default: m.TeacherStage })))

type Msg = { id: string; role: "user" | "assistant" | "system"; kind: string; content: string }
type Point = { id: string; title: string; minutes: number; taught: boolean; state: string; current: boolean }
type Question = { type: "choice" | "short" | "fix_error"; stem: string; options: string[]; kind: string; number?: number; total?: number }
type Room = {
  plan: { id: string; goal: string; state: string }
  module: { id: string; title: string; number: number; total: number; state: string }
  points: Point[]; step: string; question: Question | null; messages: Msg[]; can_skip: boolean; red: string[]
  today: { today_target: number; today_confirmed: number; today_tentative: number; needs_rest?: boolean; is_late_night?: boolean } | null
}
type Source = { text: string; title: string; url: string | null; section: string[] }

const RESET = "\u0000"
const POINT_TONE: Record<string, string> = { green: "ok", weak: "warn", red: "danger" }
const POINT_LABEL: Record<string, string> = { green: "已掌握", weak: "有瑕疵", red: "没过" }

/** What the teacher is saying while it streams: hide the question block and raw citation aliases. */
function live(text: string) {
  return text.replace(/【随口问】[\s\S]*?(【\/随口问】|$)/g, "").replace(/\[[cC]\d+\]/g, "").trim()
}

/** Plain text for reading aloud: no code, links, formulas or markdown marks. */
function speakable(markdown: string) {
  return markdown.replace(/```[\s\S]*?```/g, "（代码略）").replace(/\$\$[\s\S]*?\$\$/g, "（公式略）")
    .replace(/\[〔\d+〕\]\([^)]*\)/g, "").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/[`*_#>|~]/g, "")
    .replace(/\n{2,}/g, "\n").trim()
}

export function Classroom() {
  const planId = useApp((s) => s.planId)
  const go = useApp((s) => s.go)
  const toast = useApp((s) => s.toast)
  const [room, setRoom] = React.useState<Room | null>(null)
  const [busy, setBusy] = React.useState<string | null>(null)
  const [stream, setStream] = React.useState<string | null>(null)
  const [text, setText] = React.useState("")
  const [source, setSource] = React.useState<Source | null>(null)
  const [reading, setReading] = React.useState(() => localStorage.getItem("mf.classroom.read") !== "off")
  const teacher = React.useRef<TeacherStageHandle>(null)
  const voice = useTeacherVoice(teacher)
  const terms = React.useMemo(() => (room?.points ?? []).map((p) => p.title).slice(0, 60), [room?.points])
  const mic = useVoiceInput({ value: text, onChange: setText, terms, scopeKey: room ? `${planId}:${room.module.id}` : undefined, disabled: !!busy })
  const abort = React.useRef<AbortController | null>(null)
  const scroller = React.useRef<HTMLDivElement>(null)
  const spoken = React.useRef<string | null>(null)

  const act = React.useCallback(async (method: string, params: Record<string, unknown> = {}, label = "老师在想…") => {
    if (!planId) return false
    abort.current = new AbortController()
    setBusy(label)
    setStream("")
    try {
      const r = await call<Room>(method, { plan_id: planId, ...params }, {
        signal: abort.current.signal,
        onStream: (d) => setStream((s) => (d === RESET ? "" : (s ?? "") + d)),
      })
      setRoom(r)
      return true
    } catch (e) {
      const failure = e as BridgeFailure
      if (failure.code !== "CANCELLED") toast("error", failure.message)
      call<Room>("classroom.state", { plan_id: planId }).then(setRoom).catch(() => undefined)
      return false
    } finally {
      setBusy(null); setStream(null); abort.current = null
    }
  }, [planId, toast])

  React.useEffect(() => {
    if (!planId) { go("home"); return }
    call<Room>("classroom.enter", { plan_id: planId }).then((r) => {
      spoken.current = r.messages.at(-1)?.id ?? null   // do not read old messages aloud on entry
      setRoom(r)
    }).catch((e) => { toast("error", e.message); go("home") })
    return () => { void call("classroom.leave", { plan_id: planId }).catch(() => undefined) }
  }, [planId, go, toast])

  React.useLayoutEffect(() => {
    const el = scroller.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" })
  }, [room?.messages.length, stream, busy])

  // React to what is new since the last look: nod or think on a verdict, read the teacher's words aloud.
  React.useEffect(() => {
    const newest = room?.messages.at(-1)?.id
    if (!room || !newest || newest === spoken.current) return
    const fresh = room.messages.slice(room.messages.findIndex((m) => m.id === spoken.current) + 1)
    spoken.current = newest
    const verdict = fresh.find((m) => m.kind === "verdict")
    if (verdict) void (verdict.content.startsWith("✓") ? teacher.current?.nod() : teacher.current?.think())
    const words = fresh.filter((m) => m.role === "assistant")
    if (reading && voice.availability?.ready && words.length) void voice.speak(words.map((m) => speakable(m.content)).join("\n"))
  }, [room, reading, voice])

  function toggleReading() {
    const next = !reading
    setReading(next)
    try { localStorage.setItem("mf.classroom.read", next ? "on" : "off") } catch { /* optional */ }
    if (!next) void voice.stop()
  }

  async function send(value = text) {
    const t = value.trim()
    if (!t || busy) return
    void voice.stop()
    setText("")
    if (!(await act("classroom.reply", { text: t }, "老师在看你的回答…"))) setText(t)
  }

  async function openSource(chunk: string) {
    try { setSource(await call<Source>("classroom.source", { plan_id: planId, chunk_id: chunk })) }
    catch (e) { toast("error", (e as Error).message) }
  }

  if (!room) return <div className="page"><div className="card"><Loader2 className="spin" /> 正在进入课堂…</div></div>
  const q = room.question
  const done = room.points.filter((p) => p.taught).length
  const t = room.today
  const markdown = (content: string) => (
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}
      urlTransform={(url) => (url.startsWith("mf-source:") ? url : defaultUrlTransform(url))}
      components={{
        a: ({ href, children }) => href?.startsWith("mf-source:")
          ? <button className="cite" title="看原文" onClick={() => openSource(href.slice(10))}>{children}</button>
          : <a href={href} target="_blank" rel="noreferrer">{children}</a>,
      }}>{content}</ReactMarkdown>
  )

  return (
    <div className="class-page">
      <section className="class-main">
        <header className="class-head">
          <Button small kind="ghost" onClick={() => go("home")}><ArrowLeft />下课</Button>
          <div className="class-title">
            <span className="eyebrow">模块 {room.module.number}/{room.module.total} · {room.plan.goal}</span>
            <b>{room.module.title}</b>
          </div>
          {t && <span className="pill" title="今天已确认 / 今天的任务（暂记的时长通过模块检测后转正）">
            今天 {t.today_confirmed}{t.today_tentative ? `+${t.today_tentative}` : ""} / {t.today_target} 分</span>}
        </header>
        <div className="class-points">
          {room.points.map((p, i) => (
            <span key={p.id} className={`class-point ${p.current && ["teach", "answer"].includes(room.step) ? "current" : ""} ${p.taught ? "taught" : ""} ${POINT_TONE[p.state] ?? ""}`}
                  title={`${p.title} · ${p.minutes} 分钟${POINT_LABEL[p.state] ? " · " + POINT_LABEL[p.state] : ""}`}>
              {POINT_TONE[p.state] === "ok" ? <CheckCircle2 size={13} /> : <i>{i + 1}</i>}{p.title}
            </span>
          ))}
          <span className="muted small">{done}/{room.points.length} 已讲</span>
        </div>
        {t?.is_late_night && <div className="banner">已经很晚了，注意休息。今天学的内容会记在开课那天。</div>}
        <div className="class-scroll" ref={scroller}>
          {room.messages.length === 0 && !busy && (
            <div className="class-empty">
              <BookOpen size={28} />
              <p>这个模块有 {room.points.length} 个知识点。点“开始上课”，老师一个点一个点地讲，讲完随口问一句。</p>
            </div>
          )}
          {room.messages.map((m) => {
            if (m.role === "user") return <div key={m.id} className="bubble user" data-selectable>{m.content}</div>
            if (m.kind === "casual") return <div key={m.id} className="class-ask" data-selectable><span>随口问</span>{m.content}</div>
            if (m.kind === "question") return <div key={m.id} className="class-question" data-selectable>{markdown(m.content)}</div>
            if (m.kind === "verdict") return <div key={m.id} className={`class-verdict ${m.content.startsWith("✓") ? "ok" : "bad"}`}>{m.content}</div>
            if (m.role === "system") return <div key={m.id} className={`class-note ${m.kind === "error" ? "warn" : ""}`}>
              {m.kind === "error" && <CircleAlert size={15} />}{m.content}</div>
            return <div key={m.id} className="bubble bot" data-selectable>{markdown(m.content)}</div>
          })}
          {stream !== null && (live(stream)
            ? <div className="bubble bot streaming">{markdown(live(stream))}</div>
            : busy && <div className="plan-status"><Loader2 className="spin" size={16} />{busy}</div>)}
        </div>

        {!busy && <Actions room={room} act={act} />}
        {q && q.type === "choice" && !busy && (
          <div className="class-options">
            {q.options.map((o, i) => <button key={o} className="choice" onClick={() => send(o)}><b>{"ABCD"[i]}</b>{o}</button>)}
          </div>
        )}
        <div className="composer">
          <textarea className="input" rows={1} value={text} disabled={!!busy}
                    placeholder={q ? (q.type === "choice" ? "点上面的选项，或者打字作答" : "写下你的回答（说不会也可以）") : "有问题随时问老师"}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send() } }} />
          <MicButton voice={mic} />
          {busy
            ? <Button onClick={() => abort.current?.abort()}><Square />停止</Button>
            : <Button kind="primary" onClick={() => send()} disabled={!text.trim()}><SendHorizontal />发送</Button>}
        </div>
      </section>

      <aside className="class-side">
        <div className="class-stage">
          <React.Suspense fallback={<p className="muted">正在准备老师形象…</p>}>
            <TeacherStage ref={teacher} />
          </React.Suspense>
        </div>
        <div className="class-voice">
          <button className="icon-btn" onClick={toggleReading} title={reading ? "关闭朗读" : "打开朗读"}>
            {reading ? <Volume2 /> : <VolumeX />}
          </button>
          <span className="small muted">{reading ? voice.message : "朗读已关闭"}</span>
          {reading && voice.availability && !voice.availability.ready && voice.availability.download && (
            <Button small onClick={() => voice.download(true)}>下载朗读组件</Button>
          )}
          {voice.state === "playing" && <Button small kind="ghost" onClick={() => voice.stop()}><Square />停</Button>}
        </div>
      </aside>

      {source && (
        <div className="class-source fade-in" role="dialog">
          <div className="row between">
            <b>{source.title}{source.section.length ? ` · ${source.section.join(" / ")}` : ""}</b>
            <button className="icon-btn" onClick={() => setSource(null)} aria-label="关闭"><X /></button>
          </div>
          <div className="class-source-text" data-selectable>{source.text}</div>
          {source.url && <a href={source.url} target="_blank" rel="noreferrer" className="small"><ExternalLink size={13} /> 打开原网页</a>}
        </div>
      )}
    </div>
  )
}

function Actions({ room, act }: { room: Room; act: (m: string, p?: Record<string, unknown>, label?: string) => Promise<boolean> }) {
  const step = room.step
  if (step === "teach") return (
    <div className="plan-actions">
      {room.can_skip && <Button onClick={() => act("classroom.test", { kind: "skip_test" }, "正在出题…")}><SkipForward />我会这部分，直接考</Button>}
      <Button kind="primary" onClick={() => act("classroom.next")}><ChevronRight />{room.messages.length ? "继续" : "开始上课"}</Button>
    </div>
  )
  if (step === "test_offer") return (
    <div className="plan-actions">
      <Button kind="primary" onClick={() => act("classroom.test", { kind: "module_test" }, "正在出题…")}><ClipboardCheck />开始模块检测</Button>
    </div>
  )
  if (step === "test_done") return (
    <div className="plan-actions">
      {room.red.length > 0 && <Button kind="primary" onClick={() => act("classroom.test", { kind: "retest" }, "正在出题…")}><RotateCcw />重测没过的 {room.red.length} 个点</Button>}
      <Button kind={room.red.length ? undefined : "primary"} onClick={() => act("classroom.next_module", {}, "正在准备下一个模块…")}><ChevronRight />学下一个模块</Button>
    </div>
  )
  if (step === "finished") return <div className="class-note">全部模块都学完了。最终检验马上就会开放。</div>
  return null
}
