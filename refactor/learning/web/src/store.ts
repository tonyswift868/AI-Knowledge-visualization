import { create } from "zustand"

export type Route = "home" | "plans" | "plan" | "new-plan" | "tools" | "chat" | "settings"
export type SettingsTab = "models" | "usage" | "learning" | "time" | "appearance" | "account"

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
}

export type Account = { name: string; role_label: string; settings_available?: boolean }
export type Notice = { message: string; show_verify: boolean } | null
export type SyncStatus = { state: "syncing" | "synced" | "failed" | "reauth" | "superseded"; at: string | null; message: string } | null
export type Toast = { id: number; kind: "ok" | "error" | "info"; text: string }

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
  go: (route, tab) => set((s) => ({ route, settingsTab: tab ?? s.settingsTab })),
  set: (patch) => set(patch),
  toast: (kind, text) => {
    const id = ++toastId
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, kind, text }] }))
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), kind === "error" ? 6000 : 3200)
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))
