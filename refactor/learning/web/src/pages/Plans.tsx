import * as React from "react"
import { Archive, BookOpenCheck, PencilLine, Plus } from "lucide-react"
import { call } from "../bridge"
import { useApp } from "../store"
import { Button, SakuraArt, Segmented, Skeleton } from "../ui"

export type LedgerToday = {
  today_target: number; today_confirmed: number; today_tentative: number; debt_minutes: number; needs_negotiation: boolean
  ahead_minutes: number; ahead_days: number; remaining_minutes: number; overdue_days: number; completed: boolean
}
export type PlanItem = {
  id: string; goal: string; final_goal: string; state: string; archived: boolean; module_count: number
  window: { start: string; end: string } | null; total_minutes: number; today: LedgerToday | null
}

const STATE_LABEL: Record<string, [string, string]> = {
  creating: ["创建中", "muted"], ready: ["待开始", ""], active: ["进行中", "ok"], checking: ["检验中", ""], completed: ["已完成", "ok"],
}

export function Plans() {
  const ready = useApp((s) => s.ready)
  const go = useApp((s) => s.go)
  const set = useApp((s) => s.set)
  const [filter, setFilter] = React.useState<"current" | "all" | "archived">("current")
  const [plans, setPlans] = React.useState<PlanItem[] | null>(null)

  React.useEffect(() => {
    if (!ready) return
    call<{ plans: PlanItem[] }>("plans.list", { include_archived: true }).then((r) => setPlans(r.plans)).catch(() => setPlans([]))
  }, [ready])

  const shown = (plans ?? []).filter((p) =>
    filter === "archived" ? p.archived : filter === "current" ? !p.archived && p.state !== "completed" : !p.archived)

  return (
    <div className="page">
      <div className="row between" style={{ marginTop: 8 }}>
        <div>
          <h2 style={{ margin: "0 0 4px", fontSize: "1.5rem" }}>我的计划</h2>
          <span className="muted">每个计划都有自己的路线、进度和学习画像。</span>
        </div>
        <div className="row">
          <Segmented value={filter} onChange={setFilter}
                     options={[{ value: "current", label: "进行中" }, { value: "all", label: "全部" }, { value: "archived", label: "已归档" }]} />
          <Button kind="primary" onClick={() => go("new-plan")}><Plus />新建计划</Button>
        </div>
      </div>

      <div style={{ marginTop: 22 }}>
        {plans === null ? (
          <div className="grid two">{[0, 1].map((i) => <div key={i} className="card stack"><Skeleton h={22} w="50%" /><Skeleton h={14} /><Skeleton h={14} w="70%" /></div>)}</div>
        ) : shown.length === 0 ? (
          <div className="card empty fade-in">
            <SakuraArt />
            <h3>{filter === "archived" ? "没有归档的计划" : "这里还没有计划"}</h3>
            <p>{filter === "archived" ? "归档的计划可以随时还原，不会丢失进度。" : "新建计划后，路线图、每天的进度和学习报告都会出现在这里。"}</p>
            {filter !== "archived" && <Button kind="primary" onClick={() => go("new-plan")}><Plus />新建计划</Button>}
          </div>
        ) : (
          <div className="grid two stagger">
            {shown.map((p) => {
              const [label, tone] = STATE_LABEL[p.state] ?? [p.state, "muted"]
              return (
                <div key={p.id} className="card hover stack">
                  <div className="row between">
                    <b style={{ fontSize: "1.05rem" }}>{p.goal || "未命名计划"}</b>
                    <span className={`pill ${tone}`}>{p.archived ? <><Archive size={12} />已归档</> : label}</span>
                  </div>
                  {p.final_goal && <span className="muted small">最终目标：{p.final_goal}</span>}
                  <div className="row wrap small muted">
                    {p.window && <span>{p.window.start} → {p.window.end}</span>}
                    <span>· {p.module_count} 个模块</span>
                    {p.total_minutes > 0 && <span>· 共约 {Math.round(p.total_minutes / 60)} 小时</span>}
                  </div>
                  <div className="row">
                    {p.state === "creating" || p.state === "ready"
                      ? <Button small kind="primary" onClick={() => { set({ planId: p.id }); go("new-plan") }}><PencilLine />继续创建</Button>
                      : <Button small onClick={() => { set({ planId: p.id }); go("plan") }}><BookOpenCheck />查看路线</Button>}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
