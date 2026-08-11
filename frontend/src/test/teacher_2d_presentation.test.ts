import { describe, expect, it } from "vitest"

import { PlaceholderTeacherAdapter } from "../features/teachers/presentation/adapters/PlaceholderTeacherAdapter"
import { shouldStartTeacherAnimation } from "../features/teachers/presentation/adapters/DragonBonesTeacherAdapter"
import { blendLive2DProceduralPose, ensurePixiTextureSourceGpuData, live2DCoreAvailable, live2DSkinNames, prepareCubismMocRelease, resolveLive2DFacialFrame, resolveLive2DProceduralPose } from "../features/teachers/presentation/adapters/Live2DTeacherAdapter"
import {
  deriveLayeredRigAnchors,
  layeredBoneHierarchy,
  resolveLayeredBoneParent,
  resolveLayeredBonePose,
  resolveLayeredHitReaction,
  stepCriticallyDamped,
  type RigState,
} from "../features/teachers/presentation/adapters/LayeredTeacherAdapter"
import { resolveTeacherAnimation, validateTeacher2DManifest } from "../features/teachers/presentation/teacherPresentationManifest"
import { normalizePresentationState } from "../features/teachers/presentation/Teacher2DStage"

const manifest = {
  id: "test", displayName: "Test", renderer: "dragonbones2d", skeleton: "/s.json", textures: ["/0.png"], armature: "shizuku", defaultAnimation: "idle_00",
  animationAliases: { idle: "idle_00", questioning: "tap_body_00" },
} as const

const imageManifest = {
  id: "v9", displayName: "V9", renderer: "image2d", skeleton: "dom-css-v1",
  textures: ["/teaching.png", "/swimsuit.png"], armature: "v9", defaultAnimation: "idle",
  imageStates: { teaching: "/teaching.png", swimsuit: "/swimsuit.png" },
  animationAliases: { idle: "idle", explaining: "explain" },
} as const

const layeredManifest = {
  ...imageManifest,
  id: "v9-layered",
  renderer: "layered2d",
  skeleton: "/teacher/layered-rig.json",
} as const

const live2DManifest = {
  id: "tsubaki", displayName: "Tsubaki", renderer: "live2d2d",
  skeleton: "/teacher/tsubaki.web.model3.json",
  textures: ["/teacher/texture_00.png"], armature: "tsubaki", defaultAnimation: "idle",
  animationAliases: { idle: "idle", explaining: "explaining", praising: "praising" },
  live2d: {
    coreScript: "/vendor/live2d/live2dcubismcore.min.js",
    textureLod: "single-auto",
    skins: {
      default: { label: "银白发" },
      "red-hair": { label: "山茶红发", partIds: ["Part4"], multiplyColor: [.88, .2, .17, 1] },
    },
  },
} as const

describe("replaceable Teacher2D presentation boundary", () => {
  it("validates a local DragonBones manifest and maps only real animation names", () => {
    expect(validateTeacher2DManifest(manifest).armature).toBe("shizuku")
    expect(resolveTeacherAnimation(manifest, "questioning", ["idle_00", "tap_body_00"])).toBe("tap_body_00")
    expect(resolveTeacherAnimation(manifest, "praising", ["idle_00"])).toBe("idle_00")
  })

  it("validates the V9 image manifest and keeps both approved forms", () => {
    const validated = validateTeacher2DManifest(imageManifest)
    expect(validated.renderer).toBe("image2d")
    expect(validated.imageStates).toEqual({ teaching: "/teaching.png", swimsuit: "/swimsuit.png" })
    expect(resolveTeacherAnimation(validated, "explaining", ["idle", "explain"])).toBe("explain")
  })

  it("accepts the generated dual-form layered rig and rejects a fake non-JSON skeleton", () => {
    expect(validateTeacher2DManifest(layeredManifest).renderer).toBe("layered2d")
    expect(() => validateTeacher2DManifest({ ...layeredManifest, skeleton: "dom-css-v1" })).toThrow("分层骨架 JSON")
  })

  it("accepts a standard Live2D model3 manifest without treating a WPK as a web model", () => {
    expect(validateTeacher2DManifest(live2DManifest).renderer).toBe("live2d2d")
    expect(() => validateTeacher2DManifest({ ...live2DManifest, skeleton: "/teacher/encrypted.wpk" })).toThrow(".model3.json")
    expect(live2DCoreAvailable({ Live2DCubismCore: {} })).toBe(true)
    expect(live2DCoreAvailable({})).toBe(false)
    expect(live2DSkinNames(live2DManifest)).toEqual(["default", "red-hair"])
  })

  it("bridges the Pixi 8.13 texture cache expected by the Live2D renderer", () => {
    const source: { _gpuData?: Record<number, unknown> } = {}
    ensurePixiTextureSourceGpuData(source)
    expect(source._gpuData).toBeTruthy()
    const first = source._gpuData
    ensurePixiTextureSourceGpuData(source)
    expect(source._gpuData).toBe(first)
  })

  it("deletes the Cubism model before releasing its moc during teardown", () => {
    const calls: string[] = []
    const coreModel = {
      release() { calls.push("core-release") },
    }
    const moc = {
      deleteModel(model: typeof coreModel) {
        calls.push("delete-model")
        model.release()
      },
      release() { calls.push("moc-release") },
    }
    const internal: { coreModel: typeof coreModel; __moc?: typeof moc } = { coreModel, __moc: moc }

    const releaseMoc = prepareCubismMocRelease(internal)
    expect(internal.__moc).toBeUndefined()
    coreModel.release()
    releaseMoc?.()

    expect(calls).toEqual(["delete-model", "core-release", "moc-release"])
  })

  it("maps teaching semantics to restrained full-body Live2D parameter poses", () => {
    const idle = resolveLive2DProceduralPose("idle")
    const explaining = resolveLive2DProceduralPose("explaining")
    const questioning = resolveLive2DProceduralPose("questioning")
    const praising = resolveLive2DProceduralPose("praising")
    const encouraging = resolveLive2DProceduralPose("encouraging")

    expect(explaining.speaking).toBe(true)
    expect(explaining.rightHand).toBeGreaterThan(idle.rightHand)
    expect(questioning.bodyZ).toBeGreaterThan(0)
    expect(questioning.browY).toBeGreaterThan(idle.browY)
    expect(questioning.browAsymmetry).toBeGreaterThan(idle.browAsymmetry)
    expect(questioning.eyeAsymmetry).not.toBe(0)
    expect(questioning.headZ).toBeLessThan(-4)
    expect(praising.mouthForm).toBeGreaterThan(explaining.mouthForm)
    expect(praising.eyeExpression2).toBeGreaterThan(encouraging.eyeExpression2)
    expect(Math.abs(praising.bodyX)).toBeLessThanOrEqual(4)
    expect(resolveLive2DProceduralPose("greeting").speaking).toBe(true)
    expect(questioning.speaking).toBe(true)
    expect(praising.speaking).toBe(true)
    expect(encouraging.speaking).toBe(true)
    expect(resolveLive2DProceduralPose("confused").speaking).toBe(true)
    expect(resolveLive2DProceduralPose("ending").speaking).toBe(true)
    expect(resolveLive2DProceduralPose("thinking").speaking).toBe(false)
  })

  it("creates visible teaching lip-sync, eyebrow asymmetry, and natural blinks", () => {
    const explaining = resolveLive2DProceduralPose("explaining")
    const questioning = resolveLive2DProceduralPose("questioning")
    const idle = resolveLive2DProceduralPose("idle")
    const speechA = resolveLive2DFacialFrame(explaining, .5)
    const speechB = resolveLive2DFacialFrame(explaining, .72)
    const questionFace = resolveLive2DFacialFrame(questioning, .5)
    const blink = resolveLive2DFacialFrame(idle, .09)
    const openEyes = resolveLive2DFacialFrame(idle, .3)

    expect(speechA.mouthOpen).toBeGreaterThan(.2)
    expect(speechA.jawOpen).toBeGreaterThan(.1)
    expect(speechA.mouthOpen).not.toBeCloseTo(speechB.mouthOpen, 2)
    expect(speechA.mouthForm).not.toBeCloseTo(explaining.mouthForm, 2)
    expect(questionFace.browLeftY).not.toBeCloseTo(questionFace.browRightY, 2)
    expect(questionFace.eyeLeftOpen).not.toBeCloseTo(questionFace.eyeRightOpen, 2)
    expect(blink.eyeLeftOpen).toBeLessThan(.05)
    expect(blink.eyeRightOpen).toBeLessThan(.05)
    expect(openEyes.eyeLeftOpen).toBeGreaterThan(.9)
    expect(resolveLive2DFacialFrame(idle, .5).mouthOpen).toBe(0)
  })

  it("eases between Live2D teaching actions instead of snapping the body", () => {
    const idle = resolveLive2DProceduralPose("idle")
    const target = resolveLive2DProceduralPose("explaining")
    const firstFrame = blendLive2DProceduralPose(idle, target, 1 / 60)

    expect(firstFrame.bodyX).toBeGreaterThan(idle.bodyX)
    expect(firstFrame.bodyX).toBeLessThan(target.bodyX)
    expect(firstFrame.rightHand).toBeGreaterThan(idle.rightHand)
    expect(firstFrame.rightHand).toBeLessThan(target.rightHand)
    expect(firstFrame.speaking).toBe(true)
  })

  it("normalizes the legacy listening state without coupling business code to a runtime", () => {
    expect(normalizePresentationState("listening")).toBe("idle")
    expect(normalizePresentationState("thinking")).toBe("thinking")
    expect(normalizePresentationState("unknown")).toBe("idle")
  })

  it("does not restart an identical animation state", () => {
    expect(shouldStartTeacherAnimation("idle_00", true, "idle_00", true)).toBe(false)
    expect(shouldStartTeacherAnimation("idle_00", true, "idle_00", false)).toBe(true)
    expect(shouldStartTeacherAnimation("idle_00", true, "tap_body_00", false)).toBe(true)
  })

  it("uses rigid head and neck bones while moving gestures through human joints", () => {
    const idle = resolveLayeredBonePose(1.2, { x: 0, y: 0 }, "idle", .75)
    const wave = resolveLayeredBonePose(1.2, { x: 0, y: 0 }, "wave", .6545)
    const surprised = resolveLayeredBonePose(1.2, { x: 0, y: 0 }, "surprised", .75)
    const cheer = resolveLayeredBonePose(1.2, { x: 0, y: 0 }, "cheer", .36)

    expect(wave.neckScale).toBe(1)
    expect(wave.headScale).toBe(1)
    expect(Math.abs(wave.rightArmRotation - idle.rightArmRotation)).toBeGreaterThan(.1)
    expect(Math.abs(wave.leftArmRotation - idle.leftArmRotation)).toBeLessThan(.001)
    expect(surprised.headOffsetY).toBe(idle.headOffsetY)
    expect(surprised.chestLift).toBeLessThan(idle.chestLift - 2)
    expect(cheer.jumpY).toBeLessThan(-10)
    expect(Math.abs(cheer.leftArmRotation - idle.leftArmRotation)).toBeGreaterThan(.1)
    expect(Math.abs(cheer.rightArmRotation - idle.rightArmRotation)).toBeGreaterThan(.1)
  })

  it("builds a real parent-child bone topology instead of attaching every cutout to root", () => {
    expect(layeredBoneHierarchy).toEqual({
      pelvis: null,
      legs: "pelvis",
      chest: "pelvis",
      "left-arm": "chest",
      "right-arm": "chest",
      head: "chest",
      "hair-back": "head",
      "hair-front": "head",
    })
    expect(resolveLayeredBoneParent("eye-left")).toBe("head")
    expect(resolveLayeredBoneParent("arm-left")).toBe("left-arm")
    expect(resolveLayeredBoneParent("torso")).toBe("chest")
    expect(resolveLayeredBoneParent("feet")).toBe("legs")
    expect(resolveLayeredBoneParent("hips")).toBe("pelvis")
  })

  it("derives different anatomical joints for each skin from adjacent cutout overlap", () => {
    const makeState = (shiftX: number, swimsuit = false): RigState => ({
      canvas: { width: 1280, height: 1280 },
      layers: [
        { id: "head", name: "head", role: "head", src: "/head.png", x: 601 + shiftX, y: 67, width: 108, height: 136, order: 1 },
        { id: "neck", name: "neck", role: "neck", src: "/neck.png", x: 603 + shiftX, y: 163, width: 47, height: 67, order: 2 },
        { id: "torso", name: "torso", role: "torso", src: "/torso.png", x: 541 + shiftX, y: 210, width: 192, height: swimsuit ? 346 : 245, order: 3 },
        { id: "hips", name: "hips", role: "hips", src: "/hips.png", x: 516 + shiftX, y: swimsuit ? 463 : 432, width: 285, height: swimsuit ? 101 : 225, order: 4 },
        { id: "legs", name: "legs", role: "legs", src: "/legs.png", x: 511 + shiftX, y: swimsuit ? 476 : 569, width: 246, height: 618, order: 5 },
        { id: "left", name: "left", role: "arm-left", src: "/left.png", x: 468 + shiftX, y: 244, width: 125, height: 393, order: 6 },
        { id: "right", name: "right", role: "arm-right", src: "/right.png", x: 681 + shiftX, y: 232, width: 177, height: 186, order: 7 },
      ],
    })
    const teaching = deriveLayeredRigAnchors(makeState(0))
    const swimsuit = deriveLayeredRigAnchors(makeState(12, true))

    expect(teaching.head).toEqual({ x: 626.5, y: 183 })
    expect(teaching.chest.y).toBe(443.5)
    expect(teaching.leftShoulder.x).toBeGreaterThan(560)
    expect(swimsuit.head.x - teaching.head.x).toBe(12)
    expect(swimsuit.chest.y).not.toBe(teaching.chest.y)
  })

  it("critically damps abrupt targets without snapping or overshooting", () => {
    const first = stepCriticallyDamped(0, 0, 1, 1 / 60)
    const second = stepCriticallyDamped(first.value, first.velocity, 1, 1 / 60)
    expect(first.value).toBeGreaterThan(0)
    expect(first.value).toBeLessThan(1)
    expect(second.value).toBeGreaterThan(first.value)
    expect(second.value).toBeLessThanOrEqual(1)

    let current = second
    for (let index = 0; index < 180; index += 1) {
      current = stepCriticallyDamped(current.value, current.velocity, 1, 1 / 60)
    }
    expect(current.value).toBeCloseTo(1, 4)
    expect(Math.abs(current.velocity)).toBeLessThan(.001)
  })

  it("maps clicks to the visible body region instead of fixed viewport bands", () => {
    expect(resolveLayeredHitReaction(.5, .08)).toMatchObject({ region: "head", animation: "surprised" })
    expect(resolveLayeredHitReaction(.12, .4)).toMatchObject({ region: "left-arm", animation: "heart" })
    expect(resolveLayeredHitReaction(.5, .4)).toMatchObject({ region: "torso", animation: "explain" })
    expect(resolveLayeredHitReaction(.88, .4)).toMatchObject({ region: "right-arm", animation: "wave" })
    expect(resolveLayeredHitReaction(.5, .85)).toMatchObject({ region: "lower-body", animation: "cheer" })
  })

  it("keeps the placeholder adapter lifecycle safe for future spine/none renderers", async () => {
    const adapter = new PlaceholderTeacherAdapter()
    await adapter.load(manifest)
    expect(adapter.listAnimations()).toEqual(["idle_00"])
    adapter.playAnimation("custom", false)
    expect(adapter.listAnimations()).toEqual(["custom"])
    adapter.pause(); adapter.resume(); adapter.unload()
    expect(adapter.listAnimations()).toEqual([])
  })
})
