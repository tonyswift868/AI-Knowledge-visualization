import * as PIXI from "pixi.js"
import type {
  DragonBonesArmatureDisplay,
  DragonBonesPixiFactory,
  DragonBonesPixiFactoryClass,
  DragonBonesRuntimeGlobal,
} from "../../../../vendor/dragonbones/dragonBones"
import { resolveTeacherAnimation } from "../teacherPresentationManifest"
import type { Teacher2DManifest, Teacher2DRenderer, TeacherPresentationState } from "../teacherPresentationTypes"

const RUNTIME_URL = "/vendor/dragonbones/dragonBones.js"
let runtimePromise: Promise<DragonBonesRuntimeGlobal> | null = null
let instanceSequence = 0

export function shouldStartTeacherAnimation(currentName: string, currentLoop: boolean, nextName: string, nextLoop: boolean): boolean {
  return currentName !== nextName || currentLoop !== nextLoop
}

function runtimeGlobal(): DragonBonesRuntimeGlobal | undefined {
  return (window as Window & { dragonBones?: DragonBonesRuntimeGlobal }).dragonBones
}

function loadDragonBonesRuntime(): Promise<DragonBonesRuntimeGlobal> {
  const existing = runtimeGlobal()
  if (existing) return Promise.resolve(existing)
  if (runtimePromise) return runtimePromise

  // DragonBonesJS' official Pixi runtime is a classic script and expects a
  // global PIXI namespace. It is vendored locally; no CDN is involved.
  window.PIXI = PIXI
  runtimePromise = new Promise<DragonBonesRuntimeGlobal>((resolve, reject) => {
    const current = document.querySelector<HTMLScriptElement>(`script[data-dragonbones-runtime="${RUNTIME_URL}"]`)
    if (current) {
      current.addEventListener("load", () => runtimeGlobal() ? resolve(runtimeGlobal()!) : reject(new Error("DragonBones 运行时未暴露 dragonBones")), { once: true })
      current.addEventListener("error", () => reject(new Error("DragonBones 本地运行时加载失败")), { once: true })
      return
    }
    const script = document.createElement("script")
    script.src = RUNTIME_URL
    script.async = true
    script.dataset.dragonbonesRuntime = RUNTIME_URL
    script.onload = () => runtimeGlobal() ? resolve(runtimeGlobal()!) : reject(new Error("DragonBones 运行时未暴露 dragonBones"))
    script.onerror = () => reject(new Error("DragonBones 本地运行时加载失败"))
    document.head.appendChild(script)
  }).catch(error => {
    runtimePromise = null
    throw error
  })
  return runtimePromise
}

type Texture = PIXI.Texture
type SkeletonData = {
  version?: string
  armature?: Array<{
    name?: string
    animation?: Array<{ name?: string }>
    skin?: Array<{ name?: string }>
    aabb?: { x: number; y: number; width: number; height: number }
  }>
  textureAtlas?: Array<{ imagePath?: string }>
}

export type DragonBonesAdapterOptions = {
  host: HTMLElement
  onSnapshot?: (snapshot: { currentAnimation?: string; animations: string[]; skins: string[]; loadedTextures: string[]; bounds?: { x: number; y: number; width: number; height: number }; canvasWidth: number; canvasHeight: number; webgl: boolean }) => void
  onError?: (error: Error) => void
}

export class DragonBonesTeacherAdapter implements Teacher2DRenderer {
  private readonly host: HTMLElement
  private readonly onSnapshot?: DragonBonesAdapterOptions["onSnapshot"]
  private readonly onError?: DragonBonesAdapterOptions["onError"]
  private app: PIXI.Application | null = null
  private display: DragonBonesArmatureDisplay | null = null
  private factory: DragonBonesPixiFactory | null = null
  private factoryClass: DragonBonesPixiFactoryClass | null = null
  private manifest: Teacher2DManifest | null = null
  private dataName = ""
  private loadedTextureUrls: string[] = []
  private textures: Texture[] = []
  private animationNames: string[] = []
  private skinNames: string[] = []
  private currentAnimation = ""
  private currentLoop = true
  private currentAnimationDurationMs = 1300
  private currentSemantic: TeacherPresentationState = "loading"
  private scale = 1
  private offsetX = 0
  private offsetY = 0
  private mirrored = false
  private paused = false
  private userPaused = false
  private pageHidden = false
  private pointer = { x: 0, y: 0, active: false }
  private gestureIndex = 0
  private gestureTimer: number | undefined
  private resizeObserver: ResizeObserver | undefined
  private readonly pointerMove = (event: PointerEvent) => this.updatePointer(event)
  private readonly pointerTap = () => this.playTapGesture()
  private readonly visibilityChange = () => {
    this.pageHidden = document.hidden
    if (this.pageHidden) {
      this.paused = true
      this.app?.ticker.stop()
    } else if (!this.userPaused) {
      this.paused = false
      this.app?.ticker.start()
    }
  }
  private readonly tick = (ticker: PIXI.Ticker) => {
    if (!this.factoryClass || !this.display || this.paused) return
    this.factoryClass.advanceTime(ticker.deltaMS / 1000)
    this.updatePointerAnimation()
  }

  constructor(options: DragonBonesAdapterOptions) {
    this.host = options.host
    this.onSnapshot = options.onSnapshot
    this.onError = options.onError
  }

  async load(manifest: Teacher2DManifest): Promise<void> {
    this.unload()
    this.manifest = manifest
    this.dataName = `${manifest.id}-${++instanceSequence}`
    try {
      const runtime = await loadDragonBonesRuntime()
      const skeletonResponse = await fetch(manifest.skeleton)
      if (!skeletonResponse.ok) throw new Error(`DragonBones 骨架加载失败：HTTP ${skeletonResponse.status}`)
      const skeleton = await skeletonResponse.json() as SkeletonData
      const textureResults = await Promise.all(manifest.textures.map(async url => {
        const response = await fetch(url, { method: "HEAD" })
        if (!response.ok) throw new Error(`DragonBones 纹理加载失败：${url} HTTP ${response.status}`)
        return { url, texture: await PIXI.Assets.load<Texture>(url) }
      }))
      this.loadedTextureUrls = textureResults.map(item => item.url)
      this.textures = textureResults.map(item => item.texture)

      const armatureData = skeleton.armature?.find(item => item.name === manifest.armature) || skeleton.armature?.[0]
      if (!armatureData) throw new Error(`DragonBones 找不到 armature：${manifest.armature}`)
      const advertisedPages = skeleton.textureAtlas?.map(page => page.imagePath || "") || []
      if (advertisedPages.length > this.textures.length) {
        console.warn("[Teacher2D] skeleton advertises more texture pages than the official asset directory ships", { advertisedPages, loaded: this.loadedTextureUrls })
      }

      const factoryClass = runtime.PixiFactory
      // Use the application ticker instead of DragonBones' global shared
      // ticker so hidden/unmounted stages can pause without affecting others.
      factoryClass.useSharedTicker = false
      const factory = factoryClass.factory
      factory.parseDragonBonesData(skeleton, this.dataName)
      factory.updateTextureAtlases(this.textures, this.dataName)
      this.factoryClass = factoryClass
      this.factory = factory
      this.display = factory.buildArmatureDisplay(manifest.armature, this.dataName) || null
      if (!this.display) throw new Error(`DragonBones 无法构建 armature：${manifest.armature}`)

      this.animationNames = [...this.display.animation.animationNames]
      this.skinNames = (armatureData.skin || []).map(skin => skin.name || "default")
      if (this.skinNames.length === 0) this.skinNames = ["default"]
      this.scale = manifest.initialScale || 1
      this.display.eventMode = "none"

      const app = new PIXI.Application()
      await app.init({ backgroundAlpha: 0, antialias: true, autoStart: true, resizeTo: this.host })
      this.app = app
      this.host.replaceChildren(app.canvas)
      app.stage.addChild(this.display)
      app.ticker.add(this.tick)
      app.canvas.addEventListener("pointermove", this.pointerMove)
      app.canvas.addEventListener("click", this.pointerTap)
      document.addEventListener("visibilitychange", this.visibilityChange)
      this.resizeObserver = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(() => this.resize(this.host.clientWidth, this.host.clientHeight))
      this.resizeObserver?.observe(this.host)
      this.resize(this.host.clientWidth, this.host.clientHeight)
      this.playSemanticState("idle")

      const aabb = armatureData.aabb || this.display.armature.armatureData.aabb
      const snapshot = {
        currentAnimation: this.currentAnimation,
        animations: this.animationNames,
        skins: this.skinNames,
        loadedTextures: this.loadedTextureUrls,
        bounds: aabb,
        canvasWidth: this.host.clientWidth,
        canvasHeight: this.host.clientHeight,
        webgl: Boolean(app.renderer && app.renderer.type !== undefined),
      }
      console.info("[Teacher2D] DragonBones Shizuku loaded", {
        version: skeleton.version,
        armature: armatureData.name,
        animations: this.animationNames,
        skins: this.skinNames,
        texturePages: advertisedPages.length,
        loadedTextures: this.loadedTextureUrls,
        bounds: aabb,
      })
      this.onSnapshot?.(snapshot)
    } catch (error) {
      const failure = error instanceof Error ? error : new Error(String(error))
      this.onError?.(failure)
      this.unload()
      throw failure
    }
  }

  unload(): void {
    if (this.gestureTimer !== undefined) window.clearTimeout(this.gestureTimer)
    this.gestureTimer = undefined
    this.app?.canvas.removeEventListener("pointermove", this.pointerMove)
    this.app?.canvas.removeEventListener("click", this.pointerTap)
    document.removeEventListener("visibilitychange", this.visibilityChange)
    this.resizeObserver?.disconnect()
    this.resizeObserver = undefined
    if (this.app) {
      this.app.ticker.remove(this.tick)
      this.app.stop()
    }
    this.display?.destroy({ children: true })
    if (this.factory && this.dataName) this.factory.removeDragonBonesData(this.dataName, true)
    this.app?.destroy(true)
    for (const url of this.loadedTextureUrls) void PIXI.Assets.unload(url).catch(() => undefined)
    this.host.replaceChildren()
    this.app = null
    this.display = null
    this.factory = null
    this.factoryClass = null
    this.manifest = null
    this.loadedTextureUrls = []
    this.textures = []
    this.animationNames = []
    this.skinNames = []
    this.currentAnimation = ""
    this.currentAnimationDurationMs = 1300
    this.currentSemantic = "loading"
  }

  playSemanticState(state: TeacherPresentationState): void {
    this.currentSemantic = state
    if (!this.display || !this.manifest || state === "loading") return
    if (state === "hidden") { this.display.visible = false; return }
    const animation = resolveTeacherAnimation(this.manifest, state, this.animationNames)
    const oneShot = ["greeting", "questioning", "praising", "encouraging", "ending"].includes(state)
    this.playAnimation(animation, !oneShot)
    this.display.visible = true
  }

  playAnimation(name: string, loop: boolean): void {
    if (!this.display || !this.animationNames.includes(name)) return
    if (!shouldStartTeacherAnimation(this.currentAnimation, this.currentLoop, name, loop)) return
    this.currentAnimation = name
    this.currentLoop = loop
    const animationState = this.display.animation.play(name, loop ? 0 : 1)
    if (animationState?.totalTime && Number.isFinite(animationState.totalTime)) {
      this.currentAnimationDurationMs = Math.max(250, Math.min(10_000, animationState.totalTime * 1000))
    }
    if (!loop) this.scheduleIdle(name, this.currentAnimationDurationMs)
    this.onSnapshot?.({ currentAnimation: this.currentAnimation, animations: this.animationNames, skins: this.skinNames, loadedTextures: this.loadedTextureUrls, canvasWidth: this.host.clientWidth, canvasHeight: this.host.clientHeight, webgl: Boolean(this.app?.renderer) })
  }

  listAnimations(): string[] { return [...this.animationNames] }
  listSkins(): string[] { return [...this.skinNames] }
  setSkin(_name: string): void { /* Shizuku has one default skin; future manifests can rebuild here. */ }
  setScale(scale: number): void { this.scale = Math.max(.08, Math.min(2.8, scale)); this.resize(this.host.clientWidth, this.host.clientHeight) }
  setOffset(x: number, y: number): void {
    this.offsetX = Math.max(-1.2, Math.min(1.2, x))
    this.offsetY = Math.max(-1.4, Math.min(1.4, y))
    this.resize(this.host.clientWidth, this.host.clientHeight)
  }
  setMirror(mirrored: boolean): void { this.mirrored = mirrored; this.resize(this.host.clientWidth, this.host.clientHeight) }
  pause(): void { this.userPaused = true; this.paused = true; this.app?.ticker.stop() }
  resume(): void { this.userPaused = false; this.paused = this.pageHidden; if (!this.paused) this.app?.ticker.start() }
  resize(width: number, height: number): void {
    if (!this.display || !width || !height) return
    const manifestScale = this.manifest?.initialScale || 1
    const fit = Math.min(width / 640, height / 700)
    const magnitude = Math.max(.08, manifestScale * fit * this.scale)
    this.display.scale.set(this.mirrored ? -magnitude : magnitude, magnitude)
    this.display.x = width * .62 + this.offsetX * width + (this.manifest?.initialOffsetX || 0)
    this.display.y = height * .98 + this.offsetY * height + (this.manifest?.initialOffsetY || 0)
    if (this.app) this.app.renderer.resize(width, height)
    this.onSnapshot?.({
      currentAnimation: this.currentAnimation,
      animations: this.animationNames,
      skins: this.skinNames,
      loadedTextures: this.loadedTextureUrls,
      canvasWidth: width,
      canvasHeight: height,
      webgl: Boolean(this.app?.renderer),
    })
  }

  private updatePointer(event: PointerEvent): void {
    const rect = this.host.getBoundingClientRect()
    const scale = Math.abs(this.display?.scale.x || 1)
    this.pointer.x = (event.clientX - rect.left - (this.display?.x || 0)) / scale
    this.pointer.y = (event.clientY - rect.top - (this.display?.y || 0)) / scale
    this.pointer.active = true
  }

  private updatePointerAnimation(): void {
    const display = this.display
    if (!display || !this.pointer.active) return
    const canvas = display.armature.armatureData.canvas
    if (!canvas) return
    const px = Math.max(-1, Math.min(1, (this.pointer.x - canvas.x) / (canvas.width * .5)))
    const py = -Math.max(-1, Math.min(1, (this.pointer.y - canvas.y) / (canvas.height * .5)))
    const values: Record<string, number> = {
      PARAM_ANGLE_X: (px + 1) * .5,
      PARAM_EYE_BALL_X: (px + 1) * .5,
      PARAM_ANGLE_Y: (py + 1) * .5,
      PARAM_EYE_BALL_Y: (py + 1) * .5,
      PARAM_ANGLE_Z: (-px * py + 1) * .5,
      PARAM_BODY_X: (px + 1) * .5,
      PARAM_BODY_ANGLE_X: (px + 1) * .5,
      PARAM_BODY_Y: (-px * py + 1) * .5,
      PARAM_BODY_ANGLE_Y: (-px * py + 1) * .5,
      PARAM_BODY_Z: (-px * py + 1) * .5,
      PARAM_BODY_ANGLE_Z: (-px * py + 1) * .5,
      PARAM_BREATH: (Math.sin(display.armature.clock.time) + 1) * .5,
    }
    for (const [name, value] of Object.entries(values)) {
      if (!display.animation.hasAnimation(name)) continue
      let animationState = display.animation.getState(name, 1)
      if (!animationState) {
        animationState = display.animation.fadeIn(name, .1, 1, 1, name)
        if (animationState) { animationState.resetToPose = false; animationState.stop() }
      }
      if (animationState) animationState.currentTime = value * animationState.totalTime
    }
  }

  private playTapGesture(): void {
    const gestures = ["tap_body_00", "tap_body_01", "tap_body_02", "flick_head_00", "flick_head_01", "flick_head_02"].filter(name => this.animationNames.includes(name))
    if (!gestures.length) return
    const name = gestures[this.gestureIndex++ % gestures.length]
    this.playAnimation(name, false)
  }

  private scheduleIdle(animation: string, durationMs: number): void {
    if (this.gestureTimer !== undefined) window.clearTimeout(this.gestureTimer)
    this.gestureTimer = window.setTimeout(() => {
      if (this.currentAnimation === animation && this.manifest) {
        const idle = resolveTeacherAnimation(this.manifest, "idle", this.animationNames)
        this.playAnimation(idle, true)
      }
    }, durationMs + 80)
  }
}
