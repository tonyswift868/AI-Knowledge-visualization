import type {
  KksLocalTransformPatch,
  KksMutableVec3,
  KksShapeCategory,
  KksShapeKeyframe,
} from "./kksBodyShapeDriver"

export interface KksHeadShapeDriverData {
  version: number
  headId: number
  sliderCount: number
  categories: readonly KksShapeCategory[]
  animation: Readonly<Record<string, readonly KksShapeKeyframe[]>>
}

interface SourceTransform {
  p: KksMutableVec3
  r: KksMutableVec3
  s: KksMutableVec3
}

export const KKS_HEAD_TARGET_BONES = [
  "cf_J_FaceBase", "cf_J_FaceUp_tz", "cf_J_NoseBridge_ty", "cf_J_FaceUp_ty",
  "cf_J_FaceLow_tz", "cf_J_FaceLow_sx", "cf_J_CheekUp2_L", "cf_J_CheekUp2_R",
  "cf_J_ChinLow", "cf_J_CheekLow_s_L", "cf_J_CheekLow_s_R", "cf_J_Chin_Base",
  "cf_J_ChinTip_Base", "cf_J_Nose_tip", "cf_J_NoseBase_rx", "cf_J_NoseBridge_rx",
  "cf_J_CheekUpBase", "cf_J_CheekUp_s_L", "cf_J_CheekUp_s_R", "cf_J_Eye_tx_L",
  "cf_J_Eye_tx_R", "cf_J_megane_rx_ear", "cf_J_Eye_rz_L", "cf_J_Eye_rz_R",
  "cf_J_Eye_tz", "cf_J_Eye01_s_L", "cf_J_Eye01_s_R", "cf_J_Eye05_s_L",
  "cf_J_Eye05_s_R", "cf_J_Eye02_s_L", "cf_J_Eye02_s_R", "cf_J_Eye03_s_L",
  "cf_J_Eye03_s_R", "cf_J_Eye04_s_L", "cf_J_Eye04_s_R", "cf_J_Eye08_s_L",
  "cf_J_Eye08_s_R", "cf_J_Eye07_s_L", "cf_J_Eye07_s_R", "cf_J_Eye06_s_L",
  "cf_J_Eye06_s_R", "cf_J_Mayu_L", "cf_J_Mayu_R", "cf_J_MayuMid_s_L",
  "cf_J_MayuMid_s_R", "cf_J_MayuTip_s_L", "cf_J_MayuTip_s_R", "cf_J_EarBase_ry_L",
  "cf_J_EarBase_ry_R", "cf_J_EarUp_L", "cf_J_EarUp_R", "cf_J_EarLow_L",
  "cf_J_EarLow_R", "cf_J_MouthBase_ty", "cf_J_Mouth_L", "cf_J_Mouth_R",
  "cf_J_MouthBase_rx", "cf_J_Mouthup", "cf_J_MouthLow",
] as const

const zero = (): KksMutableVec3 => [0, 0, 0]
const one = (): KksMutableVec3 => [1, 1, 1]
const clamp01 = (value: number) => Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0.5))
const lerp = (a: number, b: number, t: number) => a + (b - a) * t
const lerpAngle = (a: number, b: number, t: number) => {
  let delta = (b - a) % 360
  if (delta > 180) delta -= 360
  if (delta < -180) delta += 360
  return a + delta * t
}

function sampleCurve(frames: readonly KksShapeKeyframe[], value: number): SourceTransform {
  if (frames.length === 0) return { p: zero(), r: zero(), s: one() }
  if (frames.length === 1) return { p: [...frames[0].position], r: [...frames[0].rotation], s: [...frames[0].scale] }
  const key = clamp01(value) * (frames.length - 1)
  const lo = Math.floor(key)
  const hi = Math.min(frames.length - 1, lo + 1)
  const t = key - lo
  const a = frames[lo]
  const b = frames[hi]
  return {
    p: [lerp(a.position[0], b.position[0], t), lerp(a.position[1], b.position[1], t), lerp(a.position[2], b.position[2], t)],
    r: [lerpAngle(a.rotation[0], b.rotation[0], t), lerpAngle(a.rotation[1], b.rotation[1], t), lerpAngle(a.rotation[2], b.rotation[2], t)],
    s: [lerp(a.scale[0], b.scale[0], t), lerp(a.scale[1], b.scale[1], t), lerp(a.scale[2], b.scale[2], t)],
  }
}

const emptyPatch = (): KksLocalTransformPatch => ({
  position: [null, null, null], rotation: [null, null, null], scale: [null, null, null],
})

/** Exact browser port of KKS ShapeHeadInfoFemale.Update for its 59 face bones. */
export function evaluateKksHeadShape(
  data: KksHeadShapeDriverData,
  sliders: readonly number[],
): Record<string, KksLocalTransformPatch> {
  if (data.sliderCount !== 52) throw new Error(`Unsupported KKS face slider count: ${data.sliderCount}`)
  if (sliders.length < data.sliderCount) throw new Error(`Expected 52 face sliders, received ${sliders.length}`)
  const sources = new Map<string, SourceTransform>()
  for (const name of KKS_HEAD_TARGET_BONES) sources.set(name, { p: zero(), r: zero(), s: one() })
  for (const category of data.categories) {
    const target = sources.get(category.source)
    const frames = data.animation[category.source]
    if (!target || !frames?.length) continue
    const sampled = sampleCurve(frames, sliders[category.slider] ?? 0.5)
    for (let axis = 0; axis < 3; axis += 1) {
      if (category.position[axis]) target.p[axis] = sampled.p[axis]
      if (category.rotation[axis]) target.r[axis] = sampled.r[axis]
      if (category.scale[axis]) target.s[axis] = sampled.s[axis]
    }
  }
  const src = KKS_HEAD_TARGET_BONES.map(name => sources.get(name)!)
  const targets = KKS_HEAD_TARGET_BONES.map(() => emptyPatch())
  const s = (i: number) => src[i]
  const t = (i: number) => targets[i]
  const apply = (dst: (number | null)[], values: readonly (number | undefined)[]) => {
    for (let i = 0; i < 3; i += 1) if (values[i] !== undefined) dst[i] = values[i]!
  }
  const P = (i: number, x?: number, y?: number, z?: number) => apply(t(i).position, [x, y, z])
  const R = (i: number, x?: number, y?: number, z?: number) => apply(t(i).rotation, [x, y, z])
  const S = (i: number, x?: number, y?: number, z?: number) => apply(t(i).scale, [x, y, z])

  // ShapeHeadInfoFemale uses these values directly. The parent head scale is
  // uniform in the female carrier, so FaceBase's lossy-scale cancellation is
  // exactly [source X, 1, 1] here.
  S(0, s(0).s[0], 1, 1)
  P(1, undefined, undefined, s(1).p[2])
  P(2, undefined, s(2).p[1], s(2).p[2])
  P(3, undefined, s(3).p[1]); S(3, ...s(3).s)
  P(4, undefined, undefined, s(4).p[2])
  S(5, s(5).s[0], 1, 1)
  P(6, s(6).p[0], undefined, s(6).p[2]); S(6, s(6).s[0], 1, 1)
  P(7, s(7).p[0], undefined, s(7).p[2]); S(7, s(7).s[0], 1, 1)
  P(8, undefined, s(8).p[1]); S(8, ...s(8).s)
  P(9, undefined, undefined, s(9).p[2]); S(9, 1, 1, s(9).s[2])
  P(10, undefined, undefined, s(10).p[2]); S(10, 1, 1, s(10).s[2])
  P(11, undefined, s(11).p[1], s(11).p[2]); S(11, s(11).s[0], 1, 1)
  P(12, undefined, s(12).p[1], s(12).p[2]); S(12, s(12).s[0], 1, 1)
  P(13, undefined, undefined, s(13).p[2])
  P(14, undefined, s(14).p[1], s(14).p[2])
  P(15, undefined, undefined, s(15).p[2])
  P(16, undefined, s(16).p[1])
  for (const i of [17, 18]) P(i, s(i).p[0], s(i).p[1], s(i).p[2])
  for (const i of [19, 20]) { P(i, undefined, s(i).p[1]); R(i, 0, 0, s(i).r[2]) }
  P(21, undefined, s(21).p[1]); R(21, s(21).r[0], 0, 0)
  for (const i of [22, 23]) { P(i, s(i).p[0]); S(i, s(i).s[0], s(i).s[1], 1) }
  P(24, undefined, undefined, s(24).p[2])
  P(25, s(25).p[0]); P(26, s(26).p[0])
  for (let i = 27; i <= 40; i += 1) P(i, undefined, s(i).p[1])
  for (const i of [41, 42]) { P(i, s(i).p[0], s(i).p[1], s(i).p[2]); R(i, 0, 0, s(i).r[2]) }
  for (let i = 43; i <= 46; i += 1) R(i, 0, 0, s(i).r[2])
  for (const i of [47, 48]) { R(i, 0, s(i).r[1], s(i).r[2]); S(i, ...s(i).s) }
  for (const i of [49, 50]) { P(i, s(i).p[0], s(i).p[1]); S(i, ...s(i).s) }
  for (const i of [51, 52]) P(i, undefined, s(i).p[1])
  P(53, undefined, s(53).p[1], s(53).p[2])
  for (const i of [54, 55]) P(i, s(i).p[0], s(i).p[1])
  for (let i = 56; i <= 58; i += 1) P(i, undefined, undefined, s(i).p[2])

  return Object.fromEntries(KKS_HEAD_TARGET_BONES.map((name, index) => [name, targets[index]]))
}
