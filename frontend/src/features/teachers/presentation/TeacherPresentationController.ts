import { createTeacher2DRenderer } from "./Teacher2DRenderer"
import { DEFAULT_TEACHER_MANIFEST_URL, FALLBACK_TEACHER_MANIFEST_URL, loadTeacher2DManifest } from "./teacherPresentationManifest"
import type { Teacher2DManifest, Teacher2DRenderer, TeacherPresentationState, TeacherRendererSnapshot } from "./teacherPresentationTypes"

export type TeacherPresentationControllerOptions = {
  onSnapshot: (snapshot: TeacherRendererSnapshot) => void
}
type PauseReason = "manual" | "reduced-motion"

const TRANSIENT_STATES: ReadonlySet<TeacherPresentationState> = new Set(["greeting", "praising", "encouraging", "shy", "playful_annoyed", "ending"])
const RETURN_TO_IDLE_MS = 1500

function asError(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value))
}

export class TeacherPresentationController {
  private readonly onSnapshot: TeacherPresentationControllerOptions["onSnapshot"]
  private renderer: Teacher2DRenderer | null = null
  private manifest: Teacher2DManifest | null = null
  private mounted = false
  private semanticState: TeacherPresentationState = "loading"
  private paused = false
  private pauseReason: PauseReason | null = null
  private hidden = false
  private returnToIdleTimer: number | undefined
  private degraded = false
  private fallbackUsed = false
  private originalError: string | undefined
  private fallbackError: string | undefined
  private fallbackManifestUrl: string | undefined
  private snapshot: TeacherRendererSnapshot = {
    status: "loading", renderer: "image2d", semanticState: "loading", animations: [], skins: [], canvasWidth: 0, canvasHeight: 0, webgl: false, loadedTextures: [],
    degraded: false, fallback_used: false, fallbackUsed: false,
  }

  constructor(options: TeacherPresentationControllerOptions) { this.onSnapshot = options.onSnapshot }

  async mount(host: HTMLElement, manifestUrl = DEFAULT_TEACHER_MANIFEST_URL): Promise<void> {
    if (this.mounted) return
    this.mounted = true
    this.degraded = false
    this.fallbackUsed = false
    this.originalError = undefined
    this.fallbackError = undefined
    this.fallbackManifestUrl = undefined
    this.publish({ status: "loading", semanticState: this.semanticState, degraded: false, fallback_used: false, fallbackUsed: false, originalError: undefined, fallbackError: undefined })

    try {
      await this.loadRenderer(host, manifestUrl)
      return
    } catch (primaryFailure) {
      if (!this.mounted) return
      const primaryError = asError(primaryFailure)
      this.originalError = primaryError.message
      if (manifestUrl === FALLBACK_TEACHER_MANIFEST_URL) {
        this.fallbackError = primaryError.message
        this.publish({ status: "error", error: primaryError.message, originalError: primaryError.message, fallbackError: primaryError.message, degraded: true, fallback_used: false, fallbackUsed: false, semanticState: "error" })
        throw primaryError
      }
      this.publish({ status: "loading", error: undefined, originalError: primaryError.message, degraded: true, fallback_used: false, fallbackUsed: false, semanticState: this.semanticState })
      try {
        this.degraded = true
        this.fallbackUsed = true
        this.fallbackManifestUrl = FALLBACK_TEACHER_MANIFEST_URL
        await this.loadRenderer(host, FALLBACK_TEACHER_MANIFEST_URL)
        if (!this.mounted) return
        this.publish({
          status: "ready",
          degraded: true,
          fallback_used: true,
          fallbackUsed: true,
          originalError: primaryError.message,
          fallbackManifestUrl: FALLBACK_TEACHER_MANIFEST_URL,
          semanticState: this.semanticState,
        })
      } catch (fallbackFailure) {
        if (!this.mounted) return
        const fallbackError = asError(fallbackFailure)
        this.fallbackError = fallbackError.message
        this.fallbackUsed = false
        const message = `${primaryError.message}；本地回退也失败：${fallbackError.message}`
        this.publish({
          status: "error",
          error: message,
          originalError: primaryError.message,
          fallbackError: fallbackError.message,
          degraded: true,
          fallback_used: false,
          fallbackUsed: false,
          semanticState: "error",
        })
        throw new Error(message)
      }
    }
  }

  setState(state: TeacherPresentationState): void {
    this.clearReturnToIdleTimer()
    this.semanticState = state
    if (state === "hidden") {
      this.hidden = true
      this.renderer?.pause()
      this.publish({ semanticState: state, status: "hidden" })
      return
    }

    this.hidden = false
    // A semantic prop change is not permission to undo a user/reduced-motion
    // pause, and a hidden state owns its renderer pause until explicitly left.
    if (!this.paused) this.renderer?.resume()
    this.renderer?.playSemanticState(state)
    // A controller can be paused before its renderer exists (the reduced-
    // motion path does this before mount). Selecting the semantic pose above
    // must not leave that newly-created renderer ticking in the background.
    if (this.paused) this.renderer?.pause()
    this.publish({ semanticState: state, status: this.snapshot.status === "hidden" ? "ready" : this.snapshot.status })
    if (this.mounted && !this.paused && TRANSIENT_STATES.has(state)) {
      this.returnToIdleTimer = window.setTimeout(() => {
        this.returnToIdleTimer = undefined
        if (!this.mounted || this.paused || this.semanticState !== state) return
        this.setState("idle")
      }, RETURN_TO_IDLE_MS)
    }
  }

  playAnimation(name: string, loop = false): void { this.renderer?.playAnimation(name, loop) }
  getLive2DCapabilities() { return this.renderer?.getLive2DCapabilities?.() }
  listLive2DActions() { return this.renderer?.listLive2DActions?.() || [] }
  triggerLive2DAction(name: string, loop = false) {
    return this.renderer?.triggerLive2DAction?.(name, loop) || { requested: name, applied: "", supported: false, fallback: true, reason: "Live2D 运行时未就绪" }
  }
  stopLive2DAction(): void { this.renderer?.stopLive2DAction?.() }
  freezeLive2DAction(): void { this.renderer?.freezeLive2DAction?.() }
  resumeLive2DAction(): void { this.renderer?.resumeLive2DAction?.() }
  restoreLive2DAuthoritativeState(): void { this.renderer?.restoreLive2DAuthoritativeState?.() }
  setScale(value: number): void { this.renderer?.setScale(value) }
  setOffset(x: number, y: number): void { this.renderer?.setOffset(x, y) }
  setMirror(value: boolean): void { this.renderer?.setMirror(value) }
  setSkin(value: string): void { this.renderer?.setSkin(value) }

  pause(reason: PauseReason = "manual"): void {
    this.clearReturnToIdleTimer()
    this.paused = true
    this.pauseReason = reason
    this.renderer?.pause()
    this.publish({ status: this.snapshot.status === "hidden" ? "hidden" : this.snapshot.status })
  }

  resume(): void {
    this.clearReturnToIdleTimer()
    this.paused = false
    this.pauseReason = null
    if (!this.hidden) this.renderer?.resume()
    this.publish({ status: this.hidden || this.snapshot.status === "hidden" ? "hidden" : this.snapshot.status })
  }

  reset(): void {
    this.clearReturnToIdleTimer()
    this.renderer?.setScale(1)
    this.renderer?.setOffset(0, 0)
    this.renderer?.setMirror(false)
    // Reduced-motion is an environment preference, not cleared by a programmatic reset.
    // The stage's explicit reset action clears its paused state and then calls resume().
    if (this.pauseReason !== "reduced-motion") {
      // Let setState perform the single resume associated with an explicit reset.
      this.paused = false
      this.pauseReason = null
    }
    this.setState("idle")
  }

  getSnapshot(): TeacherRendererSnapshot { return this.snapshot }

  dispose(): void {
    this.clearReturnToIdleTimer()
    this.mounted = false
    this.renderer?.unload()
    this.renderer = null
    this.manifest = null
    this.hidden = false
  }

  private async loadRenderer(host: HTMLElement, manifestUrl: string): Promise<void> {
    const manifest = await loadTeacher2DManifest(manifestUrl)
    if (!this.mounted) return
    this.manifest = manifest
    const renderer = createTeacher2DRenderer(manifest.renderer, host, {
      host,
      onSnapshot: value => this.publish({ status: "ready", renderer: manifest.renderer, armature: manifest.armature, ...value }),
      onError: error => this.publish({ status: "error", error: error.message, semanticState: "error" }),
    })
    this.renderer = renderer
    try {
      await renderer.load(manifest)
    } catch (error) {
      renderer.unload()
      if (this.renderer === renderer) this.renderer = null
      if (this.manifest === manifest) this.manifest = null
      throw error
    }
    if (!this.mounted) {
      renderer.unload()
      return
    }
    this.publish({
      status: "ready",
      error: undefined,
      renderer: manifest.renderer,
      armature: manifest.armature,
      animations: renderer.listAnimations(),
      skins: renderer.listSkins(),
      semanticState: this.semanticState,
      degraded: this.degraded,
      fallback_used: this.fallbackUsed,
      fallbackUsed: this.fallbackUsed,
      originalError: this.originalError,
      fallbackManifestUrl: this.fallbackManifestUrl,
    })
    this.setState(this.semanticState)
  }

  private clearReturnToIdleTimer(): void {
    if (this.returnToIdleTimer !== undefined) {
      window.clearTimeout(this.returnToIdleTimer)
      this.returnToIdleTimer = undefined
    }
  }

  private publish(update: Partial<TeacherRendererSnapshot>): void {
    this.snapshot = {
      ...this.snapshot,
      ...update,
      animations: update.animations || this.snapshot.animations,
      skins: update.skins || this.snapshot.skins,
      loadedTextures: update.loadedTextures || this.snapshot.loadedTextures,
      semanticState: update.semanticState || this.semanticState,
      degraded: update.degraded ?? this.snapshot.degraded,
      fallback_used: update.fallback_used ?? this.snapshot.fallback_used,
      fallbackUsed: update.fallbackUsed ?? this.snapshot.fallbackUsed,
      originalError: Object.prototype.hasOwnProperty.call(update, "originalError") ? update.originalError : this.snapshot.originalError,
      fallbackError: Object.prototype.hasOwnProperty.call(update, "fallbackError") ? update.fallbackError : this.snapshot.fallbackError,
      fallbackManifestUrl: Object.prototype.hasOwnProperty.call(update, "fallbackManifestUrl") ? update.fallbackManifestUrl : this.snapshot.fallbackManifestUrl,
    }
    this.onSnapshot(this.snapshot)
  }
}
