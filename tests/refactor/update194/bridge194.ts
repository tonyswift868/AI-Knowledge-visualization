import { beginActivity, endActivity } from "../../../refactor/learning/web/src/loading/activity"

export type UpdateScenario = {
  mode: string;
  response: any;
  fail: boolean;
  calls: string[];
  pending: number;
  hold: boolean;
  releasePending: null | (() => void);
}

export const fixture194: UpdateScenario = {
  mode: "idle",
  response: { available: true, busy: false, state: "UP_TO_DATE", version: "9.194.0", current: "9.194.0", message: "已是最新版本。" },
  fail: false,
  calls: [],
  pending: 0,
  hold: false,
  releasePending: null,
}

export class BridgeFailure extends Error {
  code: string
  constructor(code: string, message: string) { super(message); this.code = code }
}

export const on = () => () => undefined
export async function connect() { return true }

export async function call(method: string, _params: any = {}) {
  fixture194.calls.push(method)
  if (method === "models.providers") return { providers: [{ id: "deepseek", name: "DeepSeek", default_base_url: "https://example.invalid", default_model: "fixture", search_supported: false, search_verified: false }] }
  if (method === "models.list") return { presets: [], active_id: null }
  if (method === "models.status") return { state: "unknown", short: "模型待验证" }
  if (method === "settings.save") return { settings: _params.changes }
  if (method === "update.status") {
    if (fixture194.mode === "checking" && fixture194.pending > 0)
      return { ...fixture194.response, busy: true, state: "CHECKING", message: "正在检查更新…" }
    if (fixture194.mode === "installing" && fixture194.pending > 0)
      return { ...fixture194.response, busy: true, state: "DOWNLOADING", download_state: "PREPARING",
        message: "正在准备新版本…" }
    return { ...fixture194.response }
  }
  if (method === "update.automatic" || method === "update.check") {
    const activityId = `update194-${fixture194.calls.length}`
    fixture194.mode = "checking"
    beginActivity(activityId, method)
    fixture194.pending++
    try {
      if (fixture194.hold) await new Promise<void>(resolve => { fixture194.releasePending = resolve })
      if (fixture194.fail) throw new BridgeFailure("UPDATE_UNAVAILABLE", "更新服务暂时无法连接。")
      return { ...fixture194.response }
    } finally { fixture194.pending--; fixture194.mode = "idle"; fixture194.releasePending = null; endActivity(activityId) }
  }
  if (method === "update.install") {
    const activityId = `update194-${fixture194.calls.length}`
    fixture194.mode = "installing"
    beginActivity(activityId, method)
    fixture194.pending++
    try {
      if (fixture194.hold) await new Promise<void>(resolve => { fixture194.releasePending = resolve })
      fixture194.response = { ...fixture194.response, busy: true, state: "DOWNLOADING", download_state: "DOWNLOADING",
        downloaded_bytes: 3145728, expected_download_bytes: 8388608, downloaded_files: 3, total_download_files: 8 }
      return { ...fixture194.response }
    } finally { fixture194.pending--; fixture194.mode = "idle"; fixture194.releasePending = null; endActivity(activityId) }
  }
  if (method === "update.defer") return { ...fixture194.response, deferred: true }
  return { ok: true }
}
