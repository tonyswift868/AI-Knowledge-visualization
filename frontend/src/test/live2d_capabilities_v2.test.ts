import { describe, expect, it } from "vitest"
import { isPhysicsOutputParameter, parseLive2DMotionMetadata, registeredExpressionNames, registeredMotionNames, scanLive2DModelCapabilities } from "../features/teachers/presentation/live2d/live2dCapabilities"

describe("Live2D capability scanner v2", () => {
  const settings = {
    FileReferences: {
      Motions: { Cry: [{ File: "motions/idle.motion3.json", Loop: true }] },
      Expressions: [{ Name: "blush", File: "EXP3/blush.exp3.json" }],
    },
  }
  const physics = {
    PhysicsSettings: [{ Output: [{ Destination: { Target: "Parameter", Id: "ParamBodyAngleX" } }] }],
  }

  it("reports native groups/expressions and excludes physics destinations from safe channels", () => {
    const capabilities = scanLive2DModelCapabilities({
      settings,
      parameters: {
        ids: ["ParamAngleX", "ParamBodyAngleX", "ParamEyeLOpen"],
        minimumValues: new Float32Array([-30, -10, 0]),
        maximumValues: new Float32Array([30, 10, 1]),
        defaultValues: new Float32Array([0, 0, 1]),
      },
      physics,
    })
    expect(capabilities.physicsKnown).toBe(true)
    expect(capabilities.motionGroups.Cry?.[0]).toMatchObject({ group: "Cry", index: 0, file: "motions/idle.motion3.json" })
    expect(capabilities.expressions).toEqual([{ name: "blush", file: "EXP3/blush.exp3.json" }])
    expect(capabilities.safeParameterIds).toEqual(["ParamAngleX", "ParamEyeLOpen"])
    expect(capabilities.physicsOutputParameterIds).toEqual(["ParamBodyAngleX"])
    expect(isPhysicsOutputParameter(capabilities, "ParamBodyAngleX")).toBe(true)
    expect(registeredMotionNames(capabilities)).toEqual(["motion:Cry:0"])
    expect(registeredExpressionNames(capabilities)).toEqual(["expression:blush"])
  })

  it("uses cdi parameter IDs when the native core model is not available yet", () => {
    const capabilities = scanLive2DModelCapabilities({
      settings: {},
      cdi: { Parameters: [{ Id: "ParamAngleX" }, { Id: "ParamBodyAngleX" }] },
      physics,
    })
    expect(capabilities.parameters.map(parameter => parameter.id)).toEqual(["ParamAngleX"])
    expect(capabilities.safeParameterIds).toEqual(["ParamAngleX"])
  })

  it("fails closed when physics metadata is missing or unreadable", () => {
    const capabilities = scanLive2DModelCapabilities({ parameters: { ids: ["ParamAngleX"] } })
    expect(capabilities.physicsKnown).toBe(false)
    expect(capabilities.safeParameterIds).toEqual([])
  })

  it("enriches model3 motion entries from real motion3 Meta without inventing malformed values", () => {
    const motionFile = "motions/idle.motion3.json"
    const capabilities = scanLive2DModelCapabilities({
      settings: { FileReferences: { Motions: { Cry: [{ File: motionFile, FadeInTime: .5, FadeOutTime: .5 }] } } },
      motionMetadata: { [motionFile]: { Version: 3, Meta: { Duration: 6, Loop: true } } },
      parameters: { ids: ["ParamAngleX"] },
      physics: { PhysicsSettings: [] },
    })
    expect(capabilities.motionGroups.Cry?.[0]).toMatchObject({ durationMs: 6000, loop: true, fadeInMs: 500, fadeOutMs: 500 })
    expect(parseLive2DMotionMetadata({ Meta: { Duration: -1, Loop: "true" } })).toBeUndefined()
    expect(scanLive2DModelCapabilities({
      settings: { FileReferences: { Motions: { Cry: [{ File: motionFile }] } } },
      motionMetadata: { [motionFile]: { Meta: { Duration: "6", Loop: null } } },
      parameters: { ids: ["ParamAngleX"] },
      physics: { PhysicsSettings: [] },
    }).motionGroups.Cry?.[0]).toMatchObject({ file: motionFile })
    expect(scanLive2DModelCapabilities({
      settings: { FileReferences: { Motions: { Cry: [{ File: motionFile }] } } },
      parameters: { ids: ["ParamAngleX"] },
      physics: { PhysicsSettings: [] },
    }).motionGroups.Cry?.[0]?.durationMs).toBeUndefined()
  })
})
