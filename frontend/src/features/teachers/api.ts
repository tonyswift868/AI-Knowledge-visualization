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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = payload?.detail
    throw new Error(detail?.message || payload?.message || `请求失败（${response.status}）`)
  }
  return payload as T
}

export const teacherAvatarApi = {
  list: () => request<{ items: TeacherAvatar[]; motion_library: string[] }>("/teacher-avatars"),
  today: (workspaceId: string) => request<{ item: TeacherAvatar | null; requires_import: boolean; rotation_key: string }>(`/teacher-avatars/today?workspace_id=${encodeURIComponent(workspaceId || "default")}`),
  importModel: (body: FormData) => request<{ item: TeacherAvatar }>("/teacher-avatars/import", { method: "POST", body }),
  updateMorph: (modelId: string, morph: Partial<TeacherAvatarMorph>) => request<{ item: TeacherAvatar }>(`/teacher-avatars/${encodeURIComponent(modelId)}/morph`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(morph) }),
  inspectKoikatsuCard: async (_body: FormData): Promise<{ card: KoikatsuCardInspection }> => {
    throw new Error("KKS_RUNTIME_UNAVAILABLE: 恋活角色卡解析运行时尚未恢复")
  },
  exportKoikatsuCard: async (_body: FormData): Promise<Blob> => {
    throw new Error("KKS_RUNTIME_UNAVAILABLE: 恋活角色卡导出运行时尚未恢复")
  },
}
