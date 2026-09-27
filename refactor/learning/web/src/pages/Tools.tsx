import { ArrowRight, MessagesSquare, Swords } from "lucide-react"
import { useApp } from "../store"

export function Tools() {
  const go = useApp((s) => s.go)
  return (
    <div className="page">
      <h2 style={{ margin: "8px 0 4px", fontSize: "1.5rem" }}>更多工具</h2>
      <span className="muted">不占用学习计划的时间，随时可用。</span>
      <div className="grid two stagger" style={{ marginTop: 22 }}>
        <button className="card hover tool-card" onClick={() => go("chat")}>
          <div className="ico"><MessagesSquare /></div>
          <div className="grow">
            <b>教师闲聊</b>
            <p>和老师聊任何话题。没有任何系统限制，人设由你自己写，不进入课程。</p>
          </div>
          <ArrowRight className="go" />
        </button>
        <div className="card tool-card off">
          <div className="ico"><Swords /></div>
          <div className="grow">
            <b>学习远征</b>
            <p>用闯关的方式巩固学过的内容。</p>
          </div>
          <span className="pill muted">即将开放</span>
        </div>
      </div>
    </div>
  )
}
