import { describe, expect, it } from "vitest"
import nativeDriverJson from "../../../legacy/koikatsu-3d/resources/web/kks-body-shape-driver.json"
import headDriverJson from "../../../legacy/koikatsu-3d/resources/web/kks-head-shape-driver-0.json"
import type { KksBodyShapeDriverData } from "../features/teachers/kksBodyShapeDriver"
import type { KksHeadShapeDriverData } from "../features/teachers/kksHeadShapeDriver"
import {
  canonicalKksBoneName,
  evaluateKksBodyPatchSet,
  evaluateKksFacePatchSet,
  isKksGltfRuntime,
  kksElbowCorrectionOffsets,
  kksExpressionWeight,
  kksOutlineProfile,
  KKS_BODY_SLIDER_NAMES,
  nextKksCameraZoom,
  relativeKksPatch,
  resolveBundledKksHeadProfile,
} from "../features/teachers/kksGltfRuntime"

const driver = nativeDriverJson as unknown as KksBodyShapeDriverData
const headDriver = headDriverJson as unknown as KksHeadShapeDriverData

describe("KKBP Web GLB runtime contract", () => {
  it("routes only an explicitly native KKBP GLB away from the VRM loader", () => {
    expect(isKksGltfRuntime("glb", true, "pmx-export")).toBe(true)
    expect(isKksGltfRuntime("glb", false, "kkbp-web")).toBe(true)
    expect(isKksGltfRuntime("glb", false, "pmx-export")).toBe(false)
    expect(isKksGltfRuntime("vrm", true, "kkbp-web")).toBe(false)
  })

  it("keeps wheel zoom synchronized with the editor's larger-is-closer scale", () => {
    expect(nextKksCameraZoom(1, -120)).toBeCloseTo(1.06)
    expect(nextKksCameraZoom(1, 120)).toBeCloseTo(.96)
    expect(nextKksCameraZoom(2.7, -120)).toBe(2.7)
    expect(nextKksCameraZoom(.55, 120)).toBe(.55)
    expect(nextKksCameraZoom(Number.NaN, 0)).toBe(1)
  })

  it("keeps the complete 44-slider card contract and all native body targets", () => {
    expect(KKS_BODY_SLIDER_NAMES).toHaveLength(44)
    const patches = evaluateKksBodyPatchSet(driver, {}, 0)
    expect(Object.keys(patches)).toHaveLength(88)
    expect(patches).toHaveProperty("cf_n_height")
    expect(patches).toHaveProperty("cf_d_shoulder_L")
    expect(patches).toHaveProperty("cf_d_shoulder_R")
  })

  it("keeps front and back elbow corrective bones distinct", () => {
    expect(canonicalKksBoneName("cf_s_elbo_L")).toBe("cf_s_elbo_l")
    expect(canonicalKksBoneName("cf_s_elboback_L")).toBe("cf_s_elboback_l")
    expect(canonicalKksBoneName("cf_s_elbowback_R")).toBe("cf_s_elboback_r")
  })

  it("ports the KKBP elbow driver offsets with mirrored left/right depth", () => {
    const length = .22
    const bend = Math.PI / 6
    const left = kksElbowCorrectionOffsets("left", -bend, length)
    const right = kksElbowCorrectionOffsets("right", bend, length)
    expect(left[0].bone).toBe("cf_s_elboback_L")
    expect(right[0].bone).toBe("cf_s_elboback_R")
    expect(left[0].position[0]).toBeCloseTo(-right[0].position[0])
    expect(left[0].position[2]).toBeCloseTo(right[0].position[2])
    expect(left[1].position[0]).toBeCloseTo(-right[1].position[0])
    expect(left[1].position[2]).toBeCloseTo(right[1].position[2])
  })

  it("turns the imported card baseline into identity deltas", () => {
    const authored = evaluateKksBodyPatchSet(driver, {}, 0)
    for (const patch of Object.values(authored)) {
      const relative = relativeKksPatch(patch, patch)
      relative.position.forEach(value => { if (value !== null) expect(value).toBeCloseTo(0) })
      relative.rotation.forEach(value => { if (value !== null) expect(value).toBeCloseTo(0) })
      relative.scale.forEach(value => { if (value !== null) expect(value).toBeCloseTo(1) })
    }
  })

  it("computes multiplicative scale and wrapped rotation against the card baseline", () => {
    expect(relativeKksPatch(
      { position: [3, null, 5], rotation: [350, null, 10], scale: [2, null, 6] },
      { position: [1, null, 2], rotation: [10, null, 350], scale: [4, null, 3] },
    )).toEqual({
      position: [2, null, 3],
      rotation: [-20, null, 20],
      scale: [.5, null, 2],
    })
  })

  it("accepts only the three bundled exact head profiles", () => {
    expect([0, 200, 201].map(resolveBundledKksHeadProfile)).toEqual([0, 200, 201])
    expect(resolveBundledKksHeadProfile(777)).toBeNull()
    expect(resolveBundledKksHeadProfile(null)).toBeNull()
  })

  it("evaluates all 59 face targets and makes the imported face an identity baseline", () => {
    const face = Object.fromEntries(Array.from({ length: 52 }, (_, index) => [String(index), index / 51]))
    const authored = evaluateKksFacePatchSet(headDriver, face)
    expect(Object.keys(authored)).toHaveLength(59)
    for (const patch of Object.values(authored)) {
      const relative = relativeKksPatch(patch, patch)
      relative.position.forEach(value => { if (value !== null) expect(value).toBeCloseTo(0) })
      relative.rotation.forEach(value => { if (value !== null) expect(value).toBeCloseTo(0) })
      relative.scale.forEach(value => { if (value !== null) expect(value).toBeCloseTo(1) })
    }
  })

  it("maps KKS eye and mouth expressions exactly instead of treating Closed mouths as blinks", () => {
    expect(kksExpressionWeight("Eye 默认\u180e", 0, 1)).toBe(1)
    expect(kksExpressionWeight("Mouth Default", 0, 1)).toBe(1)
    expect(kksExpressionWeight("Eyebrow Default", 0, 1)).toBe(1)
    expect(kksExpressionWeight("Eye 闭眼\u180e", .25, 0)).toBe(1)
    expect(kksExpressionWeight("Eye 笑顔", .25, 1)).toBe(.25)
    expect(kksExpressionWeight("Eye 默认", .25, 1)).toBe(.75)
    expect(kksExpressionWeight("Mouth Default", .25, 1)).toBe(.75)
    expect(kksExpressionWeight("Mouth 笑顔, Closed", .25, 1)).toBe(0)
    expect(kksExpressionWeight("Mouth 笑顔", .25, 1)).toBe(.25)
    expect(kksExpressionWeight("Mouth A 大\u180e, Closed", .25, 0)).toBe(0)
    expect(kksExpressionWeight("Tears small", .25, 0)).toBeNull()
  })

  it("restores thin KKS outlines without outlining shared facial cutouts", () => {
    expect(kksOutlineProfile("Body card atlas-alpha").visible).toBe(false)
    expect(kksOutlineProfile("Body card eye-left").visible).toBe(false)
    expect(kksOutlineProfile("Body card face-brow").visible).toBe(false)
    expect(kksOutlineProfile("Body card face-nose-line").visible).toBe(false)
    expect(kksOutlineProfile("Body card face-eye-line").visible).toBe(false)
    expect(kksOutlineProfile("Body card face-eye-white").visible).toBe(false)
    expect(kksOutlineProfile("Body card eye-right").visible).toBe(false)
    expect(kksOutlineProfile("Body card atlas-opaque")).toMatchObject({
      visible: true,
      thickness: .00125,
    })
    expect(kksOutlineProfile("Hair Outfit 00 card atlas-alpha")).toMatchObject({
      visible: true,
      thickness: .0016,
    })
    expect(kksOutlineProfile("Outfit 00 card atlas-opaque").visible).toBe(true)
  })
})
