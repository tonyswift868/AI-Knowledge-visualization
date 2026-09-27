import * as React from "react"
import { ArrowLeft, BookOpen, ExternalLink } from "lucide-react"
import { call } from "../bridge"
import { useApp } from "../store"
import { Button, Skeleton, TASK_TIME_TIP, Tip } from "../ui"
import { PlanTree } from "./NewPlan"

type Detail = React.ComponentProps<typeof PlanTree>["detail"]

export function PlanDetail() {
  const planId = useApp((s) => s.planId)
  const go = useApp((s) => s.go)
  const toast = useApp((s) => s.toast)
  const [detail, setDetail] = React.useState<Detail | null>(null)

  React.useEffect(() => {
    if (!planId) { go("plans"); return }
    call<Detail>("plan.detail", { plan_id: planId }).then(setDetail).catch((e) => { toast("error", e.message); go("plans") })
  }, [planId, go, toast])

  if (!detail) return <div className="page"><div className="card stack"><Skeleton h={26} w="40%" /><Skeleton h={120} /></div></div>
  const points = detail.stages.reduce((a, s) => a + s.modules.reduce((b, m) => b + m.points.length, 0), 0)
  return (
    <div className="page">
      <Button small kind="ghost" onClick={() => go("plans")}><ArrowLeft />我的计划</Button>
      <div className="card hero" style={{ marginTop: 10, gridTemplateColumns: "1fr" }}>
        <div>
          <div className="eyebrow">{detail.plan.window ? `${detail.plan.window.start} → ${detail.plan.window.end}` : "学习路线"}</div>
          <h1 style={{ fontSize: "1.6rem" }}>{detail.plan.goal}</h1>
          <div className="outline-sum" style={{ marginTop: 14 }}>
            <div><span>模块</span><b>{detail.stages.reduce((a, s) => a + s.modules.length, 0)} 个 · {points} 个知识点</b></div>
            <div><span>总时长（含检验）</span><b>{Math.round(detail.plan.total_minutes / 6) / 10} 小时</b></div>
            <div><span>每日任务时长<Tip text={TASK_TIME_TIP} /></span><b>{detail.daily ? `${detail.daily.full_day} 分钟` : "—"}</b></div>
          </div>
        </div>
      </div>
      <div className="section-title"><h2>路线</h2><span>点模块名展开知识点；学习开始后这里会显示每个点的掌握情况</span></div>
      <div className="card"><PlanTree detail={detail} /></div>
      <div className="section-title"><h2>资料</h2><span>{detail.sources.length} 份</span></div>
      <div className="card stack">
        {detail.sources.map((s) => (
          <div key={s.id} className="row between">
            <span className="row" style={{ minWidth: 0 }}><BookOpen size={16} /><span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.title}</span></span>
            {s.url && <a className="btn small ghost" href={s.url} target="_blank" rel="noreferrer"><ExternalLink />打开</a>}
          </div>
        ))}
      </div>
    </div>
  )
}
