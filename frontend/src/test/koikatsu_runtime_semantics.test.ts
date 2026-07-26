import { describe, expect, it } from "vitest"
import * as THREE from "three"
import { KKS_NATURAL_ARM_POSE, collectRequiredPrivateBoneIndices, hasKksBakedLipReplacement, injectKksMtoonFragmentShader, isKksCarrierFaceMaterial, isKksNeutralManikinSurfaceMaterial, isKksRuntimeCarrierFaceHelper, isKksUnparentedAccessory, isRuntimeClothingNodeVisible, isRuntimeExpressionOnlyHeadItem, isRuntimeHeadReady, isRuntimeSemanticItemVisible, kksAccessoryAdjustmentMatrix, kksAuthoredMouthOpacity, kksFaceLayerDepthPolicy, kksFaceTextureFlipY, kksFaceTextureSlotForMaterial, kksNativeHeadLayerPolicy, kksPupilUvTransform, kksRigidRendererPlacement, mergeRuntimeNativeShellItems, resolveKksAbmxBone, resolveKksFaceMaterialTint, resolveKksHeadShapeDriverId, resolveRuntimeAlphaMode, resolveRuntimeBindPoseAssignments, resolveRuntimeBoneRemap, resolveRuntimeCompactSkinBinding, resolveRuntimeHierarchyNodeCount, resolveRuntimeMaterialEditorRenderOrder, resolveRuntimeMaterialPropertyOverride, resolveRuntimeMaterialShaderOverride, resolveRuntimeMaterialTextureOverride, resolveRuntimeShaderFamily, resolveRuntimeSkinBoneCount, runtimeAppearanceColors, runtimeAppearancePatternSlots, runtimeAppearancePrimaryColor, runtimeClothingTextureSet, runtimeColorKeyAlphaPixels, runtimeColorMaskDimensionsAreSafe, runtimeEyeWhitePixels, runtimeMaterialNeedsColorKeyAlpha, runtimePixelSampleIsPrecolored, runtimePixelSampleIsSyntheticWhite, runtimeShadeColorFactor, shouldApplyKksFaceTexture, shouldHideKksNativeFaceOverlay, shouldHideKksRuntimeFaceHelper, shouldSuppressKksNativeFaceItem, validateKksHeadShapeDriver } from "../features/teachers/KoikatsuPmxRig"
import { unityEulerDegreesToQuaternion } from "../features/teachers/kksAbmx"

const item = (property: string, stateNode = "", clothesStates = Array(9).fill(0)) => ({
  id: `${property}:${stateNode}`,
  source_kind: "clothes",
  source_property: `outfit0.ChaFileClothes.${property}`,
  stateNode,
  clothesStates,
})

describe("KKS runtime appearance semantics", () => {
  it("matches MaterialEditor textures by exact material and normalized property", () => {
    const overrides = [
      { slot: 3, material: "Anne HairMaterial1", property: "_MainTex", url: "/tex-2.png" },
      { slot: 3, material: "Anne Material Gorra", property: "MainTex", url: "/tex-3.png" },
    ]
    expect(resolveRuntimeMaterialTextureOverride(overrides, "anne hairmaterial1")?.url).toBe("/tex-2.png")
    expect(resolveRuntimeMaterialTextureOverride(overrides, "Anne HairMaterial2")).toBeUndefined()
  })

  it("keeps authored chromatic textures neutral in the toon shade branch", () => {
    expect(runtimeShadeColorFactor(true, [0.2, 0.3, 0.8, 1], [1, 1, 1])).toEqual([1, 1, 1])
    expect(runtimeShadeColorFactor(false, [0.2, 0.3, 0.8, 1], [1, 1, 1])).toEqual([0.2, 0.3, 0.8])
    expect(runtimeShadeColorFactor(false, undefined, [0.5, 0.25, 1])).toEqual([0.34, 0.17, 0.68])
  })

  it("matches the last exact MaterialEditor value without crossing materials", () => {
    const overrides = [
      { object_type: 1, slot: 0, material: "cf_m_top", property: "_Color1", kind: "color" as const, value: [1, 1, 1, 1] },
      { object_type: 1, slot: 0, material: "cf_m_top", property: "Color1", kind: "color" as const, value: [0.1, 0.2, 0.3, 1] },
      { object_type: 1, slot: 0, material: "other", property: "Color1", kind: "color" as const, value: [0, 0, 0, 1] },
    ]
    expect(resolveRuntimeMaterialPropertyOverride(overrides, "cf_m_top (Instance)", ["Color", "Color1"], "color")?.value)
      .toEqual([0.1, 0.2, 0.3, 1])
    expect(resolveRuntimeMaterialPropertyOverride(overrides, "other2", "Color1", "color")).toBeUndefined()
  })

  it("recognizes an unnamed compressed Unity toon shader from its serialized shade contract", () => {
    expect(resolveRuntimeShaderFamily({
      shaderName: "",
      shaderFamily: "unknown",
      shadeColor: [1, 0.9, 0.8, 1],
      rimFresnelPower: 0.1,
    })).toBe("kks-toon")
    expect(resolveRuntimeShaderFamily({ shaderName: "", shaderFamily: "unknown" })).toBe("unknown")
  })

  it("keeps KKS opaque clothing out of Three.js transparent sorting", () => {
    expect(resolveRuntimeAlphaMode({
      shaderName: "Koikano/main_clothes_opaque",
      alphaMode: "blend",
      depthWrite: false,
    })).toBe("cutout")
    expect(resolveRuntimeAlphaMode({
      shaderName: "Koikano/main_clothes_alpha",
      alphaMode: "cutout",
    })).toBe("blend")
    expect(resolveRuntimeAlphaMode({ shaderName: "Custom/Glass", alphaMode: "blend" })).toBe("blend")
  })

  it("applies the last exact MaterialEditor shader without crossing materials", () => {
    const overrides = [
      {
        object_type: 2,
        slot: 3,
        material: "Anne Material Cara",
        shader: "Shader Forge/main_opaque",
        render_queue: null,
      },
      {
        object_type: 2,
        slot: 3,
        material: "Anne Material Cara",
        shader: "Shader Forge/main_item_ditherd",
        render_queue: null,
      },
    ]
    const override = resolveRuntimeMaterialShaderOverride(overrides, "Anne Material Cara (Instance)")
    expect(override?.shader).toBe("Shader Forge/main_item_ditherd")
    expect(resolveRuntimeMaterialShaderOverride(overrides, "different")).toBeUndefined()
    expect(resolveRuntimeAlphaMode({ shaderName: String(override?.shader) })).toBe("cutout")
    expect(resolveRuntimeMaterialEditorRenderOrder(override)).toBe(10)
  })

  it("maps explicit MaterialEditor render queues to a bounded draw order", () => {
    expect(resolveRuntimeMaterialEditorRenderOrder({
      object_type: 1,
      slot: 0,
      material: "alpha",
      shader: "Shader Forge/main_alpha",
      render_queue: 3000,
    })).toBe(100)
    expect(resolveRuntimeMaterialEditorRenderOrder({
      object_type: 1,
      slot: 0,
      material: "alpha",
      shader: "Shader Forge/main_alpha",
      render_queue: 999999,
    })).toBe(100)
    expect(resolveRuntimeMaterialEditorRenderOrder()).toBe(0)
  })

  it("does not allocate unbounded CPU canvases for 4K colour masks", () => {
    expect(runtimeColorMaskDimensionsAreSafe(1024, 1024)).toBe(true)
    expect(runtimeColorMaskDimensionsAreSafe(4096, 4096)).toBe(false)
    expect(runtimeColorMaskDimensionsAreSafe(1024, 1024, 4096, 4096)).toBe(false)
  })

  it("selects the exact Character Maker texture set from renderer PPtr semantics", () => {
    expect(runtimeClothingTextureSet({
      rendererGroups: [{ component: "clothes", role: "normal", textureSet: 2 }],
      clothingTextureSets: [
        { textureSet: 1, mainTexture: "/first.png" },
        { textureSet: 2, mainTexture: "/second.png" },
      ],
    })?.mainTexture).toBe("/second.png")
    expect(runtimeClothingTextureSet({
      rendererGroups: [
        { component: "clothes", role: "normal", textureSet: 1 },
        { component: "clothes", role: "normal", textureSet: 2 },
      ],
      clothingTextureSets: [],
    })).toBeUndefined()
  })

  it("injects native sequential RGB tint and RG alpha formulas before MToon cutout", () => {
    const source = [
      "void main() {",
      "    diffuseColor *= sampledDiffuseColor;",
      "  #include <alphatest_fragment>",
      "}",
    ].join("\n")
    const result = injectKksMtoonFragmentShader(source, {
      colorMask: true,
      materialAlphaMask: true,
      bodyMask: true,
    })
    expect(result).toContain("kksTint = mix( kksTint, kksSlot2, kksMask.g )")
    expect(result).toContain("min( max( kksAlphaMask.r, 1.0 - kksAlphaA ), max( kksAlphaMask.g, 1.0 - kksAlphaB ) )")
    expect(result.indexOf("kksMaterialAlphaMask")).toBeLessThan(result.indexOf("#include <alphatest_fragment>"))
    expect(result).toContain("texture2D( kksBodyMask, uv ).g")
  })

  it("injects the native KKS pattern UV and low-alpha composition formulas", () => {
    const result = injectKksMtoonFragmentShader([
      "void main() {",
      "    diffuseColor *= sampledDiffuseColor;",
      "}",
    ].join("\n"), { colorMask: true, patternSlots: [true, false, false] })
    expect(result).toContain("(sourceUv + 2.0 * offset - 1.0) * (20.0 - 19.0 * tiling) - 0.5")
    expect(result).toContain("(rotate - 0.5) * 6.283185307179586")
    expect(result).toContain("patternSample.a >= 0.0 && patternSample.a <= 0.25")
    expect(result).toContain("patternSample.a * 3.0 - 2.0")
    expect(result).toContain("kksPatternSlot(texture2D(kksPattern1")
  })

  it("turns a shader colour-key eye atlas into a transparent cutout", () => {
    const pixels = new Uint8ClampedArray([
      96, 96, 96, 255,
      250, 250, 250, 255,
      96, 96, 96, 255,
      96, 96, 96, 255,
    ])
    expect(runtimeColorKeyAlphaPixels(pixels, 2, 2)).toBeGreaterThan(0)
    expect(pixels[3]).toBe(0)
    expect(pixels[7]).toBe(255)
    expect(pixels[11]).toBe(0)
    expect(pixels[15]).toBe(0)
  })

  it("never mistakes the Bonelyfans body material for an eye layer", () => {
    expect(isKksCarrierFaceMaterial("Bonelyfans")).toBe(false)
    expect(isKksCarrierFaceMaterial("cf_m_hitomi_00_cf_Ohitomi_L02")).toBe(true)
    expect(isKksCarrierFaceMaterial("cf_m_face_00")).toBe(true)
  })

  it("keeps only physical body shells on the neutral manikin", () => {
    expect(isKksNeutralManikinSurfaceMaterial("cf_m_face_00")).toBe(true)
    expect(isKksNeutralManikinSurfaceMaterial("cf_m_body")).toBe(true)
    expect(isKksNeutralManikinSurfaceMaterial("Bonelyfans")).toBe(true)
    expect(isKksNeutralManikinSurfaceMaterial("cf_m_sirome_00")).toBe(false)
    expect(isKksNeutralManikinSurfaceMaterial("cf_m_hitomi_00_cf_Ohitomi_L02")).toBe(false)
    expect(isKksNeutralManikinSurfaceMaterial("cf_m_gageye_00")).toBe(false)
  })

  it("suppresses carrier helper layers that warp on arbitrary card heads", () => {
    expect(isKksRuntimeCarrierFaceHelper("cf_m_noseline_00")).toBe(true)
    expect(isKksRuntimeCarrierFaceHelper("cf_m_sirome_00")).toBe(true)
    expect(isKksRuntimeCarrierFaceHelper("cf_m_mayuge_00")).toBe(false)
    expect(isKksRuntimeCarrierFaceHelper("cf_m_hitomi_00_cf_Ohitomi_L02")).toBe(false)
  })

  it("maps native skin, eye-white, line and asymmetric pupil colours by semantic material", () => {
    const appearance = {
      skin_main_color: [1, 0.9, 0.8, 1] as [number, number, number, number],
      skin_sub_color: [1, 0.8, 0.7, 1] as [number, number, number, number],
      eye_white_base_color: [0.8, 0.85, 0.9, 1] as [number, number, number, number],
      eyebrow_color: [0.2, 0.3, 0.4, 1] as [number, number, number, number],
      eyeline_color: [0.05, 0.06, 0.07, 1] as [number, number, number, number],
      pupils: [
        { base_color: [0.1, 0.8, 0.9, 1] as [number, number, number, number] },
        { base_color: [0.9, 0.7, 0.1, 1] as [number, number, number, number] },
      ],
    }
    expect(resolveKksFaceMaterialTint("cf_m_face_00", appearance)).toEqual([1, 1, 1, 1])
    expect(resolveKksFaceMaterialTint("cf_m_sirome_00", appearance)).toEqual([0.8, 0.85, 0.9, 1])
    expect(resolveKksFaceMaterialTint("cf_m_mayuge_00", appearance)).toEqual([0.2, 0.3, 0.4, 1])
    expect(resolveKksFaceMaterialTint("cf_m_eyeline_up_00", appearance)).toEqual([0.05, 0.06, 0.07, 1])
    expect(resolveKksFaceMaterialTint("cf_m_hitomi_00_cf_Ohitomi_L02", appearance)).toEqual([0.1, 0.8, 0.9, 1])
    expect(resolveKksFaceMaterialTint("cf_m_hitomi_00_cf_Ohitomi_R02", appearance)).toEqual([0.9, 0.7, 0.1, 1])
    expect(resolveKksFaceMaterialTint("cf_m_noseline_00", appearance)).toEqual([0.42, 0.336, 0.294, 0.65])
    expect(resolveKksFaceMaterialTint("cf_m_tang", { ...appearance, lip_line_color: [1, 0, 0, 1] })).toEqual([1, 0, 0, 1])
  })

  it("routes native face and eye materials to reusable texture slots", () => {
    expect(kksFaceTextureSlotForMaterial("cf_m_face_00")).toBe("face_composite")
    expect(kksFaceTextureSlotForMaterial("cf_m_face_00 (Instance)")).toBe("face_composite")
    expect(kksFaceTextureSlotForMaterial("cf_m_sirome_00")).toBe("eye_white")
    expect(kksFaceTextureSlotForMaterial("cf_m_hitomi_00", "cf_Ohitomi_R02")).toBe("pupil_right")
    expect(kksFaceTextureSlotForMaterial("cf_m_eyeline_kage")).toBe("")
    expect(shouldApplyKksFaceTexture("nose", "kks:transparent")).toBe(true)
    expect(shouldApplyKksFaceTexture("eyeline_down", "kks:transparent")).toBe(true)
    expect(shouldApplyKksFaceTexture("face_composite", "kks:transparent")).toBe(false)
    expect(shouldApplyKksFaceTexture("pupil_left", "kks:transparent")).toBe(false)
    expect(shouldHideKksNativeFaceOverlay("nose", "kks:transparent")).toBe(false)
    expect(shouldHideKksNativeFaceOverlay("nose", "kks:transparent", true)).toBe(true)
    expect(shouldHideKksNativeFaceOverlay("eyeline_up", "kks:transparent")).toBe(false)
    expect(kksAuthoredMouthOpacity("cf_m_tang")).toBe(1)
    expect(kksAuthoredMouthOpacity("cf_m_tooth")).toBe(1)
    expect(kksAuthoredMouthOpacity("cf_m_face_00")).toBeNull()
    expect(hasKksBakedLipReplacement({
      lip_line: "/lip-line.png",
      face_composite: "/api/face-composites/face.png",
    })).toBe(true)
    expect(hasKksBakedLipReplacement({
      lip: "/lip.png",
      face_composite: "/api/face-composites/face.png",
    })).toBe(true)
    expect(hasKksBakedLipReplacement({
      face_composite: "/api/face-composites/face.png",
    })).toBe(true)
    expect(hasKksBakedLipReplacement({
      lip_line: "/lip-line.png",
      face_composite: "/api/face-textures/base.png",
    })).toBe(false)
    expect(hasKksBakedLipReplacement({
      lip_line: "/lip-line.png",
      face_composite: "kks:transparent",
    })).toBe(false)
    expect(shouldHideKksRuntimeFaceHelper("cf_m_eyeline_kage")).toBe(true)
    expect(shouldHideKksRuntimeFaceHelper("cf_m_eyeline_00_down", "kks:transparent")).toBe(false)
    expect(shouldHideKksRuntimeFaceHelper("cf_m_eyeline_00_down", "/face-line.png")).toBe(false)
    expect(kksFaceLayerDepthPolicy("face_composite")).toEqual({ depthTest: true, depthWrite: true })
    expect(kksFaceLayerDepthPolicy("pupil_left")).toEqual({ depthTest: true, depthWrite: false })
    expect(kksNativeHeadLayerPolicy("cf_m_hitomi_00")).toEqual({ renderOrder: 130, depthWrite: false, polygonOffsetFactor: -3 })
    expect(kksNativeHeadLayerPolicy("cf_m_tang")).toEqual({ renderOrder: 0, depthWrite: true, polygonOffsetFactor: 0 })
    expect(kksNativeHeadLayerPolicy("cf_m_tooth")).toEqual({ renderOrder: 0, depthWrite: true, polygonOffsetFactor: 0 })
    expect(shouldSuppressKksNativeFaceItem("head:cf_O_noseline", true)).toBe(false)
    expect(shouldSuppressKksNativeFaceItem("head:o_tang", false)).toBe(false)
    expect(shouldSuppressKksNativeFaceItem("head:o_tang", true)).toBe(true)
    expect(kksFaceTextureFlipY("face_composite")).toBe(true)
    expect(kksFaceTextureFlipY("pupil_left")).toBe(false)
  })

  it("reproduces KKS neutral-gaze pupil scale and mirrored horizontal offset", () => {
    const appearance = { pupil_width: 0.85, pupil_height: 0.69, pupil_x: 0.62, pupil_y: 0.4 }
    const left = kksPupilUvTransform(appearance, false)
    const right = kksPupilUvTransform(appearance, true)
    expect(left.repeat[0]).toBeCloseTo(1.1, 6)
    expect(left.repeat[1]).toBeCloseTo(1.42, 6)
    expect(left.offset[0]).toBeCloseTo(-0.09144, 6)
    expect(right.offset[0]).toBeCloseTo(-0.00856, 6)
    expect(left.offset[1]).toBeCloseTo(-0.1832, 6)
    expect(right.repeat).toEqual(left.repeat)
    expect(right.offset[1]).toBeCloseTo(left.offset[1], 6)
  })

  it("uses native midpoint defaults when pupil card fields are absent", () => {
    expect(kksPupilUvTransform(undefined, false).repeat).toEqual([1.8, 1.8])
    expect(kksPupilUvTransform(undefined, true).offset[0]).toBeCloseTo(-0.316, 6)
  })

  it("bakes the KKS eye-white greyscale ramp between the card sub and base colours", () => {
    const pixels = new Uint8ClampedArray([
      0, 0, 0, 255,
      128, 128, 128, 128,
      255, 255, 255, 255,
      17, 29, 41, 0,
    ])
    expect(runtimeEyeWhitePixels(pixels, [1, .9, .8, 1], [.6, .7, .8, 1])).toBe(4)
    expect(Array.from(pixels.slice(0, 4))).toEqual([153, 179, 204, 255])
    expect(Array.from(pixels.slice(8, 12))).toEqual([255, 230, 204, 255])
    expect(Array.from(pixels.slice(12, 16))).toEqual([160, 182, 204, 255])
    expect(pixels[4]).toBeGreaterThan(153)
    expect(pixels[4]).toBeLessThan(255)
    expect(pixels[7]).toBe(255)
  })

  it("reads tint from card fields by resource kind instead of asset names", () => {
    expect(runtimeAppearancePrimaryColor({ source_kind: "hair", appearance: { baseColor: [0.1, 0.2, 0.3, 1] } })).toEqual([0.1, 0.2, 0.3, 1])
    expect(runtimeAppearancePrimaryColor({ source_kind: "accessory", appearance: { color: [[0.4, 0.5, 0.6, 0.7]] } })).toEqual([0.4, 0.5, 0.6, 0.7])
    expect(runtimeAppearancePrimaryColor({ source_kind: "clothes", appearance: { colorInfo: [{ baseColor: [0.7, 0.8, 0.9, 1] }] } })).toEqual([0.7, 0.8, 0.9, 1])
  })

  it("does not invent a tint when an optional appearance field is absent", () => {
    expect(runtimeAppearancePrimaryColor({ source_kind: "accessory", appearance: {} })).toBeUndefined()
  })

  it("uses Color4 and its alpha for an accessory rendAlpha renderer", () => {
    const item = {
      source_kind: "accessory",
      rendererGroups: [{ component: "accessory", role: "alpha" }],
      appearance: { color: [
        [1, 0, 0, 1],
        [0, 1, 0, 1],
        [0, 0, 1, 1],
        [0.2, 0.3, 0.4, 0],
      ] },
    }
    expect(runtimeAppearancePrimaryColor(item as never)).toEqual([0.2, 0.3, 0.4, 0])
    expect(isRuntimeSemanticItemVisible(item as never)).toBe(false)
  })

  it("keeps all four card colours required by the RGB garment mask", () => {
    expect(runtimeAppearanceColors({
      source_kind: "clothes",
      appearance: { colorInfo: [
        { baseColor: [1, 1, 1, 1] },
        { baseColor: [0.5, 0.5, 0.5, 1] },
        { baseColor: [0.2, 0.3, 0.4, 1] },
        { baseColor: [0.7, 0.1, 0.2, 1] },
      ] },
    })).toHaveLength(4)
  })

  it("maps the native top SubC pass to colour slots 4, 2, and 3", () => {
    expect(runtimeAppearanceColors({
      source_kind: "clothes",
      source_property: "ClothesJacketSubC",
      appearance: { colorInfo: [
        { baseColor: [1, 0, 0, 1] },
        { baseColor: [0, 1, 0, 1] },
        { baseColor: [0, 0, 1, 1] },
        { baseColor: [0.7, 0.1, 0.2, 1] },
      ] },
    })).toEqual([
      [0.7, 0.1, 0.2, 1],
      [0, 1, 0, 1],
      [0, 0, 1, 1],
    ])
  })

  it("maps all SubC pattern inputs from slot 4 rather than only its base colour", () => {
    expect(runtimeAppearancePatternSlots({
      source_kind: "clothes",
      source_property: "ClothesSailorSubC",
      appearance: { colorInfo: [
        { patternTexture: "/wrong.png", tiling: [0, 0], offset: [0, 0], rotate: 0 },
        { patternTexture: "/two.png" },
        { patternTexture: "/three.png" },
        { patternTexture: "/four.png", patternColor: [0.1, 0.2, 0.3, 0.4], tiling: [0.6, 0.7], offset: { x: 0.2, y: 0.3 }, rotate: 0.8 },
      ] },
    })[0]).toEqual({
      texture: "/four.png",
      patternColor: [0.1, 0.2, 0.3, 0.4],
      tiling: [0.6, 0.7],
      offset: [0.2, 0.3],
      rotate: 0.8,
    })
  })

  it("keeps the base, root, and tip colours required by the KKS hair mask", () => {
    expect(runtimeAppearanceColors({
      source_kind: "hair",
      appearance: {
        baseColor: [0.1, 0.2, 0.3, 1],
        startColor: [0.4, 0.5, 0.6, 1],
        endColor: [0.7, 0.8, 0.9, 1],
      },
    })).toEqual([
      [0.1, 0.2, 0.3, 1],
      [0.4, 0.5, 0.6, 1],
      [0.7, 0.8, 0.9, 1],
    ])
  })

  it("keeps all four native accessory colours for accessory and hair-like masks", () => {
    expect(runtimeAppearanceColors({
      source_kind: "accessory",
      appearance: { color: [
        [1, 0.9, 0.8, 1],
        [0.7, 0.6, 0.5, 1],
        [0.4, 0.3, 0.2, 1],
        [0.1, 0.2, 0.3, 0],
      ] },
    })).toEqual([
      [1, 0.9, 0.8, 1],
      [0.7, 0.6, 0.5, 1],
      [0.4, 0.3, 0.2, 1],
      [0.1, 0.2, 0.3, 0],
    ])
  })

  it("does not multiply an already-coloured mod texture by the card tint again", () => {
    expect(runtimePixelSampleIsPrecolored([
      240, 220, 170, 255,
      210, 165, 115, 255,
      220, 25, 60, 255,
      80, 65, 55, 255,
    ])).toBe(true)
    expect(runtimePixelSampleIsPrecolored([
      240, 240, 240, 255,
      180, 180, 180, 255,
      90, 90, 90, 255,
      20, 20, 20, 255,
    ])).toBe(false)
  })

  it("distinguishes a generated white tint base from authored texture detail", () => {
    expect(runtimePixelSampleIsSyntheticWhite([
      255, 255, 255, 255,
      250, 250, 250, 255,
      255, 255, 255, 255,
      252, 252, 252, 255,
    ])).toBe(true)
    expect(runtimePixelSampleIsSyntheticWhite([
      255, 255, 255, 255,
      180, 180, 180, 255,
      40, 50, 60, 255,
      255, 255, 255, 255,
    ])).toBe(false)
  })
})

describe("KKS runtime clothing state semantics", () => {
  it("keeps explicitly selected bra and shorts even when outer clothes exist", () => {
    expect(isRuntimeClothingNodeVisible(item("ClothesBra"))).toBe(true)
    expect(isRuntimeClothingNodeVisible(item("ClothesShorts"))).toBe(true)
  })

  it("selects canonical top state nodes from Status.clothesState", () => {
    expect(isRuntimeClothingNodeVisible(item("ClothesTop", "n_top_a"))).toBe(true)
    expect(isRuntimeClothingNodeVisible(item("ClothesTop", "n_top_b"))).toBe(false)
    const half = [1, 0, 0, 0, 0, 0, 0, 0, 0]
    expect(isRuntimeClothingNodeVisible(item("ClothesTop", "n_top_a", half))).toBe(false)
    expect(isRuntimeClothingNodeVisible(item("ClothesTop", "n_top_b", half))).toBe(true)
  })

  it("uses the bottom and underwear state slots required by ChaControl", () => {
    const states = [0, 1, 0, 2, 0, 0, 0, 0, 0]
    expect(isRuntimeClothingNodeVisible(item("ClothesBot", "n_bot_a", states))).toBe(false)
    expect(isRuntimeClothingNodeVisible(item("ClothesBot", "n_bot_b", states))).toBe(true)
    expect(isRuntimeClothingNodeVisible(item("ClothesShorts", "n_bot_b", states))).toBe(false)
    expect(isRuntimeClothingNodeVisible(item("ClothesShorts", "n_bot_c", states))).toBe(true)
  })
})

describe("KKS runtime bone path mapping", () => {
  const targets = [
    { name: "joint", path: "Body/root/left/joint" },
    { name: "joint", path: "Body/root/right/joint" },
    { name: "head", path: "Body/root/spine/head" },
  ]

  it("uses the hierarchy path to distinguish duplicate bone names", () => {
    expect(resolveRuntimeBoneRemap(
      ["joint", "joint"],
      ["Prefab/root/left/joint", "Prefab/root/right/joint"],
      targets,
    )).toEqual([0, 1])
  })

  it("never falls back to first-name-wins when the name is ambiguous", () => {
    expect(resolveRuntimeBoneRemap(["joint"], [""], targets)).toEqual([undefined])
    expect(resolveRuntimeBoneRemap(["joint"], ["joint"], targets)).toEqual([undefined])
  })

  it("allows name fallback only for a unique target and canonicalizes exporter spelling", () => {
    expect(resolveRuntimeBoneRemap(["head"], [""], targets)).toEqual([2])
    expect(resolveRuntimeBoneRemap(
      ["cf_j_elboback_L"],
      ["Prefab/arm/cf_j_elboback_L"],
      [{ name: "cf_j_elbow_L", path: "Body/arm/cf_j_elbow_L" }],
    )).toEqual([0])
  })

  it("rejects a hierarchy path whose leaf disagrees with the declared source bone", () => {
    expect(resolveRuntimeBoneRemap(
      ["joint"],
      ["Prefab/root/spine/head"],
      targets,
    )).toEqual([undefined])
  })
})

describe("KKS renderer-local inverse bind contract", () => {
  const identity = [
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    0, 0, 0, 1,
  ]
  const translated = [...identity]
  translated[12] = 3

  it("preserves source bind poses for shared and private target bones", () => {
    expect(resolveRuntimeBindPoseAssignments([1, 3], [...identity, ...translated], 4)).toEqual([
      undefined,
      identity,
      undefined,
      translated,
    ])
  })

  it("allows duplicate source entries only when their inverse binds agree", () => {
    expect(resolveRuntimeBindPoseAssignments([0, 0], [...identity, ...identity], 1)).toEqual([identity])
    expect(() => resolveRuntimeBindPoseAssignments([0, 0], [...identity, ...translated], 1)).toThrow("conflicting runtime bind poses")
  })

  it("rejects incomplete, non-finite, or out-of-range bind data", () => {
    expect(() => resolveRuntimeBindPoseAssignments([0], [], 1)).toThrow("invalid runtime bind pose count")
    const nonFinite = [...identity]
    nonFinite[4] = Number.NaN
    expect(() => resolveRuntimeBindPoseAssignments([0], nonFinite, 1)).toThrow("invalid runtime bind pose")
    expect(() => resolveRuntimeBindPoseAssignments([2], identity, 1)).toThrow("invalid runtime bind target")
  })

  it("accepts unused Unity renderer bones without inventing missing bind poses", () => {
    expect(resolveRuntimeSkinBoneCount(4, 6, [0, 3, 0, 0], [1, 0, 0, 0], identity)).toBe(1)
    expect(() => resolveRuntimeSkinBoneCount(4, 6, [0, 1, 0, 0], [.5, .5, 0, 0], identity)).toThrow("missing bind pose")
  })
})

describe("KKS compact renderer skeleton contract", () => {
  it("shares canonical targets while keeping vertex indices renderer-local", () => {
    const binding = resolveRuntimeCompactSkinBinding(
      [10, 20, 10, 30, 999],
      [0, 1, 2, 3, 0, 2, 1, 3],
      4,
    )
    expect(binding.targetBoneIndices).toEqual([10, 20, 30])
    expect(binding.sourceToSkeleton).toEqual([0, 1, 0, 2])
    expect(Array.from(binding.skinIndices)).toEqual([0, 1, 0, 2, 0, 0, 1, 2])
  })

  it("never adds hierarchy-only targets or accepts an unmapped weighted bone", () => {
    const binding = resolveRuntimeCompactSkinBinding([4, 8, 12, 580], [0, 1, 2, 0], 3)
    expect(binding.targetBoneIndices).toEqual([4, 8, 12])
    expect(binding.targetBoneIndices).not.toContain(580)
    expect(() => resolveRuntimeCompactSkinBinding([4, undefined], [0, 1], 2)).toThrow("unmapped compact skin bone")
  })
})

describe("KKS private bone materialization", () => {
  it("ignores unmatched hierarchy-only wrappers that no weighted vertex uses", () => {
    expect([...collectRequiredPrivateBoneIndices([0, 1, undefined], [-1, 0, -1], 2)]).toEqual([])
  })

  it("keeps an unresolved weighted bone and its unresolved ancestor chain", () => {
    expect([...collectRequiredPrivateBoneIndices([undefined, 1, undefined], [2, -1, -1], 1)]).toEqual([0, 2])
  })
})

describe("KKS native prefab socket hierarchy", () => {
  const identity = [
    1, 0, 0, 0,
    0, 1, 0, 0,
    0, 0, 1, 0,
    0, 0, 0, 1,
  ]

  it("accepts the selected-head tree that carries HairParent and accessory sockets", () => {
    expect(resolveRuntimeHierarchyNodeCount({
      names: ["p_cf_head_00", "cf_J_FaceUp_ty", "a_n_headside"],
      paths: ["p_cf_head_00", "p_cf_head_00/cf_J_FaceUp_ty", "p_cf_head_00/cf_J_FaceUp_ty/a_n_headside"],
      parentIndices: [-1, 0, 1],
      localMatrices: [...identity, ...identity, ...identity],
    })).toBe(3)
  })

  it("rejects cyclic/forward parents and truncated matrices before touching Three.js", () => {
    expect(() => resolveRuntimeHierarchyNodeCount({
      names: ["root", "child"],
      paths: ["root", "root/child"],
      parentIndices: [-1, 1],
      localMatrices: [...identity, ...identity],
    })).toThrow("invalid runtime hierarchy parent")
    expect(() => resolveRuntimeHierarchyNodeCount({
      names: ["root"], paths: ["root"], parentIndices: [-1], localMatrices: identity.slice(0, 15),
    })).toThrow("invalid runtime hierarchy arrays")
  })

  it("requires unique full paths whose leaf matches the declared node", () => {
    expect(() => resolveRuntimeHierarchyNodeCount({
      names: ["root", "child"],
      paths: ["root", "root/not-child"],
      parentIndices: [-1, 0],
      localMatrices: [...identity, ...identity],
    })).toThrow("invalid runtime hierarchy path")
    expect(() => resolveRuntimeHierarchyNodeCount({
      names: ["root", "root"],
      paths: ["root", "root"],
      parentIndices: [-1, -1],
      localMatrices: [...identity, ...identity],
    })).toThrow("duplicate runtime hierarchy path")
  })
})

describe("KKS universal native shell completion", () => {
  const shared = [
    { id: "shared-body", source_kind: "neutral_body", runtimeSkin: "body.json" },
    { id: "shared:cf_O_face", source_kind: "head", runtimeSkin: "face.json" },
    { id: "shared:cf_O_sirome", source_kind: "head", runtimeSkin: "eyes.json" },
  ]

  it("adds the shared body and exactly one complete default head when a card has no runtime assets", () => {
    const result = mergeRuntimeNativeShellItems([], shared)
    expect(result.fallbackBody).toBe(true)
    expect(result.fallbackHead).toBe(true)
    expect(result.items.map(item => item.id)).toEqual(["shared-body", "shared:cf_O_face", "shared:cf_O_sirome"])
  })

  it("drops incomplete selected-head fragments instead of mixing two head prefabs", () => {
    const result = mergeRuntimeNativeShellItems([
      { id: "card-body", source_kind: "neutral_body", runtimeSkin: "body.json" },
      { id: "broken:cf_O_sirome", source_kind: "head", runtimeSkin: "broken-eyes.json" },
      { id: "card-hair", source_kind: "hair", runtimeSkin: "hair.json" },
    ], shared)
    expect(result.fallbackBody).toBe(false)
    expect(result.fallbackHead).toBe(true)
    expect(result.items.map(item => item.id)).toEqual(["card-body", "shared:cf_O_face", "shared:cf_O_sirome", "card-hair"])
  })

  it("keeps a complete selected head and never appends the default head", () => {
    const result = mergeRuntimeNativeShellItems([
      { id: "card-body", source_kind: "neutral_body", runtimeSkin: "body.json" },
      { id: "card:cf_O_face", source_kind: "head", runtimeSkin: "card-face.json" },
    ], shared)
    expect(result.fallbackHead).toBe(false)
    expect(result.items.map(item => item.id)).toEqual(["card-body", "card:cf_O_face"])
  })
})

describe("KKS atomic head runtime contract", () => {
  const driver = {
    version: 1,
    headId: 0,
    sliderCount: 52,
    categories: [{
      slider: 0,
      source: "cf_J_FaceBase",
      position: [false, false, false],
      rotation: [false, false, false],
      scale: [true, false, false],
    }],
    animation: {
      cf_J_FaceBase: [{ key: 0, position: [0, 0, 0], rotation: [0, 0, 0], scale: [1, 1, 1] }],
    },
  }

  it("does not silently map an unknown/custom head to profile 0", () => {
    expect(resolveKksHeadShapeDriverId(0)).toBe(0)
    expect(resolveKksHeadShapeDriverId(200)).toBe(200)
    expect(resolveKksHeadShapeDriverId(9999)).toBeNull()
  })

  it("requires backend-ready geometry and an exact driver URL before clearing the warning", () => {
    expect(isRuntimeHeadReady({ headRuntime: { status: "ready", geometryResolved: true, shapeDriverUrl: "/driver.json" } })).toBe(true)
    expect(isRuntimeHeadReady({ headRuntime: { status: "ready", geometryResolved: false, shapeDriverUrl: "/driver.json" } })).toBe(false)
    expect(isRuntimeHeadReady({ headRuntime: { status: "partial", geometryResolved: true, shapeDriverUrl: "/driver.json" } })).toBe(false)
    expect(isRuntimeHeadReady({ headRuntime: {
      status: "invalid",
      geometryResolved: true,
      shapeDriverUrl: "/driver.json",
      reason: "head-face-uv-contract-mismatch",
    } })).toBe(true)
  })

  it("validates the 52-slider profile and rejects a mismatched official profile", () => {
    expect(validateKksHeadShapeDriver(driver, 0).sliderCount).toBe(52)
    expect(() => validateKksHeadShapeDriver(driver, 200)).toThrow("profile mismatch")
    expect(validateKksHeadShapeDriver({ ...driver, headId: -1, profile: 200 }, 200).sliderCount).toBe(52)
    expect(() => validateKksHeadShapeDriver({ ...driver, sliderCount: 51 }, 0)).toThrow("52 sliders")
  })

  it("keeps the native closed-mouth surface while suppressing expression-only head overlays", () => {
    expect(isRuntimeExpressionOnlyHeadItem({ source_kind: "head", id: "ChaFileFace.headId:cf_O_tooth" })).toBe(true)
    expect(isRuntimeExpressionOnlyHeadItem({ source_kind: "head", id: "ChaFileFace.headId:cf_O_namida_L" })).toBe(true)
    expect(isRuntimeExpressionOnlyHeadItem({ source_kind: "head", id: "ChaFileFace.headId:o_tang" })).toBe(false)
    expect(isRuntimeExpressionOnlyHeadItem({ source_kind: "clothes", id: "ChaFileClothes.ClothesTop" })).toBe(false)
  })
})

describe("KKS shared presentation pose", () => {
  it("uses one mirrored skeleton-level relaxed arm pose for every card", () => {
    const byBone = new Map(KKS_NATURAL_ARM_POSE.map(item => [item.bone, item.rotation]))
    expect(byBone.get("cf_j_arm00_L")).toEqual([0, 0, -78])
    expect(byBone.get("cf_j_arm00_R")).toEqual([0, 0, 78])
    expect(byBone.get("cf_j_forearm01_L")).toEqual([0, 0, -6])
    expect(byBone.get("cf_j_forearm01_R")).toEqual([0, 0, 6])
    expect([...byBone.keys()].some(name => /hayasaka|card|resource|id/i.test(name))).toBe(false)
  })
})

describe("KKS native transform semantics", () => {
  it("preserves native none as an explicit unparented accessory mode", () => {
    expect(isKksUnparentedAccessory("none")).toBe(true)
    expect(isKksUnparentedAccessory(" NONE ")).toBe(true)
    expect(isKksUnparentedAccessory("")).toBe(false)
    expect(isKksUnparentedAccessory("a_n_headside")).toBe(false)
  })

  it("keeps prefab root TRS and applies only the move row on the renderer branch", () => {
    const root = new THREE.Matrix4().compose(
      new THREE.Vector3(.01, -.145, .08),
      new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI),
      new THREE.Vector3(1, 1, 1),
    )
    const child = new THREE.Matrix4().makeTranslation(0, .2, 0)
    const raw = [[], [], [
      [10, 0, 0], [0, 0, 0], [1, 1, 1],
      [0, 20, 0], [0, 0, 0], [1, 1, 1],
    ]]
    const actual = kksRigidRendererPlacement([
      { name: "p_acs_root", localMatrix: root.toArray() },
      { name: "N_move2", localMatrix: new THREE.Matrix4().identity().toArray() },
      { name: "renderer", localMatrix: child.toArray() },
    ], raw)
    const expected = root.clone()
      .multiply(new THREE.Matrix4().makeTranslation(0, .2, 0))
      .multiply(child)
    actual.elements.forEach((value, index) => expect(value).toBeCloseTo(expected.elements[index], 8))
    // The N_move row's X translation belongs to the sibling branch and must
    // not leak into this N_move2 renderer.
    expect(actual.elements[12]).not.toBeCloseTo(.1, 6)
  })

  it("skips the browser eye-white Canvas fallback after offline alpha baking", () => {
    expect(runtimeMaterialNeedsColorKeyAlpha({ name: "cf_m_sirome_00", colorKeyAlphaBaked: true })).toBe(false)
    expect(runtimeMaterialNeedsColorKeyAlpha({ name: "cf_m_sirome_00", colorKeyAlphaHandled: true })).toBe(false)
    expect(runtimeMaterialNeedsColorKeyAlpha({ name: "cf_m_sirome_00" })).toBe(true)
    expect(runtimeMaterialNeedsColorKeyAlpha({ name: "cf_m_eyeline_00" })).toBe(false)
  })

  it("uses Unity Quaternion.Euler matrix order for multi-axis card rotations", () => {
    const rotation = [20, 35, 70]
    const expected = new THREE.Matrix4().makeRotationY(THREE.MathUtils.degToRad(35))
      .multiply(new THREE.Matrix4().makeRotationX(THREE.MathUtils.degToRad(20)))
      .multiply(new THREE.Matrix4().makeRotationZ(THREE.MathUtils.degToRad(70)))
    const actual = new THREE.Matrix4().makeRotationFromQuaternion(unityEulerDegreesToQuaternion(rotation))
    actual.elements.forEach((value, index) => expect(value).toBeCloseTo(expected.elements[index], 8))
  })

  it("composes both accessory addMove rows in their authored pivot order", () => {
    const raw = [[], [], [
      [10, 20, 30], [20, 35, 70], [1, 1.2, .8],
      [-5, 4, 3], [15, -25, 40], [.9, 1, 1.1],
    ]]
    const compose = (position: number[], rotation: number[], scale: number[]) => new THREE.Matrix4().compose(
      new THREE.Vector3(position[0] * .01, position[1] * .01, position[2] * .01),
      unityEulerDegreesToQuaternion(rotation),
      new THREE.Vector3(scale[0], scale[1], scale[2]),
    )
    const expected = compose(raw[2][0], raw[2][1], raw[2][2]).multiply(compose(raw[2][3], raw[2][4], raw[2][5]))
    const actual = kksAccessoryAdjustmentMatrix(raw)
    actual.elements.forEach((value, index) => expect(value).toBeCloseTo(expected.elements[index], 8))
  })

  it("resolves ABMX by native BodyTop then accessory slot priority", () => {
    const body = { name: "cf_j_hand_L", source: "body" }
    const clothingDuplicate = { name: "cf_j_hand_L", source: "clothes" }
    const accessory0 = { name: "cf_j_hand_L", source: "accessory0" }
    const accessory1Only = { name: "custom_helper", source: "accessory1" }
    const slots = new Map([
      ["clothes:outfit0.top", [clothingDuplicate]],
      ["accessory:outfit0.accessory1", [accessory1Only]],
      ["accessory:outfit0.accessory0", [accessory0]],
    ])
    expect(resolveKksAbmxBone("cf_j_hand_L", 1, [body], slots)).toBe(body)
    expect(resolveKksAbmxBone("cf_j_hand_L", 0, [body], slots)).toBe(body)
    expect(resolveKksAbmxBone("cf_j_hand_L", 10, [body], slots)).toBe(accessory0)
    expect(resolveKksAbmxBone("custom_helper", 0, [], slots)).toBe(accessory1Only)
  })
})
