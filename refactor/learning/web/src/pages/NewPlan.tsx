import { ClipboardList, GitBranch, Search, Wand2 } from "lucide-react"
import { useApp } from "../store"
import { Button } from "../ui"

const STEPS = [
  { icon: ClipboardList, title: "说清楚想学什么", text: "填一张表：目标、起止日期、每天最多学多久、哪几天有空、现在的水平。" },
  { icon: Search, title: "找权威资料", text: "AI 用模型自带的搜索找资料，逐个打开核实，你来决定用哪些。" },
  { icon: GitBranch, title: "先看路线轮廓", text: "阶段和模块排成一棵树，满意了再生成；随时在聊天里让 AI 调整。" },
  { icon: Wand2, title: "正式生成", text: "从资料里提炼每个知识点、估好时长，排进你每天的学习时间。" },
]

export function NewPlan() {
  const go = useApp((s) => s.go)
  return (
    <div className="page">
      <div className="card hero" style={{ gridTemplateColumns: "1fr" }}>
        <div>
          <div className="eyebrow">新建计划 · 即将开放</div>
          <h1>把一个目标，变成<em>每天学得完的路线</em></h1>
          <p>这一部分正在开发中，下一步开放。先去“设置 → 模型连接”确认模型已连好——找资料和生成路线都会用到它。</p>
          <div className="steps stagger">
            {STEPS.map((s, i) => (
              <div key={s.title} className="step">
                <div className="num">{i + 1}</div>
                <b><s.icon size={15} style={{ verticalAlign: -2, marginRight: 6 }} />{s.title}</b>
                <p>{s.text}</p>
              </div>
            ))}
          </div>
          <div className="row" style={{ marginTop: 20 }}>
            <Button kind="primary" onClick={() => go("settings", "models")}>去确认模型连接</Button>
          </div>
        </div>
      </div>
    </div>
  )
}
