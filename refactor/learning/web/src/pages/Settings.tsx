import * as React from "react"
import {
  BellRing, BookOpenText, Bot, CheckCircle2, Clock3, Coins, GraduationCap, HeartHandshake, KeyRound, Laugh, LogOut, Palette, PlugZap, RefreshCw,
  ShieldCheck, Target, Trash2, UserRound, Users, Waypoints,
} from "lucide-react"
import { call, BridgeFailure } from "../bridge"
import { useApp, type Settings as S, type SettingsTab } from "../store"
import { Button, Field, Segmented, Skeleton, Toggle } from "../ui"

const TABS: { id: SettingsTab; label: string; icon: any }[] = [
  { id: "models", label: "模型连接", icon: PlugZap },
  { id: "usage", label: "模型用量", icon: Coins },
  { id: "learning", label: "学习偏好", icon: GraduationCap },
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
        </div>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ models
type Provider = { id: string; name: string; default_base_url: string; default_model: string; search_supported: boolean; search_verified: boolean }
type Preset = { id: string; provider_id: string; display_name: string; base_url: string; model: string; temperature: number | null; timeout: number; has_api_key: boolean }

const isRelay = (id: string) => id.startsWith("relay_")

type VendorBalance = { label: string; balances: { amount: string; currency: string }[]; updated_at: string }

function ApiBalance({ preset, reload }: { preset: Preset; reload: number }) {
  const [value, setValue] = React.useState<VendorBalance | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [revision, setRevision] = React.useState(0)
  const [now, setNow] = React.useState(Date.now())
  React.useEffect(() => {
    let current = true
    setValue(null)
    if (!preset.has_api_key) return
    setBusy(true)
    call<{ balance: VendorBalance | null }>("models.balance", { preset_id: preset.id, refresh: reload > 0 || revision > 0 })
      .then((r) => { if (current) setValue(r.balance) })
      .catch(() => { if (current) setValue(null) })
      .finally(() => { if (current) setBusy(false) })
    return () => { current = false }
  }, [preset, reload, revision])
  React.useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30000)
    return () => clearInterval(timer)
  }, [])
  if (!value) return null
  const minutes = Math.max(0, Math.floor((now - Date.parse(value.updated_at)) / 60000))
  return <div style={{ textAlign: "right" }}>
    <div>{value.label}：{value.balances.map((b) => `${b.currency} ${b.amount}`).join(" · ")}</div>
    <div className="row small muted" style={{ justifyContent: "flex-end" }}>
      <span>{minutes < 1 ? "更新于刚刚" : `更新于 ${minutes} 分钟前`}</span>
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
  const [balanceReload, setBalanceReload] = React.useState(0)

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
    const [p, l] = await Promise.all([call<{ providers: Provider[] }>("models.providers"),
      call<{ presets: Preset[]; active_id: string | null }>("models.list")])
    setProviders(p.providers)
    setPresets(l.presets)
    setActiveId(l.active_id)
    const active = l.presets.find((x) => x.id === l.active_id)
    fill(active?.provider_id ?? "deepseek", l.presets, p.providers)
  }, [fill])
  React.useEffect(() => { load().catch((e) => toast("error", e.message)) }, [load, toast])

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
    try {
      const r = await call<{ latency_ms: number; model: string }>("models.test")
      toast("ok", `连接正常：${r.model || "模型"} 在 ${(r.latency_ms / 1000).toFixed(1)} 秒内回复。`)
    } catch (e) {
      toast("error", `连接失败：${(e as BridgeFailure).message}`)
    } finally { setTesting(false) }
  }

  async function action(method: "models.select" | "models.delete", id: string) {
    try {
      const r = await call<{ presets: Preset[]; active_id: string | null }>(method, { preset_id: id })
      setPresets(r.presets)
      setActiveId(r.active_id)
      if (method === "models.delete") toast("ok", "已删除该模型预设及其保存的 Key。")
    } catch (e) { toast("error", (e as Error).message) }
  }

  if (!providers) return <div className="card stack"><Skeleton h={24} w="40%" /><Skeleton h={120} /><Skeleton h={200} /></div>

  return (
    <div className="stack">
      <div className="card">
        <div className="row between">
          <div>
            <h2>当前使用</h2>
            <p className="sub">{active ? `${active.display_name} · ${active.model}` : "还没有可用的模型。选择厂商、粘贴 API Key 即可。"}</p>
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
            <thead><tr><th>名称</th><th>模型</th><th>Key</th><th>厂商余额</th><th /></tr></thead>
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
  usage_complete: boolean; unknown_fields: string[]; incomplete_reasons: Record<string, number> }

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
    {reasons.length ? reasons.map(([reason, count]) => <span key={reason}> {reason}（{count} 次）。</span>) : "返回数据未包含完整统计字段。"}
    未知字段显示“—”，不会将缺失量当作零。原因计数可能重叠。
  </div>
}

function Usage() {
  const toast = useApp((s) => s.toast)
  const [days, setDays] = React.useState(7)
  const [groupBy, setGroupBy] = React.useState<"purpose" | "model" | "day">("purpose")
  const [data, setData] = React.useState<{ total: Agg; groups: (Agg & { key: string })[] } | null>(null)
  React.useEffect(() => {
    let current = true
    setData(null)
    call("usage.summary", { days, group_by: groupBy }).then((r: any) => { if (current) setData(r) })
      .catch((e) => { if (current) toast("error", e.message) })
    return () => { current = false }
  }, [days, groupBy, toast])
  const t = data?.total
  const meters = [["模型调用（含失败）", t?.calls], ["输入 token", t?.input_tokens], ["缓存命中 token", t?.cached_input_tokens],
    ["缓存未命中 token", t?.uncached_input_tokens], ["输出 token", t?.output_tokens], ["搜索次数", t?.search_requests]] as const
  return (
    <div className="stack">
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
        {!data ? <Skeleton h={120} /> : data.groups.length === 0 ? <p className="sub">这段时间还没有模型调用。</p> : (
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

// ---------------------------------------------------------------- learning
const STYLE_ICON: Record<string, any> = { warm: HeartHandshake, strict: Target, humorous: Laugh, calm: BookOpenText }
const STYLE = {
  warm: ["温和", "耐心、多鼓励，节奏舒缓，错了也会先肯定你做对的部分。"],
  strict: ["严厉", "要求高、指出问题很直接；反复出错会追问你上次讲过什么，但绝不贬低人。"],
  humorous: ["幽默", "轻松有趣，爱用段子和梗帮你记住知识点。"],
  calm: ["沉稳", "条理清晰、冷静克制，像一位讲课干净利落的老教授。"],
} as Record<string, [string, string]>
const DOMAIN: Record<string, string> = { life: "生活", games: "游戏", programming: "编程", sports: "体育", music: "音乐", cooking: "烹饪", film: "影视", business: "商业", nature: "自然" }

function Learning() {
  const settings = useApp((s) => s.settings)!
  const save = useSaveSettings()
  const toggleDomain = (d: string) => {
    const has = settings.analogy_domains.includes(d)
    const next = has ? settings.analogy_domains.filter((x) => x !== d) : [...settings.analogy_domains, d]
    if (next.length) save({ analogy_domains: next })
  }
  return (
    <div className="stack">
      <div className="card stack">
        <h2>老师的风格</h2>
        <p className="sub">所有计划共用。老师永远是同一位，只是说话方式不同。</p>
        <div className="grid two">
          {Object.entries(STYLE).map(([id, [name, text]]) => (
            <button key={id} className={`choice ${settings.teacher_style === id ? "on" : ""}`} onClick={() => save({ teacher_style: id })}>
              <div className="ico">{React.createElement(STYLE_ICON[id] ?? UserRound)}</div><div><b>{name}</b><p>{text}</p></div>
            </button>
          ))}
        </div>
      </div>
      <div className="card stack">
        <div className="row between">
          <div><h2>讲解详略</h2><p className="sub">一个知识点讲多深。需要时老师仍会把该讲的讲完。</p></div>
          <Segmented value={settings.detail} onChange={(v) => save({ detail: v })}
                     options={[{ value: "brief", label: "精简" }, { value: "normal", label: "适中" }, { value: "detailed", label: "详细" }]} />
        </div>
        <div className="row between">
          <div><h2>举例多少</h2><p className="sub">多用生活中的例子来解释，而不是照念资料。</p></div>
          <Segmented value={settings.examples} onChange={(v) => save({ examples: v })}
                     options={[{ value: "few", label: "少一些" }, { value: "normal", label: "适中" }, { value: "many", label: "多一些" }]} />
        </div>
      </div>
      <div className="card stack">
        <h2>喜欢的类比领域</h2>
        <p className="sub">老师会优先用这些领域打比方；实在套不上就不硬套。至少选一个。</p>
        <div className="row wrap">
          {Object.entries(DOMAIN).map(([id, name]) => (
            <button key={id} className={`tag-btn ${settings.analogy_domains.includes(id) ? "on" : ""}`} onClick={() => toggleDomain(id)}>{name}</button>
          ))}
        </div>
      </div>
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
