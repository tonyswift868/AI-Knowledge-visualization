import * as React from "react"
import { Download, Mic, Square, X } from "lucide-react"
import type { VoiceInputController } from "./useVoiceInput"
import "./voice.css"
import { Loading } from '../loading/Loading'

const size = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(1)} MB`
const source = (url: string) => {
  try {
    const host = new URL(url).hostname
    return host === "ghfast.top" ? "GHFast（第三方加速）" : host === "github.com" ? "GitHub" : host
  } catch { return "正在连接" }
}

function ModelDialog({ voice }: { voice: VoiceInputController }) {
  const ref = React.useRef<HTMLDialogElement>(null)
  const title = React.useId()
  const description = React.useId()
  const downloading = voice.model?.state === "downloading"
  const cancelling = !!voice.model?.cancelling
  const close = () => downloading && voice.ownsDownload ? void voice.cancelDownload() : voice.closeDownload()
  React.useEffect(() => {
    if (voice.downloadOpen && !ref.current?.open) ref.current?.showModal()
    else if (!voice.downloadOpen) ref.current?.close()
  }, [voice.downloadOpen])
  const total = voice.model?.total_bytes || voice.model?.size_bytes || 0
  const done = voice.model?.downloaded_bytes || 0
  return <dialog ref={ref} className="mf-voice-dialog" aria-labelledby={title} aria-describedby={description}
    onCancel={(e) => { e.preventDefault(); close() }}>
    <span className="mf-voice-dialog-icon"><Mic size={26} /></span>
    <h2 id={title}>{voice.model?.invalid ? "重新准备语音模型" : "开启本机语音输入"}</h2>
    <p id={description}>下载 SenseVoice 语音模型和语音检测模型后，就可以边说边出字。识别在本机完成，录音不会上传，也不会自动发送文字。</p>
    <div className="mf-voice-model-size"><span>首次下载</span><strong>{voice.model ? size(voice.model.size_bytes) : "正在读取大小…"}</strong></div>
    <p className="mf-voice-fine">仅下载模型时联网；之后识别可离线使用。</p>
    {downloading && <div className="mf-voice-progress" role="status" aria-live="polite">
      <Loading label={cancelling ? '正在取消并清理临时文件…' : '正在下载并准备语音模型…'}/>
      <span>来源：{source(voice.model?.download_url || "")}</span>
      <progress aria-label="语音模型下载进度" max={total || 1} value={done} />
      <span>{cancelling ? "正在取消并清理临时文件…" : done >= total && total > 0 ? "正在校验模型…" : `${size(done)} / ${size(total)}`}</span>
      {!voice.ownsDownload && <span>下载已在另一个语音组件中启动。</span>}
    </div>}
    {voice.error && <p className="mf-voice-error" role="alert">{voice.error.message}</p>}
    <div className="mf-voice-dialog-actions">
      <button type="button" className="mf-voice-secondary" disabled={cancelling} onClick={close}>{downloading && voice.ownsDownload ? "取消下载" : "暂不使用"}</button>
      {!downloading && <button type="button" className="mf-voice-primary" disabled={!voice.model || voice.disabled} onClick={() => void voice.download()}>
        <Download size={17} />同意并下载
      </button>}
    </div>
  </dialog>
}

/** Put next to the controlled input bound through useVoiceInput({value,onChange}). */
export function MicButton({ voice, compact = false }: { voice: VoiceInputController; compact?: boolean }) {
  const [seconds, setSeconds] = React.useState(0)
  const recording = voice.state === "recording"
  const finishing = voice.state === "finishing"
  const preparing = voice.state === "checking" || voice.state === "starting"
  React.useEffect(() => {
    if (!recording) return
    setSeconds(0)
    const timer = window.setInterval(() => setSeconds((v) => v + 1), 1000)
    return () => window.clearInterval(timer)
  }, [recording])
  const label = recording ? "结束录音" : finishing ? "正在识别最后一句…" : preparing ? "正在准备…" : "开始录音"
  // The button keeps one short label width; longer progress text goes to the status strip.
  const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`
  const short = recording ? `结束 ${clock}` : finishing ? "识别中…" : preparing ? "准备中…" : "开始录音"
  return <div className="mf-voice" data-voice-state={voice.state}>
    <div className="mf-voice-controls">
      <button type="button" className={`mf-voice-mic ${recording ? "is-recording" : ""} ${compact ? "is-compact" : ""}`}
        aria-label={label} aria-pressed={recording} title={voice.supported ? label : "请在 MentorFlow 桌面端使用语音输入"}
        disabled={!voice.supported || voice.disabled || preparing || finishing || voice.model?.state === "downloading"}
        onClick={() => recording ? void voice.stop() : void voice.start()}>
        {recording ? <Square size={17} fill="currentColor" /> : <Mic size={18} />}
        {!compact && <span>{short}</span>}
      </button>
    </div>
    {/* Whatever comes and goes floats here; the input row never reflows. The corrected
        transcript itself is only shown in the input box (BUG-94, user 2026-10-14). */}
    <div className="mf-voice-status">
      <Loading active={preparing || finishing} label={label} onCancel={voice.cancel}/>
      {recording && <span className="mf-voice-live" role="status"><span className="mf-voice-bars" aria-hidden="true">{[0, 1, 2, 3, 4].map((i) => <i key={i} />)}</span>正在聆听</span>}
      {(recording || finishing || preparing) && <button type="button" className="mf-voice-quiet" onClick={voice.cancel} aria-label="取消录音"><X size={16} />取消</button>}
      {!voice.supported && <p className="mf-voice-hint">请在 MentorFlow 桌面端使用语音输入。</p>}
      {voice.error && !voice.downloadOpen && <div className="mf-voice-error" role="alert">{voice.error.message}
        {["ASR_MODEL_INVALID", "ASR_MODEL_REQUIRED"].includes(voice.error.code) && <button type="button" className="mf-voice-quiet" onClick={voice.openDownload}>准备模型</button>}
      </div>}
      {voice.notice && <p className="mf-voice-hint" role="status">{voice.notice}</p>}
    </div>
    <ModelDialog voice={voice} />
  </div>
}
