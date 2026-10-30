import { beginActivity, endActivity } from "../../../refactor/learning/web/src/loading/activity"

export const fixture258 = {
  status: { available: true, busy: false, state: "OPTIONAL_UPDATE", version: "2.0.39", current: "2.0.39", target: "2.0.40", message: "发现新版本。" } as any,
  calls: [] as string[],
}
export const on = () => () => undefined
export async function connect() { return true }
export async function call(method: string, params: any = {}) {
  fixture258.calls.push(method)
  if (method === "update.status" || method === "update.automatic" || method === "update.check") return { ...fixture258.status }
  if (method === "update.install") return { ...fixture258.status, busy: true, state: "DOWNLOADING" }
  if (method === "update.defer") return { ...fixture258.status, deferred: true }
  if (method === "settings.get") return { settings: params }
  return { ok: true }
}
