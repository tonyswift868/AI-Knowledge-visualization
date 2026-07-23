export type TeacherAvatarMorph = {
  height: number
  head_scale: number
  upper_chest: number
  shoulder_width: number
  smile: number
  blink: number
}

export type TeacherAvatar = {
  model_id: string
  display_name: string
  creator: string
  format: "vrm" | "glb"
  source_url: string
  license_name: string
  license_url?: string
  model_url: string
  motions: string[]
  morph: TeacherAvatarMorph
  adult_character_confirmed: boolean
  rights_confirmed: boolean
  imported_at: string
}

export type KoikatsuCardSlider = {
  key?: string
  index?: number
  label: string
  value: number
  min: number
  max: number
}

export type KoikatsuCardInspection = {
  character: { display_name?: string; nickname?: string }
  body: KoikatsuCardSlider[]
  face: KoikatsuCardSlider[]
  native_summary: { body_slider_count: number; face_slider_count: number; has_mod_data: boolean }
}

export type KoikatsuRuntimeStatus = "ready" | "partial" | "unavailable"
export type KoikatsuCapability = {
  available: boolean
  status: KoikatsuRuntimeStatus
  code: "KKS_RUNTIME_READY" | "KKS_RUNTIME_PARTIAL" | "KKS_RUNTIME_UNAVAILABLE"
  fallback: "teacher_without_avatar"
  reason: string
  runtime_version_hash: string
  index_version: string
  operations: Record<string, boolean>
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = payload?.detail
    const appError = payload?.error
    const message = appError?.message || detail?.message || payload?.message || `Request failed (${response.status})`
    throw new Error(appError?.code ? `${appError.code}: ${message}` : message)
  }
  return payload as T
}

export const teacherAvatarApi = {
  capability: () => request<KoikatsuCapability>("/koikatsu/capability"),
  readiness: () => request<KoikatsuCapability & { manifest: Record<string, unknown> }>("/koikatsu/readiness"),
  runtimeManifest: () => request<Record<string, unknown>>("/koikatsu/runtime-manifest"),
  list: () => request<{ items: TeacherAvatar[]; motion_library: string[] }>("/teacher-avatars"),
  today: (workspaceId: string) => request<{ item: TeacherAvatar | null; requires_import: boolean; rotation_key: string }>(`/teacher-avatars/today?workspace_id=${encodeURIComponent(workspaceId || "default")}`),
  importModel: (body: FormData) => request<{ item: TeacherAvatar }>("/teacher-avatars/import", { method: "POST", body }),
  updateMorph: (modelId: string, morph: Partial<TeacherAvatarMorph>) => request<{ item: TeacherAvatar }>(`/teacher-avatars/${encodeURIComponent(modelId)}/morph`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(morph) }),
  inspectKoikatsuCard: (body: FormData) => request<{ card: KoikatsuCardInspection }>("/teacher-avatars/koikatsu/inspect", { method: "POST", body }),
  exportKoikatsuCard: async (body: FormData): Promise<Blob> => {
    const response = await fetch("/api/teacher-avatars/koikatsu/export", { method: "POST", body })
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}))
      const appError = payload?.error
      throw new Error(appError?.code ? `${appError.code}: ${appError.message || "KKS export unavailable"}` : `Request failed (${response.status})`)
    }
    return response.blob()
  },
}
