import * as React from 'react'
export const TeacherWindow = React.forwardRef(function OfflineWindow(props: { children: React.ReactNode }, ref) {
  React.useImperativeHandle(ref, () => ({ setMouthLevel() {}, nod() {}, think() {} }))
  return <aside aria-label="离线老师栏" style={{ width: 32, borderRadius: 16, border: '1px solid var(--border)', display: 'flex', justifyContent: 'center', paddingTop: 18 }}>
    <span className="muted small" style={{ writingMode: 'vertical-rl' }}>老师栏 · 离线占位</span>
    <div style={{ display: 'none' }}>{props.children}</div>
  </aside>
})
export const useVoiceInput = () => ({})
export const MicButton = () => <button disabled className="icon-btn" aria-label="离线麦克风">🎙</button>
