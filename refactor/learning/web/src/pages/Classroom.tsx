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
import { BridgeFailure, call, on } from "../bridge"
import { useApp } from "../store"
import { Button } from "../ui"
import { MicButton, useVoiceInput } from "../voice"
import { createTeacherVoiceStream, useTeacherVoice } from "../teacher/useTeacherVoice"
import type { TeacherStageHandle } from "../teacher/TeacherStage"
import { LessonActions, LessonProgress, LessonPreparing, type Lesson, type LessonWait } from "../classroom/GuidedLesson"

const TeacherStage = React.lazy(() => import("../teacher/TeacherStage").then((m) => ({ default: m.TeacherStage })))

type Msg = { id: string; role: "user" | "assistant" | "system"; kind: string; content: string }
type Point = { id: string; title: string; minutes: number; taught: boolean; state: string; current: boolean }
type Question = { type: "choice" | "short" | "fix_error"; stem: string; options: string[]; kind: string; number?: number; total?: number }
type Room = {
  plan: { id: string; goal: string; state: string }
  module: { id: string; title: string; number: number; total: number; state: string }
  points: Point[]; step: string; question: Question | null; messages: Msg[]; can_skip: boolean; red: string[]
  more_modules: boolean; check: { passed: number; total: number } | null
  lesson?: Lesson | null
  lesson_wait?: LessonWait | null
  today: { today_target: number; today_confirmed: number; today_tentative: number; needs_rest?: boolean; is_late_night?: boolean } | null
}
type Source = { text: string; title: string; url: string | null; section: string[] }

const RESET = "\u0000"
const VERDICT_EVENT = "\u0001VERDICT:"
const POINT_TONE: Record<string, string> = { green: "ok", weak: "warn", red: "danger" }
const POINT_LABEL: Record<string, string> = { green: "已掌握", weak: "有瑕疵", red: "没过" }

/** What the teacher is saying while it streams: hide the question block and raw citation aliases. */
function live(text: string) {
  return text.replace(/【随口问】[\s\S]*?(【\/随口问】|$)/g, "").replace(/\[[cC]\d+\]/g, "").trim()
}

export function Classroom() {
  const planId = useApp((s) => s.planId)
  const go = useApp((s) => s.go)
  const toast = useApp((s) => s.toast)
  const reduceMotion = useApp((s) => s.settings?.reduce_motion ?? false)
  const [room, setRoom] = React.useState<Room | null>(null)
  const [busy, setBusy] = React.useState<string | null>(null)
  const [stream, setStream] = React.useState<string | null>(null)
  const [verdictPreview, setVerdictPreview] = React.useState<{ text: string; correct: boolean } | null>(null)
  const [text, setText] = React.useState("")
  const [source, setSource] = React.useState<Source | null>(null)
  const [selected, setSelected] = React.useState<number | null>(null)
  const [reading, setReading] = React.useState(() => { try { return localStorage.getItem("mf.classroom.read") === "on" } catch { return false } })
  const teacher = React.useRef<TeacherStageHandle>(null)
  const voice = useTeacherVoice(teacher)
  const terms = React.useMemo(() => (room?.points ?? []).map((p) => p.title).slice(0, 60), [room?.points])
  const mic = useVoiceInput({ value: text, onChange: setText, terms, scopeKey: room ? `${planId}:${room.module.id}` : undefined, disabled: !!busy })
  const abort = React.useRef<AbortController | null>(null)
  const acting = React.useRef(false)
  const lessonRefresh = React.useRef<(() => void) | null>(null)
  const lessonChanged = React.useRef(false)
  const scroller = React.useRef<HTMLDivElement>(null)
  const spoken = React.useRef<string | null>(null)
  const streamedMessages = React.useRef(new Set<string>())
  const voiceTurn = React.useRef<{ reader: ReturnType<typeof createTeacherVoiceStream> | null; streamed: boolean; cancelled: boolean } | null>(null)
  const stopReading = React.useCallback(() => {
    const turn = voiceTurn.current
    if (turn) { turn.cancelled = true; if (turn.reader) { void turn.reader.stop(); return } }
    void voice.stop()
  }, [voice.stop])

  const act = React.useCallback(async (method: string, params: Record<string, unknown> = {}, label = "老师在想…") => {
    // A ref locks immediately, including two clicks before React has rendered.
    if (!planId || acting.current) return false
    acting.current = true
    setSelected(method === "classroom.choose" ? Number(params.option) :
      method === "classroom.reply" && room?.question?.type === "choice" ? room.question.options.indexOf(String(params.text)) : null)
    const before = new Set(room?.messages.map((message) => message.id) ?? [])
    stopReading()
    const turn = { reader: reading && voice.availability?.ready ? createTeacherVoiceStream(voice) : null,
      streamed: false, cancelled: false }
    voiceTurn.current = turn
    abort.current = new AbortController()
    setBusy(label)
    setStream("")
    setVerdictPreview(null)
    try {
      const r = await call<Room>(method, { plan_id: planId, ...params }, {
        signal: abort.current.signal,
        onStream: (d) => {
          if (d.startsWith(VERDICT_EVENT)) {
            try {
              const result = JSON.parse(d.slice(VERDICT_EVENT.length)) as { text: string; correct: boolean }
              setVerdictPreview(result)
              void (result.correct ? teacher.current?.nod() : teacher.current?.think())
            } catch { /* the final room snapshot still contains the verdict */ }
            return
          }
          setStream((s) => (d === RESET ? "" : (s ?? "") + d))
          if (turn.reader && !turn.cancelled) { turn.reader.append(d); if (d !== RESET) turn.streamed = true }
        },
      })
      if (turn.streamed || turn.cancelled) {
        const streamed = r.messages.find((m) => !before.has(m.id) && m.role === "assistant")?.id
        streamedMessages.current = new Set(r.messages.filter((m) => before.has(m.id) || turn.cancelled || m.id === streamed).map((m) => m.id))
      }
      if (!turn.cancelled) void turn.reader?.end()
      setRoom(r)
      return true
    } catch (e) {
      // A failed/cancelled attempt must not finish reading its queued tail.
      turn.cancelled = true
      void turn.reader?.stop()
      const failure = e as BridgeFailure
      if (failure.code !== "CANCELLED") toast("error", failure.message)
      await call<Room>("classroom.state", { plan_id: planId }).then((r) => {
        // Refresh visible state without restarting audio from the stopped attempt.
        streamedMessages.current = new Set(r.messages.map((m) => m.id))
        setRoom(r)
      }).catch(() => undefined)
      return false
    } finally {
      acting.current = false
      setBusy(null); setStream(null); setVerdictPreview(null); setSelected(null); abort.current = null
      if (lessonChanged.current) lessonRefresh.current?.()
    }
  }, [planId, room, toast, reading, voice.availability?.ready, voice.startStream, voice.appendStream, voice.endStream, voice.stop, stopReading])

  React.useEffect(() => {
    if (!planId) { go("home"); return }
    call<Room>("classroom.enter", { plan_id: planId }).then((r) => {
      spoken.current = r.messages.at(-1)?.id ?? null   // do not read old messages aloud on entry
      setRoom(r)
    }).catch((e) => { toast("error", e.message); go("home") })
    return () => { stopReading(); void call("classroom.leave", { plan_id: planId }).catch(() => undefined) }
  }, [planId, go, toast, stopReading])

  React.useEffect(() => {
    let active = true
    let pending = false
    const refresh = () => {
      if (!active || !planId) return
      lessonChanged.current = true
      if (acting.current || pending) return
      pending = true
      lessonChanged.current = false
      call<Room>("classroom.state", { plan_id: planId }).then((r) => {
        if (active && !acting.current) setRoom(r)
        else if (active) lessonChanged.current = true
      }).catch(() => { if (active) lessonChanged.current = true }).finally(() => { pending = false })
    }
    lessonRefresh.current = refresh
    const unsubscribe = on("classroom.lesson_ready", (data) => {
      if (data?.plan_id === planId) refresh()
    })
    return () => { active = false; unsubscribe(); lessonRefresh.current = null; lessonChanged.current = false }
  }, [planId])

  React.useEffect(() => {
    if (!['preparing_questions', 'preparing_lesson'].includes(room?.step ?? '') || busy) return
    let active = true
    let pending = false
    const timer = window.setInterval(() => {
      if (pending) return
      if (acting.current) return
      pending = true
      call<Room>('classroom.state', { plan_id: planId }).then((r) => {
        if (active) setRoom(r)
      }).catch(() => undefined).finally(() => { pending = false })
    }, 500)
    return () => { active = false; window.clearInterval(timer) }
  }, [room?.step, planId, busy])

  React.useLayoutEffect(() => {
    const el = scroller.current
    const reduced = reduceMotion || window.matchMedia("(prefers-reduced-motion: reduce)").matches
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: reduced ? "auto" : "smooth" })
  }, [room?.messages.length, stream, busy, reduceMotion])

  React.useEffect(() => {
    if (room?.lesson?.awaiting !== "continue") return
    const next = (event: KeyboardEvent) => {
      const target = event.target instanceof HTMLElement ? event.target : null
      if (event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey ||
          event.isComposing || event.repeat || target?.closest("textarea, input, button, a, select, [contenteditable='true'], [role='dialog']")) return
      event.preventDefault()
      void act("classroom.next", {}, "正在接着讲…")
    }
    document.addEventListener("keydown", next)
    return () => document.removeEventListener("keydown", next)
  }, [room?.lesson?.awaiting, room?.question, act])

  // React to what is new since the last look: nod or think on a verdict, read the teacher's words aloud.
  React.useEffect(() => {
    const newest = room?.messages.at(-1)?.id
    if (!room || !newest || newest === spoken.current) return
    const fresh = room.messages.slice(room.messages.findIndex((m) => m.id === spoken.current) + 1)
    spoken.current = newest
    const verdict = fresh.find((m) => m.kind === "verdict")
    if (verdict) void (verdict.content.startsWith("✓") ? teacher.current?.nod() : teacher.current?.think())
    const words = fresh.filter((m) => m.role === "assistant" && !streamedMessages.current.has(m.id))
    // Non-streamed results (e.g. a fixed question) still read once. Both paths
    // pass raw Markdown to the shared Python text preparation.
    if (reading && voice.availability?.ready && words.length) void voice.speak(words.map((m) => m.content).join("\n"))
  }, [room, reading, voice])

  // Text is the default; a single message can be read on demand (VOICE-01).
  function listen(content: string) {
    if (voice.availability?.ready) { void voice.stop().then(() => voice.speak(content)); return }
    toast("info", "朗读组件还没下载。需要听的话，先点下方的“下载朗读组件”。")
  }

  function toggleReading() {
    const next = !reading
    setReading(next)
    try { localStorage.setItem("mf.classroom.read", next ? "on" : "off") } catch { /* optional */ }
    if (!next) stopReading()
  }

  async function send(value?: string) {
    const t = (value ?? text).trim()
    if (!t || acting.current || room?.step === 'preparing_questions') return
    stopReading()
    if (value === undefined) setText("")
    if (!(await act("classroom.reply", { text: t }, "老师在看你的回答…")) && value === undefined) {
      setText((draft) => draft || t)
    }
  }

  async function openSource(chunk: string) {
    try { setSource(await call<Source>("classroom.source", { plan_id: planId, chunk_id: chunk })) }
    catch (e) { toast("error", (e as Error).message) }
  }

  if (!room) return <div className="page"><div className="card"><Loader2 className="spin" /> 正在进入课堂…</div></div>
  // An active lesson interaction is authoritative; a saved casual question may
  // still be in the snapshot while an interjection waits for "回到刚才".
  const q = room.lesson?.awaiting ? null : room.question
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
            <span className="eyebrow">{room.check ? "全部模块已学完" : `模块 ${room.module.number}/${room.module.total}`} · {room.plan.goal}</span>
            <b>{room.check ? "最终检验" : room.module.title}</b>
          </div>
          {t && <span className="pill" title="今天已确认 / 今天的任务（暂记的时长通过模块检测后转正）">
            今天 {t.today_confirmed}{t.today_tentative ? `+${t.today_tentative}` : ""} / {t.today_target} 分</span>}
        </header>
        {room.check ? (
          <div className="class-points">
            <div className="class-check-bar"><i style={{ width: `${(room.check.passed / Math.max(1, room.check.total)) * 100}%` }} /></div>
            <span className="muted small">已通过 {room.check.passed}/{room.check.total} 个知识点</span>
          </div>
        ) : <div className="class-points">
          {room.points.map((p, i) => (
            <span key={p.id} className={`class-point ${p.current && ["teach", "lesson", "answer", "preparing_lesson"].includes(room.step) ? "current" : ""} ${p.taught ? "taught" : ""} ${POINT_TONE[p.state] ?? ""}`}
                  title={`${p.title} · ${p.minutes} 分钟${POINT_LABEL[p.state] ? " · " + POINT_LABEL[p.state] : ""}`}>
              {POINT_TONE[p.state] === "ok" ? <CheckCircle2 size={13} /> : <i>{i + 1}</i>}{p.title}
            </span>
          ))}
          <span className="muted small">{done}/{room.points.length} 已讲</span>
        </div>}
        {room.lesson && <LessonProgress lesson={room.lesson} />}
        {room.lesson_wait && <LessonPreparing wait={room.lesson_wait} busy={!!busy} act={act} />}
        {t?.is_late_night && <div className="banner">已经很晚了，注意休息。今天学的内容会记在开课那天。</div>}
        <div className="class-scroll" ref={scroller}>
          {room.messages.length === 0 && !busy && room.step === "teach" && (
            <div className="class-empty">
              <BookOpen size={28} />
              <p>这个模块有 {room.points.length} 个知识点。点“开始上课”，老师一个点一个点地讲，讲完随口问一句。</p>
            </div>
          )}
          {room.messages.map((m) => {
            if (m.role === "user") return <div key={m.id} className={`bubble user ${m.kind === "choice" ? "class-choice" : ""}`} data-selectable>
              {m.kind === "choice" && <span className="class-message-label">我的选择</span>}{m.content}</div>
            if (m.kind === "casual") return <div key={m.id} className="class-ask" data-selectable><span>随口问</span>{m.content}</div>
            if (m.kind === "question") return <div key={m.id} className="class-question" data-selectable>{markdown(m.content)}</div>
            if (m.kind === "verdict") return <div key={m.id} className={`class-verdict ${m.content.startsWith("✓") ? "ok" : "bad"}`}>{m.content}</div>
            if (m.role === "system") return <div key={m.id} className={`class-note ${m.kind === "error" ? "warn" : ""}`}>
              {m.kind === "error" && <CircleAlert size={15} />}{m.content}</div>
            return <div key={m.id} className={`bubble bot ${m.kind === "step" ? "class-step" : m.kind === "step_reply" ? "class-step-reply" : ""}`} data-selectable>
              {m.kind === "step" && <span className="class-message-label">老师讲解</span>}
              {m.kind === "step_reply" && <span className="class-message-label">老师回应</span>}{markdown(m.content)}
              <button className="icon-btn listen" title="听这段" onClick={() => listen(m.content)}><Volume2 size={15} /></button></div>
          })}
          {verdictPreview && <div className={`class-verdict ${verdictPreview.correct ? "ok" : "bad"}`}>{verdictPreview.text}</div>}
          {stream !== null && (live(stream)
            ? <div className="bubble bot streaming">{markdown(live(stream))}</div>
            : busy && <div className="plan-status"><Loader2 className="spin" size={16} />{busy}</div>)}
        </div>

        <Actions room={room} act={act} busy={!!busy} />
        {room.lesson && !q && <LessonActions lesson={room.lesson} busy={!!busy} selected={selected} act={act} />}
        {q && q.type === "choice" && (
          <div className="class-options" aria-label={q.kind === "casual" ? "随口问选项" : "检测题选项"}>
            {q.options.map((o, i) => <button key={`${i}:${o}`} className={`choice ${selected === i ? "on" : ""}`} disabled={!!busy}
              aria-pressed={selected === i} onClick={() => send(o)}><b>{String.fromCharCode(65 + i)}</b><span>{o}</span></button>)}
          </div>
        )}
        <div className="composer">
          <textarea className="input" rows={1} value={text} aria-label="课堂输入"
                    placeholder={q ? (q.type === "choice" ? "点上面的选项，或者打字作答" : "写下你的回答（说不会也可以）") : "有问题随时问老师"}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && !e.repeat) {
                      e.preventDefault()
                      if (!text.trim() && room.lesson?.awaiting === "continue" && !q) void act("classroom.next", {}, "正在接着讲…")
                      else void send()
                    } }} />
          <MicButton voice={mic} />
          {busy
            ? <Button onClick={() => { stopReading(); abort.current?.abort() }}><Square />停止</Button>
            : <Button kind="primary" onClick={() => send()} disabled={!text.trim() || room.step === 'preparing_questions'}><SendHorizontal />发送</Button>}
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
          {voice.availability && !voice.availability.ready && voice.availability.download && (
            <Button small disabled={voice.downloadState?.state === "downloading"} onClick={() => voice.download()}>下载朗读组件</Button>
          )}
          {voice.downloadState?.state === "downloading" && <>
            <span className="small muted">下载 {Math.round(100 * voice.downloadState.received_bytes / Math.max(1, voice.downloadState.total_bytes))}%</span>
            <Button small kind="ghost" onClick={() => voice.cancelDownload()}>取消下载</Button>
          </>}
          {voice.error && <span className="small" role="alert">{voice.error}</span>}
          {voice.downloadState?.state === "error" && <span className="small" role="alert">{voice.downloadState.message}</span>}
          {voice.busy && <Button small kind="ghost" onClick={stopReading}><Square />停</Button>}
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

function Actions({ room, act, busy }: { room: Room; busy: boolean; act: (m: string, p?: Record<string, unknown>, label?: string) => Promise<boolean> }) {
  const step = room.step
  if (step === 'preparing_questions') return <div className="class-note"><Loader2 className="spin" size={15} />正在准备题目，备好后会自动开始。</div>
  if (step === "teach" && room.lesson && !room.can_skip) return null
  if (step === "teach") return (
    <div className="plan-actions">
      {room.can_skip && <Button disabled={busy} onClick={() => act("classroom.test", { kind: "skip_test" }, "正在出题…")}><SkipForward />我会这部分，直接考</Button>}
      {!room.lesson && <Button kind="primary" disabled={busy} onClick={() => act("classroom.next")}><ChevronRight />{room.messages.length ? "继续" : "开始上课"}</Button>}
    </div>
  )
  if (step === "test_offer") return (
    <div className="plan-actions">
      <Button kind="primary" disabled={busy} onClick={() => act("classroom.test", { kind: "module_test" }, "正在出题…")}><ClipboardCheck />开始模块检测</Button>
    </div>
  )
  if (step === "test_done") return (
    <div className="plan-actions">
      {room.red.length > 0 && <Button kind="primary" disabled={busy} onClick={() => act("classroom.test", { kind: "retest" }, "正在出题…")}><RotateCcw />重测没过的 {room.red.length} 个点</Button>}
      <Button kind={room.red.length ? undefined : "primary"} disabled={busy} onClick={() => act("classroom.next_module", {}, "正在准备…")}>
        <ChevronRight />{room.more_modules ? "学下一个模块" : "结束模块学习"}</Button>
    </div>
  )
  if (step === "check_offer") return (
    <div className="plan-actions">
      <span className="muted small">最终检验：每个点换角度问，答对升级，全部通过就完成计划。</span>
      <Button kind="primary" disabled={busy} onClick={() => act("classroom.check", {}, "正在出题…")}><ClipboardCheck />开始最终检验</Button>
    </div>
  )
  if (step === "checking" && !room.question) return (
    <div className="plan-actions">
      <Button kind="primary" disabled={busy} onClick={() => act("classroom.check", {}, "正在出题…")}><ClipboardCheck />继续检验</Button>
    </div>
  )
  if (step === "completed") return <div className="class-note"><CheckCircle2 size={15} />这份计划已经完成，可以在「我的计划」里查看学习报告。</div>
  return null
}
