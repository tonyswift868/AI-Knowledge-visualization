import { useEffect, useMemo, useRef, useState } from "react"
import { TeacherPresentationController, type TeacherPresentationControllerOptions } from "./TeacherPresentationController"
import { Teacher2DToolbar } from "./Teacher2DToolbar"
import { DEFAULT_TEACHER_MANIFEST_URL } from "./teacherPresentationManifest"
import type { TeacherPresentationState, TeacherRendererSnapshot } from "./teacherPresentationTypes"
import "./teacherPresentation.css"
import "./imageTeacher.css"

const initialSnapshot: TeacherRendererSnapshot = {
  status: "loading", renderer: "image2d", semanticState: "loading", animations: [], skins: [], canvasWidth: 0, canvasHeight: 0, webgl: false, loadedTextures: [],
}

const stateText: Record<TeacherPresentationState, string> = {
  loading: "加载中", idle: "待机", greeting: "问候", thinking: "思考", talking: "回应", explaining: "讲解", questioning: "提问", praising: "答对", encouraging: "鼓励", confused: "困惑", ending: "结束", error: "错误", hidden: "已收起",
}

export function normalizePresentationState(value: string | undefined): TeacherPresentationState {
  if (value === "listening") return "idle"
  if (value && value in stateText) return value as TeacherPresentationState
  return "idle"
}

type Props = {
  teacherName?: string
  state?: TeacherPresentationState | string
  compact?: boolean
  manifestUrl?: string
  skin?: "teaching" | "swimsuit"
  onSkinChange?: (skin: "teaching" | "swimsuit") => void
  onRuntimeChange?: (runtime: "image2d" | "dragonbones" | "error" | "loading") => void
  controllerFactory?: (options: TeacherPresentationControllerOptions) => TeacherPresentationController
}

function initiallyReducedMotion(): boolean {
  return typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches
}

export function Teacher2DStage({ teacherName = "艾莉丝", state = "idle", compact = false, manifestUrl = DEFAULT_TEACHER_MANIFEST_URL, skin, onSkinChange, onRuntimeChange, controllerFactory }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const controllerRef = useRef<TeacherPresentationController | null>(null)
  const skinRef = useRef(skin)
  const [snapshot, setSnapshot] = useState(initialSnapshot)
  const [collapsed, setCollapsed] = useState(false)
  const [paused, setPaused] = useState(initiallyReducedMotion)
  const [mirrored, setMirrored] = useState(false)
  const [debug, setDebug] = useState(false)
  const [zoom, setZoom] = useState(1)
  const [selectedAnimation, setSelectedAnimation] = useState("")
  const [selectedSkin, setSelectedSkin] = useState(skin || "teaching")
  const normalizedState = normalizePresentationState(state)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const options: TeacherPresentationControllerOptions = { onSnapshot: next => {
      setSnapshot(next)
      if (next.status === "ready") onRuntimeChange?.(next.renderer === "image2d" ? "image2d" : "dragonbones")
      if (next.status === "loading") onRuntimeChange?.("loading")
      if (next.status === "error") onRuntimeChange?.("error")
    } }
    const controller = controllerFactory ? controllerFactory(options) : new TeacherPresentationController(options)
    controllerRef.current = controller
    void controller.mount(host, manifestUrl).then(() => {
      if (skinRef.current) controller.setSkin(skinRef.current)
    }).catch(() => undefined)
    return () => {
      controller.dispose()
      if (controllerRef.current === controller) controllerRef.current = null
    }
  }, [controllerFactory, manifestUrl, onRuntimeChange])

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return
    const media = window.matchMedia("(prefers-reduced-motion: reduce)")
    const sync = () => setPaused(media.matches)
    media.addEventListener?.("change", sync)
    return () => media.removeEventListener?.("change", sync)
  }, [])

  useEffect(() => { controllerRef.current?.setState(normalizedState) }, [normalizedState])
  useEffect(() => { controllerRef.current?.setScale(zoom) }, [zoom])
  useEffect(() => { controllerRef.current?.setMirror(mirrored) }, [mirrored])
  useEffect(() => { if (paused) controllerRef.current?.pause(); else controllerRef.current?.resume() }, [paused])
  useEffect(() => {
    skinRef.current = skin
    if (!skin) return
    setSelectedSkin(skin)
    controllerRef.current?.setSkin(skin)
  }, [skin])

  const title = useMemo(() => snapshot.status === "error" ? "2D 教师资源暂不可用" : `${teacherName} · ${stateText[normalizedState]}`, [normalizedState, snapshot.status, teacherName])
  const collapsedClass = collapsed ? " collapsed" : ""
  return <section className={`teacher-2d-stage${compact ? " compact" : ""}${collapsedClass}`} data-testid="teacher-2d-stage" data-renderer-kind={snapshot.renderer} data-runtime-status={snapshot.status} data-semantic-state={normalizedState} aria-label={`${teacherName} 2D 教师舞台`}>
    <div className="teacher-2d-heading"><span>{snapshot.renderer === "image2d" ? "V9 LIGHTWEIGHT TEACHER" : "OPEN-SOURCE 2D TEACHER"}</span><h3>{title}</h3><p>{snapshot.status === "error" ? snapshot.error : "鼠标移动会影响视线，点击角色会触发动作；形态切换带有衔接动画。"}</p></div>
    <div className="teacher-2d-host" ref={hostRef} aria-label={snapshot.renderer === "image2d" ? "V9 轻量动态教师画布" : "DragonBones Shizuku 画布"} />
    {snapshot.status === "loading" && <div className="teacher-2d-overlay" role="status">正在加载 2D 教师…</div>}
    {snapshot.status === "error" && <div className="teacher-2d-overlay teacher-2d-error" role="status">{snapshot.error || "模型加载失败；学习内容仍可继续使用。"}</div>}
    <Teacher2DToolbar collapsed={collapsed} paused={paused} mirrored={mirrored} debug={debug} animations={snapshot.animations} selectedAnimation={selectedAnimation || snapshot.currentAnimation || ""} skins={snapshot.skins} selectedSkin={selectedSkin} onCollapsedChange={setCollapsed} onPausedChange={value => { setPaused(value); value ? controllerRef.current?.pause() : controllerRef.current?.resume() }} onMirroredChange={value => { setMirrored(value); controllerRef.current?.setMirror(value) }} onDebugChange={setDebug} onZoom={delta => setZoom(value => Math.max(.5, Math.min(1.8, value + delta)))} onReset={() => { setZoom(1); setMirrored(false); setPaused(false); setSelectedSkin("teaching"); onSkinChange?.("teaching"); controllerRef.current?.setSkin("teaching"); controllerRef.current?.reset() }} onAnimationChange={value => { setSelectedAnimation(value); if (value) controllerRef.current?.playAnimation(value, false) }} onSkinChange={value => { const next = value === "swimsuit" ? "swimsuit" : "teaching"; setSelectedSkin(next); onSkinChange?.(next); controllerRef.current?.setSkin(next) }} />
    {debug && <pre className="teacher-2d-debug" data-testid="teacher-2d-debug">{JSON.stringify({ renderer: snapshot.renderer, armature: snapshot.armature, animation: snapshot.currentAnimation, semanticState: snapshot.semanticState, status: snapshot.status, canvas: [snapshot.canvasWidth, snapshot.canvasHeight], webgl: snapshot.webgl, textures: snapshot.loadedTextures, bounds: snapshot.bounds }, null, 2)}</pre>}
  </section>
}
