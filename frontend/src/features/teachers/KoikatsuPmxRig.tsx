import { useEffect, useRef } from "react"
import * as THREE from "three"
import { MToonMaterial } from "@pixiv/three-vrm"
import { MMDLoader, OBJLoader } from "three-stdlib"
import type { KoikatsuAbmxModifier, KoikatsuAppearance, KoikatsuCardInspection, TeacherAvatarMorph } from "./api"
import { evaluateKksAbmxTransform, unityEulerDegreesToQuaternion } from "./kksAbmx"
import {
  evaluateKksBodyAlwaysShape,
  evaluateKksBodyShape,
  KKS_BODY_ALWAYS_TARGET_BONES,
  KKS_BODY_TARGET_BONES,
  mergeKksLocalTransformPatch,
  type KksBodyShapeDriverData,
  type KksLocalTransform,
  type KksLocalTransformPatch,
} from "./kksBodyShapeDriver"
import { evaluateKksHeadShape, resolveKksFaceBaseScale, type KksHeadShapeDriverData } from "./kksHeadShapeDriver"
import {
  KKS_BODY_SLIDER_NAMES,
    KKS_DEFAULT_FEMALE_BODY_VALUES,
    loadBundledKksHeadShapeDriver,
    loadKksBodyShapeDriver,
  nextKksCameraZoom,
} from "./kksGltfRuntime"

export const KKS_NATURAL_ARM_POSE = [
  { bone: "cf_j_arm00_L", rotation: [0, 0, -78] as const },
  { bone: "cf_j_forearm01_L", rotation: [0, 0, -6] as const },
  { bone: "cf_j_arm00_R", rotation: [0, 0, 78] as const },
  { bone: "cf_j_forearm01_R", rotation: [0, 0, 6] as const },
] as const

type Props = {
  modelUrl: string
  runtimeMode?: "native-template" | "pmx-export"
  cameraPreset?: "body" | "upper" | "face"
  cameraZoom?: number
  cameraYaw?: number
  cameraResetToken?: number
  extrasEnabled?: boolean
  extrasManifestUrl?: string
  /** Already-fetched card manifest; avoids resolving the same card twice. */
  extrasManifestPayload?: RuntimeManifestPayload
  /** True only when modelUrl is this card's complete KKBP export. */
  nativeExport?: boolean
  /** Stable card identity used to isolate native slider baselines when a shared PMX carrier is reused. */
  nativeProfileKey?: string
  /** Render the shared white manikin instead of the carrier card appearance. */
  neutralOnly?: boolean
  morph?: TeacherAvatarMorph
  /** Full native KKS body-shape map. Unlike the generic morph, this keeps all 44 card sliders. */
  nativeBody?: Record<string, number>
  /** ChaFileBody.typeBone enables KKS's official shapecorrect table. */
  nativeBodyTypeBone?: number
  /** Active-coordinate ABMX modifiers decoded from the card's KKEx block. */
  nativeAbmxModifiers?: KoikatsuAbmxModifier[]
  /** Native KKS face-shape values, indexed by the original 52 slider positions. */
  nativeFace?: Record<string, number>
  /** KKS head asset id selecting its original animation-curve table. */
  nativeHeadId?: number | null
  /** Card-authored skin, line-art and iris colours; applied only when values change. */
  nativeAppearance?: KoikatsuAppearance
  className?: string
  onError?: (message: string) => void
  /** Reports the atomic selected-head result to the editor, not just diagnostics on the canvas. */
  onNativeHeadStateChange?: (state: NativeHeadRuntimeState) => void
  onCameraZoomChange?: (zoom: number) => void
}

export type NativeHeadRuntimeState = {
  status: "loading" | "ready" | "degraded"
  reason?: string
  profile?: string
  fallbackApplied?: boolean
}

type RuntimeSkinPayload = {
  version?: number
  vertices?: number[]
  normals?: number[]
  uvs?: number[]
  indices?: number[]
  boneNames?: string[]
  bonePaths?: string[]
  boneParentIndices?: number[]
  boneParentNames?: string[]
  boneLocalMatrices?: number[]
  /** Number of boneNames entries referenced by skinIndices/bindPoses. Remaining entries are hierarchy-only ancestors. */
  skinBoneCount?: number
  skinIndices?: number[]
  skinWeights?: number[]
  bindPoses?: number[]
  /** Unity's renderer-root translation (used by the shared vanilla body). */
  position?: [number, number, number]
  /** Rigid child transform relative to the selected accessory prefab root. */
  attachmentMatrix?: number[]
  /** Renderer transform relative to the selected prefab root. */
  rendererMatrix?: number[]
  /** Complete prefab-root -> renderer GameObject chain for rigid placement. */
  transformChain?: Array<{ name?: string; localMatrix?: number[] }>
  materials?: Array<{
    name?: string
    start?: number
    count?: number
    texture?: string
    colorMask?: string
    alphaMask?: string
    detailMask?: string
    alphaA?: number
    alphaB?: number
    colorKeyAlphaBaked?: boolean
    colorKeyAlphaHandled?: boolean
    cutoff?: number
    shaderName?: string
    shaderFamily?: "kks-skin" | "kks-hair" | "kks-face-overlay" | "kks-toon" | "mtoon" | "third-party-toon" | "standard" | "unknown"
    baseColor?: number[]
    shadeColor?: number[]
    rimColor?: number[]
    rimFresnelPower?: number
    rimLift?: number
    alphaMode?: "opaque" | "cutout" | "blend"
    depthWrite?: boolean
    transparentWithZWrite?: boolean
  }>
}

type RuntimeMaterialPayload = NonNullable<RuntimeSkinPayload["materials"]>[number]

/**
 * Keep the browser bridge keyed to Unity shader semantics, never to a card,
 * character or material name.  Older prewarmed payloads can lack the compact
 * family field, so their original shader name remains a compatibility input.
 */
export function resolveRuntimeShaderFamily(spec: RuntimeMaterialPayload): NonNullable<RuntimeMaterialPayload["shaderFamily"]> {
  if (spec.shaderFamily && spec.shaderFamily !== "unknown") return spec.shaderFamily
  const shaderName = String(spec.shaderName || "").trim().toLowerCase()
  if (shaderName.includes("main_skin")) return "kks-skin"
  if (shaderName.includes("main_hair")) return "kks-hair"
  if (["main_eyew", "toon_eye", "toon_eyew", "toon_nose", "toon_glasses"].some(token => shaderName.includes(token))) return "kks-face-overlay"
  if (["main_clothes", "main_opaque", "main_alpha", "main_item"].some(token => shaderName.includes(token))) return "kks-toon"
  if (shaderName.includes("mtoon")) return "mtoon"
  if (shaderName.includes("toony colors") || shaderName.includes("poiyomi")) return "third-party-toon"
  if (shaderName === "standard" || shaderName.endsWith("/standard")) return "standard"
  // UnityPy cannot always recover m_Name from Unity's compressed Shader
  // object, especially in zipmods. The serialized material contract remains
  // authoritative: authored shade/rim controls mean this is a lit toon
  // material even when the shader label is blank. This is intentionally based
  // on shader semantics and never on a card, character, or material name.
  if ((Array.isArray(spec.shadeColor) && spec.shadeColor.length >= 3)
    || (Array.isArray(spec.rimColor) && spec.rimColor.length >= 3)
    || Number.isFinite(Number(spec.rimFresnelPower))
    || Number.isFinite(Number(spec.rimLift))) return "kks-toon"
  return "unknown"
}

/**
 * Translate Unity's KKS clothing queue into Three.js semantics.
 *
 * Stock `main_clothes_opaque` materials can retain `_Mode=3`/premultiply
 * flags even though KKS renders them through its opaque clothing pass.
 * Treating those payloads as Three.js transparent objects changes global
 * sorting and interleaves the body, top and skirt.  The shader variant is the
 * stable cross-card contract; explicit Alpha variants remain blended.
 */
export function resolveRuntimeAlphaMode(
  spec: RuntimeMaterialPayload,
): RuntimeMaterialPayload["alphaMode"] {
  const shaderName = String(spec.shaderName || "").trim().toLowerCase()
  // MaterialEditor shader swaps are authoritative. KKS' historical shader
  // label contains the `ditherd` typo; both spellings are alpha-tested and
  // must never become an opaque black plate in Three.js.
  if (shaderName.includes("ditherd") || shaderName.includes("dithered") || shaderName.includes("dither")) return "cutout"
  if (["main_clothes_alpha", "main_alpha", "item_studio_alpha"].some(token => shaderName.includes(token))) return "blend"
  if (["main_clothes_opaque", "main_opaque"].some(token => shaderName.includes(token))) return "cutout"
  return spec.alphaMode
}

type RuntimeHierarchyPayload = {
  version?: number
  names?: string[]
  paths?: string[]
  parentIndices?: number[]
  localMatrices?: number[]
  active?: boolean[]
}

export type RuntimeManifestItem = {
  id: string
  url?: string
  texture?: string
  position?: [number, number, number]
  skinned?: boolean
  runtimeSkin?: string
  runtimeSkinSize?: number
  /** Transform-only prefab tree containing unweighted KKS sockets. */
  runtimeHierarchy?: string
  runtimeHierarchySize?: number
  source_kind?: string
  /** True for the selected head's main face renderer (vanilla or headmod). */
  headMain?: boolean
  source_property?: string
  rigidAttachment?: boolean
  attachmentBone?: string
  /** KKS MessagePack multidimensional array: [rows, columns, vectors]. */
  attachmentAdjustments?: unknown[]
  clothesState?: number
  clothesStates?: number[]
  stateNode?: string
  appearance?: Record<string, unknown>
  rendererEnabled?: boolean
  gameObjectActive?: boolean
  rendererGroups?: Array<{
    component?: "clothes" | "accessory" | "hair"
    role?: "normal" | "alpha" | "hair" | "accessory" | "emblem" | "option" | "sleeve"
    textureSet?: number
    optionSet?: number
    sleeveSet?: number
  }>
  clothingTextureSets?: Array<{
    textureSet: number
    mainTexture?: string
    colorMask?: string
  }>
  materialTextureOverrides?: Array<{
    slot: number
    material: string
    property: string
    url: string
    offset?: unknown
    scale?: unknown
  }>
  materialPropertyOverrides?: Array<{
    object_type: number
    slot: number
    material: string
    property: string
    kind: "color" | "float"
    value: number[] | number
  }>
  materialShaderOverrides?: Array<{
    object_type: number
    slot: number
    material: string
    shader?: string | null
    shader_original?: string | null
    render_queue?: number | null
    render_queue_original?: number | null
  }>
  materialCopyOverrides?: Array<{
    object_type: number
    slot: number
    material: string
    copy_name: string
  }>
  materialRendererOverrides?: Array<{
    object_type: number
    slot: number
    renderer: string
    enabled: boolean
  }>
  /** KKS ListInfo OverBodyMask, prewarmed once and sampled by the GPU. */
  alphaMaskOverride?: string
}

/** Compose KKS addMove rows using Unity Quaternion.Euler's fixed Z-X-Y application order. */
export function kksAccessoryAdjustmentMatrix(raw?: unknown[]): THREE.Matrix4 {
  const result = new THREE.Matrix4().identity()
  const values = Array.isArray(raw?.[2]) ? raw[2] as unknown[] : []
  // KKS stores N_move then N_move2 as successive local TRS rows.
  for (let offset = 0; offset + 2 < values.length; offset += 3) {
    const position = Array.isArray(values[offset]) ? values[offset] as number[] : []
    const rotation = Array.isArray(values[offset + 1]) ? values[offset + 1] as number[] : []
    const scale = Array.isArray(values[offset + 2]) ? values[offset + 2] as number[] : []
    const translation = new THREE.Vector3(
      Number(position[0] || 0) * .01,
      Number(position[1] || 0) * .01,
      Number(position[2] || 0) * .01,
    )
    const quaternion = unityEulerDegreesToQuaternion(rotation)
    const scaling = new THREE.Vector3(
      Number.isFinite(Number(scale[0])) ? Number(scale[0]) : 1,
      Number.isFinite(Number(scale[1])) ? Number(scale[1]) : 1,
      Number.isFinite(Number(scale[2])) ? Number(scale[2]) : 1,
    )
    result.multiply(new THREE.Matrix4().compose(translation, quaternion, scaling))
  }
  return result
}

export function isKksUnparentedAccessory(parentKey: unknown): boolean {
  return String(parentKey || "").trim().toLowerCase() === "none"
}

function kksAccessoryAdjustmentRowMatrix(raw: unknown[] | undefined, rowIndex: number): THREE.Matrix4 | undefined {
  const values = Array.isArray(raw?.[2]) ? raw[2] as unknown[] : []
  const offset = rowIndex * 3
  const position = Array.isArray(values[offset]) ? values[offset] as number[] : []
  const rotation = Array.isArray(values[offset + 1]) ? values[offset + 1] as number[] : []
  const scale = Array.isArray(values[offset + 2]) ? values[offset + 2] as number[] : []
  if (!position.length && !rotation.length && !scale.length) return undefined
  return new THREE.Matrix4().compose(
    new THREE.Vector3(
      Number(position[0] || 0) * .01,
      Number(position[1] || 0) * .01,
      Number(position[2] || 0) * .01,
    ),
    unityEulerDegreesToQuaternion(rotation),
    new THREE.Vector3(
      Number.isFinite(Number(scale[0])) ? Number(scale[0]) : 1,
      Number.isFinite(Number(scale[1])) ? Number(scale[1]) : 1,
      Number.isFinite(Number(scale[2])) ? Number(scale[2]) : 1,
    ),
  )
}

/** Evaluate one rigid renderer exactly along its own prefab branch. */
export function kksRigidRendererPlacement(
  chain: RuntimeSkinPayload["transformChain"],
  adjustments?: unknown[],
  legacyRendererMatrix?: number[],
): THREE.Matrix4 {
  if (!Array.isArray(chain) || !chain.length) {
    const legacy = legacyRendererMatrix?.length === 16
      ? new THREE.Matrix4().fromArray(legacyRendererMatrix)
      : new THREE.Matrix4().identity()
    // Compatibility for old artifacts only. New schema payloads carry a
    // branch-aware transformChain and never collapse the two move nodes.
    return kksAccessoryAdjustmentMatrix(adjustments).multiply(legacy)
  }
  const result = new THREE.Matrix4().identity()
  chain.forEach(node => {
    const name = String(node?.name || "").trim().toLowerCase()
    const moveRow = name === "n_move" ? 0 : name === "n_move2" ? 1 : -1
    const authored = moveRow >= 0 ? kksAccessoryAdjustmentRowMatrix(adjustments, moveRow) : undefined
    const values = node?.localMatrix
    const local = authored || (Array.isArray(values) && values.length === 16 && values.every(Number.isFinite)
      ? new THREE.Matrix4().fromArray(values)
      : new THREE.Matrix4().identity())
    result.multiply(local)
  })
  return result
}

export function runtimeClothingTextureSet(item: Pick<RuntimeManifestItem, "rendererGroups" | "clothingTextureSets">) {
  const sets = new Set(
    (item.rendererGroups || [])
      .filter(group => group.component === "clothes" && (group.role === "normal" || group.role === "alpha"))
      .map(group => Number(group.textureSet))
      .filter(value => Number.isInteger(value) && value >= 1 && value <= 3),
  )
  if (sets.size !== 1) return undefined
  const [wanted] = [...sets]
  return (item.clothingTextureSets || []).find(value => Number(value.textureSet) === wanted)
}

type KksMtoonShaderFeatures = {
  colorMask?: boolean
  materialAlphaMask?: boolean
  bodyMask?: boolean
  patternSlots?: boolean[]
}

export function injectKksMtoonFragmentShader(source: string, features: KksMtoonShaderFeatures) {
  const declarations: string[] = []
  if (features.colorMask) declarations.push(
    "uniform sampler2D kksColorMask;",
    "uniform vec3 kksColor1;",
    "uniform vec3 kksColor2;",
    "uniform vec3 kksColor3;",
  )
  const patternSlots = Array.from({ length: 3 }, (_, index) => Boolean(features.colorMask && features.patternSlots?.[index]))
  if (patternSlots.some(Boolean)) {
    patternSlots.forEach((enabled, index) => {
      if (!enabled) return
      const slot = index + 1
      declarations.push(
        `uniform sampler2D kksPattern${slot};`,
        `uniform vec4 kksPatternColor${slot};`,
        `uniform vec2 kksPatternTiling${slot};`,
        `uniform vec2 kksPatternOffset${slot};`,
        `uniform float kksPatternRotate${slot};`,
      )
    })
    declarations.push([
      "vec2 kksPatternUv(vec2 sourceUv, vec2 tiling, vec2 offset, float rotate) {",
      "  vec2 q = (sourceUv + 2.0 * offset - 1.0) * (20.0 - 19.0 * tiling) - 0.5;",
      "  float theta = (rotate - 0.5) * 6.283185307179586;",
      "  float s = sin(theta);",
      "  float c = cos(theta);",
      "  return vec2(c * q.x + s * q.y, -s * q.x + c * q.y) + 0.5;",
      "}",
      "vec3 kksPatternSlot(vec4 patternSample, vec4 patternColor, vec3 baseColor) {",
      "  float averageRgb = (patternSample.r + patternSample.g + patternSample.b) / 3.0;",
      "  float alphaFloor = 1.0 - patternColor.a;",
      "  float weight = max(averageRgb, alphaFloor);",
      "  vec3 residual = mix(patternColor.rgb, baseColor, weight) - patternSample.rgb * patternColor.rgb;",
      "  float lowAlphaBand = float(patternSample.a >= 0.0 && patternSample.a <= 0.25);",
      "  float alphaRamp = max(clamp(patternSample.a * 3.0 - 2.0, 0.0, 1.0), lowAlphaBand);",
      "  float residualWeight = max(alphaRamp, alphaFloor);",
      "  vec3 colored = patternSample.rgb * patternColor.rgb + residualWeight * residual;",
      "  return mix(colored, patternSample.rgb, min(lowAlphaBand, patternColor.a));",
      "}",
    ].join("\n"))
  }
  if (features.materialAlphaMask) declarations.push(
    "uniform sampler2D kksMaterialAlphaMask;",
    "uniform float kksAlphaA;",
    "uniform float kksAlphaB;",
  )
  if (features.bodyMask) declarations.push("uniform sampler2D kksBodyMask;")
  let result = `${declarations.join("\n")}\n${source}`
  if (features.colorMask) {
    const anchor = "    diffuseColor *= sampledDiffuseColor;"
    if (!result.includes(anchor)) throw new Error("MToon color anchor is unavailable")
    const slotLines = [0, 1, 2].map(index => {
      const slot = index + 1
      return patternSlots[index]
        ? `    vec3 kksSlot${slot} = kksPatternSlot(texture2D(kksPattern${slot}, kksPatternUv(uv, kksPatternTiling${slot}, kksPatternOffset${slot}, kksPatternRotate${slot})), kksPatternColor${slot}, kksColor${slot});`
        : `    vec3 kksSlot${slot} = kksColor${slot};`
    })
    result = result.replace(anchor, [
      "    vec3 kksMask = texture2D( kksColorMask, uv ).rgb;",
      ...slotLines,
      "    vec3 kksTint = mix( vec3( 1.0 ), kksSlot1, kksMask.r );",
      "    kksTint = mix( kksTint, kksSlot2, kksMask.g );",
      "    kksTint = mix( kksTint, kksSlot3, kksMask.b );",
      "    kksTint = max(kksTint, vec3(0.0));",
      "    sampledDiffuseColor.rgb *= kksTint;",
      anchor,
    ].join("\n"))
  }
  if (features.materialAlphaMask || features.bodyMask) {
    const anchor = "  #include <alphatest_fragment>"
    if (!result.includes(anchor)) throw new Error("MToon alpha anchor is unavailable")
    const alphaLines: string[] = []
    if (features.materialAlphaMask) alphaLines.push(
      "  vec2 kksAlphaMask = texture2D( kksMaterialAlphaMask, uv ).rg;",
      "  diffuseColor.a *= min( max( kksAlphaMask.r, 1.0 - kksAlphaA ), max( kksAlphaMask.g, 1.0 - kksAlphaB ) );",
    )
    if (features.bodyMask) alphaLines.push("  diffuseColor.a *= texture2D( kksBodyMask, uv ).g;")
    result = result.replace(anchor, `${alphaLines.join("\n")}\n${anchor}`)
  }
  return result
}

function configureKksMtoonShader(
  material: MToonMaterial,
  options: {
    colorMask?: THREE.Texture
    colors?: [number, number, number, number][]
    materialAlphaMask?: THREE.Texture
    alphaA?: number
    alphaB?: number
    bodyMask?: THREE.Texture
    patterns?: Array<{
      texture?: THREE.Texture
      patternColor: [number, number, number, number]
      tiling: [number, number]
      offset: [number, number]
      rotate: number
    }>
  },
) {
  const features: KksMtoonShaderFeatures = {
    colorMask: Boolean(options.colorMask && options.colors?.length),
    materialAlphaMask: Boolean(options.materialAlphaMask),
    bodyMask: Boolean(options.bodyMask),
    patternSlots: Array.from({ length: 3 }, (_, index) => Boolean(options.colorMask && options.patterns?.[index]?.texture)),
  }
  if (!features.colorMask && !features.materialAlphaMask && !features.bodyMask) return
  const color = (index: number) => {
    const value = options.colors?.[index] || options.colors?.[0] || [1, 1, 1, 1]
    return new THREE.Color().setRGB(value[0], value[1], value[2], THREE.SRGBColorSpace)
  }
  const uniforms: Record<string, { value: unknown }> = {}
  if (features.colorMask) Object.assign(uniforms, {
    kksColorMask: { value: options.colorMask },
    kksColor1: { value: color(0) },
    kksColor2: { value: color(1) },
    kksColor3: { value: color(2) },
  })
  features.patternSlots?.forEach((enabled, index) => {
    if (!enabled) return
    const slot = index + 1
    const pattern = options.patterns?.[index]
    if (!pattern?.texture) return
    const patternColor = pattern.patternColor
    Object.assign(uniforms, {
      [`kksPattern${slot}`]: { value: pattern.texture },
      [`kksPatternColor${slot}`]: { value: new THREE.Vector4(...patternColor) },
      [`kksPatternTiling${slot}`]: { value: new THREE.Vector2(...pattern.tiling) },
      [`kksPatternOffset${slot}`]: { value: new THREE.Vector2(...pattern.offset) },
      [`kksPatternRotate${slot}`]: { value: pattern.rotate },
    })
  })
  if (features.materialAlphaMask) Object.assign(uniforms, {
    kksMaterialAlphaMask: { value: options.materialAlphaMask },
    kksAlphaA: { value: Number.isFinite(Number(options.alphaA)) ? Number(options.alphaA) : 1 },
    kksAlphaB: { value: Number.isFinite(Number(options.alphaB)) ? Number(options.alphaB) : 1 },
  })
  if (features.bodyMask) uniforms.kksBodyMask = { value: options.bodyMask }
  const runtimeMaterial = material as MToonMaterial & { uniforms?: Record<string, { value: unknown }> }
  runtimeMaterial.uniforms = { ...(runtimeMaterial.uniforms || {}), ...uniforms }
  material.defines = { ...(material.defines || {}), MTOON_USE_UV: 1 }
  const originalCompile = material.onBeforeCompile.bind(material)
  material.onBeforeCompile = (shader, renderer) => {
    originalCompile(shader, renderer)
    Object.assign(shader.uniforms, uniforms)
    shader.fragmentShader = injectKksMtoonFragmentShader(shader.fragmentShader, features)
  }
  const originalCacheKey = material.customProgramCacheKey.bind(material)
  const featureKey = `${features.colorMask ? 1 : 0}${features.materialAlphaMask ? 1 : 0}${features.bodyMask ? 1 : 0}${features.patternSlots?.map(value => value ? 1 : 0).join("") || "000"}`
  material.customProgramCacheKey = () => `${originalCacheKey()}|kks-v1:${featureKey}`
  material.needsUpdate = true
}

export type RuntimeManifestPayload = {
  card_id?: string
  items?: RuntimeManifestItem[]
  faceTextures?: Record<string, string>
  card?: KoikatsuCardInspection
  mod_index?: { status?: string; row_count?: number }
  /** Backend-resolved atomic geometry + ShapeAnime contract for this exact head. */
  headRuntime?: {
    status?: string
    geometryResolved?: boolean
    shapeDriverUrl?: string
    shapeDriverProfile?: string | number
    digest?: string
    faceUvDigest?: string | null
    faceUvCompatible?: boolean
    faceMaterialShader?: string | null
    faceCompositeCompatible?: boolean
    reason?: string
  }
  status?: "ready" | "partial"
  declared_count?: number
  unresolved_references?: unknown[]
  conversion_failures?: unknown[]
  materialEditorFailures?: unknown[]
  conversion_mode?: "cache-only" | "prewarm"
}

/** The UI must not infer head readiness from the presence of one mesh item. */
export function isRuntimeHeadReady(payload?: RuntimeManifestPayload) {
  const runtime = payload?.headRuntime
  const profile = runtime?.shapeDriverProfile
  const validOfficialProfile = profile === undefined || profile === null
    || profile === 0 || profile === 200 || profile === 201
    || profile === "0" || profile === "200" || profile === "201"
  const status = String(runtime?.status || "").toLowerCase()
  const geometryReady = runtime?.geometryResolved === true
  const driverReady = Boolean(String(runtime?.shapeDriverUrl || "").trim())
  // A custom head with a different UV layout is still display-safe when its
  // own geometry and ShapeAnime driver are valid; only the shared face atlas
  // is disabled by the renderer in that case.
  const safeUvFallback = runtime?.reason === "head-face-uv-contract-mismatch"
  return (status === "ready" || (status === "invalid" && safeUvFallback))
    && geometryReady
    && driverReady
    && validOfficialProfile
}

/** Main face renderer names emitted by vanilla KKS and common headmods. */
export function isRuntimeHeadMainFaceItem(item: Pick<RuntimeManifestItem, "source_kind" | "id" | "headMain">): boolean {
  if (item.source_kind !== "head") return false
  const name = String(item.id || "").split(":").pop()?.trim().toLowerCase() || ""
  return item.headMain === true
    || name === "cf_o_face"
    || /^face_[a-z0-9][a-z0-9_.-]*$/.test(name)
    || /^[a-z0-9][a-z0-9_.-]*_face$/.test(name)
}

export function mergeRuntimeNativeShellItems(
  items: readonly RuntimeManifestItem[],
  sharedItems: readonly RuntimeManifestItem[],
) {
  const hasBodyShell = items.some(item => item.source_kind === "neutral_body")
  const hasHeadShell = items.some(isRuntimeHeadMainFaceItem)
  if (hasBodyShell && hasHeadShell) {
    return { items: [...items], fallbackBody: false, fallbackHead: false }
  }
  const fallbackBody = hasBodyShell ? [] : sharedItems.filter(item => item.source_kind === "neutral_body")
  const fallbackHead = hasHeadShell ? [] : sharedItems.filter(item => item.source_kind === "head")
  return {
    items: [
      ...fallbackBody,
      ...items.filter(item => item.source_kind === "neutral_body"),
      ...fallbackHead,
      // An incomplete selected head is not composable with the default head:
      // both carry the same semantic face layers and private hierarchy.
      ...items.filter(item => item.source_kind !== "neutral_body" && (hasHeadShell || item.source_kind !== "head")),
    ],
    fallbackBody: !hasBodyShell,
    fallbackHead: !hasHeadShell,
  }
}

type RuntimeBoneTarget = {
  name: string
  path: string
}

// This is the original ChaFileBody.shapeValueBody order.  Every card uses
// this same 44-channel contract; card identity and resource IDs must never
// participate in body deformation.
// Exact `ill_Default_Female` body preset loaded by KKS when Character Maker
// creates a new female. Keeping all 44 values together avoids the distorted
// hand-tuned hybrid that mixed a few arbitrary overrides with 0.5 fallbacks.
// An imported card still replaces every channel with its own native payload.

// Same atomic `ill_Default_Female` preset as the body vector above. KKS does
// not initialize face shape to 52 copies of 0.5, so mixing an official body
// with an invented neutral face produces another visibly distorted template.
const KKS_DEFAULT_FEMALE_FACE_VALUES = [
  0.2280000001, 0.1239999980, 0, 0.7580645084, 0.4359999895, 0,
  0.1389999986, 0.2520000041, 0.0610000007, 0.2860000134,
  0.1129999980, 0.2700000107, 0, 0, 0.2619999945, 0.2310000062,
  0.3849999905, 0.4149999917, 0.2460000068, 0.7548390031, 0,
  0.6612908244, 0.5258065462, 0.7064514160, 0.6109995246,
  0.9516128898, 0.7406449318, 0.7689996362, 0.8818383217,
  0.6205163002, 0.5178712010, 0.2651297152, 0, 0.4193548262,
  0.3843544424, 0.4720000029, 0.4039033055, 0.4690966904,
  0.4359999895, 0.3975161910, 0.6470000148, 0.8483867049,
  0.4161293209, 0.5322580934, 0.0580646135, 0.2806455493, 1,
  0.5, 0.5, 0.5, 0, 0,
] as const
const KKS_DEFAULT_FEMALE_HEAD_ID = 0

type KksHeadShapeDriverCacheEntry = {
  promise: Promise<KksHeadShapeDriverData>
  signal: AbortSignal
  settled: boolean
}
const kksHeadShapeDriverPromises = new Map<string, KksHeadShapeDriverCacheEntry>()
const KKS_HEAD_SHAPE_DRIVER_CACHE_LIMIT = 4
const KKS_HEAD_SHAPE_DRIVER_MAX_BYTES = 2 * 1024 * 1024
const KKS_HEAD_SHAPE_DRIVER_MAX_CATEGORIES = 512
const KKS_HEAD_SHAPE_DRIVER_MAX_ANIMATION_CHANNELS = 512
const KKS_HEAD_SHAPE_DRIVER_MAX_KEYS_PER_CHANNEL = 2048
const KKS_HEAD_SHAPE_DRIVER_MAX_TOTAL_KEYS = 131072
type RuntimeHierarchyCacheEntry = {
  promise: Promise<RuntimeHierarchyPayload>
  signal: AbortSignal
  settled: boolean
}
const runtimeHierarchyPayloadPromises = new Map<string, RuntimeHierarchyCacheEntry>()
const KKS_RUNTIME_HIERARCHY_CACHE_LIMIT = 24
const KKS_RUNTIME_HIERARCHY_MAX_BYTES = 4 * 1024 * 1024
const KKS_RUNTIME_OBJ_MAX_BYTES = 32 * 1024 * 1024
const KKS_RUNTIME_SKIN_MAX_BYTES = 32 * 1024 * 1024
const KKS_RUNTIME_CPU_TEXTURE_MAX_DIMENSION = 2048
const KKS_RUNTIME_CPU_TEXTURE_MAX_PIXELS = 2 * 1024 * 1024
const KKS_TRANSPARENT_FACE_TEXTURE_SENTINEL = "kks:transparent"
const KKS_CANONICAL_HEAD_HIERARCHY_URL = "/api/koikatsu-cards/shared-base/neutral-head/hierarchy.json"
let kksRendererInstanceSerial = 0
let sharedNativeShellPayloadPromise: Promise<RuntimeManifestPayload> | undefined
const KKS_RUNTIME_TEXTURE_CONCURRENCY = 2
// Mesh JSON parsing and BufferGeometry construction happen on the main
// thread. Decode one semantic slot at a time: parallel fetch/JSON.parse made
// complex cards briefly retain several expanded number[] payloads and spike
// the page's CPU without improving steady-state rendering.
const KKS_RUNTIME_GEOMETRY_CONCURRENCY = 1
let activeRuntimeTextureLoads = 0
type RuntimeTextureQueueRequest = {
  load: () => Promise<THREE.Texture>
  isCurrent?: () => boolean
  signal?: AbortSignal
  resolve: (texture: THREE.Texture) => void
  reject: (error: unknown) => void
  started: boolean
  cancelled: boolean
  onAbort?: () => void
}
const runtimeTextureQueue: RuntimeTextureQueueRequest[] = []

function runtimeTextureAbortError() {
  return new DOMException("runtime texture request aborted", "AbortError")
}

function runtimeTextureRequestIsStale(request: RuntimeTextureQueueRequest) {
  return request.cancelled
    || Boolean(request.signal?.aborted)
    || Boolean(request.isCurrent && !request.isCurrent())
}

function detachRuntimeTextureAbortListener(request: RuntimeTextureQueueRequest) {
  if (request.signal && request.onAbort) request.signal.removeEventListener("abort", request.onAbort)
  request.onAbort = undefined
}

function pumpRuntimeTextureQueue() {
  while (activeRuntimeTextureLoads < KKS_RUNTIME_TEXTURE_CONCURRENCY && runtimeTextureQueue.length) {
    const request = runtimeTextureQueue.shift()!
    if (runtimeTextureRequestIsStale(request)) {
      detachRuntimeTextureAbortListener(request)
      request.reject(runtimeTextureAbortError())
      continue
    }
    request.started = true
    activeRuntimeTextureLoads += 1
    void request.load().then(texture => {
      // An active generation can be cancelled while image decoding is in
      // flight. Never hand its eventual bitmap to the next card.
      if (runtimeTextureRequestIsStale(request)) {
        texture.dispose()
        request.reject(runtimeTextureAbortError())
        return
      }
      request.resolve(texture)
    }, request.reject).finally(() => {
      detachRuntimeTextureAbortListener(request)
      activeRuntimeTextureLoads -= 1
      pumpRuntimeTextureQueue()
    })
  }
}

function loadRuntimeTextureBounded(
  load: () => Promise<THREE.Texture>,
  isCurrent?: () => boolean,
  signal?: AbortSignal,
) {
  return new Promise<THREE.Texture>((resolve, reject) => {
    const request: RuntimeTextureQueueRequest = {
      load,
      isCurrent,
      signal,
      resolve,
      reject,
      started: false,
      cancelled: false,
    }
    request.onAbort = () => {
      request.cancelled = true
      // Remove work that has not started instead of letting rapid card
      // changes accumulate dead entries behind the four active slots.
      if (!request.started) {
        const index = runtimeTextureQueue.indexOf(request)
        if (index >= 0) runtimeTextureQueue.splice(index, 1)
        detachRuntimeTextureAbortListener(request)
      }
      reject(runtimeTextureAbortError())
      pumpRuntimeTextureQueue()
    }
    runtimeTextureQueue.push(request)
    if (signal?.aborted) request.onAbort()
    else signal?.addEventListener("abort", request.onAbort, { once: true })
    pumpRuntimeTextureQueue()
  })
}

function loadRuntimeImageBitmap(blob: Blob, signal?: AbortSignal) {
  if (signal?.aborted) return Promise.reject<ImageBitmap>(runtimeTextureAbortError())
  const decode = createImageBitmap(blob)
  return new Promise<ImageBitmap>((resolve, reject) => {
    let aborted = false
    const onAbort = () => {
      // createImageBitmap has no cancellation primitive. Keep this request in
      // the global concurrency count until the real decoder finishes; freeing
      // its slot immediately would let rapid card changes stack unbounded
      // background decodes and spike CPU above the advertised limit.
      aborted = true
    }
    signal?.addEventListener("abort", onAbort, { once: true })
    decode.then(bitmap => {
      signal?.removeEventListener("abort", onAbort)
      if (aborted || signal?.aborted) {
        bitmap.close()
        reject(runtimeTextureAbortError())
        return
      }
      resolve(bitmap)
    }, error => {
      signal?.removeEventListener("abort", onAbort)
      reject(aborted || signal?.aborted ? runtimeTextureAbortError() : error)
    })
    if (signal?.aborted) onAbort()
  })
}

function loadRuntimeHtmlImage(blob: Blob, signal?: AbortSignal) {
  if (signal?.aborted) return Promise.reject<HTMLImageElement>(runtimeTextureAbortError())
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const objectUrl = URL.createObjectURL(blob)
    const image = new Image()
    let settled = false
    const cleanup = () => {
      signal?.removeEventListener("abort", onAbort)
      image.onload = null
      image.onerror = null
      URL.revokeObjectURL(objectUrl)
    }
    const onAbort = () => {
      if (settled) return
      settled = true
      image.src = ""
      cleanup()
      reject(runtimeTextureAbortError())
    }
    image.decoding = "async"
    image.onload = () => {
      if (settled) return
      settled = true
      cleanup()
      resolve(image)
    }
    image.onerror = () => {
      if (settled) return
      settled = true
      cleanup()
      reject(new Error("runtime texture image decode failed"))
    }
    signal?.addEventListener("abort", onAbort, { once: true })
    if (signal?.aborted) onAbort()
    else image.src = objectUrl
  })
}

async function loadAbortableRuntimeTexture(url: string, signal?: AbortSignal) {
  if (signal?.aborted) throw runtimeTextureAbortError()
  const resolved = new URL(url, window.location.href)
  if (resolved.origin !== window.location.origin) throw new Error("runtime texture must be same-origin")
  const response = await fetch(resolved, { signal, credentials: "same-origin" })
  if (!response.ok) throw new Error(`runtime texture HTTP ${response.status}`)
  const blob = await response.blob()
  if (signal?.aborted) throw runtimeTextureAbortError()

  if (typeof createImageBitmap === "function") {
    try {
      const bitmap = await loadRuntimeImageBitmap(blob, signal)
      if (signal?.aborted) {
        bitmap.close()
        throw runtimeTextureAbortError()
      }
      const texture = new THREE.Texture(bitmap)
      // Three.js deliberately does not close ImageBitmap resources from
      // Texture.dispose(). Make ownership explicit so card teardown and stale
      // generation disposal release both CPU-side pixels and GPU state.
      let bitmapClosed = false
      texture.addEventListener("dispose", () => {
        if (bitmapClosed) return
        bitmapClosed = true
        bitmap.close()
      })
      texture.needsUpdate = true
      return texture
    } catch (error) {
      if (signal?.aborted) throw error
      // A few browser/image-format combinations cannot create ImageBitmap.
      // Decode the already-fetched blob through an abortable object URL rather
      // than falling back to an uninterruptible TextureLoader request.
    }
  }

  const image = await loadRuntimeHtmlImage(blob, signal)
  if (signal?.aborted) throw runtimeTextureAbortError()
  const texture = new THREE.Texture(image)
  texture.needsUpdate = true
  return texture
}

function loadSharedNativeShellPayload(): Promise<RuntimeManifestPayload> {
  if (!sharedNativeShellPayloadPromise) {
    sharedNativeShellPayloadPromise = Promise.all([
      fetch("/api/koikatsu-cards/shared-base/neutral-body/manifest.json?v=runtime-manifest-3"),
      fetch("/api/koikatsu-cards/shared-base/neutral-head/manifest.json?v=runtime-manifest-3"),
    ]).then(async ([bodyResponse, headResponse]) => {
      if (!bodyResponse.ok || !headResponse.ok) throw new Error("shared native shell unavailable")
      const [bodyPayload, headPayload] = await Promise.all([
        bodyResponse.json() as Promise<RuntimeManifestPayload>,
        headResponse.json() as Promise<RuntimeManifestPayload>,
      ])
      const sharedUrl = (root: string, value?: string) => value
        ? value.startsWith("/") ? value : `${root}${value}`
        : undefined
      return {
        items: [
          ...(bodyPayload.items || []).filter(item => Boolean(item.runtimeSkin)).map(item => ({
            ...item,
            id: `__neutral_body__:${item.id}`,
            runtimeSkin: sharedUrl("/api/koikatsu-cards/shared-base/neutral-body/", item.runtimeSkin),
            runtimeHierarchy: sharedUrl("/api/koikatsu-cards/shared-base/neutral-body/", item.runtimeHierarchy),
            source_kind: "neutral_body",
          })),
          ...(headPayload.items || []).filter(item => Boolean(item.runtimeSkin)).map(item => ({
            ...item,
            id: `__neutral_head__:${item.id}`,
            runtimeSkin: sharedUrl("/api/koikatsu-cards/shared-base/neutral-head/", item.runtimeSkin),
            runtimeHierarchy: sharedUrl("/api/koikatsu-cards/shared-base/neutral-head/", item.runtimeHierarchy),
            source_kind: "head",
            attachmentBone: "cf_s_head",
          })),
        ],
      }
    }).catch(error => {
      sharedNativeShellPayloadPromise = undefined
      throw error
    })
  }
  return sharedNativeShellPayloadPromise
}

function loadRuntimeHierarchyPayload(url: string, signal: AbortSignal) {
  if (signal.aborted) return Promise.reject(runtimeTextureAbortError())
  const resolvedUrl = new URL(`${url}${url.includes("?") ? "&" : "?"}v=runtime-hierarchy-1`, window.location.origin)
  if (resolvedUrl.origin !== window.location.origin) return Promise.reject(new Error("runtime hierarchy must be same-origin"))
  const key = resolvedUrl.href
  const cached = runtimeHierarchyPayloadPromises.get(key)
  if (cached && (cached.settled || (cached.signal === signal && !cached.signal.aborted))) {
    runtimeHierarchyPayloadPromises.delete(key)
    runtimeHierarchyPayloadPromises.set(key, cached)
    return cached.promise
  }
  const entry: RuntimeHierarchyCacheEntry = {
    signal,
    settled: false,
    promise: Promise.resolve({}),
  }
  entry.promise = fetch(resolvedUrl, { signal, credentials: "same-origin" }).then(async response => {
    if (!response.ok) throw new Error(`runtime hierarchy HTTP ${response.status}`)
    const declaredBytes = Number(response.headers.get("content-length") || 0)
    if (declaredBytes > KKS_RUNTIME_HIERARCHY_MAX_BYTES) throw new Error("runtime hierarchy is too large")
    const bytes = await response.arrayBuffer()
    if (bytes.byteLength > KKS_RUNTIME_HIERARCHY_MAX_BYTES) throw new Error("runtime hierarchy is too large")
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as RuntimeHierarchyPayload
  }).then(payload => {
    entry.settled = true
    return payload
  }).catch(error => {
    if (runtimeHierarchyPayloadPromises.get(key) === entry) runtimeHierarchyPayloadPromises.delete(key)
    throw error
  })
  runtimeHierarchyPayloadPromises.set(key, entry)
  while (runtimeHierarchyPayloadPromises.size > KKS_RUNTIME_HIERARCHY_CACHE_LIMIT) {
    const oldest = runtimeHierarchyPayloadPromises.keys().next().value as string | undefined
    if (!oldest) break
    runtimeHierarchyPayloadPromises.delete(oldest)
  }
  return entry.promise
}

async function loadRuntimeObjAbortable(loader: OBJLoader, url: string, signal: AbortSignal) {
  if (signal.aborted) throw runtimeTextureAbortError()
  const resolvedUrl = new URL(url, window.location.origin)
  if (resolvedUrl.origin !== window.location.origin) throw new Error("runtime OBJ must be same-origin")
  const response = await fetch(resolvedUrl, { signal, credentials: "same-origin" })
  if (!response.ok) throw new Error(`runtime OBJ HTTP ${response.status}`)
  const declaredBytes = Number(response.headers.get("content-length") || 0)
  if (declaredBytes > KKS_RUNTIME_OBJ_MAX_BYTES) throw new Error("runtime OBJ is too large")
  const bytes = await response.arrayBuffer()
  if (bytes.byteLength > KKS_RUNTIME_OBJ_MAX_BYTES) throw new Error("runtime OBJ is too large")
  if (signal.aborted) throw runtimeTextureAbortError()
  return loader.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes))
}

/** Validate the compact transform-only prefab hierarchy before materializing it. */
export function resolveRuntimeHierarchyNodeCount(payload: RuntimeHierarchyPayload): number {
  const names = payload.names || []
  const paths = payload.paths || []
  const parents = payload.parentIndices || []
  const matrices = payload.localMatrices || []
  if (!names.length || names.length > 4096) throw new Error("invalid runtime hierarchy size")
  if (paths.length !== names.length || parents.length !== names.length || matrices.length !== names.length * 16) {
    throw new Error("invalid runtime hierarchy arrays")
  }
  const seenPaths = new Set<string>()
  names.forEach((name, index) => {
    if (typeof name !== "string" || !name.trim()) throw new Error("invalid runtime hierarchy name")
    const path = runtimeBonePathSegments(paths[index])
    if (!path.length || path[path.length - 1] !== canonicalRuntimeBoneSegment(name)) {
      throw new Error("invalid runtime hierarchy path")
    }
    const canonicalPath = path.join("/")
    if (seenPaths.has(canonicalPath)) throw new Error("duplicate runtime hierarchy path")
    seenPaths.add(canonicalPath)
    const parent = Number(parents[index])
    if (!Number.isInteger(parent) || parent < -1 || parent >= index) throw new Error("invalid runtime hierarchy parent")
    const matrix = matrices.slice(index * 16, index * 16 + 16)
    if (!matrix.every(Number.isFinite)) throw new Error("invalid runtime hierarchy matrix")
  })
  return names.length
}
/** Bundled profiles are exact only for the three vanilla female heads. */
export function resolveKksHeadShapeDriverId(headId?: number | null): number | null {
  return headId === 0 || headId === 200 || headId === 201 ? headId : null
}

function isFiniteNumberTriple(value: unknown): value is [number, number, number] {
  return Array.isArray(value) && value.length === 3 && value.every(item => typeof item === "number" && Number.isFinite(item))
}

function isBooleanTriple(value: unknown): value is [boolean, boolean, boolean] {
  return Array.isArray(value) && value.length === 3 && value.every(item => typeof item === "boolean")
}

/**
 * Treat a downloaded ShapeAnime payload as executable deformation data. Keep
 * its shape bounded and reject partial/custom payloads before any head mesh is
 * committed to the scene.
 */
export function validateKksHeadShapeDriver(
  value: unknown,
  expectedProfile?: string | number,
): KksHeadShapeDriverData {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("KKS head shape driver must be an object")
  const candidate = value as Record<string, unknown>
  if (candidate.version !== 1) throw new Error("unsupported KKS head shape driver version")
  if (candidate.sliderCount !== 52) throw new Error("KKS head shape driver must expose 52 sliders")
  if (typeof candidate.headId !== "number" || !Number.isInteger(candidate.headId)) throw new Error("KKS head shape driver is missing its head id")
  const declaredProfile = candidate.profile ?? candidate.headId
  if (expectedProfile !== undefined && String(declaredProfile) !== String(expectedProfile)) {
    throw new Error("KKS head shape driver profile mismatch")
  }
  const categories = candidate.categories
  if (!Array.isArray(categories) || categories.length < 1 || categories.length > KKS_HEAD_SHAPE_DRIVER_MAX_CATEGORIES) {
    throw new Error("invalid KKS head shape categories")
  }
  if (!candidate.animation || typeof candidate.animation !== "object" || Array.isArray(candidate.animation)) {
    throw new Error("invalid KKS head shape animation")
  }
  const animation = candidate.animation as Record<string, unknown>
  const animationEntries = Object.entries(animation)
  if (animationEntries.length < 1 || animationEntries.length > KKS_HEAD_SHAPE_DRIVER_MAX_ANIMATION_CHANNELS) {
    throw new Error("invalid KKS head shape animation channel count")
  }
  let totalKeys = 0
  for (const [name, rawFrames] of animationEntries) {
    if (!name || name.length > 1024 || !Array.isArray(rawFrames) || rawFrames.length < 1 || rawFrames.length > KKS_HEAD_SHAPE_DRIVER_MAX_KEYS_PER_CHANNEL) {
      throw new Error("invalid KKS head shape animation channel")
    }
    totalKeys += rawFrames.length
    if (totalKeys > KKS_HEAD_SHAPE_DRIVER_MAX_TOTAL_KEYS) throw new Error("KKS head shape animation is too large")
    rawFrames.forEach(rawFrame => {
      if (!rawFrame || typeof rawFrame !== "object" || Array.isArray(rawFrame)) throw new Error("invalid KKS head shape keyframe")
      const frame = rawFrame as Record<string, unknown>
      if (typeof frame.key !== "number" || !Number.isInteger(frame.key)
        || !isFiniteNumberTriple(frame.position)
        || !isFiniteNumberTriple(frame.rotation)
        || !isFiniteNumberTriple(frame.scale)) {
        throw new Error("invalid KKS head shape keyframe")
      }
    })
  }
  categories.forEach(rawCategory => {
    if (!rawCategory || typeof rawCategory !== "object" || Array.isArray(rawCategory)) throw new Error("invalid KKS head shape category")
    const category = rawCategory as Record<string, unknown>
    if (typeof category.slider !== "number" || !Number.isInteger(category.slider) || category.slider < 0 || category.slider >= 52
      || typeof category.source !== "string" || !category.source || category.source.length > 1024
      || !isBooleanTriple(category.position)
      || !isBooleanTriple(category.rotation)
      || !isBooleanTriple(category.scale)
      || !Array.isArray(animation[category.source])
      || !(animation[category.source] as unknown[]).length) {
      throw new Error("invalid KKS head shape category")
    }
  })
  return value as KksHeadShapeDriverData
}

function normalizeKksDriverDigest(value?: string) {
  const normalized = String(value || "").trim().toLowerCase().replace(/^sha256:/, "")
  if (!normalized) return ""
  if (!/^[a-f0-9]{64}$/.test(normalized)) throw new Error("invalid KKS head shape driver digest")
  return normalized
}

async function sha256Hex(bytes: ArrayBuffer) {
  const digest = await crypto.subtle.digest("SHA-256", bytes)
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("")
}

function loadKksHeadShapeDriver(
  url: string,
  expectedProfile: string | number | undefined,
  signal: AbortSignal,
  digest?: string,
) {
  if (signal.aborted) return Promise.reject(runtimeTextureAbortError())
  const resolvedUrl = new URL(url, window.location.origin)
  if (resolvedUrl.origin !== window.location.origin || !/^https?:$/.test(resolvedUrl.protocol)) {
    return Promise.reject(new Error("KKS head shape driver URL must be same-origin"))
  }
  let expectedDigest = ""
  try {
    expectedDigest = normalizeKksDriverDigest(digest)
  } catch (error) {
    return Promise.reject(error)
  }
  const key = `${resolvedUrl.href}\u0000${expectedDigest}`
  const cached = kksHeadShapeDriverPromises.get(key)
  if (cached && (cached.settled || (cached.signal === signal && !cached.signal.aborted))) {
    kksHeadShapeDriverPromises.delete(key)
    kksHeadShapeDriverPromises.set(key, cached)
    return cached.promise.then(driver => {
      const declaredProfile = driver.profile ?? driver.headId
      if (expectedProfile !== undefined && String(declaredProfile) !== String(expectedProfile)) {
        throw new Error("KKS head shape driver profile mismatch")
      }
      return driver
    })
  }
  const entry: KksHeadShapeDriverCacheEntry = {
    signal,
    settled: false,
    promise: Promise.resolve(undefined as unknown as KksHeadShapeDriverData),
  }
  entry.promise = fetch(resolvedUrl, { signal, cache: "force-cache" }).then(async response => {
    if (!response.ok) throw new Error(`KKS head shape driver HTTP ${response.status}`)
    const declaredBytes = Number(response.headers.get("content-length") || 0)
    if (declaredBytes > KKS_HEAD_SHAPE_DRIVER_MAX_BYTES) throw new Error("KKS head shape driver is too large")
    const bytes = await response.arrayBuffer()
    if (bytes.byteLength > KKS_HEAD_SHAPE_DRIVER_MAX_BYTES) throw new Error("KKS head shape driver is too large")
    if (expectedDigest && await sha256Hex(bytes) !== expectedDigest) throw new Error("KKS head shape driver digest mismatch")
    const decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes)
    return validateKksHeadShapeDriver(JSON.parse(decoded) as unknown, expectedProfile)
  }).then(driver => {
    entry.settled = true
    return driver
  }).catch(error => {
    if (kksHeadShapeDriverPromises.get(key) === entry) kksHeadShapeDriverPromises.delete(key)
    throw error
  })
  kksHeadShapeDriverPromises.set(key, entry)
  while (kksHeadShapeDriverPromises.size > KKS_HEAD_SHAPE_DRIVER_CACHE_LIMIT) {
    const oldest = kksHeadShapeDriverPromises.keys().next().value as string | undefined
    if (!oldest) break
    kksHeadShapeDriverPromises.delete(oldest)
  }
  return entry.promise
}

const canonicalRuntimeBoneSegment = (value: string) => value.trim().toLowerCase().replace(/elboback|elbowback/g, "elbow")

/** Mirror KKABMX BoneController.FindBone: BodyTop first, then accessory slots. */
export function resolveKksAbmxBone<T extends { name: string }>(
  boneName: string,
  location: number,
  bodyTopBones: readonly T[],
  privateBonesBySlot: ReadonlyMap<string, readonly T[]>,
): T | undefined {
  const wanted = canonicalRuntimeBoneSegment(boneName)
  if (!wanted) return undefined
  const firstMatch = (bones: readonly T[]) => bones.find(bone => canonicalRuntimeBoneSegment(bone.name) === wanted)
  const bodyTop = [
    ...bodyTopBones,
    ...[...privateBonesBySlot.entries()]
      .filter(([slot]) => !slot.startsWith("accessory:"))
      .flatMap(([, bones]) => bones),
  ]
  if (location === 1) return firstMatch(bodyTop)

  const accessorySlots = [...privateBonesBySlot.entries()]
    .filter(([slot]) => slot.startsWith("accessory:"))
    .map(([slot, bones]) => {
      const match = slot.match(/(?:^|\.)accessory(\d+)(?:\.|$)/i)
      return { index: match ? Number(match[1]) : Number.POSITIVE_INFINITY, bones }
    })
    .sort((left, right) => left.index - right.index)

  if (Number.isInteger(location) && location >= 10) {
    return firstMatch(accessorySlots.find(slot => slot.index === location - 10)?.bones || [])
  }
  return firstMatch(bodyTop) || accessorySlots.map(slot => firstMatch(slot.bones)).find(Boolean)
}

const runtimeBonePathSegments = (value?: string) => String(value || "")
  .replace(/\\/g, "/")
  .split("/")
  .map(canonicalRuntimeBoneSegment)
  .filter(Boolean)

const commonBonePathSuffixLength = (left: string[], right: string[]) => {
  let count = 0
  while (count < left.length && count < right.length && left[left.length - 1 - count] === right[right.length - 1 - count]) count += 1
  return count
}

/**
 * Map Unity renderer bones onto the shared Three.js skeleton without ever
 * choosing the first same-name bone. Full/suffix hierarchy paths are
 * authoritative; a name-only fallback is accepted only when it is unique.
 */
export function resolveRuntimeBoneRemap(
  sourceBoneNames: string[],
  sourceBonePaths: string[],
  targets: RuntimeBoneTarget[],
): Array<number | undefined> {
  const targetPaths = targets.map(target => runtimeBonePathSegments(target.path))
  const targetNames = targets.map(target => canonicalRuntimeBoneSegment(target.name))
  return sourceBoneNames.map((sourceName, sourceIndex) => {
    const wantedName = canonicalRuntimeBoneSegment(sourceName)
    const sourcePath = runtimeBonePathSegments(sourceBonePaths[sourceIndex])
    if (sourcePath.length) {
      const scored = targetPaths
        .map((targetPath, targetIndex) => ({ targetIndex, score: commonBonePathSuffixLength(sourcePath, targetPath) }))
        // A path is authoritative only when it identifies the same leaf bone.
        // Silently accepting a stale/malformed path whose leaf disagrees with
        // boneNames can bind an accessory to an entirely unrelated branch.
        .filter(candidate => candidate.score > 0 && (!wantedName || targetNames[candidate.targetIndex] === wantedName))
      const bestScore = scored.reduce((maximum, candidate) => Math.max(maximum, candidate.score), 0)
      const best = scored.filter(candidate => candidate.score === bestScore)
      // A multi-segment suffix identifies the branch even when Unity and PMX
      // use different prefab roots. A one-segment path is only a bone name and
      // therefore must pass the same uniqueness rule as the fallback below.
      if (bestScore >= 2 && best.length === 1) return best[0].targetIndex
      if (bestScore === sourcePath.length && best.length === 1) return best[0].targetIndex
    }
    if (!wantedName) return undefined
    const sameName = targetNames
      .map((name, targetIndex) => ({ name, targetIndex }))
      .filter(candidate => candidate.name === wantedName)
    return sameName.length === 1 ? sameName[0].targetIndex : undefined
  })
}

const runtimeBindPoseEqual = (left: number[], right: number[], epsilon = 1e-6) =>
  left.length === 16 && right.length === 16 && left.every((value, index) => Math.abs(value - right[index]) <= epsilon)

/**
 * Build renderer-local inverse-bind assignments for its remapped bones.
 *
 * Three.js Skeleton instances own their boneInverses array even when several
 * garments share the same live Bone objects. Consequently each renderer must
 * keep the bind poses authored for that renderer; substituting the carrier
 * body's inverse binds changes the coordinate frame and causes stretched or
 * displaced clothes. A many-to-one remap is accepted only when the duplicate
 * source entries carry the same bind pose.
 */
export function resolveRuntimeBindPoseAssignments(
  remap: Array<number | undefined>,
  sourceBindPoses: number[],
  targetCount: number,
): Array<number[] | undefined> {
  if (sourceBindPoses.length !== remap.length * 16) throw new Error("invalid runtime bind pose count")
  const assignments: Array<number[] | undefined> = Array(targetCount).fill(undefined)
  remap.forEach((mapped, sourceIndex) => {
    if (mapped === undefined) return
    if (!Number.isInteger(mapped) || mapped < 0 || mapped >= targetCount) throw new Error("invalid runtime bind target")
    const values = sourceBindPoses.slice(sourceIndex * 16, sourceIndex * 16 + 16)
    if (values.length !== 16 || !values.every(Number.isFinite)) throw new Error("invalid runtime bind pose")
    const previous = assignments[mapped]
    if (previous && !runtimeBindPoseEqual(previous, values)) throw new Error("conflicting runtime bind poses")
    assignments[mapped] = values
  })
  return assignments
}

export type RuntimeCompactSkinBinding = {
  /** Indices into the canonical/shared target-bone table. */
  targetBoneIndices: number[]
  /** Renderer source bone -> compact THREE.Skeleton bone index. */
  sourceToSkeleton: Array<number | undefined>
  /** Vertex skin indices rewritten into the compact renderer-local table. */
  skinIndices: Uint16Array
}

/**
 * Keep the full KKS hierarchy as a shared Transform/socket graph without
 * copying every unweighted node into every renderer's GPU bone texture.
 */
export function resolveRuntimeCompactSkinBinding(
  remap: Array<number | undefined>,
  sourceSkinIndices: number[],
  skinBoneCount: number,
): RuntimeCompactSkinBinding {
  if (!Number.isInteger(skinBoneCount) || skinBoneCount < 1 || skinBoneCount > remap.length) {
    throw new Error("invalid compact skin bone count")
  }
  const targetBoneIndices: number[] = []
  const compactByTarget = new Map<number, number>()
  const sourceToSkeleton: Array<number | undefined> = Array(skinBoneCount).fill(undefined)
  for (let sourceIndex = 0; sourceIndex < skinBoneCount; sourceIndex += 1) {
    const targetIndex = remap[sourceIndex]
    if (targetIndex === undefined) throw new Error("unmapped compact skin bone")
    if (!Number.isInteger(targetIndex) || targetIndex < 0) throw new Error("invalid compact skin target")
    let compactIndex = compactByTarget.get(targetIndex)
    if (compactIndex === undefined) {
      compactIndex = targetBoneIndices.length
      if (compactIndex > 65535) throw new Error("compact skin exceeds Uint16 range")
      compactByTarget.set(targetIndex, compactIndex)
      targetBoneIndices.push(targetIndex)
    }
    sourceToSkeleton[sourceIndex] = compactIndex
  }
  const skinIndices = new Uint16Array(sourceSkinIndices.length)
  sourceSkinIndices.forEach((rawSourceIndex, index) => {
    const sourceIndex = Number(rawSourceIndex)
    if (!Number.isInteger(sourceIndex) || sourceIndex < 0 || sourceIndex >= skinBoneCount) {
      throw new Error("invalid compact vertex skin index")
    }
    const compactIndex = sourceToSkeleton[sourceIndex]
    if (compactIndex === undefined) throw new Error("unmapped compact vertex skin index")
    skinIndices[index] = compactIndex
  })
  return { targetBoneIndices, sourceToSkeleton, skinIndices }
}

export function resolveRuntimeMaterialTextureOverride(
  overrides: RuntimeManifestItem["materialTextureOverrides"],
  materialName: string,
  property = "MainTex",
) {
  const wantedMaterial = String(materialName || "").trim().toLowerCase()
  const wantedProperty = String(property || "").replace(/^_/, "").trim().toLowerCase()
  return (overrides || []).find(override => (
    String(override.material || "").trim().toLowerCase() === wantedMaterial
    && String(override.property || "").replace(/^_/, "").trim().toLowerCase() === wantedProperty
  ))
}

export function resolveRuntimeMaterialPropertyOverride(
  overrides: RuntimeManifestItem["materialPropertyOverrides"],
  materialName: string,
  propertyNames: string | string[],
  kind?: "color" | "float",
) {
  const wantedMaterial = String(materialName || "").replace(/\(Instance\)/gi, "").trim().toLowerCase()
  const wantedProperties = new Set((Array.isArray(propertyNames) ? propertyNames : [propertyNames])
    .map(value => String(value || "").replace(/^_/, "").trim().toLowerCase()))
  let matched: NonNullable<RuntimeManifestItem["materialPropertyOverrides"]>[number] | undefined
  for (const override of overrides || []) {
    if (String(override.material || "").replace(/\(Instance\)/gi, "").trim().toLowerCase() !== wantedMaterial) continue
    if (!wantedProperties.has(String(override.property || "").replace(/^_/, "").trim().toLowerCase())) continue
    if (kind && override.kind !== kind) continue
    matched = override
  }
  return matched
}

export function resolveRuntimeMaterialShaderOverride(
  overrides: RuntimeManifestItem["materialShaderOverrides"],
  materialName: string,
) {
  const wantedMaterial = String(materialName || "").replace(/\(Instance\)/gi, "").trim().toLowerCase()
  let matched: NonNullable<RuntimeManifestItem["materialShaderOverrides"]>[number] | undefined
  for (const override of overrides || []) {
    if (String(override.material || "").replace(/\(Instance\)/gi, "").trim().toLowerCase() !== wantedMaterial) continue
    matched = override
  }
  return matched
}

export function resolveRuntimeMaterialEditorRenderOrder(
  override?: NonNullable<RuntimeManifestItem["materialShaderOverrides"]>[number],
) {
  if (!override) return 0
  const queue = Number(override.render_queue)
  if (override.render_queue !== null && override.render_queue !== undefined && Number.isFinite(queue)) {
    return THREE.MathUtils.clamp(Math.round((queue - 2000) / 10) + 5, 0, 100)
  }
  const alphaMode = resolveRuntimeAlphaMode({ shaderName: String(override.shader || "") })
  return alphaMode === "blend" ? 20 : alphaMode === "cutout" ? 10 : 5
}

/**
 * Some KKS hair meshes serialize unused entries in Renderer.m_Bones while the
 * mesh contains bind poses only for the actually weighted prefix. Accept that
 * native Unity shape without accepting a missing bind pose for a used vertex.
 */
export function resolveRuntimeSkinBoneCount(
  declaredCount: number,
  boneNameCount: number,
  skinIndices: number[],
  skinWeights: number[],
  bindPoses: number[],
): number {
  const bindPoseCount = bindPoses.length / 16
  if (!Number.isInteger(declaredCount) || declaredCount < 1 || declaredCount > boneNameCount) throw new Error("invalid skin bone count")
  if (!Number.isInteger(bindPoseCount) || bindPoseCount < 1) throw new Error("invalid runtime bind pose count")
  let highestWeightedIndex = -1
  skinIndices.forEach((value, index) => {
    if (Number(skinWeights[index]) > 0) highestWeightedIndex = Math.max(highestWeightedIndex, Number(value))
  })
  const resolvedCount = Math.min(declaredCount, bindPoseCount)
  if (highestWeightedIndex >= resolvedCount) throw new Error("skin index references missing bind pose")
  return resolvedCount
}

export function collectRequiredPrivateBoneIndices(
  remap: Array<number | undefined>,
  sourceParents: number[],
  skinBoneCount: number,
): Set<number> {
  const required = new Set<number>()
  for (let skinIndex = 0; skinIndex < skinBoneCount; skinIndex += 1) {
    if (remap[skinIndex] !== undefined) continue
    let current = skinIndex
    while (current >= 0 && current < remap.length && remap[current] === undefined && !required.has(current)) {
      required.add(current)
      const parent = Number(sourceParents[current])
      if (!Number.isInteger(parent) || parent < 0) break
      current = parent
    }
  }
  return required
}

function rgbaFromUnknown(value: unknown): [number, number, number, number] | undefined {
  if (!Array.isArray(value) || value.length < 3) return undefined
  const rgba = [Number(value[0]), Number(value[1]), Number(value[2]), Number(value[3] ?? 1)]
  if (!rgba.every(Number.isFinite)) return undefined
  return rgba.map(component => THREE.MathUtils.clamp(component, 0, 1)) as [number, number, number, number]
}

/** Select a native card tint for one semantic KKS face/body material. */
export function resolveKksFaceMaterialTint(name: string, appearance?: KoikatsuAppearance): [number, number, number, number] | undefined {
  if (!appearance) return undefined
  const materialName = name.trim()
  // The offline cf_m_face_create pass has already baked both native skin
  // colours into the face atlas. Tinting that result again darkens every
  // imported head; only the shared body shell still needs the card tint.
  if (/^cf_m_face_00$/i.test(materialName)) return [1, 1, 1, 1]
  if (/^(?:cf_m_body|bonelyfans)$/i.test(materialName)) return rgbaFromUnknown(appearance.skin_main_color)
  if (/cf_m_mayuge/i.test(materialName)) return rgbaFromUnknown(appearance.eyebrow_color)
  if (/cf_m_eyeline/i.test(materialName)) return rgbaFromUnknown(appearance.eyeline_color)
  if (/cf_m_sirome/i.test(materialName)) return rgbaFromUnknown(appearance.eye_white_base_color)
  // KKS nose atlases are authored as a black/white alpha mask.  The Unity
  // shader supplies a low-contrast skin shadow, but the portable runtime
  // material otherwise keeps the serialized black base colour and turns the
  // tiny nose cue into a hard vertical stripe on the shared head.  Reuse the
  // card's secondary skin tone and preserve a translucent alpha so the same
  // rule works for every head profile instead of tuning one character.
  if (/cf_m_noseline/i.test(materialName)) {
    const shadow = rgbaFromUnknown(appearance.skin_sub_color || appearance.skin_main_color)
    return shadow
      ? [shadow[0] * .42, shadow[1] * .42, shadow[2] * .42, .65]
      : undefined
  }
  // `cf_m_tang` is the mouth interior, not the lip-line overlay. Giving it
  // the card's opaque lip colour revives the hidden interior sheet as the
  // large grey nose/mouth blotch seen on runtime cards.
  if (/cf_m_(?:tang|lip)/i.test(materialName)) return rgbaFromUnknown(appearance.lip_line_color || appearance.makeup?.lip_color)
  if (/cf_m_hitomi/i.test(materialName)) {
    const eyeIndex = /(?:_R\d*|right)/i.test(materialName) ? 1 : 0
    return rgbaFromUnknown(appearance.pupils?.[eyeIndex]?.base_color || appearance.pupils?.[0]?.base_color)
  }
  return undefined
}

/** Map native head materials to the exact card texture contract. */
export function kksFaceTextureSlotForMaterial(name: string, objectName = ""): string {
  // Unity appends "(Instance)" to cloned runtime materials. Strip only that
  // engine suffix so the same card material resolves identically in exports
  // and in the browser runtime.
  const normalized = name.trim().replace(/\s*\(instance\)\s*$/i, "")
  if (/^cf_m_face_00$/i.test(normalized)) return "face_composite"
  if (/cf_m_mayuge/i.test(normalized)) return "eyebrow"
  if (/cf_m_noseline/i.test(normalized)) return "nose"
  if (/cf_m_sirome/i.test(normalized)) return "eye_white"
  if (/cf_m_eyeline/i.test(normalized)) {
    // KKS drives this auxiliary shadow sheet from a separate texture and UV
    // contract. Reusing the visible line atlas creates a broad white strip.
    if (/kage/i.test(normalized)) return ""
    if (/down/i.test(normalized)) return "eyeline_down"
    if (/up/i.test(normalized)) return "eyeline_up"
    return "eyeline_combined"
  }
  if (/cf_m_hitomi/i.test(normalized)) return /(?:_R\d*|right)/i.test(`${normalized}|${objectName}`) ? "pupil_right" : "pupil_left"
  return ""
}

/** The baked atlas targets raw Unity head UVs; decal sheets keep their authored sheet convention. */
export function kksFaceTextureFlipY(slot: string): boolean {
  return slot === "face_composite"
}

/** Preserve native geometric overlays when a card has no authored decal. */
export function shouldApplyKksFaceTexture(slot: string, url: string): boolean {
  if (!url) return false
  if (url !== "kks:transparent") return true
  // The backend emits this sentinel only for an intentional "none" selection.
  // Apply it to optional overlay geometry so a textureless native sheet cannot
  // become a solid black/white plate. Never erase the main face shell.
  return /^(?:nose|eyeline_(?:up|down|combined)|eyebrow|eye_white)$/.test(slot)
}

/** Hide a native face overlay only after a verified replacement is ready. */
export function shouldHideKksNativeFaceOverlay(slot: string, url: string, replacementReady = false): boolean {
  // A transparent sentinel means that the card did not provide a replacement
  // texture. Hiding cf_O_noseline at that point removes the only available
  // nose geometry. Fail open until a replacement mesh/material has actually
  // been created and validated.
  return replacementReady && slot === "nose" && url === "kks:transparent"
}

export function shouldHideKksRuntimeFaceHelper(materialName: string, url = ""): boolean {
  void url
  // kage is a separate unsupported shadow sheet. A transparent sentinel on
  // ordinary eye-line materials means "keep the native authored geometry",
  // not "erase the line".
  return /cf_m_eyeline_kage/i.test(materialName)
}

export function kksFaceLayerDepthPolicy(slot: string): { depthTest: boolean; depthWrite: boolean } {
  if (slot === "face_composite") return { depthTest: true, depthWrite: true }
  return { depthTest: true, depthWrite: false }
}

export function kksNativeHeadLayerPolicy(materialName: string): {
  renderOrder: number
  depthWrite: boolean
  polygonOffsetFactor: number
} {
  const name = String(materialName || "")
  if (/cf_m_hitomi/i.test(name)) return { renderOrder: 130, depthWrite: false, polygonOffsetFactor: -3 }
  if (/cf_m_sirome/i.test(name)) return { renderOrder: 120, depthWrite: false, polygonOffsetFactor: -2 }
  if (/cf_m_(?:mayuge|noseline|eyeline)/i.test(name)) return { renderOrder: 140, depthWrite: false, polygonOffsetFactor: -4 }
  // Tongue, teeth and canine renderers are physical mouth-interior surfaces,
  // not decals. Pulling them forward made the closed tongue pierce through the
  // chin as a red collar on every native-template card. Let the face shell
  // occlude them in the ordinary depth pass; open-mouth geometry will expose
  // them naturally.
  return { renderOrder: 0, depthWrite: true, polygonOffsetFactor: 0 }
}

/** Native neutral mouth surfaces must remain visible in every rendering path. */
export function kksAuthoredMouthOpacity(materialName: string): number | null {
  return /cf_m_tang|cf_m_tooth|(?:^|[_ .])kuti(?:[_ .]|$)|mouth/i.test(materialName) ? 1 : null
}

/**
 * The generated face composite contains the schema-versioned neutral mouth
 * contour even when the card does not select a LiplineTex.  The native
 * `o_tang` mesh depends on KKS mouth blend shapes that the compact runtime does
 * not export, so retaining its undeformed bind pose makes the tongue protrude
 * below a closed chin.  Only our generated composite is a safe replacement;
 * ordinary face textures and the transparent sentinel are not.
 */
export function hasKksBakedLipReplacement(faceTextures?: Record<string, string>): boolean {
  const composite = String(faceTextures?.face_composite || "")
  return Boolean(
    composite
    && composite !== KKS_TRANSPARENT_FACE_TEXTURE_SENTINEL
    && /\/face-composites\//i.test(composite)
  )
}

/** Never remove the only native nose; only a retained baked lip may replace the mouth sheet. */
export function shouldSuppressKksNativeFaceItem(itemId: string, bakedLipReplacement: boolean): boolean {
  return bakedLipReplacement && /(?:^|:)(?:o_)?tang$/i.test(String(itemId || ""))
}

/** Match face surfaces by KKS material token, never by a loose `eye` substring. */
export function isKksCarrierFaceMaterial(name: string): boolean {
  return /cf_m_(?:face_00|eyeline|mayuge|noseline|namida|hitomi|gageye|tang|sirome|tooth)/i.test(name)
}

/**
 * A neutral manikin keeps only the actual face/body shells. Eye whites,
 * irises, tears and line art are card appearance layers; forcing those
 * overlays opaque is what produced the large white plates in front of the
 * diagnostic model's face.
 */
export function isKksNeutralManikinSurfaceMaterial(name: string): boolean {
  return /^(?:cf_m_face_00|cf_m_body|bonelyfans)$/i.test(name.trim())
}

/** Carrier-only face helpers that do not survive arbitrary card head shapes. */
export function isKksRuntimeCarrierFaceHelper(name: string): boolean {
  // Every helper here is tied to the carrier head's UV/shape contract. The
  // selected native head now supplies its own nose line, sclera and expression
  // layers, so none of these carrier surfaces may leak into runtime cards.
  return /cf_m_(?:noseline|sirome|gageye|namida)/i.test(name)
}

function isKksAlphaFaceMaterial(name: string): boolean {
  return /(?:cf_m_(?:eyeline|mayuge|noseline|namida|hitomi|gageye|tang|sirome|tooth)|(?:^|[_ .])eye(?:line)?(?:[_ .]|$))/i.test(name)
}

/** Resolve card-authored tint by semantic resource kind, never by card/mesh/material name. */
export function runtimeAppearancePrimaryColor(item: Pick<RuntimeManifestItem, "source_kind" | "appearance" | "rendererGroups">): [number, number, number, number] | undefined {
  const appearance = item.appearance || {}
  if (item.source_kind === "hair") return rgbaFromUnknown(appearance.baseColor)
  if (item.source_kind === "accessory") {
    const colors = Array.isArray(appearance.color) ? appearance.color : []
    // ChaControl writes _Color4 to rendAlpha after the normal colour pass and
    // disables that renderer when Color4.a is zero. Renderer PPtr semantics
    // let us reproduce this without relying on mesh or material names.
    const alphaRenderer = (item.rendererGroups || []).some(group => group.component === "accessory" && group.role === "alpha")
    return rgbaFromUnknown(colors[alphaRenderer ? 3 : 0])
  }
  if (item.source_kind === "clothes") {
    const colorInfo = Array.isArray(appearance.colorInfo) ? appearance.colorInfo : []
    const first = colorInfo[0]
    return rgbaFromUnknown(first && typeof first === "object" ? (first as Record<string, unknown>).baseColor : undefined)
  }
  return undefined
}

function orderedClothingColorInfo(item: Pick<RuntimeManifestItem, "source_property" | "appearance">): unknown[] {
  const colorInfo = Array.isArray(item.appearance?.colorInfo) ? item.appearance.colorInfo : []
  return /(?:Jacket|Sailor)SubC$/i.test(String(item.source_property || ""))
    ? [colorInfo[3], colorInfo[1], colorInfo[2]]
    : colorInfo
}

function vec2FromUnknown(value: unknown, fallback: [number, number]): [number, number] {
  const record = value && typeof value === "object" ? value as Record<string, unknown> : undefined
  const raw = Array.isArray(value)
    ? value
    : record
      ? [record.x ?? record.X, record.y ?? record.Y]
      : []
  const x = Number(raw[0])
  const y = Number(raw[1])
  return [Number.isFinite(x) ? x : fallback[0], Number.isFinite(y) ? y : fallback[1]]
}

export type KksPupilUvTransform = {
  repeat: [number, number]
  offset: [number, number]
}

/**
 * Reproduce the neutral-gaze UV transform authored by KKS ChaControl.
 *
 * Assembly-CSharp applies the four card sliders through
 * ChangeSettingEyeScaleWidth/Height and ChangeSettingEyePosX/Y, then
 * EyeLookMaterialControll.Update writes the resulting scale and centred
 * offset to _MainTex (and the two eye-highlight textures). Keeping this as a
 * small pure function lets every card use the native rule without a
 * character-specific correction or a per-frame eye controller.
 */
export function kksPupilUvTransform(
  appearance: Pick<KoikatsuAppearance, "pupil_width" | "pupil_height" | "pupil_x" | "pupil_y"> | undefined,
  rightEye = false,
): KksPupilUvTransform {
  const slider = (value: unknown) => {
    const numeric = Number(value)
    return THREE.MathUtils.clamp(Number.isFinite(numeric) ? numeric : 0.5, 0, 1)
  }
  const widthControl = THREE.MathUtils.lerp(1.8, -0.2, slider(appearance?.pupil_width))
  const heightControl = THREE.MathUtils.lerp(1.8, -0.2, slider(appearance?.pupil_height))
  const repeatX = 1 + widthControl
  const repeatY = 1 + heightControl
  const authoredX = THREE.MathUtils.lerp(0.2, -0.6, slider(appearance?.pupil_x)) * (rightEye ? -1 : 1)
  const authoredY = THREE.MathUtils.lerp(-0.5, 0.5, slider(appearance?.pupil_y))
  // Mathf.Lerp clamps the scale control before it becomes eye-look gain.
  const gainX = THREE.MathUtils.lerp(1, 5, THREE.MathUtils.clamp(widthControl, 0, 1))
  const gainY = THREE.MathUtils.lerp(1, 5, THREE.MathUtils.clamp(heightControl, 0, 1))
  return {
    repeat: [repeatX, repeatY],
    offset: [
      (1 - repeatX) / 2 + authoredX * 0.1 * gainX,
      (1 - repeatY) / 2 - authoredY * 0.1 * gainY,
    ],
  }
}

export type RuntimeAppearancePatternSlot = {
  texture?: string
  patternColor: [number, number, number, number]
  tiling: [number, number]
  offset: [number, number]
  rotate: number
}

/** Card-authored pattern inputs in the exact three slots consumed by create_topN. */
export function runtimeAppearancePatternSlots(item: Pick<RuntimeManifestItem, "source_kind" | "source_property" | "appearance">): RuntimeAppearancePatternSlot[] {
  if (item.source_kind !== "clothes") return []
  return orderedClothingColorInfo(item).slice(0, 3).map(value => {
    const info = value && typeof value === "object" ? value as Record<string, unknown> : {}
    const texture = typeof info.patternTexture === "string" && info.patternTexture ? info.patternTexture : undefined
    const rotate = Number(info.rotate)
    return {
      ...(texture ? { texture } : {}),
      patternColor: rgbaFromUnknown(info.patternColor) || [0.5, 0.5, 0.5, 1],
      tiling: vec2FromUnknown(info.tiling, [1, 1]),
      offset: vec2FromUnknown(info.offset, [0.5, 0.5]),
      rotate: Number.isFinite(rotate) ? rotate : 0.5,
    }
  })
}

/** Resolve all four shader colour slots used by Koikatsu's RGB garment mask. */
export function runtimeAppearanceColors(item: Pick<RuntimeManifestItem, "source_kind" | "source_property" | "appearance">): [number, number, number, number][] {
  const appearance = item.appearance || {}
  if (item.source_kind === "hair") {
    return [appearance.baseColor, appearance.startColor, appearance.endColor]
      .map(rgbaFromUnknown)
      .filter((value): value is [number, number, number, number] => Boolean(value))
  }
  if (item.source_kind === "accessory") {
    return (Array.isArray(appearance.color) ? appearance.color : [])
      .slice(0, 4)
      .map(rgbaFromUnknown)
      .filter((value): value is [number, number, number, number] => Boolean(value))
  }
  if (item.source_kind === "clothes") {
    // KKS ChangeCustomClothes main=false, kind=2 is the composite top SubC
    // pass: shader colour 1 comes from colorInfo[3], then colours 2/3 retain
    // colorInfo[1]/[2]. Keep this property-level rule shared by every card.
    return orderedClothingColorInfo(item)
      .map(value => rgbaFromUnknown(value && typeof value === "object" ? (value as Record<string, unknown>).baseColor : undefined))
      .filter((value): value is [number, number, number, number] => Boolean(value))
  }
  if (item.source_kind === "accessory") {
    const colors = Array.isArray(appearance.color) ? appearance.color : []
    return colors.map(rgbaFromUnknown).filter((value): value is [number, number, number, number] => Boolean(value))
  }
  const primary = runtimeAppearancePrimaryColor(item)
  return primary ? [primary] : []
}

function composeRuntimeColorTexture(
  base: THREE.Texture,
  mask: THREE.Texture,
  colors: [number, number, number, number][],
  onDegraded?: (reason: string) => void,
): THREE.Texture | undefined {
  if (typeof document === "undefined" || colors.length < 2) return undefined
  const baseImage = base.image as CanvasImageSource & { width?: number; height?: number; naturalWidth?: number; naturalHeight?: number }
  const maskImage = mask.image as CanvasImageSource & { width?: number; height?: number; naturalWidth?: number; naturalHeight?: number }
  const width = Number(baseImage.naturalWidth || baseImage.width || 0)
  const height = Number(baseImage.naturalHeight || baseImage.height || 0)
  if (!width || !height) { onDegraded?.("cpu-color-mask-invalid-size"); return undefined }
  const maskWidth = Number(maskImage.naturalWidth || maskImage.width || 0)
  const maskHeight = Number(maskImage.naturalHeight || maskImage.height || 0)
  if (!runtimeColorMaskDimensionsAreSafe(width, height, maskWidth, maskHeight)) {
    onDegraded?.("cpu-color-mask-size-limit")
    return undefined
  }
  const baseCanvas = document.createElement("canvas")
  baseCanvas.width = width
  baseCanvas.height = height
  const maskCanvas = document.createElement("canvas")
  maskCanvas.width = width
  maskCanvas.height = height
  const baseContext = baseCanvas.getContext("2d", { willReadFrequently: true })
  const maskContext = maskCanvas.getContext("2d", { willReadFrequently: true })
  if (!baseContext || !maskContext) { onDegraded?.("cpu-color-mask-context-unavailable"); return undefined }
  try {
    baseContext.drawImage(baseImage, 0, 0, width, height)
    maskContext.drawImage(maskImage, 0, 0, width, height)
    const pixels = baseContext.getImageData(0, 0, width, height)
    const selectors = maskContext.getImageData(0, 0, width, height).data
    const slots = Array.from({ length: 3 }, (_, index) => colors[index] || colors[0] || [1, 1, 1, 1])
    for (let offset = 0; offset < pixels.data.length; offset += 4) {
      const red = selectors[offset] / 255
      const green = selectors[offset + 1] / 255
      const blue = selectors[offset + 2] / 255
      const channelTotal = red + green + blue
      const scale = channelTotal > 1 ? 1 / channelTotal : 1
      const baseWeight = Math.max(0, 1 - channelTotal)
      const weights = [red * scale, green * scale, blue * scale]
      for (let channel = 0; channel < 3; channel += 1) {
        // Character Maker stores the user-facing card colour in the same
        // display-space values shown by the game UI. Canvas pixels and the
        // resulting CanvasTexture are sRGB too. Treating the card value as
        // linear and converting it again made dark blue/black hair light grey.
        // Black in a KKS colour mask means "keep the base texture"; RGB map
        // directly to card colour slots 0..2. The old implementation shifted
        // every slot by one and tinted black/unmasked blouse regions with the
        // first (often very dark) card colour.
        const tint = baseWeight + weights.reduce((sum, weight, slot) => {
          return sum + weight * THREE.MathUtils.clamp(slots[slot][channel], 0, 1)
        }, 0)
        pixels.data[offset + channel] = Math.round(pixels.data[offset + channel] * THREE.MathUtils.clamp(tint, 0, 1))
      }
    }
    baseContext.putImageData(pixels, 0, 0)
    const texture = new THREE.CanvasTexture(baseCanvas)
    texture.flipY = false
    texture.colorSpace = THREE.SRGBColorSpace
    texture.needsUpdate = true
    return texture
  } catch {
    onDegraded?.("cpu-color-mask-compose-failed")
    return undefined
  }
}

/**
 * KKBP does not always emit a baked MT_CT/light atlas. Native KKS hair and
 * some accessories instead retain the shader inputs: DM supplies detail and
 * cutout coverage, CM selects the three authored material colours. Compose
 * those two exported inputs once at load time so the browser displays the
 * card's real colour instead of a white fallback without running a custom
 * shader every frame.
 */
function composeKksExportMaskTexture(
  detail: THREE.Texture,
  colorMask: THREE.Texture,
  colors: [number, number, number, number][],
  hairCutout: boolean,
): THREE.Texture | undefined {
  if (typeof document === "undefined" || colors.length < 1) return undefined
  const detailImage = detail.image as CanvasImageSource & { width?: number; height?: number; naturalWidth?: number; naturalHeight?: number }
  const maskImage = colorMask.image as CanvasImageSource & { width?: number; height?: number; naturalWidth?: number; naturalHeight?: number }
  const width = Number(detailImage.naturalWidth || detailImage.width || 0)
  const height = Number(detailImage.naturalHeight || detailImage.height || 0)
  const maskWidth = Number(maskImage.naturalWidth || maskImage.width || 0)
  const maskHeight = Number(maskImage.naturalHeight || maskImage.height || 0)
  if (!runtimeColorMaskDimensionsAreSafe(width, height, maskWidth, maskHeight)) return undefined
  const detailCanvas = document.createElement("canvas")
  const maskCanvas = document.createElement("canvas")
  detailCanvas.width = maskCanvas.width = width
  detailCanvas.height = maskCanvas.height = height
  const detailContext = detailCanvas.getContext("2d", { willReadFrequently: true })
  const maskContext = maskCanvas.getContext("2d", { willReadFrequently: true })
  if (!detailContext || !maskContext) return undefined
  try {
    detailContext.drawImage(detailImage, 0, 0, width, height)
    maskContext.drawImage(maskImage, 0, 0, width, height)
    const pixels = detailContext.getImageData(0, 0, width, height)
    const selectors = maskContext.getImageData(0, 0, width, height).data
    const slots = Array.from({ length: 3 }, (_, index) => colors[index] || colors[0] || [1, 1, 1, 1])
    for (let offset = 0; offset < pixels.data.length; offset += 4) {
      const detailCoverage = Math.max(pixels.data[offset], pixels.data[offset + 1], pixels.data[offset + 2])
      const weights = [selectors[offset], selectors[offset + 1], selectors[offset + 2]].map(value => value / 255)
      const total = weights[0] + weights[1] + weights[2]
      const normalized = total > 0 ? weights.map(value => value / total) : [1, 0, 0]
      // The green DM channel carries the KKS toon/detail bake. Keep shadows
      // readable while avoiding the neon-green raw shader input.
      const detailShade = 0.68 + 0.32 * (pixels.data[offset + 1] / 255)
      for (let channel = 0; channel < 3; channel += 1) {
        const value = normalized.reduce((sum, weight, slot) => sum + weight * THREE.MathUtils.clamp(slots[slot][channel], 0, 1), 0)
        pixels.data[offset + channel] = Math.round(255 * value * detailShade)
      }
      if (hairCutout) {
        pixels.data[offset + 3] = detailCoverage > 2 ? pixels.data[offset + 3] : 0
      }
    }
    detailContext.putImageData(pixels, 0, 0)
    const texture = new THREE.CanvasTexture(detailCanvas)
    texture.flipY = false
    texture.colorSpace = THREE.SRGBColorSpace
    texture.needsUpdate = true
    return texture
  } catch {
    return undefined
  }
}

/**
 * Convert the native KKS Shader Forge face detail mask into one cheap card
 * albedo.  HeadMod-style faces intentionally have no _MainTex: the blue
 * channel carries the broad skin value while the low-blue nose bridge and
 * the cyan mouth stroke carry the authored facial cues.  Keeping this bake
 * driven by the material contract makes it reusable for every such head.
 */
function composeKksFaceDetailTexture(
  detail: THREE.Texture,
  skinMain: [number, number, number, number],
  skinSub: [number, number, number, number],
): THREE.Texture | undefined {
  if (typeof document === "undefined") return undefined
  const image = detail.image as CanvasImageSource & { width?: number; height?: number; naturalWidth?: number; naturalHeight?: number }
  const width = Number(image.naturalWidth || image.width || 0)
  const height = Number(image.naturalHeight || image.height || 0)
  if (!runtimeColorMaskDimensionsAreSafe(width, height)) return undefined
  const canvas = document.createElement("canvas")
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext("2d", { willReadFrequently: true })
  if (!context) return undefined
  try {
    context.drawImage(image, 0, 0, width, height)
    const pixels = context.getImageData(0, 0, width, height)
    for (let offset = 0; offset < pixels.data.length; offset += 4) {
      const pixelIndex = offset / 4
      const x = pixelIndex % width
      const y = Math.floor(pixelIndex / width)
      const u = (x + 0.5) / width
      const v = (y + 0.5) / height
      const red = pixels.data[offset] / 255
      const green = pixels.data[offset + 1] / 255
      const blue = pixels.data[offset + 2] / 255
      // The raw mask is mostly a shader-control field, not an albedo.  Keep
      // the feature tests spatially bounded: the red/green/blue channels are
      // reused by the game shader for unrelated broad shading, so treating
      // every low-blue pixel as an albedo would paint the whole face grey.
      const noseRegion = u > 0.455 && u < 0.545 && v > 0.28 && v < 0.72
      // HeadMod's nose bridge is a narrow black/low-channel signal.  A
      // stronger but still localised shade keeps it visible at normal editor
      // zoom without reintroducing the displaced grey face patch.
      const noseSignal = noseRegion && red < 0.16 && green < 0.16 && blue < 0.24
      const noseShadow = noseSignal ? 0.42 : 0
      const mouthRegion = u > 0.40 && u < 0.60 && v > 0.62 && v < 0.82
      // Depending on the exported card, the lip stroke is either the red
      // accent or the cyan/green control stroke.  Accept both authored forms
      // only inside the mouth window; never infer a mouth from the full mask.
      const redLip = mouthRegion && red > 0.20 && green < 0.24 && blue < 0.48
      const cyanLip = mouthRegion && red < 0.24 && green > 0.68 && blue > 0.68
      const mouthLine = redLip || cyanLip
      const shade = 1 - noseShadow
      const subMix = 0
      for (let channel = 0; channel < 3; channel += 1) {
        const base = skinMain[channel] * (1 - subMix) + skinSub[channel] * subMix
        const mouthColor = redLip ? [0.24, 0.035, 0.045][channel] : [0.18, 0.025, 0.035][channel]
        const value = mouthLine ? base * 0.05 + mouthColor * 0.95 : base * shade
        pixels.data[offset + channel] = Math.round(255 * THREE.MathUtils.clamp(value, 0, 1))
      }
      pixels.data[offset + 3] = 255
    }
    context.putImageData(pixels, 0, 0)
    const texture = new THREE.CanvasTexture(canvas)
    texture.flipY = false
    texture.colorSpace = THREE.SRGBColorSpace
    texture.userData.kksBakedDetailComposite = true
    texture.needsUpdate = true
    return texture
  } catch {
    return undefined
  }
}

export function runtimeColorMaskDimensionsAreSafe(
  width: number,
  height: number,
  maskWidth = width,
  maskHeight = height,
) {
  return [width, height, maskWidth, maskHeight].every(value => Number.isInteger(value) && value > 0 && value <= KKS_RUNTIME_CPU_TEXTURE_MAX_DIMENSION)
    && width * height <= KKS_RUNTIME_CPU_TEXTURE_MAX_PIXELS
    && maskWidth * maskHeight <= KKS_RUNTIME_CPU_TEXTURE_MAX_PIXELS
}

/**
 * Recreate KKS's eye-white cutout from its colour-key texture. The original
 * shader discards the uniform atlas background; treating that background as
 * ordinary opaque albedo produces the rectangular eye plates seen in WebGL.
 */
export function runtimeColorKeyAlphaPixels(pixels: Uint8ClampedArray, width: number, height: number): number {
  if (width < 1 || height < 1 || pixels.length !== width * height * 4) return 0
  const cornerOffsets = [0, (width - 1) * 4, (height - 1) * width * 4, (width * height - 1) * 4]
  const background = [0, 1, 2].map(channel => cornerOffsets
    .map(offset => pixels[offset + channel])
    .sort((left, right) => left - right)[Math.floor((cornerOffsets.length - 1) / 2)])
  let changed = 0
  for (let offset = 0; offset < pixels.length; offset += 4) {
    const difference = Math.max(
      Math.abs(pixels[offset] - background[0]),
      Math.abs(pixels[offset + 1] - background[1]),
      Math.abs(pixels[offset + 2] - background[2]),
    )
    const coverage = THREE.MathUtils.clamp((difference - 5) / 28, 0, 1)
    const nextAlpha = Math.round(pixels[offset + 3] * coverage)
    if (nextAlpha !== pixels[offset + 3]) changed += 1
    pixels[offset + 3] = nextAlpha
  }
  return changed
}

function composeRuntimeColorKeyAlphaTexture(source: THREE.Texture): THREE.Texture | undefined {
  if (typeof document === "undefined") return undefined
  const image = source.image as CanvasImageSource & { width?: number; height?: number; naturalWidth?: number; naturalHeight?: number }
  const width = Number(image.naturalWidth || image.width || 0)
  const height = Number(image.naturalHeight || image.height || 0)
  if (!runtimeColorMaskDimensionsAreSafe(width, height)) return undefined
  const canvas = document.createElement("canvas")
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext("2d", { willReadFrequently: true })
  if (!context) return undefined
  try {
    context.drawImage(image, 0, 0, width, height)
    const pixels = context.getImageData(0, 0, width, height)
    runtimeColorKeyAlphaPixels(pixels.data, width, height)
    context.putImageData(pixels, 0, 0)
    const texture = new THREE.CanvasTexture(canvas)
    texture.flipY = false
    texture.colorSpace = THREE.SRGBColorSpace
    texture.needsUpdate = true
    return texture
  } catch {
    return undefined
  }
}

export function runtimeMaterialNeedsColorKeyAlpha(spec?: RuntimeMaterialPayload): boolean {
  return !spec?.colorKeyAlphaHandled && !spec?.colorKeyAlphaBaked && /^cf_m_sirome_00$/i.test(String(spec?.name || ""))
}

export function runtimePixelSampleIsPrecolored(pixels: ArrayLike<number>): boolean {
  let visible = 0
  let chromatic = 0
  for (let offset = 0; offset < pixels.length; offset += 4) {
    if (Number(pixels[offset + 3]) < 24) continue
    visible += 1
    const maximum = Math.max(Number(pixels[offset]), Number(pixels[offset + 1]), Number(pixels[offset + 2]))
    const minimum = Math.min(Number(pixels[offset]), Number(pixels[offset + 1]), Number(pixels[offset + 2]))
    if (maximum - minimum >= 18) chromatic += 1
  }
  return visible > 0 && chromatic / visible >= 0.08
}

export function runtimeShadeColorFactor(
  precoloredMap: boolean,
  shaderShadeColor: readonly number[] | undefined,
  materialColor: readonly number[],
): [number, number, number] {
  if (precoloredMap) return [1, 1, 1]
  if (shaderShadeColor && shaderShadeColor.length >= 3) {
    return [Number(shaderShadeColor[0]), Number(shaderShadeColor[1]), Number(shaderShadeColor[2])]
  }
  return [
    Number(materialColor[0]) * .68,
    Number(materialColor[1]) * .68,
    Number(materialColor[2]) * .68,
  ]
}

/**
 * Bake KKS `main_eyew`'s two-colour sclera contract into a portable RGBA map.
 *
 * The extracted eye-white atlas stores its shade ramp as greyscale RGB while
 * the card stores the actual light/dark colours separately. Rendering that
 * greyscale as final albedo produces the black spherical eyes seen on native
 * template cards. KKS `Koikano/create_eyew` evaluates
 * `Color2 + MainTex.r * (Color - Color2)` for all four output channels, so
 * this one-time load-path bake also replaces the extractor's synthetic
 * color-key alpha with the card-authored result.
 */
export function runtimeEyeWhitePixels(
  pixels: Uint8ClampedArray,
  baseColor: readonly number[],
  subColor: readonly number[],
): number {
  if (baseColor.length < 3 || subColor.length < 3) return 0
  const base = Array.from({ length: 4 }, (_, index) => (
    THREE.MathUtils.clamp(Number(baseColor[index] ?? 1), 0, 1)
  ))
  const sub = Array.from({ length: 4 }, (_, index) => (
    THREE.MathUtils.clamp(Number(subColor[index] ?? 1), 0, 1)
  ))
  if (![...base, ...sub].every(Number.isFinite)) return 0
  let changed = 0
  for (let offset = 0; offset + 3 < pixels.length; offset += 4) {
    const ramp = pixels[offset] / 255
    for (let channel = 0; channel < 4; channel += 1) {
      pixels[offset + channel] = Math.round((sub[channel] + (base[channel] - sub[channel]) * ramp) * 255)
    }
    changed += 1
  }
  return changed
}

export function runtimePixelSampleIsSyntheticWhite(pixels: ArrayLike<number>): boolean {
  let visible = 0
  let nearWhite = 0
  for (let offset = 0; offset < pixels.length; offset += 4) {
    if (Number(pixels[offset + 3]) < 24) continue
    visible += 1
    if (Number(pixels[offset]) >= 248 && Number(pixels[offset + 1]) >= 248 && Number(pixels[offset + 2]) >= 248) nearWhite += 1
  }
  return visible > 0 && nearWhite / visible >= 0.92
}

function runtimeTextureIsPrecolored(texture: THREE.Texture): boolean {
  if (typeof document === "undefined") return false
  const image = texture.image as CanvasImageSource & { width?: number; height?: number; naturalWidth?: number; naturalHeight?: number }
  const width = Number(image.naturalWidth || image.width || 0)
  const height = Number(image.naturalHeight || image.height || 0)
  if (!width || !height) return false
  const canvas = document.createElement("canvas")
  canvas.width = 32
  canvas.height = 32
  const context = canvas.getContext("2d", { willReadFrequently: true })
  if (!context) return false
  try {
    context.drawImage(image, 0, 0, 32, 32)
    return runtimePixelSampleIsPrecolored(context.getImageData(0, 0, 32, 32).data)
  } catch {
    return false
  }
}

function runtimeTextureIsSyntheticWhite(texture: THREE.Texture): boolean {
  if (typeof document === "undefined") return false
  const image = texture.image as CanvasImageSource & { width?: number; height?: number; naturalWidth?: number; naturalHeight?: number }
  const width = Number(image.naturalWidth || image.width || 0)
  const height = Number(image.naturalHeight || image.height || 0)
  if (!width || !height) return false
  const canvas = document.createElement("canvas")
  canvas.width = 32
  canvas.height = 32
  const context = canvas.getContext("2d", { willReadFrequently: true })
  if (!context) return false
  try {
    context.drawImage(image, 0, 0, 32, 32)
    return runtimePixelSampleIsSyntheticWhite(context.getImageData(0, 0, 32, 32).data)
  } catch {
    return false
  }
}

function clothesKindFromProperty(property: string): number | undefined {
  if (/\.Clothes(?:Top|SailorSub[A-C]|JacketSub[A-C])$/i.test(property)) return 0
  if (/\.ClothesBot$/i.test(property)) return 1
  if (/\.ClothesBra$/i.test(property)) return 2
  if (/\.ClothesShorts$/i.test(property)) return 3
  if (/\.ClothesGloves$/i.test(property)) return 4
  if (/\.ClothesPanst$/i.test(property)) return 5
  if (/\.ClothesSocks$/i.test(property)) return 6
  if (/\.ClothesShoesInner$/i.test(property)) return 7
  if (/\.ClothesShoesOuter$/i.test(property)) return 8
  return undefined
}

export function isRuntimeClothingNodeVisible(item: RuntimeManifestItem): boolean {
  if (item.source_kind !== "clothes") return true
  const kind = clothesKindFromProperty(String(item.source_property || ""))
  if (kind === undefined) return true
  const states = Array.from({ length: 9 }, (_, index) => Number(item.clothesStates?.[index] ?? (index === kind ? item.clothesState : 0) ?? 0))
  const node = String(item.stateNode || "").toLowerCase()
  if (kind === 0) {
    if (node === "n_top_a") return states[0] === 0
    if (node === "n_top_b") return states[0] === 1
    if (node === "n_bot_a") return states[1] === 0 && states[0] !== 3
    if (node === "n_bot_b") return states[1] !== 0 && states[0] !== 3
    return states[0] !== 3
  }
  if (kind === 1) {
    if (node === "n_top_a") return states[0] === 0 && states[1] !== 2
    if (node === "n_top_b") return states[0] !== 0 && states[1] !== 2
    if (node === "n_bot_a") return states[1] === 0
    if (node === "n_bot_b") return states[1] === 1
    return states[1] !== 2 && states[1] !== 3
  }
  if (kind === 2) {
    if (node === "n_top_a") return states[2] === 0
    if (node === "n_top_b") return states[2] === 1
    if (node === "n_bot_a") return states[3] === 0 && states[2] !== 3
    if (node === "n_bot_b") return states[3] !== 0 && states[2] !== 3
    return states[2] !== 3
  }
  if (kind === 3) {
    if (node === "n_bot_a") return states[3] === 0
    if (node === "n_bot_b") return states[3] === 1
    if (node === "n_bot_c") return states[3] === 2
    return states[3] !== 3
  }
  if (kind === 5) {
    if (node === "n_panst_a") return states[5] === 0
    if (node === "n_panst_b") return states[5] === 1
    return states[5] !== 2 && states[5] !== 3
  }
  return states[kind] === 0
}

/** Apply renderer-level native visibility semantics before downloading geometry. */
export function isRuntimeSemanticItemVisible(item: RuntimeManifestItem): boolean {
  if (item.rendererEnabled === false || item.gameObjectActive === false) return false
  if (item.source_kind === "accessory") {
    const alphaRenderer = (item.rendererGroups || []).some(group => group.component === "accessory" && group.role === "alpha")
    if (alphaRenderer && (runtimeAppearancePrimaryColor(item)?.[3] ?? 1) === 0) return false
  }
  return isRuntimeClothingNodeVisible(item)
}

/**
 * Head renderers that are only expression overlays in the neutral preview.
 * The native `o_tang` surface is deliberately not included: it is the normal
 * closed-mouth mesh and must be present for every card.
 */
export function isRuntimeExpressionOnlyHeadItem(item: Pick<RuntimeManifestItem, "source_kind" | "id">): boolean {
  return item.source_kind === "head"
    && /(?:cf_O_namida|cf_O_gag_eye|cf_O_canine|cf_O_tooth)/i.test(String(item.id || ""))
}

/** Renders a KKBP export without reusing the generic VRM skeleton. */
export function KoikatsuPmxRig({ modelUrl, runtimeMode = "pmx-export", cameraPreset = "body", cameraZoom = 1, cameraYaw = 0, cameraResetToken = 0, morph, nativeBody, nativeBodyTypeBone, nativeAbmxModifiers, nativeFace, nativeHeadId, nativeAppearance, extrasEnabled = true, extrasManifestUrl, extrasManifestPayload, nativeExport = false, neutralOnly: neutralOnlyProp, className, onError, onNativeHeadStateChange, onCameraZoomChange }: Props) {
  const mountRef = useRef<HTMLDivElement | null>(null)
  const controls = useRef({ zoom: cameraZoom, reset: cameraResetToken, yaw: cameraYaw, pitch: 0 })
  const cameraPresetRef = useRef(cameraPreset)
  const shapeDirtyRef = useRef(true)
  const morphRef = useRef(morph)
  const nativeBodyRef = useRef(nativeBody)
  const nativeBodyTypeBoneRef = useRef(nativeBodyTypeBone)
  const nativeAbmxModifiersRef = useRef(nativeAbmxModifiers)
  const nativeFaceRef = useRef(nativeFace)
  const nativeHeadIdRef = useRef(nativeHeadId)
  const nativeAppearanceRef = useRef(nativeAppearance)
  const headShapeDriverRef = useRef<KksHeadShapeDriverData | undefined>(undefined)
  const headUsesDefaultFaceRef = useRef(true)
  const onErrorRef = useRef(onError)
  const onNativeHeadStateChangeRef = useRef(onNativeHeadStateChange)
  const onCameraZoomChangeRef = useRef(onCameraZoomChange)
  const renderRequestRef = useRef<() => void>(() => {})
  const applyAppearanceRef = useRef<() => void>(() => {})
  const loadExtrasRef = useRef<(manifest?: string, payload?: RuntimeManifestPayload) => void>(() => {})
  const extrasManifestRef = useRef(extrasManifestUrl)
  const extrasManifestPayloadRef = useRef(extrasManifestPayload)
  const extrasEnabledRef = useRef(extrasEnabled)
  const neutralOnly = neutralOnlyProp ?? new URLSearchParams(window.location.search).get("kksNeutral") === "1"
  const neutralOnlyRef = useRef(neutralOnly)
  useEffect(() => { morphRef.current = morph; shapeDirtyRef.current = true; renderRequestRef.current() }, [morph])
  useEffect(() => { nativeBodyRef.current = nativeBody; shapeDirtyRef.current = true; renderRequestRef.current() }, [nativeBody])
  useEffect(() => { nativeBodyTypeBoneRef.current = nativeBodyTypeBone; shapeDirtyRef.current = true; renderRequestRef.current() }, [nativeBodyTypeBone])
  useEffect(() => { nativeAbmxModifiersRef.current = nativeAbmxModifiers; shapeDirtyRef.current = true; renderRequestRef.current() }, [nativeAbmxModifiers])
  useEffect(() => { nativeFaceRef.current = nativeFace; shapeDirtyRef.current = true; renderRequestRef.current() }, [nativeFace])
  useEffect(() => { nativeHeadIdRef.current = nativeHeadId }, [nativeHeadId])
  useEffect(() => { nativeAppearanceRef.current = nativeAppearance; applyAppearanceRef.current(); renderRequestRef.current() }, [nativeAppearance])
  useEffect(() => {
    neutralOnlyRef.current = neutralOnly
    extrasManifestRef.current = extrasManifestUrl
    extrasManifestPayloadRef.current = extrasManifestPayload
    if (mountRef.current) mountRef.current.dataset.neutralOnly = String(neutralOnly)
    // Manifest changes are lightweight and must not recreate WebGL. Keep the
    // callback instance-local so two previews can never exchange clothes. A
    // neutral/card transition uses this same generation path and therefore
    // preserves the renderer and persistent native body core.
    loadExtrasRef.current(extrasManifestUrl, extrasManifestPayload)
  }, [extrasManifestPayload, extrasManifestUrl, neutralOnly])
  useEffect(() => { extrasEnabledRef.current = extrasEnabled }, [extrasEnabled])
  useEffect(() => { onErrorRef.current = onError }, [onError])
  useEffect(() => { onNativeHeadStateChangeRef.current = onNativeHeadStateChange }, [onNativeHeadStateChange])
  useEffect(() => { onCameraZoomChangeRef.current = onCameraZoomChange }, [onCameraZoomChange])
  useEffect(() => { controls.current.zoom = cameraZoom; renderRequestRef.current() }, [cameraZoom])
  useEffect(() => { controls.current.yaw = cameraYaw; renderRequestRef.current() }, [cameraYaw])
  useEffect(() => { controls.current = { ...controls.current, reset: cameraResetToken, yaw: 0, pitch: 0 }; renderRequestRef.current() }, [cameraResetToken])
  useEffect(() => { cameraPresetRef.current = cameraPreset; renderRequestRef.current() }, [cameraPreset])

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return
    const scene = new THREE.Scene()
    // The caller resolves capability from the local-card index. URL or
    // character-name inference is unsafe because the shared carrier and a
    // complete per-card export can both end in model.pmx.
    const hasNativeKkExport = nativeExport
    const nativeRuntimeTemplate = runtimeMode === "native-template"
    // A complete KKBP PMX is already baked at the card's authored 44/52
    // slider values.  Keep that card state as the immutable deformation
    // baseline for this model instance; later UI edits are applied as deltas
    // from it instead of stamping the same absolute KKS curves onto an
    // already-shaped export a second time.
    const nativeExportBodyBaseline = hasNativeKkExport
      ? Object.fromEntries(Object.entries(nativeBody || {}).map(([key, value]) => [key, Number(value)]))
      : undefined
    const nativeExportFaceBaseline = hasNativeKkExport
      ? Object.fromEntries(Object.entries(nativeFace || {}).map(([key, value]) => [key, Number(value)]))
      : undefined
    // Opt-in neutral-manikin mode for validating the shared skeleton before
    // card clothing and face assets are introduced. Normal editing is
    // unchanged; append `?kksNeutral=1` to the existing page to use it.
    const camera = new THREE.PerspectiveCamera(cameraPreset === "face" ? 24 : 30, 1, .05, 100)
    // The teacher preview is an interactive parameter inspector, not a final
    // render. Keep one low-cost PMX canvas so high-DPI displays do not make
    // the character editor compete with the rest of the app for GPU memory.
    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "high-performance" })
    } catch (error) {
      // A lost/blocked WebGL context must not take down the whole editor.
      // Keep the static card preview available and expose an inspectable
      // state so the UI can report the GPU failure instead of a blank panel.
      mount.dataset.modelState = "unavailable"
      onErrorRef.current?.(error instanceof Error ? `WebGL 预览不可用：${error.message}` : "WebGL 预览不可用")
      return () => {}
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    // The native KKBP textures already contain the game's light bake. ACES
    // remaps those bright values a second time and visibly washes out the
    // white/blue outfit, so keep the browser output in the same sRGB space as
    // the exported card preview.
    renderer.toneMapping = THREE.NoToneMapping
    renderer.toneMappingExposure = 1
    // KK's Unity shaders use a small toon ramp rather than continuous PBR
    // lighting. A nearest-filtered ramp keeps the web preview's planes and
    // folds readable without requiring the original Unity shader.
    const toonGradient = new THREE.DataTexture(
      // Keep the shadow bands lifted: the original ramp was too dark for the
      // already-baked *_light.png composites and turned blonde hair/navy
      // cloth into muddy brown/black in the browser preview.
      // WebGL2 does not accept unsized RGB as a texStorage2D internal format.
      // Store the same four ramp samples as explicit RGBA so the conservative
      // fallback path cannot poison later texture uploads with GL errors.
      new Uint8Array([
        108, 108, 118, 255,
        176, 176, 188, 255,
        232, 232, 238, 255,
        255, 255, 255, 255,
      ]),
      4, 1, THREE.RGBAFormat,
    )
    // The ramp stores linear lookup values and is not colour-managed.
    toonGradient.colorSpace = THREE.NoColorSpace
    toonGradient.magFilter = THREE.NearestFilter
    toonGradient.minFilter = THREE.NearestFilter
    toonGradient.needsUpdate = true
    renderer.domElement.style.touchAction = "none"
    mount.replaceChildren(renderer.domElement)
    mount.dataset.rendererInstance = String(++kksRendererInstanceSerial)
    mount.dataset.modelState = "loading"
    mount.dataset.neutralOnly = String(neutralOnlyRef.current)
    scene.add(new THREE.HemisphereLight(0xf6f7ff, 0x191425, 0.9))
    const key = new THREE.DirectionalLight(0xffead3, 1.15); key.position.set(2, 3, 3); scene.add(key)
    const rim = new THREE.DirectionalLight(0x80b7ff, 0.35); rim.position.set(-3, 2, -2); scene.add(rim)

    let mesh: THREE.SkinnedMesh | null = null
    // Outfit 00 is a separate skinned PMX. It must share the body's pose and
    // use the body's inverse bind matrices; keeping its original skeleton
    // leaves sleeves and hair in a different coordinate frame.
    let outfitMesh: THREE.SkinnedMesh | null = null
    // A native export owns its Outfit 00. A runtime card uses this PMX only as
    // a skeleton carrier, so the carrier outfit must never be rendered.
    const renderNativeOutfit = hasNativeKkExport && !extrasManifestRef.current && !neutralOnlyRef.current
    let renderReady = false
    let modelHeight = 1.6
    // The exported PMX is centered before it is displayed.  Outfit/hair OBJ
    // parts use the original export coordinates, so they must receive the
    // exact same translation or they appear detached from the body.
    const modelOffset = new THREE.Vector3()
    let extrasGroup: THREE.Group | null = null
    // Native cards share one immutable body asset/skeleton for the lifetime of
    // this WebGL preview.  Everything selected by a card lives in the sibling
    // card group and is replaced as one generation.  Keeping those ownership
    // domains explicit prevents a card switch from reparsing/reuploading the
    // neutral body while still guaranteeing that no old head or outfit survives.
    let runtimeCoreGroup: THREE.Group | null = null
    let runtimeCardGroup: THREE.Group | null = null
    let runtimeCoreReady = false
    let extraLoadToken = 0
    let extraLoadAbortController: AbortController | null = null
    const extraObjects: THREE.Object3D[] = []
    const runtimeCoreObjects = new Set<THREE.Object3D>()
    const bindBoneWorld = new Map<THREE.Object3D, THREE.Matrix4>()
    const extraBoneBindings = new Map<THREE.Object3D, { bone: THREE.Object3D; baseWorld: THREE.Matrix4; bindBoneWorld: THREE.Matrix4 }>()
    const extraSkeletons = new Set<THREE.Skeleton>()
    const runtimeCoreSkeletons = new Set<THREE.Skeleton>()
    const extraPrivateBones = new Set<THREE.Bone>()
    const baseScales = new Map<THREE.Object3D, THREE.Vector3>()
    const baseRotations = new Map<THREE.Object3D, THREE.Euler>()
    const basePositions = new Map<THREE.Object3D, THREE.Vector3>()
    const bodyShapeTargetBones = new Map<string, THREE.Object3D>()
    const runtimeSharedBones: THREE.Bone[] = []
    const runtimeCoreBones = new Set<THREE.Bone>()
    const runtimeBodyShapeTargetBones = new Map<string, THREE.Object3D>()
    const runtimeBaseScales = new Map<THREE.Object3D, THREE.Vector3>()
    const runtimeBaseRotations = new Map<THREE.Object3D, THREE.Euler>()
    const runtimeBasePositions = new Map<THREE.Object3D, THREE.Vector3>()
    const runtimePrivateBonesBySlot = new Map<string, THREE.Bone[]>()
    const previousAbmxTouchedBones = new Set<THREE.Object3D>()
    const materializedRuntimeHierarchies = new Set<string>()
    const runtimeCoreMaterialBaselines = new Map<THREE.Material, {
      color?: THREE.Color
      shadeColorFactor?: THREE.Color
      opacity: number
      transparent: boolean
    }>()
    let runtimeNativeBodyReady = false
    let runtimeNativeHeadReady = false
    const rebuildRuntimeShapeTargets = () => {
      runtimeBodyShapeTargetBones.clear()
      const byName = new Map<string, THREE.Bone[]>()
      runtimeSharedBones.forEach(bone => {
        const key = canonicalRuntimeBoneSegment(bone.name)
        const matches = byName.get(key) || []
        matches.push(bone)
        byName.set(key, matches)
      })
      byName.forEach((bones, name) => {
        if (bones.length === 1) runtimeBodyShapeTargetBones.set(name, bones[0])
      })
    }
    const syncCarrierVisibility = () => {
      mount.dataset.runtimeNativeTemplate = runtimeNativeBodyReady && runtimeNativeHeadReady ? "ready" : "loading"
      if (!mesh) return
      // The PMX is only a bootstrap skeleton while native KKS components are
      // loading. Once both body and face shell exist, keeping any PMX surface
      // visible leaks a different character and creates the distorted hybrid
      // face/body that prompted this rewrite.
      mesh.visible = !(runtimeNativeBodyReady && runtimeNativeHeadReady)
    }
    let matchedBoneCount = 0
    const applyNativeAppearance = () => {
      // This is event-driven (card/material changes), not part of the render
      // loop. A runtime card deliberately keeps the shared PMX face texture
      // disabled, so these native colours cannot reveal the carrier's makeup.
      if (hasNativeKkExport || neutralOnlyRef.current) return
      const appearance = nativeAppearanceRef.current
      if (!appearance) return
      let matched = 0
      let mouthMatches = 0
      const tintRoot = (root: THREE.Object3D | null) => root?.traverse(node => {
        const target = node as THREE.Mesh
        if (!target.isMesh) return
        const extraItem = target.userData?.extraItem as RuntimeManifestItem | undefined
        const runtimeSkinTint = extraItem?.source_kind === "neutral_body"
          ? rgbaFromUnknown(appearance.skin_main_color)
          : undefined
        const runtimeSkinShade = extraItem?.source_kind === "neutral_body"
          ? rgbaFromUnknown(appearance.skin_sub_color)
          : undefined
        const list = Array.isArray(target.material) ? target.material : [target.material]
        list.forEach(material => {
          const targetMaterial = material as THREE.Material & {
            color?: THREE.Color
            isMToonMaterial?: boolean
            shadeColorFactor?: THREE.Color
            update?: (delta: number) => void
          }
          const materialName = String(material?.name || "")
          if (/(?:^|[_ .])cf_m_tang(?:[_ .]|$)/i.test(materialName)) mouthMatches += 1
          const resolvedTint = runtimeSkinTint || (targetMaterial.userData?.kksBakedEyeComposite
            ? [1, 1, 1, 1] as [number, number, number, number]
            : resolveKksFaceMaterialTint(materialName, appearance))
          // `cf_m_tang` is exported with a white opaque mouth texture.  The
          // earlier half-strength neutral tint made every card's mouth read
          // as a grey oval; keep a shared reduction but preserve the warm
          // red lip channel that KKS's item pass contributes.
          const tint = resolvedTint && /(?:^|[_ .])cf_m_tang(?:[_ .]|$)/i.test(materialName)
            ? [resolvedTint[0] * .72, resolvedTint[1] * .48, resolvedTint[2] * .48, resolvedTint[3]] as [number, number, number, number]
            : resolvedTint
          const color = targetMaterial.color
          if (!tint || !color) return
          color.setRGB(tint[0], tint[1], tint[2], THREE.SRGBColorSpace)
          const runtimeAlphaMode = String(targetMaterial.userData?.runtimeAlphaMode || "")
          if (runtimeAlphaMode === "blend") {
            // A blend shader can source transparency from its texture even
            // when the card tint alpha is 1, so it must remain transparent.
            targetMaterial.opacity = tint[3]
            targetMaterial.transparent = true
          } else if (runtimeAlphaMode === "opaque" || runtimeAlphaMode === "cutout") {
            // KKS commonly packs non-opacity data in Color.a.  Preserve the
            // converter's shader classification when applying a new card
            // tint instead of silently turning skin/cutout hair into blend.
            targetMaterial.opacity = 1
            targetMaterial.transparent = false
          } else {
            // Compatibility for bootstrap/legacy materials that predate the
            // runtime shader metadata.
            targetMaterial.opacity = tint[3]
            targetMaterial.transparent = tint[3] < .999
          }
          if (targetMaterial.isMToonMaterial && runtimeSkinShade && targetMaterial.shadeColorFactor) {
            targetMaterial.shadeColorFactor.setRGB(
              runtimeSkinShade[0],
              runtimeSkinShade[1],
              runtimeSkinShade[2],
              THREE.SRGBColorSpace,
            )
          }
          if (targetMaterial.isMToonMaterial) targetMaterial.update?.(0)
          targetMaterial.needsUpdate = true
          matched += 1
        })
      })
      tintRoot(mesh)
      tintRoot(extrasGroup)
      mount.dataset.nativeAppearanceMaterials = String(matched)
      mount.dataset.nativeAppearanceMouthMatches = String(mouthMatches)
      mount.dataset.nativeAppearanceState = matched ? "ready" : "pending-materials"
    }
    applyAppearanceRef.current = applyNativeAppearance
    const isBoneNode = (node: THREE.Object3D) => (node as THREE.Bone).isBone === true || node.type === "Bone"
    const allBones = () => {
      const result: THREE.Object3D[] = []
      const seen = new Set<THREE.Object3D>()
      runtimeSharedBones.forEach(bone => { if (!seen.has(bone)) { seen.add(bone); result.push(bone) } })
      extraPrivateBones.forEach(bone => { if (!seen.has(bone)) { seen.add(bone); result.push(bone) } })
      mesh?.skeleton?.bones.forEach(bone => { if (!seen.has(bone)) { seen.add(bone); result.push(bone) } })
      mesh?.traverse(node => { if (isBoneNode(node) && !seen.has(node)) { seen.add(node); result.push(node) } })
      return result
    }
    const findBone = (patterns: RegExp[]) => {
      let result: THREE.Object3D | undefined
      allBones().some(node => { if (patterns.some(pattern => pattern.test(node.name))) { result = node; return true } return false })
      return result
    }
    const findBones = (patterns: RegExp[]) => {
      const result: THREE.Object3D[] = []
      allBones().forEach(node => { if (patterns.some(pattern => pattern.test(node.name))) result.push(node) })
      return result
    }
    const findBoneByExactName = (name?: string) => {
      const wanted = String(name || "").trim().toLowerCase()
      if (!wanted) return undefined
      return allBones().find(bone => bone.name.toLowerCase() === wanted)
    }
    const findUniqueBoneByExactName = (name?: string) => {
      const wanted = canonicalRuntimeBoneSegment(String(name || ""))
      if (!wanted) return undefined
      const matches = allBones().filter(bone => canonicalRuntimeBoneSegment(bone.name) === wanted)
      return matches.length === 1 ? matches[0] : undefined
    }
    let bodyShapeDriver: KksBodyShapeDriverData | undefined
    const applyMorph = () => {
      mount.dataset.shapeEvaluationCount = String(Number(mount.dataset.shapeEvaluationCount || 0) + 1)
      // A prior card may have aligned the visible PMX face to the runtime KKS
      // head. Always start from the carrier's authored placement so alignment
      // remains an absolute correction instead of accumulating per slider.
      mesh?.position.copy(modelOffset)
      const activeAbmxByBone = new Map<THREE.Object3D, KoikatsuAbmxModifier[]>()
      const unresolvedAbmx: string[] = []
      if (nativeRuntimeTemplate && !neutralOnlyRef.current) {
        for (const modifier of nativeAbmxModifiersRef.current || []) {
          const name = canonicalRuntimeBoneSegment(modifier.bone_name)
          if (!name) continue
          const location = Number(modifier.bone_location ?? 0)
          const bone = resolveKksAbmxBone(name, location, runtimeSharedBones, runtimePrivateBonesBySlot)
          if (!bone) {
            if (unresolvedAbmx.length < 24) unresolvedAbmx.push(`${modifier.bone_name}@${location}:0`)
            continue
          }
          const list = activeAbmxByBone.get(bone) || []
          list.push(modifier)
          activeAbmxByBone.set(bone, list)
        }
        // Restore the unmodified authored transform before KKS shape curves
        // and ABMX are evaluated. This makes every slider/card update absolute
        // and prevents ABMX from multiplying itself on repeated renders.
        const resetBones = new Set<THREE.Object3D>([
          ...previousAbmxTouchedBones,
          ...activeAbmxByBone.keys(),
        ])
        resetBones.forEach(bone => {
          const position = runtimeBasePositions.get(bone)
          const rotation = runtimeBaseRotations.get(bone)
          const scale = runtimeBaseScales.get(bone)
          if (position) bone.position.copy(position)
          if (rotation) bone.rotation.copy(rotation)
          if (scale) bone.scale.copy(scale)
        })
        previousAbmxTouchedBones.clear()
        activeAbmxByBone.forEach((_, bone) => previousAbmxTouchedBones.add(bone))
      }
      // Rebuild the presentation pose from the authored bind pose every frame.
      // Applying a delta to the already-posed bone accumulates rotation and is
      // why earlier previews slowly folded the arms into the torso. Every card
      // and the neutral manikin use this one symmetric relaxed pose, independent
      // of card id or selected resources, so selecting a card does not visibly
      // snap the skeleton from a diagnostic T-pose into the presentation pose.
      KKS_NATURAL_ARM_POSE.forEach(({ bone: name }) => {
        const bone = bodyShapeTargetBones.get(canonicalRuntimeBoneSegment(name))
        const base = bone && baseRotations.get(bone)
        if (bone && base) bone.rotation.copy(base)
        const runtimeBone = runtimeBodyShapeTargetBones.get(canonicalRuntimeBoneSegment(name))
        const runtimeBase = runtimeBone && runtimeBaseRotations.get(runtimeBone)
        if (runtimeBone && runtimeBase) runtimeBone.rotation.copy(runtimeBase)
      })
      // The native PMX carries Koikatsu's authored face morphs.  The earlier
      // preview only changed bones, so the smile/blink sliders appeared to
      // work in the card controls while the 3D face stayed frozen.  Apply
      // the actual Japanese morph groups on the body mesh; `blink=1` is the
      // UI's open-eye value, hence the inverse for wink morphs.
      const setMorphGroup = (patterns: RegExp[], influence: number) => {
        const amount = THREE.MathUtils.clamp(Number.isFinite(influence) ? influence : 0, 0, 1)
        mesh?.traverse(node => {
          const target = node as THREE.SkinnedMesh
          const dictionary = target.morphTargetDictionary
          const influences = target.morphTargetInfluences
          if (!dictionary || !influences) return
          Object.entries(dictionary).forEach(([name, index]) => {
            if (patterns.some(pattern => pattern.test(name))) influences[index] = amount
          })
        })
      }
      const smile = Number(morphRef.current?.smile ?? 0)
      const eyeOpen = Number(morphRef.current?.blink ?? 1)
      setMorphGroup([/(?:^|\.)kuti_(?:face|nose)\.f00_egao_op$/i, /(?:^|\.)eye_(?:face|nose)\.f00_egao_op$/i], smile)
      setMorphGroup([/(?:^|\.)eye_(?:face|nose)\.f00_winkl_op$/i, /(?:^|\.)eye_(?:face|nose)\.f00_winkr_op$/i], 1 - eyeOpen)
      mount.dataset.expressionSample = `${smile.toFixed(2)},${eyeOpen.toFixed(2)}`
      // The body path is deliberately singular: the card's 44 authored values
      // are evaluated by the original KKS curves/formulas and written to the
      // same 86 target bones used by the game.  There is no character baseline,
      // guessed gain, generic ten-field multiplier, or per-card exception.
      const applyPatchesToSkeleton = (
        patches: Record<string, KksLocalTransformPatch>,
        targets: Map<string, THREE.Object3D>,
        positions: Map<THREE.Object3D, THREE.Vector3>,
        rotations: Map<THREE.Object3D, THREE.Euler>,
        scales: Map<THREE.Object3D, THREE.Vector3>,
        baselinePatches?: Record<string, KksLocalTransformPatch>,
      ) => {
        let count = 0
        Object.entries(patches).forEach(([name, patch]) => {
          const bone = targets.get(canonicalRuntimeBoneSegment(name))
          if (!bone) return
          const basePosition = positions.get(bone) || bone.position.clone()
          const baseRotation = rotations.get(bone) || bone.rotation.clone()
          const baseScale = scales.get(bone) || bone.scale.clone()
          positions.set(bone, basePosition)
          rotations.set(bone, baseRotation)
          scales.set(bone, baseScale)
          const baseRotationUnity = new THREE.Euler().setFromQuaternion(
            new THREE.Quaternion().setFromEuler(baseRotation),
            "YXZ",
          )
          const baseTransform = {
            position: [basePosition.x, basePosition.y, basePosition.z],
            rotation: [
              THREE.MathUtils.radToDeg(baseRotationUnity.x),
              THREE.MathUtils.radToDeg(baseRotationUnity.y),
              THREE.MathUtils.radToDeg(baseRotationUnity.z),
            ],
            scale: [baseScale.x, baseScale.y, baseScale.z],
          } satisfies KksLocalTransform
          const baselinePatch = baselinePatches?.[name]
          const relativePatch: KksLocalTransformPatch | undefined = baselinePatch
            ? {
                position: patch.position.map((value, index) => {
                  const baseline = baselinePatch.position[index]
                  return value === null || baseline === null ? null : value - baseline
                }) as KksLocalTransformPatch["position"],
                rotation: patch.rotation.map((value, index) => {
                  const baseline = baselinePatch.rotation[index]
                  if (value === null || baseline === null) return null
                  return THREE.MathUtils.euclideanModulo(value - baseline + 180, 360) - 180
                }) as KksLocalTransformPatch["rotation"],
                scale: patch.scale.map((value, index) => {
                  const baseline = baselinePatch.scale[index]
                  return value === null || baseline === null || Math.abs(baseline) < 1e-8 ? null : value / baseline
                }) as KksLocalTransformPatch["scale"],
              }
            : undefined
          const resolved = relativePatch
            ? {
                position: baseTransform.position.map((value, index) => {
                  const delta = relativePatch.position[index]
                  return delta === null ? value : value + delta
                }) as KksLocalTransform["position"],
                rotation: baseTransform.rotation.map((value, index) => {
                  const delta = relativePatch.rotation[index]
                  return delta === null ? value : value + delta
                }) as KksLocalTransform["rotation"],
                scale: baseTransform.scale.map((value, index) => {
                  const ratio = relativePatch.scale[index]
                  return ratio === null ? value : value * ratio
                }) as KksLocalTransform["scale"],
              }
            : mergeKksLocalTransformPatch(baseTransform, patch)
          bone.position.fromArray(resolved.position)
          bone.quaternion.copy(unityEulerDegreesToQuaternion(resolved.rotation))
          bone.scale.fromArray(resolved.scale)
          count += 1
        })
        return count
      }
      const rawBody = nativeBodyRef.current || (neutralOnlyRef.current ? {} : undefined)
      if (rawBody && bodyShapeDriver) {
        const sliders = KKS_BODY_SLIDER_NAMES.map((name, index) => {
          const value = Number(rawBody[name] ?? (neutralOnlyRef.current ? KKS_DEFAULT_FEMALE_BODY_VALUES[index] : undefined) ?? 0.5)
          return Number.isFinite(value) ? value : 0.5
        })
        const bodyType = !neutralOnlyRef.current && nativeBodyTypeBoneRef.current === 1 ? 1 : 0
        const patches = {
          ...evaluateKksBodyShape(bodyShapeDriver, sliders, { typeBone: bodyType }),
          ...evaluateKksBodyAlwaysShape(bodyShapeDriver, { typeBone: bodyType }),
        }
        const baselineBodySliders = nativeExportBodyBaseline
          ? KKS_BODY_SLIDER_NAMES.map((name, index) => {
              const value = Number(nativeExportBodyBaseline[name] ?? KKS_DEFAULT_FEMALE_BODY_VALUES[index] ?? 0.5)
              return Number.isFinite(value) ? value : 0.5
            })
          : undefined
        const baselineBodyPatches = baselineBodySliders
          ? {
              ...evaluateKksBodyShape(bodyShapeDriver, baselineBodySliders, { typeBone: bodyType }),
              ...evaluateKksBodyAlwaysShape(bodyShapeDriver, { typeBone: bodyType }),
            }
          : undefined
        matchedBoneCount = 0
        matchedBoneCount = applyPatchesToSkeleton(patches, bodyShapeTargetBones, basePositions, baseRotations, baseScales, baselineBodyPatches)
        const runtimeMatches = applyPatchesToSkeleton(patches, runtimeBodyShapeTargetBones, runtimeBasePositions, runtimeBaseRotations, runtimeBaseScales)
        const requiredRuntimeMatches = KKS_BODY_TARGET_BONES.length + KKS_BODY_ALWAYS_TARGET_BONES.length
        mount.dataset.bodyShapeState = nativeRuntimeTemplate
          ? runtimeMatches === requiredRuntimeMatches ? "ready" : "partial"
          : matchedBoneCount + runtimeMatches >= KKS_BODY_TARGET_BONES.length ? "ready" : "partial"
        mount.dataset.nativeBodyKeys = String(Object.keys(rawBody).length)
        mount.dataset.boneMatches = String(matchedBoneCount)
        mount.dataset.runtimeBoneMatches = String(runtimeMatches)
        mount.dataset.runtimeBodyRequiredMatches = String(requiredRuntimeMatches)
        mount.dataset.runtimeShoulderAlwaysMatches = String(KKS_BODY_ALWAYS_TARGET_BONES.filter(
          name => runtimeBodyShapeTargetBones.has(canonicalRuntimeBoneSegment(name)),
        ).length)
        mount.dataset.nativeBodySample = sliders.slice(0, 6).map(value => value.toFixed(3)).join(",")
      } else {
        mount.dataset.bodyShapeState = rawBody ? "loading" : "no-card-values"
      }
      // Face identity is a separate native 52-channel system. It drives the
      // PMX carrier's 59 authored face bones and is deliberately evaluated
      // after the body/head-size driver, matching the game update order.
      // A fallback head is an atomic native preset: its 0-profile curves must
      // receive the official 52 default values, never values authored against
      // a missing custom ShapeAnime profile.
      const useDefaultFace = neutralOnlyRef.current || headUsesDefaultFaceRef.current
      const rawFace = useDefaultFace ? {} : nativeFaceRef.current
      const headShapeDriver = headShapeDriverRef.current
      if (rawFace && headShapeDriver) {
        const sliders = Array.from({ length: headShapeDriver.sliderCount }, (_, index) => {
          const value = Number(rawFace[String(index)] ?? (useDefaultFace ? KKS_DEFAULT_FEMALE_FACE_VALUES[index] : undefined) ?? 0.5)
          return Number.isFinite(value) ? value : 0.5
        })
        const patches = evaluateKksHeadShape(headShapeDriver, sliders)
        const baselineFaceSliders = nativeExportFaceBaseline
          ? Array.from({ length: headShapeDriver.sliderCount }, (_, index) => {
              const value = Number(nativeExportFaceBaseline[String(index)] ?? KKS_DEFAULT_FEMALE_FACE_VALUES[index] ?? 0.5)
              return Number.isFinite(value) ? value : 0.5
            })
          : undefined
        const baselineFacePatches = baselineFaceSliders
          ? evaluateKksHeadShape(headShapeDriver, baselineFaceSliders)
          : undefined
        const correctionY = Number(bodyShapeDriver?.corrections?.[2]?.scale?.[1] ?? 0)
        const headCorrectValue = !neutralOnlyRef.current && nativeBodyTypeBoneRef.current === 1 && Number.isFinite(correctionY) && Math.abs(1 + correctionY) > 1e-8
          ? 1 / (1 + correctionY)
          : 1
        const patchesForSkeleton = (
          sourcePatches: Record<string, KksLocalTransformPatch>,
          targets: Map<string, THREE.Object3D>,
        ) => {
          const faceBaseName = canonicalRuntimeBoneSegment("cf_J_FaceBase")
          const faceBasePatch = sourcePatches.cf_J_FaceBase
          const faceBase = targets.get(faceBaseName)
          const sourceScaleX = Number(faceBasePatch?.scale?.[0])
          if (!faceBasePatch || !faceBase?.parent || !Number.isFinite(sourceScaleX)) return sourcePatches
          // ShapeHeadInfoFemale samples parent.lossyScale after the body pass.
          // Carrier and native trees are evaluated independently because their
          // authored bind scales are not guaranteed to match.
          faceBase.parent.updateWorldMatrix(true, false)
          const parentScale = faceBase.parent.getWorldScale(new THREE.Vector3())
          return {
            ...sourcePatches,
            cf_J_FaceBase: {
              ...faceBasePatch,
              scale: resolveKksFaceBaseScale(
                sourceScaleX,
                [parentScale.x, parentScale.y, parentScale.z],
                headCorrectValue,
              ),
            },
          }
        }
        const carrierFacePatches = patchesForSkeleton(patches, bodyShapeTargetBones)
        const carrierBaselineFacePatches = baselineFacePatches
          ? patchesForSkeleton(baselineFacePatches, bodyShapeTargetBones)
          : undefined
        const carrierFaceMatches = applyPatchesToSkeleton(
          carrierFacePatches,
          bodyShapeTargetBones,
          basePositions,
          baseRotations,
          baseScales,
          carrierBaselineFacePatches,
        )
        const runtimeFaceMatches = applyPatchesToSkeleton(
          patchesForSkeleton(patches, runtimeBodyShapeTargetBones),
          runtimeBodyShapeTargetBones,
          runtimeBasePositions,
          runtimeBaseRotations,
          runtimeBaseScales,
        )
        const faceMatches = carrierFaceMatches + runtimeFaceMatches
        mount.dataset.faceShapeState = "ready"
        mount.dataset.nativeFaceKeys = String(Object.keys(rawFace).length)
        mount.dataset.faceBoneMatches = String(faceMatches)
        mount.dataset.runtimeFaceBoneMatches = String(runtimeFaceMatches)
        mount.dataset.headCorrectValue = headCorrectValue.toFixed(6)
        mount.dataset.nativeFaceSample = sliders.slice(0, 6).map(value => value.toFixed(3)).join(",")
      } else if (rawFace) {
        mount.dataset.faceShapeState = "loading"
      } else {
        mount.dataset.faceShapeState = "no-card-values"
      }
      let naturalPoseMatches = 0
      KKS_NATURAL_ARM_POSE.forEach(({ bone: name, rotation }) => {
        const bone = bodyShapeTargetBones.get(canonicalRuntimeBoneSegment(name))
        if (!bone) return
        const delta = unityEulerDegreesToQuaternion(rotation)
        bone.quaternion.multiply(delta)
        naturalPoseMatches += 1
      })
      mount.dataset.naturalPoseBones = String(naturalPoseMatches)
      let runtimeNaturalPoseMatches = 0
      KKS_NATURAL_ARM_POSE.forEach(({ bone: name, rotation }) => {
        const bone = runtimeBodyShapeTargetBones.get(canonicalRuntimeBoneSegment(name))
        if (!bone) return
        // The runtime payload keeps Unity's handedness, while the PMX carrier
        // has already been converted to MMD coordinates. The same visible arm
        // drop therefore uses the opposite local Z rotation on native bones.
        // Apply it to the current post-shape quaternion: copying the authored
        // base here would erase the native body driver's arm rotation.
        const delta = unityEulerDegreesToQuaternion(rotation.map(value => -value))
        bone.quaternion.multiply(delta)
        runtimeNaturalPoseMatches += 1
      })
      mount.dataset.runtimeNaturalPoseBones = String(runtimeNaturalPoseMatches)
      const visiblePoseMatches = nativeRuntimeTemplate
        ? runtimeNaturalPoseMatches
        : naturalPoseMatches
      mount.dataset.naturalPose = visiblePoseMatches === KKS_NATURAL_ARM_POSE.length ? "ready" : "partial"
      mount.dataset.naturalPoseSource = nativeRuntimeTemplate ? "runtime-shared" : "pmx-carrier"
      let abmxApplied = 0
      activeAbmxByBone.forEach((modifiers, bone) => {
        const evaluated = evaluateKksAbmxTransform({
          position: bone.position.clone(),
          rotation: bone.quaternion.clone(),
          scale: bone.scale.clone(),
        }, modifiers)
        bone.position.copy(evaluated.position)
        bone.quaternion.copy(evaluated.rotation)
        bone.scale.copy(evaluated.scale)
        abmxApplied += modifiers.length
      })
      mount.dataset.abmxDeclared = String(nativeAbmxModifiersRef.current?.length || 0)
      mount.dataset.abmxApplied = String(abmxApplied)
      mount.dataset.abmxResolvedBones = String(activeAbmxByBone.size)
      mount.dataset.abmxMissing = unresolvedAbmx.join(",")
      // Keep one representative native deformer visible for diagnostics and
      // regression checks; this also confirms the slider reaches the PMX
      // skeleton rather than only changing the React control value.
      const sample = findBone([/^cf_d_bust01_L$/i, /^cf_s_bust01_L$/i])
      if (sample) {
        mount.dataset.sampleBoneName = sample.name
        mount.dataset.sampleScale = [sample.scale.x, sample.scale.y, sample.scale.z].map(value => value.toFixed(3)).join(",")
      }
      // MMDLoader may use a bone texture; explicitly refresh it after the
      // custom bone scales so the next draw reflects the slider immediately.
      mesh?.updateMatrixWorld(true)
      mesh?.skeleton.update()
      extrasGroup?.updateMatrixWorld(true)
      outfitMesh?.skeleton.update()
      outfitMesh?.updateMatrixWorld(true)
      extraSkeletons.forEach(skeleton => skeleton.update())
      if (mesh && !neutralOnlyRef.current && runtimeSharedBones.length && !runtimeNativeHeadReady) {
        // Card mode renders only the PMX face layers; body, hair and clothes
        // use the vanilla runtime skeleton. The carrier PMX was authored from
        // a different character, so its head joint can sit below the native
        // KKS head even after both receive the same 44-slider patch. Translate
        // the visible face carrier onto the runtime joint without touching any
        // runtime geometry or rebuilding the WebGL renderer.
        const carrierHead = bodyShapeTargetBones.get(canonicalRuntimeBoneSegment("cf_j_head"))
        const runtimeHead = runtimeBodyShapeTargetBones.get(canonicalRuntimeBoneSegment("cf_j_head"))
        if (carrierHead && runtimeHead) {
          const carrierPosition = carrierHead.getWorldPosition(new THREE.Vector3())
          const runtimePosition = runtimeHead.getWorldPosition(new THREE.Vector3())
          const correction = runtimePosition.sub(carrierPosition)
          mesh.position.add(correction)
          mesh.updateMatrixWorld(true)
          mesh.skeleton.update()
          mount.dataset.carrierHeadCorrection = [correction.x, correction.y, correction.z].map(value => value.toFixed(3)).join(",")
        }
      }
      if (!neutralOnlyRef.current) {
        const shoulderL = bodyShapeTargetBones.get(canonicalRuntimeBoneSegment("cf_j_arm00_L"))
        const shoulderR = bodyShapeTargetBones.get(canonicalRuntimeBoneSegment("cf_j_arm00_R"))
        const handL = bodyShapeTargetBones.get(canonicalRuntimeBoneSegment("cf_j_hand_L"))
        const handR = bodyShapeTargetBones.get(canonicalRuntimeBoneSegment("cf_j_hand_R"))
        if (shoulderL && shoulderR && handL && handR) {
          const shoulderLWorld = shoulderL.getWorldPosition(new THREE.Vector3())
          const shoulderRWorld = shoulderR.getWorldPosition(new THREE.Vector3())
          const handLWorld = handL.getWorldPosition(new THREE.Vector3())
          const handRWorld = handR.getWorldPosition(new THREE.Vector3())
          mount.dataset.naturalPoseHandDrop = [
            shoulderLWorld.y - handLWorld.y,
            shoulderRWorld.y - handRWorld.y,
          ].map(value => value.toFixed(3)).join(",")
        }
        // Native channel 39 (upper-arm width) drives cf_s_arm02, while
        // cf_s_arm01 carries the upstream shoulder/body scale. Expose the
        // actual driven deformer so browser regression checks cannot report a
        // false "unchanged" result from sampling the wrong helper bone.
        const armShapeSample = bodyShapeTargetBones.get(canonicalRuntimeBoneSegment("cf_s_arm02_L"))
        if (armShapeSample) mount.dataset.armShapeSample = [armShapeSample.scale.x, armShapeSample.scale.y, armShapeSample.scale.z].map(value => value.toFixed(3)).join(",")
      }
      // Static OBJ pieces are exported in world bind coordinates. Apply only
      // the bone's change from bind pose, never the bone's original
      // translation, so the garment does not jump by the root-bone offset.
      if (extrasGroup && extraBoneBindings.size) {
        extrasGroup.updateMatrixWorld(true)
        const groupInverse = new THREE.Matrix4().copy(extrasGroup.matrixWorld).invert()
        extraBoneBindings.forEach((binding, object) => {
          const delta = new THREE.Matrix4().multiplyMatrices(binding.bone.matrixWorld, binding.bindBoneWorld.clone().invert())
          const world = delta.multiply(binding.baseWorld)
          object.matrix.copy(groupInverse).multiply(world)
          object.matrixAutoUpdate = false
          object.matrixWorldNeedsUpdate = true
        })
      }
      shapeDirtyRef.current = false
    }
    const getCameraBase = () => {
      const height = Math.max(.85, modelHeight)
      // The PMX bounds include the top hair/utility bones.  A target at the
      // very top (the old .98 value) pushed the face out of the narrow face
      // frustum, leaving an apparently empty canvas.  Aim at the actual eye
      // line and use a closer portrait distance.
      // The exported PMX height is measured from the rig root, not the face
      // surface. A sub-unit portrait distance puts the camera inside the
      // head, yielding a blank canvas; keep a safe focal distance and let the
      // perspective lens provide the close-up.
      if (cameraPresetRef.current === "face") return { distance: height * .76, targetY: height * .90 }
      if (cameraPresetRef.current === "upper") return { distance: height * 1.38, targetY: height * .76 }
      // The PMX keeps a small floor margin; aim near the feet so the full
      // figure occupies the editor canvas instead of dropping below its fold.
      // Include the exporter hair utility bones in the vertical extent. The
      // closer 1.58 distance clipped the head even when the DOM canvas was
      // correctly aligned; 2.2 keeps the full card silhouette inside the
      // frustum without relying on page scroll position.
      // Leave a margin for the stage toolbar and camera controls. The prior
      // 2.08 factor filled the canvas edge-to-edge, so the head sat beneath
      // the toolbar even though the model bounds were correct.
      // The native Outfit 00 hair reaches above the body bounds.  Aim the
      // full-body camera at the visual centre of the combined character so
      // the toolbar never covers the head while the shoes remain in frame.
      return { distance: height * 2.30, targetY: height * .60 }
    }
    let disposed = false
    const pmxHeadAbortController = new AbortController()
    void loadKksBodyShapeDriver().then(driver => {
      if (disposed) return
      bodyShapeDriver = driver
      mount.dataset.bodyShapeDriverVersion = String(driver.version)
      shapeDirtyRef.current = true
      applyMorph()
      renderRequestRef.current()
    }).catch(error => {
      if (disposed) return
      mount.dataset.bodyShapeState = "unavailable"
      onErrorRef.current?.(error instanceof Error ? error.message : "KKS body shape driver unavailable")
    })
    const resetView = () => { controls.current = { ...controls.current, yaw: 0, pitch: 0, reset: controls.current.reset + 1 }; renderRequestRef.current() }
    const clampZoom = (zoom: number) => THREE.MathUtils.clamp(zoom, .55, 2.7)
    const setViewZoom = (event: Event) => { const zoom = Number((event as CustomEvent<number>).detail); if (Number.isFinite(zoom)) { controls.current.zoom = clampZoom(zoom); renderRequestRef.current() } }
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      event.stopPropagation()
      controls.current.zoom = nextKksCameraZoom(controls.current.zoom, event.deltaY)
      // Publish immediately as well as from the next draw. PMX previews use
      // the same control object but some browser/device wheel paths can be
      // coalesced before requestAnimationFrame runs; the diagnostic and the
      // visible camera should never lag behind the user's wheel gesture.
      mount.dataset.cameraZoom = controls.current.zoom.toFixed(3)
      onCameraZoomChangeRef.current?.(controls.current.zoom)
      renderRequestRef.current()
    }
    window.addEventListener("teacher-avatar-reset-view", resetView)
    window.addEventListener("teacher-avatar-set-zoom", setViewZoom)
    mount.dataset.modelUrl = modelUrl
    mount.dataset.runtimeMode = nativeRuntimeTemplate ? "native-template" : "pmx-export"
    // Universal native cards never construct an MMD loader. This makes the
    // no-PMX contract structural instead of relying on a branch not to call it.
    const loader = nativeRuntimeTemplate ? null : new MMDLoader()
    // three-stdlib's MMDLoader still forwards the removed `skinning` and
    // `morphTargets` constructor flags to current Three.js materials.  The
    // flags no longer control shader compilation, but Material.setValues logs
    // two warnings per PMX material.  Large KKBP exports have hundreds of
    // materials, so the console formatting alone can stall the page. Strip
    // only those legacy flags while this preview owns the loader.
    const originalMaterialSetValues = THREE.Material.prototype.setValues
    const sanitizedMaterialSetValues = function(this: THREE.Material, values?: THREE.MaterialParameters) {
      if (!values || (!("skinning" in values) && !("morphTargets" in values))) {
        return originalMaterialSetValues.call(this, values)
      }
      const sanitized = { ...(values as THREE.MaterialParameters & { skinning?: boolean; morphTargets?: boolean }) }
      delete sanitized.skinning
      delete sanitized.morphTargets
      mount.dataset.loaderMaterialParamsSanitized = String(Number(mount.dataset.loaderMaterialParamsSanitized || 0) + 1)
      return originalMaterialSetValues.call(this, sanitized)
    }
    if (!nativeRuntimeTemplate) THREE.Material.prototype.setValues = sanitizedMaterialSetValues
    // The native KKBP export has one malformed root record (BodyTop points to
    // itself). Normalize that parent index before MMDLoader builds the Three
    // hierarchy; this is safer than allowing a self-parented Bone to reach
    // Object3D.add and keeps the rest of the native skeleton intact.
    const parser = loader
      ? (loader as unknown as { _getParser?: () => { parsePmx: (...args: any[]) => any } })._getParser?.()
      : undefined
    if (parser?.parsePmx) {
      const parsePmx = parser.parsePmx.bind(parser)
      parser.parsePmx = (...args: any[]) => {
        const data = parsePmx(...args)
        if (Array.isArray(data?.bones) && data.bones[0]?.parentIndex === 0) data.bones[0].parentIndex = -1
        return data
      }
    }
    // KKBP exports can contain a self-parented helper bone. Guard the
    // loader's hierarchy attachment for this PMX only, then restore Three.js.
    const originalObjectAdd = THREE.Object3D.prototype.add
    const guardedObjectAdd = function(this: THREE.Object3D, ...objects: THREE.Object3D[]) {
      return originalObjectAdd.apply(this, objects.filter(object => Boolean(object) && ((object as THREE.Object3D).isObject3D === true || object instanceof THREE.Object3D) && object !== this))
    }
    if (!nativeRuntimeTemplate) THREE.Object3D.prototype.add = guardedObjectAdd
    // MMDLoader constructs Bone instances from its own imported class. Patch
    // the concrete prototypes too so a duplicated/bundled class cannot bypass
    // the self-parent/undefined guard above.
    const originalBoneAdd = THREE.Bone.prototype.add
    if (!nativeRuntimeTemplate) THREE.Bone.prototype.add = guardedObjectAdd as typeof originalBoneAdd
    const originalSkinnedMeshAdd = THREE.SkinnedMesh.prototype.add
    if (!nativeRuntimeTemplate) THREE.SkinnedMesh.prototype.add = guardedObjectAdd as typeof originalSkinnedMeshAdd
    const originalGroupAdd = THREE.Group.prototype.add
    if (!nativeRuntimeTemplate) THREE.Group.prototype.add = guardedObjectAdd as typeof originalGroupAdd
    mount.dataset.boneSelfParentGuarded = String(!nativeRuntimeTemplate)
    const restoreObjectAdd = () => {
      if (THREE.Object3D.prototype.add === guardedObjectAdd) THREE.Object3D.prototype.add = originalObjectAdd
      if (THREE.Bone.prototype.add === guardedObjectAdd) THREE.Bone.prototype.add = originalBoneAdd
      if (THREE.SkinnedMesh.prototype.add === guardedObjectAdd) THREE.SkinnedMesh.prototype.add = originalSkinnedMeshAdd
      if (THREE.Group.prototype.add === guardedObjectAdd) THREE.Group.prototype.add = originalGroupAdd
      if (THREE.Material.prototype.setValues === sanitizedMaterialSetValues) THREE.Material.prototype.setValues = originalMaterialSetValues
    }
    // Legacy PMX/KKBP carrier textures still use the loader's callback path.
    // Native runtime-card textures below use the abortable fetch/bitmap path.
    const textureLoader = new THREE.TextureLoader()
    let carrierMaterialsReady = false
    let latestFaceTextureUrls: Record<string, string> = {}
    const runtimeFaceTextures = new Set<THREE.Texture>()
    const faceTextureSlotForMaterial = kksFaceTextureSlotForMaterial
    const clearRuntimeFaceTextures = () => {
      extrasGroup?.traverse(node => {
        const target = node as THREE.Mesh
        if (!target.isMesh) return
        if ((target.userData?.extraItem as RuntimeManifestItem | undefined)?.source_kind !== "head") return
        if (target.userData?.kksHiddenRuntimeFaceHelper) {
          target.visible = true
          delete target.userData.kksHiddenRuntimeFaceHelper
        }
        if (target.userData?.kksHiddenNativeFaceOverlay) {
          target.visible = true
          delete target.userData.kksHiddenNativeFaceOverlay
        }
        const list = Array.isArray(target.material) ? target.material : [target.material]
        list.forEach(material => {
          if (!faceTextureSlotForMaterial(String(material?.name || ""), String(target.userData?.extraId || target.name))) return
          const faceMaterial = material as THREE.MeshBasicMaterial
          if (faceMaterial.map && runtimeFaceTextures.has(faceMaterial.map)) faceMaterial.map = null
          faceMaterial.needsUpdate = true
        })
      })
      runtimeFaceTextures.forEach(texture => texture.dispose())
      runtimeFaceTextures.clear()
    }
    const loadRuntimeFaceTextures = async (urls: Record<string, string>, token: number, signal?: AbortSignal) => {
      if (!runtimeNativeHeadReady || !extrasGroup || hasNativeKkExport || neutralOnlyRef.current) return
      clearRuntimeFaceTextures()
      // These atlases now target the card-selected p_cf_head_* prefab whose UV
      // layout authored them. They must never be assigned to the PMX carrier.
      const supported = Object.entries(urls).filter(([slot, url]) => /^(?:face_composite|eyebrow|nose|eye_white|eyeline_up|eyeline_down|pupil_left(?:_composite)?|pupil_right(?:_composite)?)$/.test(slot) && shouldApplyKksFaceTexture(slot, url))
      const loadedPairs = await Promise.all(supported.map(async ([slot, url]) => {
        try {
          if (!isExtraLoadCurrent(token)) return null
          let texture: THREE.Texture
          if (url === KKS_TRANSPARENT_FACE_TEXTURE_SENTINEL) {
            const transparent = document.createElement("canvas")
            transparent.width = 1
            transparent.height = 1
            texture = new THREE.CanvasTexture(transparent)
            texture.needsUpdate = true
          } else {
            texture = await loadRuntimeTextureBounded(
              () => loadAbortableRuntimeTexture(url, signal),
              () => isExtraLoadCurrent(token),
              signal,
            )
          }
          if (!isExtraLoadCurrent(token) || signal?.aborted) { texture.dispose(); return null }
          texture.flipY = kksFaceTextureFlipY(slot)
          texture.colorSpace = THREE.SRGBColorSpace
          texture.wrapS = THREE.ClampToEdgeWrapping
          texture.wrapT = THREE.ClampToEdgeWrapping
          if (/^pupil_(?:left|right)(?:_composite)?$/.test(slot)) {
            const transform = kksPupilUvTransform(nativeAppearanceRef.current, slot.startsWith("pupil_right"))
            texture.repeat.set(...transform.repeat)
            texture.offset.set(...transform.offset)
          }
          texture.needsUpdate = true
          return [slot, texture] as const
        } catch { return null }
      }))
      if (!isExtraLoadCurrent(token) || signal?.aborted) {
        loadedPairs.forEach(pair => pair?.[1].dispose())
        return
      }
      const textures = new Map<string, THREE.Texture>()
      loadedPairs.forEach(pair => { if (pair) textures.set(pair[0], pair[1]) })
      textures.forEach(texture => runtimeFaceTextures.add(texture))
      const up = textures.get("eyeline_up")
      const down = textures.get("eyeline_down")
      if (up?.image && down?.image) {
        const upImage = up.image as CanvasImageSource & { width?: number; height?: number }
        const downImage = down.image as CanvasImageSource & { width?: number; height?: number }
        const canvas = document.createElement("canvas")
        canvas.width = Math.max(Number(upImage.width || 1), Number(downImage.width || 1))
        canvas.height = Math.max(Number(upImage.height || 1), Number(downImage.height || 1))
        const context = canvas.getContext("2d")
        if (context) {
          context.clearRect(0, 0, canvas.width, canvas.height)
          context.drawImage(upImage, 0, 0, canvas.width, canvas.height)
          context.drawImage(downImage, 0, 0, canvas.width, canvas.height)
          const combined = new THREE.CanvasTexture(canvas)
          combined.flipY = false
          combined.colorSpace = THREE.SRGBColorSpace
          combined.needsUpdate = true
          textures.set("eyeline_combined", combined)
          runtimeFaceTextures.add(combined)
        }
      }
      const eyeWhiteTexture = textures.get("eye_white")
      const eyeWhiteAppearance = nativeAppearanceRef.current
      if (eyeWhiteTexture?.image && eyeWhiteAppearance) {
        const baseColor = rgbaFromUnknown(eyeWhiteAppearance.eye_white_base_color)
        const subColor = rgbaFromUnknown(eyeWhiteAppearance.eye_white_sub_color)
        if (baseColor && subColor) {
          const sourceImage = eyeWhiteTexture.image as CanvasImageSource & { width?: number; height?: number }
          const width = Math.max(1, Number(sourceImage.width || 1))
          const height = Math.max(1, Number(sourceImage.height || 1))
          const canvas = document.createElement("canvas")
          canvas.width = width
          canvas.height = height
          const context = canvas.getContext("2d")
          if (context) {
            context.clearRect(0, 0, width, height)
            context.drawImage(sourceImage, 0, 0, width, height)
            const imageData = context.getImageData(0, 0, width, height)
            if (runtimeEyeWhitePixels(imageData.data, baseColor, subColor) > 0) {
              context.putImageData(imageData, 0, 0)
              const colorized = new THREE.CanvasTexture(canvas)
              colorized.flipY = eyeWhiteTexture.flipY
              colorized.colorSpace = THREE.SRGBColorSpace
              colorized.wrapS = THREE.ClampToEdgeWrapping
              colorized.wrapT = THREE.ClampToEdgeWrapping
              colorized.needsUpdate = true
              textures.set("eye_white", colorized)
              runtimeFaceTextures.delete(eyeWhiteTexture)
              eyeWhiteTexture.dispose()
              runtimeFaceTextures.add(colorized)
            }
          }
        }
      }
      const noseTexture = textures.get("nose")
      const noseAppearance = nativeAppearanceRef.current
      if (noseTexture?.image && noseAppearance) {
        // The KKS nose atlas is an alpha mask whose RGB channels are mostly
        // black/white data.  Feeding it directly to MeshBasicMaterial keeps
        // the serialized black pixels and produces a hard stripe, even after
        // the material tint is softened.  Recolour the small mask once on the
        // card-change path; this is cheap, preserves authored alpha, and
        // follows the card's skin shadow rather than a character-specific fix.
        const sourceImage = noseTexture.image as CanvasImageSource & { width?: number; height?: number }
        const width = Math.max(1, Number(sourceImage.width || 1))
        const height = Math.max(1, Number(sourceImage.height || 1))
        const canvas = document.createElement("canvas")
        canvas.width = width
        canvas.height = height
        const context = canvas.getContext("2d")
        const shadow = rgbaFromUnknown(noseAppearance.skin_sub_color || noseAppearance.skin_main_color)
        if (context && shadow) {
          context.clearRect(0, 0, width, height)
          context.drawImage(sourceImage, 0, 0, width, height)
          const imageData = context.getImageData(0, 0, width, height)
          for (let offset = 0; offset < imageData.data.length; offset += 4) {
            const alpha = imageData.data[offset + 3]
            if (!alpha) continue
            const luminance = (imageData.data[offset] + imageData.data[offset + 1] + imageData.data[offset + 2]) / (255 * 3)
            const scale = .72 + luminance * .28
            imageData.data[offset] = Math.round(shadow[0] * scale * 255)
            imageData.data[offset + 1] = Math.round(shadow[1] * scale * 255)
            imageData.data[offset + 2] = Math.round(shadow[2] * scale * 255)
          }
          context.putImageData(imageData, 0, 0)
          const recolored = new THREE.CanvasTexture(canvas)
          recolored.flipY = noseTexture.flipY
          recolored.colorSpace = THREE.SRGBColorSpace
          recolored.wrapS = THREE.ClampToEdgeWrapping
          recolored.wrapT = THREE.ClampToEdgeWrapping
          recolored.needsUpdate = true
          textures.set("nose", recolored)
          runtimeFaceTextures.delete(noseTexture)
          noseTexture.dispose()
          runtimeFaceTextures.add(recolored)
        }
      }
      let matched = 0
      extrasGroup.traverse(node => {
        const target = node as THREE.Mesh
        if (!target.isMesh) return
        if ((target.userData?.extraItem as RuntimeManifestItem | undefined)?.source_kind !== "head") return
        const list = Array.isArray(target.material) ? target.material : [target.material]
        let targetHasFaceComposite = false
        const nextMaterials = list.map(material => {
          const baseSlot = faceTextureSlotForMaterial(String(material?.name || ""), String(target.userData?.extraId || target.name))
          if (shouldHideKksRuntimeFaceHelper(String(material?.name || ""), urls[baseSlot] || "")) {
            // cf_O_eyeline contains both the visible upper line and an
            // unsupported kage helper in one renderer. Hiding the Object3D here
            // also erased the valid upper line, so suppress only this material.
            material.visible = false
            material.userData.kksHiddenRuntimeFaceHelper = true
            return material
          }
          const slot = /^pupil_(?:left|right)$/.test(baseSlot) && textures.has(`${baseSlot}_composite`)
            ? `${baseSlot}_composite`
            : baseSlot
          const texture = textures.get(slot)
          if (!texture) {
            if (shouldHideKksNativeFaceOverlay(
              baseSlot,
              urls[baseSlot] || "",
              textures.has("face_composite"),
            )) {
              target.visible = false
              target.userData.kksHiddenNativeFaceOverlay = true
              return material
            }
            return material
          }
          // `cf_m_face_00` starts life as a native KKS skin/MToon material.
          // Mutating only its generic `map` property leaves the old shader
          // uniforms and toon-lighting branch active, which turns the baked
          // skin atlas into the sharp yellow/purple regions seen on mod heads.
          // The offline compositor already produced the final opaque albedo,
          // so publish it through one cheap, deterministic unlit material.
          if (slot === "face_composite") {
            targetHasFaceComposite = true
            const depthPolicy = kksFaceLayerDepthPolicy(slot)
            const replacement = new THREE.MeshBasicMaterial({
              map: texture,
              color: 0xffffff,
              side: THREE.DoubleSide,
              transparent: false,
              opacity: 1,
              alphaTest: 0,
              depthTest: depthPolicy.depthTest,
              depthWrite: depthPolicy.depthWrite,
            })
            replacement.name = material.name
            material.dispose()
            target.visible = true
            matched += 1
            return replacement
          }
          const faceMaterial = material as THREE.MeshBasicMaterial
          faceMaterial.map = texture
          if (slot === "eye_white") faceMaterial.color?.set(0xffffff)
          faceMaterial.userData.kksBakedEyeComposite = slot.endsWith("_composite")
          faceMaterial.visible = true
          faceMaterial.transparent = true
          faceMaterial.opacity = 1
          faceMaterial.alphaTest = .01
          const depthPolicy = kksFaceLayerDepthPolicy(slot)
          faceMaterial.depthWrite = depthPolicy.depthWrite
          // Native head layers share one skinned surface, but their source
          // meshes can sit at slightly different depths.  The sclera may
          // therefore pass the depth test before the pupil even when the
          // geometry groups are correctly ordered.  Keep the eye-white layer
          // depth-tested, while forcing pupil/line overlays to draw on top of
          // that authored surface; the head shell still occludes them through
          // the surrounding face mesh.
          faceMaterial.depthTest = depthPolicy.depthTest
          if (/^pupil_(?:left|right)(?:_composite)?$/.test(slot)) {
            faceMaterial.polygonOffset = true
            faceMaterial.polygonOffsetFactor = -1
            faceMaterial.polygonOffsetUnits = -1
          }
          faceMaterial.needsUpdate = true
          target.visible = true
          matched += 1
          return material
        })
        target.material = Array.isArray(target.material) ? nextMaterials : nextMaterials[0]
        if (targetHasFaceComposite && target.userData?.extraItem &&
          (target.userData.extraItem as RuntimeManifestItem).source_kind === "head") {
          target.renderOrder = 20
        }
        // Some native head exports split sclera, iris and line art into
        // separate meshes instead of geometry groups.  Give each mesh the
        // same deterministic semantic order as the grouped path, so an
        // opaque sclera cannot cover a correctly loaded iris.
        const materialNames = (Array.isArray(target.material) ? target.material : [target.material])
          .map(material => String(material?.name || "")).join("|")
        if (/(?:hitomi|gageye)/i.test(materialNames)) target.renderOrder = 130
        else if (/sirome/i.test(materialNames)) target.renderOrder = 120
        else if (/(?:eyeline|mayuge|noseline|namida)/i.test(materialNames)) target.renderOrder = 140
      })
      // Unlike the legacy PMX carrier, native runtime heads are assembled in
      // `extrasGroup` and do not pass through the carrier material sort.  A
      // late sort here is what guarantees face -> sclera -> iris -> line art
      // order for every imported card, instead of relying on the asset's
      // arbitrary group order.
      sortFaceOverlayGroups(extrasGroup)
      applyNativeAppearance()
      const diagnostics: string[] = []
      extrasGroup.traverse(node => {
        const target = node as THREE.Mesh
        if (!target.isMesh) return
        if ((target.userData?.extraItem as RuntimeManifestItem | undefined)?.source_kind !== "head") return
        const list = Array.isArray(target.material) ? target.material : [target.material]
        list.forEach(material => {
          const name = String(material?.name || "")
          if (!isKksCarrierFaceMaterial(name)) return
          const faceMaterial = material as THREE.MeshBasicMaterial
          const image = faceMaterial.map?.image as { src?: string } | undefined
          diagnostics.push(`${name}:${material.visible}:${target.visible}:${faceMaterial.opacity}:${faceMaterial.color?.getHexString() || ""}:${String(image?.src || "")}`)
        })
      })
      mount.dataset.nativeFaceTextureCount = String(matched)
      mount.dataset.nativeFaceTextureState = matched ? "ready" : "unmatched-carrier"
      mount.dataset.nativeFaceTextureDetails = diagnostics.join("|")
      renderRequestRef.current()
    }
    // PMX stores face layers in one skinned mesh.  The material array order is
    // not guaranteed to match geometry group draw order, so the white-sclera
    // groups can otherwise be rendered after the iris groups and hide the
    // eyes entirely.  Sort groups by semantic overlay depth after materials
    // have been replaced with WebGL-safe materials.
    const sortFaceOverlayGroups = (loaded: THREE.Object3D) => {
      loaded.traverse(node => {
        const mesh = node as THREE.Mesh
        const groups = mesh.geometry?.groups
        if (!mesh.isMesh || !groups?.length) return
        const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
        const uv = mesh.geometry?.attributes?.uv as THREE.BufferAttribute | undefined
        const priority = (materialIndex: number) => {
          const name = String(materials[materialIndex]?.name || "").toLowerCase()
          if (/^(?:cf_m_face|cf_m_body|bonelyfans|cf_m_tang)/i.test(name)) return 0
          if (/sirome/.test(name)) return 10
          if (/(?:hitomi|gageye)/.test(name)) return 20
          if (/(?:eyeline|mayuge|noseline|namida)/.test(name)) return 30
          return 5
        }
        groups.sort((a, b) => priority(a.materialIndex ?? 0) - priority(b.materialIndex ?? 0))
        if (uv) {
          const uvRanges = groups.filter(group => /(hitomi|gageye|sirome)/i.test(String(materials[group.materialIndex ?? 0]?.name || ""))).map(group => {
            let minU = 1, minV = 1, maxU = 0, maxV = 0
            const end = Math.min(group.start + group.count, mesh.geometry.index?.count ?? group.start + group.count)
            for (let offset = group.start; offset < end; offset += 1) {
              const vertex = mesh.geometry.index ? mesh.geometry.index.getX(offset) : offset
              const u = uv.getX(vertex), v = uv.getY(vertex)
              minU = Math.min(minU, u); minV = Math.min(minV, v); maxU = Math.max(maxU, u); maxV = Math.max(maxV, v)
            }
            return `${String(materials[group.materialIndex ?? 0]?.name || "")}:${minU.toFixed(2)},${minV.toFixed(2)},${maxU.toFixed(2)},${maxV.toFixed(2)}`
          })
          mount.dataset.faceOverlayUv = uvRanges.join("|")
        }
      })
    }
    // The mouth is a separate PMX surface (cf_m_tang). Keep it explicit in
    // the browser material pass: the native body mesh also contains clothing
    // groups that are hidden/replaced, so relying on the generic alpha rules
    // can accidentally make the authored mouth disappear with them.
    const keepAuthoredMouthVisible = (loaded: THREE.Object3D) => {
      const details: string[] = []
      loaded.traverse(node => {
        const target = node as THREE.Mesh
        if (!target.isMesh) return
        const list = Array.isArray(target.material) ? target.material : [target.material]
        list.forEach(material => {
          const name = String(material?.name || "")
          const authoredOpacity = kksAuthoredMouthOpacity(name)
          if (authoredOpacity == null) return
          const mouth = material as THREE.MeshBasicMaterial
          mouth.transparent = true
          mouth.opacity = authoredOpacity
          mouth.alphaTest = 0.01
          mouth.depthTest = true
          mouth.depthWrite = false
          mouth.side = THREE.DoubleSide
          mouth.needsUpdate = true
          target.visible = true
          target.frustumCulled = false
          target.renderOrder = Math.max(target.renderOrder, 145)
          const image = mouth.map?.image as { width?: number; height?: number } | undefined
          details.push(`${name}:${mouth.opacity}:${Boolean(mouth.map)}:${image?.width || 0}x${image?.height || 0}`)
        })
      })
      mount.dataset.mouthState = details.join("|") || "missing"
    }
    const exportMaterialColorSets = new Map<string, [number, number, number, number][]>()
    const loadExportMaterialColors = async (loaded: THREE.Object3D, sourceUrl = modelUrl, metadataUrl = sourceUrl) => {
      const base = metadataUrl.slice(0, metadataUrl.lastIndexOf("/") + 1)
      try {
        const response = await fetch(`${base}KK_MaterialDataComplete.json`)
        if (!response.ok) return
        const records = await response.json() as Array<{ MaterialInformation?: Array<{ MaterialName?: string; ShaderPropNames?: string[]; ShaderPropColorValues?: Array<{ r?: number; g?: number; b?: number; a?: number }> }> }>
        const entries = records.flatMap(record => record.MaterialInformation || [])
        const normalize = (value: string) => value.replace(/\s+\d+$/, "").trim()
        const materials: THREE.Material[] = []
        loaded.traverse(node => {
          const value = (node as THREE.Mesh).material
          if (!value) return
          const list = Array.isArray(value) ? value : [value]
          list.forEach(material => { if (material && !materials.includes(material)) materials.push(material) })
        })
        mount.dataset.materialNames = materials.map(material => String(material.name || "")).slice(0, 24).join("|")
        let matchedCount = 0
        const fallbackColor = (name: string) => {
          const lower = name.toLowerCase()
          if (/(hair|kami|髪)/i.test(lower)) return new THREE.Color(0x513b5f)
          if (/(?:cf_m_(?:hitomi|gageye|eyeline)|(?:^|[_ .])eye(?:line)?(?:[_ .]|$)|瞳|まつげ)/i.test(lower)) return new THREE.Color(0x6b4b70)
          if (/(cloth|shirt|skirt|dress|衣|服|スカート)/i.test(lower)) return new THREE.Color(0xd9d0e5)
          if (/(shoe|靴)/i.test(lower)) return new THREE.Color(0x403847)
          return new THREE.Color(0xe8cfc4)
        }
        materials.forEach(material => {
          const target = material as THREE.MeshToonMaterial
          const name = normalize(String(material.name || ""))
          const source = entries.find(entry => {
            const sourceName = normalize(String(entry.MaterialName || ""))
            return sourceName === name || sourceName.startsWith(name) || name.startsWith(sourceName)
          })
          const names = source?.ShaderPropNames || []
          const colors = source?.ShaderPropColorValues || []
          if (source && colors.length) {
            exportMaterialColorSets.set(name.toLowerCase(), colors.slice(0, 3).map(colorValue => [
              Number(colorValue.r ?? 1),
              Number(colorValue.g ?? 1),
              Number(colorValue.b ?? 1),
              Number(colorValue.a ?? 1),
            ]))
          }
          const colorIndex = Math.max(0, names.findIndex(prop => /^_Color(?:\s+Color)?\s*0$/.test(prop.trim())))
          const color = colors[colorIndex] || colors[0]
          if (color) matchedCount += 1
          target.map = null
          if (color) target.color.setRGB(Number(color.r ?? 1), Number(color.g ?? 1), Number(color.b ?? 1))
          else target.color.copy(fallbackColor(name))
          target.needsUpdate = true
        })
        // KKBP's Unity shader names are not MMD shader names.  A lit MMD toon
        // material can therefore collapse to a white fallback; keep the PMX
        // geometry/skeleton but use a deterministic basic material for the
        // web preview colors.
        const replacements = materials.map(material => {
          const sourceMaterial = material as THREE.MeshToonMaterial
          const sourceName = String(sourceMaterial.name || "")
          const alphaLayer = isKksAlphaFaceMaterial(sourceName)
          const hiddenExpressionLayer = /(?:gageye|namida)/i.test(sourceName)
          const carrierFaceBase = /cf_m_face_00/i.test(sourceName)
          const nativeFace = carrierFaceBase && hasNativeKkExport
          const nativeHair = /cf_m_hair_/i.test(sourceName) && hasNativeKkExport
          const carrierEyeOverlay = isKksRuntimeCarrierFaceHelper(sourceName)
          // The shared PMX eye whites/pupils were authored for its source
          // character and become large rectangular plates on arbitrary card
          // head shapes. Keep the stable face/line/iris layers for runtime
          // cards; a complete native PMX export may still use its own eyes.
          const carrierFaceLayer = isKksCarrierFaceMaterial(sourceName)
            && (neutralOnlyRef.current || hasNativeKkExport)
            && !((neutralOnlyRef.current || !hasNativeKkExport) && carrierEyeOverlay)
          // Hide full-face mouth helper quads; the face albedo already has the neutral mouth line.
          // `cf_m_tang` is the authored mouth surface in this export, not a
          // debug helper.  Hiding it removes the entire mouth from the 3D
          // face. Tooth/tear overlays can remain suppressed until needed.
          const mouthHelper = /(?:tooth|namida)/i.test(sourceName)
          const baseExportClothing = /(?:cf_m_bot|cf_m_bra|cf_m_shorts|cf_m_glove|cf_m_socks|petticoat|acs_m_waist|skirt)/i.test(sourceName)
            && hasNativeKkExport
            && !/Outfit%20?00/i.test(sourceUrl)
          const duplicateExportClothing = hasNativeKkExport
            && !/Outfit%20?00/i.test(sourceUrl)
            && /(?:衣服|前裙|裙|skirt|bot_|bra|shorts|socks|glove|shoes|petticoat|acs_)/i.test(sourceName)
          const opaqueThirdPartySkinOverlay = hasNativeKkExport && /^Bonelyfans$/i.test(sourceName)
          // Non-native cards use the PMX only as a skeleton carrier. Hide its
          // entire appearance so no previous character leaks into the card.
          const hideCarrierSkin = neutralOnlyRef.current || !hasNativeKkExport
          const hideExportLayer = duplicateExportClothing || baseExportClothing || opaqueThirdPartySkinOverlay || (hideCarrierSkin && !carrierFaceLayer)
          const replacement = (alphaLayer || nativeFace || nativeHair || carrierFaceLayer ? new THREE.MeshBasicMaterial({
          name: sourceMaterial.name,
          // Keep the baked face albedo close to the card preview.  The PMX
          // face texture is already shaded; a dark tint here makes it look
          // muddy and gives the eyes/cheeks an unnatural contrast.
          color: carrierFaceBase ? 0xffffff : nativeHair ? 0xffffff : (sourceMaterial.color.getHex() === 0 ? fallbackColor(String(material.name || "")) : sourceMaterial.color),
          side: THREE.DoubleSide,
          // KKBP exports alpha-test/toon flags that are not meaningful in a
          // WebGL preview; keeping them can make hair and clothing vanish.
          transparent: alphaLayer || hideExportLayer,
          alphaTest: alphaLayer || hideExportLayer ? 0.02 : 0,
          // The exported sirome composite is an opaque debug-colored sheet;
          // its background masks the iris in WebGL.  The face texture already
          // contains the sclera, so keep this helper layer hidden and let the
          // hitomi/gageye overlays render the actual eyes.
          opacity: hideExportLayer || mouthHelper || hiddenExpressionLayer ? 0 : 1,
          depthWrite: !alphaLayer && !hideExportLayer,
          }) : new THREE.MeshToonMaterial({
            name: sourceMaterial.name,
            color: sourceMaterial.color.getHex() === 0 ? fallbackColor(String(material.name || "")) : sourceMaterial.color,
            side: THREE.DoubleSide,
            gradientMap: toonGradient,
            transparent: hideExportLayer,
            depthWrite: !hideExportLayer,
          })) as THREE.MeshBasicMaterial | THREE.MeshToonMaterial
          // Three's PMX loader marks the original toon material as skinned;
          // keep those flags on the lightweight replacement or the browser
          // preview silently falls back to the bind-pose T-pose.
          // `opacity: 0` still leaves the material in the draw list. Mark
          // carrier-only groups invisible as well so the stale eye/body layers
          // cannot reappear when a later texture callback updates the material,
          // and so they consume no fragment work on runtime cards.
          replacement.visible = !(hideExportLayer || mouthHelper || hiddenExpressionLayer)
          return replacement
        })
        loaded.traverse(node => {
          const value = (node as THREE.Mesh).material
          if (!value) return
          const list = Array.isArray(value) ? value : [value]
          const next = list.map(material => {
            const index = materials.indexOf(material)
            return index >= 0 ? replacements[index] : material
          })
          const meshNode = node as THREE.Mesh
          ;(meshNode).material = Array.isArray(value) ? next : next[0]
          if ((meshNode as THREE.SkinnedMesh).isSkinnedMesh) next.forEach(material => { material.needsUpdate = true })
          // Iris/eyeball overlays share the face mesh and can be depth-tested
          // behind the opaque face/sclera groups even when their geometry
          // groups are sorted. Draw them as a late transparent overlay so the
          // actual blue iris texture remains visible in the preview.
          next.forEach(material => {
            const materialName = String(material?.name || "")
            if (/hitomi/i.test(materialName)) {
              const overlay = material as THREE.MeshBasicMaterial
              overlay.transparent = true
              overlay.opacity = 1
              // The iris must still be occluded by the head when the camera
              // rotates to the side or rear.  Disabling depth testing made
              // both eyes visible through the skull.  A small negative
              // polygon offset is enough to keep the coplanar front overlay
              // visible without breaking normal head occlusion.
              overlay.depthTest = true
              overlay.depthWrite = false
              overlay.depthFunc = THREE.LessEqualDepth
              overlay.polygonOffset = true
              overlay.polygonOffsetFactor = -1
              overlay.polygonOffsetUnits = -1
              overlay.needsUpdate = true
              meshNode.renderOrder = Math.max(meshNode.renderOrder, 80)
              meshNode.visible = !neutralOnlyRef.current
              meshNode.frustumCulled = false
            }
            if (/sirome/i.test(materialName) && hasNativeKkExport) {
              // The shared face atlas leaves the eye sockets open; the
              // dedicated sirome mesh supplies the sclera.  Suppressing it
              // made the background visible through both sockets and left
              // the irises floating over a dark oval.  Keep the sclera in the
              // normal depth pass, then draw hitomi/gageye above it.
              const sclera = material as THREE.MeshBasicMaterial
              sclera.transparent = false
              sclera.opacity = 1
              sclera.depthTest = true
              sclera.depthWrite = true
              sclera.needsUpdate = true
            }
            if (/cf_m_face_00/i.test(materialName)) {
              // Keep the authored face surface depth-tested.  Drawing the
              // whole face as a late overlay makes the front-hair cards fall
              // behind it, which is why the earlier preview looked bald.
              const faceMaterial = material as THREE.MeshBasicMaterial
              faceMaterial.depthTest = true
              faceMaterial.depthWrite = true
              faceMaterial.needsUpdate = true
              meshNode.renderOrder = Math.min(meshNode.renderOrder, 0)
              meshNode.visible = !neutralOnlyRef.current
              meshNode.frustumCulled = false
            }
          })
          if (neutralOnlyRef.current) meshNode.visible = false
        })
        mount.dataset.materialMatches = `${matchedCount}/${materials.length}`
        mount.dataset.materialState = "ready"
      } catch {
        mount.dataset.materialState = "error"
      }
    }
    const loadExportTextures = async (loaded: THREE.Object3D, sourceUrl = modelUrl, metadataUrl = sourceUrl) => {
      // A neutral diagnostic manikin intentionally discards every texture.
      // Do not download or probe them only to null the maps a few lines later.
      if (neutralOnlyRef.current) {
        mount.dataset.textureState = "skipped-neutral"
        return
      }
      // KKBP keeps Unity material texture references in this sidecar because
      // the PMX itself intentionally contains no texture table.
      const base = sourceUrl.slice(0, sourceUrl.lastIndexOf("/") + 1)
      const metadataBase = metadataUrl.slice(0, metadataUrl.lastIndexOf("/") + 1)
      try {
        const response = await fetch(`${metadataBase}KK_TextureData.json`)
        if (!response.ok) return
        const entries = await response.json() as Array<{ textureName?: string }>
        const names = entries.map(entry => String(entry.textureName || "")).filter(Boolean)
        mount.dataset.textureState = "mapping"
        const materials: THREE.Material[] = []
        loaded.traverse(node => {
          const value = (node as THREE.Mesh).material
          if (!value) return
          const list = Array.isArray(value) ? value : [value]
          list.forEach(material => { if (material && !materials.includes(material)) materials.push(material) })
        })
        mount.dataset.textureMaterialNames = materials.map(material => String(material.name || "")).slice(0, 24).join("|")
        const textureJobs: Promise<boolean>[] = []
        let loadedTextureCount = 0
        let failedTextureCount = 0
        mount.dataset.textureSource = sourceUrl
        materials.forEach(material => {
          const target = material as THREE.MeshStandardMaterial
          // Material filtering runs before texture assignment. Hidden carrier
          // layers (old eye sheets, mouth helpers and duplicate body shells)
          // cannot contribute a pixel, so avoid their deterministic 404
          // fallback chain as well as decoding maps that would never render.
          if (target.visible === false) return
          // Keep the complete PMX material name for generated filenames.  The
          // numeric suffix is part of the actual Koikatsu texture filename
          // (for example `衣服 9300_MT_CT.png`); stripping it here makes every
          // clothing lookup silently miss its albedo texture.
          const fullMaterialName = String(material.name || "").trim()
          // These carrier sheets are intentionally suppressed in the runtime
          // composition: the neutral mouth is baked into the face map and the
          // white/tear helper sheets become opaque plates in WebGL. Do not
          // probe files for layers that this renderer will never draw.
          if (/^(?:cf_m_tang|cf_m_tooth|cf_m_sirome_00|cf_m_gageye_\d+|cf_m_namida_00)$/i.test(fullMaterialName)) return
          if (!hasNativeKkExport && /^cf_m_face_00$/i.test(fullMaterialName)) {
            // The shared PMX is geometry/skeleton only. Its face atlas contains
            // source-character makeup/decal pixels, so do not download or
            // upload that texture for arbitrary runtime cards.
            target.map = null
            target.color.set(0xfff0ddd3)
            target.needsUpdate = true
            mount.dataset.carrierFaceTextureMode = "neutral-solid"
            return
          }
          const materialName = fullMaterialName.replace(/\s+\d+.*$/, "").trim()
          const materialId = fullMaterialName.match(/\d+(?:\s*)$/)?.[0]?.trim()
          const matchingNames = names.filter(name => name.startsWith(`${materialName}_`) || name.startsWith(`${materialName} `) || Boolean(materialId && name.includes(materialId)))
          const candidates = matchingNames.filter(name => /_(?:MT_CT|CM|T\d+)\.(?:png|jpg|jpeg)$/i.test(name))
          const colorMaskCandidate = matchingNames.find(name => /_CM\.(?:png|jpg|jpeg)$/i.test(name))
          const detailMaskCandidate = matchingNames.find(name => /_DM\.(?:png|jpg|jpeg)$/i.test(name))
          const exportMaskColors = exportMaterialColorSets.get(materialName.toLowerCase())
          // CM/MT are shader inputs, not the final display albedo.  KKBP's
          // exporter writes a *_light.png composite for every visible card
          // material (including clothing). Using it for garments is critical:
          // sampling MT_CT alone loses the card's white/blue colors and makes
          // the native outfit render as a gray mannequin.
          // Eye materials are exported as distinct left/right atlases.  The
          // old preview shortcut pointed both materials at a non-existent
          // `cf_m_hitomi_preview.png`, leaving the face with blank sclera.
          // Keep the complete PMX material name so L02/R02 resolve to their
          // authored *_light.png files just like the native card.
          const lightCandidate = `${fullMaterialName}_light.png`
          // The sidecar's Chinese names were decoded with the wrong code
          // page (for example 琛ｆ湇 -> 衣服). Use the PMX material name to
          // reconstruct the real filename when an MT_CT entry exists.
          const hasComposite = candidates.some(name => /_MT_CT\.(?:png|jpg|jpeg)$/i.test(name))
          // Clothing entries in KK_TextureData.json can be mojibake while the
          // exported files retain their real names.  For every non-light
          // material, still probe the deterministic PMX-derived MT_CT path;
          // a 404 is harmless, while this makes Chinese outfit pieces load.
          const directComposite = (hasComposite || materialId)
            ? `${fullMaterialName}_MT_CT.png`
            : undefined
          // Shared carrier exports are allowed to contain only the original
          // MT_CT atlases.  A non-empty light filename is not proof that the
          // file exists; the old `lightCandidate || directComposite` branch
          // therefore requested 23 guaranteed 404s and never reached the
          // available face/eye textures.  Native KKBP folders prefer their
          // baked light composite, while the universal carrier deterministically
          // uses its exported MT_CT atlas.
          const listedComposite = candidates.find(name => /_MT_CT\.(?:png|jpg|jpeg)$/i.test(name))
          const rawChoices = hasNativeKkExport
            ? [
                { name: lightCandidate, base: `${metadataBase}pre_light/` },
                { name: lightCandidate, base: metadataBase },
                { name: directComposite, base },
                { name: listedComposite, base },
              ]
            : [
                { name: directComposite, base },
                { name: listedComposite, base },
                { name: lightCandidate, base: metadataBase },
              ]
          const seenChoices = new Set<string>()
          const textureChoices = rawChoices.filter((choice): choice is { name: string; base: string } => {
            if (!choice.name) return false
            const key = `${choice.base}|${choice.name}`
            if (seenChoices.has(key)) return false
            seenChoices.add(key)
            return true
          })
          if (textureChoices.length) mount.dataset.textureCandidates = String(Number(mount.dataset.textureCandidates || 0) + 1)
          // MMDLoader may leave a stale placeholder map from the PMX texture
          // table.  The KKBP sidecar is authoritative for this export, so
          // always replace that map with the local light/MT_CT composite.
          if (!textureChoices.length) return
          // The first export may have returned 404s for nested Outfit files;
          // version the asset request so a later local repair is not hidden by
          // a browser's negative cache.
          // KKBP's exported *_light composites are kept at the model root
          // for both the body PMX and Outfit 00.  The `pre_light` folder only
          // contains a small duplicate subset used by the card preview; it
          // is not a complete clothing texture set.
          textureJobs.push(new Promise<boolean>(resolve => {
            let settled = false
            const finish = (ok: boolean) => {
              if (settled) return
              settled = true
              if (ok) loadedTextureCount += 1
              else failedTextureCount += 1
              mount.dataset.textureLoadedCount = String(loadedTextureCount)
              mount.dataset.textureFailedCount = String(failedTextureCount)
              resolve(ok)
            }
            // A missing optional texture must not block the whole character;
            // some export folders contain references to files not selected by
            // the card. Continue with the textures that did arrive.
            // Outfit atlases are several-megabyte PNGs and are requested in
            // parallel with the body maps.  The old 8s watchdog marked the
            // slower clothing maps as failed even though the files existed,
            // leaving the entire outfit on the white material fallback.
            const attempt = (choiceIndex: number) => {
              const choice = textureChoices[choiceIndex]
              if (!choice) { finish(false); return }
              const textureUrl = `${choice.base}${encodeURIComponent(choice.name)}?v=assets-4`
              const timeout = window.setTimeout(() => attempt(choiceIndex + 1), 45000)
              textureLoader.load(textureUrl, texture => {
              window.clearTimeout(timeout)
              // PMX eye UVs use the image-space orientation from the
              // exporter; the iris composites are authored top-down while
              // the rest of the KKBP maps follow the PMX bottom-up path.
              texture.flipY = /(?:gageye|sirome)/i.test(fullMaterialName)
              texture.colorSpace = THREE.SRGBColorSpace
              if (/hitomi/i.test(fullMaterialName)) {
                // The PMX eye UVs already cover the complete MT_CT atlas;
                // do not apply the earlier light-composite crop transform.
                texture.repeat.set(1, 1)
                texture.offset.set(0, 0)
                texture.wrapS = THREE.ClampToEdgeWrapping
                texture.wrapT = THREE.ClampToEdgeWrapping
              }
              // KKBP exports the visible character textures as MT_CT/CM files.
              // Keep the sidecar color as a fallback, but use the local texture
              // when present so hair, clothing and accessories remain visible.
              target.map = texture
              target.color.set(0xffffff)
              // The body composite is intentionally very pale; a small skin
              // tint keeps exposed thighs/arms from becoming paper-white when
              // the native stockings leave gaps between garment layers.
              if (/^cf_m_body$/i.test(fullMaterialName)) target.color.set(0xffe8cfc4)
              else if (/^cf_m_face_00$/i.test(fullMaterialName)) target.color.set(0xfff0ddd3)
              target.needsUpdate = true
              renderRequestRef.current()
              // KKBP exports tangent-space normal maps separately. They are
              // the detail that gives the coat, gloves and skirt their folds
              // instead of a flat grey/PBR appearance. Load opportunistically
              // so missing optional maps never block the character.
              if (!(target as unknown as THREE.MeshBasicMaterial).isMeshBasicMaterial) {
                const normalCandidate = `${fullMaterialName}_NMP.png`
                const normalUrl = `${choice.base}${encodeURIComponent(normalCandidate)}?v=normal-1`
                textureLoader.load(normalUrl, normalTexture => {
                  normalTexture.flipY = texture.flipY
                  normalTexture.colorSpace = THREE.NoColorSpace
                  normalTexture.wrapS = THREE.ClampToEdgeWrapping
                  normalTexture.wrapT = THREE.ClampToEdgeWrapping
                  const shaded = target as unknown as THREE.MeshToonMaterial
                  shaded.normalMap = normalTexture
                  shaded.normalScale.set(0.42, 0.42)
                  shaded.needsUpdate = true
                  renderRequestRef.current()
                }, undefined, () => {})
              }
              const loadedNames = mount.dataset.textureLoadedMaterials ? mount.dataset.textureLoadedMaterials.split("|") : []
              if (!loadedNames.includes(fullMaterialName)) loadedNames.push(fullMaterialName)
              mount.dataset.textureLoadedMaterials = loadedNames.slice(0, 80).join("|")
              finish(true)
              }, undefined, () => { window.clearTimeout(timeout); attempt(choiceIndex + 1) })
            }
            if (hasNativeKkExport && !hasComposite && colorMaskCandidate && detailMaskCandidate && exportMaskColors?.length) {
              const loadMaskInput = (name: string) => new Promise<THREE.Texture | undefined>(resolveTexture => {
                textureLoader.load(
                  `${base}${encodeURIComponent(name)}?v=assets-5`,
                  texture => resolveTexture(texture),
                  undefined,
                  () => resolveTexture(undefined),
                )
              })
              void Promise.all([
                loadMaskInput(detailMaskCandidate),
                loadMaskInput(colorMaskCandidate),
              ]).then(([detailTexture, colorMaskTexture]) => {
                if (!detailTexture || !colorMaskTexture) {
                  detailTexture?.dispose()
                  colorMaskTexture?.dispose()
                  attempt(0)
                  return
                }
                const composed = composeKksExportMaskTexture(
                  detailTexture,
                  colorMaskTexture,
                  exportMaskColors,
                  /cf_m_hair_/i.test(fullMaterialName),
                )
                detailTexture.dispose()
                colorMaskTexture.dispose()
                if (!composed) {
                  attempt(0)
                  return
                }
                target.map = composed
                target.color.set(0xffffff)
                if (/cf_m_hair_/i.test(fullMaterialName)) {
                  target.transparent = true
                  target.alphaTest = 0.02
                  target.depthWrite = true
                }
                target.needsUpdate = true
                mount.dataset.textureMaskComposites = String(Number(mount.dataset.textureMaskComposites || 0) + 1)
                const loadedNames = mount.dataset.textureLoadedMaterials ? mount.dataset.textureLoadedMaterials.split("|") : []
                if (!loadedNames.includes(fullMaterialName)) loadedNames.push(fullMaterialName)
                mount.dataset.textureLoadedMaterials = loadedNames.slice(0, 80).join("|")
                renderRequestRef.current()
                finish(true)
              })
            } else {
              attempt(0)
            }
          }))
        })
        // The outfit pass replaces materials after this function returns. Wait
        // for every image first, otherwise the replacement copies `map: null`
        // and late texture callbacks update detached materials only.
        const textureResults = await Promise.all(textureJobs)
        mount.dataset.textureState = textureResults.some(Boolean) ? "ready" : "error"
      } catch {
        // A PMX without the optional sidecar remains usable with flat colors.
      }
    }
    const anchorForExtra = (item: Pick<RuntimeManifestItem, "source_kind" | "source_property" | "attachmentBone">) => {
      const authored = findBoneByExactName(item.attachmentBone)
      if (authored) return authored
      const property = String(item.source_property || "")
      if (item.source_kind === "hair") return findBone([/^cf_s_head$/i, /^cf_j_head$/i, /^cf_j_neck$/i])
      if (/\.Clothes(?:Top|SailorSub[A-C]|JacketSub[A-C]|Bra)$/i.test(property)) return findBone([/^cf_j_spine03$/i, /^cf_s_spine03$/i, /^cf_j_spine02$/i])
      if (/\.Clothes(?:Bot|Shorts|Panst)$/i.test(property)) return findBone([/^cf_j_hips$/i, /^cf_t_hips$/i])
      if (/\.Clothes(?:Socks|ShoesInner|ShoesOuter)$/i.test(property)) return findBone([/^cf_j_leg01_L$/i, /^cf_j_leg01_R$/i, /^cf_t_hips$/i])
      if (/\.ClothesGloves$/i.test(property)) return findBone([/^cf_j_arm00_L$/i, /^cf_j_arm00_R$/i, /^cf_j_spine03$/i])
      return findBone([/^cf_j_root$/i, /^cf_t_root$/i])
    }
    const rigidBoneForExtra = (item: Pick<RuntimeManifestItem, "source_kind" | "source_property" | "attachmentBone">) => {
      const authored = findBoneByExactName(item.attachmentBone)
      if (authored) return authored
      const property = String(item.source_property || "")
      // These are deliberately body anchors rather than the PMX clothing
      // helper bones.  The OBJ pieces are rigid fallback meshes, so an
      // anatomical anchor gives a stable baseline and avoids inheriting the
      // helper bones' original local offsets twice.
      if (item.source_kind === "hair") return findBone([/^cf_j_head$/i, /^cf_s_head$/i, /^cf_j_neck$/i])
      if (/\.ClothesGloves$/i.test(property)) return findBone([/^cf_j_arm00_[lr]$/i, /^cf_s_arm01_[lr]$/i, /^cf_j_shoulder02_[lr]$/i])
      if (/\.Clothes(?:Socks|ShoesInner|ShoesOuter)$/i.test(property)) return findBone([/^cf_j_leg01_[lr]$/i, /^cf_t_leg_[lr]$/i, /^cf_j_hips$/i])
      if (/\.Clothes(?:Bot|Shorts|Panst)$/i.test(property)) return findBone([/^cf_j_hips$/i, /^cf_t_hips$/i])
      return findBone([/^cf_j_spine03$/i, /^cf_s_spine03$/i, /^cf_j_spine02$/i, /^cf_j_hips$/i])
    }
    const hasUsefulPosition = (position?: [number, number, number]) => {
      if (!position) return false
      return position.some(value => Math.abs(value) > 0.0001)
    }
    const applyExtraPositionFallback = (item: RuntimeManifestItem, object: THREE.Object3D, group?: THREE.Group) => {
      const toLocal = (position: THREE.Vector3) => {
        if (!group) return position
        return group.worldToLocal(position)
      }
      if (item.position && hasUsefulPosition(item.position)) {
        object.position.copy(toLocal(new THREE.Vector3(item.position[0], item.position[1], item.position[2])))
        return
      }
      const anchor = rigidBoneForExtra(item) || anchorForExtra(item)
      if (anchor) {
        const anchorPosition = new THREE.Vector3().setFromMatrixPosition(anchor.matrixWorld)
        object.position.copy(toLocal(anchorPosition))
        return
      }
      object.position.copy(toLocal(new THREE.Vector3(0, modelHeight * 0.72, 0)))
    }
    const loadExtraTexture = async (base: string, item: { texture?: string }, token: number, signal?: AbortSignal) => {
      if (!item.texture) return undefined
      try {
        if (!isExtraLoadCurrent(token)) return undefined
        const textureUrl = item.texture.startsWith("/")
          ? item.texture
          : `${base}${encodeURIComponent(item.texture)}`
        const texture = await loadRuntimeTextureBounded(
          () => loadAbortableRuntimeTexture(textureUrl, signal),
          () => isExtraLoadCurrent(token),
          signal,
        )
        if (!isExtraLoadCurrent(token) || signal?.aborted) { texture.dispose(); return undefined }
        texture.flipY = false
        texture.colorSpace = THREE.SRGBColorSpace
        return texture
      } catch {
        return undefined
      }
    }
    const disposeMaterialResources = (materials: readonly THREE.Material[]) => {
      const disposedTextures = new Set<THREE.Texture>()
      const disposeTexture = (value: unknown) => {
        if (value instanceof THREE.Texture && value !== toonGradient && !disposedTextures.has(value)) {
          disposedTextures.add(value)
          value.dispose()
        } else if (Array.isArray(value)) {
          value.forEach(disposeTexture)
        }
      }
      materials.forEach(item => {
        if (!item) return
        Object.values(item).forEach(disposeTexture)
        // MToon exposes textures through prototype getters backed by nested
        // uniforms, so Object.values(material) alone does not own/dispose
        // them. Inspect uniform values explicitly and deduplicate shared
        // main/shade maps before releasing GPU resources.
        const uniforms = (item as THREE.ShaderMaterial).uniforms
        Object.values(uniforms || {}).forEach(uniform => {
          disposeTexture((uniform as { value?: unknown } | undefined)?.value)
        })
        item.dispose()
      })
    }
    const countRuntimeMtoonMaterials = (root: THREE.Object3D | null) => {
      const materials = new Set<THREE.Material>()
      root?.traverse(node => {
        const target = node as THREE.Mesh
        if (!target.isMesh) return
        const list = Array.isArray(target.material) ? target.material : [target.material]
        list.forEach(material => {
          if (material && (material as THREE.Material & { isMToonMaterial?: boolean }).isMToonMaterial) materials.add(material)
        })
      })
      return materials.size
    }
    const disposeExtraObject = (object: THREE.Object3D) => {
      object.traverse(node => {
        const target = node as THREE.Mesh
        if (target.geometry) target.geometry.dispose()
        const material = target.material
        const list = Array.isArray(material) ? material : [material]
        disposeMaterialResources(list.filter((item): item is THREE.Material => Boolean(item)))
        const skinned = target as THREE.SkinnedMesh
        if (skinned.isSkinnedMesh && skinned.skeleton && extraSkeletons.has(skinned.skeleton)) {
          skinned.skeleton.dispose()
          extraSkeletons.delete(skinned.skeleton)
          runtimeCoreSkeletons.delete(skinned.skeleton)
        }
      })
    }
    const rememberRuntimeCoreMaterials = (object: THREE.Object3D) => {
      object.traverse(node => {
        const target = node as THREE.Mesh
        if (!target.isMesh) return
        const materials = Array.isArray(target.material) ? target.material : [target.material]
        materials.forEach(material => {
          if (!material || runtimeCoreMaterialBaselines.has(material)) return
          const color = (material as THREE.Material & { color?: THREE.Color }).color
          const shadeColorFactor = (material as THREE.Material & { shadeColorFactor?: THREE.Color }).shadeColorFactor
          runtimeCoreMaterialBaselines.set(material, {
            ...(color ? { color: color.clone() } : {}),
            ...(shadeColorFactor ? { shadeColorFactor: shadeColorFactor.clone() } : {}),
            opacity: material.opacity,
            transparent: material.transparent,
          })
        })
      })
    }
    const restoreRuntimeCoreMaterials = () => {
      runtimeCoreMaterialBaselines.forEach((baseline, material) => {
        const color = (material as THREE.Material & { color?: THREE.Color }).color
        const mtoon = material as THREE.Material & {
          isMToonMaterial?: boolean
          shadeColorFactor?: THREE.Color
          update?: (delta: number) => void
        }
        if (baseline.color && color) color.copy(baseline.color)
        if (baseline.shadeColorFactor && mtoon.shadeColorFactor) mtoon.shadeColorFactor.copy(baseline.shadeColorFactor)
        material.opacity = baseline.opacity
        material.transparent = baseline.transparent
        if (mtoon.isMToonMaterial) mtoon.update?.(0)
        material.needsUpdate = true
      })
    }
    const forgetRuntimeCoreMaterials = () => {
      runtimeCoreGroup?.traverse(node => {
        const target = node as THREE.Mesh
        if (!target.isMesh) return
        const materials = Array.isArray(target.material) ? target.material : [target.material]
        materials.forEach(material => { if (material) runtimeCoreMaterialBaselines.delete(material) })
      })
    }
    const restoreRuntimeCoreBones = () => {
      runtimeCoreBones.forEach(bone => {
        const position = runtimeBasePositions.get(bone)
        const rotation = runtimeBaseRotations.get(bone)
        const scale = runtimeBaseScales.get(bone)
        if (position) bone.position.copy(position)
        if (rotation) bone.rotation.copy(rotation)
        if (scale) bone.scale.copy(scale)
      })
      runtimeCoreGroup?.updateMatrixWorld(true)
    }
    const clearRuntimeCardState = () => {
      clearRuntimeFaceTextures()
      latestFaceTextureUrls = {}
      // These are per-card diagnostics. Clear them before assembling the next
      // manifest so a previous UV/material fallback cannot masquerade as the
      // current card's head contract.
      delete mount.dataset.runtimeShellFallback
      delete mount.dataset.runtimeHeadFallbackReason
      if (runtimeCardGroup) {
        runtimeCardGroup.removeFromParent()
        disposeExtraObject(runtimeCardGroup)
        runtimeCardGroup = null
      }
      for (let index = extraObjects.length - 1; index >= 0; index -= 1) {
        if (!runtimeCoreObjects.has(extraObjects[index])) extraObjects.splice(index, 1)
      }
      extraBoneBindings.clear()
      bindBoneWorld.clear()
      extraSkeletons.forEach(skeleton => {
        if (runtimeCoreSkeletons.has(skeleton)) return
        skeleton.dispose()
        extraSkeletons.delete(skeleton)
      })
      // Hair/clothes/accessory private bones and the selected head hierarchy
      // are parented directly below shared body sockets, not below cardGroup.
      // Detach them explicitly before clearing registries so the old card can
      // never remain reachable through the persistent body tree.
      extraPrivateBones.forEach(bone => {
        bone.removeFromParent()
        runtimeBasePositions.delete(bone)
        runtimeBaseRotations.delete(bone)
        runtimeBaseScales.delete(bone)
      })
      extraPrivateBones.clear()
      runtimeSharedBones.filter(bone => !runtimeCoreBones.has(bone)).forEach(bone => {
        bone.removeFromParent()
        runtimeBasePositions.delete(bone)
        runtimeBaseRotations.delete(bone)
        runtimeBaseScales.delete(bone)
      })
      const retainedCoreBones = runtimeSharedBones.filter(bone => runtimeCoreBones.has(bone))
      runtimeSharedBones.splice(0, runtimeSharedBones.length, ...retainedCoreBones)
      runtimePrivateBonesBySlot.clear()
      previousAbmxTouchedBones.clear()
      materializedRuntimeHierarchies.clear()
      restoreRuntimeCoreBones()
      restoreRuntimeCoreMaterials()
      rebuildRuntimeShapeTargets()
      runtimeNativeBodyReady = runtimeCoreReady
      runtimeNativeHeadReady = false
      mount.dataset.runtimeSharedBoneCount = String(runtimeSharedBones.length)
      mount.dataset.runtimePrivateSlotCount = "0"
      mount.dataset.extraCount = String(runtimeCoreObjects.size)
      syncCarrierVisibility()
      shapeDirtyRef.current = true
      renderer.renderLists.dispose()
      renderRequestRef.current()
    }
    const disposeRuntimeCoreState = () => {
      forgetRuntimeCoreMaterials()
      if (runtimeCoreGroup) {
        runtimeCoreGroup.removeFromParent()
        disposeExtraObject(runtimeCoreGroup)
        runtimeCoreGroup = null
      }
      runtimeCoreSkeletons.forEach(skeleton => {
        if (extraSkeletons.has(skeleton)) {
          skeleton.dispose()
          extraSkeletons.delete(skeleton)
        }
      })
      runtimeCoreSkeletons.clear()
      runtimeCoreBones.forEach(bone => {
        bone.removeFromParent()
        runtimeBasePositions.delete(bone)
        runtimeBaseRotations.delete(bone)
        runtimeBaseScales.delete(bone)
      })
      runtimeCoreBones.clear()
      runtimeCoreObjects.forEach(object => {
        const index = extraObjects.indexOf(object)
        if (index >= 0) extraObjects.splice(index, 1)
      })
      runtimeCoreObjects.clear()
      runtimeCoreMaterialBaselines.clear()
      runtimeSharedBones.length = 0
      runtimeBodyShapeTargetBones.clear()
      runtimeCoreReady = false
      runtimeNativeBodyReady = false
    }
    const isExtraLoadCurrent = (token: number) => !disposed && token === extraLoadToken
    const applySkinnedAccessoryMoveBones = (
      raw: unknown[] | undefined,
      sourceNames: string[],
      remap: Array<number | undefined>,
      bones: THREE.Bone[],
    ) => {
      const values = Array.isArray(raw?.[2]) ? raw[2] as unknown[] : []
      const moveNames = ["n_move", "n_move2"]
      let applied = 0
      moveNames.forEach((moveName, rowIndex) => {
        const sourceIndex = sourceNames.findIndex(name => canonicalRuntimeBoneSegment(name) === moveName)
        const targetIndex = sourceIndex >= 0 ? remap[sourceIndex] : undefined
        const target = targetIndex === undefined ? undefined : bones[targetIndex]
        // N_move/N_move2 belong to the accessory prefab. Never mutate a
        // same-named shared character bone if a mod happens to expose one.
        if (!(target instanceof THREE.Bone) || !extraPrivateBones.has(target)) return
        const offset = rowIndex * 3
        const position = Array.isArray(values[offset]) ? values[offset] as number[] : []
        const rotation = Array.isArray(values[offset + 1]) ? values[offset + 1] as number[] : []
        const scale = Array.isArray(values[offset + 2]) ? values[offset + 2] as number[] : []
        if (!position.length && !rotation.length && !scale.length) return
        // This mirrors ChaAccessoryComponent: the two card rows directly set
        // the local TRS of N_move and N_move2. Collapsing both rows into one
        // mesh transform changes their pivot order and turns face accessories
        // such as a one-eye patch into a full-width band.
        target.position.set(
          Number(position[0] || 0) * .01,
          Number(position[1] || 0) * .01,
          Number(position[2] || 0) * .01,
        )
        target.quaternion.copy(unityEulerDegreesToQuaternion(rotation))
        target.scale.set(
          Number.isFinite(Number(scale[0])) ? Number(scale[0]) : 1,
          Number.isFinite(Number(scale[1])) ? Number(scale[1]) : 1,
          Number.isFinite(Number(scale[2])) ? Number(scale[2]) : 1,
        )
        // addMove is the card-authored local rest transform for this
        // accessory generation. ABMX evaluates after it; retaining the
        // prefab's pre-card baseline would erase N_move/N_move2 placement on
        // every slider update.
        runtimeBasePositions.set(target, target.position.clone())
        runtimeBaseRotations.set(target, target.rotation.clone())
        runtimeBaseScales.set(target, target.scale.clone())
        target.updateMatrixWorld(true)
        applied += 1
      })
      return applied
    }
    const loadRuntimeSkinnedExtra = async (
      item: RuntimeManifestItem,
      base: string,
      token: number,
      group: THREE.Group | undefined,
      signal: AbortSignal,
    ): Promise<THREE.Object3D | null> => {
      if (!item.runtimeSkin) return null
      if (!isExtraLoadCurrent(token)) return null
      if (isExtraLoadCurrent(token)) {
        mount.dataset.runtimeSkinAttempted = String(Number(mount.dataset.runtimeSkinAttempted || 0) + 1)
      }
      let stagedGeometry: THREE.BufferGeometry | undefined
      let stagedMaterials: THREE.Material[] = []
      let stagedSkeleton: THREE.Skeleton | undefined
      const stagedPrivateBones: THREE.Bone[] = []
      const stagedCoreBones: THREE.Bone[] = []
      let committed = false
      try {
        const neutralManikin = item.source_kind === "neutral_body"
        const nativeHead = item.source_kind === "head"
        const resolvedRuntimeSkin = item.runtimeSkin.startsWith("/")
          ? item.runtimeSkin
          : neutralManikin
            ? `/api/koikatsu-cards/shared-base/neutral-body/${item.runtimeSkin}`
            : item.runtimeSkin
        const resolvedRuntimeHierarchy = item.runtimeHierarchy
          ? item.runtimeHierarchy.startsWith("/")
            ? item.runtimeHierarchy
            : `${resolvedRuntimeSkin.slice(0, resolvedRuntimeSkin.lastIndexOf("/") + 1)}${encodeURIComponent(item.runtimeHierarchy)}`
          : undefined
        const advertisedSkinBytes = Number(item.runtimeSkinSize || 0)
        if (!Number.isFinite(advertisedSkinBytes) || advertisedSkinBytes < 0 || advertisedSkinBytes > KKS_RUNTIME_SKIN_MAX_BYTES) {
          throw new Error("skin payload is too large")
        }
        const skinUrl = `${resolvedRuntimeSkin}${resolvedRuntimeSkin.includes("?") ? "&" : "?"}v=runtime-skin-3`
        const response = await fetch(skinUrl, { signal })
        if (!response.ok) throw new Error(`skin payload ${response.status}`)
        const declaredSkinBytes = Number(response.headers.get("content-length") || 0)
        if (!Number.isFinite(declaredSkinBytes) || declaredSkinBytes < 0 || declaredSkinBytes > KKS_RUNTIME_SKIN_MAX_BYTES) {
          throw new Error("skin payload is too large")
        }
        if (!isExtraLoadCurrent(token) || signal.aborted) return null
        const skinBytes = await response.arrayBuffer()
        if (skinBytes.byteLength > KKS_RUNTIME_SKIN_MAX_BYTES) throw new Error("skin payload is too large")
        const payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(skinBytes)) as RuntimeSkinPayload
        if (!isExtraLoadCurrent(token) || signal.aborted) return null
        const vertices = payload.vertices || []
        const normals = payload.normals || []
        const uvs = payload.uvs || []
        const indices = payload.indices || []
        const sourceBoneNames = payload.boneNames || []
        const sourceBonePaths = payload.bonePaths || []
        const sourceSkinIndices = payload.skinIndices || []
        const skinWeights = payload.skinWeights || []
        const declaredSkinBoneCount = payload.skinBoneCount ?? sourceBoneNames.length
        const sourceParents = payload.boneParentIndices || []
        const sourceParentNames = payload.boneParentNames || []
        const sourceLocalMatrices = payload.boneLocalMatrices || []
        const vertexCount = vertices.length / 3
        if (!Number.isInteger(vertexCount) || vertexCount < 1 || uvs.length !== vertexCount * 2 || sourceSkinIndices.length !== vertexCount * 4 || skinWeights.length !== vertexCount * 4 || !indices.length) throw new Error("invalid skin payload")
        const skinBoneCount = resolveRuntimeSkinBoneCount(
          declaredSkinBoneCount,
          sourceBoneNames.length,
          sourceSkinIndices,
          skinWeights,
          payload.bindPoses || [],
        )
        if (sourceSkinIndices.some(value => !Number.isInteger(value) || value < 0 || value >= skinBoneCount)) throw new Error("skin index references hierarchy-only bone")
        const bodyHierarchyRequest = neutralManikin && resolvedRuntimeHierarchy
          ? loadRuntimeHierarchyPayload(resolvedRuntimeHierarchy, signal)
          : undefined
        void bodyHierarchyRequest?.catch(() => undefined)
        // Build the canonical body Transform/socket graph from p_cf_body_bone,
        // not from one renderer's truncated m_Bones array. The full hierarchy
        // owns all 86 ShapeBody targets, ABMX BodyTop nodes and a_n_* sockets.
        // Individual GPU Skeletons remain compact below.
        if (neutralManikin && group && runtimeSharedBones.length === 0) {
          if (!bodyHierarchyRequest || !resolvedRuntimeHierarchy) throw new Error("missing neutral body hierarchy")
          const hierarchy = await bodyHierarchyRequest
          if (!isExtraLoadCurrent(token) || signal.aborted) return null
          const hierarchyCount = resolveRuntimeHierarchyNodeCount(hierarchy)
          const hierarchyNames = hierarchy.names || []
          const hierarchyPaths = hierarchy.paths || []
          const hierarchyParents = hierarchy.parentIndices || []
          const hierarchyMatrices = hierarchy.localMatrices || []
          const nativeBones = hierarchyNames.map((name, index) => {
            const bone = new THREE.Bone()
            bone.name = name
            bone.userData.runtimeHierarchyPath = hierarchyPaths[index]
            const values = hierarchyMatrices.slice(index * 16, index * 16 + 16)
            new THREE.Matrix4().fromArray(values).decompose(bone.position, bone.quaternion, bone.scale)
            return bone
          })
          nativeBones.forEach((bone, index) => {
            const parentIndex = Number(hierarchyParents[index])
            if (Number.isInteger(parentIndex) && parentIndex >= 0 && nativeBones[parentIndex]) nativeBones[parentIndex].add(bone)
            else group.add(bone)
          })
          runtimeSharedBones.push(...nativeBones)
          nativeBones.forEach(bone => runtimeCoreBones.add(bone))
          stagedCoreBones.push(...nativeBones)
          nativeBones.forEach(bone => {
            runtimeBasePositions.set(bone, bone.position.clone())
            runtimeBaseRotations.set(bone, bone.rotation.clone())
            runtimeBaseScales.set(bone, bone.scale.clone())
          })
          rebuildRuntimeShapeTargets()
          materializedRuntimeHierarchies.add(resolvedRuntimeHierarchy)
          mount.dataset.runtimeBodyHierarchyNodes = String(hierarchyCount)
          mount.dataset.runtimeBodySocketCount = String(hierarchyNames.filter(name => /^a_n_/i.test(name)).length)
          mount.dataset.runtimeSharedBoneCount = String(nativeBones.length)
        }
        // Do not launch hierarchy requests until the corresponding skin JSON
        // has passed its structural checks. They still share this generation's
        // AbortSignal and run together once the head is known to be usable.
        const hierarchyRequest = nativeHead && resolvedRuntimeHierarchy
          ? loadRuntimeHierarchyPayload(resolvedRuntimeHierarchy, signal)
          : undefined
        const canonicalHeadHierarchyRequest = nativeHead
          ? loadRuntimeHierarchyPayload(KKS_CANONICAL_HEAD_HIERARCHY_URL, signal)
          : undefined
        // Both promises are awaited below in native order. Attach observers
        // immediately so a faster sibling rejection is never reported as an
        // unhandled promise while the canonical hierarchy is still parsing.
        void hierarchyRequest?.catch(() => undefined)
        void canonicalHeadHierarchyRequest?.catch(() => undefined)
        if (nativeHead && group && canonicalHeadHierarchyRequest) {
          const wantedParent = canonicalRuntimeBoneSegment(String(item.attachmentBone || ""))
          const attachmentMatches = runtimeSharedBones.filter(
            bone => canonicalRuntimeBoneSegment(bone.name) === wantedParent,
          )
          const attachmentBone = attachmentMatches.length === 1 ? attachmentMatches[0] : undefined
          if (!(attachmentBone instanceof THREE.Bone)) {
            throw new Error(`missing native head attachment ${item.attachmentBone || "(empty)"}`)
          }
          // KKS owns one canonical p_cf_head_bone tree below cf_s_head. A
          // selected head never replaces that tree: ChangeHead copies local
          // transforms onto same-named nodes and all hair/accessories keep
          // using the canonical ChaReference sockets.
          if (!materializedRuntimeHierarchies.has(KKS_CANONICAL_HEAD_HIERARCHY_URL)) {
            const canonical = await canonicalHeadHierarchyRequest
            if (!isExtraLoadCurrent(token) || signal.aborted) return null
            const canonicalCount = resolveRuntimeHierarchyNodeCount(canonical)
            const canonicalNames = canonical.names || []
            const canonicalPaths = canonical.paths || []
            const canonicalParents = canonical.parentIndices || []
            const canonicalMatrices = canonical.localMatrices || []
            const canonicalBones = canonicalNames.map((name, index) => {
              const bone = new THREE.Bone()
              bone.name = name
              bone.userData.runtimeHierarchyPath = canonicalPaths[index]
              const values = canonicalMatrices.slice(index * 16, index * 16 + 16)
              new THREE.Matrix4().fromArray(values).decompose(bone.position, bone.quaternion, bone.scale)
              return bone
            })
            canonicalBones.forEach((bone, index) => {
              const parentIndex = canonicalParents[index]
              if (parentIndex >= 0) canonicalBones[parentIndex].add(bone)
              else attachmentBone.add(bone)
              runtimeBasePositions.set(bone, bone.position.clone())
              runtimeBaseRotations.set(bone, bone.rotation.clone())
              runtimeBaseScales.set(bone, bone.scale.clone())
            })
            runtimeSharedBones.push(...canonicalBones)
            materializedRuntimeHierarchies.add(KKS_CANONICAL_HEAD_HIERARCHY_URL)
            mount.dataset.runtimeHeadHierarchyNodes = String(canonicalCount)
            mount.dataset.runtimeHeadSocketCount = String(canonicalNames.filter(
              name => /^cf_J_FaceUp_ty$|^a_n_/i.test(name),
            ).length)
          }
          if (
            resolvedRuntimeHierarchy
            && hierarchyRequest
            && !materializedRuntimeHierarchies.has(resolvedRuntimeHierarchy)
          ) {
            const selected = await hierarchyRequest
            if (!isExtraLoadCurrent(token) || signal.aborted) return null
            resolveRuntimeHierarchyNodeCount(selected)
            const selectedNames = selected.names || []
            const selectedPaths = selected.paths || []
            const selectedMatrices = selected.localMatrices || []
            const canonicalTargets = runtimeSharedBones.map(bone => {
              const names: string[] = []
              let current: THREE.Object3D | null = bone
              while (current) {
                if (current.name) names.push(current.name)
                current = current.parent
              }
              return { name: bone.name, path: names.reverse().join("/") }
            })
            const copyRemap = resolveRuntimeBoneRemap(selectedNames, selectedPaths, canonicalTargets)
            let copied = 0
            copyRemap.forEach((targetIndex, sourceIndex) => {
              if (targetIndex === undefined) return
              const target = runtimeSharedBones[targetIndex]
              // ChangeHead replaces the canonical head subtree, never the
              // persistent body/attachment bones that own that subtree.
              if (runtimeCoreBones.has(target)) return
              const values = selectedMatrices.slice(sourceIndex * 16, sourceIndex * 16 + 16)
              new THREE.Matrix4().fromArray(values).decompose(target.position, target.quaternion, target.scale)
              runtimeBasePositions.set(target, target.position.clone())
              runtimeBaseRotations.set(target, target.rotation.clone())
              runtimeBaseScales.set(target, target.scale.clone())
              copied += 1
            })
            materializedRuntimeHierarchies.add(resolvedRuntimeHierarchy)
            mount.dataset.runtimeSelectedHeadTransformCopies = String(copied)
          }
          rebuildRuntimeShapeTargets()
          mount.dataset.runtimeSharedBoneCount = String(runtimeSharedBones.length)
        }
        // Unity clothing bundles occasionally contain the historical
        // `elboback` spelling while the shared PMX skeleton exposes the same
        // joint as `elbow`.
        const privateSlotKey = !neutralManikin && !nativeHead
          ? `${String(item.source_kind || "asset")}:${String(item.source_property || item.id)}`
          : ""
        const slotPrivateBones = privateSlotKey ? runtimePrivateBonesBySlot.get(privateSlotKey) || [] : []
        // Native KKS creates one private prefab hierarchy per semantic hair/
        // accessory/clothing slot. All renderers from that prefab reuse those
        // Bone objects; creating a new chain per renderer causes visible drift.
        const bindingBones = runtimeSharedBones.length
          ? [...runtimeSharedBones, ...slotPrivateBones]
          : mesh?.skeleton.bones || []
        if (!bindingBones.length) throw new Error("native skeleton not ready")
        const unparentedAttachment = item.source_kind === "accessory" && isKksUnparentedAccessory(item.attachmentBone)
        const wantedAttachmentName = unparentedAttachment
          ? ""
          : canonicalRuntimeBoneSegment(String(item.attachmentBone || ""))
        const runtimeAttachmentMatches = wantedAttachmentName
          ? (runtimeSharedBones.length ? runtimeSharedBones : bindingBones).filter(bone => canonicalRuntimeBoneSegment(bone.name) === wantedAttachmentName)
          : []
        const exactAttachmentBone = runtimeAttachmentMatches.length === 1
          ? runtimeAttachmentMatches[0]
          : runtimeSharedBones.length ? undefined : findBoneByExactName(item.attachmentBone)
        const exactAttachmentIndex = exactAttachmentBone ? bindingBones.indexOf(exactAttachmentBone as THREE.Bone) : -1
        const targetBonePaths = bindingBones.map(bone => {
          const names: string[] = []
          let current: THREE.Object3D | null = bone
          const seen = new Set<THREE.Object3D>()
          while (current && !seen.has(current)) {
            seen.add(current)
            if (current.name) names.push(current.name)
            current = current.parent
          }
          return names.reverse().join("/")
        })
        const targetBoneRecords = bindingBones.map((bone, index) => ({ name: bone.name, path: targetBonePaths[index] }))
        const remap = resolveRuntimeBoneRemap(
          sourceBoneNames,
          sourceBonePaths,
          targetBoneRecords,
        )
        sourceBoneNames.forEach((name, index) => {
          if (name === "__attachment__" && exactAttachmentIndex >= 0) remap[index] = exactAttachmentIndex
        })
        if (isExtraLoadCurrent(token)) {
          const sourceSkinBones = sourceBoneNames.slice(0, skinBoneCount)
          const mappedSkinBones = remap.slice(0, skinBoneCount).filter(index => index !== undefined)
          const sourceBustBones = sourceSkinBones.filter(name => /(?:bust|bnip)/i.test(name))
          const mappedBustBones = sourceSkinBones.filter((name, index) => /(?:bust|bnip)/i.test(name) && remap[index] !== undefined)
          mount.dataset.runtimeSkinSourceBones = String(Number(mount.dataset.runtimeSkinSourceBones || 0) + sourceSkinBones.length)
          mount.dataset.runtimeSkinMappedBones = String(Number(mount.dataset.runtimeSkinMappedBones || 0) + mappedSkinBones.length)
          mount.dataset.runtimeSkinSourceBustBones = String(Number(mount.dataset.runtimeSkinSourceBustBones || 0) + sourceBustBones.length)
          mount.dataset.runtimeSkinMappedBustBones = String(Number(mount.dataset.runtimeSkinMappedBustBones || 0) + mappedBustBones.length)
        }
        const extendedBones = [...bindingBones]
        // Exported payloads append readable, unweighted Unity ancestors after
        // the renderer's actual bone prefix.  A wrapper such as
        // `p_cf_body_00_low` is useful for path matching but is not itself a
        // skinning input.  Creating every unmatched wrapper made otherwise
        // valid renderers fail.  Materialize only an unmatched skin bone and
        // the unresolved ancestors needed to reach it.
        const pendingPrivate = collectRequiredPrivateBoneIndices(remap, sourceParents, skinBoneCount)
        const createdPrivateBones = stagedPrivateBones
        let progressed = true
        while (pendingPrivate.size && progressed) {
          progressed = false
          for (const sourceIndex of [...pendingPrivate]) {
            const parentSourceIndex = Number(sourceParents[sourceIndex])
            if (Number.isInteger(parentSourceIndex) && parentSourceIndex >= 0 && remap[parentSourceIndex] === undefined) continue
            const sourcePath = runtimeBonePathSegments(sourceBonePaths[sourceIndex])
            const parentPath = sourcePath.slice(0, -1).join("/")
            const parentTargetIndex = resolveRuntimeBoneRemap(
              [String(sourceParentNames[sourceIndex] || "")],
              [parentPath],
              targetBoneRecords,
            )[0]
            const parentBone = Number.isInteger(parentSourceIndex) && parentSourceIndex >= 0
              ? extendedBones[Number(remap[parentSourceIndex])]
              : parentTargetIndex !== undefined
                ? extendedBones[parentTargetIndex]
                : findUniqueBoneByExactName(sourceParentNames[sourceIndex]) || exactAttachmentBone
            if (!(parentBone instanceof THREE.Bone)) continue
            const privateBone = new THREE.Bone()
            privateBone.name = sourceBoneNames[sourceIndex]
            const matrixValues = sourceLocalMatrices.slice(sourceIndex * 16, sourceIndex * 16 + 16)
            if (matrixValues.length === 16 && matrixValues.every(Number.isFinite)) {
              const local = new THREE.Matrix4().fromArray(matrixValues)
              local.decompose(privateBone.position, privateBone.quaternion, privateBone.scale)
            }
            parentBone.add(privateBone)
            privateBone.updateMatrixWorld(true)
            remap[sourceIndex] = extendedBones.length
            extendedBones.push(privateBone)
            extraPrivateBones.add(privateBone)
            createdPrivateBones.push(privateBone)
            pendingPrivate.delete(sourceIndex)
            progressed = true
          }
        }
        if (pendingPrivate.size) {
          const missing = [...pendingPrivate].map(index => sourceBoneNames[index])
          mount.dataset.runtimeSkinMissing = `${item.id}:${missing.slice(0, 8).join(",")}`
          throw new Error(`missing bone hierarchy ${missing.length}`)
        }
        if (neutralManikin && createdPrivateBones.length) {
          // Some split body renderers expose an extra weighted child that was
          // not present in the first renderer's hierarchy. It is still body
          // state, so promote it into the persistent core instead of letting
          // card cleanup detach a bone used by the retained body skeleton.
          createdPrivateBones.forEach(bone => {
            extraPrivateBones.delete(bone)
            runtimeCoreBones.add(bone)
            if (!runtimeSharedBones.includes(bone)) runtimeSharedBones.push(bone)
            if (!stagedCoreBones.includes(bone)) stagedCoreBones.push(bone)
            runtimeBasePositions.set(bone, bone.position.clone())
            runtimeBaseRotations.set(bone, bone.rotation.clone())
            runtimeBaseScales.set(bone, bone.scale.clone())
          })
          rebuildRuntimeShapeTargets()
          mount.dataset.runtimeSharedBoneCount = String(runtimeSharedBones.length)
        }
        else if (nativeHead && createdPrivateBones.length) {
          // The first native face shell expands the shared body hierarchy with
          // KKS's real face bones. Subsequent eye/line/mouth renderers and hair
          // then bind to those exact Bone objects instead of creating one
          // duplicate face rig per renderer.
          createdPrivateBones.forEach(bone => {
            if (!runtimeSharedBones.includes(bone)) runtimeSharedBones.push(bone)
            runtimeBasePositions.set(bone, bone.position.clone())
            runtimeBaseRotations.set(bone, bone.rotation.clone())
            runtimeBaseScales.set(bone, bone.scale.clone())
          })
          rebuildRuntimeShapeTargets()
          mount.dataset.runtimeSharedBoneCount = String(runtimeSharedBones.length)
        }
        else if (privateSlotKey && createdPrivateBones.length) {
          const slotBones = runtimePrivateBonesBySlot.get(privateSlotKey) || []
          createdPrivateBones.forEach(bone => {
            if (!slotBones.includes(bone)) slotBones.push(bone)
            runtimeBasePositions.set(bone, bone.position.clone())
            runtimeBaseRotations.set(bone, bone.rotation.clone())
            runtimeBaseScales.set(bone, bone.scale.clone())
          })
          runtimePrivateBonesBySlot.set(privateSlotKey, slotBones)
          mount.dataset.runtimePrivateSlotCount = String(runtimePrivateBonesBySlot.size)
        }
        const compactBinding = resolveRuntimeCompactSkinBinding(remap, sourceSkinIndices, skinBoneCount)
        const rendererBones = compactBinding.targetBoneIndices.map(index => extendedBones[index])
        if (rendererBones.some(bone => !(bone instanceof THREE.Bone))) throw new Error("invalid compact renderer bone")
        const skinIndices = compactBinding.skinIndices
        const geometry = new THREE.BufferGeometry()
        stagedGeometry = geometry
        geometry.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3))
        if (normals.length === vertexCount * 3) {
          geometry.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3))
        }
        geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2))
        const maxIndex = indices.reduce((maximum, value) => Math.max(maximum, Number(value) || 0), 0)
        geometry.setIndex(maxIndex > 65535 ? new THREE.Uint32BufferAttribute(indices, 1) : new THREE.Uint16BufferAttribute(indices, 1))
        geometry.setAttribute("skinIndex", new THREE.Uint16BufferAttribute(skinIndices, 4))
        geometry.setAttribute("skinWeight", new THREE.Float32BufferAttribute(skinWeights, 4))
        if (!geometry.getAttribute("normal")) geometry.computeVertexNormals()
        const alphaCutout = /^(?:clothes|accessory|hair|head)$/.test(String(item.source_kind || ""))
        const skinBase = resolvedRuntimeSkin.slice(0, resolvedRuntimeSkin.lastIndexOf("/") + 1)
        const loadRuntimeTexture = async (relative?: string) => {
          if (!relative) return undefined
          try {
            if (!isExtraLoadCurrent(token)) return undefined
            const textureUrl = relative.startsWith("/") ? relative : `${skinBase}${encodeURIComponent(relative)}`
            const texture = await loadRuntimeTextureBounded(
              () => loadAbortableRuntimeTexture(textureUrl, signal),
              () => isExtraLoadCurrent(token),
              signal,
            )
            if (!isExtraLoadCurrent(token) || signal.aborted) { texture.dispose(); return undefined }
            texture.flipY = false
            texture.colorSpace = THREE.SRGBColorSpace
            return texture
          } catch { return undefined }
        }
        const materialSpecs = payload.materials || []
        const authoredPatterns = runtimeAppearancePatternSlots(item)
        // The white validation shell deliberately has no authored appearance.
        // Avoid downloading, decoding and then discarding body/head textures;
        // those unused maps previously survived without a material owner.
        const skipTexturesForNeutralShell = neutralOnlyRef.current && (neutralManikin || nativeHead)
        const nativeClothingTextures = runtimeClothingTextureSet(item)
        const nativeClothingMainMap = !skipTexturesForNeutralShell && nativeClothingTextures?.mainTexture
          ? await loadRuntimeTexture(nativeClothingTextures.mainTexture)
          : undefined
        const nativeClothingColorMask = !skipTexturesForNeutralShell && nativeClothingTextures?.colorMask
          ? await loadRuntimeTexture(nativeClothingTextures.colorMask)
          : undefined
        if (nativeClothingColorMask) {
          nativeClothingColorMask.colorSpace = THREE.NoColorSpace
          nativeClothingColorMask.needsUpdate = true
        }
        const detailMaskMaps = skipTexturesForNeutralShell
          ? []
          : materialSpecs.length
            ? await Promise.all(materialSpecs.map(spec => {
                const override = resolveRuntimeMaterialTextureOverride(
                  item.materialTextureOverrides,
                  String(spec.name || ""),
                  "DetailMask",
                )
                const url = override?.url || spec.detailMask
                return url ? loadRuntimeTexture(url) : Promise.resolve(undefined)
              }))
            : []
        detailMaskMaps.forEach(mask => {
          if (!mask) return
          mask.colorSpace = THREE.NoColorSpace
          mask.needsUpdate = true
        })
        const materialMaps = skipTexturesForNeutralShell
          ? (materialSpecs.length ? materialSpecs.map(() => undefined) : [undefined])
          : materialSpecs.length
            ? await Promise.all(materialSpecs.map(async spec => {
                const override = resolveRuntimeMaterialTextureOverride(
                  item.materialTextureOverrides,
                  String(spec.name || ""),
                )
                const texture = nativeClothingMainMap || await loadRuntimeTexture(override?.url || spec.texture)
                if (texture && override) {
                  const offset = Array.isArray(override.offset) ? override.offset.map(Number) : []
                  const scale = Array.isArray(override.scale) ? override.scale.map(Number) : []
                  if (offset.length >= 2 && offset.slice(0, 2).every(Number.isFinite)) texture.offset.set(offset[0], offset[1])
                  if (scale.length >= 2 && scale.slice(0, 2).every(Number.isFinite)) texture.repeat.set(scale[0], scale[1])
                  if (offset.length >= 2 || scale.length >= 2) {
                    texture.wrapS = THREE.RepeatWrapping
                    texture.wrapT = THREE.RepeatWrapping
                    texture.needsUpdate = true
                  }
                }
                return texture
              }))
            : [await loadExtraTexture(base, item, token, signal)]
        const colorMaskMaps = skipTexturesForNeutralShell
          ? []
          : materialSpecs.length
            ? await Promise.all(materialSpecs.map(spec => {
                const override = resolveRuntimeMaterialTextureOverride(
                  item.materialTextureOverrides,
                  String(spec.name || ""),
                  "ColorMask",
                )
                return override?.url
                  ? loadRuntimeTexture(override.url)
                  : nativeClothingColorMask || loadRuntimeTexture(spec.colorMask)
              }))
            : []
        colorMaskMaps.forEach(mask => {
          if (!mask) return
          mask.colorSpace = THREE.NoColorSpace
          mask.needsUpdate = true
        })
        const materialAlphaMaskMaps = skipTexturesForNeutralShell
          ? []
          : materialSpecs.length
            ? await Promise.all(materialSpecs.map(spec => {
                const override = resolveRuntimeMaterialTextureOverride(
                  item.materialTextureOverrides,
                  String(spec.name || ""),
                  "AlphaMask",
                )
                return loadRuntimeTexture(override?.url || spec.alphaMask)
              }))
            : []
        materialAlphaMaskMaps.forEach(mask => {
          if (!mask) return
          mask.colorSpace = THREE.NoColorSpace
          mask.needsUpdate = true
        })
        const gpuPatternCapable = !skipTexturesForNeutralShell && colorMaskMaps.some(Boolean) && materialSpecs.some(spec =>
          ["kks-skin", "kks-hair", "kks-toon", "mtoon", "third-party-toon"].includes(resolveRuntimeShaderFamily(spec)),
        )
        const patternMaps = gpuPatternCapable
          ? await Promise.all(authoredPatterns.map(pattern => loadRuntimeTexture(pattern.texture)))
          : []
        patternMaps.forEach(pattern => {
          if (!pattern) return
          pattern.wrapS = THREE.RepeatWrapping
          pattern.wrapT = THREE.RepeatWrapping
          pattern.needsUpdate = true
        })
        const alphaMaskMap = !neutralOnlyRef.current && neutralManikin && item.alphaMaskOverride
          ? await loadRuntimeTexture(item.alphaMaskOverride)
          : undefined
        if (alphaMaskMap) {
          // KKS masks encode visibility in the green channel. Alpha maps are
          // data textures, not colour textures; keeping them out of sRGB avoids
          // a softened threshold while still requiring no browser pixel pass.
          alphaMaskMap.colorSpace = THREE.NoColorSpace
          alphaMaskMap.wrapS = THREE.ClampToEdgeWrapping
          alphaMaskMap.wrapT = THREE.ClampToEdgeWrapping
          alphaMaskMap.needsUpdate = true
        }
        const authoredColors = runtimeAppearanceColors(item)
        const syntheticWhiteBases = materialMaps.map(map => Boolean(map && runtimeTextureIsSyntheticWhite(map)))
        let appliedColorMasks = 0
        const gpuColorMaskIndices = new Set<number>()
        const colorComposedMaps = materialMaps.map((map, index) => {
          const mask = colorMaskMaps[index]
          if (!map || !mask) return map
          const family = resolveRuntimeShaderFamily(materialSpecs[index] || {})
          if (["kks-skin", "kks-hair", "kks-toon", "mtoon", "third-party-toon"].includes(family)) {
            gpuColorMaskIndices.add(index)
            return map
          }
          const composed = composeRuntimeColorTexture(map, mask, authoredColors, reason => {
            if (!isExtraLoadCurrent(token)) return
            mount.dataset.runtimeColorMaskState = reason
            mount.dataset.runtimeColorMasksSkipped = String(Number(mount.dataset.runtimeColorMasksSkipped || 0) + 1)
          })
          if (!composed) return map
          appliedColorMasks += 1
          map.dispose()
          mask.dispose()
          return composed
        })
        colorMaskMaps.forEach((mask, index) => {
          if (mask && !gpuColorMaskIndices.has(index) && colorComposedMaps[index] === materialMaps[index]) mask.dispose()
        })
        const detailCompositeIndices = new Set<number>()
        const detailComposedMaps = colorComposedMaps.map((map, index) => {
          const detailMask = detailMaskMaps[index]
          if (!detailMask || String(item.source_kind || "") !== "head") return map
          const appearance = nativeAppearanceRef.current
          const skinMain = rgbaFromUnknown(appearance?.skin_main_color)
          const skinSub = rgbaFromUnknown(appearance?.skin_sub_color)
          if (!skinMain || !skinSub) return map
          const composed = composeKksFaceDetailTexture(detailMask, skinMain, skinSub)
          detailMask.dispose()
          if (!composed) return map
          detailCompositeIndices.add(index)
          if (map) map.dispose()
          return composed
        })
        const resolvedMaterialMaps = detailComposedMaps.map((map, index) => {
          if (!map || !runtimeMaterialNeedsColorKeyAlpha(materialSpecs[index])) return map
          const keyed = composeRuntimeColorKeyAlphaTexture(map)
          if (!keyed) return map
          map.dispose()
          return keyed
        })
        if (appliedColorMasks && isExtraLoadCurrent(token)) {
          mount.dataset.runtimeColorMasks = String(Number(mount.dataset.runtimeColorMasks || 0) + appliedColorMasks)
        }
        const authoredColor = runtimeAppearancePrimaryColor(item)
        let gpuPatternMapsUsed = false
        const makeMaterial = (
          map: THREE.Texture | undefined,
          spec: RuntimeMaterialPayload = {},
          colorMaskApplied = false,
          syntheticWhiteBase = false,
          colorMask?: THREE.Texture,
          materialAlphaMask?: THREE.Texture,
          detailComposite = false,
        ) => {
          if (neutralOnlyRef.current && (neutralManikin || nativeHead)) return new THREE.MeshBasicMaterial({ color: 0xdfe4ec, side: THREE.DoubleSide })
          // Mod authors often ship a fully coloured main texture (hair cards
          // are a common example). Applying the card tint again makes blonde
          // hair brown. Only tint neutral/grayscale maps; RGB mask composites
          // and already-chromatic maps are complete colour results.
          const precoloredMap = Boolean(map && (detailComposite || runtimeTextureIsPrecolored(map)))
          const materialName = String(spec.name || "")
          const shaderOverride = resolveRuntimeMaterialShaderOverride(
            item.materialShaderOverrides,
            materialName,
          )
          const effectiveSpec = shaderOverride?.shader
            ? { ...spec, shaderName: shaderOverride.shader }
            : spec
          const overrideColor = (...properties: string[]) => rgbaFromUnknown(
            resolveRuntimeMaterialPropertyOverride(item.materialPropertyOverrides, materialName, properties, "color")?.value,
          )
          const overrideFloat = (...properties: string[]) => {
            const value = resolveRuntimeMaterialPropertyOverride(item.materialPropertyOverrides, materialName, properties, "float")?.value
            const numeric = Number(value)
            return Number.isFinite(numeric) ? numeric : undefined
          }
          const materialMaskColors = authoredColors.map(color => [...color] as [number, number, number, number])
          ;[["Color1", "Color", "MainColor"], ["Color2"], ["Color3"], ["Color4"]].forEach((properties, index) => {
            const overridden = overrideColor(...properties)
            if (overridden) materialMaskColors[index] = overridden
          })
          const materialPrimaryColor = overrideColor("Color", "MainColor", "Color1") || authoredColor
          const shaderBaseColor = materialPrimaryColor || rgbaFromUnknown(spec.baseColor)
          const shaderShadeColor = overrideColor("ShadeColor") || rgbaFromUnknown(spec.shadeColor)
          const shaderRimColor = overrideColor("RimColor") || rgbaFromUnknown(spec.rimColor)
          // The persistent body material starts from an appearance-independent
          // baseline. applyNativeAppearance writes the active card colour after
          // loading; a later card switch can therefore restore this baseline
          // before applying the next card instead of retaining the old tint.
          // A composed ColorMask and a chromatic _MainTex already contain the
          // final card colour. Their material multiplier must stay white or
          // the browser applies the same tint twice.
          const color = materialPrimaryColor && !colorMaskApplied && !precoloredMap
            ? new THREE.Color().setRGB(materialPrimaryColor[0], materialPrimaryColor[1], materialPrimaryColor[2], THREE.SRGBColorSpace)
            : (colorMaskApplied || precoloredMap) && map
              ? new THREE.Color(0xffffff)
              : shaderBaseColor
                ? new THREE.Color().setRGB(shaderBaseColor[0], shaderBaseColor[1], shaderBaseColor[2])
                : new THREE.Color(map ? 0xffffff : neutralManikin ? 0xffd7c7 : item.source_kind === "hair" ? 0xffd08a : item.source_kind === "clothes" ? 0xd5c7d8 : 0x8d6c72)
          const authoredOpacity = materialPrimaryColor?.[3] ?? shaderBaseColor?.[3] ?? 1
          const runtimeAlphaMode = resolveRuntimeAlphaMode(effectiveSpec)
          const usesCutout = Boolean(alphaMaskMap || materialAlphaMask) || runtimeAlphaMode === "cutout" || (!runtimeAlphaMode && alphaCutout)
          const usesBlend = runtimeAlphaMode === "blend" || (!runtimeAlphaMode && authoredOpacity < .999)
          // Opaque/cutout KKS shaders can pack unrelated data into Color.a.
          // Only a blend path treats that component as material opacity.
          const opacity = usesBlend ? authoredOpacity : 1
          // The shared neutral body is a full skin shell that reaches into the
          // KKS head volume.  It must remain visible for neck/torso coverage,
          // but writing its depth would occlude the card-selected cf_O_face
          // (and therefore the baked eyes, nose and mouth) that is committed
          // immediately after it.  Keep the carrier shell depth-tested but
          // non-writing; clothing/native head layers then own the final depth.
          const depthWrite = neutralManikin
            ? false
            : typeof spec.depthWrite === "boolean" ? spec.depthWrite : !usesBlend
          const transparentWithZWrite = typeof spec.transparentWithZWrite === "boolean"
            ? spec.transparentWithZWrite
            : usesBlend && depthWrite
          const tagAlphaMode = <T extends THREE.Material>(material: T): T => {
            material.userData.runtimeAlphaMode = usesBlend ? "blend" : usesCutout ? "cutout" : "opaque"
            return material
          }
          const rawCutoff = Number(overrideFloat("Cutoff", "AlphaCutoff") ?? spec.cutoff ?? .04)
          const cutoff = Number.isFinite(rawCutoff) ? THREE.MathUtils.clamp(rawCutoff, .01, .95) : .04
          const shared = {
            color,
            ...(map ? { map } : {}),
            ...(alphaMaskMap ? { alphaMap: alphaMaskMap } : {}),
            side: THREE.DoubleSide,
            transparent: usesBlend,
            opacity,
            alphaTest: usesCutout ? cutoff : 0,
            depthWrite,
            // KKS clothes are authored almost coplanar with the body. Pull
            // their depth value forward by a tiny amount so the shared skin
            // does not punch circular holes through tops at large bust sizes.
            // This changes only raster depth, not the card's geometry.
            polygonOffset: item.source_kind === "clothes",
            polygonOffsetFactor: item.source_kind === "clothes" ? -1 : 0,
            polygonOffsetUnits: item.source_kind === "clothes" ? -1 : 0,
          }
          // Face sheets are order-sensitive unlit overlays; the diagnostic
          // white shell is intentionally cheap. Everything else is selected
          // by shader family, not by card or material naming conventions.
          const shaderFamily = resolveRuntimeShaderFamily(effectiveSpec)
          // `cf_m_tang` is a tiny authored mouth surface.  Its Unity
          // main_item shade colour is often a cool blue intended for the
          // game's lighting ramp; sending it through MToon makes the mouth a
          // grey blob on custom heads.  The exported base is intentionally
          // pale (the game darkens it in its item pass), so apply a shared
          // neutral reduction before the cheap unlit pass.
          if (item.source_kind === "head" && /(?:^|[_ .])cf_m_tang(?:[_ .]|$)/i.test(materialName)) {
            const material = tagAlphaMode(new THREE.MeshBasicMaterial({
              ...shared,
              color: color.clone().multiplyScalar(.5),
              toneMapped: false,
            }))
            if (shaderOverride) material.userData.runtimeShaderOverride = { ...shaderOverride }
            return material
          }
          if (shaderFamily === "kks-face-overlay") {
            const material = tagAlphaMode(new THREE.MeshBasicMaterial(shared))
            if (shaderOverride) material.userData.runtimeShaderOverride = { ...shaderOverride }
            return material
          }
          if (shaderFamily === "standard") {
            const material = tagAlphaMode(new THREE.MeshStandardMaterial({
              ...shared,
              roughness: .82,
              metalness: .02,
            }))
            if (shaderOverride) material.userData.runtimeShaderOverride = { ...shaderOverride }
            return material
          }
          if (["kks-skin", "kks-hair", "kks-toon", "mtoon", "third-party-toon"].includes(shaderFamily)) {
            const shade = runtimeShadeColorFactor(
              precoloredMap,
              shaderShadeColor,
              [color.r, color.g, color.b],
            )
            const shadeColorFactor = new THREE.Color().setRGB(...shade)
            const parametricRimColorFactor = shaderRimColor
              ? new THREE.Color().setRGB(shaderRimColor[0], shaderRimColor[1], shaderRimColor[2])
              : new THREE.Color(0x000000)
            const rawRimPower = Number(overrideFloat("RimFresnelPower", "RimPower") ?? spec.rimFresnelPower ?? 5)
            const rawRimLift = Number(overrideFloat("RimLift") ?? spec.rimLift ?? 0)
            const material = new MToonMaterial({
              ...shared,
              // Reuse the main texture in the shade branch so a toon band
              // preserves authored detail instead of becoming a flat block.
              ...(map ? { shadeMultiplyTexture: map } : {}),
              shadeColorFactor,
              shadingShiftFactor: 0,
              // Clothing color masks are already authored as final KKS
              // albedo. A hard .9 toon step turns smooth tops into large
              // purple/red facets in the browser; keep skin/hair crisp while
              // giving clothes a softer shared ramp.
              shadingToonyFactor: item.source_kind === "clothes" ? .58 : .9,
              giEqualizationFactor: item.source_kind === "clothes" ? .96 : .9,
              parametricRimColorFactor,
              rimLightingMixFactor: 1,
              parametricRimFresnelPowerFactor: Number.isFinite(rawRimPower) ? THREE.MathUtils.clamp(rawRimPower, .001, 100) : 5,
              parametricRimLiftFactor: Number.isFinite(rawRimLift) ? THREE.MathUtils.clamp(rawRimLift, -1, 1) : 0,
              transparentWithZWrite,
              outlineWidthMode: "none",
              outlineWidthFactor: 0,
              isOutline: false,
            })
            // three-vrm synchronizes alphaTest and getter-backed uniforms in
            // update(); one zero-delta call is enough because this bridge has
            // no per-frame UV animation.
            material.update(0)
            configureKksMtoonShader(material, {
              colorMask,
              colors: materialMaskColors,
              materialAlphaMask,
              alphaA: spec.alphaA,
              alphaB: spec.alphaB,
              bodyMask: alphaMaskMap,
              patterns: authoredPatterns.map((pattern, index) => ({ ...pattern, texture: patternMaps[index] })),
            })
            if (colorMask && patternMaps.some(Boolean)) gpuPatternMapsUsed = true
            const tagged = tagAlphaMode(material)
            if (shaderOverride) tagged.userData.runtimeShaderOverride = { ...shaderOverride }
            return tagged
          }
          // Unknown third-party shaders stay on the conservative legacy path:
          // mapped assets remain unlit and textureless assets keep one cheap
          // toon ramp. This avoids shader-specific guesses and blank meshes.
          const material = tagAlphaMode(syntheticWhiteBase || !map
            ? new THREE.MeshToonMaterial({ ...shared, gradientMap: toonGradient })
            : new THREE.MeshBasicMaterial(shared))
          if (shaderOverride) material.userData.runtimeShaderOverride = { ...shaderOverride }
          return material
        }
        const materials = resolvedMaterialMaps.map((map, index) => makeMaterial(
          map,
          materialSpecs[index],
          gpuColorMaskIndices.has(index) || Boolean(materialSpecs[index]?.colorMask && map !== materialMaps[index]),
          syntheticWhiteBases[index],
          colorMaskMaps[index],
          materialAlphaMaskMaps[index],
          detailCompositeIndices.has(index),
        ))
        if (!gpuPatternMapsUsed) patternMaps.forEach(pattern => pattern?.dispose())
        stagedMaterials = materials
        if (materialSpecs.length) {
          geometry.clearGroups()
          let groupCount = 0
          materialSpecs.forEach((spec, index) => {
            const start = Number(spec.start)
            const count = Number(spec.count)
            if (!Number.isFinite(start) || !Number.isFinite(count) || count <= 0) return
            geometry.addGroup(start, count, index)
            groupCount += 1
          })
          if (!groupCount) geometry.addGroup(0, indices.length, 0)
        }
        const material: THREE.Material | THREE.Material[] = materials.length === 1 ? materials[0] : materials
        const materialEditorRenderOrder = materialSpecs.reduce((maximum, spec) => {
          const override = resolveRuntimeMaterialShaderOverride(
            item.materialShaderOverrides,
            String(spec.name || ""),
          )
          return Math.max(maximum, resolveRuntimeMaterialEditorRenderOrder(override))
        }, 0)
        if (!isExtraLoadCurrent(token)) {
          geometry.dispose()
          disposeMaterialResources(materials)
          return null
        }
        materials.forEach((material, index) => {
          material.name = materialSpecs[index]?.name || `${item.source_kind || "runtime"}:${item.id}:${index}`
          material.needsUpdate = true
        })
        const isRigidAttachment = Boolean(item.rigidAttachment || sourceBoneNames.includes("__attachment__"))
        if (isRigidAttachment && !exactAttachmentBone && !unparentedAttachment) throw new Error(`missing attachment bone ${item.attachmentBone || "(empty)"}`)
        const object: THREE.Object3D = isRigidAttachment
          ? new THREE.Mesh(geometry, material)
          : new THREE.SkinnedMesh(geometry, material)
        // Every renderer shares the native KKS Bone objects, while preserving
        // its own inverse-bind matrices. This matches Unity's
        // SkinnedMeshRenderer contract without borrowing the PMX carrier's
        // character-specific bind frame.
        if (object instanceof THREE.SkinnedMesh) {
          const sourceBindPoses = payload.bindPoses || []
          const rendererInverseBinds = resolveRuntimeBindPoseAssignments(
            compactBinding.sourceToSkeleton,
            sourceBindPoses,
            rendererBones.length,
          )
          const inverseBinds = rendererInverseBinds.map(values => {
            if (!values) throw new Error("missing compact renderer inverse bind")
            return new THREE.Matrix4().fromArray(values)
          })
          const clothingSkeleton = new THREE.Skeleton(rendererBones, inverseBinds)
          stagedSkeleton = clothingSkeleton
          extraSkeletons.add(clothingSkeleton)
          if (neutralManikin) runtimeCoreSkeletons.add(clothingSkeleton)
          if (isExtraLoadCurrent(token)) {
            mount.dataset.runtimeSkeletonBoneTotal = String(Number(mount.dataset.runtimeSkeletonBoneTotal || 0) + rendererBones.length)
            mount.dataset.runtimeSkeletonBoneMax = String(Math.max(Number(mount.dataset.runtimeSkeletonBoneMax || 0), rendererBones.length))
          }
          object.bind(clothingSkeleton, new THREE.Matrix4())
          object.normalizeSkinWeights()
          if (item.source_kind === "accessory") {
            const adjustedMoveBones = applySkinnedAccessoryMoveBones(
              item.attachmentAdjustments,
              sourceBoneNames,
              remap,
              extendedBones,
            )
            if (adjustedMoveBones && isExtraLoadCurrent(token)) {
              mount.dataset.runtimeSkinnedAdjustmentCount = String(Number(mount.dataset.runtimeSkinnedAdjustmentCount || 0) + adjustedMoveBones)
            }
          }
        }
        // Rigid renderers need their serialized GameObject placement. Skinned
        // renderer bind poses already contain that placement (the vanilla body
        // resolves to a 1.1435m renderer transform at rest), so applying
        // `payload.position` to the SkinnedMesh would translate it twice and
        // put the bust over the face.
        const rootPosition = item.position && item.position.length === 3
          ? item.position
          : payload.position && payload.position.length === 3
            ? payload.position
            : undefined
        if (isRigidAttachment && group && (exactAttachmentBone || unparentedAttachment)) {
          mesh?.updateMatrixWorld(true)
          group.updateMatrixWorld(true)
          const rendererPlacement = payload.rendererMatrix?.length === 16
            ? payload.rendererMatrix
            : payload.attachmentMatrix
          const attachment = kksRigidRendererPlacement(
            payload.transformChain,
            item.attachmentAdjustments,
            rendererPlacement,
          )
          const local = exactAttachmentBone
            ? group.matrixWorld.clone().invert().multiply(exactAttachmentBone.matrixWorld).multiply(attachment)
            : attachment
          local.decompose(object.position, object.quaternion, object.scale)
          if (exactAttachmentBone) object.userData.attachmentBone = item.attachmentBone
          if (unparentedAttachment) object.userData.unparentedAttachment = true
        } else if (object instanceof THREE.SkinnedMesh) {
          object.position.set(0, 0, 0)
          object.quaternion.identity()
          object.scale.set(1, 1, 1)
        }
        else if (rootPosition) object.position.set(rootPosition[0], rootPosition[1], rootPosition[2])
        else object.position.set(0, 0, 0)
        object.userData.extraId = item.id
        object.userData.extraItem = item
        object.userData.extraSkinned = !isRigidAttachment
        object.userData.rigidAttachment = isRigidAttachment
        object.userData.runtimeSkin = true
        if (nativeHead) {
          let semanticRenderOrder = 0
          materials.forEach((value, index) => {
            const semanticName = String(materialSpecs[index]?.name || value.name || "")
            const layerPolicy = kksNativeHeadLayerPolicy(semanticName)
            semanticRenderOrder = Math.max(semanticRenderOrder, layerPolicy.renderOrder)
            const faceLayer = value as THREE.MeshBasicMaterial
            faceLayer.depthTest = true
            faceLayer.depthWrite = layerPolicy.depthWrite
            if (layerPolicy.polygonOffsetFactor) {
              faceLayer.polygonOffset = true
              faceLayer.polygonOffsetFactor = layerPolicy.polygonOffsetFactor
              faceLayer.polygonOffsetUnits = faceLayer.polygonOffsetFactor
            } else {
              faceLayer.polygonOffset = false
              faceLayer.polygonOffsetFactor = 0
              faceLayer.polygonOffsetUnits = 0
            }
            faceLayer.needsUpdate = true
          })
          object.renderOrder = semanticRenderOrder
        }
        // Clothing is intentionally rendered just in front of the body.  The
        // two meshes share bind-space surfaces, so without a small depth
        // offset the PMX body wins the depth test and hides the chest panels.
        if (!nativeHead) object.renderOrder = 5
        object.renderOrder = Math.max(object.renderOrder, materialEditorRenderOrder)
        object.frustumCulled = false
        if (isExtraLoadCurrent(token)) mount.dataset.runtimeSkinLoaded = String(Number(mount.dataset.runtimeSkinLoaded || 0) + 1)
        committed = true
        return object
      } catch (error) {
        if (!committed) {
          stagedGeometry?.dispose()
          disposeMaterialResources(stagedMaterials)
          if (stagedSkeleton) {
            stagedSkeleton.dispose()
            extraSkeletons.delete(stagedSkeleton)
            runtimeCoreSkeletons.delete(stagedSkeleton)
          }
          stagedPrivateBones.forEach(bone => {
            bone.removeFromParent()
            extraPrivateBones.delete(bone)
            const sharedIndex = runtimeSharedBones.indexOf(bone)
            if (sharedIndex >= 0) runtimeSharedBones.splice(sharedIndex, 1)
            runtimeBasePositions.delete(bone)
            runtimeBaseRotations.delete(bone)
            runtimeBaseScales.delete(bone)
          })
          runtimePrivateBonesBySlot.forEach((bones, slot) => {
            const retained = bones.filter(bone => !stagedPrivateBones.includes(bone))
            if (retained.length) runtimePrivateBonesBySlot.set(slot, retained)
            else runtimePrivateBonesBySlot.delete(slot)
          })
          stagedCoreBones.forEach(bone => {
            bone.removeFromParent()
            runtimeCoreBones.delete(bone)
            const sharedIndex = runtimeSharedBones.indexOf(bone)
            if (sharedIndex >= 0) runtimeSharedBones.splice(sharedIndex, 1)
            runtimeBasePositions.delete(bone)
            runtimeBaseRotations.delete(bone)
            runtimeBaseScales.delete(bone)
          })
          if (stagedPrivateBones.length || stagedCoreBones.length) rebuildRuntimeShapeTargets()
        }
        const reason = error instanceof Error ? error.message : String(error)
        if (isExtraLoadCurrent(token)) mount.dataset.runtimeSkinErrors = `${String(Number(mount.dataset.runtimeSkinErrors?.split(":")[0] || 0) + 1)}:${item.id}:${reason.slice(0, 96)}`
        return null
      }
    }
    const loadLocalExtrasFast = async (
      manifestOverride?: string,
      payloadOverride?: RuntimeManifestPayload,
    ) => {
      // This load generation owns one coherent neutral/card state. A later
      // toggle aborts it and starts another generation on the same renderer.
      const neutralOnly = neutralOnlyRef.current
      // Neutral mode is itself backed by the shared-body runtime manifest.
      // It must load even before a card enables card-specific extras; otherwise
      // the hidden PMX carrier leaves only whichever face mesh escaped the
      // carrier filter and the supposed white manikin appears as a lone head.
      if (!extrasEnabledRef.current && !manifestOverride && !hasNativeKkExport && !neutralOnly && !nativeRuntimeTemplate) { mount.dataset.extraState = "disabled"; mount.dataset.extraCount = "0"; return }
      extraLoadAbortController?.abort()
      const loadAbortController = new AbortController()
      extraLoadAbortController = loadAbortController
      const token = ++extraLoadToken
      const reportHeadState = (state: NativeHeadRuntimeState) => {
        if (!isExtraLoadCurrent(token) || loadAbortController.signal.aborted) return
        mount.dataset.nativeHeadAtomicState = state.status
        mount.dataset.nativeHeadAtomicReason = state.reason || ""
        onNativeHeadStateChangeRef.current?.(state)
      }
      const loadDefaultHeadDriver = () => loadBundledKksHeadShapeDriver(
        KKS_DEFAULT_FEMALE_HEAD_ID,
        loadAbortController.signal,
      )
      const commitHeadDriver = (
        driver: KksHeadShapeDriverData,
        useDefaultFace: boolean,
        profile: string | number,
      ) => {
        if (!isExtraLoadCurrent(token) || loadAbortController.signal.aborted) return false
        headShapeDriverRef.current = driver
        headUsesDefaultFaceRef.current = useDefaultFace
        shapeDirtyRef.current = true
        mount.dataset.nativeHeadId = String(useDefaultFace ? KKS_DEFAULT_FEMALE_HEAD_ID : nativeHeadIdRef.current ?? "")
        mount.dataset.nativeHeadProfile = String(profile)
        mount.dataset.headShapeDriverVersion = String(driver.version)
        return true
      }
      headShapeDriverRef.current = undefined
      headUsesDefaultFaceRef.current = true
      shapeDirtyRef.current = true
      reportHeadState({ status: "loading" })
      mount.dataset.runtimeSkinAttempted = "0"
      mount.dataset.runtimeSkinLoaded = "0"
      mount.dataset.runtimeSkinErrors = ""
      mount.dataset.runtimeSkinMissing = ""
      mount.dataset.runtimeColorMasks = "0"
      mount.dataset.runtimeColorMasksSkipped = "0"
      mount.dataset.runtimeColorMaskState = "ready"
      mount.dataset.runtimeSkinSourceBones = "0"
      mount.dataset.runtimeSkinMappedBones = "0"
      mount.dataset.runtimeSkinSourceBustBones = "0"
      mount.dataset.runtimeSkinMappedBustBones = "0"
      mount.dataset.runtimeSkeletonBoneTotal = "0"
      mount.dataset.runtimeSkeletonBoneMax = "0"
      if (hasNativeKkExport && !extrasManifestRef.current && !manifestOverride && !neutralOnly) {
        // This model already owns the complete KKS body/head/outfit. Remove
        // diagnostics left by a previously selected shared-shell card instead
        // of making the DOM claim that both pipelines are active.
        delete mount.dataset.extraManifestUrl
        delete mount.dataset.runtimeShellFallback
        delete mount.dataset.runtimeHeadFallbackReason
        mount.dataset.extraState = "native-pmx"
        mount.dataset.extraCount = "0"
        return
      }
      if (nativeRuntimeTemplate) {
        clearRuntimeCardState()
        // A cancelled/failed first generation may have staged body bones but no
        // complete renderer. Never promote that partial core into the next card.
        if (!runtimeCoreReady && (runtimeCoreGroup || runtimeCoreBones.size || runtimeCoreObjects.size)) {
          disposeRuntimeCoreState()
        }
        mount.dataset.runtimeCoreReuse = runtimeCoreReady ? "reused" : "cold"
      } else {
        clearRuntimeFaceTextures()
        if (extrasGroup) { scene.remove(extrasGroup); disposeExtraObject(extrasGroup); extrasGroup = null }
        runtimeCardGroup = null
        runtimeCoreGroup = null
        runtimeCoreReady = false
        runtimeCoreObjects.clear()
        runtimeCoreSkeletons.clear()
        runtimeCoreBones.clear()
        runtimeCoreMaterialBaselines.clear()
        extraObjects.length = 0
        extraBoneBindings.clear()
        bindBoneWorld.clear()
        extraSkeletons.forEach(skeleton => skeleton.dispose())
        extraSkeletons.clear()
        extraPrivateBones.forEach(bone => bone.removeFromParent())
        extraPrivateBones.clear()
        runtimeSharedBones.length = 0
        runtimeBodyShapeTargetBones.clear()
        runtimeBasePositions.clear()
        runtimeBaseRotations.clear()
        runtimeBaseScales.clear()
        runtimePrivateBonesBySlot.clear()
        previousAbmxTouchedBones.clear()
        materializedRuntimeHierarchies.clear()
        runtimeNativeBodyReady = false
        runtimeNativeHeadReady = false
        syncCarrierVisibility()
      }
      mount.dataset.runtimeMtoonMaterialCount = String(countRuntimeMtoonMaterials(runtimeCoreGroup))
      const base = modelUrl.slice(0, modelUrl.lastIndexOf("/") + 1)
      try {
        const fallbackManifest = nativeRuntimeTemplate || /\/api\/koikatsu-cards\/local\//.test(base) ? undefined : `${base}extras/manifest.json`
        const useSharedNativeShell = nativeRuntimeTemplate && !neutralOnly && !manifestOverride && !extrasManifestRef.current
        const manifestUrl = neutralOnly || useSharedNativeShell
          ? "/api/koikatsu-cards/shared-base/neutral-template"
          : (manifestOverride || extrasManifestRef.current || fallbackManifest)
        if (!manifestUrl) {
          mount.dataset.extraState = "unavailable"
          mount.dataset.extraCount = "0"
          reportHeadState({ status: "degraded", reason: "头部运行清单不可用", fallbackApplied: false })
          return
        }
        let payload: RuntimeManifestPayload
        let manifestUsesFallbackHead = neutralOnly || useSharedNativeShell
        let headFallbackReason = ""
        let headUvTextureFallback = false
        let headFaceCompositeFallback = false
        let pendingHeadDriver: KksHeadShapeDriverData | undefined
        let pendingHeadProfile: string | number = KKS_DEFAULT_FEMALE_HEAD_ID
        if (neutralOnly || useSharedNativeShell) {
          payload = await loadSharedNativeShellPayload()
        } else if (payloadOverride) {
          // The editor already fetched this response to decide head fallback
          // and report missing resources. Reuse that immutable snapshot so a
          // single card click never repeats parsing/resolution on the server.
          payload = payloadOverride
        } else {
          const manifestRequestUrl = `${manifestUrl}${manifestUrl.includes("?") ? "&" : "?"}v=runtime-manifest-3`
          const response = await fetch(manifestRequestUrl, { signal: loadAbortController.signal })
          if (!response.ok) {
            mount.dataset.extraState = "unavailable"
            reportHeadState({ status: "degraded", reason: `头部运行清单 HTTP ${response.status}`, fallbackApplied: false })
            return
          }
          if (!isExtraLoadCurrent(token) || loadAbortController.signal.aborted) return
          payload = await response.json() as RuntimeManifestPayload
        }
        if (nativeRuntimeTemplate) {
          const originalItems = payload.items || []
            const selectedMainFaceDeclared = originalItems.some(isRuntimeHeadMainFaceItem)
          if (!neutralOnly && !useSharedNativeShell) {
            const contract = payload.headRuntime
            const statusReady = String(contract?.status || "").toLowerCase() === "ready"
            const geometryReady = contract?.geometryResolved === true && selectedMainFaceDeclared
            const driverUrl = String(contract?.shapeDriverUrl || "").trim()
            const rawProfile = contract?.shapeDriverProfile
            const officialProfile = rawProfile === undefined || rawProfile === null
              ? undefined
              : rawProfile === 0 || rawProfile === 200 || rawProfile === 201
                || rawProfile === "0" || rawProfile === "200" || rawProfile === "201"
                ? Number(rawProfile)
                : Number.NaN
            const officialProfileValid = officialProfile === undefined
              || officialProfile === 0
              || officialProfile === 200
              || officialProfile === 201
            const uvOnlyMismatch = contract?.reason === "head-face-uv-contract-mismatch"
            const faceCompositeIncompatible = contract?.faceCompositeCompatible === false
            if (uvOnlyMismatch && geometryReady && driverUrl && officialProfileValid) {
              // A face atlas/UV mismatch is a geometry-level contract failure.
              // Do not mix that prefab with the canonical face overlays: use
              // the shared head so the mesh and UV space remain coherent.
              try {
                await loadKksHeadShapeDriver(
                  driverUrl,
                  officialProfile,
                  loadAbortController.signal,
                  contract?.digest,
                )
                headFallbackReason = "当前卡脸部 UV/材质契约不兼容，已切换共享头部"
              } catch (error) {
                if (!isExtraLoadCurrent(token) || loadAbortController.signal.aborted) return
                const detail = error instanceof Error ? error.message : "ShapeAnime driver unavailable"
                headFallbackReason = `当前卡的精确 ShapeAnime 曲线加载失败：${detail}`
              }
            } else if (faceCompositeIncompatible && geometryReady && driverUrl && officialProfileValid) {
              // A shader/material composite mismatch is narrower than a UV
              // mismatch. Keep the card's native head, eyes, nose and mouth,
              // but let the later face-material branch remove only unsafe
              // composite sheets. This preserves authored facial proportions.
              try {
                pendingHeadDriver = await loadKksHeadShapeDriver(
                  driverUrl,
                  officialProfile,
                  loadAbortController.signal,
                  contract?.digest,
                )
                pendingHeadProfile = officialProfile ?? `custom:${String(contract?.digest || "undigested").slice(0, 12)}`
                headFaceCompositeFallback = true
              } catch (error) {
                if (!isExtraLoadCurrent(token) || loadAbortController.signal.aborted) return
                const detail = error instanceof Error ? error.message : "ShapeAnime driver unavailable"
                headFallbackReason = `当前卡的精确 ShapeAnime 曲线加载失败：${detail}`
              }
            } else if (!statusReady || !geometryReady || !driverUrl || !officialProfileValid) {
              headFallbackReason = "当前卡的头部几何与 ShapeAnime 曲线未形成完整契约"
            } else {
              try {
                pendingHeadDriver = await loadKksHeadShapeDriver(
                  driverUrl,
                  officialProfile,
                  loadAbortController.signal,
                  contract?.digest,
                )
                pendingHeadProfile = officialProfile ?? `custom:${String(contract?.digest || "undigested").slice(0, 12)}`
              } catch (error) {
                if (!isExtraLoadCurrent(token) || loadAbortController.signal.aborted) return
                const detail = error instanceof Error ? error.message : "ShapeAnime driver unavailable"
                headFallbackReason = `当前卡的精确 ShapeAnime 曲线加载失败：${detail}`
              }
            }
          }

          if (neutralOnly || useSharedNativeShell || headFallbackReason) {
            manifestUsesFallbackHead = true
            if (headFallbackReason) {
              // Do not compose any fragment of a custom head with profile 0.
              // Strip the entire selected prefab, then add the shared head.
              const shared = await loadSharedNativeShellPayload()
              const completed = mergeRuntimeNativeShellItems(
                originalItems.filter(item => item.source_kind !== "head"),
                shared.items || [],
              )
              // The selected card's full-face atlas belongs to the rejected UV
              // space. Keep only independent sheets rendered by canonical
              // overlay meshes; the nose sheet is safe here because it is
              // applied to the shared nose mesh, while the face base and
              // composite outputs are never carried over.
              const safeFaceTextures = Object.fromEntries(
                Object.entries(payload.faceTextures || {}).filter(([slot]) =>
                  ![
                    "face_base",
                    "face_color_mask",
                    "face_composite",
                    "pupil_left_composite",
                    "pupil_right_composite",
                  ].includes(slot),
                ),
              )
              payload = { ...payload, items: completed.items, faceTextures: safeFaceTextures }
              mount.dataset.runtimeShellFallback = completed.fallbackBody ? "body+head-contract" : "head-contract"
              mount.dataset.runtimeHeadFallbackReason = headFallbackReason
            }
            pendingHeadDriver = await loadDefaultHeadDriver()
            pendingHeadProfile = KKS_DEFAULT_FEMALE_HEAD_ID
          } else if (headUvTextureFallback || headFaceCompositeFallback) {
            // Keep the selected head/rig, but never apply a canonical atlas to
            // a different UV layout.  Individual authored decal sheets remain
            // eligible and the native geometry continues to supply its face
            // cues (nose, mouth and eye overlays).
            const safeFaceTextures = Object.fromEntries(
              Object.entries(payload.faceTextures || {}).filter(([slot]) =>
                slot !== "face_composite"
                  && slot !== "pupil_left_composite"
                  && slot !== "pupil_right_composite",
              ),
            )
            payload = { ...payload, faceTextures: safeFaceTextures }
            mount.dataset.runtimeShellFallback = headUvTextureFallback ? "head-face-uv-texture" : "head-face-material"
            mount.dataset.runtimeHeadFallbackReason = "当前卡脸部 UV 与通用合成图不兼容，已保留原生头部并停用合成图"
          } else {
            // A missing body is independently replaceable; a selected head is
            // retained only because its exact driver already passed validation.
            const shared = originalItems.some(item => item.source_kind === "neutral_body")
              ? undefined
              : await loadSharedNativeShellPayload()
            if (shared) {
              const completed = mergeRuntimeNativeShellItems(originalItems, shared.items || [])
              payload = { ...payload, items: completed.items }
              mount.dataset.runtimeShellFallback = "body"
            }
          }
        }
        if (!isExtraLoadCurrent(token)) return
        latestFaceTextureUrls = payload.faceTextures || {}
        mount.dataset.extraManifestUrl = manifestUrl
        mount.dataset.runtimeSkinManifest = String((payload.items || []).filter(item => Boolean(item.runtimeSkin)).length)
        const group = nativeRuntimeTemplate ? (extrasGroup || new THREE.Group()) : new THREE.Group()
        if (!extrasGroup || !nativeRuntimeTemplate) {
          if (nativeRuntimeTemplate) group.position.set(0, 0, 0)
          else group.position.copy(modelOffset)
          extrasGroup = group
          scene.add(group)
        }
        const coreGroup = nativeRuntimeTemplate
          ? (runtimeCoreGroup || new THREE.Group())
          : group
        if (nativeRuntimeTemplate && !runtimeCoreGroup) {
          runtimeCoreGroup = coreGroup
          group.add(coreGroup)
        }
        const cardGroup = nativeRuntimeTemplate ? new THREE.Group() : group
        if (nativeRuntimeTemplate) {
          runtimeCardGroup = cardGroup
          group.add(cardGroup)
        }
        const objLoader = new OBJLoader()
        let fallbackHairCount = 0
        // Schema-versioned generated composites carry a neutral mouth contour.
        // The native `o_tang` bind pose is not safe here because its KKS mouth
        // blend shapes are not part of the compact runtime geometry.
        const hasBakedLipTexture = hasKksBakedLipReplacement(payload.faceTextures)
        const rawManifestItems = (payload.items || []).filter(item => {
          if (neutralOnly && !/^(?:neutral_body|head)$/.test(String(item.source_kind || ""))) return false
          // Only the generated face-composite route proves that the neutral
          // mouth has been baked and can replace the undeformed tongue mesh.
          if (item.source_kind === "head" && shouldSuppressKksNativeFaceItem(item.id, hasBakedLipTexture)) return false
          return true
        })
        // A custom main face prefab can carry its own nose/mouth surfaces.
        // The card also serializes the vanilla `cf_O_noseline`/`tang` helpers;
        // attaching both rigs to the custom head duplicates those surfaces
        // and, because their UV/bind contract is different, turns the nose
        // into the tall dark stripe seen on modded faces.  Keep the rule
        // semantic: only suppress the vanilla helpers when an authored
        // non-carrier main face is actually being retained.
        const retainedCustomMainFace = !manifestUsesFallbackHead
          && rawManifestItems.some(item => isRuntimeHeadMainFaceItem(item) && !/(?:^|:)cf_O_face$/i.test(String(item.id || "")))
        if (retainedCustomMainFace) mount.dataset.runtimeCustomFaceOverlayPolicy = "preserve-native-nose"
        const manifestItems = rawManifestItems.filter(item => {
          if (neutralOnly && item.source_kind === "head" && !/(?:^|:)cf_O_face$/i.test(item.id)) return false
          if (item.source_kind === "head" && shouldSuppressKksNativeFaceItem(item.id, hasBakedLipTexture)) return false
          // Neutral preview suppresses expression-only overlays (tears, gag
          // eyes, teeth and canines). Generated composites also suppress
          // `o_tang`; their baked contour is the neutral replacement.
          if (isRuntimeExpressionOnlyHeadItem(item)) return false
          return isRuntimeSemanticItemVisible(item)
        })
        mount.dataset.runtimeSkinVisible = String(manifestItems.filter(item => Boolean(item.runtimeSkin)).length)
        mount.dataset.runtimeLoadConcurrency = String(KKS_RUNTIME_GEOMETRY_CONCURRENCY)
        mount.dataset.extraState = "loading"
        mount.dataset.extraCount = String(runtimeCoreObjects.size)
        // Attach the group before decoding every renderer. Runtime payloads can
        // then appear progressively instead of leaving a floating carrier head
        // until the slowest accessory and colour mask has finished.
        const commitLoadedObject = (object: THREE.Object3D | null) => {
          if (!object) return null
          if (!isExtraLoadCurrent(token)) {
            // Card switches can invalidate a generation after its async
            // textures and GPU buffers have finished loading.  A stale object
            // never reaches a scene owner, so dispose it here instead of
            // leaking the new KKS mask/pattern uniforms until page teardown.
            disposeExtraObject(object)
            return null
          }
          const sourceItem = object.userData?.extraItem as RuntimeManifestItem | undefined
          const coreObject = nativeRuntimeTemplate && sourceItem?.source_kind === "neutral_body"
          const ownerGroup = coreObject ? coreGroup : cardGroup
          ownerGroup.add(object)
          extraObjects.push(object)
          if (coreObject) {
            runtimeCoreObjects.add(object)
            rememberRuntimeCoreMaterials(object)
            runtimeNativeBodyReady = true
          }
          if (sourceItem && isRuntimeHeadMainFaceItem(sourceItem)) runtimeNativeHeadReady = true
          syncCarrierVisibility()
          mount.dataset.extraCount = String(extraObjects.length)
          renderRequestRef.current()
          return object
        }
        const loadItem = async (item: RuntimeManifestItem): Promise<THREE.Object3D | null> => {
          try {
            if (!isExtraLoadCurrent(token)) return null
            // Keep accessory layers in the composite.  Their OBJ coordinates
            // are local to the exported accessory root and the manifest's
            // position is the native Unity root placement; dropping them
            // leaves the PMX body's nude arms visible through the outfit.
            // The extracted OBJ for a clothing renderer is already baked in
            // the PMX world bind pose.  Until a card's exact Unity bind-pose
            // matrices are available in the browser, applying a second
            // SkinnedMesh bind produces the stretched triangles seen in the
            // screenshot.  Keep runtime skinning for rigid/optional pieces;
            // render baked clothing rigidly at its exported coordinates.
            // Clothing and body accessories exported with Unity skin weights
            // must use the shared PMX skeleton.  Their payload vertices are
            // keep native local coordinates and bind poses; the rigid OBJ is retained
            // only for true accessory props without a compatible bone list.
            const runtimeOwnerGroup = nativeRuntimeTemplate && item.source_kind === "neutral_body" ? coreGroup : cardGroup
            const runtimeObject = await loadRuntimeSkinnedExtra(item, base, token, runtimeOwnerGroup, loadAbortController.signal)
            if (!isExtraLoadCurrent(token)) { if (runtimeObject) disposeExtraObject(runtimeObject); return null }
            // A skinned garment without a complete shared-bone binding is not
            // a valid fallback candidate. Rendering its OBJ rigidly is what
            // produced sleeves/gloves floating in the old preview; hide the
            // item until its bone contract is repaired instead.
            if (!runtimeObject && item.skinned) {
              mount.dataset.runtimeSkinRejected = `${String(Number(mount.dataset.runtimeSkinRejected || 0) + 1)}:${item.id}`
              return null
            }
            if (!runtimeObject && !item.url) return null
            const fallbackUrl = item.url || ""
            const object = runtimeObject || await loadRuntimeObjAbortable(
              objLoader,
              `${fallbackUrl}${fallbackUrl.includes("?") ? "&" : "?"}v=skinned-1`,
              loadAbortController.signal,
            )
            if (!isExtraLoadCurrent(token)) { disposeExtraObject(object); return null }
            if (!runtimeObject) {
              const hasUseful = item.position && hasUsefulPosition(item.position)
              if (item.skinned) object.position.set(0, 0, 0)
              else applyExtraPositionFallback(item, object, runtimeOwnerGroup)
              if (item.source_kind === "hair" && !hasUseful) fallbackHairCount += 1
            }
            object.userData.extraId = item.id
            object.userData.extraItem = item
            // `skinned` marks runtime payload success. If fallback OBJ is used,
            // keep it as a rigid piece and bind it to anchor bones so it stays
            // visible and follows the character pose.
              const runtimeLoaded = Boolean(runtimeObject)
              object.userData.extraSkinned = runtimeLoaded && !object.userData.rigidAttachment
              object.userData.runtimeSkin = Boolean(runtimeObject)
              object.renderOrder = runtimeLoaded ? 10 : 8
            if (!runtimeObject) {
              const map = await loadExtraTexture(base, item, token, loadAbortController.signal)
              object.traverse(node => {
                const target = node as THREE.Mesh
                if (!target.isMesh) return
                target.frustumCulled = false
              const color = map ? 0xffffff : item.source_kind === "hair" ? 0xffd08a : item.source_kind === "clothes" ? 0xd5c7d8 : 0x8d6c72
                target.material = new THREE.MeshStandardMaterial({
                  color,
                  ...(map ? { map } : {}),
                  side: THREE.DoubleSide,
                  transparent: false,
                  depthWrite: true,
                  roughness: 0.85,
                  metalness: 0.02,
                })
              })
            }
            return object
          } catch { return null }
        }
        // The neutral body defines the authoritative KKS hierarchy. Load it
        // first so every concurrently fetched hair/clothing renderer binds to
        // that hierarchy instead of racing onto the PMX carrier skeleton.
        const neutralItems = manifestItems.filter(item => item.source_kind === "neutral_body")
        const headItems = manifestItems
          .filter(item => item.source_kind === "head")
          .sort((left, right) => Number(!isRuntimeHeadMainFaceItem(left)) - Number(!isRuntimeHeadMainFaceItem(right)))
        const otherItems = manifestItems.filter(item => item.source_kind !== "neutral_body" && item.source_kind !== "head")
        const objects: Array<THREE.Object3D | null> = []
        if (nativeRuntimeTemplate && runtimeCoreReady) {
          runtimeNativeBodyReady = true
          objects.push(...runtimeCoreObjects)
          mount.dataset.runtimeCoreReuse = "reused"
        } else {
          const loadedNeutral: Array<THREE.Object3D | null> = []
          for (const item of neutralItems) {
            loadedNeutral.push(commitLoadedObject(await loadItem(item)))
            if (!isExtraLoadCurrent(token)) return
          }
          objects.push(...loadedNeutral)
          if (nativeRuntimeTemplate) {
            const completeCore = neutralItems.length > 0
              && loadedNeutral.every(Boolean)
              && runtimeNativeBodyReady
              && runtimeCoreBones.size > 0
              && runtimeCoreObjects.size === loadedNeutral.length
            if (!completeCore) {
              disposeRuntimeCoreState()
              throw new Error("native neutral body core incomplete")
            }
            runtimeCoreReady = true
            mount.dataset.runtimeCoreReuse = "loaded"
          }
        }
        // The neutral body establishes the shared skeleton. Apply the card body
        // once before garments arrive; subsequent skinned renderers bind to the
        // already-shaped bones and can be committed without another full pass.
        applyMorph()
        // Geometry and ShapeAnime are one commit unit. Stage the main face
        // first; only after it decodes successfully do we expose the prefab and
        // enable its exact 52-channel driver. Small overlays follow afterward.
        if (nativeRuntimeTemplate) {
          let activeHeadItems = headItems
          let activeDriver = pendingHeadDriver
          let activeProfile = pendingHeadProfile
          let usesDefaultHead = manifestUsesFallbackHead
          const loadHeadMain = async (items: RuntimeManifestItem[]) => {
            const main = items.find(isRuntimeHeadMainFaceItem)
            return main ? { item: main, object: await loadItem(main) } : { item: undefined, object: null }
          }
          let stagedMain = await loadHeadMain(activeHeadItems)
          if (!isExtraLoadCurrent(token)) {
            if (stagedMain.object) disposeExtraObject(stagedMain.object)
            return
          }
          if ((!activeDriver || !stagedMain.object) && !usesDefaultHead) {
            // The selected driver was valid, but geometry decoding is also part
            // of readiness. Discard the entire selected prefab and load the
            // shared 0-head + 0-driver pair; never retain selected overlays.
            if (stagedMain.object) disposeExtraObject(stagedMain.object)
            const failedMainName = String(stagedMain.item?.id || "主脸").split(":").pop() || "主脸"
            headFallbackReason = activeDriver
              ? `当前卡主脸 ${failedMainName} 加载失败`
              : "当前卡精确 ShapeAnime 曲线不可用"
            latestFaceTextureUrls = {}
            clearRuntimeFaceTextures()
            mount.dataset.runtimeShellFallback = "head-load-failure"
            mount.dataset.runtimeHeadFallbackReason = headFallbackReason
            const shared = await loadSharedNativeShellPayload()
            activeHeadItems = (shared.items || [])
              .filter(item => item.source_kind === "head")
              .filter(item => !isRuntimeExpressionOnlyHeadItem(item))
              .sort((left, right) => Number(!isRuntimeHeadMainFaceItem(left)) - Number(!isRuntimeHeadMainFaceItem(right)))
            activeDriver = await loadDefaultHeadDriver()
            activeProfile = KKS_DEFAULT_FEMALE_HEAD_ID
            usesDefaultHead = true
            stagedMain = await loadHeadMain(activeHeadItems)
          }
          if (!isExtraLoadCurrent(token)) {
            if (stagedMain.object) disposeExtraObject(stagedMain.object)
            return
          }
          if (!activeDriver || !stagedMain.item || !stagedMain.object) {
            reportHeadState({ status: "degraded", reason: headFallbackReason || "共享默认头部未完整加载", fallbackApplied: false })
            throw new Error("native head geometry/ShapeAnime atomic commit failed")
          }
          if (!commitHeadDriver(activeDriver, usesDefaultHead, activeProfile)) {
            disposeExtraObject(stagedMain.object)
            return
          }
          objects.push(commitLoadedObject(stagedMain.object))
          applyMorph()
          reportHeadState(headFallbackReason
            ? { status: "degraded", reason: headFallbackReason, profile: String(activeProfile), fallbackApplied: true }
            : { status: "ready", profile: String(activeProfile) })
          for (const item of activeHeadItems) {
            if (item === stagedMain.item) continue
            const loaded = commitLoadedObject(await loadItem(item))
            if (!isExtraLoadCurrent(token)) return
            objects.push(loaded)
          }
        } else {
          for (const item of headItems) {
            const loaded = commitLoadedObject(await loadItem(item))
            if (!isExtraLoadCurrent(token)) return
            objects.push(loaded)
          }
        }
        applyNativeAppearance()
        applyMorph()
        if (Object.keys(latestFaceTextureUrls).length) {
          await loadRuntimeFaceTextures(latestFaceTextureUrls, token, loadAbortController.signal)
        }
        const itemsBySemanticSlot = new Map<string, RuntimeManifestItem[]>()
        otherItems.forEach(item => {
          const key = `${String(item.source_kind || "asset")}:${String(item.source_property || item.id)}`
          const slotItems = itemsBySemanticSlot.get(key) || []
          slotItems.push(item)
          itemsBySemanticSlot.set(key, slotItems)
        })
        const semanticSlots = [...itemsBySemanticSlot.values()]
        const maxConcurrentRuntimeLoads = KKS_RUNTIME_GEOMETRY_CONCURRENCY
        for (let index = 0; index < semanticSlots.length; index += maxConcurrentRuntimeLoads) {
          if (!isExtraLoadCurrent(token)) return
          const batch = semanticSlots.slice(index, index + maxConcurrentRuntimeLoads)
          const loadedSlots = await Promise.all(batch.map(async slotItems => {
            const loaded: Array<THREE.Object3D | null> = []
            // One prefab can expose several renderers that share private hair
            // or accessory bones. Preserve source order within the slot.
            for (const item of slotItems) {
              loaded.push(commitLoadedObject(await loadItem(item)))
            }
            return loaded
          }))
          objects.push(...loadedSlots.flat())
        }
        if (!isExtraLoadCurrent(token)) return
        applyNativeAppearance()
        // Runtime bones are created after the PMX finishes its first morph
        // pass. Reapply the card's 44 native body values and relaxed arm pose
        // now, before measuring bounds or drawing the newly attached meshes.
        applyMorph()
        // A reused root still carries the previous card's presentation offset.
        // Measure the new composition in authored local space so centering is
        // absolute rather than accumulating a translation on every card.
        if (nativeRuntimeTemplate) group.position.set(0, 0, 0)
        group.updateMatrixWorld(true)
        let loadedBounds = new THREE.Box3().setFromObject(group)
        if (nativeRuntimeTemplate && !loadedBounds.isEmpty()) {
          const center = loadedBounds.getCenter(new THREE.Vector3())
          modelHeight = Math.max(.85, loadedBounds.max.y - loadedBounds.min.y)
          modelOffset.set(-center.x, -loadedBounds.min.y + modelHeight * .08, -center.z)
          group.position.copy(modelOffset)
          group.updateMatrixWorld(true)
          loadedBounds = new THREE.Box3().setFromObject(group)
          mount.dataset.modelHeight = modelHeight.toFixed(3)
        }
        mount.dataset.extraBounds = [loadedBounds.min.x, loadedBounds.min.y, loadedBounds.min.z, loadedBounds.max.x, loadedBounds.max.y, loadedBounds.max.z].map(value => value.toFixed(3)).join(",")
        mount.dataset.extraHairAutoOffset = String(fallbackHairCount)
        // Capture each rigid fallback piece in the same world bind space as
        // the PMX.  On later morph/pose updates we apply only the bone delta;
        // the original translation remains the OBJ export's own placement.
        mesh?.updateMatrixWorld(true)
        group.updateMatrixWorld(true)
        objects.filter(Boolean).forEach(object => {
          const target = object as THREE.Object3D
          if (target.userData?.extraSkinned || target.userData?.unparentedAttachment) return
          const bone = findBoneByExactName(String(target.userData?.attachmentBone || ""))
            || rigidBoneForExtra((target.userData?.extraItem || {}) as RuntimeManifestItem)
          if (!bone) return
          const bind = bindBoneWorld.get(bone) || bone.matrixWorld.clone()
          bindBoneWorld.set(bone, bind)
          extraBoneBindings.set(target, { bone, baseWorld: target.matrixWorld.clone(), bindBoneWorld: bind.clone() })
        })
        const nativeShellReady = !nativeRuntimeTemplate || (runtimeNativeBodyReady && runtimeNativeHeadReady)
        mount.dataset.runtimeMtoonMaterialCount = String(countRuntimeMtoonMaterials(extrasGroup))
        mount.dataset.modelState = nativeShellReady ? "ready" : "incomplete"
        mount.dataset.extraState = nativeShellReady ? "ready" : "partial"
        mount.dataset.extraCount = String(extraObjects.length)
        if (!nativeShellReady) {
          const missing = [runtimeNativeBodyReady ? "" : "body", runtimeNativeHeadReady ? "" : "head"].filter(Boolean).join("+")
          onErrorRef.current?.(`通用原生模型未完整装配：缺少 ${missing}`)
        }
        renderRequestRef.current()
      } catch (error) {
        if (!isExtraLoadCurrent(token)) return
        mount.dataset.extraState = "unavailable"
        mount.dataset.extraLoadError = error instanceof Error ? error.message.slice(0, 180) : String(error).slice(0, 180)
        reportHeadState({
          status: "degraded",
          reason: error instanceof Error ? error.message : "头部几何与 ShapeAnime 装配失败",
          fallbackApplied: false,
        })
        if (isExtraLoadCurrent(token)) {
          if (nativeRuntimeTemplate) {
            clearRuntimeCardState()
            if (!runtimeCoreReady) disposeRuntimeCoreState()
            mount.dataset.modelState = runtimeCoreReady ? "incomplete" : "unavailable"
          }
          onErrorRef.current?.(error instanceof Error ? error.message : "通用原生模型资源加载失败")
        }
      }
    }
    loadExtrasRef.current = (manifest, payload) => { if (!disposed) void loadLocalExtrasFast(manifest, payload) }
    if (nativeRuntimeTemplate) {
      // The native body manifest creates the one authoritative KKS skeleton;
      // no PMX request or character-specific carrier participates in runtime
      // cards. Assets remain progressive and rendering stays event-driven.
      carrierMaterialsReady = true
      renderReady = true
      mount.dataset.modelState = "loading"
      void loadLocalExtrasFast(extrasManifestRef.current, extrasManifestPayloadRef.current)
    } else {
      loader!.load(modelUrl, loaded => {
      restoreObjectAdd()
      if (disposed) { disposeExtraObject(loaded); return }
      mesh = loaded
      const loadedBones = allBones()
      const bonesByName = new Map<string, THREE.Object3D[]>()
      loadedBones.forEach(node => {
        basePositions.set(node, node.position.clone())
        baseRotations.set(node, node.rotation.clone())
        baseScales.set(node, node.scale.clone())
        const name = canonicalRuntimeBoneSegment(node.name)
        const matches = bonesByName.get(name) || []
        matches.push(node)
        bonesByName.set(name, matches)
      })
      bonesByName.forEach((bones, name) => { if (bones.length === 1) bodyShapeTargetBones.set(name, bones[0]) })
      let morphTargetCount = 0
      let meshCount = 0
      let vertexCount = 0
      const meshNames: string[] = []
      const pmxMaterialNames: string[] = []
      let boneCount = 0
      const boneNames: string[] = []
      const morphNames: string[] = []
      mesh.traverse(node => {
        const target = node as THREE.Mesh
        if (target.isMesh) {
          meshCount += 1
          vertexCount += target.geometry?.attributes?.position?.count || 0
          if (/cf_m_body/i.test(String(target.material && !Array.isArray(target.material) ? target.material.name : ""))) {
            const p = target.geometry?.attributes?.position
            if (p) {
              const bounds = new THREE.Box3().setFromBufferAttribute(p as THREE.BufferAttribute)
              mount.dataset.bodyBounds = [bounds.min.x, bounds.min.y, bounds.min.z, bounds.max.x, bounds.max.y, bounds.max.z].map(value => value.toFixed(3)).join(",")
            }
          }
          if (meshNames.length < 24) meshNames.push(node.name || "(unnamed)")
          const materials = Array.isArray(target.material) ? target.material : [target.material]
          materials.forEach(material => { if (material && pmxMaterialNames.length < 128) pmxMaterialNames.push(String(material.name || "(unnamed)")) })
        }
        if (target.morphTargetDictionary) {
          const names = Object.keys(target.morphTargetDictionary)
          morphTargetCount += names.length
          if (morphNames.length < 160) morphNames.push(...names.slice(0, 160 - morphNames.length))
        }
      })
      allBones().forEach(node => { boneCount += 1; if (boneNames.length < 40) boneNames.push(node.name || "(unnamed)") })
      mount.dataset.meshCount = String(meshCount)
      mount.dataset.vertexCount = String(vertexCount)
      mount.dataset.meshNames = meshNames.join("|")
      mount.dataset.pmxMaterialNames = pmxMaterialNames.join("|")
      mount.dataset.boneCount = String(boneCount)
      mount.dataset.boneNames = boneNames.join("|")
      mount.dataset.morphTargets = String(morphTargetCount)
      mount.dataset.morphNames = morphNames.join("|")
      const box = new THREE.Box3().setFromObject(mesh)
      const center = box.getCenter(new THREE.Vector3())
      mount.dataset.modelBounds = [box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z].map(item => item.toFixed(3)).join(",")
      modelHeight = Math.max(.85, box.max.y - box.min.y)
      mount.dataset.modelHeight = modelHeight.toFixed(3)
      // Leave a small visual floor margin. The raw exporter bounds are
      // normalized to y=0, which makes the character sit too low in the
      // editor canvas once the camera targets the torso.
      modelOffset.set(-center.x, -box.min.y + modelHeight * 0.08, -center.z)
      // Keep raw MMD geometry out of WebGL until the safe material pass has
      // removed unsupported morph attributes.
      mesh.visible = false
      mesh.position.copy(modelOffset)
      scene.add(mesh)
      mesh.updateMatrixWorld(true)
      bindBoneWorld.clear()
      allBones().forEach(bone => bindBoneWorld.set(bone, bone.matrixWorld.clone()))
      mount.dataset.modelState = "ready"
      applyMorph()
      if (hasNativeKkExport) {
        const profile = resolveKksHeadShapeDriverId(nativeHeadIdRef.current)
        if (profile === null) {
          // A custom PMX head has no trustworthy bundled curve table. Keep its
          // face at bind pose instead of silently applying profile 0.
          headShapeDriverRef.current = undefined
          headUsesDefaultFaceRef.current = true
          mount.dataset.faceShapeState = "unavailable"
          onNativeHeadStateChangeRef.current?.({
            status: "degraded",
            reason: "该 PMX 自定义头缺少精确 ShapeAnime 曲线，未应用错误的0号曲线。",
            fallbackApplied: false,
          })
        } else {
          void loadBundledKksHeadShapeDriver(
            profile as 0 | 200 | 201,
            pmxHeadAbortController.signal,
          ).then(driver => {
            if (disposed || pmxHeadAbortController.signal.aborted) return
            headShapeDriverRef.current = driver
            headUsesDefaultFaceRef.current = false
            mount.dataset.nativeHeadId = String(nativeHeadIdRef.current ?? "")
            mount.dataset.nativeHeadProfile = String(profile)
            mount.dataset.headShapeDriverVersion = String(driver.version)
            shapeDirtyRef.current = true
            applyMorph()
            onNativeHeadStateChangeRef.current?.({ status: "ready", profile: String(profile) })
            renderRequestRef.current()
          }).catch(error => {
            if (disposed || pmxHeadAbortController.signal.aborted) return
            mount.dataset.faceShapeState = "unavailable"
            onNativeHeadStateChangeRef.current?.({
              status: "degraded",
              reason: error instanceof Error ? error.message : "KKS head shape driver unavailable",
              fallbackApplied: false,
            })
          })
        }
      }
      // Both registry imports (`/file/model.pmx`) and direct local-card
      // exports (`/model/model.pmx`) use the same KKBP folder layout.  The
      // old check only recognized the registry URL, so a valid local card
      // loaded its body but silently dropped every clothing/hair mesh.
      const outfitUrl = /(?:\/file|\/model)\/model\.pmx(?:\?.*)?$/i.test(modelUrl)
        ? modelUrl.replace(/\/(?:file|model)\/model\.pmx(?:\?.*)?$/i, match => match.toLowerCase().startsWith("/model/") ? "/model/Outfit%2000/model.pmx" : "/file/Outfit%2000/model.pmx")
        : undefined
      if (outfitUrl) {
        mount.dataset.nativeOutfitState = "loading"
        loader!.load(outfitUrl, loadedOutfit => {
          if (disposed) { disposeExtraObject(loadedOutfit); return }
          // Outfit 00 is a skinned PMX. Reuse the body's skeleton and bind
          // matrices instead of flattening it into a rigid mesh; this keeps
          // clothing vertices in the same coordinate system as the card body.
          outfitMesh = loadedOutfit
          const useSharedOutfitSkeleton = true
          if (useSharedOutfitSkeleton && mesh?.isSkinnedMesh && outfitMesh.isSkinnedMesh) {
            const bodyMesh = mesh
            // PMX skin indices refer to the outfit's bone-array order. Build
            // a skeleton in that same order, but point each slot at the
            // body's named bone so both meshes receive the same pose.
            const bodyBones = new Map<string, { bone: THREE.Bone; index: number }>()
            bodyMesh.skeleton.bones.forEach((bone, index) => bodyBones.set(bone.name, { bone, index }))
            const outfitInverses = outfitMesh.skeleton.boneInverses
            const unmatchedOutfitBones = outfitMesh.skeleton.bones.filter(outfitBone => !bodyBones.has(outfitBone.name)).map(outfitBone => outfitBone.name)
            const remappedBones = outfitMesh.skeleton.bones.map(outfitBone => bodyBones.get(outfitBone.name)?.bone || outfitBone)
            // The outfit vertex weights keep the outfit bone-array order, but
            // the remapped bones now come from the body's skeleton. Using the
            // old outfit inverse-bind matrices here leaves sleeves/gloves in
            // the outfit's bind frame; when the arm is lowered they slide
            // underneath the torso instead of following the arm. Pair every
            // slot with the body's inverse for the matched bone.
            const remappedInverses = outfitMesh.skeleton.bones.map((outfitBone, index) => {
              const bodyBinding = bodyBones.get(outfitBone.name)
              return bodyBinding ? bodyMesh.skeleton.boneInverses[bodyBinding.index].clone() : (outfitInverses[index]?.clone() || new THREE.Matrix4())
            })
            outfitMesh.skeleton = new THREE.Skeleton(remappedBones, remappedInverses)
            // MMDLoader preserves each PMX's bindMatrix separately. Reusing
            // the body's bones/inverses without also sharing that matrix
            // leaves the outfit in its original bind frame: sleeves and
            // gloves then drift when the common arm pose is applied.
            outfitMesh.bindMatrix.copy(bodyMesh.bindMatrix)
            outfitMesh.bindMatrixInverse.copy(bodyMesh.bindMatrixInverse)
            mount.dataset.nativeOutfitSharedInverse = "body-skeleton"
            mount.dataset.nativeOutfitUnmatchedBones = unmatchedOutfitBones.slice(0, 24).join(",")
            mount.dataset.nativeOutfitUnmatchedCount = String(unmatchedOutfitBones.length)
          }
          outfitMesh.visible = false
          outfitMesh.position.copy(modelOffset)
          outfitMesh.renderOrder = 20
          let outfitMeshCount = 0
          let outfitVisibleMeshCount = 0
          const outfitMaterials: string[] = []
          const outfitMaterialDetails: string[] = []
          outfitMesh.traverse(node => {
            const target = node as THREE.Mesh
            if (!target.isMesh) return
            target.renderOrder = 20
            target.frustumCulled = false
            outfitMeshCount += 1
            if (target.visible) outfitVisibleMeshCount += 1
            const value = target.material
            const list = Array.isArray(value) ? value : [value]
            list.forEach(material => {
              if (!material) return
              if (outfitMaterials.length < 48) outfitMaterials.push(String(material.name || "(unnamed)"))
              if (outfitMaterialDetails.length < 12) outfitMaterialDetails.push(`${String(material.name || "(unnamed)")}:${String((material as any).opacity)}:${String((material as any).transparent)}:${String(Boolean((material as any).map))}`)
            })
          })
          const outfitBounds = new THREE.Box3().setFromObject(outfitMesh)
          // The body PMX bounds do not include all native hair/garment roots.
          // Recompute the framing height from the combined body + Outfit 00
          // bounds, otherwise the camera clips the top of the hair even at a
          // mathematically safe body distance.
          const combinedBounds = new THREE.Box3().setFromObject(mesh!)
          combinedBounds.union(outfitBounds)
          modelHeight = Math.max(.85, combinedBounds.max.y - combinedBounds.min.y)
          mount.dataset.modelHeight = modelHeight.toFixed(3)
          mount.dataset.nativeOutfitMeshCount = String(outfitMeshCount)
          mount.dataset.nativeOutfitVisibleMeshCount = String(outfitVisibleMeshCount)
          mount.dataset.nativeOutfitVertexCount = String(outfitMesh.geometry?.attributes?.position?.count || 0)
          mount.dataset.nativeOutfitVisible = String(outfitMesh.visible)
          mount.dataset.nativeOutfitMaterials = outfitMaterials.join("|")
          mount.dataset.nativeOutfitMaterialDetails = outfitMaterialDetails.join("|")
          mount.dataset.nativeOutfitBounds = [outfitBounds.min.x, outfitBounds.min.y, outfitBounds.min.z, outfitBounds.max.x, outfitBounds.max.y, outfitBounds.max.z].map(value => value.toFixed(3)).join(",")
          scene.add(outfitMesh)
          outfitMesh.updateMatrixWorld(true)
          mount.dataset.nativeOutfitState = "ready"
          void (async () => {
            await loadExportMaterialColors(outfitMesh!, outfitUrl, modelUrl)
            await loadExportTextures(outfitMesh!, outfitUrl, modelUrl)
            keepAuthoredMouthVisible(outfitMesh!)
            outfitMesh!.traverse(node => {
              const target = node as THREE.Mesh
              if (!target.isMesh) return
              const list = Array.isArray(target.material) ? target.material : [target.material]
              const safe = list.map(material => {
                const alpha = isKksAlphaFaceMaterial(String(material.name || ""))
                const flatNativeChest = hasNativeKkExport && /cf_m_bra_npl_00/i.test(String(material.name || ""))
                const nativeOutfitMaterial = hasNativeKkExport
                const options = {
                  name: material.name,
                  color: nativeOutfitMaterial ? 0xffffff : ((material as THREE.MeshBasicMaterial).color?.getHex() || 0xffffff),
                  map: (material as THREE.MeshBasicMaterial).map || null,
                  side: THREE.DoubleSide,
                  // Cutout clothing must still participate in depth testing;
                  // disabling depth for an alpha atlas lets a skirt cover a
                  // hand that is physically behind it.
                  transparent: alpha,
                  opacity: 1,
                  alphaTest: alpha ? 0.02 : 0,
                  depthTest: true,
                  depthWrite: true,
                }
                // The *_light composites already contain Koikatsu's baked
                // lighting. Applying Lambert on top of them double-darkens
                // the white blouse and turns the native blue/purple cloth
                // gray. Native Outfit 00 therefore stays on an unlit
                // material so its colors match the card preview; only
                // imported/non-native assets use the toon-lighted path.
                return alpha || flatNativeChest || nativeOutfitMaterial
                  ? new THREE.MeshBasicMaterial(options)
                  : new THREE.MeshToonMaterial({ ...options, gradientMap: toonGradient })
              })
              target.material = Array.isArray(target.material) ? safe : safe[0]
              // MMDLoader exposes morph attributes on the geometry; Three.js
              // then enables the morph-target shader even for MeshBasicMaterial.
              // This renderer uses the baked card pose, so disable that path to
              // avoid an invalid MORPHTARGETS shader on WebGL1/limited GPUs.
              target.geometry.morphAttributes = {}
              ;(target as any).morphTargetInfluences = []
              ;(target as any).morphTargetDictionary = {}
              target.renderOrder = 100
            })
            outfitVisibleMeshCount = 0
            outfitMesh!.traverse(node => {
              const target = node as THREE.Mesh
              if (target.isMesh && target.visible) outfitVisibleMeshCount += 1
            })
            mount.dataset.nativeOutfitVisibleMeshCount = String(outfitVisibleMeshCount)
            const outfitMapNames: string[] = []
            outfitMesh!.traverse(node => {
              const target = node as THREE.Mesh
              if (!target.isMesh) return
              const list = Array.isArray(target.material) ? target.material : [target.material]
              list.forEach(material => {
                const map = (material as THREE.MeshBasicMaterial).map
                const image = map?.image as { src?: string } | undefined
                if (outfitMapNames.length < 32) outfitMapNames.push(`${String(material.name || "")}:${String(image?.src || "")}`)
              })
            })
            mount.dataset.nativeOutfitMapNames = outfitMapNames.join("|")
          outfitMesh!.visible = renderNativeOutfit
          mount.dataset.nativeOutfitVisible = String(outfitMesh!.visible)
          })()
          applyMorph()
        }, undefined, () => { if (!disposed) mount.dataset.nativeOutfitState = "error" })
      }
      void loadLocalExtrasFast(extrasManifestRef.current, extrasManifestPayloadRef.current)
      // Material replacement must finish before texture assignment.  Running
      // both requests in parallel can replace the material after its texture
      // was attached, which makes the character look flat or partially empty.
      void (async () => {
        await loadExportMaterialColors(mesh!)
        await loadExportTextures(mesh!)
        keepAuthoredMouthVisible(mesh!)
        sortFaceOverlayGroups(mesh!)
        mesh!.traverse(node => {
          const target = node as THREE.Mesh
          if (!target.isMesh) return
          const list = Array.isArray(target.material) ? target.material : [target.material]
          const names = list.map(material => String(material?.name || "")).join("|")
          const faceDiagnostics = list.filter(material => /cf_m_face_00/i.test(String(material?.name || ""))).map(material => {
            const texture = (material as THREE.MeshBasicMaterial).map
            const image = texture?.image as { width?: number; height?: number; src?: string } | undefined
            return `${String(material?.name || "")}:${Boolean(texture)}:${image?.width || 0}x${image?.height || 0}:${String(image?.src || "")}`
          })
          if (faceDiagnostics.length) mount.dataset.faceTextureState = faceDiagnostics.join("|")
          if (/(?:hitomi|gageye)/i.test(names)) target.renderOrder = 130
          else if (/sirome/i.test(names)) target.renderOrder = 120
          else if (/(?:eyeline|mayuge|noseline)/i.test(names)) target.renderOrder = 140
          list.forEach(material => {
            const materialName = String(material?.name || "")
            if (/hitomi/i.test(materialName)) {
              const iris = material as THREE.MeshBasicMaterial
              // Keep the local iris composite: it contains the dark pupil,
              // cyan gradient and highlight that a flat overlay cannot
              // reproduce. The geometry is still scaled below to match the
              // card's eye proportions.
              iris.color.set(0xffffff)
              iris.transparent = true
              iris.opacity = 1
              iris.depthTest = true
              iris.depthWrite = false
              iris.depthFunc = THREE.LessEqualDepth
              iris.polygonOffset = true
              iris.polygonOffsetFactor = -1
              iris.polygonOffsetUnits = -1
              iris.needsUpdate = true
            } else if (!hasNativeKkExport && /cf_m_noseline/i.test(materialName)) {
              const noseLine = material as THREE.MeshBasicMaterial
              // Runtime cards use the native head's own face shell. Keeping
              // the carrier nose helper visible here creates the black
              // vertical stripe that was mistaken for the card's nose.
              noseLine.visible = false
              noseLine.transparent = true
              noseLine.opacity = 0
              noseLine.alphaTest = 0.02
              noseLine.depthWrite = false
              noseLine.needsUpdate = true
              mount.dataset.carrierNoseLineState = "hidden-runtime"
            } else if (kksAuthoredMouthOpacity(materialName) != null) {
              const mouth = material as THREE.MeshBasicMaterial
              mouth.transparent = true
              mouth.opacity = kksAuthoredMouthOpacity(materialName) ?? 1
              mouth.alphaTest = 0.01
              mouth.depthTest = true
              mouth.depthWrite = false
              mouth.side = THREE.DoubleSide
              mouth.needsUpdate = true
              target.renderOrder = Math.max(target.renderOrder, 145)
            }
          })
        })
        mesh!.traverse(node => {
          const target = node as THREE.Mesh
          if (!target.isMesh) return
          // PMX exports often carry split/flat normals from Unity's toon
          // baker. Rebuild smooth vertex normals for the WebGL preview so
          // the face and sleeves do not read as faceted white wedges.
          if (target.geometry?.attributes?.position) {
            target.geometry.computeVertexNormals()
            target.geometry.normalizeNormals()
          }
          // KKBP puts hundreds of expression targets on the same PMX mesh
          // (hundreds on some exports). Compiling all of them into
          // a WebGL skinning shader exceeds the browser/GPU program limits
          // and blanks the entire canvas.  Keep the authored static face and
          // bone skinning; expressions will be reintroduced as a small,
          // explicitly selected target set instead of the whole export.
          target.geometry.morphAttributes = {}
          ;(target as any).morphTargetInfluences = []
          ;(target as any).morphTargetDictionary = {}
        })
        if (neutralOnlyRef.current) {
          // Render the complete carrier geometry as a single neutral manikin.
          // Hiding every group except cf_m_face_00 produced the lone floating
          // head seen in the editor. Keep only the true face/body shells; the
          // eye, iris, tear and line-art materials are appearance overlays and
          // become opaque plates if they are flattened into the manikin colour.
          mesh!.traverse(node => {
            const target = node as THREE.Mesh
            if (!target.isMesh) return
            const list = Array.isArray(target.material) ? target.material : [target.material]
            target.visible = true
            list.forEach(material => {
              const basic = material as THREE.MeshBasicMaterial
              basic.visible = isKksNeutralManikinSurfaceMaterial(basic.name)
              basic.map = null
              basic.transparent = false
              basic.opacity = 1
              basic.color?.set(0xdfe4ec)
              basic.depthWrite = true
              basic.depthTest = true
              basic.needsUpdate = true
            })
          })
        }
        // Neutral mode uses the complete carrier as a colourless white model;
        // normal card mode renders the complete PMX carrier or hides it in
        // favour of the selected card's runtime body.
        syncCarrierVisibility()
        applyNativeAppearance()
        carrierMaterialsReady = true
        if (Object.keys(latestFaceTextureUrls).length) {
          void loadRuntimeFaceTextures(latestFaceTextureUrls, extraLoadToken, extraLoadAbortController?.signal)
        }
        renderReady = true
        renderRequestRef.current()
      })()
      }, undefined, error => {
        restoreObjectAdd()
        mount.dataset.modelState = "error"
        onErrorRef.current?.(error.message || "恋活 PMX 模型加载失败")
      })
    }

    const resize = () => { const width = Math.max(1, mount.clientWidth), height = Math.max(1, mount.clientHeight); renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix(); renderRequestRef.current() }
    const observer = new ResizeObserver(resize); observer.observe(mount); resize()
    let pointerId = -1, x = 0, y = 0
    const down = (event: PointerEvent) => { pointerId = event.pointerId; x = event.clientX; y = event.clientY; renderer.domElement.setPointerCapture(pointerId) }
    const move = (event: PointerEvent) => { if (event.pointerId !== pointerId) return; controls.current.yaw -= (event.clientX - x) * .012; controls.current.pitch = THREE.MathUtils.clamp(controls.current.pitch + (event.clientY - y) * .008, -.55, .55); x = event.clientX; y = event.clientY; renderRequestRef.current() }
    const up = (event: PointerEvent) => { if (event.pointerId !== pointerId) return; if (renderer.domElement.hasPointerCapture(pointerId)) renderer.domElement.releasePointerCapture(pointerId); pointerId = -1 }
    renderer.domElement.addEventListener("pointerdown", down); renderer.domElement.addEventListener("pointermove", move); renderer.domElement.addEventListener("pointerup", up); renderer.domElement.addEventListener("pointercancel", up); renderer.domElement.addEventListener("wheel", onWheel, { passive: false })
    // Capture at the mount as well as the canvas. Overlay controls and browser
    // hit-testing can otherwise swallow wheel events for PMX/native cards.
    mount.addEventListener("wheel", onWheel, { passive: false, capture: true })
    let frame = 0
    let renderPending = false
    let renderCount = 0
    // A card can contain 200k+ skinned vertices spread across dozens of
    // renderers. Re-evaluating the body driver, every skeleton and every draw
    // call at 60/120 Hz while nothing changes permanently saturated the
    // user's CPU/GPU. The editor is static between interactions, so draw once
    // after a camera, slider, resize, texture or asset update. Geometry,
    // texture resolution, antialiasing and materials remain unchanged.
    mount.dataset.renderMode = "on-demand"
    mount.dataset.renderCount = "0"
    const render = () => {
      renderPending = false
      if (!renderReady || disposed || document.hidden) return
      const control = controls.current
      const wantedFov = cameraPresetRef.current === "face" ? 24 : 30
      if (camera.fov !== wantedFov) {
        camera.fov = wantedFov
        camera.updateProjectionMatrix()
      }
      const cameraBase = getCameraBase()
      const distance = cameraBase.distance / Math.max(.5, control.zoom)
      camera.position.set(Math.sin(control.yaw) * distance, cameraBase.targetY + Math.sin(control.pitch) * distance * .6, Math.cos(control.yaw) * distance)
      camera.lookAt(0, cameraBase.targetY, 0)
      mount.dataset.cameraDistance = distance.toFixed(3)
      mount.dataset.cameraYaw = control.yaw.toFixed(3)
      mount.dataset.cameraPitch = control.pitch.toFixed(3)
      mount.dataset.cameraZoom = control.zoom.toFixed(3)
      if (shapeDirtyRef.current) applyMorph()
      renderer.render(scene, camera)
      renderCount += 1
      mount.dataset.renderCount = String(renderCount)
    }
    const requestRender = () => {
      // Browsers can suspend requestAnimationFrame entirely for background
      // tabs. Do not leave a permanently-pending frame behind while hidden;
      // visibilitychange will request the one catch-up draw on return.
      if (renderPending || disposed || document.hidden) return
      renderPending = true
      frame = requestAnimationFrame(render)
    }
    renderRequestRef.current = requestRender
    const onVisibilityChange = () => {
      if (document.hidden) {
        if (renderPending) cancelAnimationFrame(frame)
        renderPending = false
        return
      }
      requestRender()
    }
    document.addEventListener("visibilitychange", onVisibilityChange)
    requestRender()
    return () => {
      disposed = true
      pmxHeadAbortController.abort()
      extraLoadToken += 1
      extraLoadAbortController?.abort()
      extraLoadAbortController = null
      renderRequestRef.current = () => {}
      applyAppearanceRef.current = () => {}
      loadExtrasRef.current = () => {}
      clearRuntimeFaceTextures()
      document.removeEventListener("visibilitychange", onVisibilityChange)
      restoreObjectAdd()
      window.removeEventListener("teacher-avatar-reset-view", resetView)
      window.removeEventListener("teacher-avatar-set-zoom", setViewZoom)
      renderer.domElement.removeEventListener("wheel", onWheel)
      mount.removeEventListener("wheel", onWheel, { capture: true })
      cancelAnimationFrame(frame)
      observer.disconnect()
      renderer.domElement.removeEventListener("pointerdown", down)
      renderer.domElement.removeEventListener("pointermove", move)
      renderer.domElement.removeEventListener("pointerup", up)
      renderer.domElement.removeEventListener("pointercancel", up)
      if (mesh) disposeExtraObject(mesh)
      if (outfitMesh) disposeExtraObject(outfitMesh)
      if (extrasGroup) disposeExtraObject(extrasGroup)
      extraSkeletons.forEach(skeleton => skeleton.dispose())
      extraSkeletons.clear()
      runtimeCoreSkeletons.clear()
      extraPrivateBones.forEach(bone => bone.removeFromParent())
      extraPrivateBones.clear()
      runtimeSharedBones.forEach(bone => bone.removeFromParent())
      runtimeSharedBones.length = 0
      runtimeCoreBones.clear()
      runtimeCoreObjects.clear()
      runtimeCoreMaterialBaselines.clear()
      runtimeCardGroup = null
      runtimeCoreGroup = null
      extrasGroup = null
      runtimeCoreReady = false
      extraBoneBindings.clear()
      bindBoneWorld.clear()
      toonGradient.dispose()
      renderer.renderLists.dispose()
      renderer.dispose()
      renderer.domElement.remove()
    }
  // Runtime-card changes are handled by the instance-local `loadExtrasRef`
  // above. Recreating the whole effect for each manifest discarded the WebGL
  // context and reparsed the 10 MB PMX carrier on every card click, which is
  // both the largest import-time cost and the main source of browser crashes.
  // Neutral/card transitions also stay inside that path so the WebGL context
  // and shared native body survive reset and the first import. Only a real
  // model/preview-mode change performs the full teardown.
  }, [modelUrl, runtimeMode, nativeExport])
  return <div className={className} ref={mountRef} aria-label="恋活 PMX 教师模型预览" data-model-state="loading" />
}
