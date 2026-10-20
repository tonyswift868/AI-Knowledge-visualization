/** Offline bridge for the real React classroom and settings pages. */
export const fixture177: any = {
  accountId: null as string | null,
  enterCalls: 0,
  draftGets: [] as string[],
  draftSets: [] as { account: string | null; text: string }[],
  durable: new Map<string, string>(),
  deferNextDraft: false,
  resolveDeferredDraft: null as null | ((value: { text: string }) => void),
  settingsByAccount: new Map<string, any>(),
  settingsSaves: [] as { account: string; changes: any }[],
}

export class BridgeFailure extends Error {
  code: string
  constructor(code: string, message: string) { super(message); this.code = code }
}
export const on = () => () => undefined
export async function connect() { return true }
export async function call<T = any>(method: string, params: any = {}): Promise<T> {
  if (method === "classroom.enter") {
    fixture177.enterCalls++
    return {
      plan: { id: params.plan_id, goal: "隔离测试", state: "active" },
      module: { id: "m177", title: "测试模块", number: 1, total: 1, state: "active" },
      points: [{ id: "p177", title: "测试点", minutes: 10, taught: false, state: "active", current: true }],
      step: "teach", question: null, messages: [], can_skip: false, red: [], more_modules: false,
      check: null, conversation: null, teaching_version: null, direct_review: null, preparation: null,
      failure: null, explanation: null, today: null,
    }
  }
  if (method === "classroom.draft_get") {
    const account = fixture177.accountId as string | null
    fixture177.draftGets.push(account ?? "<unresolved>")
    if (fixture177.deferNextDraft) {
      fixture177.deferNextDraft = false
      return await new Promise(resolve => { fixture177.resolveDeferredDraft = resolve as any }) as T
    }
    return { text: account ? fixture177.durable.get(`${account}:${params.plan_id}`) || "" : "" } as T
  }
  if (method === "classroom.draft_set") {
    fixture177.draftSets.push({ account: fixture177.accountId, text: params.text || "" })
    const account = fixture177.accountId
    if (account) fixture177.durable.set(`${account}:${params.plan_id}`, params.text || "")
    return { saved: true } as T
  }
  if (method === "settings.get") {
    const account = fixture177.accountId || "<unresolved>"
    return { settings: fixture177.settingsByAccount.get(account) || settingsDefaults() } as T
  }
  if (method === "settings.save") {
    const account = fixture177.accountId || "<unresolved>"
    const settings = { ...(fixture177.settingsByAccount.get(account) || settingsDefaults()), ...params.changes }
    fixture177.settingsByAccount.set(account, settings)
    fixture177.settingsSaves.push({ account, changes: params.changes })
    return { settings } as T
  }
  if (method === "classroom.leave" || method === "classroom.cancel_prefetch" || method === "classroom.prefetch") return { ok: true } as T
  if (method === "classroom.state") return {} as T
  if (method === "app.bootstrap") return { ready: true, account: null, settings: settingsDefaults(), options: {}, notice: null } as T
  if (method === "update.status") return { available: false, busy: false, state: "HEALTHY", version: "test", current: "test", message: "测试" } as T
  if (method === "models.status") return { state: "unknown", short: "测试" } as T
  if (method === "sync.status") return null as T
  if (method.startsWith("speech.")) return { ready: false, components: [], download: null } as T
  return { ok: true } as T
}
function settingsDefaults() {
  return { version: 1, theme: "sakura", font_scale: 1, reduce_motion: true, teacher_style: "warm", detail: "normal",
    examples: "normal", analogy_domains: ["life"], refresh_time: "04:00", reminder_enabled: false,
    reminder_time: "20:00", chat_persona: "" }
}
