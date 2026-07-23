export type KksMutableVec3 = [number, number, number]
export type KksLocalTransformPatch = {
  position: [number | null, number | null, number | null]
  rotation: [number | null, number | null, number | null]
  scale: [number | null, number | null, number | null]
}
export type KksShapeKeyframe = {
  position: KksMutableVec3
  rotation: KksMutableVec3
  scale: KksMutableVec3
}
export type KksShapeCategory = {
  source: string
  slider: number
  position: [boolean, boolean, boolean]
  rotation: [boolean, boolean, boolean]
  scale: [boolean, boolean, boolean]
}
