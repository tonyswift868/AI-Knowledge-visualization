import * as PIXI from "pixi.js"
import { resolveTeacherAnimation } from "../teacherPresentationManifest"
import type { Teacher2DManifest, Teacher2DRenderer, TeacherPresentationState } from "../teacherPresentationTypes"
import type { DragonBonesAdapterOptions } from "./DragonBonesTeacherAdapter"

export type RigLayer = {
  id: string
  name: string
  role: string
  src: string
  x: number
  y: number
  width: number
  height: number
  order: number
}

export type RigState = {
  canvas: { width: number; height: number }
  layers: RigLayer[]
  anchors?: LayeredRigAnchors
  contentBounds?: { x: number; y: number; width: number; height: number }
  bones?: Partial<Record<LayeredBoneName, LayeredBoneName | null>>
}

type LayeredRigData = {
  version: number
  states: Record<"teaching" | "swimsuit", RigState>
}

type RigNode = {
  layer: RigLayer
  bone: LayeredBoneName
  sprite: PIXI.Sprite
}

export type LayeredBoneName =
  | "pelvis"
  | "legs"
  | "chest"
  | "left-arm"
  | "right-arm"
  | "head"
  | "hair-back"
  | "hair-front"

type BoneNode = {
  name: LayeredBoneName
  container: PIXI.Container
  restX: number
  restY: number
}

export type LayeredRigAnchors = {
  pelvis: { x: number; y: number }
  chest: { x: number; y: number }
  head: { x: number; y: number }
  leftShoulder: { x: number; y: number }
  rightShoulder: { x: number; y: number }
}

type SkinGraph = {
  root: PIXI.Container
  renderLayer: InstanceType<typeof PIXI.RenderLayer> & PIXI.Container
  bones: Record<LayeredBoneName, BoneNode>
  nodes: RigNode[]
  state: RigState
  anchors: LayeredRigAnchors
  contentBounds: { x: number; y: number; width: number; height: number }
}

export const layeredTeacherAnimations = [
  "idle", "wave", "think", "talk", "explain", "question", "cheer", "heart", "confused", "surprised",
] as const

export type LayeredTeacherAnimation = typeof layeredTeacherAnimations[number]

export type LayeredBonePose = {
  breath: number
  pelvisRotation: number
  pelvisShiftX: number
  chestRotation: number
  chestLift: number
  headRotation: number
  headOffsetX: number
  headOffsetY: number
  leftArmRotation: number
  rightArmRotation: number
  hairBackRotation: number
  hairFrontRotation: number
  jumpY: number
  headScale: 1
  neckScale: 1
}

export type LayeredHitReaction = {
  region: "head" | "left-arm" | "torso" | "right-arm" | "lower-body"
  animation: LayeredTeacherAnimation
  reaction: string
}

const skins = ["teaching", "swimsuit"] as const
const HEAD_ROLES = new Set([
  "hair-back", "hair-front", "head", "brow-right", "brow-left", "eye-right", "eye-left",
  "iris-right", "iris-left", "lash-right", "lash-left", "mouth", "nose", "ear-right",
  "ear-left", "headwear",
])
const EYE_ROLES = new Set(["eye-right", "eye-left", "iris-right", "iris-left", "lash-right", "lash-left"])
const IRIS_ROLES = new Set(["iris-right", "iris-left"])
const MOTION_KEYS = [
  "breath",
  "pelvisRotation",
  "pelvisShiftX",
  "chestRotation",
  "chestLift",
  "headRotation",
  "headOffsetX",
  "headOffsetY",
  "leftArmRotation",
  "rightArmRotation",
  "hairBackRotation",
  "hairFrontRotation",
  "jumpY",
] as const
type MotionKey = typeof MOTION_KEYS[number]

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

function easeOut(value: number): number {
  const x = clamp(value, 0, 1)
  return 1 - (1 - x) ** 3
}

function actionEnvelope(seconds: number): number {
  if (seconds < 0 || seconds > 1.5) return 0
  return Math.sin(Math.PI * clamp(seconds / 1.5, 0, 1))
}

function layerFor(state: RigState, role: string): RigLayer | undefined {
  return state.layers.find(layer => layer.role === role)
}

function centerOf(layer: RigLayer): { x: number; y: number } {
  return { x: layer.x + layer.width / 2, y: layer.y + layer.height / 2 }
}

function overlapCenter(
  first: RigLayer | undefined,
  second: RigLayer | undefined,
  fallback: { x: number; y: number },
): { x: number; y: number } {
  if (!first || !second) return fallback
  const left = Math.max(first.x, second.x)
  const top = Math.max(first.y, second.y)
  const right = Math.min(first.x + first.width, second.x + second.width)
  const bottom = Math.min(first.y + first.height, second.y + second.height)
  if (right <= left || bottom <= top) return fallback
  return { x: (left + right) / 2, y: (top + bottom) / 2 }
}

export function deriveLayeredRigAnchors(state: RigState): LayeredRigAnchors {
  if (state.anchors) return state.anchors
  const hips = layerFor(state, "hips")
  const legs = layerFor(state, "legs")
  const torso = layerFor(state, "torso")
  const neck = layerFor(state, "neck")
  const head = layerFor(state, "head")
  const leftArm = layerFor(state, "arm-left")
  const rightArm = layerFor(state, "arm-right")
  const canvasCenterX = state.canvas.width / 2

  const hipsCenter = hips ? centerOf(hips) : { x: canvasCenterX, y: state.canvas.height * .43 }
  const pelvis = overlapCenter(hips, legs, hipsCenter)
  const chest = overlapCenter(torso, hips, {
    x: torso ? centerOf(torso).x : pelvis.x,
    y: pelvis.y - state.canvas.height * .13,
  })
  const headAnchor = overlapCenter(head, neck, {
    x: head ? head.x + head.width * .49 : chest.x,
    y: head ? head.y + head.height * .9 : chest.y - state.canvas.height * .2,
  })
  const leftShoulder = leftArm
    ? { x: leftArm.x + leftArm.width * .96, y: leftArm.y + leftArm.height * .045 }
    : { x: chest.x - state.canvas.width * .045, y: chest.y - state.canvas.height * .1 }
  const rightShoulder = rightArm
    ? { x: rightArm.x + rightArm.width * .08, y: rightArm.y + rightArm.height * .1 }
    : { x: chest.x + state.canvas.width * .045, y: chest.y - state.canvas.height * .1 }

  return { pelvis, chest, head: headAnchor, leftShoulder, rightShoulder }
}

export function resolveLayeredBoneParent(role: string): LayeredBoneName {
  if (role === "hair-back") return "hair-back"
  if (role === "hair-front") return "hair-front"
  if (HEAD_ROLES.has(role)) return "head"
  if (role === "arm-left") return "left-arm"
  if (role === "arm-right" || role === "accessory") return "right-arm"
  if (role === "legs" || role === "feet") return "legs"
  if (role === "hips") return "pelvis"
  return "chest"
}

export const layeredBoneHierarchy: Record<LayeredBoneName, LayeredBoneName | null> = {
  pelvis: null,
  legs: "pelvis",
  chest: "pelvis",
  "left-arm": "chest",
  "right-arm": "chest",
  head: "chest",
  "hair-back": "head",
  "hair-front": "head",
}

export function stepCriticallyDamped(
  value: number,
  velocity: number,
  target: number,
  deltaSeconds: number,
  halfLife = .085,
): { value: number; velocity: number } {
  const dt = clamp(deltaSeconds, 0, .05)
  const omega = (2 * Math.LN2) / Math.max(.001, halfLife)
  const offset = value - target
  const decay = Math.exp(-omega * dt)
  const transient = (velocity + omega * offset) * dt
  return {
    value: target + (offset + transient) * decay,
    velocity: (velocity - omega * transient) * decay,
  }
}

function anchorForBone(name: LayeredBoneName, anchors: LayeredRigAnchors): { x: number; y: number } {
  if (name === "pelvis" || name === "legs") return anchors.pelvis
  if (name === "chest") return anchors.chest
  if (name === "left-arm") return anchors.leftShoulder
  if (name === "right-arm") return anchors.rightShoulder
  return anchors.head
}

export function resolveLayeredBonePose(
  time: number,
  pointer: { x: number; y: number },
  animation: LayeredTeacherAnimation,
  actionTime: number,
): LayeredBonePose {
  const breath = Math.sin(time * Math.PI * 2 / 4.7)
  const weightShift = Math.sin(time * .72)
  const micro = Math.sin(time * .83 + .7)
  const envelope = actionEnvelope(actionTime)
  const waveCycle = Math.sin(actionTime * 12)
  const pose: LayeredBonePose = {
    breath,
    pelvisRotation: weightShift * .0065,
    pelvisShiftX: weightShift * 2.1,
    chestRotation: pointer.x * .019 + micro * .008,
    chestLift: breath * 1.05,
    headRotation: pointer.x * .052 + micro * .011,
    headOffsetX: 0,
    headOffsetY: 0,
    leftArmRotation: Math.sin(time * .88 + .6) * .013,
    rightArmRotation: -Math.sin(time * .92 + .1) * .015,
    // The current source keeps scalp and ponytail in the same cutout.
    // Keep secondary hair motion below seam-visible amplitude until the asset is split again.
    hairBackRotation: Math.sin(time * 1.16 + .35) * .0022 + pointer.x * .0018,
    hairFrontRotation: Math.sin(time * 1.31 + .9) * .0014 + pointer.x * .0012,
    jumpY: 0,
    // Human neck and skull are rigid. They must never be stretched to fake motion.
    headScale: 1,
    neckScale: 1,
  }

  if (animation === "wave") {
    pose.rightArmRotation += waveCycle * .22 * envelope
    pose.chestRotation -= .032 * envelope
    pose.headRotation += .03 * envelope
    pose.hairBackRotation -= .002 * envelope
  } else if (animation === "explain") {
    pose.rightArmRotation -= .19 * envelope
    pose.chestRotation -= .042 * envelope
    pose.headRotation += .035 * envelope
  } else if (animation === "question") {
    pose.rightArmRotation += .12 * envelope
    pose.headRotation += .095 * envelope
    pose.chestRotation += .032 * envelope
  } else if (animation === "heart") {
    pose.leftArmRotation -= .13 * envelope
    pose.rightArmRotation += .08 * envelope
    pose.headRotation -= .06 * envelope
    pose.chestRotation += .036 * envelope
  } else if (animation === "confused") {
    pose.headRotation += Math.sin(actionTime * 8) * .08 * envelope
    pose.chestRotation -= .032 * envelope
  } else if (animation === "surprised") {
    pose.chestLift -= 3 * envelope
    pose.leftArmRotation -= .052 * envelope
    pose.rightArmRotation += .07 * envelope
  } else if (animation === "cheer") {
    pose.leftArmRotation -= .21 * envelope
    pose.rightArmRotation += .24 * envelope
    // Keep fallback semantics on authored joints; a pelvis jump would move the
    // entire cutout and masquerade as whole-model motion.
  } else if (animation === "think") {
    pose.headRotation += .06 + Math.sin(time * .7) * .016
    pose.chestRotation -= .018
  } else if (animation === "talk") {
    pose.rightArmRotation += Math.sin(time * 1.8) * .024
    pose.chestRotation += Math.sin(time * 1.2) * .009
  }

  return pose
}

export function resolveLayeredHitReaction(x: number, y: number): LayeredHitReaction {
  const normalizedX = clamp(x, 0, 1)
  const normalizedY = clamp(y, 0, 1)
  if (normalizedY < .22) {
    return { region: "head", animation: "surprised", reaction: "呀！" }
  }
  if (normalizedY < .62) {
    if (normalizedX < .34) return { region: "left-arm", animation: "heart", reaction: "我在陪你 ♡" }
    if (normalizedX > .66) return { region: "right-arm", animation: "wave", reaction: "你好呀 ✦" }
    return { region: "torso", animation: "explain", reaction: "这里我来讲清楚" }
  }
  return { region: "lower-body", animation: "cheer", reaction: "一起加油！" }
}

function resolveAssetUrl(src: string, rigUrl: string): string {
  return new URL(src, new URL(rigUrl, window.location.href)).toString()
}

export class LayeredTeacherAdapter implements Teacher2DRenderer {
  private readonly host: HTMLElement
  private readonly onSnapshot?: DragonBonesAdapterOptions["onSnapshot"]
  private readonly onError?: DragonBonesAdapterOptions["onError"]
  private manifest: Teacher2DManifest | null = null
  private rig: LayeredRigData | null = null
  private wrapper: HTMLDivElement | null = null
  private app: PIXI.Application | null = null
  private graphs = new Map<string, SkinGraph>()
  private loadedTextureUrls: string[] = []
  private currentAnimation = "idle"
  private currentSkin: typeof skins[number] = "teaching"
  private previousSkin: typeof skins[number] | null = null
  private semanticState: TeacherPresentationState = "idle"
  private actionStartedAt = 0
  private skinStartedAt = 0
  private elapsedSeconds = 0
  private blinkStartedAt = -100
  private nextBlinkAt = 2.4
  private scale = 1
  private offsetX = 0
  private offsetY = 0
  private mirrored = false
  private userPaused = false
  private pageHidden = false
  private pointerTarget = { x: 0, y: 0 }
  private pointer = { x: 0, y: 0 }
  private motionPose: LayeredBonePose | null = null
  private motionVelocity: Record<MotionKey, number> = Object.fromEntries(
    MOTION_KEYS.map(key => [key, 0]),
  ) as Record<MotionKey, number>
  private resizeObserver: ResizeObserver | undefined
  private idleTimer: number | undefined
  private reactionTimer: number | undefined

  private readonly tick = (ticker: PIXI.Ticker) => {
    if (this.userPaused || this.pageHidden) return
    const dt = Math.min(.05, ticker.deltaMS / 1000)
    this.elapsedSeconds += dt
    this.pointer.x += (this.pointerTarget.x - this.pointer.x) * Math.min(1, dt * 7)
    this.pointer.y += (this.pointerTarget.y - this.pointer.y) * Math.min(1, dt * 7)
    if (this.elapsedSeconds >= this.nextBlinkAt) {
      this.blinkStartedAt = this.elapsedSeconds
      this.nextBlinkAt = this.elapsedSeconds + 2.5 + Math.random() * 3.5
    }
    this.updateGraphs(dt)
  }

  private readonly pointerMove = (event: PointerEvent) => {
    const bounds = this.app?.canvas.getBoundingClientRect()
    if (!bounds || !bounds.width || !bounds.height) return
    this.pointerTarget.x = clamp(((event.clientX - bounds.left) / bounds.width - .5) * 2, -1, 1)
    this.pointerTarget.y = clamp(((event.clientY - bounds.top) / bounds.height - .5) * 2, -1, 1)
    this.wrapper?.style.setProperty("--rig-pointer-x", this.pointerTarget.x.toFixed(3))
    this.wrapper?.style.setProperty("--rig-pointer-y", this.pointerTarget.y.toFixed(3))
  }

  private readonly pointerLeave = () => {
    this.pointerTarget.x = 0
    this.pointerTarget.y = 0
  }

  private readonly pointerTap = (event: PointerEvent) => {
    if (this.userPaused) return
    const bounds = this.app?.canvas.getBoundingClientRect()
    const graph = this.graphs.get(this.currentSkin)
    if (!bounds || !bounds.height || !graph || !graph.root.scale.x || !graph.root.scale.y) return
    const stageX = event.clientX - bounds.left
    const stageY = event.clientY - bounds.top
    const localX = (stageX - graph.root.position.x) / graph.root.scale.x
    const localY = (stageY - graph.root.position.y) / graph.root.scale.y
    const content = graph.contentBounds
    const padding = 36
    if (
      localX < content.x - padding
      || localX > content.x + content.width + padding
      || localY < content.y - padding
      || localY > content.y + content.height + padding
    ) return
    const hit = resolveLayeredHitReaction(
      (localX - content.x) / content.width,
      (localY - content.y) / content.height,
    )
    this.setHitRegion(hit.region, hit.animation, hit.reaction)
  }

  private readonly keyDown = (event: KeyboardEvent) => {
    if (event.key !== "Enter" && event.key !== " ") return
    event.preventDefault()
    this.setHitRegion("keyboard", "wave", "你好呀 ✦")
  }

  private readonly visibilityChange = () => {
    this.pageHidden = document.hidden
    if (this.pageHidden) this.app?.ticker.stop()
    else if (!this.userPaused) this.app?.ticker.start()
  }

  constructor(options: DragonBonesAdapterOptions) {
    this.host = options.host
    this.onSnapshot = options.onSnapshot
    this.onError = options.onError
  }

  async load(manifest: Teacher2DManifest): Promise<void> {
    this.unload()
    this.manifest = manifest
    try {
      const response = await fetch(manifest.skeleton, {
        cache: "no-store",
        headers: { Accept: "application/json" },
      })
      if (!response.ok) throw new Error(`分层教师骨架加载失败：HTTP ${response.status}`)
      const contentType = response.headers.get("content-type")?.toLowerCase() || ""
      if (!contentType.includes("json")) throw new Error("分层教师骨架被错误路由；请刷新页面重试")
      const rig = await response.json() as LayeredRigData
      if (!rig.states?.teaching?.layers?.length || !rig.states?.swimsuit?.layers?.length) {
        throw new Error("分层教师骨架缺少双形态语义层")
      }
      this.rig = rig

      const wrapper = document.createElement("div")
      wrapper.className = "layered-teacher"
      wrapper.tabIndex = 0
      wrapper.setAttribute("role", "button")
      wrapper.setAttribute("aria-label", "可互动分层 AI 教师；移动鼠标观察视线与身体联动，点击不同部位触发动作")
      wrapper.dataset.rigKind = "pixi-hierarchical-v3"
      wrapper.dataset.motionModel = "skin-anchors-critical-damping-render-layers"
      wrapper.dataset.semanticMotion = "authored-layered-bones"
      wrapper.dataset.activeBones = "pelvis,chest,head,eyes,left-arm,right-arm,legs,hair-follow"
      wrapper.dataset.state = "idle"
      wrapper.dataset.skin = "teaching"
      wrapper.innerHTML = `
        <div class="layered-teacher-glow" aria-hidden="true"></div>
        <div class="layered-teacher-reaction" aria-live="polite"></div>
        <div class="layered-teacher-transition" aria-hidden="true"><i></i><b>FORM SHIFT</b></div>
        <div class="layered-teacher-hint">视线、呼吸、发丝与四肢独立联动 · 点击不同部位触发反应</div>
      `
      this.host.replaceChildren(wrapper)
      this.wrapper = wrapper

      const app = new PIXI.Application()
      await app.init({ backgroundAlpha: 0, antialias: true, autoStart: true, resizeTo: wrapper, preference: "webgl" })
      this.app = app
      wrapper.prepend(app.canvas)
      app.canvas.className = "layered-teacher-canvas"

      for (const skin of skins) {
        const graph = await this.createGraph(rig.states[skin], manifest.skeleton)
        graph.root.visible = skin === "teaching"
        graph.root.alpha = skin === "teaching" ? 1 : 0
        graph.renderLayer.visible = skin === "teaching"
        this.graphs.set(skin, graph)
        app.stage.addChild(graph.root, graph.renderLayer)
      }

      app.ticker.add(this.tick)
      app.canvas.addEventListener("pointermove", this.pointerMove)
      app.canvas.addEventListener("pointerleave", this.pointerLeave)
      app.canvas.addEventListener("pointerdown", this.pointerTap)
      wrapper.addEventListener("keydown", this.keyDown)
      document.addEventListener("visibilitychange", this.visibilityChange)
      this.resizeObserver = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(() => this.resize(wrapper.clientWidth, wrapper.clientHeight))
      this.resizeObserver?.observe(wrapper)
      this.resize(wrapper.clientWidth, wrapper.clientHeight)
      this.playSemanticState("idle")
      this.emit()
    } catch (error) {
      const failure = error instanceof Error ? error : new Error(String(error))
      this.onError?.(failure)
      this.unload()
      throw failure
    }
  }

  unload(): void {
    if (this.idleTimer !== undefined) window.clearTimeout(this.idleTimer)
    if (this.reactionTimer !== undefined) window.clearTimeout(this.reactionTimer)
    this.idleTimer = undefined
    this.reactionTimer = undefined
    this.app?.canvas.removeEventListener("pointermove", this.pointerMove)
    this.app?.canvas.removeEventListener("pointerleave", this.pointerLeave)
    this.app?.canvas.removeEventListener("pointerdown", this.pointerTap)
    this.wrapper?.removeEventListener("keydown", this.keyDown)
    document.removeEventListener("visibilitychange", this.visibilityChange)
    this.resizeObserver?.disconnect()
    this.resizeObserver = undefined
    if (this.app) {
      this.app.ticker.remove(this.tick)
      this.app.stop()
      this.app.destroy(true, { children: true })
    }
    for (const url of this.loadedTextureUrls) void PIXI.Assets.unload(url).catch(() => undefined)
    this.host.replaceChildren()
    this.graphs.clear()
    this.loadedTextureUrls = []
    this.wrapper = null
    this.app = null
    this.manifest = null
    this.rig = null
    this.previousSkin = null
    this.motionPose = null
    for (const key of MOTION_KEYS) this.motionVelocity[key] = 0
  }

  playSemanticState(state: TeacherPresentationState): void {
    this.semanticState = state
    if (!this.manifest || state === "loading") return
    if (state === "hidden") {
      if (this.wrapper) this.wrapper.hidden = true
      return
    }
    if (this.wrapper) this.wrapper.hidden = false
    const name = resolveTeacherAnimation(this.manifest, state, layeredTeacherAnimations)
    const loop = state === "idle" || state === "thinking" || state === "talking"
    this.playAnimation(name, loop)
  }

  playAnimation(name: string, loop = false): void {
    if (!layeredTeacherAnimations.includes(name as typeof layeredTeacherAnimations[number])) return
    if (this.idleTimer !== undefined) window.clearTimeout(this.idleTimer)
    this.currentAnimation = name
    this.actionStartedAt = this.elapsedSeconds
    if (this.wrapper) this.wrapper.dataset.state = name
    if (!loop && name !== "idle") {
      this.idleTimer = window.setTimeout(() => {
        this.currentAnimation = "idle"
        if (this.wrapper) this.wrapper.dataset.state = "idle"
        this.emit()
      }, 1550)
    }
    this.emit()
  }

  listAnimations(): string[] { return [...layeredTeacherAnimations] }
  listSkins(): string[] { return [...skins] }

  setSkin(name: string): void {
    if (name !== "teaching" && name !== "swimsuit") return
    if (name === this.currentSkin || !this.graphs.has(name)) return
    this.previousSkin = this.currentSkin
    this.currentSkin = name
    this.skinStartedAt = this.elapsedSeconds
    const incoming = this.graphs.get(name)
    const outgoing = this.previousSkin ? this.graphs.get(this.previousSkin) : undefined
    if (incoming) {
      incoming.root.visible = true
      incoming.root.alpha = 0
      incoming.renderLayer.visible = true
      if (this.app) this.app.stage.setChildIndex(incoming.renderLayer, this.app.stage.children.length - 1)
    }
    if (outgoing) {
      outgoing.root.visible = true
      outgoing.root.alpha = 1
      outgoing.renderLayer.visible = true
    }
    if (this.wrapper) {
      this.wrapper.dataset.skin = name
      this.wrapper.classList.remove("is-transforming")
      void this.wrapper.offsetWidth
      this.wrapper.classList.add("is-transforming")
    }
    this.showReaction(name === "teaching" ? "教学模式" : "休息模式", 1100)
    this.emit()
  }

  setScale(scale: number): void {
    this.scale = clamp(scale, .5, 2.8)
    this.resize(this.host.clientWidth, this.host.clientHeight)
  }

  setOffset(x: number, y: number): void {
    this.offsetX = clamp(x, -1.2, 1.2)
    this.offsetY = clamp(y, -1.4, 1.4)
    this.resize(this.host.clientWidth, this.host.clientHeight)
  }

  setMirror(mirrored: boolean): void {
    this.mirrored = mirrored
    this.resize(this.host.clientWidth, this.host.clientHeight)
  }

  pause(): void {
    this.userPaused = true
    this.app?.ticker.stop()
    this.wrapper?.classList.add("is-paused")
  }

  resume(): void {
    this.userPaused = false
    if (!this.pageHidden) this.app?.ticker.start()
    this.wrapper?.classList.remove("is-paused")
  }

  resize(width: number, height: number): void {
    if (!this.rig || width <= 0 || height <= 0) return
    for (const graph of this.graphs.values()) {
      const { canvas } = graph.state
      const fit = Math.min(width / (canvas.width * .54), height / (canvas.height * .98)) * this.scale
      const sx = fit * (this.mirrored ? -1 : 1)
      graph.root.scale.set(sx, fit)
      graph.root.position.set(
        width / 2 - (canvas.width / 2) * sx + this.offsetX * width,
        height - canvas.height * fit + this.offsetY * height,
      )
    }
    this.emit()
  }

  private async createGraph(state: RigState, rigUrl: string): Promise<SkinGraph> {
    const root = new PIXI.Container()
    const renderLayer = new PIXI.RenderLayer({ sortableChildren: true }) as InstanceType<typeof PIXI.RenderLayer> & PIXI.Container
    const anchors = deriveLayeredRigAnchors(state)
    const bones = {} as Record<LayeredBoneName, BoneNode>
    const boneOrder: LayeredBoneName[] = [
      "pelvis", "legs", "chest", "left-arm", "right-arm", "head", "hair-back", "hair-front",
    ]

    for (const name of boneOrder) {
      const parentName = layeredBoneHierarchy[name]
      const parent = parentName ? bones[parentName].container : root
      const worldAnchor = anchorForBone(name, anchors)
      const parentWorldAnchor = parentName ? anchorForBone(parentName, anchors) : { x: 0, y: 0 }
      const container = new PIXI.Container()
      const restX = worldAnchor.x - parentWorldAnchor.x
      const restY = worldAnchor.y - parentWorldAnchor.y
      container.position.set(restX, restY)
      parent.addChild(container)
      bones[name] = { name, container, restX, restY }
    }

    const nodes: RigNode[] = []
    const resolved = state.layers.map(layer => ({ layer, url: resolveAssetUrl(layer.src, rigUrl) }))
    const textures = await Promise.all(resolved.map(item => PIXI.Assets.load<PIXI.Texture>(item.url)))
    for (const [index, item] of resolved.entries()) {
      const { layer, url } = item
      const texture = textures[index]
      this.loadedTextureUrls.push(url)
      const bone = resolveLayeredBoneParent(layer.role)
      const boneAnchor = anchorForBone(bone, anchors)
      const sprite = new PIXI.Sprite(texture)
      sprite.position.set(layer.x - boneAnchor.x, layer.y - boneAnchor.y)
      sprite.zIndex = layer.order
      bones[bone].container.addChild(sprite)
      renderLayer.attach(sprite)
      nodes.push({ layer, bone, sprite })
    }
    renderLayer.sortRenderLayerChildren()
    const minX = state.contentBounds?.x ?? Math.min(...state.layers.map(layer => layer.x))
    const minY = state.contentBounds?.y ?? Math.min(...state.layers.map(layer => layer.y))
    const maxX = state.contentBounds
      ? state.contentBounds.x + state.contentBounds.width
      : Math.max(...state.layers.map(layer => layer.x + layer.width))
    const maxY = state.contentBounds
      ? state.contentBounds.y + state.contentBounds.height
      : Math.max(...state.layers.map(layer => layer.y + layer.height))
    return {
      root,
      renderLayer,
      bones,
      nodes,
      state,
      anchors,
      contentBounds: { x: minX, y: minY, width: maxX - minX, height: maxY - minY },
    }
  }

  private updateGraphs(deltaSeconds = 1 / 60): void {
    const time = this.elapsedSeconds
    const actionTime = time - this.actionStartedAt
    const targetPose = resolveLayeredBonePose(
      time,
      this.pointer,
      this.currentAnimation as LayeredTeacherAnimation,
      actionTime,
    )
    if (!this.motionPose) this.motionPose = { ...targetPose }
    const pose = this.motionPose
    for (const key of MOTION_KEYS) {
      const halfLife = key === "jumpY" ? .045 : key.includes("Arm") ? .065 : .085
      const stepped = stepCriticallyDamped(
        pose[key],
        this.motionVelocity[key],
        targetPose[key],
        deltaSeconds,
        halfLife,
      )
      pose[key] = stepped.value
      this.motionVelocity[key] = stepped.velocity
    }
    pose.headScale = 1
    pose.neckScale = 1
    const blinkAge = time - this.blinkStartedAt
    const blink = blinkAge >= 0 && blinkAge < .22
      ? .08 + .92 * Math.abs(Math.cos(Math.PI * blinkAge / .22))
      : 1
    const talking = this.currentAnimation === "talk"
    const transitionAge = time - this.skinStartedAt

    if (this.previousSkin) {
      const mix = easeOut(transitionAge / .82)
      const incoming = this.graphs.get(this.currentSkin)
      const outgoing = this.graphs.get(this.previousSkin)
      if (incoming) incoming.root.alpha = mix
      if (outgoing) outgoing.root.alpha = 1 - mix
      if (mix >= 1) {
        if (outgoing) {
          outgoing.root.visible = false
          outgoing.renderLayer.visible = false
        }
        this.previousSkin = null
        this.wrapper?.classList.remove("is-transforming")
      }
    }

    for (const graph of this.graphs.values()) {
      if (!graph.root.visible) continue
      const { bones } = graph
      bones.pelvis.container.position.set(
        bones.pelvis.restX + pose.pelvisShiftX,
        bones.pelvis.restY + pose.jumpY,
      )
      bones.pelvis.container.rotation = pose.pelvisRotation
      bones.legs.container.position.set(bones.legs.restX, bones.legs.restY)
      bones.legs.container.rotation = -pose.pelvisRotation * .72
      bones.chest.container.position.set(bones.chest.restX, bones.chest.restY + pose.chestLift)
      bones.chest.container.rotation = pose.chestRotation
      bones["left-arm"].container.position.set(bones["left-arm"].restX, bones["left-arm"].restY)
      bones["left-arm"].container.rotation = pose.leftArmRotation
      bones["right-arm"].container.position.set(bones["right-arm"].restX, bones["right-arm"].restY)
      bones["right-arm"].container.rotation = pose.rightArmRotation
      bones.head.container.position.set(
        bones.head.restX + pose.headOffsetX,
        bones.head.restY + pose.headOffsetY,
      )
      bones.head.container.rotation = pose.headRotation
      bones["hair-back"].container.position.set(bones["hair-back"].restX, bones["hair-back"].restY)
      bones["hair-back"].container.rotation = pose.hairBackRotation
      bones["hair-front"].container.position.set(bones["hair-front"].restX, bones["hair-front"].restY)
      bones["hair-front"].container.rotation = pose.hairFrontRotation

      for (const node of graph.nodes) {
        const { sprite, layer, bone } = node
        const role = layer.role
        const boneAnchor = anchorForBone(bone, graph.anchors)
        sprite.position.set(layer.x - boneAnchor.x, layer.y - boneAnchor.y)
        sprite.scale.set(1, 1)
        if (IRIS_ROLES.has(role)) {
          sprite.x += this.pointer.x * 2.5
          sprite.y += this.pointer.y * 1.4
        }
        if (EYE_ROLES.has(role)) {
          sprite.scale.y = blink
          sprite.y += layer.height * (1 - blink) / 2
        }
        if (role === "mouth" && talking) {
          const mouthScale = .72 + Math.abs(Math.sin(time * 11.5)) * .58
          sprite.scale.y = mouthScale
          sprite.y -= layer.height * (mouthScale - 1) / 2
        }
        if (role === "torso") {
          // Breathing belongs to the ribcage, never the neck. Keep the deformation subtle.
          sprite.scale.x = 1 + pose.breath * .0045
          sprite.scale.y = 1 + pose.breath * .0035
        }
      }
    }
  }

  private setHitRegion(region: string, animation: string, reaction: string): void {
    if (this.wrapper) this.wrapper.dataset.lastHitRegion = region
    this.playAnimation(animation, false)
    this.showReaction(reaction, 1300)
  }

  private showReaction(text: string, duration: number): void {
    if (this.reactionTimer !== undefined) window.clearTimeout(this.reactionTimer)
    const element = this.wrapper?.querySelector<HTMLElement>(".layered-teacher-reaction")
    if (!element) return
    element.textContent = text
    element.classList.add("visible")
    this.reactionTimer = window.setTimeout(() => element.classList.remove("visible"), duration)
  }

  private emit(): void {
    if (!this.manifest) return
    const bounds = this.host.getBoundingClientRect()
    this.onSnapshot?.({
      currentAnimation: this.currentAnimation,
      animations: [...layeredTeacherAnimations],
      skins: [...skins],
      loadedTextures: [...new Set(this.loadedTextureUrls)],
      canvasWidth: Math.round(bounds.width),
      canvasHeight: Math.round(bounds.height),
      webgl: Boolean(this.app?.renderer),
      bounds: { x: 0, y: 0, width: Math.round(bounds.width), height: Math.round(bounds.height) },
    })
  }
}
