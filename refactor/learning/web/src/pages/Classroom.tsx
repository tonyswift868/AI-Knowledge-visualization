import { SkipControl127 } from "./SkipControl127"
import { WrongAnswers } from "../classroom/WrongAnswers"
import * as React from "react"
import { useRoundScroll } from "../useRoundScroll"
import { ClassroomMarkdown } from "../classroom/Markdown"
import { focusSpeech } from "../classroom/spokenFocus"
import { RecapCard, type RecapContent } from "../classroom/RecapCard"
import { ProgressStatus, type StudyProgress } from "../ProgressStatus"
import {
  ArrowLeft, BookOpen, CheckCircle2, ChevronRight, CircleAlert, ClipboardCheck, ExternalLink, RotateCcw,
  SendHorizontal, SkipForward, Square, Volume2, VolumeX, X,
} from "lucide-react"
import { BridgeFailure, call, on } from "../bridge"
import { useApp } from "../store"
import { Button } from "../ui"
import { MicButton, useVoiceInput } from "../voice"
import { createTeacherVoiceStream, useTeacherVoice } from "../teacher/useTeacherVoice"
import type { TeacherStageHandle } from "../teacher/TeacherStage"
import { MaterialToolbar, PreparationBadge, PreparationWait, ConversationActions, OriginalCards, QuestionInputs, type Concept, type NavigationEntry, type OriginalCardsHandle, type BankQuestion, type Conversation, type Preparation, type SourceCard } from "../classroom/Conversation"
import { RecallQuestion, type RecallRequest } from "../classroom/RecallQuestion"
import { Loading } from '../loading/Loading'

const TeacherWindow = React.lazy(() => import("../teacher/TeacherStage").then((m) => ({ default: m.TeacherWindow })))

type Msg = { id: string; role: "user" | "assistant" | "system"; kind: string; content: string; sources: SourceCard[]; recap?: RecapContent }
type Point = { id: string; title: string; minutes: number; taught: boolean; state: string; current: boolean }
type ReviewEntry = { point_id: string; title: string; number: number; question: string; answer?: string; answered: boolean; sources: SourceCard[] }

type Room = {
  plan: { id: string; goal: string; state: string }
  module: { id: string; title: string; number: number; total: number; state: string; navigation?: NavigationEntry[] }
  points: Point[]; step: string; question: BankQuestion | null; messages: Msg[]; can_skip: boolean; red: string[]
  more_modules: boolean; check: { passed: number; total: number } | null
  conversation?: Conversation | null
  teaching_version?: string | null
  concept?: Concept | null
  direct_review?: ReviewEntry & { total: number; entries?: ReviewEntry[] } | null
  preparation?: Preparation | null
  failure?: { message: string } | null
  explanation?: { text: string; source: { title: string; url: string; locator: string }; label: string; open: boolean; message_id: string } | null
  today: (StudyProgress & { needs_rest?: boolean; is_late_night?: boolean }) | null
}
type Source = { text: string; title: string; url: string | null; section: string[] }

const RESET = "\u0000"
const VERDICT_EVENT = "\u0001VERDICT:"
const MATERIAL_EVENT = "\u0001MATERIAL:"
const TODAY_EVENT = "\u0001TODAY:"
const POINT_TONE: Record<string, string> = { green: "ok", weak: "warn", red: "danger" }
const POINT_LABEL: Record<string, string> = { green: "已掌握", weak: "有瑕疵", red: "没过" }

/** What the teacher is saying while it streams: hide the question block and raw citation aliases. */
function live(text: string) {
  return text.replace(/【选项】[\s\S]*?(【\/选项】|$)/g, "").replace(/\[[cC]\d+\]/g, "").trim()
}

export function Classroom() {
  const planId = useApp((s) => s.planId)
  const [wrongAnswers, setWrongAnswers] = React.useState(false)
  const go = useApp((s) => s.go)
  const toast = useApp((s) => s.toast)
  const reduceMotion = useApp((s) => s.settings?.reduce_motion ?? false)
  const [room, setRoom] = React.useState<Room | null>(null)
  const [busy, setBusy] = React.useState<string | null>(null)
  const [stream, setStream] = React.useState<string | null>(null)
  const [pendingUser, setPendingUser] = React.useState<string | null>(null)
  const [verdictPreview, setVerdictPreview] = React.useState<{ text: string; correct: boolean } | null>(null)
  const [text, setText] = React.useState("")
  const [source, setSource] = React.useState<Source | null>(null)
  const [askMode, setAskMode] = React.useState(false)
  const [pointsExpanded, setPointsExpanded] = React.useState(false)
  const [classroomFocus, setClassroomFocus] = React.useState(() => { try { return localStorage.getItem('mf.classroom.focus') === 'on' } catch { return false } })
  const streamMessageKey = React.useRef('teacher-stream')
  const [skipModule, setSkipModule] = React.useState(false)
  const [skipConfirm, setSkipConfirm] = React.useState(false)
  const cancelledPrefetch = React.useRef<string | null>(null)
  const [recallAttempt, setRecallAttempt] = React.useState(0)
  const input = React.useRef<HTMLTextAreaElement>(null)
  const generation = React.useRef(0)
  const [reading, setReading] = React.useState(() => { try { return localStorage.getItem("mf.classroom.read") === "on" } catch { return false } })
  const teacher = React.useRef<TeacherStageHandle>(null)
  const voice = useTeacherVoice(teacher, { autoWarm: reading })
  const terms = React.useMemo(() => (room?.points ?? []).map((p) => p.title).slice(0, 60), [room?.points])
  const mic = useVoiceInput({ value: text, onChange: value => {cancelPrefetch();setText(value)}, terms,
    context: room && planId ? {plan_id:planId,module_id:room.module.id,point_id:room.points.find(p=>p.current)?.id} : undefined,
    scopeKey: room ? `${planId}:${room.module.id}` : undefined, disabled: !!busy })
  const abort = React.useRef<AbortController | null>(null)
  const acting = React.useRef(false)
  const lessonRefresh = React.useRef<(() => void) | null>(null)
  const lessonChanged = React.useRef(false)
  const scroller = React.useRef<HTMLDivElement>(null)
  const beginRound = useRoundScroll(scroller, planId)
  // Toggling “只看重点” resizes every turn above; keep the turn at the top of the view in place.
  const focusAnchor = React.useRef<{ key: string, offset: number } | null>(null)
  const toggleFocus = () => {
    const el = scroller.current
    if (el) {
      const top = el.getBoundingClientRect().top
      const turn = Array.from(el.querySelectorAll<HTMLElement>('[data-turn-key]')).find(e => e.getBoundingClientRect().bottom > top + 1)
      focusAnchor.current = turn ? { key: turn.dataset.turnKey!, offset: Math.max(0, turn.getBoundingClientRect().top - top) } : null
    }
    const next = !classroomFocus; setClassroomFocus(next)
    try { localStorage.setItem('mf.classroom.focus', next ? 'on' : 'off') } catch { /* the current page still switches */ }
  }
  React.useLayoutEffect(() => {
    const anchor = focusAnchor.current, el = scroller.current
    focusAnchor.current = null
    const turn = anchor && el && Array.from(el.querySelectorAll<HTMLElement>('[data-turn-key]')).find(e => e.dataset.turnKey === anchor.key)
    if (turn && el) el.scrollTop += turn.getBoundingClientRect().top - el.getBoundingClientRect().top - anchor!.offset
  }, [classroomFocus])
  const sourceCards = React.useRef(new Map<string, OriginalCardsHandle>())
  const spoken = React.useRef<string | null>(null)
  const streamedMessages = React.useRef(new Set<string>())
  const voiceTurn = React.useRef<{ reader: ReturnType<typeof createTeacherVoiceStream> | null; streamed: boolean; cancelled: boolean; pendingReset: boolean; raw: string; spoken: string } | null>(null)
  const stopReading = React.useCallback(() => {
    const turn = voiceTurn.current
    if (turn) { turn.cancelled = true; if (turn.reader) { void turn.reader.stop(); return } }
    void voice.stop()
  }, [voice.stop])

  React.useEffect(() => { if (askMode) input.current?.focus() }, [askMode])
  React.useEffect(() => { setAskMode(false) }, [room?.question?.id, room?.module.id])
  React.useEffect(() => { setPointsExpanded(false) }, [room?.module.id])
  const currentPoint = room?.points.find(p=>p.current)?.id
  React.useEffect(() => { setSkipModule(false); setSkipConfirm(false) }, [planId, currentPoint])
  React.useEffect(() => {
    if (!planId || !room?.teaching_version) return
    const version = room.teaching_version
    cancelledPrefetch.current = null
    void call('classroom.prefetch', { plan_id: planId, version }).catch(()=>undefined)
    return () => { void call('classroom.cancel_prefetch', {plan_id:planId,version}).catch(()=>undefined) }
  }, [planId,room?.teaching_version])
  const cancelPrefetch = () => {
    const version=room?.teaching_version
    if (!version || cancelledPrefetch.current===version) return
    cancelledPrefetch.current=version
    void call('classroom.cancel_prefetch', {plan_id:planId,version}).catch(()=>undefined)
  }

  const act = React.useCallback(async (method: string, params: Record<string, unknown> = {}, label = "老师在想…") => {
    // A ref locks immediately, including two clicks before React has rendered.
    if (!planId || acting.current) return false
    acting.current = true
    if (method !== "classroom.state") beginRound()
    const ownGeneration = generation.current
    const requestId = crypto.randomUUID()
    streamMessageKey.current = `teacher-stream:${requestId}`
    const before = new Set(room?.messages.map((message) => message.id) ?? [])
    stopReading()
    const turn = { reader: reading && voice.availability?.ready ? createTeacherVoiceStream(voice) : null,
      streamed: false, cancelled: false, pendingReset: false, raw: '', spoken: '' }
    voiceTurn.current = turn
    abort.current = new AbortController()
    setBusy(label)
    setPendingUser(!room?.direct_review && ["classroom.reply", "classroom.ask", "classroom.choose"].includes(method)
      ? String(params.text ?? room?.conversation?.options?.[Number(params.option)] ?? "") || null : null)
    setStream("")
    setVerdictPreview(null)
    try {
      const r = await call<Room>(method, { plan_id: planId, request_id: requestId, ...params }, {
        signal: abort.current.signal,
        onStream: (d) => {
          if (ownGeneration !== generation.current) return
          if (d.startsWith(TODAY_EVENT)) {
            try { const today = JSON.parse(d.slice(TODAY_EVENT.length)) as Room["today"]; setRoom(previous => previous ? { ...previous, today } : previous) } catch { /* final snapshot remains authoritative */ }
            return
          }
          if (d.startsWith(MATERIAL_EVENT)) {
            try { const p = JSON.parse(d.slice(MATERIAL_EVENT.length)) as Preparation; setBusy(`${p.text} ${p.done}/${p.total}`); setRoom(previous => previous ? { ...previous, preparation: p } : previous) } catch { /* keep the current progress */ }
            return
          }
          if (d.startsWith(VERDICT_EVENT)) {
            try {
              const result = JSON.parse(d.slice(VERDICT_EVENT.length)) as { text: string; correct: boolean }
              setVerdictPreview(result)
              void (result.correct ? teacher.current?.nod() : teacher.current?.think())
            } catch { /* the final room snapshot still contains the verdict */ }
            return
          }
          setStream((s) => (d === RESET ? "" : (s ?? "") + d))
          if (turn.reader && !turn.cancelled) {
            // A parsed citation replacement changes display markup, not words:
            // do not stop and read the same spoken reply from the beginning.
            if (d === RESET) turn.pendingReset = true
            else {
              const sameWords = turn.pendingReset && live(d) === live(turn.raw)
              if (turn.pendingReset && !sameWords) { turn.reader.append(RESET); turn.spoken = '' }
              if (turn.pendingReset) turn.raw = ''
              turn.pendingReset = false; turn.raw += d
              const cleaned = focusSpeech(turn.raw)
              if (!sameWords) turn.reader.append(cleaned.slice(turn.spoken.length))
              turn.spoken = cleaned
              turn.streamed = true
            }
          }
        },
      })
      if (ownGeneration !== generation.current) { turn.cancelled = true; void turn.reader?.stop(); return false }
      if (turn.streamed || turn.cancelled) {
        const streamed = r.messages.find((m) => !before.has(m.id) && m.role === "assistant")?.id
        streamedMessages.current = new Set(r.messages.filter((m) => before.has(m.id) || turn.cancelled || m.id === streamed).map((m) => m.id))
      }
      if (!turn.cancelled) {
        if (turn.pendingReset) { turn.reader?.append(RESET); turn.spoken = '' }
        if (turn.reader) turn.reader.append(focusSpeech(turn.raw, true).slice(turn.spoken.length))
        void turn.reader?.end()
      }
      setRoom(r)
      return !r.failure
    } catch (e) {
      // A failed/cancelled attempt must not finish reading its queued tail.
      turn.cancelled = true
      void turn.reader?.stop()
      if (ownGeneration !== generation.current) return false
      const failure = e as BridgeFailure
      if (failure.code !== "CANCELLED") toast("error", failure.message)
      await call<Room>("classroom.state", { plan_id: planId }).then((r) => {
        // Refresh visible state without restarting audio from the stopped attempt.
        if (ownGeneration !== generation.current) return
        streamedMessages.current = new Set(r.messages.map((m) => m.id))
        setRoom(r)
      }).catch(() => undefined)
      return false
    } finally {
      if (ownGeneration === generation.current) {
        acting.current = false
        setBusy(null); setStream(null); setPendingUser(null); setVerdictPreview(null); abort.current = null
      }
      if (lessonChanged.current) lessonRefresh.current?.()
    }
  }, [planId, room, toast, reading, voice.availability?.ready, voice.startStream, voice.appendStream, voice.endStream, voice.stop, stopReading])

  const recallRequest: RecallRequest = async <T,>(method: string, params: Record<string, unknown>) => {
    if (!planId || acting.current) throw new Error("请等当前操作完成。")
    acting.current = true
    const ownGeneration = generation.current
    stopReading()
    setBusy(method === "class.recall_reveal" ? "正在保存回答…" : "正在提交要点…")
    try {
      const result = await call<T>(method, { plan_id: planId, ...params })
      if (ownGeneration !== generation.current) throw new Error("课堂已经切换。")
      return result
    } finally {
      if (ownGeneration === generation.current) { acting.current = false; setBusy(null) }
    }
  }

  React.useEffect(() => {
    if (!planId) { go("home"); return }
    const ownGeneration = ++generation.current
    setRoom(null); setPendingUser(null); acting.current = false; setAskMode(false); setPointsExpanded(false)
    call<Room>("classroom.enter", { plan_id: planId }).then((r) => {
      if (ownGeneration !== generation.current) return
      spoken.current = r.messages.at(-1)?.id ?? null   // do not read old messages aloud on entry
      setRoom(r)
    }).catch((e) => { if (ownGeneration === generation.current) { toast("error", e.message); go("home") } })
    return () => { ++generation.current; abort.current?.abort(); stopReading(); void call("classroom.leave", { plan_id: planId }).catch(() => undefined) }
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
    const unsubscribe = on("classroom.materials_ready", (data) => {
      if (data?.plan_id === planId) refresh()
    })
    return () => { active = false; unsubscribe(); lessonRefresh.current = null; lessonChanged.current = false }
  }, [planId])

  React.useEffect(() => {
    if (!['preparing_materials'].includes(room?.step ?? '') || busy) return
    let active = true
    let pending = false
    const timer = window.setInterval(() => {
      if (pending) return
      if (acting.current) return
      pending = true
      act('classroom.state', {}, '老师在准备…').finally(() => { pending = false })
    }, 500)
    return () => { active = false; window.clearInterval(timer) }
  }, [room?.step, planId, busy, act])

  // React to what is new since the last look: nod or think on a verdict, read the teacher's words aloud.
  React.useEffect(() => {
    const newest = room?.messages.at(-1)?.id
    if (!room || !newest || newest === spoken.current) return
    const fresh = room.messages.slice(room.messages.findIndex((m) => m.id === spoken.current) + 1)
    spoken.current = newest
    const verdict = fresh.find((m) => m.kind === "verdict")
    if (verdict) void (verdict.content.startsWith("✓") ? teacher.current?.nod() : teacher.current?.think())
    const words = fresh.filter((m) => m.role === "assistant" && m.kind !== 'recap' && !streamedMessages.current.has(m.id))
    // Non-streamed results (e.g. a fixed question) still read once. Both paths
    // pass raw Markdown to the shared Python text preparation.
    if (reading && voice.availability?.ready && words.length) void voice.speak(words.map((m) => focusSpeech(m.content, true)).join("\n"))
  }, [room, reading, voice])

  // Text is the default; a single message can be read on demand (VOICE-01).
  function listen(content: string) {
    if (voice.availability?.ready) { void voice.stop().then(() => voice.speak(focusSpeech(content, true))); return }
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
    if (!t || acting.current || (room?.question?.kind === "recall" && !askMode)) return
    stopReading()
    if (value === undefined) setText("")
    const numericAnswer = !askMode && (room?.question?.kind === "calc" || room?.question?.question_type === "calc")
    const questionMessage = askMode
    setAskMode(false) // The tag belongs to this send only, including a failed send.
    if (!(await act(questionMessage ? "classroom.ask" : numericAnswer ? "classroom.answer" : "classroom.reply",
      numericAnswer ? { value: t } : { text: t }, numericAnswer ? "正在核对数值…" : "老师在看你的回答…")) && value === undefined) {
      setText((draft) => draft || t)
    }
  }

  async function openSource(chunk: string) {
    const ownGeneration = generation.current
    try {
      const original = await call<Source>("classroom.source", { plan_id: planId, chunk_id: chunk })
      if (ownGeneration === generation.current) setSource(original)
    } catch (e) { if (ownGeneration === generation.current) toast("error", (e as Error).message) }
  }

  if (!room) return <div className="page"><Loading shape="conversation" label="正在进入课堂…" onCancel={()=>go('home')}/></div>
  // An active lesson interaction is authoritative; a saved casual question may
  // still be in the snapshot while an interjection waits for "回到刚才".
  const q = room.question
  const done = room.points.filter((p) => p.taught).length
  const t = room.today
  const markdown = (content: string, showSource: (id: string) => void = openSource, messageKey?: React.Key, focus = false) => (
    <ClassroomMarkdown content={content} onSource={showSource} focus={focus} messageKey={messageKey} />
  )

  return (
    <div className="class-page class-with-teacher" style={{ gridTemplateColumns: "minmax(0, 1fr) var(--teacher-width, 32px)" }}>
      <section className="class-main">
        <header className="class-head">
          <Button small kind="ghost" onClick={() => go("home")}><ArrowLeft />下课</Button>
          <div className="class-title">
            <span className="eyebrow">{room.check ? "全部模块已学完" : `模块 ${room.module.number}/${room.module.total}`} · {room.plan.goal}</span>
            <b>{room.check ? "最终检验" : room.module.title}</b>
            <div className="class-module-inline129">
              <span>{room.check ? `已通过 ${room.check.passed}/${room.check.total} 个知识点` : `本模块 ${done}/${room.points.length} 个点已讲`}</span>
              {!room.check && <button aria-expanded={pointsExpanded} aria-controls="classroom-points" onClick={() => setPointsExpanded(value => !value)}>
                {pointsExpanded ? '收起知识点' : '展开知识点'}</button>}
              <div className="class-check-bar" role="progressbar" aria-label={room.check ? '最终检验进度' : '本模块讲解进度'}
                aria-valuenow={room.check?.passed ?? done} aria-valuemin={0} aria-valuemax={room.check?.total ?? room.points.length}>
                <i style={{ width: `${100 * (room.check?.passed ?? done) / Math.max(1, room.check?.total ?? room.points.length)}%` }}/></div>
            </div>
          </div>
          {room.plan.state === "checking" && <Button small onClick={() => setWrongAnswers(true)}>错题集</Button>}
          <Button small kind="ghost" role="switch" aria-label="只看重点" aria-checked={classroomFocus} onClick={toggleFocus}>只看重点{classroomFocus?'：开':'：关'}</Button>
          <div className="class-status129">
            {t && <ProgressStatus value={t} />}
            {room.preparation && <PreparationBadge preparation={room.preparation} busy={!!busy} act={act} reducedMotion={reduceMotion}/>}
            {t?.is_late_night && <span className="class-late129" title="已经很晚了，注意休息。今天学的内容会记在开课那天。">夜深了，注意休息</span>}
          </div>
        </header>
          {pointsExpanded && !room.check && <div className="class-points class-points-popup129" id="classroom-points">
          {room.points.map((p, i) => (
            <span key={p.id} className={`class-point ${p.current && ["teach", "opening", "teaching", "closing", "answer", "preparing_materials"].includes(room.step) ? "current" : ""} ${p.taught ? "taught" : ""} ${POINT_TONE[p.state] ?? ""}`}
                  title={`${p.title} · ${p.minutes} 分钟${POINT_LABEL[p.state] ? " · " + POINT_LABEL[p.state] : ""}`}>
              {p.taught ? <CheckCircle2 size={13} /> : <i>{i + 1}</i>}{p.title}
            </span>
          ))}
          </div>}
        {room.failure && <div className="class-failure129" role="alert"><span title={room.failure.message}>{room.failure.message}</span> <Button small disabled={!!busy}
          onClick={() => act("classroom.retry", {}, "重试这一步…")}>重试</Button></div>}
        <MaterialToolbar key={room.module.id} concept={room.concept} entries={room.module.navigation ?? []}/>
        <div className="class-scroll" ref={scroller}>
          {room.step === 'preparing_materials' && <PreparationWait preparation={room.preparation} busy={!!busy} act={act}/>}
          {room.messages.length === 0 && !busy && room.step === "teach" && (
            <div className="class-empty">
              <BookOpen size={28} />
              <p>这个模块有 {room.points.length} 个知识点。点“开始上课”，老师一个点一个点地讲，依据原文解释，再用题库题确认。</p>
            </div>
          )}
          {room.messages.map((m) => {
            if (m.kind === 'recap' && m.recap) return <div key={m.id} data-turn-key={m.id} data-turn-role={m.role}>
              <RecapCard content={m.recap} onSource={id => void openSource(id)} /></div>
            if (m.role === "user") return <div key={m.id} data-turn-key={m.id} data-turn-role={m.role} className={`bubble user ${m.kind === "choice" ? "class-choice" : ""}`} data-selectable>
              {m.kind === "choice" && <span className="class-message-label">我的选择</span>}{m.content}</div>
            if (m.kind === "casual") return <div key={m.id} data-turn-key={m.id} data-turn-role={m.role} className="class-ask" data-selectable><span>随口问</span>{m.content}</div>
            if (m.kind === "question") return <div key={m.id} data-turn-key={m.id} data-turn-role={m.role} className="class-question" data-selectable>{markdown(m.content)}</div>
            if (m.kind === "verdict") return <div key={m.id} data-turn-key={m.id} data-turn-role={m.role} className={`class-verdict ${m.content.startsWith("✓") ? "ok" : "bad"}`}>{m.content}
              {m.id === room.explanation?.message_id && <Button small disabled={!!busy} onClick={() => act("classroom.why")}>为什么？</Button>}</div>
            if (m.role === "system") return <div key={m.id} data-turn-key={m.id} data-turn-role={m.role} className={`class-note ${m.kind === "error" ? "warn" : ""}`}>
              {m.kind === "error" && <CircleAlert size={15} />}{m.content}</div>
            return <div key={m.id} data-turn-key={m.id} data-turn-role={m.role} className={`bubble bot ${m.kind === "step" ? "class-step" : m.kind === "step_reply" ? "class-step-reply" : ""}`} data-selectable>
              {m.kind === "step" && <span className="class-message-label">老师讲解</span>}
              {m.kind === "step_reply" && <span className="class-message-label">老师回应</span>}{markdown(m.content, id => {
                if (!sourceCards.current.get(m.id)?.reveal(id)) void openSource(id)
              }, m.id, classroomFocus)}
              <OriginalCards cards={m.sources ?? []} reducedMotion={reduceMotion}
                ref={handle => { if (handle) sourceCards.current.set(m.id, handle); else sourceCards.current.delete(m.id) }} />
              <button className="icon-btn listen" title="听这段" onClick={() => listen(m.content)}><Volume2 size={15} /></button></div>
          })}
          {room.direct_review && <div data-testid="direct-review">
            {(room.direct_review.entries ?? [room.direct_review]).map(entry => <div key={entry.number} className="bubble bot" style={{ marginBottom: 12 }}
              data-turn-key={`direct-question-${entry.number}`} data-turn-role="assistant">
              <span className="eyebrow">随口聊聊 {entry.number}/{room.direct_review!.total} · 不记分</span>
              <b style={{ display: "block", margin: "6px 0" }}>{entry.title}</b>
              {markdown(entry.question, openSource, `${planId}:direct-question:${entry.number}:${entry.point_id}`, classroomFocus)}
              {entry.answered && <>
                <section className="recall-own" data-testid="direct-answer" data-turn-key={`direct-answer-${entry.number}`} data-turn-role="user">
                  <b className="small">我的回答</b><div data-selectable style={{ whiteSpace: "pre-wrap" }}>{entry.answer || "（这次回答未保留）"}</div>
                </section>
                <div style={{ borderTop: "1px solid var(--border)", marginTop: 12, paddingTop: 12 }}>
                  <span className="small muted">对照原文，自己判断理解得对不对；不判分、不记录。</span>
                  {entry.sources.map(card => <section key={card.id} style={{ marginTop: 10 }}>
                    <b className="small">{card.title} · {card.locator}</b><div data-selectable>{markdown(card.text)}</div>
                  </section>)}
                </div>
              </>}
            </div>)}
          </div>}
          {pendingUser && <div className="bubble user" data-turn-key="pending-student" data-turn-role="user" data-selectable>{pendingUser}</div>}
          {verdictPreview && <div className={`class-verdict ${verdictPreview.correct ? "ok" : "bad"}`}>{verdictPreview.text}</div>}
          {stream !== null && (live(stream)
            ? <div data-turn-key="teacher-stream" data-turn-role="assistant" className="bubble bot streaming wait-content">{markdown(live(stream), openSource, streamMessageKey.current, classroomFocus)}</div>
            : <Loading active={!!busy} label={busy?.includes('判')||busy?.includes('核对') ? busy : `老师正在想…${busy && !busy.includes('老师') ? ` · ${busy}` : ''}`}
                shape="conversation" onCancel={abort.current ? ()=>abort.current?.abort() : undefined}/>)}
          {stream === null && <Loading active={!!busy} label={busy ?? '正在记录回答…'}/>}
          {q?.kind === "recall" && <RecallQuestion key={`${planId}:${q.id}:${q.purpose}:${q.number ?? ""}:${recallAttempt}`}
            question={q} request={recallRequest} busy={!!busy} next={() => {
              void act("classroom.state", {}, "正在读取下一题…").then((ok) => { if (ok) setRecallAttempt((value) => value + 1) })
            }} />}
        </div>

        {room.can_skip && <div className="plan-actions">
          <SkipControl127 module={skipModule} busy={!!busy} hasPoint={!!currentPoint} reducedMotion={reduceMotion}
            onMode={value => { cancelPrefetch(); setSkipModule(value) }}
            onSkip={() => {
              if (skipModule) { cancelPrefetch(); setSkipConfirm(true) }
              else void act('classroom.skip_point', {}, '正在进入下一个知识点…').then(ok => { if (ok) setSkipModule(false) })
            }} />
        </div>}
        <Actions room={room} act={act} busy={!!busy} />
        {!room.direct_review && <ConversationActions step={room.step} conversation={room.conversation} busy={!!busy} act={act}
          ask={() => { cancelPrefetch(); setAskMode(!!q); input.current?.focus() }} />}
        {room.explanation && <div className="answer-explanation">
          {room.explanation.open && <><div>{markdown(room.explanation.text)}</div><small className="muted">
            {room.explanation.label} · {room.explanation.source.title} · {room.explanation.source.locator}</small>
            {room.explanation.source.url && <a href={room.explanation.source.url} target="_blank" rel="noreferrer"> 查看答案出处</a>}</>}
        </div>}
        {q && q.kind !== "recall" && <QuestionInputs question={q} act={act} busy={!!busy} />}
        {askMode && <div className="class-question-tag" role="status">这句是提问 · <button onClick={() => setAskMode(false)}>取消</button></div>}
        <div className="composer">
          <textarea ref={input} className="input" rows={1} value={text} aria-label="课堂输入"
                    disabled={(q?.kind === "recall" && !askMode) || !!room.direct_review?.answered}
                    placeholder={room.direct_review?.answered ? "看完原文后，选择下一句或是否跳过" : room.direct_review ? "随口回答，不记分" : askMode ? "想问什么？答完回到刚才" : q?.kind === "recall" ? "作答请用上方问答卡片；提问可点“我想问…”" : (q?.kind === "calc" || q?.question_type === "calc") ? "填写计算数值，可带单位" : q ? "输入作答，或点上方选项；提问可点“我想问…”" : "回答老师或提问，都可以直接说"}
                    onChange={(e) => {cancelPrefetch();setText(e.target.value)}}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && !e.repeat) {
                      e.preventDefault()
                      void send()
                    } }} />
          <MicButton voice={mic} />
          {busy
            ? <Button onClick={() => { stopReading(); abort.current?.abort() }}><Square />停止</Button>
            : <Button kind="primary" onClick={() => send()} disabled={!text.trim() || (q?.kind === "recall" && !askMode) || !!room.direct_review?.answered}><SendHorizontal />发送</Button>}
        </div>
      </section>

      <React.Suspense fallback={<Loading label="正在准备老师栏…"/>}>
        <TeacherWindow ref={teacher} reducedMotion={reduceMotion} speaking={voice.state === "playing"}
          pointKey={room?.points.find((point) => point.current)?.id}>
        <div className="class-voice">
          <button className={`btn small ${reading ? "primary" : ""}`} onClick={toggleReading} aria-pressed={reading} title={reading ? "关闭朗读并释放语音引擎" : "打开朗读"}>
            {reading ? <Volume2 size={16} /> : <VolumeX size={16} />} 朗读：{reading ? "开" : "关"}
          </button>
          <span className="small muted">{reading ? voice.message : "朗读已关闭"}</span>
          <Loading active={reading && ['checking','warming','synthesizing'].includes(voice.state)} label={voice.message} onCancel={stopReading}/>
          {voice.availability && !voice.availability.ready && voice.availability.download && (
            <Button small disabled={voice.downloadState?.state === "downloading"} onClick={() => voice.download()}>下载朗读组件</Button>
          )}
          {voice.downloadState?.state === "downloading" && <>
            <Loading label="正在下载朗读组件…" detail={`${Math.round(100 * voice.downloadState.received_bytes / Math.max(1, voice.downloadState.total_bytes))}%`} onCancel={()=>void voice.cancelDownload()}/>
            <span className="small muted">下载 {Math.round(100 * voice.downloadState.received_bytes / Math.max(1, voice.downloadState.total_bytes))}%</span>
            <Button small kind="ghost" onClick={() => voice.cancelDownload()}>取消下载</Button>
          </>}
          {voice.error && <span className="small" role="alert">{voice.error}</span>}
          {voice.downloadState?.state === "error" && <span className="small" role="alert">{voice.downloadState.message}</span>}
          {voice.busy && <Button small kind="ghost" onClick={stopReading}><Square />停</Button>}
        </div>
        </TeacherWindow>
      </React.Suspense>

      {wrongAnswers && <WrongAnswers planId={planId!} onClose={() => setWrongAnswers(false)} />}
      {skipConfirm && <section className="class-source" role="dialog" aria-modal="true" aria-label="确认跳过该模块">
        <h3>跳过该模块？</h3><p>有题会进入整个模块的直接考；没有题会随口聊三个点，再由你决定是否跳过。</p>
        <div className="row"><Button kind="primary" onClick={()=>{setSkipConfirm(false);void act('classroom.test',{kind:'skip_test'},'正在准备直接考…').then(ok=>{if(ok)setSkipModule(false)})}}>确认，进入直接考</Button>
        <Button kind="ghost" onClick={()=>setSkipConfirm(false)}>取消</Button></div>
      </section>}
      {source && (
        <div className="class-source fade-in" role="dialog">
          <div className="row between">
            <b>{source.title}{source.section.length ? ` · ${source.section.join(" / ")}` : ""}</b>
            <button className="icon-btn" onClick={() => setSource(null)} aria-label="关闭"><X /></button>
          </div>
          <div className="class-source-text" data-selectable>{markdown(source.text)}</div>
          {source.url && <a href={source.url} target="_blank" rel="noreferrer" className="small"><ExternalLink size={13} /> 打开原网页</a>}
        </div>
      )}
    </div>
  )
}

function Actions({ room, act, busy }: { room: Room; busy: boolean; act: (m: string, p?: Record<string, unknown>, label?: string) => Promise<boolean> }) {
  const step = room.step
  if (step === "direct_choice") return <div className="plan-actions" role="group" aria-label="是否跳过这个模块" data-testid="direct-choice">
    <span className="small muted">不管刚才回答怎样，都由你自己决定。</span>
    <Button kind="primary" disabled={busy} onClick={() => act("classroom.direct_choice", { skip: true }, "正在记录你的选择…")}><SkipForward />跳过这个模块</Button>
    <Button disabled={busy} onClick={() => act("classroom.direct_choice", { skip: false }, "正在回到原位置…")}>接着学</Button>
  </div>
  if (step === "direct_review") return room.direct_review?.answered ? <div className="plan-actions">
    <Button kind="primary" disabled={busy} onClick={() => act("classroom.direct_next", {}, "老师在想下一句…")}>看完原文，下一句</Button>
  </div> : <div className="small muted" style={{ padding: "6px 18px" }}>用下面的输入框回答，答完就能看这个点的原文。</div>
  if (step === "preparing_materials") return null
  if (step === "teach") return (
    <div className="plan-actions">
      {<Button kind="primary" disabled={busy} onClick={() => act("classroom.next")}><ChevronRight />{room.messages.length ? "继续" : "开始上课"}</Button>}
    </div>
  )
  if (step === "test_offer") return (
    <div className="plan-actions">
      <Button kind="primary" disabled={busy} onClick={() => act("classroom.test", { kind: "module_test" }, "正在出题…")}><ClipboardCheck />开始模块检测</Button>
    </div>
  )
  if (step === "test_done") return (
    <div className="plan-actions">
      {room.red.length > 0 && <Button kind="primary" disabled={busy} onClick={() => act("classroom.test", { kind: "retest" }, "正在出题…")}><RotateCcw />只重测答错的题</Button>}
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
