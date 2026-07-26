import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react"
import { Camera, Download, RotateCcw, SlidersHorizontal, Upload } from "lucide-react"
import { teacherAvatarApi, type KoikatsuCardInspection, type LocalKoikatsuCard, type TeacherAvatar } from "./api"
import type { TeacherAvatarMorph } from "./api"
import { isRuntimeHeadMainFaceItem, isRuntimeHeadReady, type NativeHeadRuntimeState, type RuntimeManifestPayload } from "./KoikatsuPmxRig"
import { ParametricCardPreview } from "./ParametricCardPreview"
import { friendlyKksDisplayName } from "./kksDisplayNames"

const LazyTeacherAvatarRig = lazy(() => import("./TeacherAvatarRig").then(module => ({ default: module.TeacherAvatarRig })))

type Values = Record<string, number>
type View = "body" | "upper" | "face"
type PreparedInspection = {
  selected: File
  card: KoikatsuCardInspection
  body: Values
  face: Values
}

// Runtime cards start from KKS's extracted native skeleton/body manifests.
// This is a component mode token, not a downloadable model; no arbitrary PMX
// character is fetched or used as a hidden carrier.
const KOIKATSU_NATIVE_TEMPLATE_URL = "/api/koikatsu-cards/shared-base/native-template"

function valuesOf(card: KoikatsuCardInspection, section: "body" | "face"): Values {
  return Object.fromEntries(card[section].map(item => [section === "body" ? item.key! : String(item.index!), item.value]))
}

function modIndexWarning(manifest?: RuntimeManifestPayload): string {
  const status = String(manifest?.mod_index?.status || "").toLowerCase()
  if (!status || status === "ready" || status === "partial") return ""
  const detail = status === "missing"
    ? "zipmod 离线索引尚未建立"
    : status === "root-missing"
      ? "zipmod 游戏资源目录当前不可用"
    : status === "root-mismatch"
        ? "zipmod 索引与当前游戏目录不匹配"
        : status === "schema-mismatch"
          ? "zipmod 索引版本与当前程序不兼容"
          : "zipmod 离线索引无效或不可读取"
  return ` ${detail}，mod 发型、服装和附件暂时无法解析。`
}

export function isRuntimeAssemblyReady(manifest?: RuntimeManifestPayload): boolean {
  // Runtime manifests are deliberately cache-only on the request path. A
  // single missing hair/accessory conversion must not throw away the body,
  // face and every already-converted garment: doing so is what made cards
  // with valid face geometry fall back to the generic white manikin. The
  // renderer already skips an individual failed item, so the assembly gate
  // only rejects a missing *core* contract (neutral body or selected head).
  if (!manifest || !Array.isArray(manifest.items)) return false
  // A selected head with a different UV layout is unsafe to compose with the
  // shared face atlas, but it is still safe to assemble the card's body,
  // clothes and accessories.  The renderer atomically replaces only that
  // head with the canonical neutral head; rejecting the whole manifest here
  // would incorrectly discard all wearable resources as well.
  const headContract = manifest.headRuntime
  const safeHeadFallback = String(headContract?.status || "").toLowerCase() === "invalid"
    && headContract?.reason === "head-face-uv-contract-mismatch"
  if (!isRuntimeHeadReady(manifest) && !safeHeadFallback) return false
  const hasNeutralBody = manifest.items.some(item => item.source_kind === "neutral_body")
  const hasMainHead = manifest.items.some(isRuntimeHeadMainFaceItem)
  if (!hasNeutralBody || !hasMainHead) return false
  const unresolvedCore = (manifest.unresolved_references || []).some(value => {
    if (!value || typeof value !== "object") return false
    const row = value as { kind?: unknown; property?: unknown }
    return String(row.kind || "").toLowerCase() === "head"
      || String(row.property || "").toLowerCase().includes("headid")
  })
  return !unresolvedCore
}

export function matchingInstalledKksPack(
  card: KoikatsuCardInspection,
  models: readonly TeacherAvatar[],
): TeacherAvatar | undefined {
  if (!card.native_fingerprint) return undefined
  return models.find(model =>
    model.asset_profile === "kks-character-pack"
    && model.card_fingerprint === card.native_fingerprint
    && model.appearance_contract?.status === "ready"
    && model.appearance_contract.card_fingerprint === card.native_fingerprint
    && model.appearance_contract.model_kind === "kkbp-web"
    && /^[0-9a-f]{64}$/i.test(model.appearance_contract.receipt_hash || ""))
}

export function matchingInstalledKksRuntimePack(
  card: KoikatsuCardInspection,
  models: readonly TeacherAvatar[],
): TeacherAvatar | undefined {
  if (!card.native_fingerprint) return undefined
  return models.find(model =>
    model.asset_profile === "kks-runtime-pack"
    && model.card_fingerprint === card.native_fingerprint
    && Boolean(model.runtime_manifest_url))
}

/** Select one atomic preview path; never mix an exact export with runtime parts. */
export function selectKksPreviewRoute(options: {
  installedModelUrl?: string | null
  nativeExportModelUrl?: string | null
  runtimeAssemblyReady: boolean
}) {
  const completeModelUrl = String(options.installedModelUrl || options.nativeExportModelUrl || "")
  if (completeModelUrl) {
    return {
      modelUrl: completeModelUrl,
      nativeExport: true,
      useRuntimeManifest: false,
    }
  }
  if (options.runtimeAssemblyReady) {
    return {
      modelUrl: KOIKATSU_NATIVE_TEMPLATE_URL,
      nativeExport: false,
      useRuntimeManifest: true,
    }
  }
  return {
    modelUrl: "",
    nativeExport: false,
    useRuntimeManifest: false,
  }
}

export function toPreviewMorph(values: Values): TeacherAvatarMorph {
  // Sunshine cards are authored around a neutral value near 0.5; keep that as baseline.
  const value = (key: string, fallback = 0.5) => values[key] ?? fallback
  // Card values are already normalized (the UI multiplies them by 100 only
  // for display).  A value of 1.06 therefore means 1.06x, not a 1.67x
  // deformation from the old 1.2 gain.  Keep the native 0.5 centre while
  // applying a restrained response so clothes and the body stay in contact.
  const ratio = (key: string, gain = 0.35, minimum = 0.1, maximum = 2.2) => Math.max(minimum, Math.min(maximum, 1 + (value(key) - 0.5) * gain))
  // Chest depth is intentionally given a wider response than the other
  // proportions.  The original card range tops out around 1.5, but the
  // editor is also the body's sculpting layer; clamping it to the card's
  // narrow range made the 3D chest barely move when the slider was maxed.
  const bustDepth = Math.max(.1, Math.min(10, 1 + (value("bust_size") - .5) * 1.0))
  return {
    height: Math.max(.5, Math.min(2, 1 + (value("height", .3) - .3) * 1.2)),
    head_scale: ratio("head_size"),
    upper_chest: bustDepth,
    shoulder_width: ratio("body_shoulder_width"),
    waist_width: Math.max(.1, Math.min(2.2, 1 + (((value("waist_upper_width") + value("waist_lower_width")) / 2) - .5) * .35)),
    hip_width: ratio("hip_width"),
    arm_length: 1,
    leg_length: 1,
    // Imported cards must open in their authored neutral expression. Applying
    // a global smile here activates KKS mouth/eye expression morphs and changes
    // the character before the user touches any control.
    smile: 0,
    blink: 1,
  }
}

export function KoikatsuCardEditor({
  modelUrl,
  modelFormat,
  onMorphChange,
  onAssetProfileChange,
  onAssetManifestChange,
  onNativeCardChange,
  onModelUrlChange,
  onPreviewChange,
  previewMorph,
  show3dPreview = false,
}: { modelUrl?: string; modelFormat?: "vrm" | "glb" | "pmx"; onMorphChange?: (morph: TeacherAvatarMorph) => void; onAssetProfileChange?: (enabled: boolean) => void; onAssetManifestChange?: (url?: string) => void; onNativeCardChange?: (enabled: boolean) => void; onModelUrlChange?: (url?: string) => void; onPreviewChange?: (url?: string) => void; previewMorph?: TeacherAvatarMorph; show3dPreview?: boolean }) {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState("")
  const [card, setCard] = useState<KoikatsuCardInspection | null>(null)
  const [fallbackModelUrl, setFallbackModelUrl] = useState("")
  const [body, setBody] = useState<Values>({})
  const [face, setFace] = useState<Values>({})
  const [tab, setTab] = useState<"body" | "face">("body")
  const [view, setView] = useState<View>("body")
  const [zoom, setZoom] = useState(1)
  const [rotation, setRotation] = useState(0)
  const [cameraReset, setCameraReset] = useState(0)
  const [localCards, setLocalCards] = useState<LocalKoikatsuCard[]>([])
  const [localQuery, setLocalQuery] = useState("")
  const [localOffset, setLocalOffset] = useState(0)
  const [localCount, setLocalCount] = useState(0)
  const [localNextOffset, setLocalNextOffset] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState("")
  const [extrasEnabled, setExtrasEnabled] = useState(false)
  const [extrasManifestUrl, setExtrasManifestUrl] = useState<string | undefined>()
  const [extrasManifestPayload, setExtrasManifestPayload] = useState<RuntimeManifestPayload | undefined>()
  // The editor owns the active card/model association. Do not continuously
  // mirror the roster's base model prop here: doing so can re-apply a stale
  // PMX after a user selects a different card.
  const [selectedModelUrl, setSelectedModelUrl] = useState<string | undefined>(KOIKATSU_NATIVE_TEMPLATE_URL)
  const [activeCardId, setActiveCardId] = useState<string | undefined>()
  const [neutralOnly, setNeutralOnly] = useState(true)
  const [nativeExport, setNativeExport] = useState(false)
  const [runtimeHeadResolved, setRuntimeHeadResolved] = useState(false)
  const [runtimeHeadState, setRuntimeHeadState] = useState<NativeHeadRuntimeState>({ status: "loading" })
  const catalogGenerationRef = useRef(0)
  const catalogAbortRef = useRef<AbortController | null>(null)
  const catalogQueryRef = useRef(localQuery)
  const selectionGenerationRef = useRef(0)
  const selectionAbortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    const debounceMs = catalogQueryRef.current === localQuery ? 0 : 200
    catalogQueryRef.current = localQuery
    const generation = catalogGenerationRef.current + 1
    catalogGenerationRef.current = generation
    catalogAbortRef.current?.abort()
    const controller = new AbortController()
    catalogAbortRef.current = controller
    const timeout = window.setTimeout(() => {
      void teacherAvatarApi.listLocalKoikatsuCards({ query: localQuery, offset: localOffset }, controller.signal)
        .then(result => {
          if (controller.signal.aborted || generation !== catalogGenerationRef.current) return
          setLocalCards(result.items)
          setLocalCount(result.count)
          setLocalNextOffset(result.next_offset)
        })
        .catch(() => {})
    }, debounceMs)
    return () => {
      window.clearTimeout(timeout)
      controller.abort()
      if (catalogAbortRef.current === controller) catalogAbortRef.current = null
    }
  }, [localOffset, localQuery])

  useEffect(() => { if (Object.keys(body).length > 0) onMorphChange?.(toPreviewMorph(body)) }, [body, onMorphChange])
  useEffect(() => { if (preview) onPreviewChange?.(preview) }, [preview, onPreviewChange])
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])
  useEffect(() => () => {
    selectionGenerationRef.current += 1
    selectionAbortRef.current?.abort()
    selectionAbortRef.current = null
  }, [])

  useEffect(() => {
    setNeutralOnly(true)
    setNativeExport(false)
    setRuntimeHeadResolved(false)
    setRuntimeHeadState({ status: "loading" })
    setSelectedModelUrl(KOIKATSU_NATIVE_TEMPLATE_URL)
    onNativeCardChange?.(false)
    onModelUrlChange?.(KOIKATSU_NATIVE_TEMPLATE_URL)
    onAssetManifestChange?.(undefined)
    onAssetProfileChange?.(false)
    setExtrasManifestUrl(undefined)
    setExtrasManifestPayload(undefined)
    setExtrasEnabled(false)
  }, [])

  const changed = useMemo(() => Boolean(card && (card.body.some(item => body[item.key!] !== item.value) || card.face.some(item => face[String(item.index!)] !== item.value))), [body, card, face])

  async function prepareInspection(selected: File, generation = selectionGenerationRef.current): Promise<PreparedInspection | null> {
    const data = new FormData()
    data.set("file", selected)
    const result = await teacherAvatarApi.inspectKoikatsuCard(data)
    if (generation !== selectionGenerationRef.current) return null
    return {
      selected,
      card: result.card,
      body: valuesOf(result.card, "body"),
      face: valuesOf(result.card, "face"),
    }
  }

  function commitInspection(prepared: PreparedInspection, manifestOverride?: string, cardPreviewUrl?: string, extractedOutfit = true, manifestPayload?: RuntimeManifestPayload) {
    setFile(prepared.selected)
    setCard(prepared.card)
    setBody(prepared.body)
    setFace(prepared.face)

    const previewUrl = URL.createObjectURL(prepared.selected)
    setPreview(previous => { if (previous) URL.revokeObjectURL(previous); return previewUrl })
    onPreviewChange?.(cardPreviewUrl || previewUrl)

    // Native exports already contain their own outfit. Runtime cards receive
    // only their own resolved manifest. This rule is identical for every card.
    const manifest = extractedOutfit ? (manifestOverride || undefined) : undefined
    setExtrasManifestUrl(manifest)
    setExtrasManifestPayload(manifest ? manifestPayload : undefined)
    setExtrasEnabled(Boolean(manifest))
    onAssetProfileChange?.(Boolean(manifest))
    onAssetManifestChange?.(manifest)
  }

  function resetToNeutral() {
    selectionAbortRef.current?.abort()
    selectionAbortRef.current = null
    selectionGenerationRef.current += 1
    setNeutralOnly(true)
    setNativeExport(false)
    setRuntimeHeadResolved(false)
    setRuntimeHeadState({ status: "loading" })
    setBusy(false)
    setFile(null)
    setCard(null)
    setNotice("")
    setActiveCardId(undefined)
    setSelectedModelUrl(KOIKATSU_NATIVE_TEMPLATE_URL)
    setFallbackModelUrl("")
    setBody({})
    setFace({})
    setExtrasManifestUrl(undefined)
    setExtrasManifestPayload(undefined)
    setExtrasEnabled(false)
    onNativeCardChange?.(false)
    onModelUrlChange?.(KOIKATSU_NATIVE_TEMPLATE_URL)
    onAssetManifestChange?.(undefined)
    onAssetProfileChange?.(false)
    onPreviewChange?.("")
    setPreview(previous => { if (previous) URL.revokeObjectURL(previous); return "" })
  }

  async function loadDefault() { resetToNeutral() }

  async function loadLocalCard(item: LocalKoikatsuCard) {
    const generation = selectionGenerationRef.current + 1
    selectionGenerationRef.current = generation
    selectionAbortRef.current?.abort()
    const controller = new AbortController()
    selectionAbortRef.current = controller
    try {
      setBusy(true)
      setNotice("")
      const response = await fetch(item.card_url, { cache: "no-store", signal: controller.signal })
      if (!response.ok) throw new Error("本机角色卡读取失败")
      if (generation !== selectionGenerationRef.current) return
      let runtimeManifest: string | undefined
      let runtimeManifestPayload: RuntimeManifestPayload | undefined
      let hasRuntimeAssets = false
      let runtimeStatus: "ready" | "partial" | undefined
      let runtimeDeclaredCount = 0
      let runtimeUnresolvedCount = 0
      let runtimeConversionCount = 0
      let hasRuntimeHead = false
      let runtimeAssemblyReady = false
      const hasCompleteNativeExport = (item.asset_profile === "kkbp-web" || item.asset_profile === "kkbp-export")
        && item.asset_status === "ready"
        && Boolean(item.model_url)
      // A complete KKBP export is the character that KKS actually instantiated
      // with Sideloader, MaterialEditor and ABMX. It is authoritative for
      // geometry, skinning, clothing and materials; the reconstructed shared
      // shell is only the fallback for cards that have no completed export.
      if (!hasCompleteNativeExport) {
        try {
          const runtimeResponse = await fetch(`${item.card_url}/runtime-manifest`, { cache: "no-store", signal: controller.signal })
          if (runtimeResponse.ok) {
            const runtime = await runtimeResponse.json() as RuntimeManifestPayload
            runtimeManifestPayload = runtime
            hasRuntimeAssets = Array.isArray(runtime.items) && runtime.items.length > 0
            hasRuntimeHead = isRuntimeHeadReady(runtime)
            runtimeStatus = runtime.status
            runtimeDeclaredCount = Number(runtime.declared_count || 0)
            runtimeUnresolvedCount = runtime.unresolved_references?.length || 0
            runtimeConversionCount = runtime.conversion_failures?.length || 0
            if (hasRuntimeAssets) runtimeManifest = `${item.card_url}/runtime-manifest`
            runtimeAssemblyReady = isRuntimeAssemblyReady(runtime)
          }
        } catch {
          // Clothes/resources are optional. A manifest transport or JSON error
          // must not discard the card's native body and face parameters.
        }
      }
      if (generation !== selectionGenerationRef.current) return
      const blob = await response.blob()
      if (generation !== selectionGenerationRef.current) return
      const selected = new File([blob], item.filename, { type: "image/png" })
      const inspectedCard = runtimeManifestPayload?.card_id === item.card_id
        && runtimeManifestPayload.card?.native_fingerprint
        ? runtimeManifestPayload.card
        : undefined
      const prepared = inspectedCard
        ? {
            selected,
            card: inspectedCard,
            body: valuesOf(inspectedCard, "body"),
            face: valuesOf(inspectedCard, "face"),
          }
        : await prepareInspection(selected, generation)
      if (!prepared || generation !== selectionGenerationRef.current) return
      if (prepared.card.native_summary.sex !== 1) throw new Error("当前通用原生模型仅支持女性 KKS 角色卡")
      const installed = await teacherAvatarApi.list().catch(() => ({ items: [], motion_library: [] }))
      if (generation !== selectionGenerationRef.current) return
      const matchedPack = matchingInstalledKksPack(prepared.card, installed.items)
      const matchedRuntimePack = matchingInstalledKksRuntimePack(prepared.card, installed.items)
      // A runtime pack has no replacement GLB: it supplies a portable native
      // manifest and cached clothing assets while the universal body remains
      // the renderer. Load that manifest before committing the card so the
      // customer path is identical to a local cache-ready card.
      const installedRuntimeManifestUrl = matchedRuntimePack?.runtime_manifest_url || matchedPack?.runtime_manifest_url
      if (installedRuntimeManifestUrl) {
        try {
          const packResponse = await fetch(installedRuntimeManifestUrl, { cache: "no-store", signal: controller.signal })
          if (packResponse.ok) {
            const packRuntime = await packResponse.json() as RuntimeManifestPayload
            if (packRuntime.card?.native_fingerprint === prepared.card.native_fingerprint) {
              runtimeManifestPayload = packRuntime
              runtimeManifest = installedRuntimeManifestUrl
              hasRuntimeAssets = Array.isArray(packRuntime.items) && packRuntime.items.length > 0
              hasRuntimeHead = isRuntimeHeadReady(packRuntime)
              runtimeStatus = packRuntime.status
              runtimeDeclaredCount = Number(packRuntime.declared_count || 0)
              runtimeUnresolvedCount = packRuntime.unresolved_references?.length || 0
              runtimeConversionCount = packRuntime.conversion_failures?.length || 0
              runtimeAssemblyReady = isRuntimeAssemblyReady(packRuntime)
            }
          }
        } catch {
          // Keep the normal local-cache fallback if a downloaded pack is stale.
        }
      }
      const route = selectKksPreviewRoute({
        installedModelUrl: matchedPack?.model_url,
        nativeExportModelUrl: hasCompleteNativeExport ? item.model_url : undefined,
        runtimeAssemblyReady,
      })
      const hasCompleteCustomerModel = route.nativeExport
      const preferredModel = route.modelUrl

      // Card identity, 44/52 shape values and its manifest commit in one React
      // batch. The previous character stays intact while parsing, so a failed
      // or superseded request can never expose a half-A/half-B renderer state.
      setActiveCardId(item.card_id)
      setNeutralOnly(false)
      setNativeExport(hasCompleteCustomerModel)
      setRuntimeHeadState({ status: "loading" })
      setRuntimeHeadResolved(true)
      setSelectedModelUrl(preferredModel)
      // Every parsed card owns the native 44/52-channel pipeline. Hide the
      // legacy ten-field approximation so deformation is never applied twice.
      onNativeCardChange?.(true)
      setFallbackModelUrl("")
      // An empty override is intentional: it prevents the parent roster's
      // any previous PMX from being reused for a card without its own export.
      onModelUrlChange?.(preferredModel)
      commitInspection(
        prepared,
        route.useRuntimeManifest ? runtimeManifest : undefined,
        item.card_url,
        route.useRuntimeManifest,
        route.useRuntimeManifest ? runtimeManifestPayload : undefined,
      )
      const displayName = friendlyKksDisplayName(item.character.display_name || item.filename)
      const indexWarning = modIndexWarning(runtimeManifestPayload)
      if (route.useRuntimeManifest) {
        setNotice(`已读取 ${displayName} 角色卡，并通过通用原生骨架装配其头部、服装和附件；无需启动恋活。${indexWarning}`)
      } else if (!hasCompleteCustomerModel) {
        const details = [
          runtimeUnresolvedCount ? `${runtimeUnresolvedCount} 个资源引用未解析` : "",
          runtimeConversionCount ? `${runtimeConversionCount} 个部件尚未完成离线转换` : "",
        ].filter(Boolean).join("，")
        setNotice(`已读取 ${displayName} 的角色卡参数，但完整外观资源包尚未通过原卡身份与渲染验收${details ? `（${details}）` : ""}。当前只显示原角色卡预览，不再展示失真的拼装 3D。${indexWarning}`)
      } else if (matchedPack) {
        setNotice(`已读取 ${displayName} 角色卡，并自动匹配已下载的“${friendlyKksDisplayName(matchedPack.display_name)}”角色资源包；客户侧无需安装或启动恋活。`)
      } else if (hasCompleteNativeExport) {
        setNotice(`已载入 ${displayName} 经 KKS 完整实例化的 KKBP 模型；当前直接使用原生骨骼、服装、附件和导出材质。`)
      }
    } catch (error) { // loadLocalCard generation guard
      if (generation !== selectionGenerationRef.current) return
      const message = error instanceof Error ? error.message : "本机角色卡读取失败"
      resetToNeutral()
      setNotice(message)
    } finally {
      if (selectionAbortRef.current === controller) selectionAbortRef.current = null
      if (generation === selectionGenerationRef.current) setBusy(false)
    }
  }

  async function loadFile(selected: File | null) {
    if (!selected) return
    const generation = selectionGenerationRef.current + 1
    selectionGenerationRef.current = generation
    selectionAbortRef.current?.abort()
    const controller = new AbortController()
    selectionAbortRef.current = controller
    try {
      setBusy(true)
      setNotice("")
      // The selected bytes are authoritative. A filename match can point at a
      // different card revision, so upload this File once and atomically use
      // the inspection + runtime manifest produced from its content hash.
      const data = new FormData()
      data.set("file", selected)
      const result = await teacherAvatarApi.importKoikatsuCardManifest(data, controller.signal)
      if (generation !== selectionGenerationRef.current || controller.signal.aborted) return
      let runtime = result.manifest as unknown as RuntimeManifestPayload
      if (!runtime.card_id || !runtime.card) throw new Error("角色卡运行清单无效")
      const prepared: PreparedInspection = {
        selected,
        card: runtime.card,
        body: valuesOf(runtime.card, "body"),
        face: valuesOf(runtime.card, "face"),
      }
      let hasRuntimeHead = isRuntimeHeadReady(runtime)
      let runtimeAssemblyReady = isRuntimeAssemblyReady(runtime)
      const installed = await teacherAvatarApi.list().catch(() => ({ items: [], motion_library: [] }))
      if (generation !== selectionGenerationRef.current || controller.signal.aborted) return
      const matchedPack = matchingInstalledKksPack(prepared.card, installed.items)
      const matchedRuntimePack = matchingInstalledKksRuntimePack(prepared.card, installed.items)
      if (matchedRuntimePack?.runtime_manifest_url) {
        try {
          const packResponse = await fetch(matchedRuntimePack.runtime_manifest_url, { cache: "no-store", signal: controller.signal })
          if (packResponse.ok) {
            const packRuntime = await packResponse.json() as RuntimeManifestPayload
            if (packRuntime.card?.native_fingerprint === prepared.card.native_fingerprint) {
              runtime = packRuntime
              hasRuntimeHead = isRuntimeHeadReady(packRuntime)
              runtimeAssemblyReady = isRuntimeAssemblyReady(packRuntime)
            }
          }
        } catch {
          // The uploaded card's own manifest remains the safe fallback.
        }
      }
      const route = selectKksPreviewRoute({
        installedModelUrl: matchedPack?.model_url,
        runtimeAssemblyReady,
      })
      const preferredModel = route.modelUrl
      setNeutralOnly(false)
      setNativeExport(route.nativeExport)
      setRuntimeHeadState({ status: "loading" })
      setRuntimeHeadResolved(true)
      setActiveCardId(runtime.card_id)
      setSelectedModelUrl(preferredModel)
      onNativeCardChange?.(true)
      onModelUrlChange?.(preferredModel)
      setFallbackModelUrl("")
      // The POST URL is a stable manifest identity token. The renderer reuses
      // payloadOverride directly and therefore never issues a GET for it.
      commitInspection(
        prepared,
        route.useRuntimeManifest
          ? (matchedRuntimePack?.runtime_manifest_url || "/api/koikatsu-cards/import-manifest")
          : undefined,
        undefined,
        route.useRuntimeManifest,
        route.useRuntimeManifest ? runtime : undefined,
      )
      const unresolved = runtime.unresolved_references?.length || 0
      const cold = runtime.conversion_failures?.length || 0
      const indexWarning = modIndexWarning(runtime)
      setNotice(matchedPack
        ? `已读取角色卡并自动匹配已下载的“${friendlyKksDisplayName(matchedPack.display_name)}”角色资源包；客户侧无需安装或启动恋活。`
        : `已读取所选角色卡参数，但完整外观资源包尚未通过原卡身份与渲染验收（${unresolved} 个资源未解析，${cold} 个部件未完成转换）。当前只显示原角色卡预览，不再展示失真的拼装 3D。${indexWarning}`)
    } catch (error) { // loadFile generation guard
      if (generation !== selectionGenerationRef.current) return
      const message = error instanceof Error ? error.message : "角色卡导入失败"
      resetToNeutral()
      setNotice(message)
    } finally {
      if (selectionAbortRef.current === controller) selectionAbortRef.current = null
      if (generation === selectionGenerationRef.current) setBusy(false)
    }
  }

  async function buildCard(): Promise<File | null> {
    if (!file) return null
    const data = new FormData()
    data.set("file", file)
    data.set("body_values", JSON.stringify(body))
    data.set("face_values", JSON.stringify(face))
    return new File([await teacherAvatarApi.exportKoikatsuCard(data)], `${file.name.replace(/\.png$/i, "")}-edited.png`, { type: "image/png" })
  }

  async function downloadCard() {
    try {
      const edited = await buildCard()
      if (!edited) return
      const url = URL.createObjectURL(edited)
      const a = document.createElement("a")
      a.href = url
      a.download = edited.name
      a.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "导出失败")
    }
  }

  // Never silently render an unrelated character model for an imported card.
  // A PMX is valid only when it is a complete export for the selected card,
  // or the neutral carrier is paired with that card's runtime manifest.
  const previewModelUrl = selectedModelUrl || fallbackModelUrl
  const previewModelFormat = previewModelUrl === KOIKATSU_NATIVE_TEMPLATE_URL
    ? "pmx"
    : previewModelUrl.toLowerCase().endsWith(".pmx")
      ? "pmx"
      : previewModelUrl.toLowerCase().split(/[?#]/, 1)[0].endsWith(".glb")
        ? "glb"
        : modelFormat
  // A native PMX card already receives the complete 44-value body payload.
  // Do not also feed the legacy ten-field approximation into that same rig:
  // doing so applies chest/waist/limb deformation twice and is the source of
  // the unnatural silhouettes. Non-PMX avatars still use the generic driver.
  const rigMorph = previewModelFormat === "pmx"
    ? { height: 1, head_scale: 1, upper_chest: 1, shoulder_width: 1, waist_width: 1, hip_width: 1, arm_length: 1, leg_length: 1, smile: 0, blink: 1 }
    : previewModelFormat === "glb"
      // The baked KKBP model already contains the selected card's authored
      // face. Do not open several "egao" eye/mouth morphs at once merely to
      // make the preview look animated; that changes the character identity.
      ? { height: 1, head_scale: 1, upper_chest: 1, shoulder_width: 1, waist_width: 1, hip_width: 1, arm_length: 1, leg_length: 1, smile: 0, blink: 1 }
      : (previewMorph || toPreviewMorph(body))
  // The model URL is the authority.  A card name must never decide whether
  // the 3D path is enabled; doing that made one character a special case and hid
  // the very rig we needed to validate.  Any card with its own native export
  // uses the same 3D pipeline, regardless of character name.
  const use3dPreview = show3dPreview && Boolean(previewModelUrl)
  const parameterShellOnly = Boolean(
    card
    && previewModelUrl === KOIKATSU_NATIVE_TEMPLATE_URL
    && !nativeExport
    && !extrasEnabled,
  )
  const sliders = card ? card[tab] : []
  const values = tab === "body" ? body : face
  const setValues = tab === "body" ? setBody : setFace
  const runtimeHeadFeedback = card && !parameterShellOnly && (!runtimeHeadResolved || runtimeHeadState.status === "degraded")
    ? `${runtimeHeadState.status === "degraded" ? (runtimeHeadState.reason || "当前卡头部未完整还原。") : "当前卡 headRuntime 未就绪，头部未完整还原。"} ${runtimeHeadState.fallbackApplied === false ? "已停止应用不匹配的52项脸部曲线。" : "当前预览未混用错误曲线，已整体使用官方女性0号头预设。"}`
    : ""
  const localCardGroups = localCards.reduce<Record<string, LocalKoikatsuCard[]>>((groups, item) => {
    const category = item.category_path || "默认角色"
    ;(groups[category] ||= []).push(item)
    return groups
  }, {})

  return <section className="koikatsu-maker" aria-label="角色卡编辑器">
    <header>
      <div>
        <span>KOIKATSU SUNSHINE CHARACTER MAKER</span>
        <h2>通用恋活角色卡预览</h2>
        <p>按卡片自身资源加载模型、服装与附件；不使用角色专属兼容分支。</p>
      </div>
      <SlidersHorizontal size={24} />
    </header>

    <div className="koikatsu-saved">
      <button type="button" onClick={resetToNeutral}>白模初始预设</button>
      <b>当前角色：{file?.name || "未导入"}</b>
      <label className="koikatsu-local-search">
        <input value={localQuery} onChange={event => { setLocalQuery(event.target.value); setLocalOffset(0) }} placeholder="搜索本机角色卡" />
        <span>{localCount} 个</span>
      </label>
      <div className="koikatsu-card-roster" aria-label="按分类浏览本机角色卡">
        {Object.entries(localCardGroups).map(([category, items]) => <div className="koikatsu-card-category" key={category}>
                <span>{category} <b>本页 {items.length}</b></span>
          <div>{items.map(item => <button key={item.card_id} type="button" onClick={() => void loadLocalCard(item)} disabled={busy}>{friendlyKksDisplayName(item.character.display_name || item.filename, category)}</button>)}</div>
        </div>)}
      </div>
      <small className="koikatsu-card-page">当前显示 {localOffset + 1}-{Math.min(localOffset + localCards.length, localCount)} / {localCount} 张；每页最多 24 张，按本机目录分类</small>
      <button type="button" onClick={() => setLocalOffset(value => Math.max(0, value - 24))} disabled={!localOffset}>上一页</button>
      <button type="button" onClick={() => localNextOffset !== null && setLocalOffset(localNextOffset)} disabled={localNextOffset === null}>下一页</button>
      <button type="button" onClick={() => void loadDefault()}><RotateCcw size={14} />重置默认</button>
    </div>

    <div className="koikatsu-maker-grid">
      <section className={`koikatsu-stage view-${view}`}>
        <div className="koikatsu-stage-toolbar">
          <Camera size={15} />
          {neutralOnly && <span className="koikatsu-neutral-badge">通用白模</span>}
          {parameterShellOnly && <span className="koikatsu-neutral-badge">通用参数白模</span>}
          {(["body", "upper", "face"] as View[]).map(item => (
            <button key={item} className={view === item ? "active" : ""} onClick={() => setView(item)}>
              {item === "body" ? "全身" : item === "upper" ? "上半身" : "脸部"}
            </button>
          ))}
          <button type="button" onClick={() => { setZoom(1); setRotation(0); setCameraReset(value => value + 1) }} title="重置镜头">
            <RotateCcw size={14} />
            重置
          </button>
        </div>

        {use3dPreview ? (
          <Suspense fallback={<div className="teacher-avatar-empty">正在加载 3D 模型...</div>}>
            <LazyTeacherAvatarRig className="koikatsu-model" modelUrl={previewModelUrl} format={previewModelFormat} runtimeMode={previewModelUrl === KOIKATSU_NATIVE_TEMPLATE_URL ? "native-template" : previewModelFormat === "glb" ? "kkbp-web" : "pmx-export"} motion="idle" morph={rigMorph} nativeBody={body} nativeBodyTypeBone={card?.native_summary.body_type_bone} nativeAbmxModifiers={card?.abmx_modifiers} nativeFace={face} nativeHeadId={parameterShellOnly ? 0 : card?.native_summary.head_id} nativeAppearance={nativeExport || extrasEnabled ? card?.appearance : undefined} extrasEnabled={extrasEnabled} extrasManifestUrl={extrasManifestUrl} extrasManifestPayload={extrasManifestPayload} nativeExport={nativeExport} nativeProfileKey={activeCardId} neutralOnly={neutralOnly} cameraPreset={view} cameraZoom={zoom} cameraYaw={rotation * Math.PI / 180} cameraResetToken={cameraReset} onError={setNotice} onNativeHeadStateChange={setRuntimeHeadState} onCameraZoomChange={setZoom} />
          </Suspense>
        ) : preview ? (
          <div className="koikatsu-card-static-preview">
            <ParametricCardPreview src={preview} morph={toPreviewMorph(body)} zoom={zoom} alt={file?.name || "本机角色卡预览"} />
            <span>本机角色卡预览（2D）</span>
          </div>
        ) : (
          <div className="teacher-avatar-empty">请先导入角色卡</div>
        )}

        <div className="koikatsu-camera">
          <label><span>视角旋转</span><input aria-label="视角旋转" type="range" min={-180} max={180} step={1} value={rotation} onChange={event => setRotation(Number(event.target.value))} /><span>{Math.round(rotation)}°</span></label>
          <label><span>角色大小</span><input aria-label="角色大小" type="range" min={0.55} max={2.7} step={0.01} value={zoom} onChange={event => setZoom(Number(event.target.value))} /><span>{zoom.toFixed(2)}x</span></label>
        </div>

        {preview && (
          <div className="koikatsu-import-status" role="status">
            <b>{parameterShellOnly ? "通用参数白模已载入" : use3dPreview ? "3D 角色已载入" : "角色卡参数已读取"}</b>
            <span>{parameterShellOnly
              ? "身体与脸部参数可直接调整；完整外观需要匹配的角色资源包。"
              : use3dPreview
              ? "参数修改会实时反映到 3D 预览。"
              : "尚未安装匹配的角色资源包，当前显示卡片预览和通用参数。"}</span>
          </div>
        )}
      </section>

      <section className="koikatsu-panel">
        <label className="koikatsu-file">
          <Upload size={16} />
          <span>{file?.name || "导入 Koikatsu Sunshine 角色卡 PNG"}</span>
          <input type="file" accept="image/png,.png" onChange={event => void loadFile(event.target.files?.[0] || null)} disabled={busy} />
        </label>

        <div className="koikatsu-tabs">
          <button className={tab === "body" ? "active" : ""} onClick={() => setTab("body")}>身体参数</button>
          <button className={tab === "face" ? "active" : ""} onClick={() => setTab("face")}>面部参数</button>
        </div>

        <div className="koikatsu-sliders">
          {sliders.map(item => {
            const key = tab === "body" ? item.key! : String(item.index!)
            const value = values[key] ?? item.value
            return <label key={key}><span>{item.label}<b>{Math.round(value * 100)}</b></span><input type="range" min={item.min} max={item.max} step=".01" value={value} onChange={event => setValues(current => ({ ...current, [key]: Number(event.target.value) }))} /></label>
          })}
        </div>
      </section>
    </div>

    <section className="koikatsu-persona">
      <h3>角色参数</h3>
      <p>建议先在“身体参数”和“面部参数”里调好，再导出卡片。</p>
      <div className="koikatsu-actions">
        <button type="button" onClick={() => card && (setBody(valuesOf(card, "body")), setFace(valuesOf(card, "face")))} disabled={!changed || busy}>恢复默认参数</button>
        <button type="button" onClick={() => void downloadCard()} disabled={!card || busy}><Download size={15} />下载修改后的卡片</button>
      </div>
    </section>

    {notice && <div className="teacher-avatar-notice" role="status">{notice}</div>}
    {runtimeHeadFeedback && <div className="teacher-avatar-notice" role="status">{runtimeHeadFeedback}</div>}
  </section>
}
