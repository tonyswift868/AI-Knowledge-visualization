export type KksVec3 = readonly [number, number, number]
export type KksMutableVec3 = [number, number, number]

export interface KksShapeKeyframe {
  key: number
  position: KksVec3
  rotation: KksVec3
  scale: KksVec3
}

export interface KksShapeCategory {
  slider: number
  source: string
  position: readonly [boolean, boolean, boolean]
  rotation: readonly [boolean, boolean, boolean]
  scale: readonly [boolean, boolean, boolean]
}

export interface KksBodyShapeDriverData {
  version: number
  sliderCount: number
  categories: readonly KksShapeCategory[]
  animation: Readonly<Record<string, readonly KksShapeKeyframe[]>>
  corrections: readonly {
    position: KksVec3
    rotation: KksVec3
    scale: KksVec3
  }[]
}

/**
 * A component set to null is intentionally untouched by the original KKS
 * SetLocalPosition/Rotation/Scale calls and must retain the model's bind value.
 */
export interface KksLocalTransformPatch {
  position: [number | null, number | null, number | null]
  rotation: [number | null, number | null, number | null]
  scale: [number | null, number | null, number | null]
}

export interface KksBodyShapeOptions {
  typeBone?: 0 | 1
  deformedHeight?: number
  deformedHeightAid?: number
  deformedHeadSize?: number
  deformedNeckSize?: number
  correctHeadSize?: number
  correctNeckSize?: number
}

export interface KksLocalTransform {
  position: KksMutableVec3
  rotation: KksMutableVec3
  scale: KksMutableVec3
}

interface SourceTransform {
  p: KksMutableVec3
  r: KksMutableVec3
  s: KksMutableVec3
}

const SOURCE_NAMES = [
  'cf_a_height', 'cf_a_height_aid', 'cf_a_head', 'cf_a_neck', 'cf_a_spine03',
  'cf_a_shoulder', 'cf_a_shoulder_L_aid03', 'cf_a_shoulder_R_aid03',
  'cf_a_arm_L_aid03', 'cf_a_arm_R_aid03', 'cf_a_arm02', 'cf_a_arm03_blend01',
  'cf_a_arm03_blend02', 'cf_a_farm01', 'cf_a_farm02_blend01',
  'cf_a_farm02_blend03', 'cf_a_farm03', 'cf_a_spine02',
  'cf_a_spine02_aid_berry', 'cf_a_spine01', 'cf_a_berry', 'cf_a_waist01',
  'cf_a_waist02', 'cf_a_siri', 'cf_a_thigh01_L', 'cf_a_thigh01_L_aid',
  'cf_a_thigh01_R', 'cf_a_thigh01_R_aid', 'cf_a_thigh02_L_blend01',
  'cf_a_thigh02_L_blend03', 'cf_a_thigh02_R_blend01',
  'cf_a_thigh02_R_blend03', 'cf_a_thigh03_L', 'cf_a_thigh03_R',
  'cf_a_leg01_L', 'cf_a_leg01_R', 'cf_a_leg02_L', 'cf_a_leg02_R',
  'cf_a_leg03', 'cf_a_dan', 'cf_a_bust_ty', 'cf_a_bust00_aid03_sz',
  'cf_a_bust00_aid02_sz', 'cf_a_bust_L_ry', 'cf_a_bust_rx',
  'cf_a_bust01_size', 'cf_a_bust_L_tx', 'cf_a_bust02_size', 'cf_a_bust_tz',
  'cf_a_bust03_size', 'cf_a_bust01_shape1', 'cf_a_bust02_shape1',
  'cf_a_bust03_shape1', 'cf_a_hit_bust_shape1', 'cf_a_hit_bust_shape2',
  'cf_a_bnip01', 'cf_a_bnip01_size', 'cf_a_d_bnip01_size',
  'cf_a_bnip02_size', 'cf_a_bnip015_size', 'cf_a_bnip02',
  'cf_a_bnipacc_stand', 'cf_a_bnipacc_size', 'cf_a_bust_R_ry',
  'cf_a_bust_R_tx', 'cf_a_hit_siri_shape1', 'cf_a_hit_siri_shape2',
  'cf_a_hit_siri_shape3', 'cf_a_hit_siri_shape4', 'cf_a_hit_siri_shape5',
  'cf_a_hit_siri_shape6', 'cf_a_hit_waist_shape1', 'cf_a_hit_waist_shape2',
  'cf_a_hit_waist_shape3', 'cf_a_hit_spinety_shape', 'cf_a_hit_waist_shape4',
  'cf_a_hit_waist_shape5', 'cf_a_hit_berry_shape', 'cf_a_hit_berry_shape2',
  'cf_a_hit_berry_shape3', 'cf_a_hit_spine02_shape1', 'cf_a_hit_spine02_shape2',
  'cf_a_hit_spine02_shape3', 'cf_a_hit_shoulder_shape1',
  'cf_a_hit_shoulder_shape2', 'cf_a_hit_shoulder_shape3', 'cf_a_hit_arm_shape2',
  'cf_a_hit_arm_shape3', 'cf_a_hit_arm_shape4', 'cf_a_hit_spine01_shape1',
  'cf_a_hit_spine01_shape2', 'cf_a_sk_00_00', 'cf_a_sk_00_01', 'cf_a_sk_berry',
  'cf_a_sk_thigh01_sz', 'cf_a_sk_01_00', 'cf_a_sk_01_01',
  'cf_a_sk_thigh01_sx', 'cf_a_sk_02_00', 'cf_a_sk_02_01', 'cf_a_sk_siri',
  'cf_a_sk_03_00', 'cf_a_sk_03_01', 'cf_a_sk_04_00', 'cf_a_sk_04_01',
  'cf_a_sk_05_00', 'cf_a_sk_05_01', 'cf_a_sk_06_00', 'cf_a_sk_06_01',
  'cf_a_sk_07_00', 'cf_a_sk_07_01',
] as const

export const KKS_BODY_TARGET_BONES = [
  'cf_n_height', 'cf_s_hand_L', 'cf_s_hand_R', 'cf_s_head', 'cf_s_neck',
  'cf_s_spine03', 'cf_s_shoulder02_L', 'cf_s_shoulder02_R', 'cf_s_arm01_L',
  'cf_s_arm01_R', 'cf_s_arm02_L', 'cf_s_arm02_R', 'cf_s_arm03_L',
  'cf_s_arm03_R', 'cf_s_forearm01_L', 'cf_s_forearm01_R', 'cf_s_forearm02_L',
  'cf_s_forearm02_R', 'cf_s_wrist_L', 'cf_s_wrist_R', 'cf_s_spine02',
  'cf_s_spine01', 'cf_s_waist01', 'cf_s_waist02', 'cf_s_siri_L', 'cf_s_siri_R',
  'cf_s_thigh01_L', 'cf_s_thigh01_R', 'cf_s_thigh02_L', 'cf_s_thigh02_R',
  'cf_s_thigh03_L', 'cf_s_thigh03_R', 'cf_s_leg01_L', 'cf_s_leg01_R',
  'cf_s_leg02_L', 'cf_s_leg02_R', 'cf_s_leg03_L', 'cf_s_leg03_R', 'cf_d_kokan',
  'cf_s_bust00_L', 'cf_d_bust01_L', 'cf_d_bust02_L', 'cf_d_bust03_L',
  'cf_s_bust01_L', 'cf_s_bust02_L', 'cf_s_bust03_L', 'cf_hit_bust02_L',
  'cf_d_bnip01_L', 'cf_s_bnip01_L', 'cf_s_bnip025_L', 'cf_s_bnip015_L',
  'cf_s_bnip02_L', 'cf_s_bnipacc_L', 'cf_s_bust00_R', 'cf_d_bust01_R',
  'cf_d_bust02_R', 'cf_d_bust03_R', 'cf_s_bust01_R', 'cf_s_bust02_R',
  'cf_s_bust03_R', 'cf_hit_bust02_R', 'cf_d_bnip01_R', 'cf_s_bnip01_R',
  'cf_s_bnip025_R', 'cf_s_bnip015_R', 'cf_s_bnip02_R', 'cf_s_bnipacc_R',
  'cf_hit_siri_L', 'cf_hit_siri_R', 'cf_hit_waist_L', 'cf_hit_berry',
  'cf_hit_spine02_L', 'cf_hit_shoulder_L', 'cf_hit_shoulder_R', 'cf_hit_arm_L',
  'cf_hit_arm_R', 'cf_hit_spine01', 'cf_d_sk_top', 'cf_d_sk_00_00',
  'cf_d_sk_01_00', 'cf_d_sk_02_00', 'cf_d_sk_03_00', 'cf_d_sk_04_00',
  'cf_d_sk_05_00', 'cf_d_sk_06_00', 'cf_d_sk_07_00',
] as const

/** ShapeBodyInfoFemale.UpdateAlways targets outside its 86-entry DstName table. */
export const KKS_BODY_ALWAYS_TARGET_BONES = [
  'cf_d_shoulder_L', 'cf_d_shoulder_R',
] as const

const zero = (): KksMutableVec3 => [0, 0, 0]
const one = (): KksMutableVec3 => [1, 1, 1]
const finiteRate = (value: number) => Number.isFinite(value) ? value : 0.5
const safeDivide = (numerator: number, denominator: number, fallback = 1) => (
  Number.isFinite(numerator) && Number.isFinite(denominator) && Math.abs(denominator) > 1e-8
    ? numerator / denominator
    : fallback
)
const lerp = (a: number, b: number, t: number) => a + (b - a) * t
const deltaAngle = (a: number, b: number) => {
  let delta = (b - a) % 360
  if (delta > 180) delta -= 360
  if (delta < -180) delta += 360
  return delta
}
const lerpAngle = (a: number, b: number, t: number) => a + deltaAngle(a, b) * t

const keepsClampedRotationOutsideNativeRange = (name: string) => (
  (/thigh/i.test(name) && /01/.test(name))
  || (/^cf_a_bust/i.test(name) && /_size$/i.test(name))
)

/**
 * Match AnimationKeyInfo.GetInfo plus BepisPlugins SliderUnlocker.
 * Vanilla evaluates 0..1; persisted values outside that interval are linearly
 * extrapolated from the first/last authored records instead of being clamped.
 */
function sampleCurve(name: string, frames: readonly KksShapeKeyframe[], value: number): SourceTransform {
  if (frames.length === 0) return { p: zero(), r: zero(), s: one() }
  if (frames.length === 1) {
    const f = frames[0]
    return { p: [...f.position], r: [...f.rotation], s: [...f.scale] }
  }
  const rate = finiteRate(value)
  if (rate < 0 || rate > 1) {
    const first = frames[0]
    const second = frames[1]
    const last = frames[frames.length - 1]
    const rotation = first.rotation.map((start, axis) => {
      if (keepsClampedRotationOutsideNativeRange(name)) {
        return rate < 0 ? first.rotation[axis] : last.rotation[axis]
      }
      const firstStep = second.rotation[axis] - start
      let span = last.rotation[axis] - start
      const positive = firstStep >= 0
      if (span > 0 && !positive) span -= 360
      else if (span < 0 && positive) span += 360
      return rate < 0
        ? start - span * Math.abs(rate)
        : last.rotation[axis] + span * Math.abs(rate - 1)
    }) as KksMutableVec3
    return {
      p: first.position.map((start, axis) => lerp(start, last.position[axis], rate)) as KksMutableVec3,
      r: rotation,
      s: first.scale.map((start, axis) => lerp(start, last.scale[axis], rate)) as KksMutableVec3,
    }
  }
  const key = rate * (frames.length - 1)
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

function buildSources(data: KksBodyShapeDriverData, sliders: readonly number[]) {
  const byName = new Map<string, SourceTransform>()
  for (const name of SOURCE_NAMES) byName.set(name, { p: zero(), r: zero(), s: one() })
  for (const category of data.categories) {
    const target = byName.get(category.source)
    const frames = data.animation[category.source]
    if (!target || !frames?.length) continue
    const sampled = sampleCurve(category.source, frames, sliders[category.slider] ?? 0.5)
    for (let axis = 0; axis < 3; axis += 1) {
      if (category.position[axis]) target.p[axis] = sampled.p[axis]
      if (category.rotation[axis]) target.r[axis] = sampled.r[axis]
      if (category.scale[axis]) target.s[axis] = sampled.s[axis]
    }
  }
  return SOURCE_NAMES.map((name) => byName.get(name)!)
}

const emptyPatch = (): KksLocalTransformPatch => ({
  position: [null, null, null], rotation: [null, null, null], scale: [null, null, null],
})

/** Evaluate the native KKS female-body driver for all 86 destination bones. */
export function evaluateKksBodyShape(
  data: KksBodyShapeDriverData,
  sliders: readonly number[],
  options: KksBodyShapeOptions = {},
): Record<string, KksLocalTransformPatch> {
  if (data.sliderCount !== 44) throw new Error(`Unsupported KKS body slider count: ${data.sliderCount}`)
  if (sliders.length < data.sliderCount) throw new Error(`Expected 44 body sliders, received ${sliders.length}`)
  const src = buildSources(data, sliders)
  const targets = KKS_BODY_TARGET_BONES.map(() => emptyPatch())
  const s = (i: number) => src[i]
  const t = (i: number) => targets[i]
  const apply = (dst: (number | null)[], values: readonly (number | undefined)[]) => {
    for (let i = 0; i < 3; i += 1) if (values[i] !== undefined) dst[i] = values[i]!
  }
  const P = (i: number, x?: number, y?: number, z?: number) => apply(t(i).position, [x, y, z])
  const R = (i: number, x?: number, y?: number, z?: number) => apply(t(i).rotation, [x, y, z])
  const S = (i: number, x?: number, y?: number, z?: number) => apply(t(i).scale, [x, y, z])
  const corrected = options.typeBone === 1
  const c = (i: number) => corrected
    ? (data.corrections[i] ?? { position: zero(), rotation: zero(), scale: zero() })
    : { position: zero(), rotation: zero(), scale: zero() }
  const height = options.deformedHeight ?? 1
  const heightAid = options.deformedHeightAid ?? 1
  const headSize = options.deformedHeadSize ?? 1
  const neckSize = options.deformedNeckSize ?? 1
  // ShapeBodyInfoFemale only changes these to .91 when initialized with male
  // sex; this female-card driver therefore uses the native female default.
  const correctHead = options.correctHeadSize ?? 1
  const correctNeck = options.correctNeckSize ?? 1
  const v: KksMutableVec3 = [s(0).s[0] * height, s(0).s[1] * height, s(0).s[2] * height]
  const va: KksMutableVec3 = [s(1).s[0] * heightAid, s(1).s[1] * heightAid, s(1).s[2] * heightAid]

  S(0, v[0], v[1], v[2]); S(1, va[0], va[1], va[2]); S(2, va[0], va[1], va[2])
  P(3, undefined, s(2).p[1] + c(2).position[1]); S(3, s(2).s[0] * va[0] * correctHead * headSize + c(2).scale[0], s(2).s[1] * va[1] * correctHead * headSize + c(2).scale[1], s(2).s[2] * va[2] * correctHead * headSize + c(2).scale[2])
  P(4, undefined, c(1).position[1], s(3).p[2]); R(4, c(1).rotation[0], 0, 0); S(4, s(3).s[0] * va[0] * correctNeck * neckSize + c(1).scale[0], 1, s(3).s[2] * va[2] * correctNeck * neckSize + c(1).scale[2])
  P(5, undefined, undefined, s(4).p[2] + c(5).position[2]); R(5, c(5).rotation[0], 0, 0); S(5, s(4).s[0] * va[0] + c(5).scale[0], 1, s(4).s[2] * va[2] + c(5).scale[2])
  P(6, s(6).p[0] + s(5).p[0] + c(14).position[0], s(5).p[1] + c(14).position[1], c(14).position[2]); S(6, s(5).s[0], s(5).s[1] * va[1] + c(14).scale[1], s(5).s[2] * s(6).s[2] * va[2] + c(14).scale[2])
  P(7, s(7).p[0] - s(5).p[0] + c(15).position[0], s(5).p[1] + c(15).position[1], c(15).position[2]); S(7, s(5).s[0], s(5).s[1] * va[1] + c(15).scale[1], s(5).s[2] * s(7).s[2] * va[2] + c(15).scale[2])
  P(8, s(8).p[0], s(5).p[1] + c(16).position[1]); S(8, 1 + c(16).scale[0], s(5).s[1] * va[1] + c(16).scale[1], s(5).s[2] * s(8).s[2] * va[2] + c(16).scale[2])
  P(9, s(9).p[0], s(5).p[1] + c(17).position[1]); S(9, 1 + c(17).scale[0], s(5).s[1] * va[1] + c(17).scale[1], s(5).s[2] * s(9).s[2] * va[2] + c(17).scale[2])
  for (const i of [10, 11]) { P(i, undefined, s(10).p[1] + c(i + 8).position[1], s(10).p[2] + c(i + 8).position[2]); S(i, 1, s(10).s[1] * va[1] + c(i + 8).scale[1], s(10).s[2] * va[2] + c(i + 8).scale[2]) }
  for (const i of [12, 13]) { P(i, undefined, c(i + 8).position[1]); S(i, 1, s(11).s[1] * s(12).s[1] * va[1] + c(i + 8).scale[1], s(11).s[2] * s(12).s[2] * va[2] + c(i + 8).scale[2]) }
  for (const i of [14, 15]) { P(i, undefined, c(i + 8).position[1]); S(i, 1, s(13).s[1] * va[1], s(13).s[2] * va[2]) }
  for (const i of [16, 17]) { R(i, 0, c(i + 8).rotation[1], c(i + 8).rotation[2]); S(i, 1, s(14).s[1] * s(15).s[1] * va[1] + c(i + 8).scale[1], s(14).s[2] * s(15).s[2] * va[2] + c(i + 8).scale[2]) }
  for (const i of [18, 19]) S(i, 1 + c(i + 8).scale[0], s(16).s[1] * va[1] + c(i + 8).scale[1], s(16).s[2] * va[2])
  P(20, undefined, undefined, s(17).p[2] + s(18).p[2] + c(4).position[2]); S(20, s(17).s[0] * va[0] + c(4).scale[0], 1, s(17).s[2] * va[2] * s(18).s[2] + c(4).scale[2])
  P(21, undefined, s(19).p[1] + s(20).p[1], s(19).p[2] + s(20).p[2] + c(3).position[2]); R(21, s(20).r[0], 0, 0); S(21, s(19).s[0] * va[0] * s(20).s[0] + c(3).scale[0], s(20).s[1], s(19).s[2] * va[2] * s(20).s[2] + c(3).scale[2])
  P(22, undefined, s(20).p[1], s(21).p[2] + s(20).p[2]); R(22, s(20).r[0], 0, 0); S(22, s(21).s[0] * va[0] * s(20).s[0], s(20).s[1], s(21).s[2] * va[2] * s(20).s[2])
  P(23, undefined, undefined, s(22).p[2]); S(23, s(22).s[0] * va[0] + c(0).scale[0], 1, s(22).s[2] * va[2])
  for (const [i, ci] of [[24, 28], [25, 29]] as const) { P(i, c(ci).position[0], s(23).p[1], s(23).p[2] + s(22).p[2] * .3); R(i, s(23).r[0] + c(ci).rotation[0], 0, c(ci).rotation[2]); S(i, s(23).s[0] + (s(22).s[0] - 1) * .5 + c(ci).scale[0], s(23).s[1], s(23).s[2] + (s(22).s[2] - 1) * .5 + (s(21).s[2] - 1) * .5) }
  const upperLeg = (i: number, a: number, aid: number, ci: number) => { P(i, s(a).p[0] + s(aid).p[0] + c(ci).position[0], undefined, s(a).p[2] + c(ci).position[2]); R(i, s(a).r[0] + c(ci).rotation[0], c(ci).rotation[1], s(a).r[2] + c(ci).rotation[2]); S(i, s(a).s[0] * s(aid).s[0] * va[0] + c(ci).scale[0], 1, s(a).s[2] * s(aid).s[2] * va[2] + c(ci).scale[2]) }
  upperLeg(26, 24, 25, 6); upperLeg(27, 26, 27, 7)
  const middleLeg = (i: number, a: number, aid: number, ci: number) => { P(i, s(a).p[0] + s(aid).p[0] + c(ci).position[0], undefined, s(a).p[2] + s(aid).p[2]); S(i, s(a).s[0] * s(aid).s[0] * va[0] + c(ci).scale[0], 1, s(a).s[2] * s(aid).s[2] * va[2] + c(ci).scale[2]) }
  middleLeg(28, 28, 29, 8); middleLeg(29, 30, 31, 9)
  for (const [i, a, ci] of [[30, 32, 10], [31, 33, 11]] as const) { P(i, s(a).p[0], undefined, s(a).p[2] + c(ci).position[2]); R(i, s(a).r[0], 0, s(a).r[2]); S(i, s(a).s[0] * va[0] + c(ci).scale[0], 1, s(a).s[2] * va[2]) }
  for (const [i, a, ci] of [[32, 34, 12], [33, 35, 13]] as const) { P(i, s(a).p[0], undefined, s(a).p[2]); S(i, s(a).s[0] * va[0] + c(ci).scale[0], 1, s(a).s[2] * va[2] + c(ci).scale[2]) }
  for (const [i, a] of [[34, 36], [35, 37]] as const) { P(i, undefined, s(a).p[1], s(a).p[2]); S(i, s(a).s[0] * va[0], 1, s(a).s[2] * va[2]) }
  S(36, s(38).s[0], 1, s(38).s[2]); S(37, s(38).s[0], 1, s(38).s[2]); P(38, undefined, undefined, s(39).p[2])

  evaluateBustTargets(s, P, R, S)
  evaluateCollisionAndSkirtTargets(s, va, P, R, S)
  return Object.fromEntries(KKS_BODY_TARGET_BONES.map((name, i) => [name, targets[i]]))
}

/**
 * Match ShapeBodyInfoFemale.UpdateAlways's shoulder-root correction.
 *
 * The game executes this after the ordinary 86-target ShapeBody pass. Keeping
 * it separate preserves the native DstName contract while letting the web
 * runtime apply both operations from one event-driven shape update.
 */
export function evaluateKksBodyAlwaysShape(
  data: KksBodyShapeDriverData,
  options: KksBodyShapeOptions = {},
): Record<(typeof KKS_BODY_ALWAYS_TARGET_BONES)[number], KksLocalTransformPatch> {
  const correctionX = (index: number) => {
    const value = Number(data.corrections[index]?.position?.[0])
    if (!Number.isFinite(value)) throw new Error(`Missing KKS body correction ${index}`)
    return value
  }
  const corrected = options.typeBone === 1
  return {
    cf_d_shoulder_L: {
      position: [corrected ? correctionX(30) : -0.01563369, null, null],
      rotation: [null, null, null],
      scale: [null, null, null],
    },
    cf_d_shoulder_R: {
      position: [corrected ? correctionX(31) : 0.01560147, null, null],
      rotation: [null, null, null],
      scale: [null, null, null],
    },
  }
}

type SourceAt = (index: number) => SourceTransform
type PatchAxis = (index: number, x?: number, y?: number, z?: number) => void

function evaluateBustTargets(s: SourceAt, P: PatchAxis, R: PatchAxis, S: PatchAxis) {
  const bustSide = (right: boolean) => {
    const d = right ? 14 : 0
    const ry = right ? 63 : 43
    const tx = right ? 64 : 46
    const mirror = right ? -1 : 1
    P(39 + d, undefined, s(40).p[1], s(41).p[2] + s(42).p[2] + s(43).p[2] + s(40).p[2] + s(44).p[2]); R(39 + d, s(44).r[0], 0, 0)
    // `cf_a_bust_L_tx` and `cf_a_bust_R_tx` already carry their authored
    // side sign (-X for L, +X for R). Mirroring the right source a second
    // time puts both breast roots on the left side of the shared body.
    P(40 + d, s(tx).p[0], s(45).p[1], s(45).p[2]); R(40 + d, s(45).r[0], right ? s(ry).r[1] - s(45).r[1] : s(ry).r[1] + s(45).r[1], 0); S(40 + d, ...s(45).s)
    P(41 + d, undefined, s(47).p[1], s(47).p[2] + s(48).p[2]); R(41 + d, s(47).r[0], 0, 0); S(41 + d, ...s(47).s)
    P(42 + d, undefined, s(49).p[1], s(49).p[2] + s(48).p[2] / 2); R(42 + d, s(49).r[0], 0, 0); S(42 + d, ...s(49).s)
    P(43 + d, s(50).p[0] * mirror, s(50).p[1], s(50).p[2]); R(43 + d, s(50).r[0], s(50).r[1] * mirror, 0); S(43 + d, s(50).s[0], s(50).s[1], s(48).s[2])
    P(44 + d, s(51).p[0] * mirror, s(51).p[1], s(51).p[2]); R(44 + d, s(51).r[0], s(51).r[1] * mirror, 0); S(44 + d, s(51).s[0] * s(48).s[0], s(51).s[1] * s(48).s[1], s(51).s[2])
    P(45 + d, undefined, s(52).p[1], s(52).p[2]); R(45 + d, s(52).r[0], 0, 0); S(45 + d, s(52).s[0] * s(48).s[0], s(52).s[1] * s(48).s[1], 1)
    P(46 + d, s(53).p[0] * mirror, s(53).p[1], s(53).p[2] + s(54).p[2]); S(46 + d, s(53).s[0] * s(54).s[0], s(53).s[1] * s(54).s[0], s(53).s[2] * s(54).s[0])
    P(47 + d, undefined, undefined, s(55).p[2] + s(56).p[2]); S(47 + d, s(57).s[0], s(57).s[0], s(57).s[2])
    P(48 + d, undefined, undefined, -(s(55).p[2] - .01) * 1.2); S(48 + d, s(55).s[0] * s(56).s[0], s(55).s[1] * s(56).s[1], s(55).s[2] * s(56).s[2])
    S(49 + d, safeDivide(s(58).s[0] * 1.2, s(55).s[0]), safeDivide(s(58).s[1] * 1.2, s(55).s[1]), safeDivide(1, s(55).s[2]))
    P(50 + d, undefined, undefined, .0025 + s(59).p[2] - (s(55).p[2] - .01)); S(50 + d, .1 + s(59).s[1] * s(59).s[0], .1 + s(59).s[1] * s(59).s[0], .1 + s(59).s[2] * s(59).s[0])
    P(51 + d, undefined, undefined, .004 + s(60).p[2] + s(55).p[0] + s(58).p[2]); S(51 + d, s(60).s[0] * s(58).s[0], s(60).s[1] * s(58).s[1], s(60).s[2] * s(58).s[2])
    P(52 + d, undefined, undefined, s(61).p[2]); S(52 + d, safeDivide(safeDivide(s(61).s[0], s(60).s[0]), s(58).s[0]) * s(62).s[0], safeDivide(safeDivide(s(61).s[1], s(60).s[1]), s(58).s[1]) * s(62).s[1], safeDivide(safeDivide(s(61).s[2], s(60).s[2]), s(58).s[2]) * s(62).s[2])
  }
  bustSide(false)
  bustSide(true)
}

function evaluateCollisionAndSkirtTargets(s: SourceAt, va: KksVec3, P: PatchAxis, R: PatchAxis, S: PatchAxis) {
  const hitSiriScale: KksMutableVec3 = [s(65).s[0] * s(67).s[0] * s(68).s[0] * va[0] * s(70).s[0], s(65).s[1] * s(67).s[1] * s(68).s[1] * va[1] * s(70).s[0], s(65).s[2] * s(67).s[2] * s(68).s[2] * va[2] * s(70).s[0]]
  const siriX = s(65).p[0] + s(66).p[0] + s(67).p[0] + s(68).p[0] + s(69).p[0] + s(70).p[0]
  const siriY = s(67).p[1] + s(68).p[1] + s(70).p[1]
  const siriZ = s(65).p[2] + s(67).p[2] + s(70).p[2]
  P(67, siriX, siriY, siriZ); S(67, ...hitSiriScale)
  P(68, -siriX, siriY, siriZ); S(68, ...hitSiriScale)
  P(69, undefined, s(71).p[1] + s(72).p[1] + s(73).p[1] + s(74).p[1] + s(75).p[1] + s(76).p[1], s(72).p[2] + s(73).p[2]); S(69, s(71).s[0] * s(72).s[0] * va[0] * s(73).s[0] * s(76).s[0] * s(75).s[0], s(71).s[0] * s(72).s[0] * va[1] * s(73).s[1] * s(76).s[1] * s(75).s[1], s(71).s[0] * s(72).s[0] * va[2] * s(73).s[2] * s(76).s[2] * s(75).s[2])
  P(70, s(77).p[0], s(77).p[1] + s(74).p[1] * 2 + s(78).p[1] + s(79).p[1], s(77).p[2] + s(78).p[2] + s(79).p[2]); S(70, s(77).s[0] * s(78).s[0] * va[0] * s(79).s[0], s(77).s[1] * s(78).s[1] * va[1] * s(79).s[1], s(77).s[2] * s(78).s[2] * va[2] * s(79).s[2])
  P(71, undefined, s(80).p[1] + s(81).p[1] + s(82).p[1], s(82).p[2]); S(71, s(81).s[0] * s(82).s[0] * va[0], s(81).s[1] * s(82).s[1] * va[1], s(81).s[2] * s(82).s[2] * va[2])
  const shoulderX = s(83).p[0] + s(84).p[0] + s(85).p[0]
  const shoulderY = s(83).p[1] + s(84).p[1] + s(85).p[1]
  const shoulderZ = s(83).p[2] + s(84).p[2]
  const shoulderScale: KksMutableVec3 = [s(83).s[0] * s(84).s[0] * s(85).s[0] * va[0], s(83).s[1] * s(84).s[1] * s(85).s[1] * va[1], s(83).s[2] * s(84).s[2] * s(85).s[2] * va[2]]
  P(72, shoulderX, shoulderY, shoulderZ); S(72, ...shoulderScale)
  P(73, -shoulderX, shoulderY, shoulderZ); S(73, ...shoulderScale)
  const armHitY = s(86).s[1] * s(87).s[1] * s(88).s[1] * va[0]
  const armHitZ = s(86).s[2] * s(87).s[2] * s(88).s[2] * va[0]
  S(74, 1, armHitY, armHitZ); S(75, 1, armHitY, armHitZ)
  P(76, undefined, undefined, s(89).p[2] + s(90).p[2]); S(76, s(89).s[0] * s(90).s[0] * va[0], s(89).s[1] * s(90).s[1] * va[1], s(89).s[2] * s(90).s[2] * va[2])
  S(77, va[0], va[1], va[2])
  const r94 = s(94).r[0] > 180 ? s(94).r[0] - 360 : s(94).r[0]
  const r97 = s(97).r[0] > 180 ? s(97).r[0] - 360 : s(97).r[0]
  P(78, s(91).p[0] + s(92).p[0], undefined, s(91).p[2] + s(92).p[2] + s(93).p[2]); R(78, s(91).r[0] + s(92).r[0] + s(92).r[2] + r94, s(91).r[1], s(91).r[2])
  P(79, s(95).p[0] + s(96).p[0] - s(93).p[0] * .5, undefined, s(95).p[2] + s(96).p[2] + s(93).p[2]); R(79, s(95).r[0] + s(96).r[0] + s(96).r[2] + r94 * .6 + r97 * .6, s(95).r[1], s(95).r[2])
  P(80, s(98).p[0] + s(99).p[0] + s(100).p[0] * .5 - s(93).p[0], undefined, s(98).p[2] + s(99).p[2]); R(80, s(98).r[0] + s(99).r[0] + s(99).r[2] + r97, s(98).r[1], s(98).r[2])
  P(81, s(101).p[0] + s(102).p[0] + s(100).p[0], undefined, s(101).p[2] + s(102).p[2] + s(100).p[2]); R(81, s(101).r[0] + s(102).r[0] + s(102).r[2] + r94 * .6 + r97 * .6 + s(100).r[0], s(101).r[1], s(101).r[2])
  P(82, s(103).p[0] + s(104).p[0], undefined, s(103).p[2] + s(104).p[2] + s(100).p[2]); R(82, s(103).r[0] + s(104).r[0] + s(104).r[2] + r94 + s(100).r[0], s(103).r[1], s(103).r[2])
  P(83, s(105).p[0] + s(106).p[0] - s(100).p[0], undefined, s(105).p[2] + s(106).p[2] + s(100).p[2]); R(83, s(105).r[0] + s(106).r[0] + s(106).r[2] + r94 * .6 + r97 * .6 + s(100).r[0], s(105).r[1], s(105).r[2])
  P(84, s(107).p[0] + s(108).p[0] - s(100).p[0] * .5 + s(93).p[0], undefined, s(107).p[2] + s(108).p[2]); R(84, s(107).r[0] + s(108).r[0] + s(108).r[2] + r97, s(107).r[1], s(107).r[2])
  P(85, s(109).p[0] + s(110).p[0] + s(93).p[0] * .5, undefined, s(109).p[2] + s(110).p[2] + s(93).p[2]); R(85, s(109).r[0] + s(110).r[0] + s(110).r[2] + r94 * .6 + r97 * .6, s(109).r[1], s(109).r[2])
}

/** Merge a native partial SetLocal* result with a model's authored bind transform. */
export function mergeKksLocalTransformPatch(base: KksLocalTransform, patch: KksLocalTransformPatch): KksLocalTransform {
  const merge = (source: KksMutableVec3, values: readonly (number | null)[]): KksMutableVec3 => [
    values[0] !== null && Number.isFinite(values[0]) ? values[0] : source[0],
    values[1] !== null && Number.isFinite(values[1]) ? values[1] : source[1],
    values[2] !== null && Number.isFinite(values[2]) ? values[2] : source[2],
  ]
  return {
    position: merge(base.position, patch.position),
    rotation: merge(base.rotation, patch.rotation),
    scale: merge(base.scale, patch.scale),
  }
}
