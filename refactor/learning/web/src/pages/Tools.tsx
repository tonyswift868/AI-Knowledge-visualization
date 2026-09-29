import { ArrowRight, MessagesSquare } from "lucide-react"
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
        {/* 学习远征暂不开发，入口先藏起来（UI-02）；后端代码保留不动 */}
      </div>
    </div>
  )
}
