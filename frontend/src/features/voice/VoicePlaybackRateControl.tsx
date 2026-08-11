import {
  formatVoicePlaybackRate,
  setVoicePlaybackRate,
  useVoicePlaybackRate,
  VOICE_PLAYBACK_RATE_MAX,
  VOICE_PLAYBACK_RATE_MIN,
  VOICE_PLAYBACK_RATE_PRESETS,
  VOICE_PLAYBACK_RATE_STEP,
} from "./voicePlaybackRate"
import { useId } from "react"
import "./voice-playback-rate.css"

export function VoicePlaybackRateControl({ compact = false }: { compact?: boolean }) {
  const rate = useVoicePlaybackRate()
  const id = `voice-playback-rate-${useId().replace(/:/g, "")}`
  return (
    <div className={`voice-playback-rate${compact ? " is-compact" : ""}`} data-testid="voice-playback-rate">
      <div className="voice-playback-rate__heading">
        <label htmlFor={id}>朗读速度</label>
        <span className="voice-playback-rate__value">{formatVoicePlaybackRate(rate)}</span>
      </div>
      <input aria-label="朗读速度" id={id} max={VOICE_PLAYBACK_RATE_MAX} min={VOICE_PLAYBACK_RATE_MIN}
        onChange={event => setVoicePlaybackRate(event.target.valueAsNumber)} step={VOICE_PLAYBACK_RATE_STEP}
        type="range" value={rate} />
      {!compact && <div className="voice-playback-rate__presets" aria-label="常用朗读速度">
        {VOICE_PLAYBACK_RATE_PRESETS.map(preset => <button aria-pressed={rate === preset}
          className={rate === preset ? "is-active" : ""} key={preset}
          onClick={() => setVoicePlaybackRate(preset)} type="button">{formatVoicePlaybackRate(preset)}</button>)}
      </div>}
      {!compact && <small>所有离线声包和系统语音共用此速度并自动记住；离线声包播放中立即生效，系统语音从下一次朗读生效。</small>}
    </div>
  )
}
