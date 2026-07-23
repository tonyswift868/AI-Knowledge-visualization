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
}
