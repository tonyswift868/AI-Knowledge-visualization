import * as React from "react"
export const TeacherStage = React.forwardRef(function Fixture(_props, ref) {
  React.useImperativeHandle(ref, () => ({ setMouthLevel() {}, nod() {}, think() {} }))
  return <p className="muted">老师形象 · 离线占位</p>
})
export const useVoiceInput = () => ({})
export const MicButton = () => <button disabled className="icon-btn" aria-label="离线麦克风">🎙</button>
