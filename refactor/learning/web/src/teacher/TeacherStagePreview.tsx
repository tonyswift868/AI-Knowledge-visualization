/** Hidden development route only: #/dev/teacher. Never enters lesson flow. */
import * as React from "react"
import type { TeacherStageHandle } from "./TeacherStage"
import type { Capabilities, Reaction, ReactionEvent, StageSnapshot } from "./teacherRuntime"
import { useTeacherVoice } from "./useTeacherVoice"
import "./teacher-stage.css"

const TeacherStage = React.lazy(() => import("./TeacherStage").then((m) => ({ default: m.TeacherStage })))

const LABELS: Record<Reaction, string> = { nod: "点头", shake: "轻轻摇头", think: "思考", happy: "开心" }
const READING = "我们先来看一个生活中的例子。你可以把计算机里的内存想成一张书桌，正在使用的资料放在桌上，暂时不用的资料放进柜子。CPU 就像处理资料的人，需要先找到资料，再一步一步完成任务。现在请想一想，如果书桌空间不够，你会怎样安排这些资料？"
const size = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(2)} GiB`

export function TeacherStagePreview() {
  const teacher = React.useRef<TeacherStageHandle>(null)
  const voice = useTeacherVoice(teacher)
  const voiceLatest = React.useRef(voice)
  voiceLatest.current = voice
  const [consent, setConsent] = React.useState(false)
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
      mouthSample: (v: number) => teacher.current?.setMouthLevel(v), events: events.current,
      voice: () => ({ state: voiceLatest.current.state, mouth: voiceLatest.current.mouthLevel,
        sentence: voiceLatest.current.sentence, availability: voiceLatest.current.availability, error: voiceLatest.current.error }) }
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
        <fieldset className="teacher-voice"><legend>老师朗读</legend>
          <p data-voice-state>{voice.message}</p>
          <div className="teacher-preview-row">
            <button data-voice="speak" disabled={!voice.availability?.ready || voice.busy || paused || !visible || !mounted}
              onClick={() => { setMouth(0); void voice.speak(READING) }}>读一段话</button>
            <button data-voice="stop" disabled={!voice.busy} onClick={() => void voice.stop()}>停止朗读</button>
          </div>
          {voice.sentence && <p className="teacher-voice-sentence" data-voice-sentence data-reading={voice.sentence.state === "started"}>
            {voice.sentence.state === "started" ? "正在读：" : "已读："}{voice.sentence.text}</p>}
          {voice.error && <p role="alert" className="teacher-voice-error">{voice.error}</p>}
          <details><summary>音量与语速</summary><label>音量 {Math.round(voice.volume * 100)}%
            <input aria-label="朗读音量" type="range" min="0" max="1" step="0.05" value={voice.volume} onChange={(e) => void voice.configure({ volume: Number(e.target.value) })} /></label>
            <label>语速 {voice.speed.toFixed(2)} 倍（下段生效）<input aria-label="朗读语速" type="range" min="0.5" max="2" step="0.05" value={voice.speed} onChange={(e) => void voice.configure({ speed: Number(e.target.value) })} /></label>
          </details>
          {voice.availability && !voice.availability.ready && <details data-voice-install><summary>安装朗读资源</summary>
            {voice.availability.reason && <p>{voice.availability.reason}</p>}
            {voice.availability.components.map((part) => <p key={part.id}>{part.name} · {part.installed ? "已安装" : "未安装"} · {part.size_estimated ? "约 " : ""}{size(part.size_bytes)}<br />{!part.installed && part.instruction}</p>)}
            {voice.availability.download?.required && <>
              <p>将下载 {size(voice.availability.download.size_bytes)}，安装后占用 {size(voice.availability.download.installed_size_bytes)}。{voice.availability.download.instruction}</p>
              <label className="teacher-voice-consent"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />我同意下载上述朗读底模</label>
              <button data-voice="download" disabled={!consent || ["downloading", "cancelling"].includes(voice.downloadState?.state || "")} onClick={() => void voice.download()}>同意并下载</button>
            </>}
            <button onClick={() => void voice.refresh()}>重新检查</button>
          </details>}
          {voice.downloadState && <div role="status" className="teacher-voice-download">
            <p>{({ downloading: "正在下载并校验", cancelling: "正在取消", completed: "下载完成", cancelled: "已取消", error: "下载失败" } as Record<string, string>)[voice.downloadState.state]}</p>
            <progress max={voice.downloadState.total_bytes} value={voice.downloadState.received_bytes} />
            {voice.downloadState.state === "downloading" && <button onClick={() => void voice.cancelDownload()}>取消下载</button>}
          </div>}
        </fieldset>
        <fieldset><legend>朗读嘴型 <output className="teacher-preview-value">{(voice.busy ? voice.mouthLevel : mouth).toFixed(2)}</output></legend>
          <input aria-label="嘴型开合" type="range" min="0" max="1" step="0.01" disabled={!caps?.mouth || !mounted || voice.busy} value={mouth} onChange={(e) => setMouth(Number(e.target.value))} />
          <p>拖动滑块检查开合。朗读时，嘴型跟随正在播放的声音。</p>
          <div className="teacher-preview-row"><button data-mouth="closed" disabled={!caps?.mouth || !mounted || voice.busy} onClick={() => setMouth(0)}>闭嘴 · 0</button><button data-mouth="open" disabled={!caps?.mouth || !mounted || voice.busy} onClick={() => setMouth(.8)}>张嘴 · 0.8</button></div>
        </fieldset>
        <fieldset><legend>短反应</legend><div className="teacher-preview-row">
          {(Object.keys(LABELS) as Reaction[]).map((name) => <button key={name} data-reaction={name} disabled={!caps?.reactions[name] || paused || !visible || !mounted}
            title={caps && !caps.reactions[name] ? "模型缺少对应参数，接口暂不可用" : undefined} onClick={() => teacher.current?.[name]()}>{LABELS[name]}</button>)}
        </div><p className="teacher-preview-event" aria-live="polite">{reaction ? `${LABELS[reaction.reaction]} · ${{ started: "进行中", finished: "已完成", cancelled: "已停止" }[reaction.phase]}` : "选择一个反应，看看老师的变化。"}</p></fieldset>
        <fieldset><legend>渲染检查</legend><div className="teacher-preview-row">
          <button data-toggle="pause" onClick={() => { if (!paused) void voice.stop(); setPaused(!paused) }}>{paused ? "继续渲染" : "暂停渲染"}</button>
          <button data-toggle="visible" onClick={() => { if (visible) void voice.stop(); setVisible(!visible) }}>{visible ? "隐藏形象" : "显示形象"}</button>
          <button data-toggle="mount" onClick={() => { if (mounted) void voice.stop(); setMounted(!mounted) }}>{mounted ? "卸载组件" : "重新加载"}</button>
        </div><div className="teacher-preview-stats"><span>{snapshot?.active ? "渲染中" : "渲染已停止"}</span><span>{snapshot?.fps.toFixed(1) ?? "—"} FPS</span><span>{snapshot?.frames ?? 0} 帧</span></div></fieldset>
      </section>
    </div>
  </div>
}
