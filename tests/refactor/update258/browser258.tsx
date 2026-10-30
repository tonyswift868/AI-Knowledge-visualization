import * as React from "react"
import { createRoot } from "react-dom/client"
import { hasUpdate, UpdateBanner, UpdateNotice, useUpdates } from "../../../refactor/learning/web/src/updates/UpdateNotice"
import { useApp } from "../../../refactor/learning/web/src/store"
import { connect, fixture258 } from "./bridge258"
import "../../../refactor/learning/web/src/theme.css"

useApp.setState({ ready: true, connected: true, route: "home", account: null, settings: {
  version: 1, theme: "sakura", font_scale: 1, reduce_motion: true, teacher_style: "", detail: "normal",
  examples: "normal", analogy_domains: [], refresh_time: "04:00", reminder_enabled: false,
  reminder_time: "20:00", show_plan_name_in_notification: false, chat_persona: "",
} as any })
useUpdates.setState({ status: fixture258.status, open: false, later: false, manual: false, error: "" } as any)
;(window as any).fixture258 = fixture258
;(window as any).set258Status = (status: any) => { fixture258.status = { ...status }; useUpdates.setState({ status: fixture258.status, open: false }) }
;(window as any).set258Theme = (theme: "sakura" | "aurora") => {
  document.documentElement.dataset.theme = theme
  useApp.getState().set({ settings: { ...useApp.getState().settings!, theme } })
}
;(window as any).set258Route = (route: "home" | "plans" | "settings") => useApp.getState().set({ route })
;(window as any).has258Update = () => hasUpdate()
;(window as any).dismissed258 = () => useUpdates.getState().dismissedVersion
connect().then(() => {
  function FixturePage() {
    const route = useApp(s => s.route)
    const title = route === "home" ? "首页" : route === "plans" ? "我的计划" : "设置"
    return <div className="page"><h1>{title}</h1><p>用于验证跨页面更新提示状态。</p></div>
  }
  createRoot(document.getElementById("root")!).render(<>
    <div className="shell">
      <aside className="sidebar"><div className="brand"><div className="brand-mark">M</div><div className="brand-text"><b>MentorFlow</b><span>你的 AI 学习导师</span></div></div>
        <nav className="nav" aria-label="主导航"><div className="nav-group">
          <button className="nav-item" onClick={() => (window as any).set258Route("home")}>首页</button>
          <button className="nav-item" onClick={() => (window as any).set258Route("plans")}>我的计划</button>
          <button className="nav-item" onClick={() => (window as any).set258Route("settings")}>设置</button>
        </div></nav>
      </aside>
      <main className="main"><header className="topbar"><h1>MentorFlow</h1><div className="grow"/><span className="chip">已登录</span></header>
        <UpdateBanner /><div className="content"><FixturePage /></div>
      </main>
    </div>
    <UpdateNotice />
  </>)
  ;(window as any).ready258 = true
})
