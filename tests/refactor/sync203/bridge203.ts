type Listener = (data: any) => void
const listeners = new Map<string, Set<Listener>>()
let status: any = { state: "retrying", at: "2026-10-27T08:15:00Z", message: "", pending: 7,
  retry_at: new Date(Date.now() + 60_000).toISOString(),
  last_error: { code: "SYNC_UNAVAILABLE", message: "网络暂时不可用", at: "2026-10-27T08:15:20Z" } }
let retryCalls = 0
const settings = { version: 1, theme: "sakura", font_scale: 1, reduce_motion: true, teacher_style: "warm", detail: "normal",
  examples: "normal", analogy_domains: [], refresh_time: "04:00", reminder_enabled: false, reminder_time: "20:00",
  show_plan_name_in_notification: true, chat_persona: "warm" }
const push = (name: string, data: any) => listeners.get(name)?.forEach(listener => listener(data))
export async function call(method: string): Promise<any> {
  if (method === "app.bootstrap") return { account: { name: "测试账号", account_id: "sync203", role_label: "学习者" }, settings, options: {}, notice: null, ready: true }
  if (method === "settings.get") return { settings, options: {} }
  if (method === "sync.status") return status
  if (method === "sync.now") {
    retryCalls++
    if (retryCalls === 1) {
      status = { state: "synced", at: "2026-10-27T08:16:25Z", message: "", pending: 0, retry_at: null,
        last_error: { code: "SYNC_UNAVAILABLE", message: "网络暂时不可用", at: "2026-10-27T08:15:20Z" } }
      push("sync_status", status)
    }
    return status
  }
  if (method === "models.status") return { state: "ok", short: "模型正常", account_id: "sync203" }
  if (method === "update.status") return { available: false, busy: false, state: "UP_TO_DATE" }
  return {}
}
export function on(name: string, listener: Listener): () => void {
  if (!listeners.has(name)) listeners.set(name, new Set())
  listeners.get(name)!.add(listener)
  return () => listeners.get(name)?.delete(listener)
}
;(window as any).sync203 = {
  setStatus(next: any) { status = next; push("sync_status", next) },
  currentStatus() { return status },
  retryCalls() { return retryCalls },
}
