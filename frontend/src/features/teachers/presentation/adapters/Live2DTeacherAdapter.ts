import * as PIXI from "pixi.js"

import { resolveTeacherAnimation } from "../teacherPresentationManifest"
import type { Teacher2DManifest, Teacher2DRenderer, TeacherPresentationState } from "../teacherPresentationTypes"

const DEFAULT_CORE_SCRIPT = "/vendor/live2d/live2dcubismcore.min.js"
const coreLoads = new Map<string, Promise<void>>()
let runtimePromise: Promise<typeof import("untitled-pixi-live2d-engine/cubism")> | null = null
let pluginRegistered = false

type Model3Settings = {
  FileReferences?: {
    Textures?: string[]
    Motions?: Record<string, Array<{ File?: string }>>
    Expressions?: Array<{ Name?: string; File?: string }>
  }
}

type CubismIdManager = { getId: (id: string) => unknown }
type ReleasableCubismCoreModel = { release: () => void }
type CubismCoreModel = ReleasableCubismCoreModel & {
  getParameterIndex: (id: unknown) => number
  addParameterValueById: (id: unknown, value: number, weight?: number) => void
  setParameterValueById: (id: unknown, value: number, weight?: number) => void
  getPartIndex: (id: unknown) => number
  getPartCount: () => number
  setPartMultiplyColorByRGBA: (partIndex: number, r: number, g: number, b: number, a: number) => void
  setPartScreenColorByRGBA: (partIndex: number, r: number, g: number, b: number, a: number) => void
  setOverrideColorForPartMultiplyColors: (partIndex: number, value: boolean) => void
  setOverrideColorForPartScreenColors: (partIndex: number, value: boolean) => void
}
type CubismMocCompat = {
  deleteModel: (model: ReleasableCubismCoreModel) => void
  release: () => void
}
type Live2DInternalModel = {
  coreModel: CubismCoreModel
  __moc?: CubismMocCompat
  idManager?: CubismIdManager
  originalWidth: number
  originalHeight: number
  focusController: { focus: (x: number, y: number, instant?: boolean) => void }
  motionManager: { expressionManager?: { resetExpression: () => void } }
  on: (event: "beforeModelUpdate", listener: () => void) => void
  off: (event: "beforeModelUpdate", listener: () => void) => void
}
type Live2DModelInstance = PIXI.Container & {
  internalModel: Live2DInternalModel
  textures: PIXI.Texture[]
  anchor: PIXI.ObservablePoint
  expression: (id?: number | string) => Promise<boolean>
  motion: (group: string, index?: number, priority?: number, options?: { loop?: boolean }) => Promise<boolean>
}

type PixiTextureSourceCompat = {
  _gpuData?: Record<number, unknown>
}

export function ensurePixiTextureSourceGpuData(source: unknown): void {
  if (!source || typeof source !== "object") return
  const compatible = source as PixiTextureSourceCompat
  if (compatible._gpuData) return
  // untitled-pixi-live2d-engine 1.3.5 checks this former Pixi internal cache
  // before asking Pixi 8.13's TextureSystem to bind a source. Pixi 8.13 moved
  // that cache into the renderer, so provide only the empty compatibility
  // slot; Pixi still owns the real GPU texture and its lifecycle.
  compatible._gpuData = Object.create(null) as Record<number, unknown>
}

export function prepareCubismMocRelease(
  internal: { coreModel: ReleasableCubismCoreModel; __moc?: CubismMocCompat },
): (() => void) | undefined {
  const moc = internal.__moc
  const coreModel = internal.coreModel
  if (!moc || typeof moc.deleteModel !== "function" || typeof moc.release !== "function" || typeof coreModel.release !== "function") {
    return undefined
  }

  // untitled-pixi-live2d-engine 1.3.5 attaches moc.release() to the
  // internal-model destroy event, which fires before coreModel.release().
  // Cubism requires the opposite order and asserts while a model is alive.
  // Replace the one core release with moc.deleteModel(), then release the moc
  // after the engine has finished destroying its renderer/internal model.
  const releaseCoreModel = coreModel.release.bind(coreModel)
  internal.__moc = undefined
  coreModel.release = () => {
    coreModel.release = releaseCoreModel
    moc.deleteModel(coreModel)
  }
  return () => moc.release()
}

export type Live2DProceduralPose = {
  focusX: number
  focusY: number
  headX: number
  headY: number
  headZ: number
  bodyX: number
  bodyY: number
  bodyZ: number
  mouthForm: number
  mouthX: number
  mouthPucker: number
  mouthShrug: number
  eyeOpen: number
  eyeAsymmetry: number
  browY: number
  browAsymmetry: number
  browForm: number
  eyeBrowX: number
  jawOpen: number
  cheekPuff: number
  eyeExpression1: number
  eyeExpression2: number
  leftHand: number
  rightHand: number
  speaking: boolean
}

const neutralPose: Live2DProceduralPose = {
  focusX: 0,
  focusY: 0,
  headX: 0,
  headY: 0,
  headZ: 0,
  bodyX: 0,
  bodyY: 0,
  bodyZ: 0,
  mouthForm: .12,
  mouthX: 0,
  mouthPucker: 0,
  mouthShrug: 0,
  eyeOpen: .98,
  eyeAsymmetry: 0,
  browY: 0,
  browAsymmetry: 0,
  browForm: 0,
  eyeBrowX: 0,
  jawOpen: 0,
  cheekPuff: 0,
  eyeExpression1: 0,
  eyeExpression2: 0,
  leftHand: 0,
  rightHand: 0,
  speaking: false,
}

const poses: Record<string, Partial<Live2DProceduralPose>> = {
  idle: {},
  greeting: { focusX: .22, focusY: .08, headX: 4, headZ: -4, bodyX: 3.2, bodyZ: -2.2, mouthForm: .66, eyeOpen: .9, browY: .22, browForm: .16, cheekPuff: .06, eyeExpression2: .62, rightHand: 3, speaking: true },
  thinking: { focusX: -.32, focusY: .24, headX: -6, headY: 4, headZ: -5, bodyX: -2.2, bodyY: 1.5, bodyZ: 1.8, mouthForm: -.18, mouthPucker: .28, eyeOpen: .8, eyeAsymmetry: .08, browY: .18, browAsymmetry: .16, browForm: -.22, eyeBrowX: -.12, eyeExpression1: .24, leftHand: 1.6 },
  talking: { focusX: .08, focusY: .02, headX: 2, headZ: 1.5, bodyX: 1.8, mouthForm: .42, eyeOpen: .96, browY: .06, eyeExpression2: .28, speaking: true },
  explaining: { focusX: .2, focusY: .04, headX: 4.4, headZ: -3.2, bodyX: 3.8, bodyZ: -1.8, mouthForm: .5, mouthX: .1, eyeOpen: .94, eyeAsymmetry: .03, browY: .2, browAsymmetry: .14, browForm: .18, eyeBrowX: .08, eyeExpression2: .4, rightHand: 4.2, speaking: true },
  questioning: { focusX: -.16, focusY: .12, headX: -3, headY: 1.2, headZ: -8, bodyX: -3, bodyZ: 2.6, mouthForm: .08, mouthPucker: .34, eyeOpen: .9, eyeAsymmetry: .1, browY: .38, browAsymmetry: .22, browForm: .32, eyeBrowX: -.22, eyeExpression1: .62, leftHand: 2.5, speaking: true },
  praising: { focusX: .16, focusY: -.03, headX: 5, headY: -2, headZ: 5, bodyX: 4, bodyZ: -2.8, mouthForm: .9, eyeOpen: .82, browY: .3, browForm: .24, cheekPuff: .1, eyeExpression2: .95, leftHand: 3, rightHand: 4.4, speaking: true },
  encouraging: { focusX: .12, focusY: .03, headX: 3.5, headZ: -4, bodyX: 2.8, bodyZ: -1.2, mouthForm: .72, mouthShrug: .1, eyeOpen: .86, browY: .3, browForm: .2, cheekPuff: .08, eyeExpression2: .76, rightHand: 3, speaking: true },
  confused: { focusX: -.28, focusY: .18, headX: -5, headY: 3, headZ: -9, bodyX: -2.8, bodyY: 1.8, bodyZ: 3, mouthForm: -.34, mouthX: -.22, mouthPucker: .36, eyeOpen: 1, eyeAsymmetry: -.08, browY: -.28, browAsymmetry: .2, browForm: -.46, eyeBrowX: -.3, cheekPuff: .2, eyeExpression1: -.72, eyeExpression2: -.32, speaking: true },
  ending: { focusX: .2, focusY: .08, headX: 3.5, headZ: 3.8, bodyX: 3.2, bodyZ: -2, mouthForm: .66, eyeOpen: .86, browY: .2, browForm: .18, cheekPuff: .05, eyeExpression2: .68, rightHand: 3.2, speaking: true },
  error: { focusX: -.12, focusY: .22, headY: 4, headZ: -3, bodyX: -2, bodyZ: 1.5, mouthForm: -.48, mouthShrug: .24, eyeOpen: .72, browY: -.46, browForm: -.56, eyeBrowX: -.2, eyeExpression1: -.78, eyeExpression2: -.46 },
}

export function resolveLive2DProceduralPose(name: string): Live2DProceduralPose {
  return { ...neutralPose, ...(poses[name] || poses.idle) }
}

export function blendLive2DProceduralPose(
  current: Live2DProceduralPose,
  target: Live2DProceduralPose,
  deltaSeconds: number,
  speed = 5.2,
): Live2DProceduralPose {
  const alpha = 1 - Math.exp(-Math.max(0, deltaSeconds) * speed)
  const blend = (from: number, to: number) => from + (to - from) * alpha
  return {
    focusX: blend(current.focusX, target.focusX),
    focusY: blend(current.focusY, target.focusY),
    headX: blend(current.headX, target.headX),
    headY: blend(current.headY, target.headY),
    headZ: blend(current.headZ, target.headZ),
    bodyX: blend(current.bodyX, target.bodyX),
    bodyY: blend(current.bodyY, target.bodyY),
    bodyZ: blend(current.bodyZ, target.bodyZ),
    mouthForm: blend(current.mouthForm, target.mouthForm),
    mouthX: blend(current.mouthX, target.mouthX),
    mouthPucker: blend(current.mouthPucker, target.mouthPucker),
    mouthShrug: blend(current.mouthShrug, target.mouthShrug),
    eyeOpen: blend(current.eyeOpen, target.eyeOpen),
    eyeAsymmetry: blend(current.eyeAsymmetry, target.eyeAsymmetry),
    browY: blend(current.browY, target.browY),
    browAsymmetry: blend(current.browAsymmetry, target.browAsymmetry),
    browForm: blend(current.browForm, target.browForm),
    eyeBrowX: blend(current.eyeBrowX, target.eyeBrowX),
    jawOpen: blend(current.jawOpen, target.jawOpen),
    cheekPuff: blend(current.cheekPuff, target.cheekPuff),
    eyeExpression1: blend(current.eyeExpression1, target.eyeExpression1),
    eyeExpression2: blend(current.eyeExpression2, target.eyeExpression2),
    leftHand: blend(current.leftHand, target.leftHand),
    rightHand: blend(current.rightHand, target.rightHand),
    speaking: target.speaking,
  }
}

export type Live2DFacialFrame = {
  eyeLeftOpen: number
  eyeRightOpen: number
  browLeftY: number
  browRightY: number
  browLeftForm: number
  browRightForm: number
  eyeBrowX: number
  mouthOpen: number
  mouthForm: number
  mouthX: number
  mouthPucker: number
  mouthShrug: number
  jawOpen: number
}

const clamp = (value: number, min = -1, max = 1) => Math.max(min, Math.min(max, value))

export function resolveLive2DFacialFrame(pose: Live2DProceduralPose, elapsedSeconds: number): Live2DFacialFrame {
  const time = Math.max(0, elapsedSeconds)
  const blinkPhase = time % 4.4
  const blink = blinkPhase < .18 ? Math.abs(blinkPhase - .09) / .09 : 1
  const eyePulse = pose.speaking ? Math.sin(time * 2.6) * .018 : 0
  const syllable = pose.speaking
    ? clamp(.54 + Math.sin(time * 11.7) * .3 + Math.sin(time * 17.3 + .8) * .16, 0, 1)
    : 0
  const consonant = pose.speaking ? (.5 + Math.sin(time * 7.1 + 1.4) * .5) : 0
  const mouthOpen = pose.speaking ? clamp(.14 + syllable * .68, 0, .88) : 0
  const browPulse = pose.speaking ? Math.sin(time * 3.2) * .055 : 0

  return {
    eyeLeftOpen: clamp((pose.eyeOpen + pose.eyeAsymmetry + eyePulse) * blink, 0, 1),
    eyeRightOpen: clamp((pose.eyeOpen - pose.eyeAsymmetry - eyePulse) * blink, 0, 1),
    browLeftY: clamp(pose.browY + pose.browAsymmetry + browPulse),
    browRightY: clamp(pose.browY - pose.browAsymmetry - browPulse),
    browLeftForm: clamp(pose.browForm + pose.browAsymmetry * .45),
    browRightForm: clamp(pose.browForm - pose.browAsymmetry * .45),
    eyeBrowX: clamp(pose.eyeBrowX),
    mouthOpen,
    mouthForm: clamp(pose.mouthForm + (pose.speaking ? Math.sin(time * 4.7) * .14 : 0)),
    mouthX: clamp(pose.mouthX + (pose.speaking ? Math.sin(time * 5.9 + .4) * .1 : 0)),
    mouthPucker: clamp(pose.mouthPucker + (pose.speaking ? (1 - syllable) * .16 : 0)),
    mouthShrug: clamp(pose.mouthShrug + consonant * .1),
    jawOpen: clamp(Math.max(pose.jawOpen, mouthOpen * .72), 0, 1),
  }
}

export function live2DSkinNames(manifest: Teacher2DManifest): string[] {
  const names = Object.keys(manifest.live2d?.skins || {})
  return names.length ? names : ["default"]
}

export function live2DCoreAvailable(scope: unknown = globalThis): boolean {
  return Boolean((scope as { Live2DCubismCore?: unknown } | undefined)?.Live2DCubismCore)
}

async function loadCoreScript(url: string): Promise<void> {
  if (live2DCoreAvailable()) return
  const pending = coreLoads.get(url)
  if (pending) return pending

  const task = (async () => {
    await new Promise<void>((resolve, reject) => {
      let existing = Array.from(document.querySelectorAll<HTMLScriptElement>("script[data-live2d-core]"))
        .find(candidate => candidate.dataset.live2dCore === url)
      if (existing && existing.dataset.live2dCoreState !== "loading") {
        existing.remove()
        existing = undefined
      }
      const script = existing || document.createElement("script")
      const succeed = () => {
        script.dataset.live2dCoreState = "loaded"
        if (live2DCoreAvailable()) resolve()
        else {
          script.remove()
          reject(new Error("Live2D Cubism Core 已加载，但未暴露 Live2DCubismCore"))
        }
      }
      const fail = () => {
        script.dataset.live2dCoreState = "error"
        script.remove()
        reject(new Error(`Live2D 官方 Cubism Core 脚本加载失败：${url}`))
      }
      script.addEventListener("load", succeed, { once: true })
      script.addEventListener("error", fail, { once: true })
      if (!existing) {
        script.src = url
        script.async = true
        script.dataset.live2dCore = url
        script.dataset.live2dCoreState = "loading"
        document.head.appendChild(script)
      }
    })
  })().catch(error => {
    coreLoads.delete(url)
    throw error
  })
  coreLoads.set(url, task)
  return task
}

async function loadRuntime(coreScript: string) {
  await loadCoreScript(coreScript)
  if (!runtimePromise) {
    runtimePromise = import("untitled-pixi-live2d-engine/cubism").then(runtime => {
      if (!pluginRegistered) {
        PIXI.extensions.add(runtime.Live2DPlugin)
        runtime.configureCubismSDK({ memorySizeMB: 64 })
        pluginRegistered = true
      }
      return runtime
    }).catch(error => {
      runtimePromise = null
      throw error
    })
  }
  return runtimePromise
}

export type Live2DAdapterOptions = {
  host: HTMLElement
  onSnapshot?: (snapshot: {
    currentAnimation?: string
    animations: string[]
    skins: string[]
    loadedTextures: string[]
    bounds?: { x: number; y: number; width: number; height: number }
    canvasWidth: number
    canvasHeight: number
    webgl: boolean
  }) => void
  onError?: (error: Error) => void
}

export class Live2DTeacherAdapter implements Teacher2DRenderer {
  private readonly host: HTMLElement
  private readonly onSnapshot?: Live2DAdapterOptions["onSnapshot"]
  private readonly onError?: Live2DAdapterOptions["onError"]
  private app: PIXI.Application | null = null
  private model: Live2DModelInstance | null = null
  private manifest: Teacher2DManifest | null = null
  private settings: Model3Settings | null = null
  private resizeObserver: ResizeObserver | null = null
  private currentSemantic: TeacherPresentationState = "loading"
  private currentAnimation = "idle"
  private animationNames: string[] = []
  private userScale = 1
  private initialOffsetX = 0
  private initialOffsetY = 0
  private offsetX = 0
  private offsetY = 0
  private mirrored = false
  private paused = false
  private elapsed = 0
  private lastDeltaSeconds = 1 / 60
  private smoothedPose: Live2DProceduralPose = { ...neutralPose }
  private pointer = { x: 0, y: 0, active: false }
  private gestureTimer: number | undefined
  private parameterIds = new Map<string, unknown>()
  private currentSkin = "default"
  private loadGeneration = 0
  private readonly pointerMove = (event: PointerEvent) => this.updatePointer(event)
  private readonly pointerLeave = () => { this.pointer.active = false }
  private readonly pointerTap = () => this.playTapGesture()
  private readonly visibilityChange = () => document.hidden ? this.pause() : this.resume()
  private readonly tick = (ticker: PIXI.Ticker) => this.update(ticker.deltaMS / 1000)
  private readonly beforeModelUpdate = () => this.applyProceduralPose()

  constructor(options: Live2DAdapterOptions) {
    this.host = options.host
    this.onSnapshot = options.onSnapshot
    this.onError = options.onError
  }

  async load(manifest: Teacher2DManifest): Promise<void> {
    this.unload()
    const generation = ++this.loadGeneration
    this.manifest = manifest
    this.userScale = manifest.initialScale || 1
    this.initialOffsetX = manifest.initialOffsetX || 0
    this.initialOffsetY = manifest.initialOffsetY || 0
    this.offsetX = 0
    this.offsetY = 0
    try {
      const settingsResponse = await fetch(manifest.skeleton, { cache: "no-store" })
      if (generation !== this.loadGeneration) return
      if (!settingsResponse.ok) throw new Error(`Live2D model3.json 加载失败：HTTP ${settingsResponse.status}`)
      this.settings = await settingsResponse.json() as Model3Settings
      if (generation !== this.loadGeneration) return

      const runtime = await loadRuntime(manifest.live2d?.coreScript || DEFAULT_CORE_SCRIPT)
      if (generation !== this.loadGeneration) return
      const app = new PIXI.Application()
      await app.init({ backgroundAlpha: 0, antialias: true, autoStart: true, resizeTo: this.host, preference: "webgl" })
      if (generation !== this.loadGeneration) {
        app.destroy(true)
        return
      }
      this.app = app
      this.host.replaceChildren(app.canvas)

      const model = await runtime.Live2DModel.from(manifest.skeleton, {
        autoUpdate: true,
        autoHitTest: false,
        autoFocus: false,
        ticker: app.ticker,
        breathDepth: .65,
        textureOptions: { lod: manifest.live2d?.textureLod ?? "single-auto" },
      }) as Live2DModelInstance
      if (generation !== this.loadGeneration) {
        model.destroy({ children: true })
        return
      }
      model.textures.forEach(texture => ensurePixiTextureSourceGpuData(texture.source))
      this.model = model
      model.anchor.set(.5, .5)
      model.eventMode = "none"
      app.stage.addChild(model)
      model.internalModel.on("beforeModelUpdate", this.beforeModelUpdate)
      app.ticker.add(this.tick, undefined, PIXI.UPDATE_PRIORITY.HIGH)
      app.canvas.addEventListener("pointermove", this.pointerMove)
      app.canvas.addEventListener("pointerleave", this.pointerLeave)
      app.canvas.addEventListener("click", this.pointerTap)
      document.addEventListener("visibilitychange", this.visibilityChange)
      this.resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => this.resize(this.host.clientWidth, this.host.clientHeight))
      this.resizeObserver?.observe(this.host)

      const motionNames = Object.entries(this.settings.FileReferences?.Motions || {}).flatMap(([group, entries]) => entries.map((_, index) => `motion:${group}:${index}`))
      const expressionNames = (this.settings.FileReferences?.Expressions || []).map(entry => `expression:${entry.Name || "unknown"}`)
      const semanticNames = Array.from(new Set([manifest.defaultAnimation, ...Object.values(manifest.animationAliases || {})].filter(Boolean) as string[]))
      this.animationNames = Array.from(new Set([...semanticNames, ...motionNames, ...expressionNames]))
      this.resize(this.host.clientWidth, this.host.clientHeight)
      this.currentSkin = live2DSkinNames(manifest)[0]
      this.applySkin(this.currentSkin)
      this.playSemanticState("idle")
      this.publish()
    } catch (error) {
      if (generation !== this.loadGeneration) return
      const failure = error instanceof Error ? error : new Error(String(error))
      this.onError?.(failure)
      this.unload()
      throw failure
    }
  }

  unload(): void {
    this.loadGeneration += 1
    if (this.gestureTimer !== undefined) window.clearTimeout(this.gestureTimer)
    this.gestureTimer = undefined
    document.removeEventListener("visibilitychange", this.visibilityChange)
    this.resizeObserver?.disconnect()
    this.resizeObserver = null
    if (this.app) {
      this.app.canvas.removeEventListener("pointermove", this.pointerMove)
      this.app.canvas.removeEventListener("pointerleave", this.pointerLeave)
      this.app.canvas.removeEventListener("click", this.pointerTap)
      this.app.ticker.remove(this.tick)
      this.app.stop()
    }
    this.model?.internalModel.off("beforeModelUpdate", this.beforeModelUpdate)
    const releaseMoc = this.model ? prepareCubismMocRelease(this.model.internalModel) : undefined
    try {
      this.model?.destroy({ children: true })
    } finally {
      releaseMoc?.()
    }
    this.app?.destroy(true)
    this.host.replaceChildren()
    this.app = null
    this.model = null
    this.manifest = null
    this.settings = null
    this.animationNames = []
    this.parameterIds.clear()
    this.currentSkin = "default"
    this.elapsed = 0
    this.lastDeltaSeconds = 1 / 60
    this.smoothedPose = { ...neutralPose }
    this.paused = false
  }

  playSemanticState(state: TeacherPresentationState): void {
    this.currentSemantic = state
    if (!this.manifest || state === "hidden") return
    const next = resolveTeacherAnimation(this.manifest, state, this.animationNames)
    this.playAnimation(next, state === "idle" || state === "talking" || state === "explaining")
    const expression = this.manifest.live2d?.expressions?.[state]
    if (expression) void this.model?.expression(expression)
    else this.model?.internalModel.motionManager.expressionManager?.resetExpression()
  }

  playAnimation(name: string, loop: boolean): void {
    this.currentAnimation = name
    if (name.startsWith("motion:")) {
      const [, group, rawIndex] = name.split(":")
      void this.model?.motion(group, Number(rawIndex) || 0, 2, { loop })
    } else if (name.startsWith("expression:")) {
      void this.model?.expression(name.slice("expression:".length))
    }
    this.publish()
  }

  listAnimations(): string[] { return [...this.animationNames] }
  listSkins(): string[] { return this.manifest ? live2DSkinNames(this.manifest) : [] }
  setSkin(name: string): void {
    if (!this.manifest || !this.model || !this.listSkins().includes(name)) return
    this.applySkin(name)
    this.currentSkin = name
    this.publish()
  }
  setScale(scale: number): void { this.userScale = Math.max(.3, Math.min(2.5, scale)); this.layout() }
  setOffset(x: number, y: number): void { this.offsetX = Math.max(-1.2, Math.min(1.2, x)); this.offsetY = Math.max(-1.4, Math.min(1.4, y)); this.layout() }
  setMirror(mirrored: boolean): void { this.mirrored = mirrored; this.layout() }
  pause(): void { this.paused = true; this.app?.ticker.stop() }
  resume(): void { this.paused = false; if (!document.hidden) this.app?.ticker.start() }
  resize(width: number, height: number): void { this.app?.renderer.resize(Math.max(1, width), Math.max(1, height)); this.layout(); this.publish() }

  private layout(): void {
    if (!this.model) return
    const width = Math.max(1, this.host.clientWidth)
    const height = Math.max(1, this.host.clientHeight)
    const sourceWidth = Math.max(1, this.model.internalModel.originalWidth)
    const sourceHeight = Math.max(1, this.model.internalModel.originalHeight)
    const fit = Math.min(width / sourceWidth, height / sourceHeight) * .94 * this.userScale
    this.model.scale.set(fit * (this.mirrored ? -1 : 1), fit)
    this.model.position.set(
      width / 2 + this.initialOffsetX + this.offsetX * width,
      height / 2 + this.initialOffsetY + this.offsetY * height,
    )
  }

  private updatePointer(event: PointerEvent): void {
    const bounds = this.host.getBoundingClientRect()
    if (!bounds.width || !bounds.height) return
    this.pointer.x = Math.max(-1, Math.min(1, ((event.clientX - bounds.left) / bounds.width) * 2 - 1))
    this.pointer.y = Math.max(-1, Math.min(1, 1 - ((event.clientY - bounds.top) / bounds.height) * 2))
    this.pointer.active = true
  }

  private playTapGesture(): void {
    if (!this.manifest) return
    if (this.gestureTimer !== undefined) window.clearTimeout(this.gestureTimer)
    const sequence = ["greeting", "praising", "questioning"]
    const index = Math.floor(this.elapsed / 1.7) % sequence.length
    const action = sequence[index]
    this.playAnimation(action, false)
    const semantic = action as TeacherPresentationState
    const expression = this.manifest.live2d?.expressions?.[semantic]
    if (expression) void this.model?.expression(expression)
    this.gestureTimer = window.setTimeout(() => this.playSemanticState(this.currentSemantic), 1700)
  }

  private update(deltaSeconds: number): void {
    if (!this.model || !this.manifest || this.paused) return
    const dt = Math.min(.05, Math.max(0, deltaSeconds))
    this.lastDeltaSeconds = dt
    this.elapsed += dt
    const pose = this.smoothedPose
    const idleX = Math.sin(this.elapsed * .62) * .08
    const idleY = Math.sin(this.elapsed * .43 + .7) * .035
    const focusX = this.pointer.active ? this.pointer.x * .65 : pose.focusX + idleX
    const focusY = this.pointer.active ? this.pointer.y * .5 : pose.focusY + idleY
    this.model.internalModel.focusController.focus(focusX, focusY)
  }

  private applyProceduralPose(): void {
    if (!this.model || !this.manifest || this.paused) return
    this.smoothedPose = blendLive2DProceduralPose(
      this.smoothedPose,
      resolveLive2DProceduralPose(this.currentAnimation),
      this.lastDeltaSeconds,
    )
    const pose = this.smoothedPose
    const idleX = Math.sin(this.elapsed * .62) * .08

    const breathe = (Math.sin(this.elapsed * 1.55) + 1) * .5
    const face = resolveLive2DFacialFrame(pose, this.elapsed)
    const isGesturing = this.currentAnimation !== "idle"
    const gestureWave = isGesturing ? Math.sin(this.elapsed * 2.1) : 0
    const speechNod = pose.speaking ? Math.sin(this.elapsed * 2.65) * .7 : 0
    this.setParameter("angleX", pose.headX + idleX * 14, .78)
    this.setParameter("angleY", pose.headY + speechNod, .76)
    this.setParameter("angleZ", pose.headZ + gestureWave * 1.05, .78)
    this.setParameter("bodyX", pose.bodyX + idleX * 7, .7)
    this.setParameter("bodyY", pose.bodyY, .65)
    this.setParameter("bodyZ", pose.bodyZ + gestureWave * .7, .72)
    this.setParameter("eyeLeftOpen", face.eyeLeftOpen, .9)
    this.setParameter("eyeRightOpen", face.eyeRightOpen, .9)
    this.setParameter("mouthForm", face.mouthForm, .86)
    this.setParameter("mouthX", face.mouthX, .78)
    this.setParameter("mouthPucker", face.mouthPucker, .76)
    this.setParameter("mouthShrug", face.mouthShrug, .76)
    this.setParameter("mouthOpen", face.mouthOpen, .96)
    this.setParameter("jawOpen", face.jawOpen, .9)
    this.setParameter("browLeftY", face.browLeftY, .84)
    this.setParameter("browRightY", face.browRightY, .84)
    this.setParameter("browLeftForm", face.browLeftForm, .82)
    this.setParameter("browRightForm", face.browRightForm, .82)
    this.setParameter("eyeBrowX", face.eyeBrowX, .8)
    this.setParameter("cheekPuff", pose.cheekPuff, .72)
    this.setParameter("eyeExpression1", pose.eyeExpression1, .9)
    this.setParameter("eyeExpression2", pose.eyeExpression2, .9)
    this.setParameter("breath", breathe, .22)
    this.setParameter("leftHand1", pose.leftHand + gestureWave * .7, .42)
    this.setParameter("leftHand2", pose.leftHand * .6 - gestureWave * .42, .4)
    this.setParameter("rightHand1", pose.rightHand - gestureWave * .78, .42)
    this.setParameter("rightHand2", pose.rightHand * .6 + gestureWave * .46, .4)

  }

  private parameterName(role: keyof NonNullable<NonNullable<Teacher2DManifest["live2d"]>["parameters"]>): string | undefined {
    const defaults: Record<string, string> = {
      angleX: "ParamAngleX", angleY: "ParamAngleY", angleZ: "ParamAngleZ",
      bodyX: "ParamBodyAngleX", bodyY: "ParamBodyAngleY", bodyZ: "ParamBodyAngleZ",
      eyeLeftOpen: "ParamEyeLOpen", eyeRightOpen: "ParamEyeROpen",
      eyeExpression1: "ParamEyeExpression1", eyeExpression2: "ParamEyeExpression2",
      mouthOpen: "ParamMouthOpenY", mouthForm: "ParamMouthForm", mouthX: "ParamMouthX", mouthPucker: "MouthPucker", mouthShrug: "MouthShrug",
      jawOpen: "JawOpen", browLeftY: "ParamBrowLY", browRightY: "ParamBrowRY", browLeftForm: "ParamBrowLForm", browRightForm: "ParamBrowRForm", eyeBrowX: "ParamEyeBrowX", cheekPuff: "ParamCheekPuff", breath: "ParamBreath",
      leftHand1: "ParamLHandPhysics1", leftHand2: "ParamLHandPhysics2", rightHand1: "ParamRHandPhysics1", rightHand2: "ParamRHandPhysics2",
    }
    return this.manifest?.live2d?.parameters?.[role] || defaults[role]
  }

  private parameterId(role: keyof NonNullable<NonNullable<Teacher2DManifest["live2d"]>["parameters"]>): unknown | undefined {
    const name = this.parameterName(role)
    const internal = this.model?.internalModel
    if (!name || !internal?.idManager) return undefined
    if (!this.parameterIds.has(name)) this.parameterIds.set(name, internal.idManager.getId(name))
    const id = this.parameterIds.get(name)
    return id !== undefined && internal.coreModel.getParameterIndex(id) >= 0 ? id : undefined
  }

  private addParameter(role: keyof NonNullable<NonNullable<Teacher2DManifest["live2d"]>["parameters"]>, value: number, weight: number): void {
    const id = this.parameterId(role)
    if (id !== undefined) this.model?.internalModel.coreModel.addParameterValueById(id, value, weight)
  }

  private setParameter(role: keyof NonNullable<NonNullable<Teacher2DManifest["live2d"]>["parameters"]>, value: number, weight: number): void {
    const id = this.parameterId(role)
    if (id !== undefined) this.model?.internalModel.coreModel.setParameterValueById(id, value, weight)
  }

  private applySkin(name: string): void {
    const internal = this.model?.internalModel
    const definitions = this.manifest?.live2d?.skins
    if (!internal || !definitions) return
    const core = internal.coreModel
    const allPartIds = Array.from(new Set(Object.values(definitions).flatMap(definition => definition.partIds || [])))
    for (const partId of allPartIds) {
      const id = internal.idManager?.getId(partId)
      if (id === undefined) continue
      const index = core.getPartIndex(id)
      if (index < 0 || index >= core.getPartCount()) continue
      core.setOverrideColorForPartMultiplyColors(index, false)
      core.setOverrideColorForPartScreenColors(index, false)
    }

    const definition = definitions[name]
    if (!definition?.partIds?.length) return
    const multiply = definition.multiplyColor || [1, 1, 1, 1]
    const screen = definition.screenColor || [0, 0, 0, 1]
    for (const partId of definition.partIds) {
      const id = internal.idManager?.getId(partId)
      if (id === undefined) continue
      const index = core.getPartIndex(id)
      if (index < 0 || index >= core.getPartCount()) continue
      core.setPartMultiplyColorByRGBA(index, ...multiply)
      core.setPartScreenColorByRGBA(index, ...screen)
      core.setOverrideColorForPartMultiplyColors(index, true)
      core.setOverrideColorForPartScreenColors(index, true)
    }
  }

  private publish(): void {
    if (!this.manifest) return
    this.onSnapshot?.({
      currentAnimation: this.currentAnimation,
      animations: this.listAnimations(),
      skins: this.listSkins(),
      loadedTextures: [...this.manifest.textures],
      bounds: this.model ? { x: 0, y: 0, width: this.model.internalModel.originalWidth, height: this.model.internalModel.originalHeight } : undefined,
      canvasWidth: this.host.clientWidth,
      canvasHeight: this.host.clientHeight,
      webgl: Boolean(this.app),
    })
  }
}
