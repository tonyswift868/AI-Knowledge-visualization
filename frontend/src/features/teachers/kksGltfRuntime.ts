import {
  evaluateKksBodyAlwaysShape,
  evaluateKksBodyShape,
  type KksBodyShapeDriverData,
  type KksLocalTransformPatch,
} from "./kksBodyShapeDriver"
import { evaluateKksHeadShape, type KksHeadShapeDriverData } from "./kksHeadShapeDriver"
import bodyDriverJson from "../../../../legacy/koikatsu-3d/resources/web/kks-body-shape-driver.json"
import headDriver0Json from "../../../../legacy/koikatsu-3d/resources/web/kks-head-shape-driver-0.json"
import headDriver200Json from "../../../../legacy/koikatsu-3d/resources/web/kks-head-shape-driver-200.json"
import headDriver201Json from "../../../../legacy/koikatsu-3d/resources/web/kks-head-shape-driver-201.json"

/** Original ChaFileBody.shapeValueBody order shared by every female KKS card. */
export const KKS_BODY_SLIDER_NAMES = [
  "height", "head_size", "neck_width", "neck_depth", "bust_size", "bust_y",
  "bust_rot_x", "bust_x", "bust_rot_y", "bust_sharp", "bust_form",
  "areola_bulge", "nip_weight", "nip_stand", "body_shoulder_width",
  "body_shoulder_depth", "body_upper_width", "body_upper_depth",
  "body_lower_width", "body_lower_depth", "waist_y", "belly",
  "waist_upper_width", "waist_upper_depth", "waist_lower_width",
  "waist_lower_depth", "hip_width", "hip_rot_x", "thigh_upper_width",
  "thigh_upper_depth", "thigh_lower_width", "thigh_lower_depth",
  "knee_lower_width", "knee_lower_depth", "calf", "ankle_width",
  "ankle_depth", "shoulder_width", "shoulder_depth", "arm_upper_width",
  "arm_upper_depth", "elbow_width", "elbow_depth", "arm_lower_width",
] as const

export const KKS_DEFAULT_FEMALE_BODY_VALUES = [
  0.300000012, 0.597580492, 0.405999988, 0.404000014, 0.427419364,
  0.467741936, 0.780645132, 0.393548399, 0.593548358, 0.397483885,
  0.623096764, 0.554838717, 0.621096790, 0.490322590, 0.300000012,
  0.129032254, 0.153806537, 0.809161305, 0.442999989, 0.423000008,
  0.637000024, 0.093000412, 0.150000006, 0.693290055, 0,
  0.374000013, 0.680644870, 0.680516124, 0.328322351, 0.192000002,
  0.338999987, 0.368999988, 0.360000014, 0.396967739, 0.315483868,
  0.442000002, 0.439000010, 0.444000006, 0.444000006, 0.314999998,
  0.324999988, 0.358999997, 0, 0.073000006,
] as const

export const canonicalKksBoneName = (value: string) => (
  // `cf_s_elbo` and `cf_s_elboback` are separate KKS deformation bones.
  // Only normalize the alternate English spelling; collapsing both to
  // "elbow" silently loses the back-of-elbow corrective joint.
  value.trim().toLowerCase().replace(/elbowback/g, "elboback")
)

export function nextKksCameraZoom(current: number, deltaY: number): number {
  const zoom = Number.isFinite(current) ? current : 1
  const factor = deltaY < 0 ? 1.06 : deltaY > 0 ? .96 : 1
  return Math.min(2.7, Math.max(.55, zoom * factor))
}

export type KksJointSide = "left" | "right"

export type KksJointCorrectionOffset = {
  bone: string
  position: [number, number, number]
}

/**
 * Port of KKBP Importer's cf_s_elbo / cf_s_elboback driver expressions.
 * Blender driver locations and glTF bone-node translations share the same
 * local coordinate basis in the recovered 886-bone master.
 */
export function kksElbowCorrectionOffsets(
  side: KksJointSide,
  bendRadians: number,
  controllerLength: number,
): KksJointCorrectionOffset[] {
  const bend = Number.isFinite(bendRadians) ? bendRadians : 0
  const length = Number.isFinite(controllerLength) ? Math.max(0, controllerLength) : 0
  const suffix = side === "left" ? "L" : "R"
  const zDirection = side === "left" ? -1 : 1
  return [
    {
      bone: `cf_s_elboback_${suffix}`,
      position: [
        bend * length * -0.7,
        0,
        bend * length * 0.6 * zDirection,
      ],
    },
    {
      bone: `cf_s_elbo_${suffix}`,
      position: [
        bend * length * 0.025,
        0,
        bend * length * 0.025 * zDirection,
      ],
    },
  ]
}

export function kksBodySliders(values?: Record<string, number>): number[] {
  return KKS_BODY_SLIDER_NAMES.map((name, index) => {
    const value = Number(values?.[name] ?? KKS_DEFAULT_FEMALE_BODY_VALUES[index])
    return Number.isFinite(value) ? value : KKS_DEFAULT_FEMALE_BODY_VALUES[index]
  })
}

export function evaluateKksBodyPatchSet(
  driver: KksBodyShapeDriverData,
  values?: Record<string, number>,
  typeBone?: number,
): Record<string, KksLocalTransformPatch> {
  const options = { typeBone: typeBone === 1 ? 1 as const : 0 as const }
  return {
    ...evaluateKksBodyShape(driver, kksBodySliders(values), options),
    ...evaluateKksBodyAlwaysShape(driver, options),
  }
}

export function relativeKksPatch(
  patch: KksLocalTransformPatch,
  baseline: KksLocalTransformPatch,
): KksLocalTransformPatch {
  return {
    position: patch.position.map((value, index) => {
      const base = baseline.position[index]
      return value === null || base === null ? null : value - base
    }) as KksLocalTransformPatch["position"],
    rotation: patch.rotation.map((value, index) => {
      const base = baseline.rotation[index]
      if (value === null || base === null) return null
      return ((value - base + 540) % 360) - 180
    }) as KksLocalTransformPatch["rotation"],
    scale: patch.scale.map((value, index) => {
      const base = baseline.scale[index]
      return value === null || base === null || Math.abs(base) < 1e-8 ? null : value / base
    }) as KksLocalTransformPatch["scale"],
  }
}

export type BundledKksHeadProfile = 0 | 200 | 201

/** Only these vanilla female heads have exact ShapeAnime data in the bundle. */
export function resolveBundledKksHeadProfile(headId?: number | null): BundledKksHeadProfile | null {
  return headId === 0 || headId === 200 || headId === 201 ? headId : null
}

export function kksFaceSliders(values?: Record<string, number>): number[] {
  return Array.from({ length: 52 }, (_, index) => {
    const value = Number(values?.[String(index)] ?? .5)
    return Number.isFinite(value) ? value : .5
  })
}

export function evaluateKksFacePatchSet(
  driver: KksHeadShapeDriverData,
  values?: Record<string, number>,
): Record<string, KksLocalTransformPatch> {
  return evaluateKksHeadShape(driver, kksFaceSliders(values))
}

const KKS_MORPH_NAME_SUFFIX = /(?:[\u180e\s]+|\.\d+)+$/g

export function canonicalKksMorphName(value: string): string {
  return value.replace(KKS_MORPH_NAME_SUFFIX, "").trim()
}

export function kksExpressionWeight(
  morphName: string,
  smileValue: number,
  eyeOpenValue: number,
): number | null {
  const smile = Math.min(1, Math.max(0, Number(smileValue) || 0))
  const eyeOpen = Math.min(1, Math.max(0, Number(eyeOpenValue) || 0))
  switch (canonicalKksMorphName(morphName)) {
    case "Mouth Default":
      return 1 - smile
    case "Mouth 笑顔":
      return smile
    case "Eye 默认":
      return (1 - smile) * eyeOpen
    case "Eye 笑顔":
      return smile * eyeOpen
    case "Eye 闭眼":
      return 1 - eyeOpen
    case "Eyebrow Default":
      return 1
    default:
      return /^(?:Eye|Mouth|Eyebrow)\s/.test(morphName) ? 0 : null
  }
}

export type KksOutlineProfile = {
  visible: boolean
  thickness: number
  color: [number, number, number]
}

/**
 * KKBP normally creates a 0.0005 m inverted hull and uses a dedicated face
 * mask so the eyes and facial cutouts do not grow black borders. The Web GLB
 * atlas no longer contains that mask as a separate channel, therefore the
 * closest safe equivalent is a thin screen-space hull on opaque skin,
 * hair and clothes, while explicitly excluding the shared face/eye cutouts.
 */
export function kksOutlineProfile(materialName: string): KksOutlineProfile {
  const name = materialName.trim().toLowerCase()
  const excluded = /eye-(?:left|right)/.test(name)
    || /face-(?:brow|nose-line|eye-line|eye-white)/.test(name)
    || (/^body\b/.test(name) && /atlas-alpha/.test(name))
  if (excluded) return {
    visible: false,
    thickness: 0,
    color: [0.07, 0.045, 0.08],
  }
  return {
    visible: true,
    thickness: /\bhair\b/.test(name) ? 0.0016 : 0.00125,
    color: [0.07, 0.045, 0.08],
  }
}

export async function loadBundledKksHeadShapeDriver(
  profile: BundledKksHeadProfile,
  signal?: AbortSignal,
): Promise<KksHeadShapeDriverData> {
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError")
  const drivers = {
    0: headDriver0Json,
    200: headDriver200Json,
    201: headDriver201Json,
  } as const
  const driver = drivers[profile] as unknown as KksHeadShapeDriverData
  if (driver.sliderCount !== 52
    || Number(driver.headId) !== profile
    || !Array.isArray(driver.categories)
    || !driver.animation
    || typeof driver.animation !== "object") {
    throw new Error(`Invalid bundled KKS head shape profile ${profile}`)
  }
  return driver
}

export const isKksGltfRuntime = (
  format?: "vrm" | "glb" | "pmx",
  nativeExport?: boolean,
  runtimeMode?: "native-template" | "pmx-export" | "kkbp-web",
) => format === "glb" && (nativeExport === true || runtimeMode === "kkbp-web")

let bodyDriverPromise: Promise<KksBodyShapeDriverData> | undefined

export function loadKksBodyShapeDriver() {
  if (!bodyDriverPromise) {
    bodyDriverPromise = Promise.resolve(bodyDriverJson as unknown as KksBodyShapeDriverData)
  }
  return bodyDriverPromise
}
