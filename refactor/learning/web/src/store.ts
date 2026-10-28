import { create } from "zustand"

export type Route = "home" | "plans" | "plan" | "new-plan" | "classroom" | "report" | "tools" | "chat" | "rag" | "settings"
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
  show_plan_name_in_notification: boolean
  chat_persona: string
  teacher_presets?: TeacherPreset[]
  default_teacher_preset_id?: string
}
export type SettingsSnapshot = { settings: Settings; settings_revision?: number; account_id?: string }
export type SettingsSaveTicket = { id: number; scope: number; account_id?: string; changes: Partial<Settings>; previous: Partial<Settings> }

export type Account = { name: string; nickname?: string; signature?: string; avatar_url?: string | null; account_id?: string; role_label: string; settings_available?: boolean; offline?: boolean }
export type Notice = { message: string; show_verify: boolean } | null
export type SyncStatus = {
  state: "syncing" | "waiting" | "retrying" | "synced" | "failed" | "reauth" | "superseded"
  at: string | null
  message: string
  phase?: string
  show_progress?: boolean
  /** Null means the backend does not know the queue size. */
  pending?: number | null
  /** Backend-provided wall-clock retry time; the UI must not estimate one. */
  retry_at?: string | null
  last_error?: { code: string; message: string; at: string } | null
} | null
export type Toast = { id: number; kind: "ok" | "error" | "info"; text: string }
export type ModelFault = { id?: string; category: string; code: string; message: string; short: string; purpose?: string; plan_id?: string;
  actions: { id: string; label: string; url?: string }[] }
export type ModelStatus = { state: 'unknown' | 'checking' | 'unconfigured' | 'ok' | 'error'; short: string; fault?: ModelFault; account_id?: string }

type State = {
  route: Route
  settingsTab: SettingsTab
  planId: string | null
  sync: SyncStatus
  account: Account | null
  settings: Settings | null
  confirmedSettings: Settings | null
  settingsRevision: number
  settingsScopeRevision: number
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
  applySettingsSnapshot: (snapshot: SettingsSnapshot, expectedScope: number, account?: Account | null) => boolean
  resetSettingsScope: () => void
  beginSettingsSave: (changes: Partial<Settings>) => SettingsSaveTicket
  finishSettingsSave: (ticket: SettingsSaveTicket, snapshot: SettingsSnapshot) => boolean
  failSettingsSave: (ticket: SettingsSaveTicket) => void
  toast: (kind: Toast["kind"], text: string) => void
  dismiss: (id: number) => void
}

let toastId = 0
let settingsSaveId = 0
const pendingSettingsFields = new Map<keyof Settings, number>()

export const useApp = create<State>((set, get) => ({
  route: "home",
  settingsTab: "models",
  planId: null,
  sync: null,
  account: null,
  settings: null,
  confirmedSettings: null,
  settingsRevision: 0,
  settingsScopeRevision: 0,
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
    modelFault: status.state === 'error' && status.fault
      ? status.fault.id !== s.modelStatus.fault?.id ? status.fault : s.modelFault
      : null})),
  go: (route, tab) => set((s) => ({ route, settingsTab: tab ?? s.settingsTab })),
  set: (patch) => set(patch),
  applySettingsSnapshot: (snapshot, expectedScope, account) => {
    let applied = false
    set((s) => {
      if (s.settingsScopeRevision !== expectedScope) return {}
      const nextAccount = account === undefined ? s.account : account
      if (snapshot.account_id && nextAccount?.account_id && snapshot.account_id !== nextAccount.account_id) return {}
      const revision = Number.isSafeInteger(snapshot.settings_revision) && snapshot.settings_revision! >= 0 ? snapshot.settings_revision! : 0
      if (revision < s.settingsRevision) return account === undefined ? {} : { account: nextAccount }
      const merged = { ...(s.settings ?? snapshot.settings), ...snapshot.settings }
      for (const [field] of pendingSettingsFields) {
        if (Object.prototype.hasOwnProperty.call(s.settings ?? {}, field)) (merged as any)[field] = (s.settings as any)[field]
      }
      applied = true
      return { ...(account === undefined ? {} : { account: nextAccount }), settings: merged, confirmedSettings: snapshot.settings, settingsRevision: revision }
    })
    return applied
  },
  resetSettingsScope: () => {
    pendingSettingsFields.clear()
    set((s) => ({ settings: null, confirmedSettings: null, settingsRevision: 0, settingsScopeRevision: s.settingsScopeRevision + 1 }))
  },
  beginSettingsSave: (changes) => {
    const s = get()
    const id = ++settingsSaveId
    const previous: Partial<Settings> = {}
    for (const field of Object.keys(changes) as (keyof Settings)[]) {
      previous[field] = (s.confirmedSettings ?? s.settings)?.[field] as any
      pendingSettingsFields.set(field, id)
    }
    set((current) => ({ settings: current.settings ? { ...current.settings, ...changes } : current.settings }))
    return { id, scope: s.settingsScopeRevision, account_id: s.account?.account_id, changes, previous }
  },
  finishSettingsSave: (ticket, snapshot) => {
    const s = get()
    if (s.settingsScopeRevision !== ticket.scope || (ticket.account_id && s.account?.account_id !== ticket.account_id) ||
        (snapshot.account_id && ticket.account_id && snapshot.account_id !== ticket.account_id)) return false
    const confirmed: Partial<Settings> = {}
    for (const field of Object.keys(ticket.changes) as (keyof Settings)[]) {
      if (pendingSettingsFields.get(field) !== ticket.id) continue
      pendingSettingsFields.delete(field)
      const latest = s.confirmedSettings
      if (latest && Object.prototype.hasOwnProperty.call(latest, field)) confirmed[field] = latest[field] as any
      else if (Object.prototype.hasOwnProperty.call(ticket.previous, field)) confirmed[field] = ticket.previous[field] as any
    }
    if (Object.keys(confirmed).length) set((current) => ({ settings: current.settings ? { ...current.settings, ...confirmed } : current.settings }))
    get().applySettingsSnapshot(snapshot, ticket.scope)
    return true
  },
  failSettingsSave: (ticket) => {
    const s = get()
    if (s.settingsScopeRevision !== ticket.scope || (ticket.account_id && s.account?.account_id !== ticket.account_id)) return
    const rollback: Partial<Settings> = {}
    for (const field of Object.keys(ticket.changes) as (keyof Settings)[]) {
      if (pendingSettingsFields.get(field) !== ticket.id) continue
      pendingSettingsFields.delete(field)
      const confirmed = s.confirmedSettings
      if (confirmed && Object.prototype.hasOwnProperty.call(confirmed, field)) rollback[field] = confirmed[field] as any
      else if (Object.prototype.hasOwnProperty.call(ticket.previous, field)) rollback[field] = ticket.previous[field] as any
    }
    if (Object.keys(rollback).length) set((current) => ({ settings: current.settings ? { ...current.settings, ...rollback } : current.settings }))
  },
  toast: (kind, text) => {
    if (kind === 'error' && useApp.getState().modelStatus.fault?.message === text) return
    const id = ++toastId
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, kind, text }] }))
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), kind === "error" ? 6000 : 3200)
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))
