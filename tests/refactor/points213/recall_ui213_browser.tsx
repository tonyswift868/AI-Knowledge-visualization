import * as React from "react"
import { createRoot, Root } from "react-dom/client"
import { RecallQuestion } from "../../../refactor/learning/web/src/classroom/RecallQuestion"
import { call, calls, groupedPoints, oldSnapshot, resetCalls, verdictForHarness } from "./recall_ui213_bridge"

const host = document.getElementById("root")!
let root: Root
function groupedQuestion() {
  return {id:"question-213", kind:"recall", stem:"说明分片键的特点，以及选择时需要考虑什么。",
    options:[], purpose:"最终检验", number:1, total:1, revealed:false, key_points:[]}
}
function render(question:any) {
  if (root) root.unmount()
  root = createRoot(host)
  root.render(<RecallQuestion question={question} request={call} busy={false} next={() => {
    (window as any).recall213.nextCount++
  }} />)
}

resetCalls()
render(groupedQuestion())
;(window as any).recall213 = {
  calls, groupedPoints, oldSnapshot, control:{nextCount:0},
  restoreLegacy() {
    resetCalls()
    render({id:"question-213", kind:"recall", stem:"旧题快照恢复演示。", options:[], purpose:"最终检验",
      number:1, total:1, revealed:true, answer:"我已作答", reference:"旧时显示的参考答案。",
      key_points:oldSnapshot, ticked:[4]})
  },
  verdictForHarness,
}
