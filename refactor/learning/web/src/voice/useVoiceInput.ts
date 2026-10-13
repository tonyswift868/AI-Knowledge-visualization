import * as React from "react"
import { call, on } from "../bridge"

export type Replacement = [number, number, string, string] // Original Python characters, end exclusive.
export type VoiceTranscript = { session_id: string; text: string; raw_text: string; replacements: Replacement[] }
export type VoiceSegment = VoiceTranscript & { index: number }
export type VoiceError = { code: string; message: string }
export type ModelStatus = {
  state: "not_downloaded" | "downloading" | "ready"; installed: boolean; invalid: boolean
  model_name: string; size_bytes: number; download_url: string; job_id: string | null
  downloaded_bytes: number; total_bytes: number; cancelling: boolean; cancelled?: boolean; error?: VoiceError
}
export type VoiceState = "idle" | "checking" | "starting" | "recording" | "finishing" | "error"
export type VoiceTransport = { call: typeof call; on: typeof on; available: () => boolean }
const desktop: VoiceTransport = { call, on, available: () => !!window.qt?.webChannelTransport }
let sequence = 0
const token = () => `v${Date.now().toString(36)}-${(++sequence).toString(36)}`
const append = (base: string, text: string) => text ? base + (base && !/\s$/.test(base) ? " " : "") + text : base

function failure(error: any): VoiceError {
  const code = error?.code ?? "ASR_ENGINE_FAILED"
  const hints: Record<string, string> = {
    ASR_MIC_UNAVAILABLE: "未找到可用麦克风。请连接麦克风，并检查系统默认输入设备。",
    ASR_MIC_PERMISSION: "麦克风权限未开启。请在系统隐私设置中允许桌面应用使用麦克风后重试。",
    ASR_MODEL_REQUIRED: "语音模型尚未准备好，请先下载。",
    ASR_MODEL_INVALID: "本机语音模型损坏或校验失败，请重新下载。",
    ASR_ENGINE_FAILED: "语音识别未完成，已有文字已保留。请重试。",
  }
  return { code, message: hints[code] ?? error?.message ?? "语音输入未完成，请重试。" }
}

type Dictation = { id: string; base: string; expected: string; active: boolean; raw: string; undone: boolean }
export type VoiceInputOptions = {
  value: string
  onChange: (value: string) => void
  /** Terms are captured at start. Unmount or change scopeKey when changing course/input. */
  terms?: readonly string[]
  context?: { plan_id: string; module_id: string; point_id?: string }
  disabled?: boolean
  scopeKey?: string
  transport?: VoiceTransport
}

/** Bind value/onChange to one input. This hook has no send/submit callback.
 * Whole partials replace only this recording's appended text. Manual edits
 * cancel recognition; late results cannot overwrite the learner's new text.
 */
export function useVoiceInput({ value, onChange, terms = [], context, disabled = false, scopeKey, transport = desktop }: VoiceInputOptions) {
  const [state, setState] = React.useState<VoiceState>("idle")
  const [model, setModel] = React.useState<ModelStatus | null>(null)
  const [error, setError] = React.useState<VoiceError | null>(null)
  const [downloadOpen, setDownloadOpen] = React.useState(false)
  const [segments, setSegments] = React.useState<VoiceSegment[]>([])
  const [replacements, setReplacements] = React.useState<Replacement[]>([])
  const [undone, setUndone] = React.useState(false)
  const [originalText, setOriginalText] = React.useState("")
  const [notice, setNotice] = React.useState("")
  const alive = React.useRef(false)
  const epoch = React.useRef(0)
  const session = React.useRef<Dictation | null>(null)
  const downloadJob = React.useRef<string | null>(null)
  const busy = React.useRef(false)
  const latest = React.useRef({ value, onChange, terms, context, disabled })
  latest.current = { value, onChange, terms, context, disabled }
  const supported = transport.available()

  const cancelRecording = React.useCallback(() => {
    ++epoch.current
    busy.current = false
    const current = session.current
    if (current?.active) {
      current.active = false
      void transport.call("voice.cancel", { session_id: current.id }).catch(() => {})
    }
    if (alive.current) setState("idle")
  }, [transport])

  const accept = React.useCallback((data: VoiceTranscript, final = false) => {
    const current = session.current
    if (!alive.current || !current?.active || data.session_id !== current.id) return
    current.raw = data.raw_text
    setOriginalText(data.raw_text)
    current.expected = append(current.base, current.undone ? data.raw_text : data.text)
    latest.current.onChange(current.expected)
    setReplacements(data.replacements)
    if (final) {
      current.active = false
      busy.current = false
      setState("idle")
      setNotice(data.text ? "录音已结束。检查文字后，由你点击发送。" : "没有识别到语音，可以重试。")
    }
  }, [])

  const report = React.useCallback((reason: any) => {
    if (!alive.current || reason?.code === "CANCELLED") return
    if (session.current) session.current.active = false
    busy.current = false
    setError(failure(reason))
    setState("error")
    if (["ASR_MODEL_REQUIRED", "ASR_MODEL_INVALID"].includes(reason?.code)) {
      setDownloadOpen(true)
      void transport.call<ModelStatus>("voice.status").then((status) => { if (alive.current) setModel(status) }).catch(() => {})
    }
  }, [transport])

  React.useEffect(() => {
    alive.current = true
    const offs = [
      transport.on("voice.partial", (data: VoiceTranscript) => accept(data)),
      transport.on("voice.final", (data: VoiceTranscript) => accept(data, true)),
      transport.on("voice.segment", (data: VoiceSegment) => {
        if (session.current?.active && data.session_id === session.current.id)
          setSegments((prior) => prior.some((s) => s.index === data.index) ? prior : [...prior, data])
      }),
      transport.on("voice.state", (data) => {
        if (data.session_id !== session.current?.id) return
        if (data.cancelled) { cancelRecording(); setNotice("录音已取消，已有文字已保留。") }
        else if (session.current?.active && ["recording", "finishing"].includes(data.state)) setState(data.state)
      }),
      transport.on("voice.error", (data) => { if (session.current?.active && data.session_id === session.current.id) report(data) }),
      transport.on("voice.model", (data: ModelStatus) => {
        setModel(data)
        if (data.job_id !== downloadJob.current || data.state === "downloading") return
        downloadJob.current = null
        if (data.error) setError(failure(data.error))
        else if (!data.cancelled) { setDownloadOpen(false); setError(null); setNotice("模型已就绪，点击开始录音。") }
      }),
      transport.on("scope_changed", () => { cancelRecording(); session.current = null; setReplacements([]); setDownloadOpen(false) }),
    ]
    if (supported) void transport.call<ModelStatus>("voice.status").then((data) => { if (alive.current) setModel(data) }).catch(report)
    return () => {
      alive.current = false
      cancelRecording()
      if (downloadJob.current) void transport.call("voice.download_cancel", { job_id: downloadJob.current }).catch(() => {})
      downloadJob.current = null
      offs.forEach((off) => off())
    }
  }, [transport, supported, accept, cancelRecording, report])

  // React-owned changes match expected. Any other edit wins over ASR, including
  // edits after stop was clicked but before its last result reached the page.
  React.useLayoutEffect(() => {
    if (session.current && value !== session.current.expected) {
      const active = session.current.active
      cancelRecording()
      session.current = null
      setReplacements([])
      if (active) setNotice("已保留你的编辑，录音已停止。")
    }
  }, [value, cancelRecording])
  React.useEffect(() => {
    cancelRecording()
    session.current = null
    setReplacements([])
    setDownloadOpen(false)
  }, [scopeKey, cancelRecording])
  React.useEffect(() => { if (disabled) cancelRecording() }, [disabled, cancelRecording])

  const start = async () => {
    if (!supported || latest.current.disabled || busy.current) return
    busy.current = true
    const run = ++epoch.current
    setError(null); setNotice(""); setState("checking")
    try {
      const info = await transport.call<ModelStatus>("voice.status")
      if (!alive.current || epoch.current !== run) return
      setModel(info)
      if (info.state !== "ready") { busy.current = false; setState("idle"); setDownloadOpen(true); return }
      const lesson = latest.current.context ? await transport.call<{terms:string[];question:string;protected?:string[]}>("voice.context", latest.current.context)
        : {terms:[...latest.current.terms],question:"",protected:[]}
      if (!alive.current || epoch.current !== run) return
      const id = token()
      session.current = { id, base: latest.current.value, expected: latest.current.value, active: true, raw: "", undone: false }
      setSegments([]); setReplacements([]); setOriginalText(""); setUndone(false); setState("starting")
      await transport.call("voice.start", { session_id: id, terms: lesson.terms, question: lesson.question,protected:lesson.protected??[] })
    } catch (reason) { if (alive.current && epoch.current === run) report(reason) }
  }

  const stop = async (): Promise<string | null> => {
    const current = session.current
    if (!current?.active || state === "finishing") return null
    setState("finishing")
    try {
      const result = await transport.call<VoiceTranscript>("voice.stop", { session_id: current.id })
      accept(result, true)
      return result.text
    } catch (reason) {
      if (session.current === current && current.active) report(reason)
      return null
    }
  }

  const download = async () => {
    if (!supported || latest.current.disabled || downloadJob.current || model?.state === "downloading") return
    const job = token()
    downloadJob.current = job
    setError(null)
    setModel((prior) => prior ? { ...prior, state: "downloading", job_id: job,
      downloaded_bytes: 0, cancelling: false } : prior)
    try {
      const data = await transport.call<ModelStatus>("voice.download", { consent: true, job_id: job, choose_path: true })
      if (alive.current && downloadJob.current === job) setModel(data)
    } catch (reason) {
      if (alive.current && downloadJob.current === job) {
        downloadJob.current = null
        setError(failure(reason))
        setModel((prior) => prior?.job_id === job ? { ...prior, state: prior.installed ? "ready" : "not_downloaded",
          job_id: null, cancelling: false } : prior)
      }
    }
  }

  const cancelDownload = async () => {
    if (!downloadJob.current) { setDownloadOpen(false); return }
    try { await transport.call("voice.download_cancel", { job_id: downloadJob.current }) }
    catch (reason) { if (alive.current) setError(failure(reason)) }
  }
  const undoCorrections = () => {
    const current = session.current
    if (!current || current.undone) return
    current.undone = true
    current.expected = append(current.base, current.raw)
    latest.current.onChange(current.expected)
    setUndone(true)
  }

  return { state, model, error, segments, replacements, originalText, notice, downloadOpen, supported, disabled,
    ownsDownload: !!downloadJob.current, canUndo: replacements.length > 0 && !undone,
    start, stop, cancel: cancelRecording, download, cancelDownload, undoCorrections,
    openDownload: () => setDownloadOpen(true), closeDownload: () => {
      // Also covers a close click before the download-start response renders.
      if (downloadJob.current) void cancelDownload()
      else setDownloadOpen(false)
    } }
}

export type VoiceInputController = ReturnType<typeof useVoiceInput>
