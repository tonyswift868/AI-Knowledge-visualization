import { describe, expect, it } from "vitest"
import { isRuntimeAssemblyReady, matchingInstalledKksPack, matchingInstalledKksRuntimePack, selectKksPreviewRoute, toPreviewMorph } from "../features/teachers/KoikatsuCardEditor"
import type { RuntimeManifestPayload } from "../features/teachers/KoikatsuPmxRig"
import type { KoikatsuCardInspection, TeacherAvatar } from "../features/teachers/api"

function readyManifest(): RuntimeManifestPayload {
  return {
    status: "ready",
    items: [
      { id: "body", source_kind: "neutral_body", runtimeSkin: "body.json" },
      { id: "ChaFileFace.headId:cf_O_face", source_kind: "head", runtimeSkin: "head.json" },
    ],
    unresolved_references: [],
    conversion_failures: [],
    headRuntime: {
      status: "ready",
      geometryResolved: true,
      shapeDriverUrl: "head-shapes.json",
      shapeDriverProfile: 0,
    },
  }
}

describe("KKS runtime assembly acceptance gate", () => {
  it("opens imported cards in the authored neutral expression", () => {
    expect(toPreviewMorph({}).smile).toBe(0)
    expect(toPreviewMorph({}).blink).toBe(1)
  })

  it("accepts a complete body and head core when no conversion work is pending", () => {
    expect(isRuntimeAssemblyReady(readyManifest())).toBe(true)
  })

  it("routes a cache-ready card through the universal native template and keeps its manifest", () => {
    expect(selectKksPreviewRoute({ runtimeAssemblyReady: true })).toEqual({
      modelUrl: "/api/koikatsu-cards/shared-base/native-template",
      nativeExport: false,
      useRuntimeManifest: true,
    })
  })

  it("prefers a complete exact export without mixing reconstructed runtime parts", () => {
    expect(selectKksPreviewRoute({
      nativeExportModelUrl: "/api/koikatsu-cards/local/card/model.glb",
      runtimeAssemblyReady: true,
    })).toEqual({
      modelUrl: "/api/koikatsu-cards/local/card/model.glb",
      nativeExport: true,
      useRuntimeManifest: false,
    })
  })

  it("fails closed to the card preview when neither complete path is ready", () => {
    expect(selectKksPreviewRoute({ runtimeAssemblyReady: false })).toEqual({
      modelUrl: "",
      nativeExport: false,
      useRuntimeManifest: false,
    })
  })

  it("rejects a cache-only shell when the selected head contract is not ready", () => {
    const manifest = readyManifest()
    manifest.status = "partial"
    manifest.headRuntime = { status: "cache-miss", geometryResolved: false }
    manifest.conversion_failures = [{ reason: "conversion-cache-miss" }]
    expect(isRuntimeAssemblyReady(manifest)).toBe(false)
  })

  it("keeps the ready body and face when only optional clothing conversions are cold", () => {
    const manifest = readyManifest()
    manifest.status = "partial"
    manifest.items = [
      ...(manifest.items || []),
      { id: "ChaFileFace.headId:cf_O_face", source_kind: "head", runtimeSkin: "head.json" },
    ]
    manifest.conversion_failures = [{ property: "outfit0.accessory7", reason: "conversion-cache-miss" }]
    expect(isRuntimeAssemblyReady(manifest)).toBe(true)
  })

  it("keeps clothes when only the selected head UV contract requires fallback", () => {
    const manifest = readyManifest()
    manifest.status = "partial"
    manifest.headRuntime = {
      status: "invalid",
      geometryResolved: true,
      reason: "head-face-uv-contract-mismatch",
      faceUvCompatible: false,
    }
    manifest.items = [
      ...(manifest.items || []),
      { id: "outfit0.top", source_kind: "clothes", runtimeSkin: "top.json" },
    ]
    expect(isRuntimeAssemblyReady(manifest)).toBe(true)
  })

  it("accepts a valid headmod main face renderer without requiring cf_O_face", () => {
    const manifest = readyManifest()
    manifest.status = "partial"
    manifest.headRuntime = {
      status: "invalid",
      geometryResolved: true,
      reason: "head-face-uv-contract-mismatch",
      faceUvCompatible: false,
      shapeDriverUrl: "head-shapes.json",
    }
    manifest.items = [
      { id: "body", source_kind: "neutral_body", runtimeSkin: "body.json" },
      { id: "ChaFileFace.headId:face_026", source_kind: "head", headMain: true, runtimeSkin: "head.json" },
    ]
    expect(isRuntimeAssemblyReady(manifest)).toBe(true)
  })

  it("matches a downloaded character pack by the path-independent card fingerprint", () => {
    const card = { native_fingerprint: "a".repeat(64) } as KoikatsuCardInspection
    const pack = {
      model_id: "pack-a",
      asset_profile: "kks-character-pack",
      card_fingerprint: "a".repeat(64),
      appearance_contract: {
        status: "ready",
        card_fingerprint: "a".repeat(64),
        model_kind: "kkbp-web",
        receipt_hash: "f".repeat(64),
      },
    } as TeacherAvatar
    const unrelated = {
      model_id: "pack-b",
      asset_profile: "kks-character-pack",
      card_fingerprint: "b".repeat(64),
    } as TeacherAvatar
    expect(matchingInstalledKksPack(card, [unrelated, pack])).toBe(pack)
  })

  it("does not promote a native runtime pack to a visually verified replacement GLB", () => {
    const card = { native_fingerprint: "c".repeat(64) } as KoikatsuCardInspection
    const pack = {
      model_id: "runtime-pack-c",
      asset_profile: "kks-runtime-pack",
      card_fingerprint: "c".repeat(64),
      model_url: null,
      runtime_manifest_url: "/api/koikatsu-runtime-packs/runtime-pack-c/manifest",
    } as TeacherAvatar
    expect(matchingInstalledKksPack(card, [pack])).toBeUndefined()
    expect(matchingInstalledKksRuntimePack(card, [pack])).toBe(pack)
  })

  it("matches runtime packs only by path-independent card fingerprint", () => {
    const card = { native_fingerprint: "1".repeat(64) } as KoikatsuCardInspection
    const wrong = {
      model_id: "runtime-wrong",
      asset_profile: "kks-runtime-pack",
      card_fingerprint: "2".repeat(64),
      runtime_manifest_url: "/api/koikatsu-runtime-packs/wrong/manifest",
    } as TeacherAvatar
    const matching = {
      model_id: "runtime-matching",
      asset_profile: "kks-runtime-pack",
      card_fingerprint: "1".repeat(64),
      runtime_manifest_url: "/api/koikatsu-runtime-packs/matching/manifest",
    } as TeacherAvatar
    expect(matchingInstalledKksRuntimePack(card, [wrong, matching])).toBe(matching)
  })

  it("rejects a character pack without a complete appearance receipt", () => {
    const fingerprint = "d".repeat(64)
    const card = { native_fingerprint: fingerprint } as KoikatsuCardInspection
    const pack = {
      model_id: "legacy-pack-d",
      asset_profile: "kks-character-pack",
      card_fingerprint: fingerprint,
    } as TeacherAvatar
    expect(matchingInstalledKksPack(card, [pack])).toBeUndefined()
  })

  it("rejects ready labels without a valid receipt hash and KKBP model kind", () => {
    const fingerprint = "e".repeat(64)
    const card = { native_fingerprint: fingerprint } as KoikatsuCardInspection
    const incomplete = {
      model_id: "pack-e",
      asset_profile: "kks-character-pack",
      card_fingerprint: fingerprint,
      appearance_contract: {
        status: "ready",
        card_fingerprint: fingerprint,
        model_kind: "shared-runtime",
        receipt_hash: "not-a-sha",
      },
    } as TeacherAvatar
    expect(matchingInstalledKksPack(card, [incomplete])).toBeUndefined()
  })
})
