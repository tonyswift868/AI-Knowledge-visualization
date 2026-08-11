import type { Teacher2DManifest, Teacher2DRenderer, TeacherPresentationState } from "../teacherPresentationTypes"

/** Explicit no-op adapter kept as the safe replacement boundary for future renderers. */
export class PlaceholderTeacherAdapter implements Teacher2DRenderer {
  private animation = ""
  async load(manifest: Teacher2DManifest) { this.animation = manifest.defaultAnimation }
  unload() { this.animation = "" }
  playSemanticState(_state: TeacherPresentationState) {}
  playAnimation(name: string, _loop = false) { this.animation = name }
  listAnimations() { return this.animation ? [this.animation] : [] }
  listSkins() { return [] }
  setSkin(_name: string) {}
  setScale(_scale: number) {}
  setOffset(_x: number, _y: number) {}
  setMirror(_mirrored: boolean) {}
  pause() {}
  resume() {}
  resize(_width: number, _height: number) {}
}
