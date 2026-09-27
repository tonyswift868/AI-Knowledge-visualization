/** Hidden development route only: #/dev/teacher. Never enters lesson flow. */
import * as React from "react"
import type { TeacherStageHandle } from "./TeacherStage"
import type { Capabilities, Reaction, ReactionEvent, StageSnapshot } from "./teacherRuntime"
import "./teacher-stage.css"

const TeacherStage = React.lazy(() => import("./TeacherStage").then((m) => ({ default: m.TeacherStage })))

const LABELS: Record<Reaction, string> = { nod: "点头", shake: "轻轻摇头", think: "思考", happy: "开心" }

export function TeacherStagePreview() {
  const teacher = React.useRef<TeacherStageHandle>(null)
  const [mouth, setMouth] = React.useState(0)
  const [paused, setPaused] = React.useState(false)
  const [visible, setVisible] = React.useState(true)
  const [mounted, setMounted] = React.useState(true)
  const [caps, setCaps] = React.useState<Capabilities | null>(null)
  const [snapshot, setSnapshot] = React.useState<StageSnapshot | null>(null)
  const [reaction, setReaction] = React.useState<ReactionEvent | null>(null)
  const [state, setState] = React.useState("准备中")
  const events = React.useRef<ReactionEvent[]>([])
  React.useEffect(() => {
    const timer = window.setInterval(() => setSnapshot(teacher.current?.snapshot() ?? null), 1000)
    // Read-only renderer inspection plus explicit input for the Qt smoke test.
    // This object exists only on the hidden preview page, never a lesson page.
    ;(window as any).__mfTeacherPreview = { snapshot: () => teacher.current?.snapshot(),
      mouthSample: (v: number) => teacher.current?.setMouthLevel(v), events: events.current }
    return () => { window.clearInterval(timer); delete (window as any).__mfTeacherPreview }
  }, [])
  const reacting = (event: ReactionEvent) => { events.current.push(event); setReaction(event) }
  const statusChanged = (status: string, message: string) => {
    setState(message)
    if (status !== "ready") { setCaps(null); setSnapshot(null); setReaction(null) }
  }
  return <div className="teacher-preview">
    <header className="teacher-preview-header"><div><span className="teacher-preview-kicker">TEACHER STUDIO · 开发预览</span>
      <h1>和椿打个招呼</h1><p>检查形象、朗读嘴型与自然反应。这里不会开始课堂。</p></div>
      <span className="chip" data-preview-state>{state}</span></header>
    <div className="teacher-preview-grid">
      <div className="teacher-preview-scene">
        {mounted && <div style={{ width: "100%", height: "100%", visibility: visible ? "visible" : "hidden" }}>
          <React.Suspense fallback={<p>正在准备老师形象…</p>}><TeacherStage ref={teacher} mouthLevel={mouth} paused={paused} visible={visible} onReady={setCaps}
            onStatus={statusChanged} onReaction={reacting} /></React.Suspense>
        </div>}
        <span className="teacher-preview-name">椿 · {paused ? "已暂停" : "本地 Live2D"}</span>
      </div>
      <section className="teacher-preview-panel"><h2>老师的声音与表情</h2><p>待机只保留轻微呼吸与眨眼。反应由你触发，结束后回到自然站姿。</p>
        <fieldset><legend>朗读嘴型 <output className="teacher-preview-value">{mouth.toFixed(2)}</output></legend>
          <input aria-label="嘴型开合" type="range" min="0" max="1" step="0.01" disabled={!caps?.mouth || !mounted} value={mouth} onChange={(e) => setMouth(Number(e.target.value))} />
          <p>拖动滑块检查开合。课堂接入后，数值来自正在播放的朗读音频。</p>
          <div className="teacher-preview-row"><button data-mouth="closed" disabled={!caps?.mouth || !mounted} onClick={() => setMouth(0)}>闭嘴 · 0</button><button data-mouth="open" disabled={!caps?.mouth || !mounted} onClick={() => setMouth(.8)}>张嘴 · 0.8</button></div>
        </fieldset>
        <fieldset><legend>短反应</legend><div className="teacher-preview-row">
          {(Object.keys(LABELS) as Reaction[]).map((name) => <button key={name} data-reaction={name} disabled={!caps?.reactions[name] || paused || !visible || !mounted}
            title={caps && !caps.reactions[name] ? "模型缺少对应参数，接口暂不可用" : undefined} onClick={() => teacher.current?.[name]()}>{LABELS[name]}</button>)}
        </div><p className="teacher-preview-event" aria-live="polite">{reaction ? `${LABELS[reaction.reaction]} · ${{ started: "进行中", finished: "已完成", cancelled: "已停止" }[reaction.phase]}` : "选择一个反应，看看老师的变化。"}</p></fieldset>
        <fieldset><legend>渲染检查</legend><div className="teacher-preview-row">
          <button data-toggle="pause" onClick={() => setPaused(!paused)}>{paused ? "继续渲染" : "暂停渲染"}</button>
          <button data-toggle="visible" onClick={() => setVisible(!visible)}>{visible ? "隐藏形象" : "显示形象"}</button>
          <button data-toggle="mount" onClick={() => setMounted(!mounted)}>{mounted ? "卸载组件" : "重新加载"}</button>
        </div><div className="teacher-preview-stats"><span>{snapshot?.active ? "渲染中" : "渲染已停止"}</span><span>{snapshot?.fps.toFixed(1) ?? "—"} FPS</span><span>{snapshot?.frames ?? 0} 帧</span></div></fieldset>
      </section>
    </div>
  </div>
}
