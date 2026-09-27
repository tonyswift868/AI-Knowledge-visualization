import { MessagesSquare, Swords } from "lucide-react"

export function Tools() {
  return (
    <div className="page">
      <h2 style={{ margin: "8px 0 4px", fontSize: "1.5rem" }}>更多工具</h2>
      <span className="muted">不占用学习计划的时间，随时可用。</span>
      <div className="grid two stagger" style={{ marginTop: 22 }}>
        <div className="card hover stack">
          <div className="row"><div className="choice" style={{ padding: 0, border: 0, background: "none", cursor: "default" }}>
            <div className="ico"><MessagesSquare /></div>
            <div><b>教师闲聊</b><p>和老师聊任何话题，人设由你自己写，不进入教学流程。</p></div>
          </div></div>
          <span className="pill muted" style={{ justifySelf: "start" }}>即将开放</span>
        </div>
        <div className="card hover stack">
          <div className="row"><div className="choice" style={{ padding: 0, border: 0, background: "none", cursor: "default" }}>
            <div className="ico"><Swords /></div>
            <div><b>学习远征</b><p>用闯关的方式巩固学过的内容。</p></div>
          </div></div>
          <span className="pill muted" style={{ justifySelf: "start" }}>即将开放</span>
        </div>
      </div>
    </div>
  )
}
