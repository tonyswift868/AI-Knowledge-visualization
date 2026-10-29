import * as React from "react"
import { createRoot } from "react-dom/client"
import { UpdateBanner, UpdateNotice, useUpdates, type UpdateStatus } from "../../../refactor/learning/web/src/updates/UpdateNotice"
import { useApp } from "../../../refactor/learning/web/src/store"
import { RequestLoading } from "../../../refactor/learning/web/src/loading/Loading"
import { Toasts } from "../../../refactor/learning/web/src/ui"
import { connect, fixture194 } from "../update194/bridge194"
import "../../../refactor/learning/web/src/theme.css"

const readyStatus: UpdateStatus = {
  available: true, busy: false, state: "UP_TO_DATE", version: "2.0.36",
  current: "2.0.36", message: "已是最新版本。",
}
useApp.setState({ ready: true, route: "home", settingsTab: "models", planId: null, account: null,
  settings: { version: 1, theme: "sakura", font_scale: 1, reduce_motion: true, teacher_style: "", detail: "normal",
    examples: "normal", analogy_domains: ["daily"], refresh_time: "04:00", reminder_enabled: false,
    reminder_time: "20:00", show_plan_name_in_notification: false, chat_persona: "" } as any })
useUpdates.setState({ status: readyStatus, open: false, later: false, manual: false, error: "" })
;(window as any).fixture194 = fixture194
;(window as any).set194Status = (status: UpdateStatus, open = false, manual = false, error = "") => {
  useUpdates.setState({ status, open, manual, error, later: false })
}
;(window as any).set194Theme = (theme: "sakura" | "aurora") => {
  document.documentElement.dataset.theme = theme
  useApp.getState().set({ settings: { ...useApp.getState().settings!, theme } })
}
;(window as any).check194Manually = async () => {
  await (await import("../../../refactor/learning/web/src/updates/UpdateNotice")).checkUpdate(true)
}
connect().then((connected) => {
  useApp.getState().set({ connected })
  createRoot(document.getElementById("root")!).render(<>
    <div className="shell"><UpdateBanner /></div>
    <UpdateNotice />
    <Toasts />
    <RequestLoading />
  </>)
  ;(window as any).ready194 = true
})
