export type TeacherRendererKind = "dragonbones2d" | "spine2d" | "kks3d" | "none"

export type TeacherPresentationState =
  | "loading"
  | "idle"
  | "greeting"
  | "thinking"
  | "talking"
  | "explaining"
  | "questioning"
  | "praising"
  | "encouraging"
  | "confused"
  | "ending"
  | "error"
  | "hidden"

export type Teacher2DManifest = {
  id: string
  displayName: string
  renderer: TeacherRendererKind
  prototypeOnly?: boolean
  license?: string
  sourceRepository?: string
  sourceCommit?: string
  skeleton: string
  textures: readonly string[]
  armature: string
  defaultAnimation: string
  animationAliases?: Partial<Record<TeacherPresentationState, string>>
  initialScale?: number
  initialOffsetX?: number
  initialOffsetY?: number
}

export type TeacherRendererSnapshot = {
  status: "idle" | "loading" | "ready" | "error" | "hidden"
  renderer: TeacherRendererKind
  armature?: string
  currentAnimation?: string
  semanticState: TeacherPresentationState
  animations: string[]
  skins: string[]
  canvasWidth: number
  canvasHeight: number
  webgl: boolean
  error?: string
  loadedTextures: string[]
  bounds?: { x: number; y: number; width: number; height: number }
}

export interface Teacher2DRenderer {
  load(manifest: Teacher2DManifest): Promise<void>
  unload(): void
  playSemanticState(state: TeacherPresentationState): void
  playAnimation(name: string, loop: boolean): void
  listAnimations(): string[]
  listSkins(): string[]
  setSkin(name: string): void
  setScale(scale: number): void
  setMirror(mirrored: boolean): void
  pause(): void
  resume(): void
  resize(width: number, height: number): void
}
