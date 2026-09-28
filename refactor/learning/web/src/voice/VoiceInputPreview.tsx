/** Hidden, local-only component preview: #/dev/voice. No classroom submission. */
import * as React from "react"
import { ArrowUp, AudioLines, ShieldCheck } from "lucide-react"
import { MicButton } from "./MicButton"
import { useVoiceInput } from "./useVoiceInput"
import "./voice.css"

export function VoiceInputPreview() {
  const [text, setText] = React.useState("")
  const [glossary, setGlossary] = React.useState(true)
  const [sent, setSent] = React.useState("")
  const voice = useVoiceInput({ value: text, onChange: setText, terms: glossary ? ["牛顿", "万有引力", "Newton"] : [] })
  return <section className="mf-voice-preview">
    <header><span className="mf-voice-kicker">VOICE STUDIO · 开发预览</span><h1>把想法说出来</h1><p>边说边看，停下来再检查。是否发送，始终由你决定。</p></header>
    <div className="mf-voice-preview-grid"><section className="mf-voice-card">
      <div className="mf-voice-card-heading"><span className="mf-voice-orb"><AudioLines size={24} /></span><div><h2>试着回答老师</h2><p>今天我们学习物理，牛顿第一定律。</p></div></div>
      <label htmlFor="voice-preview-answer">你的回答</label>
      <textarea id="voice-preview-answer" value={text} onChange={(e) => setText(e.target.value)} placeholder="点击开始录音，也可以直接打字…" />
      <div className="mf-voice-composer-actions"><MicButton voice={voice} /><button type="button" className="mf-voice-send" disabled={!text.trim() || ["recording", "finishing", "starting"].includes(voice.state)} onClick={() => setSent(text)} aria-label="手动发送预览"><ArrowUp size={18} />发送预览</button></div>
      <p className="mf-voice-fine">手动编辑会停止本次录音，并保留你的修改。此页面不向老师发送回答。</p>
      {sent && <div className="mf-voice-sent" data-voice-sent><b>你手动发送的预览</b><p>{sent}</p></div>}
    </section><aside className="mf-voice-card mf-voice-guide">
      <ShieldCheck size={27} /><h2>留在本机的声音</h2><p>使用本机识别模型。首次下载需要你的同意，日常录音无需联网。</p>
      <label className="mf-voice-glossary"><input type="checkbox" checked={glossary} disabled={["recording", "finishing", "starting"].includes(voice.state)} onChange={(e) => setGlossary(e.target.checked)} />启用物理课程术语</label>
      <p className="mf-voice-fine">本次录音使用：牛顿、万有引力、Newton。修正后可以撤销。</p>
      <div className="mf-voice-model-state"><span>模型状态</span><b>{!voice.supported ? "请使用桌面端" : !voice.model ? "检查中" : ({ ready: "已就绪", downloading: "正在下载", not_downloaded: "待下载" })[voice.model.state]}</b></div>
      <h3>已确定的语句</h3><div className="mf-voice-sentences" aria-live="polite">{voice.segments.length ? voice.segments.map((s) => <p key={s.index}>{s.text}</p>) : <p>停顿后，确定的语句会显示在这里。</p>}</div>
    </aside></div>
  </section>
}
