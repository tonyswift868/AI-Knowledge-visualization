import * as React from "react"
import { createRoot } from "react-dom/client"
import { connect, call, on } from "../../../refactor/learning/web/src/bridge"
import { useSaveSettings } from "../../../refactor/learning/web/src/pages/Settings"
import { Chat } from "../../../refactor/learning/web/src/pages/Chat"
import { Settings } from "../../../refactor/learning/web/src/pages/Settings"
import { App } from "../../../refactor/learning/web/src/App"
import { useApp } from "../../../refactor/learning/web/src/store"

declare global { interface Window { startSave229: (kind: string) => void; state229: () => unknown; scope229: (accountId: string) => void; clearToasts229: () => void; readSettings229: () => void; push229: (name: string, data?: any) => Promise<void>; remote229: () => Promise<void>; stats229: () => Promise<any>; mountChat229: () => void; mountSettings229: (tab: string) => void; mountApp229: () => void; navigate229: (route: string, tab: string) => void; } }

function Harness() {
  const save = useSaveSettings()
  const settings = useApp(s => s.settings)
  React.useEffect(() => {
    window.startSave229 = (kind) => {
      const changes = kind === "theme" ? { theme: "aurora" as const }
        : kind === "scale" ? { font_scale: 1.25 }
        : kind === "slow-fail" ? { detail: "detailed" }
        : { examples: "many" }
      void save(changes)
    }
    window.state229 = () => ({ settings: useApp.getState().settings, revision: (useApp.getState() as any).settingsRevision ?? 0,
      errors: useApp.getState().toasts.filter(t => t.kind === "error").length, oks: useApp.getState().toasts.filter(t => t.kind === "ok").length,
      toasts: useApp.getState().toasts.map(t => ({ id: t.id, kind: t.kind, text: t.text })), account_id: useApp.getState().account?.account_id,
      scope: (useApp.getState() as any).settingsScopeRevision })
    window.scope229 = (accountId) => {
      ;(useApp.getState() as any).resetSettingsScope?.()
      useApp.getState().set({ account: { name: accountId, account_id: accountId, role_label: "学习者" }, settings: baseSettings(), confirmedSettings: baseSettings() } as any)
    }
    window.clearToasts229 = () => useApp.getState().set({ toasts: [] })
    window.push229 = (name, data={}) => call("test.push", { name, data }).then(() => undefined)
    window.remote229 = () => call("test.remote").then(() => undefined)
    window.stats229 = () => call("test.stats")
    window.readSettings229 = () => {
      const s = useApp.getState() as any, scope = s.settingsScopeRevision
      void call("settings.get").then((snapshot) => (useApp.getState() as any).applySettingsSnapshot(snapshot, scope))
    }
    window.mountChat229 = () => root.render(<Chat />)
    window.mountSettings229 = (tab) => { useApp.getState().go("settings", tab as any); root.render(<Settings/>) }
    window.mountApp229 = () => {
      ;(useApp.getState() as any).resetSettingsScope?.()
      useApp.getState().set({ account: null, settings: null, confirmedSettings: null, ready: false, route: "home" } as any)
      root.render(<App/>)
    }
    window.navigate229 = (route, tab) => useApp.getState().go(route as any, tab as any)
  }, [save])
  return <pre id="state229">{JSON.stringify(settings)}</pre>
}

function baseSettings() {
  return { version: 1, theme: "sakura" as const, font_scale: 1, reduce_motion: true, teacher_style: "warm", detail: "normal",
    examples: "normal", analogy_domains: ["life"], refresh_time: "04:00", reminder_enabled: false, reminder_time: "20:00",
    show_plan_name_in_notification: true, chat_persona: "" }
}

useApp.setState({ account: { name: "A", account_id: "a229", role_label: "学习者" }, settings: baseSettings(), confirmedSettings: baseSettings(), ...( {settingsRevision: 0, settingsScopeRevision: 0} as any) } as any)
const root = createRoot(document.getElementById("root")!)
on("scope_changed", () => window.scope229("b229"))
void connect().then(() => root.render(<Harness/>))
