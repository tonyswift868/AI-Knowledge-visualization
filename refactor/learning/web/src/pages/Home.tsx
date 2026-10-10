import * as React from "react"
import { CalendarClock, PlayCircle, Plus, Sparkles } from "lucide-react"
import { call, on } from "../bridge"
import { useApp } from "../store"
import { Button, SakuraArt } from "../ui"
import { ProgressStatus, aggregateProgress } from "../ProgressStatus"
import type { PlanItem } from "./Plans"
import "./home.css"
import { Loading } from '../loading/Loading'

function greeting(): string {
  const h = new Date().getHours()
  if (h < 5) return "夜深了"
  if (h < 11) return "早上好"
  if (h < 14) return "中午好"
  if (h < 18) return "下午好"
  return "晚上好"
}

function minutes(n: number) {
  if (n < 60) return `${n} 分钟`
  const h = Math.floor(n / 60)
  const m = n % 60
  return m ? `${h} 小时 ${m} 分` : `${h} 小时`
}

export function Home() {
  const account = useApp((s) => s.account)
  const go = useApp((s) => s.go)
  const ready = useApp((s) => s.ready)
  const [plans, setPlans] = React.useState<PlanItem[] | null>(null)
  const [loadError, setLoadError] = React.useState(false)
  const [studyDay, setStudyDay] = React.useState("")
  const [loading, setLoading] = React.useState(true)
  const loadRevision = React.useRef(0)

  const load = React.useCallback(() => {
    const revision = ++loadRevision.current
    setLoading(true)
    call<{ plans: PlanItem[]; study_day: string }>("plans.list").then((r) => {
      if (revision !== loadRevision.current) return
      setPlans(r.plans.filter((p) => !p.archived))
      setStudyDay(r.study_day)
      setLoadError(false)
    }).catch(() => {if(revision===loadRevision.current)setLoadError(true)}).finally(() => {if(revision===loadRevision.current)setLoading(false)})
  }, [])
  React.useEffect(() => { if (ready) load() }, [ready, load])
  React.useEffect(() => on("study_day", () => load()), [load])   // the refresh time passed while open
  React.useEffect(() => on("plans_changed", () => { if (ready) load() }), [ready, load])

  const running = (plans ?? []).filter((p) => p.today)
  const total = aggregateProgress(running.map(p => p.today!))
  const date = new Date()
  const dateText = `${date.getMonth() + 1} 月 ${date.getDate()} 日 · 周${"日一二三四五六"[date.getDay()]}`

  return (
    <div className="page home-page">
      <div className="card hero home-hero">
        <div>
          <div className="eyebrow">{dateText}</div>
          <h1>{greeting()}，<em>{account?.name ?? "同学"}</em></h1>
          <p>{running.length
            ? `今天有 ${running.length} 份计划在进行。按自己的节奏来，学会一点就记下一点。`
            : "从一个想学的东西开始：告诉我你的目标，我会帮你找资料、搭好路线，再陪你一节一节学完。"}</p>
          <div className="row wrap" style={{ marginTop: 18 }}>
            <Button kind="primary" onClick={() => go("new-plan")}><Plus />新建计划</Button>
            <Button onClick={() => go("plans")}>查看全部计划</Button>
          </div>
        </div>
        <div className="home-total">
          <span className="small muted">今天全部计划</span>
          <ProgressStatus value={total} />
        </div>
      </div>

      <div className="section-title">
        <h2>今天</h2>
        <span>{studyDay ? `学习日 ${studyDay}` : ""}</span>
      </div>

      {loadError && <div className="card" role="alert">
        读取失败。{plans !== null ? "现有列表可能不是最新。" : "请重试读取计划。"}
        <Button small onClick={load}>重试</Button>
      </div>}

      {plans === null && loadError ? null : plans === null ? (
        <Loading shape="cards" label={ready ? "正在读取今天的学习安排…" : "正在连接账号服务…"} onRetry={load}/>
      ) : running.length === 0 ? (
        <div className="card empty fade-in">
          <SakuraArt />
          <h3>今天还没有要学的内容</h3>
          <p>{plans.length ? "你的计划还没有开始。进入计划，确认路线后点“开始计划”，这里就会出现每天的学习目标。"
                            : "还没有学习计划。新建一个计划，AI 会根据你的目标和时间，把每天要学多久安排好。"}</p>
          <Button kind="primary" onClick={() => go(plans.length ? "plans" : "new-plan")}>
            {plans.length ? <><CalendarClock />去看我的计划</> : <><Sparkles />开始第一个计划</>}
          </Button>
        </div>
      ) : (
        <div className="home-plan-grid stagger">
          {running.map((p) => <TodayCard key={p.id} plan={p} />)}
        </div>
      )}
      {plans !== null && <Loading active={loading} label="正在更新今天的学习安排…"/>}
    </div>
  )
}

function TodayCard({ plan }: { plan: PlanItem }) {
  const t = plan.today!
  const go = useApp((s) => s.go)
  const set = useApp((s) => s.set)
  const title = plan.title || plan.goal || "未命名计划"
  return (
    <div className="card hover today-plan">
      <div className="today-plan-header">
        <b className="today-plan-title" title={title}>{title}</b>
        {plan.state === "checking" ? <span className="pill">检验中</span> : null}
      </div>
      <div className="today-plan-progress">
        <ProgressStatus value={t} />
      </div>
      {t.needs_negotiation && (
        <div className="negotiate small">
          <span>已经欠了 {minutes(t.debt_minutes)}，超过两天的量了。是不是每天安排太紧？可以推迟截止日期或调整每天的时长。</span>
          <Button small onClick={() => { set({ planId: plan.id }); go("plan") }}>调整安排</Button>
        </div>
      )}
      <Button className="today-plan-start" kind="primary" onClick={() => { set({ planId: plan.id }); go("classroom") }}><PlayCircle />开课</Button>
    </div>
  )
}
