import * as React from "react"
import { create } from "zustand"
import { call } from "../bridge"
import { useApp } from "../store"
import { Button } from "../ui"

export type UpdateStatus = {
  available: boolean; busy: boolean; state: string; version: string; current?: string
  latest?: string; target?: string; message?: string; release_notes?: string; mandatory?: boolean
  deferred?: boolean; mandatory_floor?: string; min_supported_version?: string; mandatory_reason?: string
  downloaded_bytes?: number; actual_downloaded_bytes?: number; expected_download_bytes?: number
}
type Notice = { status: UpdateStatus | null; open: boolean; later: boolean; manual: boolean; error: string }
export const useUpdates = create<Notice>(() => ({ status: null, open: false, later: false, manual: false, error: "" }))
const offered = (s: UpdateStatus | null) => !!s && ["AVAILABLE", "UPDATE_AVAILABLE", "OPTIONAL_UPDATE", "MANDATORY_UPDATE", "PENDING_INSTALL"].includes(s.state)
const mandatory = (s: UpdateStatus | null) => !!s?.mandatory || s?.state === "MANDATORY_UPDATE"
export const hasUpdate = () => offered(useUpdates.getState().status) || useUpdates.getState().later
let started = false
let nextAutomaticAt = 0
let pendingCheck: "automatic" | "manual" | null = null
let dispatching = false
function acceptStatus(status: UpdateStatus) {
  useUpdates.setState({ status, ...(status.deferred === undefined ? {} : { later: status.state !== "UP_TO_DATE" && status.deferred }) })
}

export async function checkUpdate(manual = true) {
  pendingCheck = manual ? "manual" : pendingCheck ?? "automatic"
  useUpdates.setState({ error: "", ...(manual ? { manual: true } : {}) })
  await dispatchCheck()
}

async function dispatchCheck() {
  if (pendingCheck === null || dispatching) return
  dispatching = true
  try {
    const current = await call<UpdateStatus>("update.status")
    if (current.busy) return // startup/apply still owns the one-request worker
    const s = await call<UpdateStatus>(pendingCheck === "manual" ? "update.check" : "update.automatic")
    pendingCheck = null
    acceptStatus(s)
  } catch (e) {
    pendingCheck = null
    useUpdates.setState({ error: (e as Error).message, manual: false })
  } finally { dispatching = false }
}

export async function installUpdate() {
  useUpdates.setState({ open: true, error: "" })
  try { acceptStatus(await call<UpdateStatus>("update.install")) }
  catch (e) { useUpdates.setState({ error: (e as Error).message }) }
}

async function deferUpdate() {
  if (mandatory(useUpdates.getState().status)) return
  useUpdates.setState({ open: false, later: true, manual: false })
  try { acceptStatus(await call<UpdateStatus>("update.defer")) }
  catch (e) { useUpdates.setState({ error: (e as Error).message }) }
}

export function UpdateNotice() {
  const ready = useApp((s) => s.ready)
  const route = useApp((s) => s.route)
  const toast = useApp((s) => s.toast)
  const notice = useUpdates()
  const status = notice.status
  React.useEffect(() => {
    if (!ready) return // only the logged-in main interface runs discovery
    let live = true
    const refresh = async () => {
      try {
        if (Date.now() >= nextAutomaticAt && pendingCheck === null) {
          nextAutomaticAt = Date.now() + 24 * 60 * 60 * 1000
          await checkUpdate(false)
        }
        if (pendingCheck !== null) await dispatchCheck()
        const s = await call<UpdateStatus>("update.status")
        if (live) acceptStatus(s)
      } catch (e) { if (live) useUpdates.setState({ error: (e as Error).message }) }
    }
    if (!started) { started = true; nextAutomaticAt = Date.now() + 24 * 60 * 60 * 1000; void checkUpdate(false) }
    void refresh()
    const poll = setInterval(refresh, 1000)
    return () => { live = false; clearInterval(poll) }
  }, [ready])

  React.useEffect(() => {
    if (!ready || !status || status.busy || pendingCheck !== null) return
    if (offered(status)) {
      // All classroom modes share this route. Defer until home/plan; manual
      // discovery in Settings uses exactly the same dialog.
      const safe = ["home", "plans", "plan"].includes(route) || notice.manual && route === "settings"
      if (safe && (notice.manual || !notice.later || mandatory(status))) useUpdates.setState({ open: true, manual: false })
    } else {
      if (status.state === "UP_TO_DATE" && (notice.open || notice.later)) useUpdates.setState({ open: false, later: false })
      if (!notice.manual || !["UP_TO_DATE", "FAILED_RECOVERABLE", "FAILED_SECURITY", "UNPACKAGED"].includes(status.state)) return
      toast(status.state === "UP_TO_DATE" ? "ok" : "info", status.state === "UP_TO_DATE"
        ? `已是最新版本 ${status.current || status.version}` : status.message || "暂时无法检查更新。")
      useUpdates.setState({ manual: false })
    }
  }, [ready, route, status, notice.manual, notice.later])

  if (!notice.open || !ready || route === "classroom") return null
  const downloading = !!status?.busy || ["APPLYING", "RESTART_REQUIRED", "UPDATE_DOWNLOADED"].includes(status?.state || "")
  const total = status?.expected_download_bytes || 0
  const received = status?.downloaded_bytes ?? status?.actual_downloaded_bytes ?? 0
  const bytes = (v: number) => `${(v / 1024 / 1024).toFixed(2)} MiB`
  return <div role="presentation" style={{ position: "fixed", inset: 0, background: "#0008", display: "grid", placeItems: "center", zIndex: 1000 }}>
    <section role="dialog" aria-modal="true" aria-label="MentorFlow 更新" className="card stack" style={{ width: "min(540px, 90vw)", padding: 28, maxHeight: "80vh", overflowY: "auto" }}>
      <h2>MentorFlow 更新</h2>
      <p>当前版本 {status?.current || status?.version} → 新版本 {status?.latest || status?.target}</p>
      <p style={{ whiteSpace: "pre-wrap" }}>{status?.release_notes || "此版本未提供更新说明。"}</p>
      <p>更新完成后会自动重启，并继续当前登录（仅这一次）。</p>
      {mandatory(status) && <p>版本 {status?.mandatory_floor || status?.min_supported_version || status?.latest} 必须安装：{({security:"安全修复",protocol:"更新协议兼容要求",data_compatibility:"数据兼容要求",support_floor:"版本兼容要求"} as Record<string,string>)[status?.mandatory_reason || "support_floor"] || status?.mandatory_reason}</p>}
      <p role="status">{notice.error || status?.message}</p>
      {downloading && <div aria-label="下载进度"><progress max={Math.max(1, total)} value={Math.min(total, received)} style={{ width: "100%" }} /><p>{bytes(received)} / {bytes(total)}</p></div>}
      <div className="row">
        <Button kind="primary" busy={!!status?.busy} disabled={downloading || status?.state === "FAILED_SECURITY"} onClick={() => void installUpdate()}>
          {status?.state === "DOWNLOAD_PAUSED_OR_INTERRUPTED" ? "继续" : mandatory(status) ? "立即更新" : "立即更新并重启"}
        </Button>
        <Button disabled={mandatory(status) || downloading} onClick={() => void deferUpdate()}>稍后</Button>
      </div>
    </section>
  </div>
}
