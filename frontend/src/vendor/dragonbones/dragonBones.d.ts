import type * as PIXI from "pixi.js"

export interface DragonBonesAnimationState {
  totalTime: number
  currentTime: number
  resetToPose: boolean
  stop(): void
}

export interface DragonBonesAnimation {
  play(name: string, playTimes?: number): DragonBonesAnimationState | null
  stop(name?: string): void
  hasAnimation(name: string): boolean
  getState(name: string, layer?: number): DragonBonesAnimationState | null
  fadeIn(name: string, fadeInTime?: number, playTimes?: number, layer?: number, group?: string): DragonBonesAnimationState | null
  animationNames: ReadonlyArray<string>
}

export interface DragonBonesCanvasData { x: number; y: number; width: number; height: number }
export interface DragonBonesArmatureData { name: string; canvas?: DragonBonesCanvasData; aabb?: { x: number; y: number; width: number; height: number } }
export interface DragonBonesArmature { armatureData: DragonBonesArmatureData; animation: DragonBonesAnimation; clock: { time: number } }

export interface DragonBonesArmatureDisplay extends PIXI.Sprite {
  animation: DragonBonesAnimation
  armature: DragonBonesArmature
}

export interface DragonBonesPixiFactory {
  parseDragonBonesData(data: unknown, name?: string): unknown
  updateTextureAtlases(textures: Array<PIXI.Texture | null | undefined>, name?: string): void
  buildArmatureDisplay(armatureName: string, dragonBonesName?: string, skinName?: string, textureAtlasName?: string): DragonBonesArmatureDisplay | null
  removeDragonBonesData(name: string, disposeData?: boolean): void
}

export interface DragonBonesPixiFactoryClass {
  factory: DragonBonesPixiFactory
  useSharedTicker: boolean
  advanceTime(passedTime: number): void
}

export interface DragonBonesRuntimeGlobal {
  PixiFactory: DragonBonesPixiFactoryClass
}

declare global {
  interface Window {
    dragonBones?: DragonBonesRuntimeGlobal
    PIXI?: typeof PIXI
  }
}

export {}
