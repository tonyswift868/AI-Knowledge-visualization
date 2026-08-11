import { useEffect, useMemo, useRef, useState } from "react"
import { TeacherPresentationController, type TeacherPresentationControllerOptions } from "./TeacherPresentationController"
import { Teacher2DToolbar, teacherSkinLabel } from "./Teacher2DToolbar"
import { DEFAULT_TEACHER_MANIFEST_URL } from "./teacherPresentationManifest"
import type { TeacherPresentationState, TeacherRendererSnapshot } from "./teacherPresentationTypes"
import "./teacherPresentation.css"
import "./teacherPresentationWarm.css"
import "./imageTeacher.css"
import "./layeredTeacher.css"

const initialSnapshot: TeacherRendererSnapshot = {
  status: "loading", renderer: "image2d", semanticState: "loading", animations: [], skins: [], canvasWidth: 0, canvasHeight: 0, webgl: false, loadedTextures: [],
}

const stateText: Record<TeacherPresentationState, string> = {
  loading: "加载中", idle: "待机", greeting: "问候", thinking: "思考", talking: "回应", explaining: "讲解", questioning: "提问", praising: "答对", encouraging: "鼓励", confused: "困惑", ending: "结束", error: "错误", hidden: "已收起",
}

type CameraPreset = "face" | "half" | "full"
type TeacherCamera = { preset: CameraPreset | "custom"; zoom: number; x: number; y: number }
export type Teacher2DRuntime = "image2d" | "layered2d" | "live2d" | "dragonbones" | "error" | "loading"

const cameraPresets: Record<CameraPreset, Omit<TeacherCamera, "preset">> = {
  // At 2.3x the model's head already travels well above its visual centre.
  // A 1.04 viewport offset pushed Live2D faces to the bottom edge; 0.7 keeps
  // the eyes centred while retaining the close portrait crop.
  face: { zoom: 2.3, x: 0, y: .7 },
  half: { zoom: 1.52, x: 0, y: .44 },
  full: { zoom: 1, x: 0, y: 0 },
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
  skin?: string
  onSkinChange?: (skin: string) => void
  onRuntimeChange?: (runtime: Teacher2DRuntime) => void
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
  const defaultCameraPreset: CameraPreset = compact ? "half" : "full"
  const [camera, setCamera] = useState<TeacherCamera>(() => ({ preset: defaultCameraPreset, ...cameraPresets[defaultCameraPreset] }))
  const cameraRef = useRef(camera)
  const [selectedAnimation, setSelectedAnimation] = useState("")
  const [selectedSkin, setSelectedSkin] = useState(skin || "")
  const normalizedState = normalizePresentationState(state)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const options: TeacherPresentationControllerOptions = { onSnapshot: next => {
      setSnapshot(next)
      if (next.status === "ready") {
        onRuntimeChange?.(
          next.renderer === "dragonbones2d"
            ? "dragonbones"
            : next.renderer === "live2d2d"
              ? "live2d"
            : next.renderer === "layered2d"
              ? "layered2d"
              : "image2d",
        )
      }
      if (next.status === "loading") onRuntimeChange?.("loading")
      if (next.status === "error") onRuntimeChange?.("error")
    } }
    const controller = controllerFactory ? controllerFactory(options) : new TeacherPresentationController(options)
    controllerRef.current = controller
    void controller.mount(host, manifestUrl).then(() => {
      controller.setScale(cameraRef.current.zoom)
      controller.setOffset(cameraRef.current.x, cameraRef.current.y)
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
  useEffect(() => {
    cameraRef.current = camera
    controllerRef.current?.setScale(camera.zoom)
    controllerRef.current?.setOffset(camera.x, camera.y)
  }, [camera])
  useEffect(() => { controllerRef.current?.setMirror(mirrored) }, [mirrored])
  useEffect(() => { if (paused) controllerRef.current?.pause(); else controllerRef.current?.resume() }, [paused])
  useEffect(() => {
    skinRef.current = skin
    if (!skin) return
    if (snapshot.skins.length && !snapshot.skins.includes(skin)) return
    setSelectedSkin(skin)
    controllerRef.current?.setSkin(skin)
  }, [skin, snapshot.skins])
  useEffect(() => {
    if (snapshot.status !== "ready" || !snapshot.skins.length) return
    const requested = skinRef.current
    const next = requested && snapshot.skins.includes(requested)
      ? requested
      : snapshot.skins.includes(selectedSkin)
        ? selectedSkin
        : snapshot.skins[0]
    if (next !== selectedSkin) setSelectedSkin(next)
    controllerRef.current?.setSkin(next)
  }, [selectedSkin, snapshot.skins, snapshot.status])

  const title = useMemo(() => snapshot.status === "error" ? "2D 教师资源暂不可用" : `${teacherName} · ${stateText[normalizedState]}`, [normalizedState, snapshot.status, teacherName])
  const collapsedClass = collapsed ? " collapsed" : ""
  const selectCameraPreset = (preset: CameraPreset) => setCamera({ preset, ...cameraPresets[preset] })
  const moveCamera = (x: number, y: number) => setCamera(current => ({ ...current, preset: "custom", x: Math.max(-1.2, Math.min(1.2, current.x + x)), y: Math.max(-1.4, Math.min(1.4, current.y + y)) }))
  const resetCamera = () => setCamera({ preset: defaultCameraPreset, ...cameraPresets[defaultCameraPreset] })
  const changeSkin = (value: string) => {
    if (!snapshot.skins.includes(value)) return
    setSelectedSkin(value)
    onSkinChange?.(value)
    controllerRef.current?.setSkin(value)
  }
  return <section className={`teacher-2d-stage${compact ? " compact" : ""}${collapsedClass}`} data-testid="teacher-2d-stage" data-renderer-kind={snapshot.renderer} data-runtime-status={snapshot.status} data-semantic-state={normalizedState} data-camera-preset={camera.preset} data-camera-zoom={camera.zoom.toFixed(2)} data-camera-x={camera.x.toFixed(2)} data-camera-y={camera.y.toFixed(2)} aria-label={`${teacherName} 2D 教师舞台`}>
    <div className="teacher-2d-heading"><span>{snapshot.renderer === "live2d2d" ? "LIVE2D CUBISM TEACHER" : snapshot.renderer === "layered2d" ? "OPEN-SOURCE LAYERED TEACHER" : snapshot.renderer === "image2d" ? "V9 LIGHTWEIGHT TEACHER" : "OPEN-SOURCE 2D TEACHER"}</span><h3>{title}</h3><p>{snapshot.status === "error" ? snapshot.error : "视线、呼吸、发丝与四肢会独立联动；点击角色不同部位可触发动作。"}</p></div>
    <div className="teacher-2d-host" ref={hostRef} aria-label={snapshot.renderer === "live2d2d" ? "Live2D Cubism 动态教师画布" : snapshot.renderer === "layered2d" ? "PixiJS 分层动态教师画布" : snapshot.renderer === "image2d" ? "V9 轻量动态教师画布" : "DragonBones Shizuku 画布"} />
    {snapshot.status === "loading" && <div className="teacher-2d-overlay" role="status">正在加载 2D 教师…</div>}
    {snapshot.status === "error" && <div className="teacher-2d-overlay teacher-2d-error" role="status">{snapshot.error || "模型加载失败；学习内容仍可继续使用。"}</div>}
    {!collapsed && snapshot.status === "ready" && snapshot.skins.length > 1 && <div className="teacher-2d-form-switch" role="group" aria-label="教师双形态切换">
      {snapshot.skins.map(name => <button type="button" key={name} data-testid={`teacher-form-${name}`} className={selectedSkin === name ? "active" : ""} aria-pressed={selectedSkin === name} onClick={() => changeSkin(name)}>{teacherSkinLabel(name)}</button>)}
    </div>}
    {!collapsed && <div className="teacher-2d-camera" aria-label="教师镜头控制">
      <div className="teacher-2d-camera-presets" role="group" aria-label="教师构图预设">
        <button type="button" className={camera.preset === "face" ? "active" : ""} aria-pressed={camera.preset === "face"} onClick={() => selectCameraPreset("face")}>脸部</button>
        <button type="button" className={camera.preset === "half" ? "active" : ""} aria-pressed={camera.preset === "half"} onClick={() => selectCameraPreset("half")}>半身</button>
        <button type="button" className={camera.preset === "full" ? "active" : ""} aria-pressed={camera.preset === "full"} onClick={() => selectCameraPreset("full")}>全身</button>
      </div>
      <div className="teacher-2d-camera-pan" role="group" aria-label="微调教师位置">
        <button type="button" aria-label="教师向左移动" title="向左" onClick={() => moveCamera(-.06, 0)}>←</button>
        <button type="button" aria-label="教师向上移动" title="向上" onClick={() => moveCamera(0, -.06)}>↑</button>
        <button type="button" aria-label="教师向下移动" title="向下" onClick={() => moveCamera(0, .06)}>↓</button>
        <button type="button" aria-label="教师向右移动" title="向右" onClick={() => moveCamera(.06, 0)}>→</button>
      </div>
    </div>}
    <Teacher2DToolbar collapsed={collapsed} paused={paused} mirrored={mirrored} debug={debug} animations={snapshot.animations} selectedAnimation={selectedAnimation || snapshot.currentAnimation || ""} skins={snapshot.skins} selectedSkin={selectedSkin} onCollapsedChange={setCollapsed} onPausedChange={value => { setPaused(value); value ? controllerRef.current?.pause() : controllerRef.current?.resume() }} onMirroredChange={value => { setMirrored(value); controllerRef.current?.setMirror(value) }} onDebugChange={setDebug} onZoom={delta => setCamera(current => ({ ...current, preset: "custom", zoom: Math.max(.5, Math.min(2.8, current.zoom + delta)) }))} onReset={() => { resetCamera(); setMirrored(false); setPaused(false); changeSkin(snapshot.skins[0] || "teaching"); controllerRef.current?.reset() }} onAnimationChange={value => { setSelectedAnimation(value); if (value) controllerRef.current?.playAnimation(value, false) }} onSkinChange={changeSkin} />
    {debug && <pre className="teacher-2d-debug" data-testid="teacher-2d-debug">{JSON.stringify({ renderer: snapshot.renderer, armature: snapshot.armature, animation: snapshot.currentAnimation, semanticState: snapshot.semanticState, camera, status: snapshot.status, canvas: [snapshot.canvasWidth, snapshot.canvasHeight], webgl: snapshot.webgl, textures: snapshot.loadedTextures, bounds: snapshot.bounds }, null, 2)}</pre>}
  </section>
}
