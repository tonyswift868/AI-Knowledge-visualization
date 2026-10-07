import * as React from "react"
import { ArrowRight, ClipboardList, MessagesSquare } from "lucide-react"
import { useApp } from "../store"
import { SelfTest } from "./SelfTest"

export function Tools() {
  const go = useApp((s) => s.go)
  const [selfTest, setSelfTest] = React.useState(false)
  if (selfTest) return <SelfTest back={() => setSelfTest(false)} />
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
        <button className="card hover tool-card" onClick={() => setSelfTest(true)}>
          <div className="ico"><ClipboardList /></div>
          <div className="grow"><b>上传题目自测</b><p>上传或粘贴自己的题，按原答案练习，连续答对 2 遍或 3 遍才过。</p></div>
          <ArrowRight className="go" />
        </button>
        {/* 学习远征暂不开发，入口先藏起来（UI-02）；后端代码保留不动 */}
      </div>
    </div>
  )
}
