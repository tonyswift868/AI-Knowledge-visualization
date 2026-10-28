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
  downloaded_files?: number; total_download_files?: number; download_state?: string
  background_downloading?: boolean; prepared?: boolean
  deferred_update?: boolean; deferred_target?: string; deferred_message?: string
}
type Notice = { status: UpdateStatus | null; open: boolean; later: boolean; manual: boolean; error: string }
export const useUpdates = create<Notice>(() => ({ status: null, open: false, later: false, manual: false, error: "" }))
export const offered = (s: UpdateStatus | null) => !!s && (["AVAILABLE", "UPDATE_AVAILABLE", "OPTIONAL_UPDATE", "MANDATORY_UPDATE", "PENDING_INSTALL"].includes(s.state) || s.state === "HEALTHY" && !!s.deferred_update)
const mandatory = (s: UpdateStatus | null) => !!s?.mandatory || s?.state === "MANDATORY_UPDATE"
type MandatoryUpdateGuard = () => Promise<void>
let mandatoryUpdateGuard: MandatoryUpdateGuard | null = null
export function registerMandatoryUpdateGuard(guard: MandatoryUpdateGuard) {
  mandatoryUpdateGuard = guard
  return () => { if (mandatoryUpdateGuard === guard) mandatoryUpdateGuard = null }
}
async function prepareMandatoryUpdate() { await mandatoryUpdateGuard?.() }
export const hasUpdate = () => offered(useUpdates.getState().status) || useUpdates.getState().later
export function UpdateProgress({status, error = ""}: {status: UpdateStatus | null; error?: string}) {
  if (!status || error || ["FAILED_RECOVERABLE", "FAILED_SECURITY", "UNPACKAGED", "APPLYING", "RESTART_REQUIRED", "UPDATE_DOWNLOADED", "UP_TO_DATE"].includes(status.state)) return null
  const phase = status.download_state || status.state
  if (phase !== 'DOWNLOADING' && phase !== 'PREPARING') return null
  const total = status.expected_download_bytes || 0
  const received = status.downloaded_bytes ?? status.actual_downloaded_bytes ?? 0
  return <div aria-label="更新进度" data-testid="update-progress">
    <progress aria-label="下载进度" max={Math.max(1,total)} value={Math.min(total,received)} style={{width:'100%'}}/>
  </div>
}

/** One concise, accessible description of the current update state. */
export function UpdateStatusSummary({status, error = ""}: {status: UpdateStatus | null; error?: string}) {
  const phase = status?.download_state || status?.state || ""
  const active = !!status?.busy || !!status?.background_downloading
  const received = status?.downloaded_bytes ?? status?.actual_downloaded_bytes ?? 0
  const total = status?.expected_download_bytes || 0
  const version = status?.latest || status?.target
  let message = error
  if (!message && !status) message = "正在检查更新…"
  else if (!message && ["FAILED_RECOVERABLE", "FAILED_SECURITY", "UNPACKAGED"].includes(status?.state || "")) message = status?.message || "暂时无法检查更新。"
  else if (!message && status?.state === "APPLYING") message = status?.message || "正在安装更新，完成后将自动重启。"
  else if (!message && status?.state === "RESTART_REQUIRED") message = status?.message || "正在重新启动…"
  else if (!message && (status?.state === "PENDING_INSTALL" || status?.deferred_update && !status.busy && offered(status)))
    message = status.deferred_message || status.message || "新版已下载好，下次打开软件时完成更新。"
  else if (!message && status?.state === "UPDATE_DOWNLOADED") message = status?.prepared ? "已下载好，点一下就能更新。" : status?.message || "下载完成，正在准备安装。"
  else if (!message && status?.state === "DOWNLOAD_PAUSED_OR_INTERRUPTED") message = status?.message || "下载已暂停，可继续。"
  else if (!message && status?.state === "UP_TO_DATE") message = status.current || status.version ? `已是最新版本 ${status.current || status.version}` : "已是最新版本。"
  else if (!message && active && phase === "DOWNLOADING")
    message = `正在下载 ${status?.downloaded_files || 0}/${status?.total_download_files || 0} 个文件（${(received / 1048576).toFixed(2)} MB / ${(total / 1048576).toFixed(2)} MB）`
  else if (!message && active && phase === "PREPARING") message = "正在准备新版本…"
  else if (!message && status?.prepared) message = "已下载好，点一下就能更新。"
  else if (!message && offered(status)) message = status?.message || `发现新版本 ${version || ""}`.trim()
  else if (!message) message = status?.message || (active ? "正在检查更新…" : "")
  if (!message) return null
  return <p data-testid="update-status" role="status" aria-live="polite">{message}</p>
}
let started = false
let nextAutomaticAt = 0
// User 2026-10-17: re-check every 30 minutes; mandatory releases then lock open apps promptly.
const AUTOMATIC_CHECK_MS = 30 * 60 * 1000
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
  const guardedVersion = React.useRef<string | null>(null)
  const guardRunning = React.useRef(false)
  const [savingClassroom, setSavingClassroom] = React.useState(false)
  const [saveError, setSaveError] = React.useState("")
  const mustGuardClassroom = mandatory(status) && route === "classroom"
  const mandatoryKey = status ? `${status.latest || status.target || status.version}:${status.mandatory_floor || status.min_supported_version || ""}` : ""

  const saveClassroom = React.useCallback((key: string) => {
    if (guardRunning.current) return
    guardRunning.current = true
    setSavingClassroom(true)
    setSaveError("")
    void prepareMandatoryUpdate().then(() => {
      guardedVersion.current = key
      const current = useUpdates.getState().status
      if (mandatory(current) && `${current?.latest || current?.target || current?.version}:${current?.mandatory_floor || current?.min_supported_version || ""}` === key)
        useUpdates.setState({ open: true })
    }).catch((e) => {
      setSaveError((e as Error)?.message || "课堂进度暂时无法保存。请重试后继续更新。")
    }).finally(() => {
      guardRunning.current = false
      setSavingClassroom(false)
    })
  }, [])
  React.useEffect(() => {
    let live = true
    const refresh = async () => {
      try {
        // Status is a local, read-only bridge snapshot. Keep observing it even
        // while account services are reconnecting: a mandatory policy must
        // still lock every route. Only network discovery waits for `ready`.
        if (ready && Date.now() >= nextAutomaticAt && pendingCheck === null) {
          nextAutomaticAt = Date.now() + AUTOMATIC_CHECK_MS
          await checkUpdate(false)
        }
        if (ready && pendingCheck !== null) await dispatchCheck()
        const s = await call<UpdateStatus>("update.status")
        if (live) acceptStatus(s)
      } catch (e) { if (live) useUpdates.setState({ error: (e as Error).message }) }
    }
    if (ready && !started) { started = true; nextAutomaticAt = Date.now() + AUTOMATIC_CHECK_MS; void checkUpdate(false) }
    void refresh()
    const poll = setInterval(refresh, 1000)
    return () => { live = false; clearInterval(poll) }
  }, [ready])

  React.useEffect(() => {
    if (!status || (!ready && !mandatory(status)) || pendingCheck !== null && !mandatory(status)) return
    if (offered(status)) {
      // All classroom modes share this route. Defer until home/plan; manual
      // discovery in Settings uses exactly the same dialog.
      const safe = ["home", "plans", "plan"].includes(route) || notice.manual && route === "settings"
      if (mandatory(status)) {
        if (route === "classroom" && guardedVersion.current !== mandatoryKey) {
          if (!guardRunning.current) saveClassroom(mandatoryKey)
        } else useUpdates.setState({ open: true, manual: false })
      } else if (safe && (notice.manual || !notice.later)) useUpdates.setState({ open: true, manual: false })
    } else {
      if (status.state === "UP_TO_DATE" && (notice.open || notice.later)) useUpdates.setState({ open: false, later: false })
      if (!notice.manual || !["UP_TO_DATE", "FAILED_RECOVERABLE", "FAILED_SECURITY", "UNPACKAGED"].includes(status.state)) return
      if (route !== "settings") toast(status.state === "UP_TO_DATE" ? "ok" : "info", status.state === "UP_TO_DATE"
        ? `已是最新版本 ${status.current || status.version}` : status.message || "暂时无法检查更新。")
      useUpdates.setState({ manual: false })
    }
  }, [ready, route, status, notice.manual, notice.later, mandatoryKey, saveClassroom])

  const blocking = mustGuardClassroom && guardedVersion.current !== mandatoryKey
  const showSaving = blocking && (savingClassroom || (!saveError && !guardRunning.current))
  const retrySave = () => { if (mandatoryKey) saveClassroom(mandatoryKey) }
  React.useLayoutEffect(() => {
    const shell = document.querySelector<HTMLElement>(".shell")
    if (!shell) return
    const lock = mandatory(status) && (notice.open || blocking)
    shell.inert = !!lock
    if (lock) shell.setAttribute("aria-hidden", "true")
    else shell.removeAttribute("aria-hidden")
    return () => { shell.inert = false; shell.removeAttribute("aria-hidden") }
  }, [ready, status, notice.open, blocking])

  if (!ready && !mandatory(status)) return null
  if (blocking) return <div role="presentation" style={{ position: "fixed", inset: 0, background: "#0008", display: "grid", placeItems: "center", zIndex: 1000 }}>
    <section role="dialog" aria-modal="true" aria-label="必须更新前保存课堂" className="card stack" tabIndex={-1} autoFocus style={{ width: "min(540px, 90vw)", padding: 28 }}>
      <h2>正在准备必须更新</h2>
      {showSaving ? <p role="status">正在保存课堂进度和未发送的输入…</p> : <><p role="alert">{saveError || "课堂进度暂时无法保存。请重试后继续更新。"}</p><Button kind="primary" onClick={retrySave}>重试保存</Button></>}
    </section>
  </div>
  if (!notice.open) return null
  const downloading = !!status?.busy || ["APPLYING", "RESTART_REQUIRED", "UPDATE_DOWNLOADED"].includes(status?.state || "")
  return <div role="presentation" style={{ position: "fixed", inset: 0, background: "#0008", display: "grid", placeItems: "center", zIndex: 1000 }}>
    <section role="dialog" aria-modal="true" aria-label="MentorFlow 更新" className="card stack" tabIndex={-1} autoFocus style={{ width: "min(540px, 90vw)", padding: 28, maxHeight: "80vh", overflowY: "auto" }}>
      <h2>MentorFlow 更新</h2>
      <p>当前版本 {status?.current || status?.version} → 新版本 {status?.latest || status?.target}</p>
      <p style={{ whiteSpace: "pre-wrap" }}>{status?.release_notes || "此版本未提供更新说明。"}</p>
      <p>更新完成后会自动重启，并继续当前登录（仅这一次）。</p>
      {mandatory(status) && <p>版本 {status?.mandatory_floor || status?.min_supported_version || status?.latest} 必须安装：{({security:"安全修复",protocol:"更新协议兼容要求",data_compatibility:"数据兼容要求",support_floor:"版本兼容要求"} as Record<string,string>)[status?.mandatory_reason || "support_floor"] || status?.mandatory_reason}</p>}
      <UpdateStatusSummary status={status} error={notice.error}/>
      <UpdateProgress status={status} error={notice.error}/>
      <div className="row">
        <Button kind="primary" busy={!!status?.busy} disabled={downloading || status?.state === "FAILED_SECURITY"} onClick={() => void installUpdate()}>
          {status?.state === "DOWNLOAD_PAUSED_OR_INTERRUPTED" ? "继续" : mandatory(status) ? "立即更新" : "立即更新并重启"}
        </Button>
        {!mandatory(status) && <Button disabled={downloading} onClick={() => void deferUpdate()}>稍后</Button>}
      </div>
    </section>
  </div>
}
