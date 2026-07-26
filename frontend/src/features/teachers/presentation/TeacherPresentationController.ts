import { createTeacher2DRenderer } from "./Teacher2DRenderer"
import { loadTeacher2DManifest } from "./teacherPresentationManifest"
import type { Teacher2DManifest, Teacher2DRenderer, TeacherPresentationState, TeacherRendererSnapshot } from "./teacherPresentationTypes"

export type TeacherPresentationControllerOptions = {
  onSnapshot: (snapshot: TeacherRendererSnapshot) => void
}

export class TeacherPresentationController {
  private readonly onSnapshot: TeacherPresentationControllerOptions["onSnapshot"]
  private renderer: Teacher2DRenderer | null = null
  private manifest: Teacher2DManifest | null = null
  private mounted = false
  private semanticState: TeacherPresentationState = "loading"
  private snapshot: TeacherRendererSnapshot = {
    status: "loading", renderer: "image2d", semanticState: "loading", animations: [], skins: [], canvasWidth: 0, canvasHeight: 0, webgl: false, loadedTextures: [],
  }

  constructor(options: TeacherPresentationControllerOptions) { this.onSnapshot = options.onSnapshot }

  async mount(host: HTMLElement, manifestUrl?: string): Promise<void> {
    if (this.mounted) return
    this.mounted = true
    this.publish({ status: "loading", semanticState: this.semanticState })
    try {
      const manifest = await loadTeacher2DManifest(manifestUrl)
      if (!this.mounted) return
      this.manifest = manifest
      this.renderer = createTeacher2DRenderer(manifest.renderer, host, {
        host,
        onSnapshot: value => this.publish({ status: "ready", armature: manifest.armature, ...value }),
        onError: error => this.publish({ status: "error", error: error.message, semanticState: "error" }),
      })
      await this.renderer.load(manifest)
      if (!this.mounted) return
      this.publish({ status: "ready", renderer: manifest.renderer, armature: manifest.armature, animations: this.renderer.listAnimations(), skins: this.renderer.listSkins(), semanticState: this.semanticState })
      this.setState(this.semanticState)
    } catch (error) {
      if (!this.mounted) return
      const message = error instanceof Error ? error.message : String(error)
      this.publish({ status: "error", error: message, semanticState: "error" })
      throw error
    }
  }

  setState(state: TeacherPresentationState): void {
    this.semanticState = state
    if (state === "hidden") this.renderer?.pause()
    else this.renderer?.playSemanticState(state)
    this.publish({ semanticState: state, status: state === "hidden" ? "hidden" : this.snapshot.status })
  }
  playAnimation(name: string, loop = false): void { this.renderer?.playAnimation(name, loop) }
  setScale(value: number): void { this.renderer?.setScale(value) }
  setMirror(value: boolean): void { this.renderer?.setMirror(value) }
  setSkin(value: string): void { this.renderer?.setSkin(value) }
  pause(): void { this.renderer?.pause(); this.publish({ status: "ready" }) }
  resume(): void { this.renderer?.resume(); this.publish({ status: "ready" }) }
  reset(): void { this.renderer?.setScale(1); this.renderer?.setMirror(false); this.setState("idle") }
  getSnapshot(): TeacherRendererSnapshot { return this.snapshot }
  dispose(): void { this.mounted = false; this.renderer?.unload(); this.renderer = null; this.manifest = null }

  private publish(update: Partial<TeacherRendererSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...update, animations: update.animations || this.snapshot.animations, skins: update.skins || this.snapshot.skins, loadedTextures: update.loadedTextures || this.snapshot.loadedTextures, semanticState: update.semanticState || this.semanticState }
    this.onSnapshot(this.snapshot)
  }
}
