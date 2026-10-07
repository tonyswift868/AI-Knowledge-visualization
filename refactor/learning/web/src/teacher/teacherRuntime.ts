// Extracted loading/layout/release compatibility from the mature classroom
// Live2DTeacherAdapter. Lesson states, fake speech and its old pose registry
// deliberately do not belong to this rendering component.
import * as PIXI from "pixi.js"
// Pixi's bundled no-eval polyfill preserves the app's strict script CSP.
import "pixi.js/unsafe-eval"

export type Reaction = "nod" | "shake" | "think" | "happy"
export type ReactionEvent = { reaction: Reaction; phase: "started" | "finished" | "cancelled"; source: "parameters" }
export type TeacherManifest = { id: "tsubaki-live2d"; displayName: string; skeleton: string;
  live2d: { coreScript: string; textureLod: "single-auto" } }
export type Capabilities = { mouth: boolean; blink: boolean; breath: boolean; reactions: Record<Reaction, boolean> }
export type ModelView = { scale: number; panX: number; panY: number }
export type StageSnapshot = { frames: number; fps: number; active: boolean; mouth: number;
  reaction: Reaction | null; parameters: Record<string, number>; width: number; height: number; view: ModelView }

let coreLoading: Promise<void> | null = null
let engineLoading: Promise<typeof import("untitled-pixi-live2d-engine/cubism")> | null = null

async function engine(coreUrl: string) {
  if (!(window as any).Live2DCubismCore) {
    if (!/^mentorflow:\/\/assets\/live2d\/[a-f0-9]+\/runtime\/core\.js$/.test(coreUrl)) throw new Error("老师运行资源地址无效。")
    if (!coreLoading) coreLoading = new Promise<void>((resolve, reject) => {
      const script = document.createElement("script")
      script.src = coreUrl
      script.onload = () => (window as any).Live2DCubismCore ? resolve() : reject(new Error("老师形象运行资源无法初始化。"))
      script.onerror = () => { script.remove(); reject(new Error("老师形象运行资源无法加载。")) }
      document.head.appendChild(script)
    }).catch((e) => { coreLoading = null; throw e })
    await coreLoading
  }
  if (!engineLoading) engineLoading = import("untitled-pixi-live2d-engine/cubism").then((runtime) => {
    PIXI.extensions.add(runtime.Live2DPlugin)
    runtime.configureCubismSDK({ memorySizeMB: 64 })
    return runtime
  }).catch((e) => { engineLoading = null; throw e })
  return engineLoading
}

function releaseModel(model: any) {
  // Engine 1.3.5 destroys the moc before its live model. Preserve the existing
  // adapter's Cubism release ordering fix (otherwise unmount can assert).
  const internal = model.internalModel
  const moc = internal.__moc
  if (moc?.deleteModel && moc?.release && internal.coreModel?.release) {
    const core = internal.coreModel
    const release = core.release.bind(core)
    internal.__moc = undefined
    core.release = () => { core.release = release; moc.deleteModel(core) }
    try { model.destroy({ children: true }) } finally { moc.release() }
  } else model.destroy({ children: true })
}

const IDS = ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen",
  "ParamMouthOpenY", "ParamMouthForm", "ParamBreath", "ParamBrowLY", "ParamBrowRY"]
const durations: Record<Reaction, number> = { nod: .95, shake: 1.15, think: 1.8, happy: 1.65 }
const clamp = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v))

export class TeacherRuntime {
  private app: PIXI.Application | null = null
  private model: any = null
  private parameters = new Map<string, { id: any; min: number; max: number }>()
  private disposed = false
  private active = false
  private requestedActive = true
  private resizeObserver: ResizeObserver | null = null
  private elapsed = 0
  private dt = 0
  private mouth = 0
  private mouthTarget = 0
  private mouthAt = 0
  private action: { name: Reaction; elapsed: number } | null = null
  private frames = 0
  private fps = 0
  private fpsFrames = 0
  private fpsAt = 0
  private applied: Record<string, number> = {}
  private view: ModelView = { scale: 1, panX: 0, panY: 0 }
  capabilities: Capabilities = { mouth: false, blink: false, breath: false,
    reactions: { nod: false, shake: false, think: false, happy: false } }

  constructor(private host: HTMLDivElement, private onReaction?: (event: ReactionEvent) => void) {}

  async load(manifest: TeacherManifest) {
    if (manifest.id !== "tsubaki-live2d") throw new Error("当前仅支持椿老师形象。")
    const runtime = await engine(manifest.live2d.coreScript)
    if (this.disposed) return
    const app = new PIXI.Application()
    await app.init({ backgroundAlpha: 0, antialias: true, autoStart: false,
      preference: "webgl", resolution: Math.min(window.devicePixelRatio || 1, 2), autoDensity: true })
    if (this.disposed) { app.destroy(true); return }
    this.app = app
    app.ticker.maxFPS = 60
    this.host.replaceChildren(app.canvas)
    app.canvas.setAttribute("aria-label", "椿老师形象")
    const model = await runtime.Live2DModel.from(manifest.skeleton, {
      autoUpdate: true, autoHitTest: false, autoFocus: false, ticker: app.ticker,
      // SDK default breathing also turns the head. Only animate breath/eyes
      // below; idle never selects a gesture or generates a fake mouth movement.
      breathDepth: 0, eyeBlink: false, motionPreload: runtime.MotionPreloadStrategy.NONE,
      // Use Chromium's image loader for the private scheme; no Blob Worker or
      // relaxed worker-src policy is needed for the three model textures.
      textureOptions: { lod: manifest.live2d.textureLod, preferCreateImageBitmap: false },
    }) as any
    if (this.disposed) { releaseModel(model); return }
    this.model = model
    // Pixi 8.13 moved its GPU cache; engine 1.3.5 still tests the old slot.
    // Include lazily generated LOD textures, not just the original atlases:
    // otherwise the engine silently falls back to unfiltered 4096px textures.
    const upload = model.uploadTextureForRender
    model.uploadTextureForRender = function(renderer: any, texture: any, update: boolean) {
      texture.source._gpuData ??= Object.create(null)
      return upload.call(this, renderer, texture, update)
    }
    const internal = model.internalModel, core = internal.coreModel
    const count = core.getParameterCount()
    for (const name of IDS) {
      const id = internal.idManager.getId(name), index = core.getParameterIndex(id)
      // Cubism returns a virtual index for nonexistent IDs; >= 0 alone is insufficient.
      if (index >= 0 && index < count) this.parameters.set(name, {
        id, min: core.getParameterMinimumValue(index), max: core.getParameterMaximumValue(index),
      })
    }
    const has = (id: string) => this.parameters.has(id)
    this.capabilities = { mouth: has("ParamMouthOpenY"), blink: has("ParamEyeLOpen") && has("ParamEyeROpen"),
      breath: has("ParamBreath"), reactions: { nod: has("ParamAngleY"), shake: has("ParamAngleX"),
        think: has("ParamAngleZ"), happy: has("ParamMouthForm") } }
    model.anchor.set(.5, .5)
    model.eventMode = "none"
    internal.motionManager.stopAllMotions()
    internal.on("beforeModelUpdate", this.beforeModelUpdate)
    app.stage.addChild(model)
    app.ticker.add(this.tick, undefined, PIXI.UPDATE_PRIORITY.HIGH)
    this.resizeObserver = new ResizeObserver(this.resize)
    this.resizeObserver.observe(this.host)
    this.resize()
    this.setActive(this.requestedActive)
  }

  setMouthLevel(value: number, transient = true) {
    this.mouthTarget = this.active && Number.isFinite(value) ? clamp(value, 0, 1) : 0
    this.mouthAt = transient ? performance.now() : Infinity
    // TTS's explicit silence/stop frame closes immediately on the next render;
    // positive levels use a short attack smoothing window, never invented syllables.
    if (this.mouthTarget === 0) this.mouth = 0
  }

  react(name: Reaction): boolean {
    if (!this.active || !this.capabilities.reactions[name]) return false
    this.stopReaction()
    this.action = { name, elapsed: 0 }
    this.onReaction?.({ reaction: name, phase: "started", source: "parameters" })
    return true
  }

  stopReaction() {
    const old = this.action
    this.action = null
    if (old) this.onReaction?.({ reaction: old.name, phase: "cancelled", source: "parameters" })
  }

  setActive(value: boolean) {
    this.requestedActive = value
    this.active = value && !!this.model && !this.disposed
    if (this.active) {
      this.fpsAt = performance.now(); this.fpsFrames = 0
      this.app?.start()
    } else {
      this.app?.stop()
      this.mouth = this.mouthTarget = 0
      this.stopReaction()
    }
  }

  setView(view: ModelView) {
    this.view = { scale: clamp(view.scale, .5, 2.5), panX: clamp(view.panX, -1, 1), panY: clamp(view.panY, -1, 1) }
    this.resize()
  }

  /** One static head crop; a collapsed stage never needs a running ticker. */
  thumbnail(): string | null {
    if (!this.app || !this.model || this.disposed) return null
    const view = this.view
    try {
      this.setView({ scale: 1, panX: 0, panY: 0 })
      this.app.render()
      const source = this.app.canvas
      const canvas = document.createElement("canvas")
      canvas.width = canvas.height = 96
      const edge = Math.min(source.width * .55, source.height * .4)
      canvas.getContext("2d")!.drawImage(source, (source.width - edge) / 2, source.height * .07, edge, edge, 0, 0, 96, 96)
      return canvas.toDataURL("image/png")
    } catch { return null }
    finally { this.setView(view) }
  }

  private resize = () => {
    if (!this.app || !this.model || this.disposed) return
    const w = Math.max(1, this.host.clientWidth), h = Math.max(1, this.host.clientHeight)
    const internal = this.model.internalModel
    const scale = Math.min(w / internal.originalWidth, h / internal.originalHeight) * .94
    this.app.renderer.resize(w, h)
    this.model.scale.set(scale * this.view.scale)
    this.model.position.set(w / 2 + this.view.panX * w, h / 2 + this.view.panY * h)
  }

  private tick = (ticker: PIXI.Ticker) => {
    this.dt = clamp(ticker.deltaMS / 1000, 0, .06)
    this.elapsed += this.dt
    this.frames++; this.fpsFrames++
    const now = performance.now()
    if (now - this.fpsAt >= 1000) {
      this.fps = this.fpsFrames * 1000 / (now - this.fpsAt)
      this.fpsAt = now; this.fpsFrames = 0
    }
    if (now - this.mouthAt > 250) this.mouthTarget = this.mouth = 0 // lost stream cannot leave an open mouth
    this.mouth += (this.mouthTarget - this.mouth) * (1 - Math.exp(-this.dt * 32))
    if (this.action) {
      this.action.elapsed += this.dt
      if (this.action.elapsed >= durations[this.action.name]) {
        const name = this.action.name
        this.action = null
        this.onReaction?.({ reaction: name, phase: "finished", source: "parameters" })
      }
    }
  }

  private write(name: string, value: number) {
    const p = this.parameters.get(name)
    if (!p) return
    value = clamp(value, p.min, p.max)
    this.model.internalModel.coreModel.setParameterValueById(p.id, value, 1)
    this.applied[name] = value
  }

  private beforeModelUpdate = () => {
    const action = this.action
    const u = action ? clamp(action.elapsed / durations[action.name], 0, 1) : 0
    const envelope = Math.sin(Math.PI * u) ** 2 // zero velocity at both ends
    const x = action?.name === "shake" ? 5 * Math.sin(2 * Math.PI * u) * envelope : 0
    const y = action?.name === "nod" ? -5 * envelope : action?.name === "happy" ? -1.2 * envelope : 0
    const z = action?.name === "think" ? 4.5 * envelope : 0
    const smile = action?.name === "happy" ? .7 * envelope : 0
    const blinkPhase = this.elapsed % 4.3
    const blink = blinkPhase < .17 ? Math.abs(blinkPhase - .085) / .085 : 1
    this.write("ParamAngleX", x); this.write("ParamAngleY", y); this.write("ParamAngleZ", z)
    this.write("ParamEyeLOpen", blink * (1 - smile * .13)); this.write("ParamEyeROpen", blink * (1 - smile * .13))
    this.write("ParamMouthOpenY", this.mouth); this.write("ParamMouthForm", smile)
    this.write("ParamBrowLY", action?.name === "think" ? .1 * envelope : 0)
    this.write("ParamBrowRY", action?.name === "think" ? .1 * envelope : 0)
    this.write("ParamBreath", .45 + .18 * Math.sin(this.elapsed * 1.4))
  }

  snapshot(): StageSnapshot {
    return { frames: this.frames, fps: this.fps, active: this.active, mouth: this.mouth,
      reaction: this.action?.name ?? null, parameters: { ...this.applied },
      width: this.host.clientWidth, height: this.host.clientHeight, view: { ...this.view } }
  }

  dispose() {
    this.disposed = true
    this.setActive(false)
    this.resizeObserver?.disconnect()
    if (this.model) {
      this.model.internalModel.off("beforeModelUpdate", this.beforeModelUpdate)
      releaseModel(this.model)
      this.model = null
    }
    this.app?.ticker.remove(this.tick)
    this.app?.destroy(true)
    this.app = null
    this.host.replaceChildren()
  }
}
