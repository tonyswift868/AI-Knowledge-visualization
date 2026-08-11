export type TeacherRendererKind = "image2d" | "layered2d" | "dragonbones2d" | "live2d2d" | "spine2d" | "kks3d" | "none"

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
  imageStates?: {
    teaching: string
    swimsuit: string
  }
  live2d?: {
    coreScript?: string
    textureLod?: "full" | "single-auto" | false
    expressions?: Partial<Record<TeacherPresentationState, string>>
    skins?: Record<string, {
      label?: string
      partIds?: readonly string[]
      multiplyColor?: readonly [number, number, number, number]
      screenColor?: readonly [number, number, number, number]
    }>
    parameters?: Partial<Record<
      | "angleX" | "angleY" | "angleZ"
      | "bodyX" | "bodyY" | "bodyZ"
      | "eyeLeftOpen" | "eyeRightOpen"
      | "eyeExpression1" | "eyeExpression2"
      | "mouthOpen" | "mouthForm" | "mouthX" | "mouthPucker" | "mouthShrug"
      | "jawOpen" | "browLeftY" | "browRightY" | "browLeftForm" | "browRightForm" | "eyeBrowX"
      | "cheekPuff" | "breath"
      | "leftHand1" | "leftHand2" | "rightHand1" | "rightHand2",
      string
    >>
  }
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
  setOffset(x: number, y: number): void
  setMirror(mirrored: boolean): void
  pause(): void
  resume(): void
  resize(width: number, height: number): void
}
