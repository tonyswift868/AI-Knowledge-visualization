import { describe, expect, it, vi } from "vitest"
import { Live2DTeacherAdapter, resolveLive2DIdleAmbient, resolveLive2DIdleBodySway } from "../features/teachers/presentation/adapters/Live2DTeacherAdapter"
import { scanLive2DModelCapabilities } from "../features/teachers/presentation/live2d/live2dCapabilities"
import { createLive2DMotionRegistry, type Live2DSemanticAction } from "../features/teachers/presentation/live2d/live2dMotionRegistry"
import type { Teacher2DManifest } from "../features/teachers/presentation/teacherPresentationTypes"

const manifest: Teacher2DManifest = {
  id: "runner",
  displayName: "Runner",
  renderer: "live2d2d",
  skeleton: "/runner.model3.json",
  textures: ["/texture.png"],
  armature: "runner",
  defaultAnimation: "idle",
}

function fixture() {
  const calls: Array<{ id: string; value: number; operation?: "set" | "add" }> = []
  const errors: Error[] = []
  const coreParameterIds = ["ParamEyeLOpen", "ParamEyeROpen", "ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamBreath", "ParamBodyAngleX", "ParamBodyAngleZ"]
  const capabilities = scanLive2DModelCapabilities({
    parameters: {
      ids: coreParameterIds,
      minimumValues: [0, 0, -30, -30, -30, 0, -10, -10],
      maximumValues: [1, 1, 30, 30, 30, 1, 10, 10],
      defaultValues: [1, 1, 0, 0, 0, 0, 0, 0],
    },
    physics: { PhysicsSettings: [{ Output: [{ Destination: { Id: "ParamBodyAngleX" } }, { Destination: { Id: "ParamBodyAngleZ" } }] }] },
  })
  const registry = createLive2DMotionRegistry(manifest, capabilities)
  const core = {
    getParameterIndex: (id: string) => coreParameterIds.includes(id) ? 0 : -1,
    setParameterValueById: (id: string, value: number) => calls.push({ id, value, operation: "set" }),
    addParameterValueById: (id: string, value: number) => calls.push({ id, value, operation: "add" }),
  }
  const adapter = new Live2DTeacherAdapter({ host: document.createElement("div"), onSnapshot: vi.fn(), onError: error => errors.push(error) }) as any
  const ticker = { start: vi.fn(), stop: vi.fn() }
  adapter.app = { ticker }
  adapter.manifest = manifest
  adapter.capabilities = capabilities
  adapter.motionRegistry = registry
  adapter.physicsOutputIds = new Set(capabilities.physicsOutputParameterIds)
  adapter.elapsed = .25
  adapter.model = {
    internalModel: {
      idManager: { getId: (id: string) => id },
      coreModel: core,
      motionManager: { expressionManager: { resetExpression: vi.fn() } },
    },
    scale: { x: 1, y: 1 },
    position: { x: 10, y: 20 },
  }
  return { adapter, capabilities, registry, calls, errors, ticker }
}

describe("Live2D internal action runner v2", () => {
  it("keeps the stage fixed and drives quiet idle life through body parameters only", () => {
    const samples = Array.from({ length: 240 }, (_, index) => resolveLive2DIdleAmbient(index / 20))
    expect(samples.every(sample => sample.x === 0 && sample.y === 0)).toBe(true)
    expect(resolveLive2DIdleAmbient(8, false)).toEqual({ x: 0, y: 0 })
    const body = Array.from({ length: 240 }, (_, index) => resolveLive2DIdleBodySway(index / 20))
    expect(Math.max(...body.map(sample => Math.abs(sample.bodyX)))).toBeGreaterThan(2.2)
    expect(Math.max(...body.map(sample => Math.abs(sample.bodyZ)))).toBeLessThanOrEqual(.7)
    expect(resolveLive2DIdleBodySway(8, false)).toEqual({ bodyX: 0, bodyZ: 0 })
  })

  it("adds model body sway without translating the stage or adding an idle head swing", () => {
    const { adapter, calls } = fixture()
    adapter.currentSemantic = "idle"
    adapter.authoritativeSemantic = "idle"
    adapter.elapsed = 2
    adapter.applyProceduralPose()
    const bodyWrites = calls.filter(call => call.operation === "add")
    expect(bodyWrites.map(call => call.id).sort()).toEqual(["ParamBodyAngleX", "ParamBodyAngleZ"])
    expect(adapter.model.position).toEqual({ x: 10, y: 20 })
    const headX = calls.find(call => call.id === "ParamAngleX")
    expect(headX?.value).toBe(0)
  })

  it("writes normalized safe action curves and never physics outputs or transforms", () => {
    const { adapter, registry, calls } = fixture()
    const action = registry.byId.get("BLINK")!
    adapter.activeAction = { action, startedAt: 0, progress: .5, frozen: false, loop: false }
    adapter.applyActiveActionCurves()
    expect(calls.length).toBeGreaterThan(0)
    expect(calls.every(call => call.id === "ParamEyeLOpen" || call.id === "ParamEyeROpen")).toBe(true)
    expect(calls.every(call => call.value >= 0 && call.value <= 1)).toBe(true)
    expect(adapter.model.scale).toEqual({ x: 1, y: 1 })
    expect(adapter.model.position).toEqual({ x: 10, y: 20 })
  })

  it("keeps idle free of named actions and still starts explicit action curves", () => {
    const { adapter, calls } = fixture()
    adapter.playSemanticState("idle")
    expect(adapter.activeAction).toBeUndefined()
    expect(adapter.currentAnimation).toBe("idle")

    adapter.triggerLive2DAction("BLINK")
    expect(adapter.activeAction?.action.id).toBe("BLINK")
    adapter.activeAction.progress = .5
    adapter.applyActiveActionCurves()
    expect(calls.length).toBeGreaterThan(0)
  })

  it("stops, freezes, resumes, and restores the authoritative semantic state", () => {
    const { adapter, registry, calls, ticker } = fixture()
    const action = registry.byId.get("BLINK")!
    adapter.authoritativeSemantic = "talking"
    adapter.currentSemantic = "talking"
    adapter.activeAction = { action, startedAt: 0, progress: .4, frozen: false, loop: false }
    adapter.elapsed = 1
    adapter.freezeLive2DAction()
    expect(adapter.activeAction.frozen).toBe(true)
    expect(ticker.stop).toHaveBeenCalledTimes(1)
    adapter.elapsed = 4
    adapter.resumeLive2DAction()
    expect(adapter.activeAction.frozen).toBe(false)
    expect(adapter.activeAction.startedAt).toBe(3)
    expect(ticker.start).toHaveBeenCalledTimes(1)
    adapter.stopLive2DAction()
    expect(adapter.activeAction).toBeUndefined()
    expect(adapter.currentSemantic).toBe("talking")
    expect(ticker.start).toHaveBeenCalledTimes(2)
    expect(calls).toEqual([])
  })

  it("keeps a frozen frame stopped across visibility resume until explicit resume", () => {
    const { adapter, registry, ticker } = fixture()
    const action = registry.byId.get("BLINK")!
    adapter.activeAction = { action, startedAt: 0, progress: .4, frozen: false, loop: false }
    adapter.freezeLive2DAction()
    ticker.start.mockClear()

    adapter.visibilityPaused = true
    adapter.applyPauseState()
    adapter.visibilityPaused = false
    adapter.applyPauseState()
    expect(ticker.start).not.toHaveBeenCalled()

    adapter.resumeLive2DAction()
    expect(ticker.start).toHaveBeenCalledTimes(1)
  })

  it("direct restore releases a frozen ticker without requiring Resume first", () => {
    const { adapter, ticker } = fixture()
    adapter.playSemanticState("idle")
    adapter.authoritativeSemantic = "questioning"
    adapter.freezeLive2DAction()
    ticker.start.mockClear()

    adapter.restoreLive2DAuthoritativeState()
    expect(ticker.start).toHaveBeenCalledTimes(1)
    expect(adapter.currentSemantic).toBe("questioning")
  })

  it("freezing the active action prevents procedural writes until resumed", () => {
    const { adapter, registry, calls } = fixture()
    const action = registry.byId.get("BLINK")!
    adapter.activeAction = { action, startedAt: 0, progress: .4, frozen: false, loop: false }
    calls.length = 0
    adapter.freezeLive2DAction()
    adapter.applyProceduralPose()
    expect(calls).toEqual([])
    adapter.resumeLive2DAction()
  })

  it("rejects direct physics curves even when supplied by an untrusted action object", () => {
    const { adapter, calls } = fixture()
    const unsafe = {
      id: "unsafe",
      source: "internal-parameters",
      curves: [{ parameterId: "ParamBodyAngleX", points: [{ at: 0, value: 0 }, { at: 1, value: 1 }] }],
    } as unknown as Live2DSemanticAction
    adapter.activeAction = { action: unsafe, startedAt: 0, progress: .5, frozen: false, loop: false }
    adapter.applyActiveActionCurves()
    expect(calls).toEqual([])
  })

  it("surfaces rejected native playback without reporting a false success", async () => {
    const { adapter, registry, errors } = fixture()
    const native = { ...registry.byId.get("BLINK")!, id: "native-blink", source: "registered-motion" as const, implementation: "registered-motion" as const, motion: { group: "Cry", index: 0 } }
    adapter.motionRegistry = { actions: [native], byId: new Map([[native.id, native]]), resolve: (requested: string) => ({ requested, action: native, fallback: false }) }
    adapter.model.motion = vi.fn(() => Promise.reject(new Error("motion load failed")))
    const result = adapter.triggerLive2DAction(native.id)
    await Promise.resolve()
    await Promise.resolve()
    expect(result.supported).toBe(true)
    expect(result.fallback).toBe(true)
    expect(errors).toEqual([])
    expect(adapter.runtimeActions()[0]).toMatchObject({
      supported: true,
      source: "internal-parameters",
      supportReason: expect.stringContaining("安全参数动作"),
    })
  })

  it("does not disable a native action when Cubism reports an interrupted attempt", async () => {
    const { adapter, registry, errors } = fixture()
    const native = { ...registry.byId.get("BLINK")!, id: "native-interrupted", source: "registered-motion" as const, implementation: "registered-motion" as const, motion: { group: "Cry", index: 0 } }
    adapter.motionRegistry = { actions: [native], byId: new Map([[native.id, native]]), resolve: (requested: string) => ({ requested, action: native, fallback: false }) }
    adapter.model.motion = vi.fn(() => Promise.resolve(false))
    const result = adapter.triggerLive2DAction(native.id)
    await Promise.resolve()
    await Promise.resolve()
    expect(result.supported).toBe(true)
    expect(errors).toEqual([])
    expect(adapter.runtimeActions()[0]).toMatchObject({ supported: true, source: "registered-motion" })
  })

  it("uses motion3 duration/loop metadata for native action lifecycle", async () => {
    const { adapter, capabilities } = fixture()
    const motionFile = "motions/idle.motion3.json"
    const enriched = scanLive2DModelCapabilities({
      settings: { FileReferences: { Motions: { Cry: [{ File: motionFile, FadeInTime: .5, FadeOutTime: .5 }] } } },
      motionMetadata: { [motionFile]: { Meta: { Duration: 6, Loop: true } } },
      parameters: { ids: capabilities.parameters.map(parameter => parameter.id) },
      physics: { PhysicsSettings: [] },
    })
    const nativeManifest = { ...manifest, animationAliases: { greeting: "motion:Cry:0" } }
    const registry = createLive2DMotionRegistry(nativeManifest, enriched)
    const action = registry.byId.get("greeting")!
    const motion = vi.fn(() => Promise.resolve(true))
    adapter.motionRegistry = registry
    adapter.model.motion = motion
    adapter.elapsed = 0

    const result = adapter.triggerLive2DAction("greeting")
    await Promise.resolve()
    expect(result.supported).toBe(true)
    expect(action).toMatchObject({ durationMs: 6000, motion: { loop: true, durationMs: 6000, fadeInMs: 500, fadeOutMs: 500 } })
    expect(motion).toHaveBeenCalledWith("Cry", 0, 3, { loop: true })

    adapter.elapsed = 1.3
    adapter.advanceActiveAction()
    expect(adapter.activeAction?.loop).toBe(true)
    expect(adapter.currentAnimation).toBe("greeting")
  })

  it("does not invent a native timeout when motion metadata is unavailable", () => {
    const { adapter, capabilities } = fixture()
    const motionFile = "motions/missing.motion3.json"
    const enriched = scanLive2DModelCapabilities({
      settings: { FileReferences: { Motions: { Cry: [{ File: motionFile }] } } },
      parameters: { ids: capabilities.parameters.map(parameter => parameter.id) },
      physics: { PhysicsSettings: [] },
    })
    const nativeManifest = { ...manifest, animationAliases: { greeting: "motion:Cry:0" } }
    const registry = createLive2DMotionRegistry(nativeManifest, enriched)
    adapter.motionRegistry = registry
    adapter.model.motion = vi.fn(() => Promise.resolve(true))
    adapter.triggerLive2DAction("greeting")
    adapter.elapsed = 20
    adapter.advanceActiveAction()
    expect(adapter.activeAction).toBeTruthy()
  })
})
