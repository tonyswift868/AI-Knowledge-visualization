export interface DragonBonesAnimation {
  play(name: string, playTimes?: number): unknown
  stop(name?: string): unknown
  hasAnimation(name: string): boolean
  getState(name: string, layer?: number): any
  fadeIn(name: string, fadeInTime?: number, playTimes?: number, layer?: number, group?: string): any
  animationNames: string[]
}

export interface DragonBonesArmatureDisplay extends HTMLElement {
  animation: DragonBonesAnimation
  armature: any
  debugDraw?: boolean
}

export interface DragonBonesPixiFactory {
  parseDragonBonesData(data: unknown, name?: string): unknown
  updateTextureAtlases(textures: unknown[], name?: string): unknown
  buildArmatureDisplay(armatureName: string, dragonBonesName?: string, skinName?: string, textureAtlasName?: string): DragonBonesArmatureDisplay
}

export interface DragonBonesRuntimeGlobal {
  PixiFactory: { factory: DragonBonesPixiFactory }
}

declare global {
  interface Window {
    dragonBones?: DragonBonesRuntimeGlobal
  }
}

export {}
