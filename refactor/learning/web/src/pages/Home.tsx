import * as React from "react"
import { CalendarClock, Flame, PlayCircle, Plus, Sparkles, TrendingUp } from "lucide-react"
import { call, on } from "../bridge"
import { useApp } from "../store"
import { Button, Ring, SakuraArt, Skeleton } from "../ui"
import type { PlanItem } from "./Plans"

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
  const [studyDay, setStudyDay] = React.useState("")

  const load = React.useCallback(() => {
    call<{ plans: PlanItem[]; study_day: string }>("plans.list").then((r) => {
      setPlans(r.plans.filter((p) => !p.archived))
      setStudyDay(r.study_day)
    }).catch(() => setPlans([]))
  }, [])
  React.useEffect(() => { if (ready) load() }, [ready, load])
  React.useEffect(() => on("study_day", () => load()), [load])   // the refresh time passed while open

  const running = (plans ?? []).filter((p) => p.today)
  const target = running.reduce((a, p) => a + (p.today?.today_target ?? 0), 0)
  const done = running.reduce((a, p) => a + (p.today?.today_confirmed ?? 0), 0)
  const tentative = running.reduce((a, p) => a + (p.today?.today_tentative ?? 0), 0)
  const date = new Date()
  const dateText = `${date.getMonth() + 1} 月 ${date.getDate()} 日 · 周${"日一二三四五六"[date.getDay()]}`

  return (
    <div className="page">
      <div className="card hero">
        <div>
          <div className="eyebrow">{dateText}</div>
          <h1>{greeting()}，<em>{account?.name ?? "同学"}</em></h1>
          <p>{running.length
            ? `今天有 ${running.length} 份计划在进行。按自己的节奏来，学会一点就记下一点。`
            : "从一个想学的东西开始：告诉我你的目标，我会帮你找资料、搭好路线，再陪你一节一节学完。"}</p>
          <div className="row" style={{ marginTop: 18 }}>
            <Button kind="primary" onClick={() => go("new-plan")}><Plus />新建计划</Button>
            <Button onClick={() => go("plans")}>查看全部计划</Button>
          </div>
        </div>
        <Ring value={done} total={target || 1} tentative={tentative} size={150} stroke={13}>
          {target ? <><b>{done}</b><span>/ {target} 分钟</span></> : <><b>—</b><span>今日目标</span></>}
        </Ring>
      </div>

      <div className="section-title">
        <h2>今天</h2>
        <span>{studyDay ? `学习日 ${studyDay}` : ""}</span>
      </div>

      {plans === null ? (
        <div className="grid three">
          {[0, 1, 2].map((i) => <div key={i} className="card stack"><Skeleton h={22} w="60%" /><Skeleton h={90} /><Skeleton h={16} w="40%" /></div>)}
        </div>
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
        <div className="grid three stagger">
          {running.map((p) => <TodayCard key={p.id} plan={p} />)}
        </div>
      )}
    </div>
  )
}

function TodayCard({ plan }: { plan: PlanItem }) {
  const t = plan.today!
  const go = useApp((s) => s.go)
  const set = useApp((s) => s.set)
  return (
    <div className="card hover stack">
      <div className="row between">
        <b style={{ fontSize: "1.02rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{plan.title || plan.goal || "未命名计划"}</b>
        {t.overdue_days ? <span className="pill warn">逾期 {t.overdue_days} 天</span> : plan.state === "checking" ? <span className="pill">检验中</span> : null}
      </div>
      <div className="row" style={{ gap: 18 }}>
        <Ring value={t.today_confirmed} total={t.today_target || 1} tentative={t.today_tentative} size={104} stroke={10}>
          {t.today_target ? <><b>{t.today_confirmed}</b><span>/ {t.today_target} 分</span></> : <><b>休</b><span>今天</span></>}
        </Ring>
        <div className="stack" style={{ gap: 8 }}>
          {t.debt_minutes > 0 && <span className="pill warn"><Flame size={13} />欠 {minutes(t.debt_minutes)}</span>}
          {t.ahead_minutes > 0 && <span className="pill ok"><TrendingUp size={13} />超前 {t.ahead_days ? `${t.ahead_days} 天` : minutes(t.ahead_minutes)}</span>}
          {t.today_tentative > 0 && <span className="pill muted">暂记 {t.today_tentative} 分</span>}
          {!t.debt_minutes && !t.ahead_minutes && <span className="muted small">{t.today_target ? "按计划进行" : "今天是休息日，不用学"}</span>}
        </div>
      </div>
      {t.needs_negotiation && (
        <div className="negotiate small">
          <span>已经欠了 {minutes(t.debt_minutes)}，超过两天的量了。是不是每天安排太紧？可以推迟截止日期或调整每天的时长。</span>
          <Button small onClick={() => { set({ planId: plan.id }); go("plan") }}>调整安排</Button>
        </div>
      )}
      <Button kind="primary" onClick={() => { set({ planId: plan.id }); go("classroom") }}><PlayCircle />开课</Button>
    </div>
  )
}
