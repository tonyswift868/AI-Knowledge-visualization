import { create } from "zustand"

export type Route = "home" | "plans" | "plan" | "new-plan" | "classroom" | "report" | "tools" | "chat" | "settings"
export type SettingsTab = "models" | "usage" | "learning" | "time" | "appearance" | "account"

export type TeacherPreset = { id: string; name: string; persona: string; style: string; detail: string; examples: string; analogies: string[] }
export type PointLink = { from: string; to: string; kind: "prerequisite" | "related" }
export type PointLinksStatus = "none" | "running" | "ready" | "partial" | "failed"
export type KnowledgePoint = { id: string; title: string; summary: string; minutes: number; state?: string; taught?: boolean; needs_review?: boolean;
  citations?: { chunk_id: string; title: string; url: string | null; locator: string; quote: string }[] }
export type KnowledgeModule = { id: string; title: string; summary: string; prerequisites?: string[]; state?: string | null;
  question_count?: number; passed_questions?: number; learning_minutes: number; check_minutes: number; points: KnowledgePoint[] }
export type KnowledgeDetail = { plan: { id: string; goal: string }; stages: {id: string; title: string; modules: KnowledgeModule[]}[];
  point_links?: PointLink[]; point_links_status?: PointLinksStatus }
export type Settings = {
  version: number
  theme: "sakura" | "aurora"
  font_scale: number
  reduce_motion: boolean
  teacher_style: string
  detail: string
  examples: string
  analogy_domains: string[]
  refresh_time: string
  reminder_enabled: boolean
  reminder_time: string
  chat_persona: string
  teacher_presets?: TeacherPreset[]
  default_teacher_preset_id?: string
}

export type Account = { name: string; role_label: string; settings_available?: boolean }
export type Notice = { message: string; show_verify: boolean } | null
export type SyncStatus = { state: "syncing" | "synced" | "failed" | "reauth" | "superseded"; at: string | null; message: string; phase?: string; show_progress?: boolean } | null
export type Toast = { id: number; kind: "ok" | "error" | "info"; text: string }
export type ModelFault = { id?: string; category: string; code: string; message: string; short: string; purpose?: string; plan_id?: string;
  actions: { id: string; label: string; url?: string }[] }
export type ModelStatus = { state: 'unknown' | 'ok' | 'error'; short: string; fault?: ModelFault }

type State = {
  route: Route
  settingsTab: SettingsTab
  planId: string | null
  sync: SyncStatus
  account: Account | null
  settings: Settings | null
  options: Record<string, string[]>
  notice: Notice
  ready: boolean
  connected: boolean
  fullscreen: boolean
  sidebarCollapsed: boolean
  toasts: Toast[]
  modelStatus: ModelStatus
  modelFault: ModelFault | null
  modelRetry: (() => void) | null
  setModelStatus: (status: ModelStatus) => void
  go: (route: Route, tab?: SettingsTab) => void
  set: (patch: Partial<State>) => void
  toast: (kind: Toast["kind"], text: string) => void
  dismiss: (id: number) => void
}

let toastId = 0

export const useApp = create<State>((set) => ({
  route: "home",
  settingsTab: "models",
  planId: null,
  sync: null,
  account: null,
  settings: null,
  options: {},
  notice: null,
  ready: false,
  connected: false,
  fullscreen: false,
  sidebarCollapsed: false,
  toasts: [],
  modelStatus: {state:'unknown', short:'模型待验证'},
  modelFault: null,
  modelRetry: null,
  setModelStatus: (status) => set((s) => ({modelStatus:status,
    modelFault: status.state === 'ok' ? null : status.fault && status.fault.id !== s.modelStatus.fault?.id ? status.fault : s.modelFault})),
  go: (route, tab) => set((s) => ({ route, settingsTab: tab ?? s.settingsTab })),
  set: (patch) => set(patch),
  toast: (kind, text) => {
    if (kind === 'error' && useApp.getState().modelStatus.fault?.message === text) return
    const id = ++toastId
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, kind, text }] }))
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), kind === "error" ? 6000 : 3200)
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))
