import { useSyncExternalStore } from "react"

export const VOICE_PLAYBACK_RATE_MIN = 0.4
export const VOICE_PLAYBACK_RATE_MAX = 1.5
export const VOICE_PLAYBACK_RATE_STEP = 0.05
export const VOICE_PLAYBACK_RATE_DEFAULT = 1
export const VOICE_PLAYBACK_RATE_PRESETS = [0.5, 0.65, 0.8, 1, 1.2] as const

const STORAGE_KEY = "mentorflow_voice_playback_rate_v1"
const CHANGE_EVENT = "mentorflow:voice-playback-rate-change"

export function normalizeVoicePlaybackRate(value: unknown): number {
  const parsed = typeof value === "number" ? value : Number(value)
  if (!Number.isFinite(parsed)) return VOICE_PLAYBACK_RATE_DEFAULT
  const clamped = Math.min(VOICE_PLAYBACK_RATE_MAX, Math.max(VOICE_PLAYBACK_RATE_MIN, parsed))
  return Number((Math.round(clamped / VOICE_PLAYBACK_RATE_STEP) * VOICE_PLAYBACK_RATE_STEP).toFixed(2))
}

export function getVoicePlaybackRate(): number {
  if (typeof window === "undefined") return VOICE_PLAYBACK_RATE_DEFAULT
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return stored === null ? VOICE_PLAYBACK_RATE_DEFAULT : normalizeVoicePlaybackRate(stored)
  } catch {
    return VOICE_PLAYBACK_RATE_DEFAULT
  }
}

export function setVoicePlaybackRate(value: number): number {
  const next = normalizeVoicePlaybackRate(value)
  if (typeof window === "undefined") return next
  try { window.localStorage.setItem(STORAGE_KEY, String(next)) } catch { /* optional persistence */ }
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: next }))
  return next
}

export function subscribeVoicePlaybackRate(listener: () => void): () => void {
  if (typeof window === "undefined") return () => undefined
  const onStorage = (event: StorageEvent) => { if (event.key === STORAGE_KEY) listener() }
  window.addEventListener(CHANGE_EVENT, listener)
  window.addEventListener("storage", onStorage)
  return () => {
    window.removeEventListener(CHANGE_EVENT, listener)
    window.removeEventListener("storage", onStorage)
  }
}

export function useVoicePlaybackRate(): number {
  return useSyncExternalStore(subscribeVoicePlaybackRate, getVoicePlaybackRate, () => VOICE_PLAYBACK_RATE_DEFAULT)
}

export function formatVoicePlaybackRate(rate: number): string {
  return `${Number(normalizeVoicePlaybackRate(rate).toFixed(2))}×`
}
