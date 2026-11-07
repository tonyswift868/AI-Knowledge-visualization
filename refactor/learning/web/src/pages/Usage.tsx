import * as React from "react"
import { call, on } from "../bridge"
import { Button, Segmented } from "../ui"
import { Loading } from "../loading/Loading"
import { addMoney, money } from "../usageMoney"

const PURPOSE: Record<string, string> = {
  connection_test: "连接测试", research: "找资料", source_scoring: "资料评分", sufficiency: "需求核对", outline: "生成轮廓",
  generate: "生成计划", teacher: "老师讲课", judge: "判定作答", question: "出题", profile: "学习档案", free_chat: "教师闲聊", rag_qa: "资料问答",
}
type Agg = {
  calls: number; failed: number; cancelled: number; in_progress: number;
  input_tokens: number | null; cached_input_tokens: number | null; uncached_input_tokens: number | null;
  output_tokens: number | null; search_requests: number | null; cache_hit_rate: string | null;
  usage_complete: boolean; unknown_fields: string[]; partial_usage_calls: number;
  transport_unknown_calls: number; cancelled_unknown_calls: number;
  known_amounts: Record<string, string>; estimated_amounts: Record<string, string>;
  unpriced_calls: number; amount_complete: boolean;
}
type Window = { start: string; end: string; total: Agg }
type Overview = { timezone: string; periods: { today: Window; week: Window; month: Window } }
type Summary = Window & { timezone: string; groups: (Agg & { key: string | string[] })[] }
type UsageCall = { request_id: string; at_utc: string; provider_id: string; model: string; purpose: string;
  state: string; error_code: string; usage: Record<string, any> | null; cost: { amount: string; currency: string } | null }
type Details = { start: string; end: string; timezone: string; total_calls: number; items: UsageCall[]; next_offset: number | null }
type BalanceResult = { preset?: { display_name: string }; spend: {
  balance: { balances: { amount: string; currency: string }[]; updated_at: string } | null;
  supported: boolean; query_failed?: boolean; unavailable?: boolean;
} | null }
const fmt = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString("zh-CN")

function Fees({ value }: { value: Agg }) {
  const amounts = { ...value.known_amounts }
  for (const [currency, amount] of Object.entries(value.estimated_amounts ?? {})) {
    amounts[currency] = addMoney(amounts[currency] ?? "0", amount)
  }
  const entries = Object.entries(amounts)
  return <>
    <div>{entries.length ? entries.map(([currency, amount]) => <div key={currency}>{money(amount, currency)}</div>)
      : value.calls === 0 ? "0" : "未知"}</div>
    {value.unpriced_calls > 0 && <div className="small muted">{entries.length ? "已知小计；另有 " : ""}{value.unpriced_calls} 次费用未知</div>}
    {value.partial_usage_calls > 0 && <div className="small muted">{value.partial_usage_calls} 次仅返回部分用量</div>}
  </>
}

function Meters({ value }: { value: Agg }) {
  return <>
    <p className="sub">调用 {fmt(value.calls)} 次 · 失败 {fmt(value.failed)} 次 · 取消 {fmt(value.cancelled)} 次 · 尚未结束 {fmt(value.in_progress)} 次</p>
    <p className="sub">输入 token {fmt(value.input_tokens)} · 缓存命中 {fmt(value.cached_input_tokens)} · 缓存未命中 {fmt(value.uncached_input_tokens)} · 输出 {fmt(value.output_tokens)} · 搜索 {fmt(value.search_requests)} 次
      {value.cache_hit_rate != null && <> · 缓存命中率 {(Number(value.cache_hit_rate) * 100).toFixed(1)}%</>}</p>
    {!value.usage_complete && <p className="small muted">用量未完整统计，未返回的字段显示“—”；失败、取消或断线的调用也可能产生费用。
      {value.transport_unknown_calls > 0 && <> 超时或断线后用量未知 {value.transport_unknown_calls} 次。</>}
      {value.cancelled_unknown_calls > 0 && <> 取消后用量未知 {value.cancelled_unknown_calls} 次。</>}
    </p>}
  </>
}

function localTime(value: string, timezone: string) {
  const date = new Date(value)
  try { return date.toLocaleString("zh-CN", { timeZone: timezone, hour12: false }) }
  catch { return date.toLocaleString("zh-CN", { hour12: false }) }
}

export function Usage() {
  const [overview, setOverview] = React.useState<Overview | null>(null)
  const [error, setError] = React.useState("")
  const [revision, refresh] = React.useReducer(n => n + 1, 0)
  const [details, setDetails] = React.useState(false)
  React.useEffect(() => {
    let current = true
    let sequence = 0
    const load = async () => {
      const own = ++sequence
      try {
        const result = await call<Overview>("usage.overview")
        if (current && own === sequence) { setOverview(result); setError("") }
      } catch { if (current && own === sequence) setError("本应用用量暂时无法读取，请重试。") }
    }
    void load()
    const off = on("balance_spend", () => void load())
    // Local day/week/month boundaries can change while this page stays open.
    const timer = setInterval(() => void load(), 30000)
    return () => { current = false; off(); clearInterval(timer) }
  }, [revision])
  return <div className="stack">
    <section className="stack" aria-label="本应用估算费用">
      <div className="row between"><h3>本应用估算费用</h3><Button small onClick={refresh}>刷新用量</Button></div>
      <p className="sub">按这台电脑上当前账号的 MentorFlow 调用记录估算，仅供参考；厂商账单以厂商记录为准。不同币种分别显示。</p>
      {error && <div role="alert">{error}<Button small onClick={refresh}>重试</Button>{overview && <p className="small muted">下面保留上次成功读取的结果。</p>}</div>}
      {!overview && !error && <Loading label="正在读取本应用用量…" onRetry={refresh} />}
      {overview && <>
        <div className="grid three">{([['today', '今天'], ['week', '本周'], ['month', '本月']] as const).map(([key, label]) =>
          <div className="card stat" key={key} aria-label={`${label}估算费用`}>
            <div className="label">{label} · 估算费用</div><div className="value"><Fees value={overview.periods[key].total} /></div>
          </div>)}</div>
        <p className="small muted">按本地日期（{overview.timezone}），本周从周一开始。更新至 {localTime(overview.periods.today.end, overview.timezone)}。</p>
        <Meters value={overview.periods.today.total} />
      </>}
      <Button onClick={() => setDetails(value => !value)}>{details ? "关闭详情" : "查看详情"}</Button>
      {details && <UsageDetails refreshKey={`${revision}:${overview?.periods.today.start ?? ""}`} />}
    </section>
    <VendorBalance />
  </div>
}

function UsageDetails({ refreshKey }: { refreshKey: string }) {
  const [days, setDays] = React.useState(7)
  const [groupBy, setGroupBy] = React.useState<"purpose" | "model" | "day">("purpose")
  const [summary, setSummary] = React.useState<Summary | null>(null)
  const [details, setDetails] = React.useState<Details | null>(null)
  const [summaryError, setSummaryError] = React.useState("")
  const [detailError, setDetailError] = React.useState("")
  const [revision, retry] = React.useReducer(n => n + 1, 0)
  const [busy, setBusy] = React.useState(false)
  const generation = React.useRef(0)
  const loading = React.useRef(false)
  React.useEffect(() => {
    let current = true
    setSummary(null); setSummaryError("")
    call<Summary>("usage.summary", { days, group_by: groupBy }).then(result => { if (current) setSummary(result) })
      .catch(() => { if (current) setSummaryError("分类用量暂时无法读取。") })
    return () => { current = false }
  }, [days, groupBy, revision, refreshKey])
  React.useEffect(() => {
    const own = ++generation.current
    loading.current = true
    setBusy(true); setDetails(null); setDetailError("")
    call<Details>("usage.details", { days, offset: 0, limit: 50 }).then(result => { if (own === generation.current) setDetails(result) })
      .catch(() => { if (own === generation.current) setDetailError("逐笔费用暂时无法读取。") })
      .finally(() => { if (own === generation.current) { loading.current = false; setBusy(false) } })
    return () => { ++generation.current }
  }, [days, revision, refreshKey])
  const loadMore = async () => {
    if (!details || details.next_offset == null || loading.current) return
    const own = generation.current
    loading.current = true; setBusy(true); setDetailError("")
    try {
      const result = await call<Details>("usage.details", { days, end: details.end, offset: details.next_offset, limit: 50 })
      if (own === generation.current) setDetails(previous => previous && ({ ...result,
        items: [...previous.items, ...result.items.filter(item => !previous.items.some(old => old.request_id === item.request_id))] }))
    } catch { if (own === generation.current) setDetailError("加载更多失败，请重试。") }
    finally { if (own === generation.current) { loading.current = false; setBusy(false) } }
  }
  return <section className="stack" aria-label="用量详情">
    <h3>用量详情</h3>
    <div className="row between">
      <Segmented value={days} onChange={setDays} options={[{ value: 1, label: "今天" }, { value: 7, label: "近 7 天" }, { value: 30, label: "近 30 天" }, { value: 365, label: "近 365 天" }]} />
      <Segmented value={groupBy} onChange={setGroupBy} options={[{ value: "purpose", label: "按用途" }, { value: "model", label: "按模型" }, { value: "day", label: "按本地日期" }]} />
    </div>
    {summaryError && <div role="alert">{summaryError}<Button small onClick={retry}>重试</Button></div>}
    {!summary && !summaryError && <Loading label="正在读取分类用量…" />}
    {summary && <>
      <p className="small muted">{localTime(summary.start, summary.timezone)} 至 {localTime(summary.end, summary.timezone)}（{summary.timezone}）</p>
      <div className="card" style={{ overflowX: "auto" }}><table className="table">
        <thead><tr><th>{groupBy === "purpose" ? "用途" : groupBy === "model" ? "模型" : "本地日期"}</th><th>次数</th><th>估算费用</th><th>输入 token</th><th>输出 token</th></tr></thead>
        <tbody>{summary.groups.map(group => <tr key={String(group.key)}>
          <td>{groupBy === "purpose" ? PURPOSE[String(group.key)] ?? String(group.key) : Array.isArray(group.key) ? group.key.join(" · ") : group.key}</td>
          <td>{fmt(group.calls)}</td><td><Fees value={group} /></td><td>{fmt(group.input_tokens)}</td><td>{fmt(group.output_tokens)}</td>
        </tr>)}</tbody>
      </table>{summary.groups.length === 0 && <p className="sub">这段时间还没有模型调用。</p>}</div>
      <Meters value={summary.total} />
    </>}
    <h3>逐笔费用 · 估算</h3>
    {details && <>
      <p className="small muted">已显示 {details.items.length} / {details.total_calls} 次调用（{details.timezone}）</p>
      <div className="card" style={{ overflowX: "auto" }}><table className="table">
        <thead><tr><th>本地时间</th><th>用途</th><th>模型</th><th>状态</th><th>估算费用</th></tr></thead>
        <tbody>{details.items.map(item => {
          const meta = item.usage?.raw?._mentorflow ?? item.usage?._mentorflow ?? {}
          const partial = meta.partial_usage === true || item.usage?.partial_usage === true
          const incomplete = !item.usage || partial || meta.unreported_fields?.length > 0 || meta.token_total_reconciled === false || item.usage?.usage_complete === false
          return <tr key={item.request_id}>
            <td>{localTime(item.at_utc, details.timezone)}</td><td>{PURPOSE[item.purpose] ?? item.purpose}</td>
            <td>{item.provider_id} · {item.model}</td>
            <td>{{ succeeded: "成功", failed: "失败", cancelled: "已取消", in_progress: "尚未结束" }[item.state] ?? item.state}
              {item.error_code && <div className="small muted">{item.error_code}</div>}
              {incomplete && <div className="small muted">{partial ? "仅返回部分用量" : "用量未完整返回"}</div>}</td>
            <td>{item.cost ? money(item.cost.amount, item.cost.currency) : "未知"}</td>
          </tr>
        })}</tbody>
      </table>{details.items.length === 0 && <p className="sub">这段时间还没有模型调用。</p>}</div>
    </>}
    {detailError && <div role="alert">{detailError}<Button small onClick={details ? () => void loadMore() : retry}>重试</Button></div>}
    {busy && <Loading label="正在读取逐笔费用…" />}
    {details?.next_offset != null && <Button busy={busy} onClick={() => void loadMore()}>加载更多</Button>}
  </section>
}

function VendorBalance() {
  const [data, setData] = React.useState<BalanceResult | null>(null)
  const [error, setError] = React.useState("")
  const [busy, setBusy] = React.useState(false)
  const request = React.useRef(0)
  const load = React.useCallback(async (refresh = false) => {
    const own = ++request.current
    setBusy(true)
    try {
      const result = await call<BalanceResult>("usage.spend", { refresh })
      if (own === request.current) { setData(result); setError("") }
    } catch { if (own === request.current) setError("厂商账户余额暂时无法读取。") }
    finally { if (own === request.current) setBusy(false) }
  }, [])
  React.useEffect(() => {
    void load(true)
    const off = on("balance_spend", () => void load())
    return () => { ++request.current; off() }
  }, [load])
  const spend = data?.spend
  return <section className="card stack" aria-label="厂商账户余额" style={{ padding: 20 }}>
    <div className="row between"><h3>厂商账户余额{data?.preset ? ` · ${data.preset.display_name}` : ""}</h3><Button small busy={busy} onClick={() => void load(true)}>刷新余额</Button></div>
    <p className="sub">整个厂商账户的余额，可能包括其他工具或其他 Key 的消费。</p>
    {spend?.balance ? <div>{spend.balance.balances.map((value: { amount: string; currency: string }) => <span key={value.currency} style={{ marginRight: 16 }}>{money(value.amount, value.currency)}</span>)}
      <p className="small muted">更新于 {localTime(spend.balance.updated_at, Intl.DateTimeFormat().resolvedOptions().timeZone)}</p></div>
      : !busy && <p className="sub">{spend?.supported === false ? "此预设暂不支持查询账户余额。" : "尚未查到厂商账户余额。"}</p>}
    {busy && <Loading label="正在查询厂商账户余额…" />}
    {(error || spend?.query_failed || spend?.unavailable) && <p role="status" className="sub">{error || (spend?.balance ? "本次余额查询未成功，显示上次保存的余额。" : "本次余额查询未成功，请重试。")}</p>}
  </section>
}
