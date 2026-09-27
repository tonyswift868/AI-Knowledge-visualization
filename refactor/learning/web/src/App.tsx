import * as React from "react"
import { Home as HomeIcon, LibraryBig, Maximize2, Minimize2, PanelLeftClose, PanelLeftOpen, Settings as SettingsIcon,
  Sparkles, Wand2, Wrench } from "lucide-react"
import { call, on } from "./bridge"
import { useApp, type Route } from "./store"
import { Ambient, Button, Toasts } from "./ui"
import { Home } from "./pages/Home"
import { Plans } from "./pages/Plans"
import { NewPlan } from "./pages/NewPlan"
import { Tools } from "./pages/Tools"
import { Settings } from "./pages/Settings"

const NAV: { id: Route; label: string; icon: any }[] = [
  { id: "home", label: "首页", icon: HomeIcon },
  { id: "plans", label: "我的计划", icon: LibraryBig },
  { id: "new-plan", label: "新建计划", icon: Wand2 },
  { id: "tools", label: "更多工具", icon: Wrench },
  { id: "settings", label: "设置", icon: SettingsIcon },
]

export function App() {
  const s = useApp()
  const theme = s.settings?.theme ?? "sakura"

  React.useEffect(() => {
    const load = () => call("app.bootstrap").then((r) => s.set({ account: r.account, settings: r.settings, options: r.options,
                                                                  notice: r.notice, ready: r.ready }))
    load()
    const offs = [
      on("ready", (d) => { s.set({ ready: true, account: d.account }); call("settings.get").then((r) => s.set({ settings: r.settings })) }),
      on("account", (d) => s.set({ account: d })),
      on("settings", (d) => s.set({ settings: d })),
      on("session_notice", (d) => s.set({ notice: d })),
      on("window", (d) => s.set({ fullscreen: !!d.fullscreen })),
      on("scope_changed", () => s.set({ ready: false })),
    ]
    return () => offs.forEach((off) => off())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  React.useEffect(() => {
    const root = document.documentElement
    root.dataset.theme = theme
    root.style.setProperty("--scale", String(s.settings?.font_scale ?? 1))
    root.classList.toggle("reduce-motion", !!s.settings?.reduce_motion)
  }, [theme, s.settings?.font_scale, s.settings?.reduce_motion])

  const Page = { home: Home, plans: Plans, "new-plan": NewPlan, tools: Tools, settings: Settings }[s.route]

  return (
    <>
      {!s.settings?.reduce_motion && <Ambient theme={theme} />}
      <div className="shell">
        <aside className={`sidebar ${s.sidebarCollapsed ? "collapsed" : ""}`}>
          <div className="brand">
            <div className="brand-mark"><Sparkles /></div>
            <span className="brand-text">MentorFlow</span>
          </div>
          {NAV.map((n) => (
            <button key={n.id} className={`nav-item ${s.route === n.id ? "active" : ""}`} onClick={() => s.go(n.id)} title={n.label}>
              <n.icon /><span className="nav-label">{n.label}</span>
            </button>
          ))}
          <div className="nav-spacer" />
          <div className="account-card" title={s.account?.name}>
            <div className="avatar">{s.account?.name?.slice(0, 1) ?? "M"}</div>
            <div className="account-meta"><b>{s.account?.name ?? "…"}</b><span>{s.account?.role_label ? `服务端身份：${s.account.role_label}` : ""}</span></div>
          </div>
          <div className="side-actions">
            <button className="side-btn" onClick={() => call("app.account", { action: "switch" })}>切换账号</button>
            <button className="side-btn" onClick={() => call("app.account", { action: "logout" })}>退出登录</button>
          </div>
        </aside>

        <main className="main">
          <header className="topbar">
            <button className="icon-btn" aria-label="收起侧栏" onClick={() => s.set({ sidebarCollapsed: !s.sidebarCollapsed })}>
              {s.sidebarCollapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
            </button>
            <div className="grow" />
            <span className={`chip ${s.ready ? "" : "warn"}`}><span className="dot" />{s.ready ? "账号已连接" : "正在连接账号服务…"}</span>
            <button className="icon-btn" aria-label={s.fullscreen ? "退出全屏" : "全屏"} title="全屏（F11）"
                    onClick={() => call("app.window", { action: "toggle_fullscreen" })}>
              {s.fullscreen ? <Minimize2 /> : <Maximize2 />}
            </button>
          </header>
          {s.notice && (
            <div className="banner fade-in">
              <span className="grow">{s.notice.message}</span>
              {s.notice.show_verify && <Button small onClick={() => call("app.account", { action: "verify" })}>重新验证</Button>}
            </div>
          )}
          <div className="content">
            {s.settings ? <Page key={s.route} /> : null}
          </div>
        </main>
      </div>
      <Toasts />
    </>
  )
}
