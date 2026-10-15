import * as React from "react"
import { useUpdates, checkUpdate, installUpdate } from "../updates/UpdateNotice"
import {
  BellRing, BookOpenText, Bot, CheckCircle2, Clock3, Coins, GraduationCap, HeartHandshake, KeyRound, Laugh, LogOut, Palette, PlugZap, RefreshCw,
  ShieldCheck, Target, Trash2, UserRound, Users, Waypoints,
} from "lucide-react"
import { call, on, BridgeFailure } from "../bridge"
import { useApp, type Settings as S, type SettingsTab, type TeacherPreset } from "../store"
import { Button, Field, Segmented, Toggle, ModelFaultPanel, useModelRetry } from "../ui"
import { Loading } from '../loading/Loading'

const TABS: { id: SettingsTab; label: string; icon: any }[] = [
  { id: "models", label: "模型连接", icon: PlugZap },
  { id: "usage", label: "模型用量", icon: Coins },
  { id: "learning", label: "老师预设", icon: GraduationCap },
  { id: "time", label: "时间与提醒", icon: Clock3 },
  { id: "appearance", label: "外观", icon: Palette },
  { id: "account", label: "账号与安全", icon: ShieldCheck },
]

export function useSaveSettings() {
  const set = useApp((s) => s.set)
  const toast = useApp((s) => s.toast)
  return React.useCallback(async (changes: Partial<S>) => {
    const prev = useApp.getState().settings
    if (prev) set({ settings: { ...prev, ...changes } })
    try {
      const r = await call<{ settings: S }>("settings.save", { changes })
      set({ settings: r.settings })
    } catch (e) {
      if (prev) set({ settings: prev })
      toast("error", (e as Error).message)
    }
  }, [set, toast])
}

export function Settings() {
  const tab = useApp((s) => s.settingsTab)
  const go = useApp((s) => s.go)
  return (
    <div className="page">
      <h2 style={{ margin: "8px 0 18px", fontSize: "1.5rem" }}>设置</h2>
      <div className="settings-layout">
        <nav className="card subnav" aria-label="设置分类">
          {TABS.map((t) => (
            <button key={t.id} className={tab === t.id ? "on" : ""} onClick={() => go("settings", t.id)}>
              <t.icon />{t.label}
            </button>
          ))}
        </nav>
        <div key={tab} className="fade-in">
          {tab === "models" && <Models />}
          {tab === "usage" && <Usage />}
          {tab === "learning" && <Learning />}
          {tab === "time" && <TimeAndReminders />}
          {tab === "appearance" && <Appearance />}
          {tab === "account" && <Account />}
          <AboutUpdates />
        </div>
      </div>
    </div>
  )
}

function AboutUpdates() {
  const { status, error, later } = useUpdates()
  const [logError, setLogError] = React.useState("")
  const offered = status && ["AVAILABLE", "UPDATE_AVAILABLE", "OPTIONAL_UPDATE", "MANDATORY_UPDATE", "PENDING_INSTALL"].includes(status.state)
  const message = status?.message || (status ? ({ UP_TO_DATE: "已是最新版本。", UPDATE_AVAILABLE: "有新版本可用。",
    APPLYING: "正在安装更新，完成后将自动重启。", UPDATE_DOWNLOADED: "下载完成，正在准备安装。" } as Record<string, string>)[status.state] : "正在读取版本…")
  return <section className="card" aria-label="关于 MentorFlow" style={{ marginTop: 20, padding: 20 }}>
    <h3>关于 MentorFlow {later && <span aria-label="有新版本" style={{ color: "#f24b63" }}>●</span>}</h3>
    <p>版本 {status?.version ?? "…"}</p>
    <Loading active={!status || !!status.busy} label={message || '正在读取版本…'}/>
    <p className="small muted" role="status">{logError || error || message}</p>
    <div className="row">
      <Button small disabled={!status?.available || status.busy} onClick={() => void checkUpdate()}><RefreshCw />检查更新</Button>
      {offered && <Button small kind="primary" disabled={status.busy} onClick={() => void installUpdate()}>更新并自动重启</Button>}
      <Button small onClick={() => call("logs.open").then(() => setLogError(""), (e: Error) => setLogError(e.message))}>打开日志文件夹</Button>
    </div>
  </section>
}

// ------------------------------------------------------------------ models
type Provider = { id: string; name: string; default_base_url: string; default_model: string; search_supported: boolean; search_verified: boolean }
type Preset = { id: string; provider_id: string; display_name: string; base_url: string; model: string; temperature: number | null; timeout: number; has_api_key: boolean }

const isRelay = (id: string) => id.startsWith("relay_")

type VendorBalance = { label: string; balances: { amount: string; currency: string }[]; updated_at: string }
type SpendCurrency = { currency: string; ready: boolean; today: string; week: string; month: string; days: { date: string; amount: string }[] }
type Spend = { ready: boolean; observations: number; currencies: SpendCurrency[]; balance: VendorBalance | null;
  supported: boolean; query_failed?: boolean; unavailable?: boolean }
const money = (amount: string, currency: string) => `${currency === "CNY" ? "¥" : currency === "USD" ? "$" : currency + " "}${Number(amount).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`
const spendHint = (value: Spend) => value.unavailable ? "花费记录暂时无法读取" : !value.supported ? "此预设暂不支持查询账户余额" : value.observations > 0
  ? "刚开始记录，下次查询后显示花费" : "尚未查到余额，下次查询后开始记录"

function ApiBalance({ preset, reload }: { preset: Preset; reload: number }) {
  const [value, setValue] = React.useState<Spend | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [revision, setRevision] = React.useState(0)
  const [now, setNow] = React.useState(Date.now())
  const request = React.useRef(0)
  const load = React.useCallback(async (refresh: boolean) => {
    const sequence = ++request.current
    if (!preset.has_api_key) { setValue(null); setBusy(false); return }
    setBusy(true)
    try {
      const result = await call<{ spend: Spend | null }>("usage.spend", { preset_id: preset.id, refresh })
      if (sequence === request.current) setValue(result.spend)
    } catch { /* keep the last successful amounts while the bridge reconnects */ }
    finally { if (sequence === request.current) setBusy(false) }
  }, [preset.id, preset.has_api_key, preset.base_url])
  React.useEffect(() => { void load(true) }, [load, reload, revision])
  React.useEffect(() => {
    const off = on("balance_spend", (data) => { if (data?.preset_id === preset.id) void load(false) })
    const timer = setInterval(() => { setNow(Date.now()); void load(false) }, 30000)
    return () => { ++request.current; off(); clearInterval(timer) }
  }, [load, preset.id])
  if (!value) return busy ? <Loading label="正在查询厂商余额与花费…"/> : <span className="small muted">暂未查到花费</span>
  const minutes = value.balance ? Math.max(0, Math.floor((now - Date.parse(value.balance.updated_at)) / 60000)) : null
  const tooltip = value.balance ? `${value.balance.label}：${value.balance.balances.map((b) => money(b.amount, b.currency)).join(" · ")}` : "暂无余额记录"
  return <div style={{ textAlign: "right" }} title={tooltip}>
    <div>{value.ready ? value.currencies.map((c) => c.ready ? `今天花了 ${money(c.today, c.currency)}` : `${c.currency} 刚开始记录`).join(" · ") : <span className="small muted">{spendHint(value)}</span>}</div>
    <div className="row small muted" style={{ justifyContent: "flex-end" }}>
      <span>{minutes == null ? "" : minutes < 1 ? "更新于刚刚" : `更新于 ${minutes} 分钟前`}</span>
      <Button small busy={busy} onClick={() => setRevision((r) => r + 1)}><RefreshCw size={13} />刷新</Button>
    </div>
  </div>
}

function Models() {
  const toast = useApp((s) => s.toast)
  const [providers, setProviders] = React.useState<Provider[] | null>(null)
  const [presets, setPresets] = React.useState<Preset[]>([])
  const [activeId, setActiveId] = React.useState<string | null>(null)
  const [form, setForm] = React.useState({ provider_id: "deepseek", preset_id: "" as string | null, display_name: "", base_url: "", model: "", api_key: "", timeout: 120 })
  const [saving, setSaving] = React.useState(false)
  const [testing, setTesting] = React.useState(false)
  const [testFault, setTestFault] = React.useState<import('../store').ModelFault|null>(null)
  const testCancel=React.useRef<AbortController|null>(null)
  React.useEffect(()=>()=>testCancel.current?.abort(),[])
  const [balanceReload, setBalanceReload] = React.useState(0)
  const [loadError,setLoadError]=React.useState('')

  const fill = React.useCallback((providerId: string, list: Preset[], all: Provider[]) => {
    const existing = list.find((p) => p.provider_id === providerId)
    const spec = all.find((p) => p.id === providerId)
    setForm({
      provider_id: providerId, preset_id: existing?.id ?? null,
      display_name: existing?.display_name ?? spec?.name ?? providerId,
      base_url: existing?.base_url ?? spec?.default_base_url ?? "",
      model: existing?.model ?? spec?.default_model ?? "",
      api_key: "", timeout: existing?.timeout ?? 120,
    })
  }, [])

  const load = React.useCallback(async () => {
    setLoadError('')
    const [p, l] = await Promise.all([call<{ providers: Provider[] }>("models.providers"),
      call<{ presets: Preset[]; active_id: string | null }>("models.list")])
    setProviders(p.providers)
    setPresets(l.presets)
    setActiveId(l.active_id)
    const active = l.presets.find((x) => x.id === l.active_id)
    fill(active?.provider_id ?? "deepseek", l.presets, p.providers)
  }, [fill])
  React.useEffect(() => { load().catch((e) => setLoadError(e.message)) }, [load, toast])

  const active = presets.find((p) => p.id === activeId)
  const selected = providers?.find((p) => p.id === form.provider_id)
  const existing = presets.find((p) => p.id === form.preset_id)
  const relay = isRelay(form.provider_id)

  async function save() {
    setSaving(true)
    try {
      const r = await call<{ presets: Preset[]; active_id: string }>("models.save", {
        provider_id: form.provider_id, preset_id: form.preset_id ?? undefined, display_name: form.display_name,
        base_url: form.base_url, model: form.model, api_key: form.api_key || undefined, timeout: Number(form.timeout),
      })
      setPresets(r.presets)
      setActiveId(r.active_id)
      setForm((f) => ({ ...f, api_key: "", preset_id: r.active_id }))
      toast("ok", "已保存并设为当前使用的模型。")
    } catch (e) {
      toast("error", (e as Error).message)
    } finally { setSaving(false) }
  }

  async function test() {
    setTesting(true)
    setTestFault(null)
    testCancel.current=new AbortController()
    try {
      const r = await call<{ latency_ms: number; model: string }>("models.test",{}, {signal:testCancel.current.signal})
      toast("ok", `连接正常：${r.model || "模型"} 在 ${(r.latency_ms / 1000).toFixed(1)} 秒内回复。`)
    } catch (e) {
      const error = e as BridgeFailure
      setTestFault(error.model_fault ?? null)
      toast("error", error.message)
    } finally { testCancel.current=null;setTesting(false) }
  }

  useModelRetry(test,f=>f.purpose === 'connection_test' || f.purpose === 'models.test' || !f.purpose)
  async function action(method: "models.select" | "models.delete", id: string) {
    try {
      const r = await call<{ presets: Preset[]; active_id: string | null }>(method, { preset_id: id })
      setPresets(r.presets)
      setActiveId(r.active_id)
      if (method === "models.delete") toast("ok", "已删除该模型预设及其保存的 Key。")
    } catch (e) { toast("error", (e as Error).message) }
  }

  if (!providers) return loadError ? <div className="card" role="alert">{loadError}<Button onClick={()=>load().catch(e=>setLoadError(e.message))}>重试</Button></div>
    : <Loading shape="form" label="正在读取模型预设…" onRetry={()=>load().catch(e=>setLoadError(e.message))}/>

  return (
    <div className="stack">
      <Loading active={testing || saving} label={testing ? '正在验证模型连接…' : '正在保存模型预设…'} onCancel={testing ? ()=>testCancel.current?.abort() : undefined}/>
      {testFault && <ModelFaultPanel fault={testFault} onRetry={test}/>}
      <div className="card">
        <div className="row between">
          <div>
            <h2>当前使用</h2>
            <p className="sub">{active ? `${active.display_name} · ${active.model}` : "还没有可用的模型：请到 设置 → 模型连接 选择厂商、填入 API Key 并保存。"}</p>
          </div>
          <div className="row">
            {active && (active.has_api_key ? <span className="pill ok"><CheckCircle2 size={13} />Key 已保存</span> : <span className="pill warn"><KeyRound size={13} />缺少 Key</span>)}
            <Button onClick={test} busy={testing} disabled={!active}><RefreshCw />测试连接</Button>
          </div>
        </div>
      </div>

      <div className="card stack">
        <h2>选择厂商</h2>
        <div className="grid auto">
          {providers.map((p) => (
            <button key={p.id} className={`provider ${form.provider_id === p.id ? "on" : ""}`} onClick={() => fill(p.id, presets, providers)}>
              <div className="logo">{isRelay(p.id) ? <Waypoints size={18} /> : p.name.slice(0, 1)}</div>
              <b style={{ minWidth: 0 }}>{p.name}</b>
            </button>
          ))}
        </div>
      </div>

      <div className="card stack">
        <div className="row between">
          <h2>{selected?.name} 连接信息</h2>
          {existing && <span className="pill">已有预设</span>}
        </div>
        <div className="grid two">
          <Field label="名称"><input className="input" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></Field>
          <Field label="模型名称" hint={relay ? "填中转站里的模型名。" : `默认 ${selected?.default_model ?? ""}`}>
            <input className="input" value={form.model} placeholder={relay ? "例如 claude-sonnet-5" : ""} onChange={(e) => setForm({ ...form, model: e.target.value })} />
          </Field>
          <Field label="接口地址" hint={relay ? "填中转站给你的地址。" : "一般不用改。用中转站请选上面的“中转站”。"}>
            <input className="input" value={form.base_url} placeholder={relay ? "https://…/v1" : ""} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
          </Field>
          <Field label="API Key" hint={existing?.has_api_key ? "已保存在本机 Windows 凭据里。留空表示继续用原来的 Key。" : "只保存在这台电脑的 Windows 凭据里，不会上传。"}>
            <input className="input" type="password" autoComplete="off" placeholder={existing?.has_api_key ? "••••••••（已保存）" : "粘贴你的 API Key"}
                   value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
          </Field>
          <Field label="请求时限（秒）" hint="网络慢或模型思考时间长时可以调大。">
            <input className="input" type="number" min={1} max={300} value={form.timeout} onChange={(e) => setForm({ ...form, timeout: Number(e.target.value) })} />
          </Field>
        </div>
        <div className="row">
          <Button kind="primary" onClick={save} busy={saving}><Bot />保存并使用</Button>
        </div>
      </div>

      {presets.length > 0 && (
        <div className="card stack">
          <div className="row between"><h2>已保存的预设</h2><Button small onClick={() => setBalanceReload((n) => n + 1)}><RefreshCw />刷新余额</Button></div>
          <table className="table">
            <thead><tr><th>名称</th><th>模型</th><th>Key</th><th>今天花费</th><th /></tr></thead>
            <tbody>
              {presets.map((p) => (
                <tr key={p.id}>
                  <td><b>{p.display_name}</b>{p.id === activeId && <span className="pill ok" style={{ marginLeft: 8 }}>使用中</span>}</td>
                  <td className="muted">{p.model}</td>
                  <td>{p.has_api_key ? "已保存" : <span style={{ color: "var(--warn)" }}>未保存</span>}</td>
                  <td><ApiBalance preset={p} reload={balanceReload} /></td>
                  <td style={{ textAlign: "right" }}>
                    <div className="row" style={{ justifyContent: "flex-end" }}>
                      {p.id !== activeId && <Button small onClick={() => action("models.select", p.id)}>使用</Button>}
                      <Button small kind="danger" onClick={() => action("models.delete", p.id)}><Trash2 />删除</Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------- usage
const PURPOSE: Record<string, string> = {
  connection_test: "连接测试", research: "找资料", source_scoring: "资料评分", sufficiency: "需求核对", outline: "生成轮廓",
  generate: "生成计划", teacher: "老师讲课", judge: "判定作答", question: "出题", profile: "学习档案", free_chat: "教师闲聊",
}
type Agg = { calls: number; failed: number; cancelled: number; in_progress: number;
  input_tokens: number | null; cached_input_tokens: number | null; uncached_input_tokens: number | null;
  output_tokens: number | null; search_requests: number | null; cache_hit_rate: string | null;
  usage_complete: boolean; unknown_fields: string[]; incomplete_reasons: Record<string, number>;
  transport_unknown_calls?: number; cancelled_unknown_calls?: number; partial_usage_calls?: number }

function fmt(n: number | null | undefined) { return n == null ? "—" : n.toLocaleString("zh-CN") }
function rate(n: string | null | undefined) { return n == null ? "—" : `${(Number(n) * 100).toFixed(1)}%` }
const METER_NAMES: Record<string, string> = { input_tokens: "输入", cached_input_tokens: "缓存命中", uncached_input_tokens: "缓存未命中",
  output_tokens: "输出", reasoning_tokens: "推理分项", search_requests: "搜索次数" }
function UsageGap({ value }: { value: Agg }) {
  const missing = new Set(value.unknown_fields ?? [])
  for (const field of ["input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens", "search_requests"] as const) {
    if (value[field] == null) missing.add(field)
  }
  if (value.usage_complete !== false && !missing.size) return null
  const reasons = Object.entries(value.incomplete_reasons ?? {})
  return <div className="sub">未完整统计：{[...missing].map((f) => METER_NAMES[f] ?? f).join("、")}。
    {!!value.transport_unknown_calls && <span> 有 {value.transport_unknown_calls} 次调用超时或断线，用量未知（可能已计费）。</span>}
    {!!value.cancelled_unknown_calls && <span> 有 {value.cancelled_unknown_calls} 次调用取消，用量未知（可能已计费）。</span>}
    {!!value.partial_usage_calls && <span> {value.partial_usage_calls} 次调用已记录收到的部分用量。</span>}
    {reasons.length ? reasons.map(([reason, count]) => <span key={reason}> {reason}（{count} 次）。</span>) : "返回数据未包含完整统计字段。"}
    未知字段显示“—”，不会将缺失量当作零。原因计数可能重叠。
  </div>
}

function Usage() {
  const toast = useApp((s) => s.toast)
  const [days, setDays] = React.useState(7)
  const [groupBy, setGroupBy] = React.useState<"purpose" | "model" | "day">("purpose")
  const [data, setData] = React.useState<{ total: Agg; groups: (Agg & { key: string })[] } | null>(null)
  const [error,setError]=React.useState('')
  const [attempt,retry]=React.useReducer(n=>n+1,0)
  React.useEffect(() => {
    let current = true
    setData(null)
    setError('')
    call("usage.summary", { days, group_by: groupBy }).then((r: any) => { if (current) setData(r) })
      .catch((e) => { if (current) setError(e.message) })
    return () => { current = false }
  }, [days, groupBy, toast,attempt])
  const t = data?.total
  const meters = [["模型调用（含失败）", t?.calls], ["输入 token", t?.input_tokens], ["缓存命中 token", t?.cached_input_tokens],
    ["缓存未命中 token", t?.uncached_input_tokens], ["输出 token", t?.output_tokens], ["搜索次数", t?.search_requests]] as const
  return (
    <div className="stack">
      <BalanceSpend />
      <div className="row between">
        <Segmented value={days} onChange={setDays} options={[{ value: 1, label: "今天" }, { value: 7, label: "近 7 天" }, { value: 30, label: "近 30 天" }]} />
        <Segmented value={groupBy} onChange={setGroupBy} options={[{ value: "purpose", label: "按用途" }, { value: "model", label: "按模型" }, { value: "day", label: "按日期（UTC）" }]} />
      </div>
      <div className="grid three">
        {meters.map(([label, value]) => <div className="card stat" key={label}><div className="label">{label}</div><div className="value">{t ? fmt(value) : "…"}</div></div>)}
        <div className="card stat"><div className="label">缓存命中率</div><div className="value">{t ? rate(t.cache_hit_rate) : "…"}</div><div className="hint">缓存命中 ÷ 输入 token</div></div>
      </div>
      {t && <><p className="sub">失败 {fmt(t.failed)} 次 · 取消 {fmt(t.cancelled)} 次 · 尚未结束 {fmt(t.in_progress)} 次；均计入调用次数。</p><UsageGap value={t} /></>}
      <div className="card" style={{ overflowX: "auto" }}>
        {!data ? error ? <div role="alert">{error}<Button onClick={retry}>重试</Button></div> : <Loading shape="cards" label="正在读取模型用量…" resetKey={`${days}:${groupBy}:${attempt}`} onRetry={retry}/> : data.groups.length === 0 ? <p className="sub">这段时间还没有模型调用。</p> : (
          <table className="table">
            <thead><tr><th>{groupBy === "purpose" ? "用途" : groupBy === "model" ? "模型" : "日期（UTC）"}</th><th>次数</th><th>输入 token</th><th>缓存命中</th><th>缓存未命中</th><th>输出 token</th><th>搜索次数</th><th>命中率</th></tr></thead>
            <tbody>{data.groups.map((g) => <React.Fragment key={String(g.key)}>
              <tr><td><b>{groupBy === "purpose" ? PURPOSE[g.key] ?? g.key : String(g.key).replace("/", " · ")}</b></td>
                <td>{fmt(g.calls)}</td><td>{fmt(g.input_tokens)}</td><td>{fmt(g.cached_input_tokens)}</td><td>{fmt(g.uncached_input_tokens)}</td>
                <td>{fmt(g.output_tokens)}</td><td>{fmt(g.search_requests)}</td><td>{rate(g.cache_hit_rate)}</td></tr>
              {!g.usage_complete && <tr><td colSpan={8}><UsageGap value={g} /></td></tr>}
            </React.Fragment>)}</tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function BalanceSpend() {
  const [value, setValue] = React.useState<Spend | null>(null)
  const [preset, setPreset] = React.useState<Preset | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState("")
  const request = React.useRef(0)
  const load = React.useCallback(async (refresh = false) => {
    const sequence = ++request.current
    setBusy(true)
    try {
      const result = await call<{ preset: Preset | null; spend: Spend | null }>("usage.spend", { refresh })
      if (sequence === request.current) { setPreset(result.preset); setValue(result.spend); setError("") }
    } catch { if (sequence === request.current) setError("花费暂时无法读取，请稍后刷新。") }
    finally { if (sequence === request.current) setBusy(false) }
  }, [])
  React.useEffect(() => {
    void load(true)
    const off = on("balance_spend", () => void load())
    // Refresh local date buckets at midnight even when no new observation arrives.
    const timer = setInterval(() => void load(), 30000)
    return () => { ++request.current; off(); clearInterval(timer) }
  }, [load])
  const currencies = value?.currencies.length ? value.currencies : [{ currency: "CNY", ready: false, today: "0", week: "0", month: "0", days: [] }]
  return <section className="stack" aria-label="账户余额变化花费">
    <div className="row between"><h3>花费{preset ? ` · ${preset.display_name}` : ""}</h3><Button small busy={busy} onClick={() => void load(true)}><RefreshCw />刷新花费</Button></div>
    <Loading active={busy} label="正在查询账户余额与花费…"/>
    {currencies.map((currency) => <React.Fragment key={currency.currency}>
      <div className="grid three">
        {([["今天花了", currency.today], ["本周", currency.week], ["本月", currency.month]] as const).map(([label, amount]) => <div className="card stat" key={label}>
          <div className="label">{label}</div><div className="value">{currency.ready ? money(amount, currency.currency) : "—"}</div>
        </div>)}
      </div>
      <p className="small muted">按账户余额变化计算；如果这把 Key 也在别处使用，那部分也会算进来</p>
      {!currency.ready && (value ? <p className="sub" role="status">{spendHint(value)}</p> : busy ? null : <p className="sub">尚未选择可查询余额的模型预设</p>)}
      {value?.query_failed && <p className="small muted" role="status">本次余额查询未成功，已保留原有记录，下次再查。</p>}
      <div className="card" aria-label={`最近 14 天花费（${currency.currency}）`} style={{ padding: 20 }}>
        <h3>最近 14 天</h3>
        {currency.days.length ? <div role="list" style={{ display: "flex", gap: 6, marginTop: 16, alignItems: "flex-end", height: 152 }}>
          {currency.days.map((day, index) => {
            const max = Math.max(...currency.days.map((d) => Number(d.amount)), 0.0001)
            const label = `${day.date}：${currency.ready ? money(day.amount, currency.currency) : "尚未开始统计"}`
            return <div role="listitem" aria-label={label} title={label} key={day.date} style={{ flex: 1, minWidth: 0, textAlign: "center" }}>
              <div style={{ height: Math.max(2, Number(day.amount) / max * 112), background: "var(--accent)", borderRadius: "5px 5px 0 0", opacity: Number(day.amount) > 0 ? 0.85 : 0.18 }} />
              <div className="small muted" style={{ marginTop: 6, fontSize: "0.65rem", whiteSpace: "nowrap" }}>{index % 2 === 0 || index === 13 ? day.date.slice(5) : "\u00a0"}</div>
            </div>
          })}
        </div> : <p className="sub">查询余额后开始记录。</p>}
      </div>
    </React.Fragment>)}
    {error && <p className="sub" role="status">{error}</p>}
    <h3>Token 与缓存统计</h3>
  </section>
}

// ---------------------------------------------------------------- learning
const STYLE_ICON: Record<string, any> = { warm: HeartHandshake, strict: Target, humorous: Laugh, calm: BookOpenText }
const STYLE = {
  warm: ["温和", "耐心、多鼓励，节奏舒缓，错了也会先肯定你做对的部分。"],
  strict: ["严厉", "要求高、指出问题很直接；反复出错会追问你上次讲过什么，但绝不贬低人。"],
  humorous: ["幽默", "轻松有趣，爱用段子和梗帮你记住知识点。"],
  calm: ["沉稳", "条理清晰、冷静克制，像一位讲课干净利落的老教授。"],
} as Record<string, [string, string]>
const DOMAIN: Record<string, string> = { life: "生活", games: "游戏", programming: "编程", sports: "体育", music: "音乐", cooking: "烹饪", film: "影视", business: "商业", nature: "自然" }

type PresetGroup = { presets: TeacherPreset[]; default_id: string }

function Learning() {
  const settings = useApp((s) => s.settings)!
  const toast = useApp((s) => s.toast)
  const [group, setGroup] = React.useState<PresetGroup>({ presets: [], default_id: "" })
  const [edit, setEdit] = React.useState<TeacherPreset | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [confirm, setConfirm] = React.useState<{ group: PresetGroup; message: string } | null>(null)
  React.useEffect(() => {
    if (settings.teacher_presets) setGroup({ presets: settings.teacher_presets, default_id: settings.default_teacher_preset_id! })
  }, [settings.teacher_presets, settings.default_teacher_preset_id])
  React.useEffect(() => {
    let live = true
    call<PresetGroup>("settings.teacher_presets").then((r) => { if (live) setGroup(r) }).catch((e) => toast("error", e.message))
    return () => { live = false }
  }, [toast])
  const save = async (next: PresetGroup, confirmed = false) => {
    setBusy(true)
    try {
      const r = await call<PresetGroup & { confirmation_required?: boolean; message?: string }>("settings.save_teacher_presets", { ...next, confirm_delete: confirmed })
      if (r.confirmation_required) { setConfirm({ group: next, message: r.message! }); return }
      setGroup({ presets: r.presets, default_id: r.default_id }); setConfirm(null); setEdit(null)
      toast("ok", "老师预设已保存，下次讲课立即使用。")
    } catch (e) { toast("error", (e as Error).message) } finally { setBusy(false) }
  }
  const create = () => {
    const name = ["预设一", "预设二", "预设三", "预设四"].find((n) => !group.presets.some((p) => p.name === n)) ?? "新老师"
    setEdit({ id: `teacher-${crypto.randomUUID()}`, name, persona: "", style: "warm", detail: "brief", examples: "normal", analogies: ["life"] })
  }
  const change = (patch: Partial<TeacherPreset>) => setEdit((p) => p ? { ...p, ...patch } : p)
  return (
    <div className="stack" data-teacher-presets>
      <div className="row between"><div><h2>老师预设</h2><p className="sub">新计划使用默认预设；每个计划可以随时换老师。人设只影响讲课措辞，教学规则优先。</p></div>
        <Button onClick={create} disabled={busy || !!edit || group.presets.length >= 4}>新建预设</Button></div>
      {group.presets.length >= 4 && <p className="muted">最多 4 个预设</p>}
      {group.presets.map((p) => (
        <div key={p.id} className="card stack" data-preset-id={p.id}>
          <div className="row between"><h3>{p.name} {p.id === group.default_id && <span className="badge">默认</span>}</h3>
            <div className="row"><Button kind="ghost" disabled={busy || !!edit} onClick={() => setEdit({ ...p, analogies: [...p.analogies] })}>编辑 {p.name}</Button>
              {p.id !== group.default_id && <Button kind="ghost" disabled={busy || !!edit} onClick={() => save({ ...group, default_id: p.id })}>设为默认</Button>}
              <Button kind="ghost" disabled={busy || !!edit || p.id === group.default_id} title={p.id === group.default_id ? "默认预设不能删，请先改默认" : "删除预设"}
                onClick={() => save({ ...group, presets: group.presets.filter((r) => r.id !== p.id) })}><Trash2 size={15} />删除</Button></div></div>
          <p style={{ whiteSpace: "pre-wrap" }}>{p.persona ? p.persona.slice(0, 140) + (p.persona.length > 140 ? "…" : "") : "未设置手写人设"}</p>
          <p className="sub">{STYLE[p.style]?.[0]} · 讲解{({ brief: "精简", normal: "适中", detailed: "详细" } as Record<string, string>)[p.detail]} · 例子{({ few: "少一些", normal: "适中", many: "多一些" } as Record<string, string>)[p.examples]} · 类比：{p.analogies.map((d) => DOMAIN[d]).join("、")}</p>
        </div>
      ))}
      {edit && <section className="card stack" aria-label="编辑老师预设">
        <h3>编辑老师预设</h3>
        <Field label="名称"><input className="input" aria-label="预设名称" value={edit.name} onChange={(e) => change({ name: e.target.value })} /></Field>
        <Field label="老师人设（可空，最多 2000 字）"><textarea className="input" aria-label="老师人设" rows={5} maxLength={2000} value={edit.persona} onChange={(e) => change({ persona: e.target.value })} /><span className="sub">{edit.persona.length} / 2000</span></Field>
        <h3>老师风格</h3><div className="grid two">{Object.entries(STYLE).map(([id, [name, text]]) => (
          <button key={id} className={`choice ${edit.style === id ? "on" : ""}`} onClick={() => change({ style: id })}><div className="ico">{React.createElement(STYLE_ICON[id] ?? UserRound)}</div><div><b>{name}</b><p>{text}</p></div></button>
        ))}</div>
        <div className="row between"><span>讲解详略</span><Segmented value={edit.detail} onChange={(v) => change({ detail: v })} options={[{ value: "brief", label: "精简" }, { value: "normal", label: "适中" }, { value: "detailed", label: "详细" }]} /></div>
        <div className="row between"><span>举例多少</span><Segmented value={edit.examples} onChange={(v) => change({ examples: v })} options={[{ value: "few", label: "少一些" }, { value: "normal", label: "适中" }, { value: "many", label: "多一些" }]} /></div>
        <h3>类比领域（至少一个）</h3><div className="row wrap">{Object.entries(DOMAIN).map(([id, name]) => (
          <button key={id} className={`tag-btn ${edit.analogies.includes(id) ? "on" : ""}`} onClick={() => {
            const next = edit.analogies.includes(id) ? edit.analogies.filter((d) => d !== id) : [...edit.analogies, id]
            if (next.length) change({ analogies: next })
          }}>{name}</button>
        ))}</div>
        <div className="row"><Button disabled={busy || !edit.name.trim()} onClick={() => save({ ...group, presets: group.presets.some((p) => p.id === edit.id) ? group.presets.map((p) => p.id === edit.id ? edit : p) : [...group.presets, edit] })}>保存预设</Button><Button kind="ghost" disabled={busy} onClick={() => setEdit(null)}>取消编辑</Button></div>
      </section>}
      {confirm && <section className="card stack" role="dialog" aria-modal="true" aria-label="确认删除预设"><h3>删除预设</h3><p>{confirm.message}</p><div className="row"><Button disabled={busy} onClick={() => save(confirm.group, true)}>确认删除</Button><Button kind="ghost" disabled={busy} onClick={() => setConfirm(null)}>取消</Button></div></section>}
    </div>
  )
}

// -------------------------------------------------------------------- time
function TimeAndReminders() {
  const settings = useApp((s) => s.settings)!
  const save = useSaveSettings()
  return (
    <div className="stack">
      <div className="card stack">
        <div className="row between">
          <div><h2><RefreshCw size={18} style={{ verticalAlign: -3, marginRight: 8 }} />每天几点算新的一天</h2>
            <p className="sub">到这个时间，今天的学习任务刷新。深夜还在上课时不会打断你，那节课会记到前一天。</p></div>
          <input className="input" type="time" style={{ width: 140 }} value={settings.refresh_time}
                 onChange={(e) => e.target.value && save({ refresh_time: e.target.value })} />
        </div>
      </div>
      <div className="card stack">
        <div className="row between">
          <div><h2><BellRing size={18} style={{ verticalAlign: -3, marginRight: 8 }} />学习提醒</h2>
            <p className="sub">到点还没完成今天的任务，会在软件里提醒你一次。软件关着时不会提醒。</p></div>
          <Toggle label="学习提醒" on={settings.reminder_enabled} onChange={(v) => save({ reminder_enabled: v })} />
        </div>
        {settings.reminder_enabled && (
          <div className="row fade-in">
            <span className="muted">提醒时间</span>
            <input className="input" type="time" style={{ width: 140 }} value={settings.reminder_time}
                   onChange={(e) => e.target.value && save({ reminder_time: e.target.value })} />
          </div>
        )}
      </div>
    </div>
  )
}

// -------------------------------------------------------------- appearance
function Appearance() {
  const settings = useApp((s) => s.settings)!
  const save = useSaveSettings()
  return (
    <div className="stack">
      <div className="grid two">
        <button className={`theme-card ${settings.theme === "sakura" ? "on" : ""}`} onClick={() => save({ theme: "sakura" })}>
          <div className="preview" style={{ background: "linear-gradient(135deg,#fde7ef,#f6d3e2 50%,#fbeee4)" }}>
            <div style={{ position: "absolute", left: 14, top: 14, bottom: 14, width: 54, borderRadius: 12, background: "linear-gradient(180deg,#3d2239,#2e1a2c)" }} />
            <div style={{ position: "absolute", left: 82, top: 18, right: 18, height: 38, borderRadius: 12, background: "rgba(255,255,255,.8)" }} />
            <div style={{ position: "absolute", left: 82, top: 64, width: 70, height: 34, borderRadius: 11, background: "linear-gradient(135deg,#f07aa2,#b8457a)" }} />
          </div>
          <div className="meta"><b>樱花</b><span>柔和的粉与深梅紫，樱花花瓣缓缓飘落。</span></div>
        </button>
        <button className={`theme-card ${settings.theme === "aurora" ? "on" : ""}`} onClick={() => save({ theme: "aurora" })}>
          <div className="preview" style={{ background: "radial-gradient(120% 90% at 20% 0%,rgba(79,224,205,.35),transparent 55%),radial-gradient(120% 90% at 90% 20%,rgba(155,140,255,.4),transparent 55%),#0c0e1c" }}>
            <div style={{ position: "absolute", left: 14, top: 14, bottom: 14, width: 54, borderRadius: 12, background: "#0a0c19", border: "1px solid rgba(170,176,255,.15)" }} />
            <div style={{ position: "absolute", left: 82, top: 18, right: 18, height: 38, borderRadius: 12, background: "rgba(30,34,64,.8)", border: "1px solid rgba(170,176,255,.15)" }} />
            <div style={{ position: "absolute", left: 82, top: 64, width: 70, height: 34, borderRadius: 11, background: "linear-gradient(135deg,#4fe0cd,#9b8cff,#ff7ab6)" }} />
          </div>
          <div className="meta"><b>极光夜</b><span>深夜学习的暗色主题，极光在远处缓缓流动。</span></div>
        </button>
      </div>
      <div className="card stack">
        <div className="row between">
          <div><h2>文字大小</h2><p className="sub">当前 {Math.round(settings.font_scale * 100)}%</p></div>
          <input type="range" min={0.85} max={1.4} step={0.05} value={settings.font_scale} style={{ width: 260, accentColor: "var(--accent)" }}
                 onChange={(e) => save({ font_scale: Number(e.target.value) })} />
        </div>
        <div className="row between">
          <div><h2>减少动态效果</h2><p className="sub">关闭飘落的花瓣、流动背景和过渡动画。</p></div>
          <Toggle label="减少动态效果" on={settings.reduce_motion} onChange={(v) => save({ reduce_motion: v })} />
        </div>
      </div>
    </div>
  )
}

// ----------------------------------------------------------------- account
function Account() {
  const account = useApp((s) => s.account)
  const act = (action: string) => call("app.account", { action }).catch(() => undefined)
  return (
    <div className="stack">
      <div className="card">
        <div className="row">
          <div className="avatar" style={{ width: 52, height: 52, fontSize: "1.3rem" }}>{account?.name?.slice(0, 1) ?? "M"}</div>
          <div><h2 style={{ margin: 0 }}>{account?.name}</h2><span className="muted">服务端身份：{account?.role_label}</span></div>
        </div>
      </div>
      <div className="card stack">
        <div className="row between">
          <div><h2>邮箱、密码与注销</h2><p className="sub">查看绑定邮箱、修改密码、注销账号。</p></div>
          <Button onClick={() => act("account_settings")} disabled={!account?.settings_available}><ShieldCheck />打开账号设置</Button>
        </div>
        <div className="row between">
          <div><h2>切换账号</h2><p className="sub">回到登录页，换一个账号登录。</p></div>
          <Button onClick={() => act("switch")}><Users />切换账号</Button>
        </div>
        <div className="row between">
          <div><h2>退出登录</h2><p className="sub">本机学习数据会保留，下次登录继续。</p></div>
          <Button kind="danger" onClick={() => act("logout")}><LogOut />退出登录</Button>
        </div>
      </div>
    </div>
  )
}
