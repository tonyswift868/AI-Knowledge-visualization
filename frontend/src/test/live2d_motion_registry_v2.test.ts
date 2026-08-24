import { describe, expect, it } from "vitest"
import { scanLive2DModelCapabilities } from "../features/teachers/presentation/live2d/live2dCapabilities"
import { createLive2DMotionRegistry } from "../features/teachers/presentation/live2d/live2dMotionRegistry"
import type { Teacher2DManifest } from "../features/teachers/presentation/teacherPresentationTypes"

const manifest: Teacher2DManifest = {
  id: "tsubaki",
  displayName: "Tsubaki",
  renderer: "live2d2d",
  skeleton: "/teacher2d/tsubaki/tsubaki.model3.json",
  textures: ["/texture.png"],
  armature: "tsubaki",
  defaultAnimation: "idle",
  animationAliases: { idle: "idle", greeting: "greeting", talking: "talking" },
  live2d: { expressions: { greeting: "blush" } },
}

describe("Live2D semantic action registry v2", () => {
  it("prefers a registered expression and otherwise retargets safe internal channels", () => {
    const capabilities = scanLive2DModelCapabilities({
      settings: {
        FileReferences: {
          Expressions: [{ Name: "blush", File: "blush.exp3.json" }],
          Motions: { Action: [{ File: "action.motion3.json" }] },
        },
      },
      parameters: {
        ids: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthForm", "ParamMouthOpenY", "ParamMouthX", "ParamEyeExpression1"],
      },
      physics: { PhysicsSettings: [{ Output: [{ Destination: { Id: "ParamEyeExpression1" } }] }] },
    })
    const registry = createLive2DMotionRegistry(manifest, capabilities)
    expect(registry.byId.get("greeting")).toMatchObject({ source: "registered-expression", expression: "blush", implementation: "registered-expression" })
    expect(registry.byId.get("talking")).toMatchObject({ source: "internal-parameters", implementation: "internal-parameters" })
    expect(registry.byId.get("talking")?.requiredParameters).not.toContain("ParamEyeExpression1")
    expect(registry.byId.get("BLINK")).toBeTruthy()
    expect(registry.byId.get("BLINK")?.curves?.some(curve => curve.parameterId === "ParamEyeLOpen" && curve.points.some(point => point.value === 0))).toBe(true)
    expect(registry.byId.get("BLINK")?.previewRecommended).toBe(true)
    const wink = registry.byId.get("WINK")!
    const leftEye = wink.curves?.find(curve => curve.parameterId === "ParamEyeLOpen")
    const rightEye = wink.curves?.find(curve => curve.parameterId === "ParamEyeROpen")
    expect(leftEye?.points.some(point => point.value === 0)).toBe(true)
    expect(new Set(rightEye?.points.map(point => point.value)).size).toBe(1)
    expect(rightEye?.points.every(point => point.value > 0)).toBe(true)
    expect(registry.byId.get("talking")?.previewRecommended).toBe(false)
    expect(registry.byId.get("LOOK_USER")).toBeUndefined()
  })

  it("fails unknown actions truthfully and falls back to idle without changing transform APIs", () => {
    const capabilities = scanLive2DModelCapabilities({ parameters: { ids: ["ParamAngleX"] } })
    const registry = createLive2DMotionRegistry(manifest, capabilities)
    const resolution = registry.resolve("does-not-exist")
    expect(resolution.fallback).toBe(true)
    expect(resolution.action.id).toBe("idle")
    expect(resolution.reason).toContain("未知动作")
  })

  it("does not expose partial internal recipes when a channel is unsafe", () => {
    const capabilities = scanLive2DModelCapabilities({
      parameters: { ids: ["ParamAngleX", "ParamAngleY"] },
      physics: { PhysicsSettings: [] },
    })
    const registry = createLive2DMotionRegistry(manifest, capabilities)
    expect(registry.byId.get("talking")).toBeUndefined()
  })

  it("carries native motion priority, duration, and loop metadata", () => {
    const nativeManifest = { ...manifest, animationAliases: { ...manifest.animationAliases, greeting: "motion:Cry:0" } }
    const capabilities = scanLive2DModelCapabilities({
      settings: { FileReferences: { Motions: { Cry: [{ File: "cry.motion3.json", Loop: true, Priority: 7, DurationMs: 2300 }] } } },
      parameters: { ids: ["ParamAngleX"] },
      physics: { PhysicsSettings: [] },
    })
    const registry = createLive2DMotionRegistry(nativeManifest, capabilities)
    expect(registry.byId.get("greeting")).toMatchObject({ priority: 7, durationMs: 2300, motion: { loop: true, priority: 7, durationMs: 2300 } })
  })

  it("combines the model cry motion and face expression into one preview action", () => {
    const capabilities = scanLive2DModelCapabilities({
      settings: {
        FileReferences: {
          Motions: { Cry: [{ File: "cry.motion3.json", DurationMs: 1800 }] },
          Expressions: [{ Name: "cry", File: "cry.exp3.json" }],
        },
      },
      parameters: { ids: ["ParamAngleX"] },
      physics: { PhysicsSettings: [] },
    })
    const registry = createLive2DMotionRegistry(manifest, capabilities)
    expect(registry.byId.get("CRY")).toMatchObject({
      source: "registered-motion",
      expression: "cry",
      previewRecommended: true,
      supported: true,
    })
    expect(registry.byId.get("motion:Cry:0")?.previewRecommended).toBe(false)
    expect(registry.byId.get("expression:cry")?.previewRecommended).toBe(false)
  })

  it("replaces previously weak previews with observable composite or parameter actions", () => {
    const capabilities = scanLive2DModelCapabilities({
      settings: {
        FileReferences: {
          Expressions: [{ Name: "no-highlights", File: "no-highlights.exp3.json" }],
        },
      },
      parameters: {
        ids: [
          "ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen",
          "ParamEyeBallX", "ParamEyeBallY", "ParamBrowLY", "ParamBrowRY",
          "ParamMouthForm", "ParamMouthOpenY", "ParamMouthX", "ParamBreath",
        ],
      },
      physics: { PhysicsSettings: [] },
    })
    const registry = createLive2DMotionRegistry(manifest, capabilities)
    for (const id of ["ALERT", "LOOK_LEFT", "LOOK_RIGHT", "SURPRISED", "THINK", "BREATH", "QUESTION", "TALK_ACTIVE", "TALK_SOFT", "WINK"]) {
      const action = registry.byId.get(id)
      expect(action?.supported, id).toBe(true)
      expect(action?.curves?.some(curve => {
        const values = curve.points.map(point => point.value)
        return Math.max(...values) - Math.min(...values) > .001
      }), id).toBe(true)
    }
    expect(registry.byId.get("NO_HIGHLIGHTS")).toMatchObject({
      expression: "no-highlights",
      previewRecommended: true,
      supported: true,
    })
    expect(registry.byId.get("expression:no-highlights")?.previewRecommended).toBe(false)
  })
})
