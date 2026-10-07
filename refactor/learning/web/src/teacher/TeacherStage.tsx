import * as React from "react"
import { ChevronLeft, ChevronRight, ImageOff, LoaderCircle, PanelRightClose, PanelRightOpen } from "lucide-react"
import { call, on } from "../bridge"
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
  reducedMotion?: boolean
  speaking?: boolean
  pointKey?: string
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
    const pointer = (e: PointerEvent) => renderer.current?.lookAt(e.clientX, e.clientY)
    window.addEventListener("pointermove", pointer)
    const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting && entry.intersectionRect.width > 0 && entry.intersectionRect.height > 0))
    if (host.current) observer.observe(host.current)
    return () => { document.removeEventListener("visibilitychange", visibility); window.removeEventListener("pointermove", pointer); observer.disconnect() }
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
      runtime.setReducedMotion(!!latest.current.reducedMotion)
      runtime.setSpeaking(!!latest.current.speaking)
      if (latest.current.pointKey) runtime.greet()
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
  React.useEffect(() => { renderer.current?.setReducedMotion(!!props.reducedMotion) }, [props.reducedMotion])
  React.useEffect(() => { renderer.current?.setSpeaking(!!props.speaking) }, [props.speaking])
  React.useEffect(() => { if (props.pointKey) renderer.current?.greet() }, [props.pointKey])
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

/** Original, quiet scenery; no downloaded images or animation while reading. */
function TeacherBackdrop() {
  return <div className="teacher-backdrop" aria-hidden="true">
    <svg className="teacher-sakura-scene" viewBox="0 0 400 800" preserveAspectRatio="xMidYMid slice" focusable="false">
      <g fill="none" stroke="currentColor" strokeLinecap="round">
        <path d="M430 20 Q328 54 340 176 M409 43 Q304 50 254 92 M353 86 Q322 72 297 42 M339 120 Q375 110 399 91 M349 161 Q313 141 292 161" strokeWidth="5" />
        <path d="M-25 695 Q49 661 54 563 M18 675 Q56 691 99 667 M46 616 Q21 589 3 587 M52 594 Q80 564 99 574" strokeWidth="3" />
      </g>
      <g fill="currentColor">
        {[[254,92],[298,43],[398,91],[293,161],[98,574],[100,666],[5,587]].map(([x,y]) =>
          <g key={`${x}-${y}`} transform={`translate(${x} ${y})`}>
            <ellipse cy="-6" rx="4" ry="8"/><ellipse cy="-6" rx="4" ry="8" transform="rotate(72)"/>
            <ellipse cy="-6" rx="4" ry="8" transform="rotate(144)"/><ellipse cy="-6" rx="4" ry="8" transform="rotate(216)"/>
            <ellipse cy="-6" rx="4" ry="8" transform="rotate(288)"/>
          </g>)}
        <ellipse cx="60" cy="210" rx="3" ry="7" transform="rotate(25 60 210)"/>
        <ellipse cx="360" cy="385" rx="3" ry="7" transform="rotate(-28 360 385)"/>
        <ellipse cx="115" cy="710" rx="3" ry="6" transform="rotate(45 115 710)"/>
      </g>
    </svg>
    <svg className="teacher-aurora-scene" viewBox="0 0 400 800" preserveAspectRatio="xMidYMid slice" focusable="false">
      <path d="M-40 143 Q80 29 215 97 T460 25 L460 112 Q316 219 216 153 T-40 226Z" fill="#85d7cf" opacity=".12"/>
      <path d="M-30 84 Q115 176 258 54 T452 82 L452 128 Q323 109 255 121 T-30 142Z" fill="#b7a7f2" opacity=".13"/>
      <g fill="#e1daf5">{[[45,45,1.6],[179,34,1],[333,56,1.5],[62,156,1],[305,210,1.3],[353,298,1],[33,405,1.6],[373,492,1],[67,692,1],[305,745,1.5]].map(([cx,cy,r]) => <circle key={cx} cx={cx} cy={cy} r={r} />)}</g>
      <path d="M318 336v12m-6-6h12" stroke="#bfb5df" strokeWidth="1" opacity=".4"/>
    </svg>
    <div className="teacher-ground" />
  </div>
}


const DRAWER_KEY = "mf.classroom.teacher-drawer.v1"
let nativeWindowQueue: Promise<unknown> = Promise.resolve()
function windowRequest(params: Record<string, unknown>, current = () => true) {
  const next = nativeWindowQueue.catch(() => {}).then(() => current() ? call("app.window", params) : null)
  nativeWindowQueue = next.catch(() => {})
  return next
}
type DrawerState = { open: boolean; width: number; view: ModelView }
function drawerWidth(value: number) {
  const page = document.querySelector(".class-page")
  const max = Math.max(32, Math.min(520, (page?.clientWidth || innerWidth) * .45))
  return Math.max(Math.min(240, max), Math.min(value, max))
}
function savedDrawer(): DrawerState {
  const fallback: DrawerState = { open: false, width: 320, view: { scale: 1, panX: 0, panY: 0 } }
  try {
    const saved = JSON.parse(localStorage.getItem(DRAWER_KEY) || "null")
    if (saved && typeof saved.open === "boolean" && [saved.width, saved.view?.scale, saved.view?.panX, saved.view?.panY].every(Number.isFinite))
      return { open: saved.open, width: saved.width, view: saved.view }
  } catch { /* Optional UI preferences never block a lesson. */ }
  return fallback
}
/** In-flow right rail, never an overlay over classroom controls. */
export const TeacherWindow = React.forwardRef<TeacherStageHandle, {
  children: React.ReactNode; reducedMotion?: boolean; speaking?: boolean; pointKey?: string;
}>(function TeacherWindow({ children, reducedMotion, speaking, pointKey }, ref) {
  const [state, setState] = React.useState(savedDrawer)
  const desired = React.useRef(state)
  const revision = React.useRef(0)
  const mounted = React.useRef(true)
  const [ready, setReady] = React.useState(false)
  const [mode, setMode] = React.useState("internal")
  const [hint, setHint] = React.useState("")
  const [, measurePage] = React.useReducer(v => v + 1, 0)
  const drag = React.useRef<{ id: number; x: number; width: number } | null>(null)
  React.useEffect(() => { try { localStorage.setItem(DRAWER_KEY, JSON.stringify(state)) } catch { /* optional */ } }, [state])
  React.useEffect(() => {
    mounted.current = true
    windowRequest({ action:"teacher_open", restore:true, ...desired.current }).then(result => {
      if (!mounted.current) return
      const saved = result?.teacher
      if (saved) {
        desired.current = {open:!!saved.open,width:saved.width,view:saved.view}
        setState(desired.current); setMode(saved.mode)
      }
      setReady(true)
    }).catch(() => { if (mounted.current) {setReady(true);setHint("老师栏暂时无法展开，请重试。")} })
    const off = on("teacher_window", value => { if (mounted.current) setMode(value.mode) })
    return () => {
      mounted.current = false; revision.current++; off()
      void windowRequest({action:"teacher_close",preserve:true}).catch(() => {})
    }
  }, [])
  const change = (next: DrawerState) => {
    desired.current = next
    const version = ++revision.current
    void windowRequest({action:next.open?"teacher_open":"teacher_close",width:next.width,view:next.view},
      () => mounted.current && revision.current === version).then(result => {
        if (!result || !mounted.current || revision.current !== version) return
        setState(next);setMode(result.teacher?.mode || "internal");setHint("")
      }).catch(() => { if (mounted.current) setHint("老师栏暂时无法调整，请重试。") })
  }
  React.useLayoutEffect(() => {
    const page = document.querySelector<HTMLElement>(".class-page")
    if (!page) return
    const observer = new ResizeObserver(measurePage)
    observer.observe(page)
    return () => observer.disconnect()
  }, [])
  const shownWidth = mode === "external" ? state.width : drawerWidth(state.width)
  React.useLayoutEffect(() => {
    const page = document.querySelector<HTMLElement>(".class-page")
    page?.style.setProperty("--teacher-width", `${state.open ? shownWidth : 32}px`)
    return () => { page?.style.removeProperty("--teacher-width") }
  }, [state.open, shownWidth])
  const end = () => { drag.current = null }
  return <aside className={`teacher-rail ${state.open ? "open" : "closed"}`} aria-label="课堂老师" data-window-mode={mode}
    style={{ "--drawer-width": `${shownWidth}px` } as React.CSSProperties}>
    {!state.open && <button className="teacher-edge" aria-label="展开老师" title={hint || "展开老师"} disabled={!ready}
      onClick={() => change({...desired.current,open:true})}>
      <PanelRightOpen size={18} /><span>展开老师</span><ChevronLeft size={14} />
    </button>}
    <section className="teacher-panel" aria-label="老师抽屉" aria-hidden={!state.open} inert={!state.open ? true : undefined}>
      <header className="teacher-panel-head"><b>椿老师</b><button className="icon-btn" aria-label="收起老师" onClick={() => change({...desired.current,open:false})}><PanelRightClose size={18} /></button></header>
      <div className="teacher-panel-scene"><TeacherBackdrop /><TeacherStage ref={ref} paused={!state.open} visible={state.open} interactive
        reducedMotion={reducedMotion} speaking={speaking} pointKey={pointKey}
        view={state.view} onViewChange={(view) => change({...desired.current,view})} /></div>
      {hint && <small role="status" className="teacher-window-notice">{hint}</small>}
      <div className="teacher-panel-controls">{children}<small>拖动模型 · 滚轮缩放 · 双击恢复</small></div>
      {state.open && <span role="slider" tabIndex={0} aria-label="调整老师栏宽度" aria-valuemin={mode === "external" ? 240 : drawerWidth(0)} aria-valuemax={mode === "external" ? 520 : drawerWidth(520)} aria-valuenow={Math.round(shownWidth)}
        className="teacher-panel-resize" title="拖动调整老师栏宽度"
        onPointerDown={(e) => { if (e.button !== 0) return; drag.current = {id:e.pointerId,x:e.screenX,width:shownWidth};e.currentTarget.setPointerCapture(e.pointerId);e.preventDefault() }}
        onPointerMove={(e) => { const d = drag.current;if (d && d.id === e.pointerId) change({...desired.current,width:mode === "external" ? Math.max(240,Math.min(520,d.width+d.x-e.screenX)) : drawerWidth(d.width+d.x-e.screenX)}) }}
        onPointerUp={end} onPointerCancel={end} onLostPointerCapture={end}
        onKeyDown={(e) => { if (e.key === "ArrowLeft" || e.key === "ArrowRight") {e.preventDefault();const width=desired.current.width+(e.key === "ArrowLeft"?20:-20);change({...desired.current,width:mode === "external"?Math.max(240,Math.min(520,width)):drawerWidth(width)})} }}>
        <ChevronLeft size={12} /><ChevronRight size={12} />
      </span>}
    </section>
  </aside>
})
