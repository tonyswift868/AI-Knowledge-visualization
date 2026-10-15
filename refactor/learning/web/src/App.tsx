import * as React from "react"
import { ChevronsUpDown, CirclePlus, Cloud, CloudOff, House, LayoutGrid, LogOut, Maximize2, Minimize2, PanelLeftClose, PanelLeftOpen,
  Repeat2, Route as RouteIcon, Settings2, ShieldCheck } from "lucide-react"
import { call, on } from "./bridge"
import { useApp, type Route } from "./store"
import { Ambient, BrandMark, Button, Toasts } from "./ui"
import { Home } from "./pages/Home"
import { Plans } from "./pages/Plans"
import { NewPlan } from "./pages/NewPlan"
import { Tools } from "./pages/Tools"
import { Chat } from "./pages/Chat"
import { PlanDetail } from "./pages/PlanDetail"
import { Classroom } from "./pages/Classroom"
import { Report } from "./pages/Report"
import { Settings } from "./pages/Settings"
import { UpdateNotice, useUpdates } from "./updates/UpdateNotice"
import { TeacherStagePreview } from "./teacher/TeacherStagePreview"
import { VoiceInputPreview } from "./voice/VoiceInputPreview"
import { Loading, RequestLoading } from './loading/Loading'

type NavItem = { id: Route; label: string; icon: any; also?: Route[] }
/** Keep a failed page inside the content area; navigation stays usable. */
export class PageErrorBoundary extends React.Component<{children:React.ReactNode;back:()=>void}, {error:Error|null;attempt:number}> {
  state = {error:null as Error|null,attempt:0}
  static getDerivedStateFromError(error:unknown) { return {error:error instanceof Error ? error : new Error(String(error ?? "页面无法显示"))} }
  render() {
    const e = this.state.error
    if (e) return <div className="page"><div className="card stack" role="alert">
      <h2>这一页出错了</h2><p className="muted" style={{overflowWrap:"anywhere"}}>{e.message.startsWith("Minified React error") ? `页面渲染失败（React ${e.message.match(/#\d+/)?.[0] ?? "错误"}）` : e.message.slice(0,240)}</p>
      <div className="row"><Button data-testid="page-error-back" onClick={this.props.back}>返回</Button><Button data-testid="page-error-retry" onClick={()=>this.setState(s=>({error:null,attempt:s.attempt+1}))}>重试</Button></div>
    </div></div>
    return <React.Fragment key={this.state.attempt}>{this.props.children}</React.Fragment>
  }
}
const NAV: { label: string; items: NavItem[] }[] = [
  { label: "学习", items: [
    { id: "home", label: "首页", icon: House },
    { id: "plans", label: "我的计划", icon: RouteIcon, also: ["plan"] },
    { id: "new-plan", label: "新建计划", icon: CirclePlus },
  ] },
  { label: "更多", items: [
    { id: "tools", label: "更多工具", icon: LayoutGrid, also: ["chat"] },
    { id: "settings", label: "设置", icon: Settings2 },
  ] },
]

function AccountMenu() {
  const account = useApp((s) => s.account)
  const go = useApp((s) => s.go)
  const [open, setOpen] = React.useState(false)
  const ref = React.useRef<HTMLDivElement>(null)
  React.useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener("mousedown", close)
    return () => document.removeEventListener("mousedown", close)
  }, [open])
  const act = (fn: () => void) => { setOpen(false); fn() }
  return (
    <div className="account" ref={ref}>
      {open && (
        <div className="account-menu fade-in">
          <button onClick={() => act(() => go("settings", "account"))}><ShieldCheck />账号与安全</button>
          <button onClick={() => act(() => call("app.account", { action: "switch" }))}><Repeat2 />切换账号</button>
          <button className="danger" onClick={() => act(() => call("app.account", { action: "logout" }))}><LogOut />退出登录</button>
        </div>
      )}
      <button className={`account-card ${open ? "open" : ""}`} onClick={() => setOpen(!open)} title={account?.name}>
        <span className="avatar">{account?.name?.slice(0, 1) ?? "M"}</span>
        <span className="account-meta"><b>{account?.name ?? "…"}</b><span>{account?.role_label || "学习者"}</span></span>
        <ChevronsUpDown className="account-caret" />
      </button>
    </div>
  )
}

export function App() {
  const s = useApp()
  const update = useUpdates()
  const theme = s.settings?.theme ?? "sakura"
  const [bootstrapError, setBootstrapError] = React.useState('')
  const [bootstrapAttempt, bootstrapRetry] = React.useReducer(n => n+1, 0)
  const [previewHash, setPreviewHash] = React.useState(window.location.hash)
  React.useEffect(() => {
    const changed = () => setPreviewHash(window.location.hash)
    window.addEventListener("hashchange", changed)
    return () => window.removeEventListener("hashchange", changed)
  }, [])

  React.useEffect(() => {
    let alive = true
    const load = () => call("app.bootstrap").then((r) => {if(alive){s.set({ account: r.account, settings: r.settings, options: r.options,
                                                                  notice: r.notice, ready: r.ready });setBootstrapError('')}}).catch(e => {if(alive)setBootstrapError(e.message)})
    load()
    call("sync.status").then((d) => s.set({ sync: d })).catch(() => {})
    call("models.status").then(d=>{if(d?.state)s.setModelStatus(d)}).catch(()=>{})
    const offs = [
      on("ready", (d) => { s.set({ ready: true, account: d.account }); call("settings.get").then((r) => s.set({ settings: r.settings })) }),
      on("account", (d) => s.set({ account: d })),
      on("settings", (d) => s.set({ settings: d })),
      on("session_notice", (d) => s.set({ notice: d })),
      on("window", (d) => s.set({ fullscreen: !!d.fullscreen })),
      on("navigate", (d) => { if (d?.route === "home") s.go("home") }),
      on("scope_changed", () => s.set({ ready: false, sync: null, modelStatus:{state:'unknown',short:'模型待验证'},modelFault:null,modelRetry:null })),
      on("sync_status", (d) => s.set({ sync: d })),
      on("sync_conflict", (d) => s.toast("info", d.message)),
    ]
    return () => {alive=false;offs.forEach((off) => off())}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bootstrapAttempt])

  React.useEffect(() => {
    const root = document.documentElement
    root.dataset.theme = theme
    root.style.setProperty("--scale", String(s.settings?.font_scale ?? 1))
    root.classList.toggle("reduce-motion", !!s.settings?.reduce_motion)
  }, [theme, s.settings?.font_scale, s.settings?.reduce_motion])

  const Page = previewHash === "#/dev/voice" ? VoiceInputPreview : previewHash === "#/dev/teacher" ? TeacherStagePreview : { home: Home, plans: Plans, plan: PlanDetail, "new-plan": NewPlan, classroom: Classroom, report: Report, tools: Tools, chat: Chat, settings: Settings }[s.route]

  return (
    <>
      {!s.settings?.reduce_motion && <Ambient theme={theme} />}
      <div className="shell">
        <aside className={`sidebar ${s.sidebarCollapsed ? "collapsed" : ""}`}>
          <div className="brand">
            <BrandMark />
            <div className="brand-text"><b>MentorFlow</b><span>你的 AI 学习导师</span></div>
          </div>
          <nav className="nav">
            {NAV.map((group) => (
              <div key={group.label} className="nav-group">
                <span className="nav-heading">{group.label}</span>
                {group.items.map((n) => (
                  <button key={n.id} className={`nav-item ${s.route === n.id || n.also?.includes(s.route) ? "active" : ""}`}
                          onClick={() => { if (n.id === "new-plan") s.set({ planId: null }); s.go(n.id) }} title={n.label}>
                    <span className="nav-icon"><n.icon /></span><span className="nav-label">{n.label}</span>
                    {n.id === "settings" && update.later && <span aria-label="有新版本" style={{ color: "#f24b63" }}>●</span>}
                  </button>
                ))}
              </div>
            ))}
          </nav>
          <div className="nav-spacer" />
          <AccountMenu />
        </aside>

        <main className="main">
          <header className="topbar">
            <button className="icon-btn" aria-label="收起侧栏" onClick={() => s.set({ sidebarCollapsed: !s.sidebarCollapsed })}>
              {s.sidebarCollapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
            </button>
            <div className="grow" />
            <span className={`chip ${s.ready ? "" : "warn"}`}><span className="dot" />{s.ready ? "账号已连接" : "正在连接账号服务…"}</span>
            <button className={`chip model-status ${s.modelStatus.state}`} title={s.modelStatus.fault?.message ?? '以最近一次模型请求结果为准'}
              onClick={()=>s.modelStatus.fault ? s.set({modelFault:s.modelStatus.fault}) : s.toast('info',s.modelStatus.state === 'ok' ? '最近一次模型请求正常。' : '还没有模型请求结果，可到设置测试连接。')}>
              <span className="dot"/>{s.modelStatus.short}</button>
            {s.ready && s.sync && (
              <button className={`chip sync ${s.sync.state}`} title={s.sync.state === "failed" ? "点一下重试" : "点一下立即同步"}
                      onClick={() => call("sync.now").then((d) => s.set({ sync: d })).catch(() => {})}>
                {s.sync.state === "syncing" || s.sync.state === "synced" ? <Cloud /> : <CloudOff />}
                {s.sync.state === "synced" && s.sync.at ? `已同步 ${new Date(s.sync.at).toTimeString().slice(0, 5)}`
                  : s.sync.state === 'syncing' ? s.sync.message
                  : { failed: "同步失败", reauth: "需要重新登录", superseded: "已在别处登录", synced: "已同步" }[s.sync.state]}
              </button>
            )}
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
            {s.settings && !s.ready && s.route !== 'home' && <Loading label="正在连接账号服务…"/>}
            <Loading active={s.sync?.state==='syncing' && s.sync?.show_progress===true} label={s.sync?.message || '正在同步学习记录…'} slowHint={false}/>
            <PageErrorBoundary key={`${s.route}:${s.planId}:${s.settingsTab}`} back={()=>s.go(s.route === "plan" || s.route === "home" ? "plans" : "home")}>
              {s.settings ? <Page key={s.route} /> : <div className="page">{bootstrapError ? <div className="card" role="alert">{bootstrapError}<Button onClick={bootstrapRetry}>重试</Button></div>
                : <Loading shape="page" label="正在准备学习空间…" resetKey={bootstrapAttempt} onRetry={bootstrapRetry}/>}</div>}
            </PageErrorBoundary>
          </div>
        </main>
      </div>
      <Toasts />
      <RequestLoading />
      <UpdateNotice />
    </>
  )
}
