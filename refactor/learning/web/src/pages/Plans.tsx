import * as React from "react"
import { Archive, ArchiveRestore, BookOpenCheck, Check, PencilLine, PlayCircle, Plus, Trash2, X } from "lucide-react"
import { call } from "../bridge"
import { useApp } from "../store"
import { Button, SakuraArt, Segmented } from "../ui"
import type { StudyProgress } from "../ProgressStatus"
import { Loading } from '../loading/Loading'

export type LedgerToday = StudyProgress & {
  today_target: number; today_confirmed: number; today_tentative: number; debt_minutes: number; needs_negotiation: boolean
  ahead_minutes: number; ahead_days: number; remaining_minutes: number; overdue_days: number; completed: boolean
}
export type PlanItem = {
  id: string; title: string; goal: string; final_goal: string; state: string; archived: boolean; module_count: number
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
  const [loadError, setLoadError] = React.useState(false)
  const [loading, setLoading] = React.useState(true)
  const revision = React.useRef(0)
  const reload = React.useCallback(() => {
    const own = ++revision.current
    setLoading(true)
    call<{ plans: PlanItem[] }>("plans.list", { include_archived: true }).then((r) => {
      if(own!==revision.current)return
      setPlans(r.plans); setLoadError(false)
    }).catch(() => {if(own===revision.current)setLoadError(true)}).finally(()=>{if(own===revision.current)setLoading(false)})
  }, [])

  React.useEffect(() => { if (ready) reload() }, [ready, reload])

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
        {loadError && <div className="card" role="alert">
          读取失败。{plans !== null ? "现有列表可能不是最新。" : "请重试读取计划。"}
          <Button small onClick={reload}>重试</Button>
        </div>}
        {plans === null && loadError ? null : plans === null ? (
          <Loading shape="cards" label="正在读取我的计划…" onRetry={reload}/>
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
                    <b style={{ fontSize: "1.05rem" }}>{p.title || p.goal || "未命名计划"}</b>
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
                      : <>
                        {(p.state === "active" || p.state === "checking") && !p.archived &&
                          <Button small kind="primary" onClick={() => { set({ planId: p.id }); go("classroom") }}><PlayCircle />开课</Button>}
                        <Button small onClick={() => { set({ planId: p.id }); go("plan") }}><BookOpenCheck />查看路线</Button>
                      </>}
                    <PlanMenu plan={p} onChanged={reload} />
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
      {plans !== null && <Loading active={loading} label="正在更新我的计划…"/>}
    </div>
  )
}

/** PLAN-08: rename (display name only), archive / restore, delete with a second click. */
function PlanMenu({ plan, onChanged }: { plan: PlanItem; onChanged: () => void }) {
  const toast = useApp((s) => s.toast)
  const [editing, setEditing] = React.useState(false)
  const [title, setTitle] = React.useState(plan.title || plan.goal)
  const [confirm, setConfirm] = React.useState(false)
  React.useEffect(() => {
    if (!confirm) return
    const t = setTimeout(() => setConfirm(false), 3000)
    return () => clearTimeout(t)
  }, [confirm])
  async function run(method: string, params: Record<string, unknown>, done: string) {
    try { await call(method, { plan_id: plan.id, ...params }); toast("ok", done); onChanged() }
    catch (e) { toast("error", (e as Error).message) }
  }
  if (editing) return (
    <div className="row" style={{ gap: 6, marginLeft: "auto" }}>
      <input className="input" style={{ width: 200 }} value={title} maxLength={80} autoFocus onChange={(e) => setTitle(e.target.value)}
             onKeyDown={(e) => { if (e.key === "Enter" && title.trim()) { setEditing(false); void run("plan.rename", { title }, "已改名。") } }} />
      <button className="icon-btn sm" title="保存" disabled={!title.trim()}
              onClick={() => { setEditing(false); void run("plan.rename", { title }, "已改名。") }}><Check /></button>
      <button className="icon-btn sm" title="取消" onClick={() => setEditing(false)}><X /></button>
    </div>
  )
  return (
    <div className="row" style={{ gap: 4, marginLeft: "auto" }}>
      <button className="icon-btn sm" title="改名" onClick={() => setEditing(true)}><PencilLine /></button>
      {plan.archived
        ? <button className="icon-btn sm" title="还原" onClick={() => run("plan.archive", { archived: false }, "已还原。")}><ArchiveRestore /></button>
        : <button className="icon-btn sm" title="归档（进度保留，可随时还原）" onClick={() => run("plan.archive", { archived: true }, "已归档，进度都保留。")}><Archive /></button>}
      <button className={`icon-btn sm ${confirm ? "danger" : ""}`} title={confirm ? "再点一次删除（计划、进度和报告都会删除）" : "删除"}
              onClick={() => (confirm ? run(plan.state === 'creating' || plan.state === 'ready' ? "plan.discard" : "plan.delete", {}, "已删除这份计划。") : setConfirm(true))}>
        <Trash2 />{confirm && <span className="small">确认删除</span>}</button>
    </div>
  )
}
