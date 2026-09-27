import * as React from "react"
import { ImageOff, LoaderCircle } from "lucide-react"
import { call } from "../bridge"
import { TeacherRuntime, type Capabilities, type ReactionEvent, type StageSnapshot, type TeacherManifest } from "./teacherRuntime"
import "./teacher-stage.css"

export type TeacherAssetStatus = { ready: boolean; code: string; message: string; manifest: TeacherManifest | null;
  installation: { method: string; message: string } }
export type TeacherStageHandle = {
  /** Feed each TtsService.mouth_level sample here; a lost stream closes in 250 ms. */
  setMouthLevel: (level: number) => void
  nod: () => boolean; shake: () => boolean; think: () => boolean; happy: () => boolean
  stopReaction: () => void
  snapshot: () => StageSnapshot | null
}
export type TeacherStageProps = {
  /** Controlled value, held until changed. Send 0 on TTS stop/silence. No text-driven lip animation. */
  mouthLevel?: number
  paused?: boolean
  visible?: boolean
  className?: string
  onReady?: (capabilities: Capabilities) => void
  onStatus?: (status: "loading" | "ready" | "missing" | "error", message: string) => void
  onReaction?: (event: ReactionEvent) => void
  onInstallRequest?: () => void
}

export const TeacherStage = React.forwardRef<TeacherStageHandle, TeacherStageProps>(function TeacherStage(props, ref) {
  const host = React.useRef<HTMLDivElement>(null)
  const renderer = React.useRef<TeacherRuntime | null>(null)
  const latest = React.useRef(props)
  latest.current = props
  const [status, setStatus] = React.useState<"loading" | "ready" | "missing" | "error">("loading")
  const [message, setMessage] = React.useState("正在准备老师形象…")
  const [installHelp, setInstallHelp] = React.useState("")
  const [revision, refresh] = React.useReducer((v) => v + 1, 0)
  const [inView, setInView] = React.useState(true)
  const [pageVisible, setPageVisible] = React.useState(!document.hidden)
  const active = !props.paused && props.visible !== false && inView && pageVisible
  const activeRef = React.useRef(active)
  activeRef.current = active

  React.useImperativeHandle(ref, () => ({
    setMouthLevel: (v) => renderer.current?.setMouthLevel(v),
    nod: () => renderer.current?.react("nod") ?? false,
    shake: () => renderer.current?.react("shake") ?? false,
    think: () => renderer.current?.react("think") ?? false,
    happy: () => renderer.current?.react("happy") ?? false,
    stopReaction: () => renderer.current?.stopReaction(),
    snapshot: () => renderer.current?.snapshot() ?? null,
  }), [])

  React.useEffect(() => {
    const visibility = () => setPageVisible(!document.hidden)
    document.addEventListener("visibilitychange", visibility)
    const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting && entry.intersectionRect.width > 0 && entry.intersectionRect.height > 0))
    if (host.current) observer.observe(host.current)
    return () => { document.removeEventListener("visibilitychange", visibility); observer.disconnect() }
  }, [])

  React.useEffect(() => {
    let disposed = false
    let runtime: TeacherRuntime | null = null
    const report = (value: typeof status, text: string) => {
      if (disposed) return
      setStatus(value); setMessage(text)
      latest.current.onStatus?.(value, text)
    }
    report("loading", "正在准备老师形象…")
    call<TeacherAssetStatus>("teacher.assets").then(async (resource) => {
      if (disposed) return
      setInstallHelp(resource.installation?.message || "请先安装椿模型，再重新检查。")
      if (!resource.ready || !resource.manifest) { report("missing", resource.message || "老师形象未安装"); return }
      runtime = new TeacherRuntime(host.current!, (event) => { if (!disposed) latest.current.onReaction?.(event) })
      renderer.current = runtime
      runtime.setActive(activeRef.current)
      await runtime.load(resource.manifest)
      if (disposed) return
      runtime.setActive(activeRef.current)
      if (latest.current.mouthLevel !== undefined) runtime.setMouthLevel(latest.current.mouthLevel, false)
      report("ready", "椿老师已就绪")
      latest.current.onReady?.(runtime.capabilities)
    }).catch((error) => {
      if (disposed) return
      console.error("TeacherStage load failed", error)
      runtime?.dispose(); renderer.current = null
      report("error", "老师形象暂时无法显示。可以重新检查，文字学习不受影响。")
    })
    return () => { disposed = true; runtime?.dispose(); if (renderer.current === runtime) renderer.current = null }
  }, [revision])

  React.useEffect(() => { renderer.current?.setActive(active) }, [active])
  React.useEffect(() => {
    if (props.mouthLevel !== undefined) renderer.current?.setMouthLevel(props.mouthLevel, false)
  }, [props.mouthLevel, active])

  const install = async () => {
    if (props.onInstallRequest) { props.onInstallRequest(); return }
    try { if ((await call("teacher.choose_assets")).selected) refresh() }
    catch { setMessage("模型目录暂时无法打开，请稍后重试。") }
  }

  return <section className={`teacher-stage ${props.className || ""}`} data-teacher-status={status}
                  aria-label="椿老师" aria-busy={status === "loading"} aria-hidden={props.visible === false}
                  style={props.visible === false ? { visibility: "hidden" } : undefined}>
    <div className="teacher-stage-canvas" ref={host} />
    {status !== "ready" && <div className="teacher-stage-notice" role="status">
      {status === "loading" ? <LoaderCircle className="spin" /> : <ImageOff />}
      <h3>{status === "missing" ? "老师形象未安装" : status === "loading" ? "正在准备老师形象" : "老师形象暂不可用"}</h3>
      {message !== "老师形象未安装" && <p>{message}</p>}
      {status !== "loading" && <><p>{installHelp}</p><div className="teacher-stage-actions">
        <button onClick={install}>选择已安装的模型</button><button onClick={refresh}>重新检查</button>
      </div><small>你可以继续文字学习。</small></>}
    </div>}
  </section>
})
