import { describe, expect, it } from "vitest"

import { validateSpineTeacherManifest } from "../features/teachers/SpineTeacherPlayer"
import { resolveSpineTeacherManifestUrl } from "../features/teachers/TeacherAvatarStudio"

const validManifest = {
  format: "mentorflow.spine-teacher",
  runtime_version: "4.1",
  license: {
    spine_editor_license_confirmed: true,
    character_rights_confirmed: true,
  },
  skeleton: {
    binary_url: "/teachers/iris/iris.skel",
    atlas_url: "/teachers/iris/iris.atlas",
  },
  animations: {
    idle: "idle",
    listening: "listen",
    thinking: "think",
    explaining: "talk",
    encouraging: "happy",
  },
} as const

describe("Spine teacher manifest", () => {
  it("accepts a licensed Spine 4.1 teacher package", () => {
    expect(validateSpineTeacherManifest(validManifest)).toEqual(validManifest)
  })

  it("rejects packages without both license confirmations", () => {
    expect(() => validateSpineTeacherManifest({
      ...validManifest,
      license: { ...validManifest.license, character_rights_confirmed: false },
    })).toThrow(/授权/)
  })

  it("rejects incomplete teaching-state animation maps", () => {
    const { thinking: _thinking, ...animations } = validManifest.animations
    expect(() => validateSpineTeacherManifest({ ...validManifest, animations })).toThrow(/thinking/)
  })

  it("only accepts same-origin teacher asset paths for manual runtime previews", () => {
    expect(resolveSpineTeacherManifestUrl("?spineManifest=%2Fassets%2Fteachers%2Fspine%2Firis%2Fteacher.manifest.json"))
      .toBe("/assets/teachers/spine/iris/teacher.manifest.json")
    expect(resolveSpineTeacherManifestUrl("?spineManifest=https%3A%2F%2Fevil.example%2Fteacher.json")).toBeUndefined()
  })
})
