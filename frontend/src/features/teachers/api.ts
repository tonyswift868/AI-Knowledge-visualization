export type TeacherAvatarMorph = {
  height: number
  head_scale: number
  upper_chest: number
  shoulder_width: number
  waist_width: number
  hip_width: number
  arm_length: number
  leg_length: number
  smile: number
  blink: number
}

export type TeacherAvatar = {
  model_id: string
  display_name: string
  creator: string
  format: "vrm" | "glb" | "pmx" | "runtime-pack"
  source_url: string
  license_name: string
  license_url?: string
  model_url?: string | null
  runtime_manifest_url?: string
  motions: string[]
  morph: TeacherAvatarMorph
  adult_character_confirmed: boolean
  rights_confirmed: boolean
  imported_at: string
  asset_profile?: "kks-character-pack" | string
  card_fingerprint?: string
  appearance_contract?: {
    status?: "ready" | "incomplete" | "unsupported"
    card_fingerprint?: string
    model_kind?: string
    receipt_hash?: string
  }
}

export type KoikatsuSlider = { key?: string; index?: number; label: string; value: number; min: number; max: number }
export type KoikatsuColor = [number, number, number, number]
export type KoikatsuAbmxModifier = {
  bone_name: string
  bone_location?: number
  scale: [number, number, number]
  length: number
  position: [number, number, number]
  rotation: [number, number, number]
}
export type KoikatsuAppearance = {
  skin_main_color?: KoikatsuColor | null
  skin_sub_color?: KoikatsuColor | null
  detail_id?: number | null
  detail_power?: number | null
  eyebrow_id?: number | null
  eyebrow_color?: KoikatsuColor | null
  nose_id?: number | null
  pupils?: Array<{
    id?: number | null
    base_color?: KoikatsuColor | null
    sub_color?: KoikatsuColor | null
    gradient_mask_id?: number | null
    gradient_blend?: number | null
    gradient_offset_y?: number | null
    gradient_scale?: number | null
  }>
  eye_white_id?: number | null
  eye_white_base_color?: KoikatsuColor | null
  eye_white_sub_color?: KoikatsuColor | null
  pupil_width?: number | null
  pupil_height?: number | null
  pupil_x?: number | null
  pupil_y?: number | null
  eyeline_up_id?: number | null
  eyeline_down_id?: number | null
  eyeline_color?: KoikatsuColor | null
  highlight_up_id?: number | null
  highlight_up_color?: KoikatsuColor | null
  highlight_up_x?: number | null
  highlight_up_y?: number | null
  highlight_down_id?: number | null
  highlight_down_color?: KoikatsuColor | null
  highlight_down_x?: number | null
  highlight_down_y?: number | null
  lip_line_id?: number | null
  lip_line_color?: KoikatsuColor | null
  makeup?: {
    eyeshadow_id?: number | null
    eyeshadow_color?: KoikatsuColor | null
    cheek_id?: number | null
    cheek_color?: KoikatsuColor | null
    lip_id?: number | null
    lip_color?: KoikatsuColor | null
  }
}
export type KoikatsuCardInspection = {
  character: { display_name?: string; nickname?: string; source_game?: string }
  native_summary: { header: string; version: string; sex?: 0 | 1 | null; body_slider_count: number; face_slider_count: number; head_id?: number | null; body_type_bone?: number; abmx_modifier_count?: number; has_mod_data: boolean }
  asset_status?: "ready" | "card-defined" | "pending-mapping" | "pending-mod-data"
  missing_assets?: string[]
  appearance?: KoikatsuAppearance
  abmx_modifiers?: KoikatsuAbmxModifier[]
  body: KoikatsuSlider[]
  face: KoikatsuSlider[]
  native_fingerprint?: string
}
export type KoikatsuTeacher = { teacher_id: string; display_name: string; persona: string; teaching_style: string; card_url: string; native_summary: KoikatsuCardInspection["native_summary"]; created_at: string; updated_at: string }

export type SpineTeacher = {
  teacher_id: string
  display_name: string
  creator: string
  format: "spine-4.1"
  animations: Record<"idle" | "listening" | "thinking" | "explaining" | "encouraging", string>
  skin?: string
  adult_character_confirmed: true
  manifest_url: string
  created_at: string
  updated_at: string
}
export type LocalKoikatsuCard = { card_id: string; filename: string; folder: "female" | "male"; category_path?: string; character: KoikatsuCardInspection["character"]; card_url: string; model_url?: string | null; asset_profile?: string | null; asset_status?: "ready" | "card-defined" | "pending-mapping" | "pending-mod-data" | "invalid"; runtime_cache_status?: "pending" | "running" | "ready" | "partial" | "error" | "unsupported-male" | "unknown"; missing_assets?: string[] }

export type TeacherAvatarBatchImport = {
  items: TeacherAvatar[]
  imported_count: number
  rejected: { filename: string; code: string; message: string }[]
  rejected_count: number
}

export type KksCharacterPack = {
  pack_id: string
  display_name: string
  category?: string
  category_path?: string
  creator: string
  card_fingerprint: string
  size_bytes: number
  installed: boolean
  download_url: string
  asset_profile?: "kks-character-pack" | "kks-runtime-pack" | string
  runtime_manifest_url?: string
}

export type TeacherAvatarCard = {
  format: "mentorflow.teacher-card"
  version: 1
  character: { display_name?: string; creator?: string; [key: string]: unknown }
  body_template_id: string
  morph: Partial<TeacherAvatarMorph>
  face: Record<string, unknown>
  assets: Record<string, string>
  dependencies: string[]
  missing_dependencies: string[]
  source_format: "json" | "png" | "koikatsu_sunshine"
  native_summary?: {
    format: string
    product_no: number
    sex?: 0 | 1 | null
    header: string
    version: string
    blocks: string[]
    has_mod_data: boolean
    body_slider_count: number
    face_slider_count: number
    head_id?: number | null
    body_type_bone?: number
    abmx_modifier_count?: number
    embedded_face_image_bytes: number
  } | null
  resolved_template: Pick<TeacherAvatar, "model_id" | "display_name" | "creator" | "model_url">
}

export type TeacherAvatarCardPreset = {
  card_id: string
  display_name: string
  creator: string
  description: string
  source_format: "json" | "png" | "koikatsu_sunshine"
  body_template_id: string
  morph: Partial<TeacherAvatarMorph>
  assets: Record<string, string>
  dependencies: string[]
  missing_dependencies: string[]
  native_summary?: TeacherAvatarCard["native_summary"]
}

export type TeacherAvatarCardPresetList = {
  items: TeacherAvatarCardPreset[]
  count: number
  total: number
  source: string
  status: "ready" | "partial" | "unavailable"
  available: boolean
  reason?: string | null
  reason_code?: string | null
  declared_count?: number
  unavailable?: { card_id: string; code: string; message: string }[]
}

export type TeacherAvatarPreset = {
  preset_id: string
  display_name: string
  creator: string
  description: string
  source_url: string
  license_name: string
  license_url: string
  license_snapshot: {
    avatar_use: "allow"
    commercial_use: "allow"
    redistribution: "allow"
    modification: string
    credit: string
  }
  preview_url: string
  included: boolean
  active: boolean
  active_model_id?: string | null
  requires_adult_confirmation: boolean
  source_kind?: "bundled" | "remote_redistributable"
  availability?: "download_on_select"
  age_status?: string
}

export type TeacherAvatarPresetList = {
  items: TeacherAvatarPreset[]
  count: number
  total: number
  next_cursor?: string | null
  verified_at: string
  facets?: { tags: { value: string; count: number }[] }
  online_catalog: VroidCatalogStatus
}

export type PersonalAvatarSource = {
  source_id: string
  display_name: string
  franchise: string
  publisher: string
  summary: string
  official_page_url: string
  download_url?: string
  preview_url?: string
  direct_download_url?: string
  download_variants?: { label: string; url: string }[]
  license_url: string
  acquisition_mode: "official_download" | "official_account" | "official_application"
  redistribution: "allow" | "prohibited" | "conditional"
  personal_local_use: "allow" | "conditional"
  requires_account?: boolean
  tags: string[]
  conditions: string[]
  teacher_pool_eligible: boolean
  age_sensitive?: string | boolean | null
}

export type PersonalAvatarSourceList = {
  items: PersonalAvatarSource[]
  count: number
  total: number
  reviewed_at: string
  note: string
}

export type VroidCatalogConnectionStatus = "credentials_required" | "disconnected" | "connected"

export type VroidCatalogStatus = {
  provider: "VRoid Hub" | string
  status: VroidCatalogConnectionStatus
  application_registration_url: string
  catalog_url?: string
  note?: string
  connected_account?: { id: string; name: string; icon_url?: string | null } | null
}

export type VroidCatalogUsageFilter = "teacher_safe" | "commercial"

export type VroidCatalogModel = {
  id: string
  name: string
  creator: { id: string; name: string }
  preview_url?: string | null
  source_url: string
  license: Record<string, string | boolean | null>
  age_limit: { is_r15?: boolean; is_r18?: boolean }
  is_downloadable: boolean
  eligible_for_activation: boolean
  ineligible_reasons: string[]
  tags: string[]
}

export type VroidCatalogSearchResult = {
  items: VroidCatalogModel[]
  count: number
  next_page?: string | null
  usage: VroidCatalogUsageFilter
}

export type TeacherOpenAvatarCatalogItem = {
  catalog_id: string
  collection_id: string
  collection_name: string
  license_code: "CC0" | "CC-BY"
  variant: "standard" | "voxel_vrm"
  has_fallback?: boolean
  eligibility_status: "eligible" | "ineligible" | "unreviewed"
  eligible_for_activation: boolean
  ineligibility_reasons: string[]
  eligibility_audited_at?: string
  display_name: string
  creator: string
  description: string
  preview_url: string
  source_url: string
  license_name: string
  license_url: string
}

export type TeacherOpenAvatarCollection = {
  collection_id: string
  collection_name: string
  count: number
  snapshot_commit: string
  license_code: "CC0" | "CC-BY"
  eligibility?: { eligible: number; ineligible: number; unreviewed: number }
}

export type TeacherOpenAvatarFacet = { value: string; count: number }

export type TeacherOpenAvatarCatalogResult = {
  items: TeacherOpenAvatarCatalogItem[]
  count: number
  total: number
  next_cursor?: string | null
  collections: TeacherOpenAvatarCollection[]
  selected_collection_id?: string | null
  selected_license_code?: string | null
  selected_variant?: string | null
  selected_eligibility_status?: string | null
  facets: { licenses: TeacherOpenAvatarFacet[]; variants: TeacherOpenAvatarFacet[] }
  eligibility_summary: { eligible: number; ineligible: number; unreviewed: number; selectable: number }
  collection: string
  snapshot_commit: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = payload?.detail
    const appError = payload?.error
    const message = appError?.message || detail?.message || payload?.message || `请求失败（${response.status}）`
    throw new Error(appError?.code ? `${appError.code}: ${message}` : message)
  }
  return payload as T
}

async function download(path: string, body: FormData): Promise<Blob> {
  const response = await fetch(`/api${path}`, { method: "POST", body })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const appError = payload?.error
    const message = appError?.message || payload?.detail?.message || `导出失败（${response.status}）`
    throw new Error(appError?.code ? `${appError.code}: ${message}` : message)
  }
  return response.blob()
}

export const teacherAvatarApi = {
  list: () => request<{ items: TeacherAvatar[]; motion_library: string[] }>("/teacher-avatars"),
  characterPacks: () => request<{ items: KksCharacterPack[]; count: number }>("/teacher-avatar-character-packs"),
  presets: ({ query = "", tag, limit, cursor }: { query?: string; tag?: string; limit?: number; cursor?: string } = {}) => {
    const params = new URLSearchParams()
    if (query.trim()) params.set("q", query.trim())
    if (tag) params.append("tag", tag)
    if (limit) params.set("limit", String(limit))
    if (cursor) params.set("cursor", cursor)
    const suffix = params.size ? `?${params.toString()}` : ""
    return request<TeacherAvatarPresetList>(`/teacher-avatar-presets${suffix}`)
  },
  today: (workspaceId: string) => request<{ item: TeacherAvatar | null; requires_import: boolean; rotation_key: string }>(`/teacher-avatars/today?workspace_id=${encodeURIComponent(workspaceId || "default")}`),
  activatePreset: (presetId: string) => request<{ item: TeacherAvatar }>(`/teacher-avatar-presets/${encodeURIComponent(presetId)}/activate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ adult_character_confirmed: true }) }),
  personalSources: (query = "") => {
    const suffix = query.trim() ? `?q=${encodeURIComponent(query.trim())}` : ""
    return request<PersonalAvatarSourceList>(`/teacher-avatar-personal-sources${suffix}`)
  },
  catalogStatus: () => request<VroidCatalogStatus>("/teacher-avatar-catalog/status"),
  catalogAuthorize: () => request<{ authorization_url: string }>("/teacher-avatar-catalog/authorize"),
  searchCatalog: ({ query = "", usage = "teacher_safe", page }: { query?: string; usage?: VroidCatalogUsageFilter; page?: string } = {}) => {
    const params = new URLSearchParams()
    if (query.trim()) params.set("q", query.trim())
    params.set("usage", usage)
    if (page) params.set("page", page)
    const suffix = params.size ? `?${params.toString()}` : ""
    return request<VroidCatalogSearchResult>(`/teacher-avatar-catalog/search${suffix}`)
  },
  activateCatalogModel: (modelId: string) => request<{ item: TeacherAvatar }>(`/teacher-avatar-catalog/${encodeURIComponent(modelId)}/activate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ adult_character_confirmed: true, usage_conditions_confirmed: true }) }),
  openCatalog: ({ query = "", collectionId = "", licenseCode = "", variant = "", eligibilityStatus = "", limit, cursor }: { query?: string; collectionId?: string; licenseCode?: string; variant?: string; eligibilityStatus?: string; limit?: number; cursor?: string } = {}) => {
    const params = new URLSearchParams()
    if (query.trim()) params.set("q", query.trim())
    if (collectionId) params.set("collection_id", collectionId)
    if (licenseCode) params.set("license_code", licenseCode)
    if (variant) params.set("variant", variant)
    if (eligibilityStatus) params.set("eligibility_status", eligibilityStatus)
    if (limit) params.set("limit", String(limit))
    if (cursor) params.set("cursor", cursor)
    const suffix = params.size ? `?${params.toString()}` : ""
    return request<TeacherOpenAvatarCatalogResult>(`/teacher-avatar-open-catalog${suffix}`)
  },
  activateOpenCatalog: (catalogId: string, collectionId: string) => request<{ item: TeacherAvatar }>(`/teacher-avatar-open-catalog/${encodeURIComponent(catalogId)}/activate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ collection_id: collectionId, adult_character_confirmed: true, attribution_confirmed: true }) }),
  importModel: (body: FormData) => request<{ item: TeacherAvatar }>("/teacher-avatars/import", { method: "POST", body }),
  importModelBatch: (body: FormData) => request<TeacherAvatarBatchImport>("/teacher-avatars/import-batch", { method: "POST", body }),
  importKoikatsuLocal: (cardId: string, overrides: {
    display_name?: string
    creator?: string
    source_url?: string
    license_name?: string
    license_url?: string
    rights_confirmed: boolean
    adult_character_confirmed: boolean
  }) => {
    const body = new FormData()
    body.set("display_name", overrides.display_name || "")
    body.set("creator", overrides.creator || "")
    body.set("source_url", overrides.source_url || "")
    body.set("license_name", overrides.license_name || "")
    body.set("license_url", overrides.license_url || "")
    body.set("rights_confirmed", overrides.rights_confirmed ? "true" : "false")
    body.set("adult_character_confirmed", overrides.adult_character_confirmed ? "true" : "false")
    return request<{ item: TeacherAvatar }>(`/teacher-avatars/import/koikatsu-local/${encodeURIComponent(cardId)}`, { method: "POST", body })
  },
  validateCard: (body: FormData) => request<{ card: TeacherAvatarCard }>("/teacher-avatar-cards/validate", { method: "POST", body }),
  inspectKoikatsuCard: (body: FormData) => request<{ filename: string; card: KoikatsuCardInspection }>("/koikatsu-cards/inspect", { method: "POST", body }),
  importKoikatsuCardManifest: (body: FormData, signal?: AbortSignal) => request<{ filename: string; manifest: Record<string, unknown> }>("/koikatsu-cards/import-manifest", { method: "POST", body, signal }),
  exportKoikatsuCard: (body: FormData) => download("/koikatsu-cards/export", body),
  listLocalKoikatsuCards: ({ query = "", limit = 24, offset = 0, assetStatus = "" }: { query?: string; limit?: number; offset?: number; assetStatus?: string } = {}, signal?: AbortSignal) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    if (query.trim()) params.set("q", query.trim())
    if (assetStatus.trim()) params.set("asset_status", assetStatus.trim())
    return request<{ items: LocalKoikatsuCard[]; root_available: boolean; count: number; next_offset: number | null }>(`/koikatsu-cards/local?${params}`, { signal })
  },
  listKoikatsuTeachers: () => request<{ items: KoikatsuTeacher[] }>("/koikatsu-teachers"),
  activeSpineTeacher: () => request<{ item: SpineTeacher | null }>("/spine-teachers/active"),
  importSpineTeacher: (body: FormData) => request<{ item: SpineTeacher }>("/spine-teachers/import", { method: "POST", body }),
  saveKoikatsuTeacher: (body: FormData) => request<{ item: KoikatsuTeacher }>("/koikatsu-teachers", { method: "POST", body }),
  cardPresets: () => request<TeacherAvatarCardPresetList>("/teacher-avatar-card-presets"),
  updateMorph: (modelId: string, morph: Partial<TeacherAvatarMorph>) => request<{ item: TeacherAvatar }>(`/teacher-avatars/${encodeURIComponent(modelId)}/morph`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(morph) }),
}
