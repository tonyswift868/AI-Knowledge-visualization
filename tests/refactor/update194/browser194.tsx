import * as React from "react"
import { createRoot } from "react-dom/client"
import { Settings } from "../../../refactor/learning/web/src/pages/Settings"
import { UpdateNotice, useUpdates, type UpdateStatus } from "../../../refactor/learning/web/src/updates/UpdateNotice"
import { useApp } from "../../../refactor/learning/web/src/store"
import { RequestLoading } from "../../../refactor/learning/web/src/loading/Loading"
import { Toasts } from "../../../refactor/learning/web/src/ui"
import { connect, fixture194 } from "./bridge194"
import "../../../refactor/learning/web/src/theme.css"

const readyStatus: UpdateStatus = {
  available: true, busy: false, state: "UP_TO_DATE", version: "9.194.0", current: "9.194.0", message: "已是最新版本。",
}
useApp.setState({ ready: true, route: "settings", settingsTab: "models", planId: null, account: null,
  settings: { version: 1, theme: "sakura", font_scale: 1, reduce_motion: true, teacher_style: "", detail: "normal",
    examples: "normal", analogy_domains: ["daily"], refresh_time: "04:00", reminder_enabled: false,
    reminder_time: "20:00", show_plan_name_in_notification: false, chat_persona: "" } as any })
useUpdates.setState({ status: readyStatus, open: false, later: false, manual: false, error: "" })
;(window as any).fixture194 = fixture194
;(window as any).set194Status = (status: UpdateStatus, open = true, manual = false, error = "") => {
  useUpdates.setState({ status, open, manual, error, later: false })
}
;(window as any).set194Theme = (theme: "sakura" | "aurora") => {
  document.documentElement.dataset.theme = theme
  useApp.getState().set({ settings: { ...useApp.getState().settings!, theme } })
}
;(window as any).set194Route = (route: "home" | "settings") => useApp.getState().set({ route })
;(window as any).check194Manually = async () => {
  await (await import("../../../refactor/learning/web/src/updates/UpdateNotice")).checkUpdate(true)
}
;(window as any).check194Automatically = async () =>
  await (await import("../../../refactor/learning/web/src/updates/UpdateNotice")).checkUpdate(false)
connect().then((connected) => {
  useApp.getState().set({ connected })
  createRoot(document.getElementById("root")!).render(<>
    <div className="shell"><Settings /></div>
    <Toasts />
    <RequestLoading />
    <UpdateNotice />
  </>)
  ;(window as any).ready194 = true
})
