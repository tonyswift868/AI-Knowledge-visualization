import { resolveTeacherAnimation } from "../teacherPresentationManifest"
import type { Teacher2DManifest, Teacher2DRenderer, TeacherPresentationState } from "../teacherPresentationTypes"
import type { DragonBonesAdapterOptions } from "./DragonBonesTeacherAdapter"

export const imageTeacherAnimations = ["idle", "wave", "think", "talk", "explain", "question", "cheer", "heart", "confused", "surprised"] as const
const skins = ["teaching", "swimsuit"]

function preloadImage(url: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve()
    image.onerror = () => reject(new Error(`2D 教师图片加载失败：${url}`))
    image.src = url
  })
}

export class ImageTeacherAdapter implements Teacher2DRenderer {
  private readonly host: HTMLElement
  private readonly onSnapshot?: DragonBonesAdapterOptions["onSnapshot"]
  private readonly onError?: DragonBonesAdapterOptions["onError"]
  private manifest: Teacher2DManifest | null = null
  private root: HTMLDivElement | null = null
  private currentAnimation = "idle"
  private currentSkin = "teaching"
  private scale = 1
  private mirrored = false
  private paused = false
  private actionTimer: number | undefined
  private transformTimer: number | undefined
  private readonly pointerMove = (event: PointerEvent) => this.trackPointer(event)
  private readonly pointerLeave = () => this.resetPointer()
  private readonly pointerTap = (event: PointerEvent) => this.reactToTap(event)

  constructor(options: DragonBonesAdapterOptions) {
    this.host = options.host
    this.onSnapshot = options.onSnapshot
    this.onError = options.onError
  }

  async load(manifest: Teacher2DManifest): Promise<void> {
    try {
      this.manifest = manifest
      if (!manifest.imageStates) throw new Error("image2d 教师缺少双形态图片")
      await Promise.all([preloadImage(manifest.imageStates.teaching), preloadImage(manifest.imageStates.swimsuit)])

      const root = document.createElement("div")
      root.className = "image-teacher"
      root.dataset.state = "idle"
      root.dataset.skin = "teaching"
      root.tabIndex = 0
      root.setAttribute("role", "button")
      root.setAttribute("aria-label", "可互动 AI 教师，点击角色触发动作")
      root.innerHTML = `
        <div class="image-teacher-glow" aria-hidden="true"></div>
        <div class="image-teacher-body">
          <img class="image-teacher-form form-teaching active" draggable="false" alt="${manifest.displayName}教学形态">
          <img class="image-teacher-form form-swimsuit" draggable="false" alt="${manifest.displayName}泳装形态">
        </div>
        <div class="image-teacher-transition" aria-hidden="true"><i></i><b>FORM SHIFT</b></div>
        <div class="image-teacher-reaction" aria-live="polite"></div>
        <div class="image-teacher-hint">移动鼠标，她会看向你 · 点击触发反应</div>
      `
      root.querySelector<HTMLImageElement>(".form-teaching")!.src = manifest.imageStates.teaching
      root.querySelector<HTMLImageElement>(".form-swimsuit")!.src = manifest.imageStates.swimsuit
      root.addEventListener("pointermove", this.pointerMove)
      root.addEventListener("pointerleave", this.pointerLeave)
      root.addEventListener("pointerdown", this.pointerTap)
      root.addEventListener("keydown", this.keyDown)
      this.host.replaceChildren(root)
      this.root = root
      this.setScale(manifest.initialScale || 1)
      this.emit()
    } catch (error) {
      const next = error instanceof Error ? error : new Error(String(error))
      this.onError?.(next)
      throw next
    }
  }

  unload(): void {
    if (this.actionTimer) window.clearTimeout(this.actionTimer)
    if (this.transformTimer) window.clearTimeout(this.transformTimer)
    this.root?.removeEventListener("pointermove", this.pointerMove)
    this.root?.removeEventListener("pointerleave", this.pointerLeave)
    this.root?.removeEventListener("pointerdown", this.pointerTap)
    this.root?.removeEventListener("keydown", this.keyDown)
    this.root?.remove()
    this.root = null
    this.manifest = null
  }

  playSemanticState(state: TeacherPresentationState): void {
    if (!this.manifest || !this.root || state === "hidden") return
    const next = resolveTeacherAnimation(this.manifest, state, imageTeacherAnimations)
    this.playAnimation(next, next === "idle" || next === "talk")
  }

  playAnimation(name: string, loop = false): void {
    if (!this.root || !imageTeacherAnimations.includes(name as typeof imageTeacherAnimations[number])) return
    if (this.actionTimer) window.clearTimeout(this.actionTimer)
    this.currentAnimation = name
    this.root.dataset.state = name
    this.root.classList.remove("action-restart")
    void this.root.offsetWidth
    this.root.classList.add("action-restart")
    this.setReaction(name)
    if (!loop && name !== "idle") {
      this.actionTimer = window.setTimeout(() => {
        if (!this.root) return
        this.currentAnimation = "idle"
        this.root.dataset.state = "idle"
        this.setReaction("")
        this.emit()
      }, 1450)
    }
    this.emit()
  }

  listAnimations(): string[] { return [...imageTeacherAnimations] }
  listSkins(): string[] { return [...skins] }

  setSkin(name: string): void {
    if (!this.root || !skins.includes(name) || name === this.currentSkin) return
    if (this.transformTimer) window.clearTimeout(this.transformTimer)
    const previous = this.currentSkin
    this.currentSkin = name
    this.root.dataset.previousSkin = previous
    this.root.dataset.skin = name
    this.root.classList.remove("is-transforming")
    void this.root.offsetWidth
    this.root.classList.add("is-transforming")
    this.root.querySelectorAll(".image-teacher-form").forEach(image => image.classList.remove("active"))
    this.root.querySelector(`.form-${name}`)?.classList.add("active")
    this.setReaction(name === "teaching" ? "教学模式" : "休息模式")
    this.transformTimer = window.setTimeout(() => {
      this.root?.classList.remove("is-transforming")
      this.setReaction("")
    }, 1050)
    this.emit()
  }

  setScale(scale: number): void {
    this.scale = scale
    this.root?.style.setProperty("--teacher-scale", String(scale))
  }

  setMirror(mirrored: boolean): void {
    this.mirrored = mirrored
    this.root?.style.setProperty("--teacher-mirror", mirrored ? "-1" : "1")
  }

  pause(): void { this.paused = true; this.root?.classList.add("is-paused") }
  resume(): void { this.paused = false; this.root?.classList.remove("is-paused") }
  resize(_width: number, _height: number): void { this.emit() }

  private readonly keyDown = (event: KeyboardEvent) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      this.playAnimation("wave", false)
    }
  }

  private trackPointer(event: PointerEvent): void {
    if (!this.root || this.paused) return
    const bounds = this.interactionBounds()
    const x = Math.max(-1, Math.min(1, ((event.clientX - bounds.left) / Math.max(1, bounds.width) - .5) * 2))
    const y = Math.max(-1, Math.min(1, ((event.clientY - bounds.top) / Math.max(1, bounds.height) - .5) * 2))
    this.root.style.setProperty("--look-x", x.toFixed(3))
    this.root.style.setProperty("--look-y", y.toFixed(3))
  }

  private resetPointer(): void {
    this.root?.style.setProperty("--look-x", "0")
    this.root?.style.setProperty("--look-y", "0")
  }

  private reactToTap(event: PointerEvent): void {
    if (!this.root || this.paused) return
    const bounds = this.interactionBounds()
    const y = (event.clientY - bounds.top) / Math.max(1, bounds.height)
    if (y < .34) this.playAnimation("surprised", false)
    else if (y < .68) this.playAnimation("heart", false)
    else this.playAnimation("cheer", false)
  }

  private interactionBounds(): DOMRect {
    const own = this.root?.getBoundingClientRect()
    return own && own.width > 0 && own.height > 0 ? own : this.host.getBoundingClientRect()
  }

  private setReaction(action: string): void {
    const reaction = this.root?.querySelector<HTMLElement>(".image-teacher-reaction")
    if (!reaction) return
    const copy: Record<string, string> = {
      wave: "你好呀 ✦", think: "让我想想…", talk: "正在回答", explain: "看这里", question: "轮到你啦？",
      cheer: "做得很好！", heart: "我在陪你 ♡", confused: "我们换个角度", surprised: "呀！",
    }
    const text = copy[action] || (action.endsWith("模式") ? action : "")
    reaction.textContent = text
    reaction.classList.toggle("visible", Boolean(text))
  }

  private emit(): void {
    if (!this.root || !this.manifest) return
    const bounds = this.host.getBoundingClientRect()
    this.onSnapshot?.({
      currentAnimation: this.currentAnimation,
      animations: [...imageTeacherAnimations],
      skins: [...skins],
      loadedTextures: [...this.manifest.textures],
      canvasWidth: Math.round(bounds.width),
      canvasHeight: Math.round(bounds.height),
      webgl: false,
      bounds: { x: 0, y: 0, width: Math.round(bounds.width), height: Math.round(bounds.height) },
    })
  }
}
