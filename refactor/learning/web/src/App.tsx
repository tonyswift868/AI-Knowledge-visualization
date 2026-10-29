import * as React from "react"
import { ChevronsUpDown, CirclePlus, Cloud, CloudOff, House, LayoutGrid, LogOut, Maximize2, Minimize2, PanelLeftClose, PanelLeftOpen, Files,
  Repeat2, Route as RouteIcon, Settings2, ShieldCheck } from "lucide-react"
import { call, on } from "./bridge"
import { useApp, type Route } from "./store"
import { Ambient, BrandMark, Button, ModelFaultPanel, Toasts } from "./ui"
import { Home } from "./pages/Home"
import { Plans } from "./pages/Plans"
import { NewPlan } from "./pages/NewPlan"
import { Tools } from "./pages/Tools"
import { Chat } from "./pages/Chat"
import { Rag } from "./pages/Rag"
import { PlanDetail } from "./pages/PlanDetail"
import { Classroom } from "./pages/Classroom"
import { Report } from "./pages/Report"
import { Settings } from "./pages/Settings"
import { UpdateBanner, UpdateNotice, useUpdates } from "./updates/UpdateNotice"
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
  { label: "资料", items: [
    { id: "rag", label: "资料问答", icon: Files },
  ] },
  { label: "更多", items: [
    { id: "tools", label: "更多工具", icon: LayoutGrid, also: ["chat"] },
    { id: "settings", label: "设置", icon: Settings2 },
  ] },
]

export function AccountMenu() {
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
      <button className={`account-card ${open ? "open" : ""}`} onClick={() => setOpen(!open)} title={account?.nickname || account?.name}>
        <span className="avatar">{account?.avatar_url ? <img src={account.avatar_url} alt="" /> : (account?.nickname || account?.name)?.slice(0, 1) ?? "M"}</span>
        <span className="account-meta"><b>{account?.nickname || account?.name || "…"}</b><span>{account?.role_label || "学习者"}</span></span>
        <ChevronsUpDown className="account-caret" />
      </button>
    </div>
  )
}

function syncTime(value: string | null | undefined): string {
  if (!value) return "暂无记录"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? "暂无记录" : date.toLocaleString()
}

function SyncStatusChip({status, open, onOpenChange}: {
  status: NonNullable<ReturnType<typeof useApp.getState>['sync']>
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [retrying, setRetrying] = React.useState(false)
  const ref = React.useRef<HTMLDivElement>(null)
  const needsAuth = status.state === "reauth" || status.state === "superseded"
  const retryTime = status.retry_at ? new Date(status.retry_at) : null
  const retryDue = retryTime !== null && !Number.isNaN(retryTime.getTime()) && retryTime.getTime() <= Date.now()
  const label = status.state === "waiting" ? "等待账号连接"
    : status.state === "retrying" ? "正在重试"
    : status.state === "syncing" ? (status.message || "正在同步")
    : status.state === "failed" ? "同步失败"
    : status.state === "reauth" ? "需要重新登录"
    : status.state === "superseded" ? "已在别处登录"
    : status.at ? `已同步 ${new Date(status.at).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"})}` : "已同步"
  React.useEffect(() => {
    if (!open) return
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onOpenChange(false) }
    const outside = (event: MouseEvent) => { if (!ref.current?.contains(event.target as Node)) onOpenChange(false) }
    document.addEventListener("keydown", escape)
    document.addEventListener("mousedown", outside)
    return () => { document.removeEventListener("keydown", escape); document.removeEventListener("mousedown", outside) }
  }, [open, onOpenChange])
  const retry = async () => {
    if (needsAuth || retrying) return
    setRetrying(true)
    try { useApp.getState().set({sync: await call("sync.now")}) }
    catch { /* The sync_status event remains the source of truth on failures. */ }
    finally { setRetrying(false) }
  }
  return <div className="sync-status-wrap" ref={ref}>
    <button className={`chip sync ${status.state}`} title="查看同步详情" aria-expanded={open} aria-controls="sync-status-details"
      aria-live="polite" aria-busy={status.state === "syncing" || status.state === "retrying" || status.state === "waiting"}
      onClick={() => onOpenChange(!open)}>
      {status.state === "synced" || status.state === "syncing" ? <Cloud /> : <CloudOff />}
      {label}
    </button>
    {open && <section id="sync-status-details" className="sync-status-details" role="region" aria-label="同步详情">
      <header><b>同步详情</b><button className="sync-detail-close" aria-label="关闭同步详情" onClick={() => onOpenChange(false)}>×</button></header>
      <dl>
        <div><dt>上次同步成功</dt><dd>{syncTime(status.at)}</dd></div>
        <div><dt>待同步记录</dt><dd>{status.pending == null ? "未知" : `${status.pending} 条`}</dd></div>
        <div><dt>下次重试</dt><dd>{status.state === "retrying" && (!retryTime || Number.isNaN(retryTime.getTime()) || retryDue)
          ? "正在尝试" : syncTime(status.retry_at)}</dd></div>
        <div className="sync-detail-error"><dt>最近一次错误</dt><dd>{status.last_error
          ? <>{status.last_error.message}<small>{syncTime(status.last_error.at)}</small></>
          : status.state === "failed" ? status.message : "暂无错误记录"}</dd></div>
      </dl>
      {status.state === "synced" && status.last_error && <p className="sync-recovered">已恢复</p>}
      {needsAuth ? <p className="sync-auth-note">{status.state === "reauth" ? "请重新登录后再同步。" : "账号已在其他位置登录，请切回此设备后再同步。"}</p>
        : <button className="btn primary sync-detail-retry" disabled={retrying || status.state === "syncing"} onClick={() => void retry()}>
          {retrying ? "正在发起…" : "立即重试"}
        </button>}
    </section>}
  </div>
}

export function App() {
  const s = useApp()
  const update = useUpdates()
  const theme = s.settings?.theme ?? "sakura"
  const [bootstrapError, setBootstrapError] = React.useState('')
  const [bootstrapAttempt, bootstrapRetry] = React.useReducer(n => n+1, 0)
  const [modelFaultOpen, setModelFaultOpen] = React.useState(false)
  const [syncDetailsOpen, setSyncDetailsOpen] = React.useState(false)
  const modelScopeEpoch = React.useRef(0)
  const settingsReadRevision = React.useRef(0)
  const scopedSettingsPending = React.useRef(false)
  const modelStatusRevision = React.useRef(0)
  const [scopeRevision, setScopeRevision] = React.useState(0)
  const currentModelFault = s.modelStatus.state === 'error' ? s.modelStatus.fault : undefined
  const retryModelConnection = React.useCallback(() => {
    const accountId = useApp.getState().account?.account_id
    useApp.getState().setModelStatus({ state: 'checking', short: '正在检查模型', account_id: accountId })
    void call('models.test').catch(() => {})
  }, [])
  React.useEffect(() => {
    if (s.modelStatus.state !== 'error') setModelFaultOpen(false)
  }, [s.modelStatus.state, s.modelStatus.fault?.id])
  const [previewHash, setPreviewHash] = React.useState(window.location.hash)
  React.useEffect(() => {
    const changed = () => setPreviewHash(window.location.hash)
    window.addEventListener("hashchange", changed)
    return () => window.removeEventListener("hashchange", changed)
  }, [])

  React.useEffect(() => {
    let alive = true
    const readModelStatus = () => {
      const epoch = modelScopeEpoch.current
      const revision = modelStatusRevision.current
      call("models.status").then(d => {
        const activeAccountId = useApp.getState().account?.account_id
        if (alive && epoch === modelScopeEpoch.current && revision === modelStatusRevision.current && d?.state &&
            (!d.account_id || !activeAccountId || d.account_id === activeAccountId)) s.setModelStatus(d)
      }).catch(() => {})
    }
    const readSettings = (accountId?: string, waitsForScopedSettings = false) => {
      const epoch = modelScopeEpoch.current
      const revision = ++settingsReadRevision.current
      const settingsScope = useApp.getState().settingsScopeRevision
      call("settings.get").then((r) => {
        const activeAccountId = useApp.getState().account?.account_id
        if (!alive || epoch !== modelScopeEpoch.current || revision !== settingsReadRevision.current ||
            (accountId && activeAccountId !== accountId)) return
        useApp.getState().applySettingsSnapshot(r, settingsScope)
        s.set({ ...(r.options ? { options: r.options } : {}), ...(waitsForScopedSettings ? { ready: true } : {}) })
        if (waitsForScopedSettings) scopedSettingsPending.current = false
        setBootstrapError('')
      }).catch(() => {
        if (alive && epoch === modelScopeEpoch.current && revision === settingsReadRevision.current &&
            (waitsForScopedSettings || !useApp.getState().settings))
          setBootstrapError("读取当前账号设置失败，请重试连接。")
      })
    }
    const load = () => {
      const epoch = modelScopeEpoch.current
      const settingsRevision = settingsReadRevision.current
      const settingsScope = useApp.getState().settingsScopeRevision
      call("app.bootstrap").then((r) => {
        if (!alive || epoch !== modelScopeEpoch.current || settingsRevision !== settingsReadRevision.current) return
        s.set({ options: r.options, notice: r.notice, ready: r.ready })
        useApp.getState().applySettingsSnapshot(r, settingsScope, r.account)
        setBootstrapError('')
        scopedSettingsPending.current = false
      }).catch(e => {
        if (alive && epoch === modelScopeEpoch.current && settingsRevision === settingsReadRevision.current) setBootstrapError(e.message)
      })
    }
    load()
    const syncEpoch = modelScopeEpoch.current
    call("sync.status").then((d) => {if(alive && syncEpoch === modelScopeEpoch.current)s.set({ sync: d })}).catch(() => {})
    readModelStatus()
    const offs = [
      on("ready", (d) => {
        const waitsForScopedSettings = scopedSettingsPending.current
        s.set({ ready: !waitsForScopedSettings, account: d.account })
        readSettings(d.account?.account_id, waitsForScopedSettings)
        readModelStatus()
      }),
      on("account", (d) => s.set({ account: d })),
      on("model_status", () => { modelStatusRevision.current++ }),
      on("settings", () => {
        const current = useApp.getState()
        if (current.ready && current.account) readSettings(current.account.account_id)
      }),
      on("session_notice", (d) => s.set({ notice: d })),
      on("window", (d) => s.set({ fullscreen: !!d.fullscreen })),
      on("navigate", (d) => { if (d?.route === "home") s.go("home") }),
      on("scope_changed", () => {
        modelScopeEpoch.current++
        settingsReadRevision.current++
        scopedSettingsPending.current = true
        modelStatusRevision.current++
        setScopeRevision((revision) => revision + 1)
        setBootstrapError('')
        useApp.getState().resetSettingsScope()
        s.set({ ready: false, account: null, settings: null, planId: null, sync: null, modelStatus:{state:'unknown',short:'模型待验证'},modelFault:null,modelRetry:null })
      }),
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

  const Page = previewHash === "#/dev/voice" ? VoiceInputPreview : previewHash === "#/dev/teacher" ? TeacherStagePreview : { home: Home, plans: Plans, plan: PlanDetail, "new-plan": NewPlan, classroom: Classroom, report: Report, tools: Tools, chat: Chat, rag: Rag, settings: Settings }[s.route]

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
                  <button key={n.id} data-testid={n.id === "rag" ? "nav-rag" : undefined} aria-label={n.label} className={`nav-item ${s.route === n.id || n.also?.includes(s.route) ? "active" : ""}`}
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
            <span className={`chip ${!s.ready || s.account?.offline ? "warn" : ""}`}><span className="dot" />{!s.ready ? "正在连接账号服务…" : s.account?.offline ? "离线学习" : "已登录"}</span>
            <button className={`chip model-status ${s.modelStatus.state}`} aria-live="polite" aria-busy={s.modelStatus.state === 'checking'}
              aria-expanded={!!currentModelFault && modelFaultOpen} aria-controls="model-status-details"
              title={s.modelStatus.fault?.message ?? (s.modelStatus.state === 'checking' ? '正在后台检查当前模型连接。' : s.modelStatus.state === 'unconfigured' ? '请到设置 → 模型连接配置 API Key。' : '以最近一次模型请求结果为准')}
              onClick={()=>s.modelStatus.state === 'checking' ? undefined : s.modelStatus.state === 'unconfigured' ? s.go('settings','models') :
                currentModelFault ? (s.set({ modelFault: currentModelFault }), setModelFaultOpen(open => !open)) :
                s.toast('info',s.modelStatus.state === 'ok' ? '最近一次模型请求正常。' : '还没有模型请求结果，可到设置测试连接。')}>
              <span className="dot"/>{s.modelStatus.short}</button>
            {s.ready && s.sync && <SyncStatusChip status={s.sync} open={syncDetailsOpen} onOpenChange={setSyncDetailsOpen} />}
            <button className="icon-btn" aria-label={s.fullscreen ? "退出全屏" : "全屏"} title="全屏（F11）"
                    onClick={() => call("app.window", { action: "toggle_fullscreen" })}>
              {s.fullscreen ? <Minimize2 /> : <Maximize2 />}
            </button>
          </header>
          <UpdateBanner />
          {currentModelFault && modelFaultOpen && <div id="model-status-details" className="model-status-details" role="region" aria-label="模型连接问题详情">
            <ModelFaultPanel fault={currentModelFault} onRetry={retryModelConnection}/>
            <div className="row" style={{ justifyContent: 'flex-end' }}>
              <Button small kind="ghost" onClick={() => setModelFaultOpen(false)}>收起</Button>
            </div>
          </div>}
          {s.notice && (
            <div className="banner fade-in">
              <span className="grow">{s.notice.message}</span>
              {s.notice.show_verify && <Button small onClick={() => call("app.account", { action: "verify" })}>重新验证</Button>}
            </div>
          )}
          <div className="content">
            {s.settings && !s.ready && s.route !== 'home' && <Loading label="正在连接账号服务…"/>}
            <Loading active={s.sync?.state==='syncing' && s.sync?.show_progress===true} label={s.sync?.message || '正在同步学习记录…'} slowHint={false}/>
            <PageErrorBoundary key={`${s.route}:${s.planId}:${s.settingsTab}:${scopeRevision}`} back={()=>s.go(s.route === "plan" || s.route === "home" ? "plans" : "home")}>
              {s.settings && !scopedSettingsPending.current ? <Page key={`${s.route}:${scopeRevision}`} /> : <div className="page">{bootstrapError ? <div className="card" role="alert">{bootstrapError}<Button onClick={bootstrapRetry}>重试</Button></div>
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
