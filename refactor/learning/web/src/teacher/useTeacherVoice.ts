import * as React from "react"
import { call, on } from "../bridge"
import type { TeacherStageHandle } from "./TeacherStage"

export type VoiceAvailability = {
  ready: boolean; message: string; reason?: string; state?: string; volume?: number; speed?: number
  components: { id: string; name: string; installed: boolean; size_bytes: number; size_estimated: boolean; instruction: string }[]
  download: { required: boolean; size_bytes: number; installed_size_bytes: number; target: string; instruction: string } | null
}
export type VoiceSentence = { owner: string; utterance_id: string; index: number; text: string;
  state: "started" | "finished" | "interrupted"; start_seconds: number; end_seconds: number | null }
export type VoiceDownload = { owner: string; download_id: string; state: string;
  received_bytes: number; total_bytes: number; code?: string; message?: string }

let serial = 0
const identifier = () => `voice-${Date.now().toString(36)}-${(++serial).toString(36)}`
const disabled: VoiceAvailability = { ready: false, message: "朗读未启用", components: [], download: null }

type StreamVoice = {
  startStream(): Promise<string | null>
  appendStream(text: string, utteranceId?: string | null): Promise<boolean>
  endStream(utteranceId?: string | null): Promise<boolean>
  stop(): Promise<void>
}

/** One model response, including retries. Stop/reset invalidates queued chunks
 * immediately; slow bridge replies cannot append into the replacement turn. */
export function createTeacherVoiceStream(voice: StreamVoice) {
  let active = true, ended = false, generation = 0
  let id = voice.startStream()
  let pending: Promise<unknown> = Promise.resolve()
  const stop = () => {
    if (!active) return Promise.resolve()
    active = false; generation++
    return voice.stop()
  }
  return {
    append(delta: string) {
      if (!active || ended) return
      if (delta === "\u0000") {
        generation++
        void voice.stop()
        id = voice.startStream()
        pending = Promise.resolve()
        return
      }
      const version = generation, target = id
      pending = pending.then(async () => {
        const utterance = await target
        if (active && version === generation && utterance) await voice.appendStream(delta, utterance)
      })
    },
    end() {
      ended = true
      const version = generation, target = id
      return pending.then(async () => {
        const utterance = await target
        if (active && version === generation && utterance) await voice.endStream(utterance)
      })
    },
    stop,
  }
}

/** Owns one consumer scope. Unmount/stop cannot cancel a newer page's speech. */
export function useTeacherVoice(teacher: React.RefObject<TeacherStageHandle | null>, { autoWarm = true } = {}) {
  const [owner] = React.useState(identifier)
  const current = React.useRef<string | null>(null)
  const streamCurrent = React.useRef<string | null>(null)
  const downloadId = React.useRef<string | null>(null)
  const alive = React.useRef(false)
  const revision = React.useRef(0)
  const [availability, setAvailability] = React.useState<VoiceAvailability | null>(null)
  const [state, setState] = React.useState("idle")
  const [mouthLevel, setMouthLevel] = React.useState(0)
  const [sentence, setSentence] = React.useState<VoiceSentence | null>(null)
  const [error, setError] = React.useState("")
  const [downloadState, setDownloadState] = React.useState<VoiceDownload | null>(null)
  const [volume, setVolume] = React.useState(1)
  const [speed, setSpeed] = React.useState(1)

  const mouth = React.useCallback((level: number) => {
    const value = Number.isFinite(level) ? Math.max(0, Math.min(1, level)) : 0
    teacher.current?.setMouthLevel(value)
    if (alive.current) setMouthLevel(value)
  }, [teacher])

  const refresh = React.useCallback(async () => {
    const version = ++revision.current
    try {
      const value = await call<VoiceAvailability>("speech.status")
      if (!alive.current || version !== revision.current) return
      setAvailability(value.components ? value : disabled)
      if (typeof value.volume === "number") setVolume(value.volume)
      if (typeof value.speed === "number") setSpeed(value.speed)
    } catch {
      if (alive.current && version === revision.current) setAvailability(disabled)
    }
  }, [])

  const warmUp = React.useCallback(async () => {
    try { await call("speech.warm_up", { owner }) }
    catch { /* An unavailable optional reader must not interrupt text learning. */ }
  }, [owner])

  const stop = React.useCallback(async () => {
    const utterance_id = current.current
    current.current = null
    streamCurrent.current = null
    mouth(0)
    if (alive.current) { setState("stopped"); setSentence(null) }
    try { await call("speech.stop", { owner, ...(utterance_id ? { utterance_id } : {}) }) }
    catch { /* Window may already be closing. */ }
  }, [owner, mouth])

  const speak = React.useCallback(async (text: string) => {
    if (!availability?.ready) { if (alive.current) setError(""); return false }
    const utterance_id = identifier()
    current.current = utterance_id
    streamCurrent.current = null
    mouth(0); setSentence(null); setError(""); setState("checking")
    try {
      await call("speech.speak", { owner, utterance_id, text })
      return current.current === utterance_id
    } catch (e: any) {
      if (alive.current && current.current === utterance_id) { setState("error"); setError(e.message || "朗读暂时不可用。"); mouth(0) }
      return false
    }
  }, [availability?.ready, owner, mouth])

  /** Start a streamed utterance; appendStream may be called for each model chunk. */
  const startStream = React.useCallback(async () => {
    if (!availability?.ready) { if (alive.current) setError(""); return null }
    const utterance_id = identifier()
    current.current = utterance_id
    streamCurrent.current = utterance_id
    mouth(0); setSentence(null); setError(""); setState("checking")
    try {
      await call("speech.stream_start", { owner, utterance_id })
      return current.current === utterance_id ? utterance_id : null
    } catch (e: any) {
      if (alive.current && current.current === utterance_id) {
        setState("error"); setError(e.message || "朗读暂时不可用。"); mouth(0)
        current.current = null; streamCurrent.current = null
      }
      return null
    }
  }, [availability?.ready, owner, mouth])

  const appendStream = React.useCallback(async (text: string, utteranceId?: string | null) => {
    const utterance_id = utteranceId === undefined ? streamCurrent.current : utteranceId
    if (!utterance_id || streamCurrent.current !== utterance_id || current.current !== utterance_id) return false
    try {
      const result = await call("speech.stream_append", { owner, utterance_id, text })
      return result.accepted && streamCurrent.current === utterance_id
    } catch (e: any) {
      if (alive.current && current.current === utterance_id) setError(e.message || "朗读暂时不可用。")
      return false
    }
  }, [owner])

  const endStream = React.useCallback(async (utteranceId?: string | null) => {
    const utterance_id = utteranceId === undefined ? streamCurrent.current : utteranceId
    if (!utterance_id || streamCurrent.current !== utterance_id || current.current !== utterance_id) return false
    streamCurrent.current = null
    try {
      const result = await call("speech.stream_end", { owner, utterance_id })
      return result.accepted && current.current === utterance_id
    } catch (e: any) {
      if (alive.current && current.current === utterance_id) setError(e.message || "朗读暂时不可用。")
      return false
    }
  }, [owner])

  const download = React.useCallback(async (consent: boolean) => {
    setError("")
    try {
      const result = await call("speech.download", { owner, consent })
      if (alive.current) downloadId.current = result.download_id
    } catch (e: any) { if (alive.current) setError(e.message || "下载没有开始。") }
  }, [owner])

  const cancelDownload = React.useCallback(async () => {
    try { await call("speech.cancel_download", { owner, ...(downloadId.current ? { download_id: downloadId.current } : {}) }) }
    catch { /* Optional component may already be disposed. */ }
  }, [owner])

  const configure = React.useCallback(async (changes: { volume?: number; speed?: number }) => {
    try { await call("speech.settings", changes) }
    catch (e: any) { if (alive.current) setError(e.message || "朗读设置未保存。") }
  }, [])

  React.useEffect(() => {
    alive.current = true
    const matches = (event: { owner: string; utterance_id: string | null }) => event.owner === owner && event.utterance_id === current.current
    const offs = [
      on("speech.mouth", (e) => { if (matches(e)) mouth(e.level) }),
      on("speech.sentence", (e: VoiceSentence) => { if (matches(e)) setSentence(e) }),
      on("speech.state", (e) => { if (matches(e)) { setState(e.state); if (["idle", "stopped", "disabled", "error", "closed"].includes(e.state)) mouth(0) } }),
      on("speech.error", (e) => { if (matches(e)) { setError(e.message); mouth(0) } }),
      on("speech.availability", (e) => { setAvailability(e); if (!e.ready) mouth(0) }),
      on("speech.settings", (e) => { setVolume(e.volume); setSpeed(e.speed) }),
      on("speech.download", (e: VoiceDownload) => {
        if (e.owner !== owner) return
        if (downloadId.current && downloadId.current !== e.download_id && e.state !== "downloading") return
        downloadId.current = e.download_id
        setDownloadState(e)
        if (e.state === "completed") void refresh()
        if (e.state === "error") setError(e.message || "资源下载失败，请重试。")
      }),
    ]
    void refresh()
    // Silent warm-up is useful in the background. Only actual utterances need
    // cancellation on visibility loss; cancelling preparation loses its ready event.
    const hidden = () => { if (document.hidden && current.current !== null) void stop() }
    document.addEventListener("visibilitychange", hidden)
    return () => {
      alive.current = false; revision.current++
      offs.forEach((off) => off())
      document.removeEventListener("visibilitychange", hidden)
      void stop(); void cancelDownload()
    }
  }, [owner, mouth, refresh, stop, cancelDownload])

  React.useEffect(() => { if (autoWarm && availability?.ready) void warmUp() }, [autoWarm, availability?.ready, warmUp])

  const busy = ["checking", "warming", "synthesizing", "playing"].includes(state)
  const message = !availability?.ready ? "朗读未启用" : ({ checking: "正在检查朗读资源", warming: "正在预热老师声音",
    synthesizing: "正在准备声音", playing: "老师正在朗读", error: "朗读暂时不可用" } as Record<string, string>)[state] || "朗读已就绪"
  return { availability, state, busy, message, mouthLevel, sentence, error, volume, speed, downloadState,
    speak, startStream, appendStream, endStream, stop, warmUp, refresh, download, cancelDownload, configure }
}
