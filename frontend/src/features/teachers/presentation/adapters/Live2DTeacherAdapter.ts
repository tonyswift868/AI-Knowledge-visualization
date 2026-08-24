import * as PIXI from "pixi.js"

import type { Teacher2DManifest, Teacher2DRenderer, TeacherPresentationState } from "../teacherPresentationTypes"
import {
  type Live2DModelCapabilities,
  scanLive2DModelCapabilities,
  registeredExpressionNames,
  registeredMotionNames,
} from "../live2d/live2dCapabilities"
import { createLive2DMotionRegistry, type Live2DActionCurve, type Live2DMotionRegistry, type Live2DSemanticAction } from "../live2d/live2dMotionRegistry"

const DEFAULT_CORE_SCRIPT = "/vendor/live2d/live2dcubismcore.min.js"
const NEVER_WRITE_ROLES = new Set(["bodyX", "bodyY", "bodyZ", "eyeExpression1", "eyeExpression2", "eyeBrowX", "leftHand1", "leftHand2", "rightHand1", "rightHand2"])
const coreLoads = new Map<string, Promise<void>>()
let runtimePromise: Promise<typeof import("untitled-pixi-live2d-engine/cubism")> | null = null
let pluginRegistered = false

type Model3Settings = {
  FileReferences?: {
    Textures?: string[]
    Motions?: Record<string, Array<{ File?: string; Loop?: boolean; Priority?: number; DurationMs?: number; Duration?: number; FadeInTime?: number; FadeOutTime?: number }>>
    Expressions?: Array<{ Name?: string; File?: string }>
    Physics?: string
    DisplayInfo?: string
  }
  Groups?: Array<{ Target?: string; Name?: string; Ids?: string[] }>
}

type CubismIdManager = { getId: (id: string) => unknown }
type ReleasableCubismCoreModel = { release: () => void }
type CubismCoreModel = ReleasableCubismCoreModel & {
  parameters?: {
    ids?: string[]
    minimumValues?: ArrayLike<number>
    maximumValues?: ArrayLike<number>
    defaultValues?: ArrayLike<number>
    values?: ArrayLike<number>
  }
  getParameterCount?: () => number
  getParameterMinimumValue?: (index: number) => number
  getParameterMaximumValue?: (index: number) => number
  getParameterDefaultValue?: (index: number) => number
  getParameterId?: (index: number) => unknown
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
  motionManager: { expressionManager?: { resetExpression: () => void }; stopAllMotions?: () => void }
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
  shy: { focusX: -.2, focusY: .14, headX: -4, headY: 2.2, headZ: 7, bodyX: -2.2, bodyY: 1.4, bodyZ: 2.2, mouthForm: .46, mouthPucker: .12, eyeOpen: .74, eyeAsymmetry: .05, browY: .26, browAsymmetry: .12, browForm: .16, cheekPuff: .34, eyeExpression2: .8, leftHand: 1.8, rightHand: 1.4 },
  playful_annoyed: { focusX: .02, focusY: .08, headX: .8, headY: 1.4, headZ: -5.5, bodyX: 1.2, bodyY: 1.2, bodyZ: 2.8, mouthForm: -.26, mouthX: .08, mouthPucker: .42, eyeOpen: .8, eyeAsymmetry: -.04, browY: -.24, browAsymmetry: .08, browForm: -.3, eyeBrowX: -.08, cheekPuff: .48, eyeExpression1: -.34, eyeExpression2: .18, leftHand: 2.2, rightHand: 2.2 },
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

/** The canvas itself is fixed.  Ambient life belongs to model parameters. */
export function resolveLive2DIdleAmbient(elapsedSeconds: number, enabled = true): { x: number; y: number } {
  void elapsedSeconds
  void enabled
  return { x: 0, y: 0 }
}

/**
 * A quiet model-native body sway.  It never changes the Pixi model position
 * and deliberately leaves the head/eye input channels alone.  The adapter
 * adds these small values after Cubism physics has evaluated the frame so the
 * body, hair and clothes keep their authored secondary motion.
 */
export function resolveLive2DIdleBodySway(elapsedSeconds: number, enabled = true): { bodyX: number; bodyZ: number } {
  if (!enabled) return { bodyX: 0, bodyZ: 0 }
  const time = Math.max(0, elapsedSeconds)
  return {
    bodyX: Math.sin(time * .72) * 2.35,
    bodyZ: Math.sin(time * .72 + Math.PI / 2) * .7,
  }
}

function resolveModelResource(base: string, relative: string): string {
  if (/^(?:https?:|data:|blob:|\/\/)/i.test(relative)) return relative
  const slash = base.lastIndexOf("/")
  return `${slash >= 0 ? base.slice(0, slash + 1) : ""}${relative}`
}

async function loadOptionalJson(url: string | undefined): Promise<unknown> {
  if (!url) return undefined
  try {
    const response = await fetch(url, { cache: "no-store" })
    if (!response.ok) return undefined
    return await response.json()
  } catch {
    return undefined
  }
}

async function loadReferencedMotionMetadata(settings: Model3Settings, skeleton: string): Promise<Record<string, unknown>> {
  const files = new Set<string>()
  for (const entries of Object.values(settings.FileReferences?.Motions || {})) {
    if (!Array.isArray(entries)) continue
    for (const entry of entries) {
      if (!entry || typeof entry !== "object") continue
      if (typeof entry.File === "string" && entry.File.length > 0) files.add(entry.File)
    }
  }
  const metadata: Record<string, unknown> = Object.create(null) as Record<string, unknown>
  await Promise.all([...files].map(async file => {
    const value = await loadOptionalJson(resolveModelResource(skeleton, file))
    // Keep failed loads explicitly absent.  The capability scanner will leave
    // duration/loop unknown instead of fabricating a fallback from the URL.
    if (value !== undefined) metadata[file] = value
  }))
  return metadata
}

function nativeCoreParameters(core: CubismCoreModel, cdi: unknown): unknown {
  if (core.parameters?.ids?.length) return core.parameters
  const count = typeof core.getParameterCount === "function" ? core.getParameterCount() : 0
  const cdiParameters = cdi && typeof cdi === "object" && Array.isArray((cdi as { Parameters?: unknown }).Parameters)
    ? (cdi as { Parameters: Array<{ Id?: unknown }> }).Parameters
    : []
  const cdiIds = cdiParameters.map(parameter => typeof parameter.Id === "string" ? parameter.Id : undefined).filter((id): id is string => Boolean(id))
  const nativeIds = typeof core.getParameterId === "function"
    ? Array.from({ length: count }, (_, index) => {
      const handle = core.getParameterId!(index) as unknown as { getString?: () => { s?: string } | string; s?: string }
      const value = typeof handle?.getString === "function" ? handle.getString() : handle
      return typeof value === "string" ? value : value?.s
    }).filter((id): id is string => Boolean(id))
    : []
  const ids = cdiIds.length >= count ? cdiIds : nativeIds
  if (!count || !ids.length) return undefined
  return {
    ids: ids.slice(0, count),
    minimumValues: Array.from({ length: Math.min(count, ids.length) }, (_, index) => core.getParameterMinimumValue?.(index) ?? -1),
    maximumValues: Array.from({ length: Math.min(count, ids.length) }, (_, index) => core.getParameterMaximumValue?.(index) ?? 1),
    defaultValues: Array.from({ length: Math.min(count, ids.length) }, (_, index) => core.getParameterDefaultValue?.(index) ?? 0),
  }
}

function interpolateActionCurve(curve: Live2DActionCurve, progress: number): number {
  const points = curve.points
  if (!points.length) return 0
  if (progress <= points[0].at) return points[0].value
  const last = points[points.length - 1]
  if (progress >= last.at) return last.value
  for (let index = 1; index < points.length; index += 1) {
    const right = points[index]
    const left = points[index - 1]
    if (progress > right.at) continue
    const span = Math.max(.0001, right.at - left.at)
    const alpha = (progress - left.at) / span
    return left.value + (right.value - left.value) * alpha
  }
  return last.value
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
    live2dCapabilities?: Live2DModelCapabilities
    live2dActions?: readonly import("../live2d/live2dMotionRegistry").Live2DSemanticAction[]
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
  private capabilities: Live2DModelCapabilities | undefined
  private motionRegistry: Live2DMotionRegistry | undefined
  private resizeObserver: ResizeObserver | null = null
  private currentSemantic: TeacherPresentationState = "loading"
  private authoritativeSemantic: TeacherPresentationState = "loading"
  private inspectorSemantic: TeacherPresentationState | undefined
  private activeAction: { action: Live2DSemanticAction; startedAt: number; progress: number; frozen: boolean; frozenAt?: number; loop: boolean; restoreOnComplete?: boolean } | undefined
  private readonly actionCooldownUntil = new Map<string, number>()
  private currentAnimation = "idle"
  private animationNames: string[] = []
  private userScale = 1
  private initialOffsetX = 0
  private initialOffsetY = 0
  private offsetX = 0
  private offsetY = 0
  private idleAmbientX = 0
  private idleAmbientY = 0
  private mirrored = false
  private paused = false
  private requestedPause = false
  private visibilityPaused = false
  private elapsed = 0
  private lastDeltaSeconds = 1 / 60
  private smoothedPose: Live2DProceduralPose = { ...neutralPose }
  private pointer = { x: 0, y: 0, active: false }
  private gestureTimer: number | undefined
  private parameterIds = new Map<string, unknown>()
  private physicsOutputIds = new Set<string>()
  private readonly nativePlaybackFailures = new Set<string>()
  private nativePlaybackGeneration = 0
  private currentSkin = "default"
  private loadGeneration = 0
  private readonly pointerMove = (event: PointerEvent) => this.updatePointer(event)
  private readonly pointerLeave = () => { this.pointer.active = false }
  private readonly pointerTap = () => this.playTapGesture()
  private readonly visibilityChange = () => {
    this.visibilityPaused = document.hidden
    this.applyPauseState()
  }
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
    this.idleAmbientX = 0
    this.idleAmbientY = 0
    try {
      const settingsResponse = await fetch(manifest.skeleton, { cache: "no-store" })
      if (generation !== this.loadGeneration) return
      if (!settingsResponse.ok) throw new Error(`Live2D model3.json 加载失败：HTTP ${settingsResponse.status}`)
      this.settings = await settingsResponse.json() as Model3Settings
      if (generation !== this.loadGeneration) return
      const [physics, cdi, motionMetadata] = await Promise.all([
        loadOptionalJson(this.settings.FileReferences?.Physics ? resolveModelResource(manifest.skeleton, this.settings.FileReferences.Physics) : undefined),
        loadOptionalJson(this.settings.FileReferences?.DisplayInfo ? resolveModelResource(manifest.skeleton, this.settings.FileReferences.DisplayInfo) : undefined),
        loadReferencedMotionMetadata(this.settings, manifest.skeleton),
      ])
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
      this.capabilities = scanLive2DModelCapabilities({
        settings: this.settings,
        parameters: nativeCoreParameters(model.internalModel.coreModel, cdi),
        physics,
        cdi,
        motionMetadata,
      })
      this.physicsOutputIds = new Set(this.capabilities.physicsOutputParameterIds)
      this.motionRegistry = createLive2DMotionRegistry(manifest, this.capabilities)
      model.anchor.set(.5, .5)
      model.eventMode = "none"
      app.stage.addChild(model)
      model.internalModel.on("beforeModelUpdate", this.beforeModelUpdate)
      app.ticker.add(this.tick, undefined, PIXI.UPDATE_PRIORITY.HIGH)
      app.canvas.addEventListener("pointermove", this.pointerMove)
      app.canvas.addEventListener("pointerleave", this.pointerLeave)
      app.canvas.addEventListener("click", this.pointerTap)
      document.addEventListener("visibilitychange", this.visibilityChange)
      this.visibilityPaused = document.hidden
      this.applyPauseState()
      this.resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => this.resize(this.host.clientWidth, this.host.clientHeight))
      this.resizeObserver?.observe(this.host)

      const motionNames = registeredMotionNames(this.capabilities)
      const expressionNames = registeredExpressionNames(this.capabilities)
      const semanticNames = this.motionRegistry.actions.map(action => action.id)
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
    this.capabilities = undefined
    this.motionRegistry = undefined
    this.physicsOutputIds.clear()
    this.animationNames = []
    this.currentAnimation = "idle"
    this.parameterIds.clear()
    this.currentSkin = "default"
    this.elapsed = 0
    this.idleAmbientX = 0
    this.idleAmbientY = 0
    this.lastDeltaSeconds = 1 / 60
    this.smoothedPose = { ...neutralPose }
    this.paused = false
    this.requestedPause = false
    this.visibilityPaused = false
    this.currentSemantic = "loading"
    this.authoritativeSemantic = "loading"
    this.inspectorSemantic = undefined
    this.activeAction = undefined
    this.actionCooldownUntil.clear()
    this.nativePlaybackFailures.clear()
    // Invalidate any pending native motion/expression promises from the
    // destroyed model before a future load reuses this adapter instance.
    this.nativePlaybackGeneration += 1
  }

  playSemanticState(state: TeacherPresentationState): void {
    this.currentSemantic = state
    this.authoritativeSemantic = state
    this.inspectorSemantic = undefined
    this.stopNativeMotions()
    this.activeAction = undefined
    if (!this.manifest || state === "hidden") return
    // Idle is presentation ambience, not a named action.  Keeping an
    // internal/native idle action active here could reintroduce a periodic
    // head turn through model-authored motion data. Neutral blinking and the
    // bounded model-body sway continue without moving the Pixi stage.
    if (state === "idle") {
      this.currentAnimation = "idle"
      this.model?.internalModel.motionManager.expressionManager?.resetExpression()
      this.publish()
      return
    }
    const resolution = this.motionRegistry?.resolve(state)
    if (resolution && !resolution.noop && !resolution.fallback) {
      const action = resolution.action
      // Semantic poses are authoritative and stay active until the
      // controller selects another state.  Native one-shot assets still keep
      // their authored loop metadata for explicit action triggers.
      const persistent = action.source !== "registered-motion"
        || state === "talking" || state === "explaining"
      this.startAction(action, persistent || Boolean(action.motion?.loop), true, false)
    }
    this.publish()
  }

  playAnimation(name: string, loop: boolean): void {
    this.triggerLive2DAction(name, loop)
  }

  getLive2DCapabilities(): Live2DModelCapabilities | undefined { return this.capabilities }
  listLive2DActions() { return this.runtimeActions() }

  triggerLive2DAction(name: string, loop = false): { requested: string; applied: string; supported: boolean; fallback: boolean; reason?: string } {
    const resolution = this.motionRegistry?.resolve(name)
    if (!resolution) {
      return { requested: name, applied: this.currentAnimation, supported: false, fallback: true, reason: "Live2D 尚未加载" }
    }
    if (resolution.noop || resolution.fallback) {
      return { requested: name, applied: this.currentAnimation, supported: false, fallback: true, reason: resolution.reason || `未知动作：${name}` }
    }
    const action = resolution.action
    const nativeKeys = [
      action.motion ? this.nativeFailureKey(action, "motion") : undefined,
      action.expression ? this.nativeFailureKey(action, "expression") : undefined,
    ].filter((key): key is string => Boolean(key))
    if (nativeKeys.some(key => this.nativePlaybackFailures.has(key))) {
      return { requested: name, applied: this.currentAnimation, supported: false, fallback: true, reason: "原生动作播放失败，已禁用重试" }
    }
    const cooldownUntil = this.actionCooldownUntil.get(action.id) || 0
    if (this.elapsed < cooldownUntil) {
      return { requested: name, applied: this.currentAnimation, supported: false, fallback: true, reason: `动作冷却中：${Math.ceil((cooldownUntil - this.elapsed) * 1000)}ms` }
    }
    if (this.activeAction && (!this.activeAction.action.interruptible || action.priority < this.activeAction.action.priority)) {
      return { requested: name, applied: this.currentAnimation, supported: false, fallback: true, reason: "当前动作优先级更高且不可中断" }
    }
    this.inspectorSemantic = action.semanticState
    this.currentAnimation = action.id
    // A valid motion3 Meta.Loop is authoritative when the caller did not
    // explicitly request a one-shot override.  Track the effective value too,
    // otherwise the native motion would loop while our lifecycle restored the
    // semantic state after an invented 1.2s fallback.
    this.startAction(action, loop || Boolean(action.motion?.loop), true)
    this.publish()
    const hasNativeFailure = () => nativeKeys.some(key => this.nativePlaybackFailures.has(key))
    const hasInternalFallback = this.hasObservableInternalCurves(action)
    return {
      requested: name,
      applied: action.id,
      get supported() { return !resolution.fallback && (!hasNativeFailure() || hasInternalFallback) },
      get fallback() { return resolution.fallback || (hasNativeFailure() && hasInternalFallback) },
      get reason() {
        return hasNativeFailure()
          ? hasInternalFallback
            ? "模型原生资源未接受播放，已继续使用安全参数动作"
            : "模型原生资源未接受播放；该动作已标记为不可用"
          : resolution.reason
      },
    }
  }

  restoreLive2DAuthoritativeState(): void {
    this.inspectorSemantic = undefined
    this.activeAction = undefined
    this.stopNativeMotions()
    this.resumeTickerWhenRenderable()
    this.playSemanticState(this.authoritativeSemantic)
  }

  stopLive2DAction(): void {
    this.stopNativeMotions()
    this.activeAction = undefined
    this.restoreLive2DAuthoritativeState()
  }

  freezeLive2DAction(): void {
    if (!this.activeAction) return
    this.activeAction.frozen = true
    this.activeAction.frozenAt = this.elapsed
    this.stopNativeMotions()
    // The Cubism engine restores its saved parameter array after each render.
    // Holding only our curve runner would therefore let the next engine frame
    // redraw the model in its pre-action pose.  Stop the shared Pixi ticker so
    // Freeze preserves the last genuinely rendered action frame.
    this.app?.ticker.stop()
  }

  resumeLive2DAction(): void {
    if (!this.activeAction?.frozen) return
    const action = this.activeAction.action
    const frozenAt = this.activeAction.frozenAt
    if (frozenAt !== undefined) this.activeAction.startedAt += Math.max(0, this.elapsed - frozenAt)
    this.activeAction.frozen = false
    this.activeAction.frozenAt = undefined
    if (!this.paused && !this.visibilityPaused) {
      this.resumeTickerWhenRenderable()
      this.startNativeMotion(action, this.activeAction.loop)
      this.startNativeExpression(action)
    }
  }

  private resumeTickerWhenRenderable(): void {
    if (!this.paused && !this.visibilityPaused) this.app?.ticker.start()
  }

  private startAction(action: Live2DSemanticAction, loop: boolean, track: boolean, restoreOnComplete = true): void {
    this.currentAnimation = action.id
    if (track) this.activeAction = { action, startedAt: this.elapsed, progress: 0, frozen: false, loop, restoreOnComplete }
    if (action.motion) {
      this.startNativeMotion(action, loop)
    }
    if (action.expression) {
      this.startNativeExpression(action)
    } else if (action.source !== "registered-motion") {
      this.model?.internalModel.motionManager.expressionManager?.resetExpression()
    }
  }

  private stopNativeMotions(): void {
    this.nativePlaybackGeneration += 1
    this.model?.internalModel.motionManager.stopAllMotions?.()
  }

  private startNativeMotion(action: Live2DSemanticAction, loop: boolean): void {
    if (!action.motion) return
    if (this.nativePlaybackFailures.has(this.nativeFailureKey(action, "motion"))) return
    if (this.paused || this.visibilityPaused) return
    const model = this.model
    if (!model) {
      this.handleNativePlaybackFailure(action, "motion", new Error("Live2D 模型尚未就绪"))
      return
    }
    this.stopNativeMotions()
    try {
      const request = model.motion(
        action.motion.group,
        action.motion.index,
        action.motion.priority ?? action.priority,
        { loop: loop || Boolean(action.motion.loop) },
      )
      const generation = this.nativePlaybackGeneration
      void request.then(result => {
        // false means the Cubism motion manager declined this attempt (often
        // because another request was interrupted or held priority).  It does
        // not prove that the model asset is broken, so do not permanently
        // disable the registry action.
        void result
      }).catch(error => this.handleNativePlaybackFailure(action, "motion", error, generation))
    } catch (error) {
      this.handleNativePlaybackFailure(action, "motion", error)
    }
  }

  private startNativeExpression(action: Live2DSemanticAction): void {
    if (!action.expression) return
    if (this.nativePlaybackFailures.has(this.nativeFailureKey(action, "expression"))) return
    const model = this.model
    if (!model) {
      this.handleNativePlaybackFailure(action, "expression", new Error("Live2D 模型尚未就绪"))
      return
    }
    try {
      const generation = this.nativePlaybackGeneration
      void model.expression(action.expression).then(result => {
        // Expression promises resolve false when a newer expression replaces
        // the prior request.  Treat that as normal interruption, not a
        // renderer or capability failure.
        void result
      }).catch(error => this.handleNativePlaybackFailure(action, "expression", error, generation))
    } catch (error) {
      this.handleNativePlaybackFailure(action, "expression", error)
    }
  }

  private handleNativePlaybackFailure(action: Live2DSemanticAction, kind: "motion" | "expression", error: unknown, generation?: number): void {
    if (generation !== undefined && generation !== this.nativePlaybackGeneration) return
    this.nativePlaybackFailures.add(this.nativeFailureKey(action, kind))
    const hasInternalFallback = this.hasObservableInternalCurves(action)
    if (!hasInternalFallback) {
      if (this.activeAction?.action.id === action.id) this.activeAction = undefined
      if (this.currentAnimation === action.id) this.currentAnimation = "idle"
    }
    if (kind === "motion") this.stopNativeMotions()
    // A rejected optional motion/expression is an action-level capability
    // result, not a renderer failure.  Keep the classroom and Live2D canvas
    // ready; actions with safe curves continue through that local fallback.
    void error
    this.publish()
  }

  private hasObservableInternalCurves(action: Live2DSemanticAction): boolean {
    return Boolean(action.curves?.some(curve => {
      const values = curve.points.map(point => point.value)
      return values.length > 1 && Math.max(...values) - Math.min(...values) > .001
    }))
  }

  private runtimeActions(): readonly Live2DSemanticAction[] {
    return (this.motionRegistry?.actions || []).map(action => {
      const failed = [
        action.motion ? this.nativeFailureKey(action, "motion") : undefined,
        action.expression ? this.nativeFailureKey(action, "expression") : undefined,
      ].some(key => key ? this.nativePlaybackFailures.has(key) : false)
      if (!failed) return action
      if (this.hasObservableInternalCurves(action)) {
        return {
          ...action,
          source: "internal-parameters" as const,
          implementation: "internal-parameters" as const,
          sourceLicense: "retargeted" as const,
          supported: true,
          supportReason: "模型原生资源未接受播放，当前使用安全参数动作",
        }
      }
      return { ...action, supported: false, supportReason: "模型原生资源未接受播放" }
    })
  }

  private nativeFailureKey(action: Live2DSemanticAction, kind: "motion" | "expression"): string {
    return kind === "motion" && action.motion
      ? `motion:${action.motion.group}:${action.motion.index}`
      : kind === "expression" && action.expression
        ? `expression:${action.expression}`
        : `${kind}:${action.id}`
  }

  private applyPauseState(): void {
    this.paused = this.requestedPause || this.visibilityPaused
    if (this.paused) {
      this.stopNativeMotions()
      this.app?.ticker.stop()
      return
    }
    // Inspector Freeze is independent of global pause/visibility.  Returning
    // from a hidden tab must not silently unfreeze the captured action frame.
    if (this.activeAction?.frozen) return
    this.app?.ticker.start()
    if (this.activeAction && !this.activeAction.frozen && this.activeAction.action.motion) {
      this.startNativeMotion(this.activeAction.action, this.activeAction.loop)
    }
    if (this.activeAction && !this.activeAction.frozen && this.activeAction.action.expression) {
      this.startNativeExpression(this.activeAction.action)
    }
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
  pause(): void { this.requestedPause = true; this.applyPauseState() }
  resume(): void { this.requestedPause = false; this.applyPauseState() }
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
      width / 2 + this.initialOffsetX + this.offsetX * width + this.idleAmbientX,
      height / 2 + this.initialOffsetY + this.offsetY * height + this.idleAmbientY,
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
    if (!this.model || !this.manifest || this.paused || this.activeAction?.frozen) return
    const dt = Math.min(.05, Math.max(0, deltaSeconds))
    this.lastDeltaSeconds = dt
    this.elapsed += dt
    this.advanceActiveAction()
    const pose = this.smoothedPose
    // The stage/canvas never moves during idle.  Body life is applied in
    // applyProceduralPose through the model's body parameters.
    this.idleAmbientX = 0
    this.idleAmbientY = 0
    // Default idle no longer performs a periodic look-left/look-right cycle.
    // Pointer focus and explicit Look actions remain available independently.
    if (!this.pointer.active && !this.activeAction && !this.inspectorSemantic && this.currentSemantic === "idle") {
      // Do not continuously ease the head toward a stale focus target during
      // idle.  Reset once-per-frame without interpolation; body life comes
      // from the bounded ambient sway above and native physics.
      this.model.internalModel.focusController.focus(0, 0, true)
    } else {
      const focusX = this.pointer.active ? this.pointer.x * .65 : pose.focusX
      const focusY = this.pointer.active ? this.pointer.y * .5 : pose.focusY
      this.model.internalModel.focusController.focus(focusX, focusY)
    }
  }

  private applyProceduralPose(): void {
    if (!this.model || !this.manifest || this.paused || this.activeAction?.frozen || !this.capabilities?.physicsKnown) return
    this.smoothedPose = blendLive2DProceduralPose(
      this.smoothedPose,
      // The physical animation name may be an authored alias such as
      // `motion:Cry:0`; classroom meaning lives in currentSemantic.
      resolveLive2DProceduralPose(this.inspectorSemantic || this.currentSemantic),
      this.lastDeltaSeconds,
    )
    const pose = this.smoothedPose
    const breathe = (Math.sin(this.elapsed * 1.55) + 1) * .5
    const face = resolveLive2DFacialFrame(pose, this.elapsed)
    const isGesturing = (this.inspectorSemantic || this.currentSemantic) !== "idle"
    const gestureWave = isGesturing ? Math.sin(this.elapsed * 2.1) : 0
    const speechNod = pose.speaking ? Math.sin(this.elapsed * 2.65) * .7 : 0
    this.setParameter("angleX", pose.headX, .78)
    this.setParameter("angleY", pose.headY + speechNod, .76)
    this.setParameter("angleZ", pose.headZ + gestureWave * 1.05, .78)
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
    this.setParameter("cheekPuff", pose.cheekPuff, .72)
    this.setParameter("breath", breathe, .22)
    const ambient = resolveLive2DIdleBodySway(
      this.elapsed,
      !this.activeAction && !this.inspectorSemantic && this.currentSemantic === "idle",
    )
    this.addAmbientBodyParameter("bodyX", ambient.bodyX, .58)
    this.addAmbientBodyParameter("bodyZ", ambient.bodyZ, .46)
    if (this.activeAction?.action.id === "BREATH") {
      const cycle = Math.sin(Math.max(0, Math.min(1, this.activeAction.progress)) * Math.PI)
      this.addAmbientBodyParameter("bodyX", cycle * .55, .5)
      this.addAmbientBodyParameter("bodyZ", cycle * 1.35, .55)
    }
    this.applyActiveActionCurves()

  }

  private advanceActiveAction(): void {
    const active = this.activeAction
    if (!active || active.frozen) return
    const durationMs = active.action.durationMs ?? active.action.motion?.durationMs
    // Native motion duration is owned by motion3 metadata.  If that metadata
    // could not be fetched or validated, keep the action active and let the
    // native runtime own its lifecycle rather than inventing a timeout.
    if (durationMs === undefined && active.action.source === "registered-motion") return
    const duration = Math.max(.05, (durationMs ?? 1200) / 1000)
    if (active.loop) {
      active.progress = ((this.elapsed - active.startedAt) / duration) % 1
      return
    }
    active.progress = Math.max(0, (this.elapsed - active.startedAt) / duration)
    if (active.progress < 1) return
    this.actionCooldownUntil.set(active.action.id, this.elapsed + active.action.cooldownMs / 1000)
    this.activeAction = undefined
    if (active.restoreOnComplete !== false) this.restoreLive2DAuthoritativeState()
  }

  private applyActiveActionCurves(): void {
    const active = this.activeAction
    const capabilities = this.capabilities
    const internal = this.model?.internalModel
    if (!active || active.frozen || !capabilities?.physicsKnown || !internal || active.action.source === "registered-motion") return
    const curves = active.action.curves || []
    for (const curve of curves) {
      const parameter = capabilities.parameters.find(candidate => candidate.id === curve.parameterId)
      if (!parameter || parameter.physicsOutput || this.physicsOutputIds.has(curve.parameterId)) continue
      const id = internal.idManager?.getId(curve.parameterId)
      if (id === undefined || internal.coreModel.getParameterIndex(id) < 0) continue
      const normalized = interpolateActionCurve(curve, active.progress)
      const value = parameter.minimum + normalized * (parameter.maximum - parameter.minimum)
      internal.coreModel.setParameterValueById(id, value, 1)
    }
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
    const name = this.parameterName(role)
    if (!this.capabilities?.physicsKnown || NEVER_WRITE_ROLES.has(String(role)) || (name && this.physicsOutputIds.has(name))) return
    const id = this.parameterId(role)
    if (id !== undefined) this.model?.internalModel.coreModel.addParameterValueById(id, value, weight)
  }

  private setParameter(role: keyof NonNullable<NonNullable<Teacher2DManifest["live2d"]>["parameters"]>, value: number, weight: number): void {
    const name = this.parameterName(role)
    if (!this.capabilities?.physicsKnown || NEVER_WRITE_ROLES.has(String(role)) || (name && this.physicsOutputIds.has(name))) return
    const id = this.parameterId(role)
    if (id !== undefined) this.model?.internalModel.coreModel.setParameterValueById(id, value, weight)
  }

  private addAmbientBodyParameter(
    role: "bodyX" | "bodyZ",
    value: number,
    weight: number,
  ): void {
    if (!this.capabilities?.physicsKnown || !Number.isFinite(value)) return
    const name = this.parameterName(role)
    if (!name || !this.physicsOutputIds.has(name)) return
    const id = this.parameterId(role)
    if (id !== undefined) this.model?.internalModel.coreModel.addParameterValueById(id, value, weight)
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
      live2dCapabilities: this.capabilities,
      live2dActions: this.runtimeActions(),
    })
  }
}
