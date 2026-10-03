export const fixture = {
  checks: 0, installs: 0, newest: true, mandatory: false,
  status: { available: true, busy: false, state: "HEALTHY", version: "2.0.1", current: "2.0.1", message: "尚未检查更新。" } as any,
}
export async function call(method: string) {
  if (method === "app.bootstrap") return { version: "2.0.1", ready: false, account: { name: "合成用户", role_label: "学习者" },
    settings: { theme: "sakura", reduce_motion: true, detail: "brief", analogy_domains: ["life"], refresh_time: "04:00", font_scale: 1 }, options: {}, notice: null }
  if (method === "plans.list") return { plans: [], today: { target_minutes: 0, confirmed_minutes: 0 } }
  if (method === "sync.status") return null
  if (method === "classroom.enter" || method === "classroom.state") return {
    plan: { id: "synthetic", goal: "测试课", state: "active" }, module: { id: "m", title: "测试模块", number: 1, total: 1, state: "active" },
    points: [], step: "teach", question: null, messages: [], can_skip: false, red: [], more_modules: false, check: null, today: null,
  }
  if (method === "update.status") return { ...fixture.status }
  if (method === "update.defer") { fixture.status = { ...fixture.status, deferred: true }; return { ...fixture.status } }
  if (method === "update.check" || method === "update.automatic") {
    fixture.checks++
    fixture.status = { ...fixture.status, state: "CHECKING", busy: true }
    setTimeout(() => { fixture.status = { available: true, busy: false, state: fixture.newest ? "UPDATE_AVAILABLE" : "UP_TO_DATE",
      version: "2.0.1", current: "2.0.1", latest: fixture.newest ? "2.0.2" : "2.0.1", message: "检查完成",
      release_notes: "慢网自动续传与新课堂", mandatory: fixture.mandatory, deferred: fixture.status.deferred } }, 30)
    return { ...fixture.status }
  }
  if (method === "update.install") {
    fixture.installs++
    fixture.status = { ...fixture.status, state: "DOWNLOADING", busy: true, downloaded_bytes: 2 * 1024 * 1024, expected_download_bytes: 10 * 1024 * 1024 }
    return { ...fixture.status }
  }
  return {}
}
export function on() { return () => {} }
export class BridgeFailure extends Error {}
