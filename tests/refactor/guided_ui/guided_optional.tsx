import * as React from "react"
// Explicit doubles for hardware, not the Classroom, Markdown or voice hook.
export const TeacherStage = React.forwardRef(function FixtureTeacher(_props, ref) {
  React.useImperativeHandle(ref, () => ({ setMouthLevel() {}, nod() {}, think() {} }))
  return <div style={{ display: "grid", placeContent: "center", textAlign: "center", color: "var(--text-3)", gap: 10 }}>
    <span style={{ fontSize: "2rem" }}>MentorFlow</span><span>教师形象 · 离线测试占位</span>
  </div>
})
export const useVoiceInput = () => ({})
export const MicButton = () => <button className="icon-btn" disabled title="假麦克风：不采集音频" aria-label="麦克风（离线）">🎙</button>
