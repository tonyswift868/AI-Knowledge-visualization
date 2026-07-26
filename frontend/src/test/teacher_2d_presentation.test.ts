import { describe, expect, it } from "vitest"

import { PlaceholderTeacherAdapter } from "../features/teachers/presentation/adapters/PlaceholderTeacherAdapter"
import { shouldStartTeacherAnimation } from "../features/teachers/presentation/adapters/DragonBonesTeacherAdapter"
import { resolveTeacherAnimation, validateTeacher2DManifest } from "../features/teachers/presentation/teacherPresentationManifest"
import { normalizePresentationState } from "../features/teachers/presentation/Teacher2DStage"

const manifest = {
  id: "test", displayName: "Test", renderer: "dragonbones2d", skeleton: "/s.json", textures: ["/0.png"], armature: "shizuku", defaultAnimation: "idle_00",
  animationAliases: { idle: "idle_00", questioning: "tap_body_00" },
} as const

describe("replaceable Teacher2D presentation boundary", () => {
  it("validates a local DragonBones manifest and maps only real animation names", () => {
    expect(validateTeacher2DManifest(manifest).armature).toBe("shizuku")
    expect(resolveTeacherAnimation(manifest, "questioning", ["idle_00", "tap_body_00"])).toBe("tap_body_00")
    expect(resolveTeacherAnimation(manifest, "praising", ["idle_00"])).toBe("idle_00")
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
