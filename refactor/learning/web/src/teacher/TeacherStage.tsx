import * as React from "react"
import { GripHorizontal, ImageOff, LoaderCircle, Minimize2, MoveDiagonal2 } from "lucide-react"
import { createPortal } from "react-dom"
import { call } from "../bridge"
import { TeacherRuntime, type Capabilities, type ModelView, type ReactionEvent, type StageSnapshot, type TeacherManifest } from "./teacherRuntime"
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
  interactive?: boolean
  view?: ModelView
  onViewChange?: (view: ModelView) => void
  onThumbnail?: (image: string) => void
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
      runtime.setView(latest.current.view ?? { scale: 1, panX: 0, panY: 0 })
      if (latest.current.mouthLevel !== undefined) runtime.setMouthLevel(latest.current.mouthLevel, false)
      const thumbnail = runtime.thumbnail()
      if (thumbnail) latest.current.onThumbnail?.(thumbnail)
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
  React.useEffect(() => { if (props.view) renderer.current?.setView(props.view) }, [props.view])
  React.useEffect(() => {
    if (props.mouthLevel !== undefined) renderer.current?.setMouthLevel(props.mouthLevel, false)
  }, [props.mouthLevel, active])

  const modelDrag = React.useRef<{ id: number; x: number; y: number; view: ModelView } | null>(null)
  const changeView = (view: ModelView) => {
    const next = { scale: Math.max(.5, Math.min(2.5, view.scale)), panX: Math.max(-1, Math.min(1, view.panX)), panY: Math.max(-1, Math.min(1, view.panY)) }
    renderer.current?.setView(next)
    latest.current.onViewChange?.(next)
  }
  React.useEffect(() => {
    const el = host.current
    if (!el || !props.interactive) return
    const wheel = (e: WheelEvent) => {
      if (!activeRef.current || !renderer.current) return
      e.preventDefault()
      const view = renderer.current.snapshot().view
      changeView({ ...view, scale: view.scale * Math.exp(-e.deltaY * .0015) })
    }
    el.addEventListener("wheel", wheel, { passive: false })
    return () => el.removeEventListener("wheel", wheel)
  }, [props.interactive])

  const install = async () => {
    if (props.onInstallRequest) { props.onInstallRequest(); return }
    try { if ((await call("teacher.choose_assets")).selected) refresh() }
    catch { setMessage("模型目录暂时无法打开，请稍后重试。") }
  }

  return <section className={`teacher-stage ${props.className || ""}`} data-teacher-status={status}
                  aria-label="椿老师" aria-busy={status === "loading"} aria-hidden={props.visible === false}
                  style={props.visible === false ? { visibility: "hidden" } : undefined}>
    <div className={`teacher-stage-canvas ${props.interactive ? "interactive" : ""}`} ref={host}
      title={props.interactive ? "拖动平移老师，滚轮缩放，双击恢复" : undefined}
      onPointerDown={(e) => {
        if (!props.interactive || !active || e.button !== 0 || !renderer.current) return
        modelDrag.current = { id: e.pointerId, x: e.clientX, y: e.clientY, view: renderer.current.snapshot().view }
        e.currentTarget.setPointerCapture(e.pointerId)
      }} onPointerMove={(e) => {
        const drag = modelDrag.current
        if (!drag || drag.id !== e.pointerId) return
        changeView({ ...drag.view, panX: drag.view.panX + (e.clientX - drag.x) / Math.max(1, e.currentTarget.clientWidth),
          panY: drag.view.panY + (e.clientY - drag.y) / Math.max(1, e.currentTarget.clientHeight) })
      }} onPointerUp={() => { modelDrag.current = null }} onPointerCancel={() => { modelDrag.current = null }}
      onLostPointerCapture={() => { modelDrag.current = null }}
      onDoubleClick={() => { if (props.interactive) changeView({ scale: 1, panX: 0, panY: 0 }) }} />
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

const WINDOW_KEY = "mf.classroom.teacher-window.v1"
type WindowState = { open: boolean; x: number; y: number; width: number; height: number; view: ModelView }
const sameBox = (a: WindowState, b: WindowState) => a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height
function windowBounds() {
  const input = document.querySelector(".class-main .composer, .class-main .recall-chat")?.getBoundingClientRect()
  return { width: Math.max(1, window.innerWidth - 16), bottom: Math.max(9, Math.min(window.innerHeight - 8, input ? input.top - 8 : window.innerHeight - 100)) }
}
const avatarBottom = () => Math.max(8, Math.min(window.innerHeight - 64, window.innerHeight - windowBounds().bottom + 8))
function fitWindow(value: WindowState): WindowState {
  const bounds = windowBounds()
  const width = Math.max(Math.min(220, bounds.width), Math.min(value.width, 560, bounds.width))
  const availableHeight = Math.max(1, bounds.bottom - 8)
  const height = Math.max(Math.min(280, availableHeight), Math.min(value.height, 720, availableHeight))
  return { ...value, width, height, x: Math.max(8, Math.min(value.x, window.innerWidth - width - 8)),
    y: Math.max(8, Math.min(value.y, bounds.bottom - height)) }
}
function savedWindow(): WindowState {
  const fallback: WindowState = { open: false, x: window.innerWidth - 300, y: window.innerHeight - 530,
    width: 280, height: 400, view: { scale: 1, panX: 0, panY: 0 } }
  try {
    const saved = JSON.parse(localStorage.getItem(WINDOW_KEY) || "null")
    if (saved && typeof saved.open === "boolean" && [saved.x, saved.y, saved.width, saved.height, saved.view?.scale, saved.view?.panX, saved.view?.panY].every(Number.isFinite)) {
      return fitWindow({ ...fallback, ...saved, view: { scale: Math.max(.5, Math.min(2.5, saved.view.scale)), panX: Math.max(-1, Math.min(1, saved.view.panX)), panY: Math.max(-1, Math.min(1, saved.view.panY)) } })
    }
  } catch { /* Optional UI preferences must not block a lesson. */ }
  return fitWindow(fallback)
}

export const TeacherWindow = React.forwardRef<TeacherStageHandle, { children: React.ReactNode }>(function TeacherWindow({ children }, ref) {
  const [state, setState] = React.useState(savedWindow)
  const [avatar, setAvatar] = React.useState(() => { try { return localStorage.getItem(WINDOW_KEY + ".avatar") || "" } catch { return "" } })
  const [bottom, setBottom] = React.useState(avatarBottom)
  const drag = React.useRef<{ id: number; x: number; y: number; resize: boolean; start: WindowState } | null>(null)
  React.useEffect(() => {
    try { localStorage.setItem(WINDOW_KEY, JSON.stringify(state)) } catch { /* optional preference */ }
  }, [state])
  React.useLayoutEffect(() => {
    const measure = () => {
      setBottom(avatarBottom())
      setState((old) => { const fitted = fitWindow(old); return sameBox(old, fitted) ? old : fitted })
    }
    measure()
    window.addEventListener("resize", measure)
    window.visualViewport?.addEventListener("resize", measure)
    const observer = new ResizeObserver(measure)
    const changes = new MutationObserver(measure)
    const main = document.querySelector(".class-main")
    if (main) { observer.observe(main); changes.observe(main, { childList: true, subtree: true }) }
    return () => { window.removeEventListener("resize", measure); window.visualViewport?.removeEventListener("resize", measure); observer.disconnect(); changes.disconnect() }
  }, [])
  const start = (e: React.PointerEvent<HTMLElement>, resize: boolean) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest("button")) return
    drag.current = { id: e.pointerId, x: e.clientX, y: e.clientY, resize, start: state }
    e.currentTarget.setPointerCapture(e.pointerId)
    e.preventDefault()
  }
  const move = (e: React.PointerEvent<HTMLElement>) => {
    const d = drag.current
    if (!d || d.id !== e.pointerId) return
    const dx = e.clientX - d.x, dy = e.clientY - d.y
    setState(fitWindow(d.resize ? { ...d.start, width: d.start.width + dx, height: d.start.height + dy }
      : { ...d.start, x: d.start.x + dx, y: d.start.y + dy }))
  }
  const end = () => { drag.current = null }
  return createPortal(<>
    <section className="teacher-window" aria-label="浮动老师" hidden={!state.open}
      style={{ left: state.x, top: state.y, width: state.width, height: state.height }}>
      <header className="teacher-window-handle" onPointerDown={(e) => start(e, false)} onPointerMove={move}
        onPointerUp={end} onPointerCancel={end} onLostPointerCapture={end} title="按住这里拖动老师窗口">
        <GripHorizontal size={18} /><b>椿老师</b>
        <button className="icon-btn" aria-label="收起老师" onClick={() => setState((s) => ({ ...s, open: false }))}><Minimize2 /></button>
      </header>
      <div className="teacher-window-scene"><TeacherStage ref={ref} paused={!state.open} visible={state.open} interactive
        view={state.view} onViewChange={(view) => setState((s) => ({ ...s, view }))}
        onThumbnail={(image) => { setAvatar(image); try { localStorage.setItem(WINDOW_KEY + ".avatar", image) } catch { /* optional */ } }} /></div>
      <div className="teacher-window-controls">{children}<small className="teacher-window-tip">拖动老师 · 滚轮缩放 · 双击恢复</small></div>
      <span role="slider" tabIndex={0} aria-label="调整老师窗口大小" aria-valuemin={220} aria-valuemax={560} aria-valuenow={Math.round(state.width)}
        className="teacher-window-resize" onPointerDown={(e) => start(e, true)} onPointerMove={move}
        onPointerUp={end} onPointerCancel={end} onLostPointerCapture={end}
        onKeyDown={(e) => { const delta = { ArrowRight: [20, 0], ArrowLeft: [-20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] }[e.key];
          if (delta) { e.preventDefault(); setState((s) => fitWindow({ ...s, width: s.width + delta[0], height: s.height + delta[1] })) } }}><MoveDiagonal2 size={17} /></span>
    </section>
    {!state.open && <button className="teacher-avatar" style={{ bottom }} aria-label="展开老师" title="展开椿老师" onClick={() => setState((s) => ({ ...fitWindow(s), open: true }))}>
      {avatar ? <img src={avatar} alt="椿老师头像" /> : <span>椿</span>}<i aria-hidden>＋</i>
    </button>}
  </>, document.body)
})
