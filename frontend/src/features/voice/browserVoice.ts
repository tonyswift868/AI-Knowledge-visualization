import { getVoicePlaybackRate } from './voicePlaybackRate'

export type BrowserVoiceOptions = {
  rate?: number
  pitch?: number
  volume?: number
  voiceHint?: string
  onStart?: () => void
  onEnd?: () => void
  onError?: (reason?: string) => void
}

/** Only used after the learner explicitly enables the system-voice fallback. */
export const COMPATIBILITY_TEACHER_BROWSER_VOICE_HINT = 'xiaoxiao'

const NATURAL_FEMALE_VOICE_HINTS = [
  'xiaoxiao', 'xiaoyi', 'xiaohan', 'xiaomo', 'xiaorui', 'xiaoxuan',
  'xiaoyan', 'xiaozhen', 'hsiaochen', 'hsiaoyu', 'ting-ting',
  'mei-jia', 'sin-ji', 'google 普通话', 'google mandarin',
]

const MALE_VOICE_HINTS = ['yunxi', 'yunyang', 'yunfeng', 'male', '男声']

let browserVoiceGeneration = 0
const pendingVoiceTimers = new Set<number>()
let pendingVoicesChanged: {
  synthesis: SpeechSynthesis
  listener: () => void
} | null = null

function clearPendingBrowserVoice() {
  for (const timer of pendingVoiceTimers) window.clearTimeout(timer)
  pendingVoiceTimers.clear()
  if (pendingVoicesChanged) {
    pendingVoicesChanged.synthesis.removeEventListener?.(
      'voiceschanged',
      pendingVoicesChanged.listener,
    )
    pendingVoicesChanged = null
  }
}

function scheduleBrowserVoice(callback: () => void, delay: number): number {
  const timer = window.setTimeout(() => {
    pendingVoiceTimers.delete(timer)
    callback()
  }, delay)
  pendingVoiceTimers.add(timer)
  return timer
}

function allVoices(): SpeechSynthesisVoice[] {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return []
  return window.speechSynthesis.getVoices?.() || []
}

function voiceScore(voice: SpeechSynthesisVoice, language: string): number {
  const name = voice.name.toLowerCase()
  const lang = voice.lang.toLowerCase()
  const baseLanguage = language.toLowerCase().split('-')[0]
  if (!lang.startsWith(baseLanguage)) return -1000
  let score = voice.default ? 2 : 0
  if (lang === language.toLowerCase()) score += 8
  for (const [index, hint] of NATURAL_FEMALE_VOICE_HINTS.entries()) {
    if (name.includes(hint)) score += 100 - index
  }
  if (name.includes('female') || name.includes('女')) score += 4
  if (name.includes('natural') || name.includes('online')) score += 8
  if (MALE_VOICE_HINTS.some(hint => name.includes(hint))) score -= 80
  return score
}

/** Returns Chinese voices currently installed in the browser, best first. */
export function listBrowserVoices(language = 'zh-CN'): SpeechSynthesisVoice[] {
  return allVoices()
    .map(voice => ({ voice, score: voiceScore(voice, language) }))
    .filter(item => item.score > -1000)
    .sort((a, b) => b.score - a.score || a.voice.name.localeCompare(b.voice.name))
    .map(item => item.voice)
}

export function pickPreferredBrowserVoice(language = 'zh-CN', voiceHint = ''): SpeechSynthesisVoice | undefined {
  const voices = listBrowserVoices(language)
  const hint = voiceHint.trim().toLowerCase()
  return (hint ? voices.find(voice => voice.name.toLowerCase() === hint || voice.name.toLowerCase().includes(hint)) : undefined)
    || voices[0]
}

export function speakBrowser(text: string, language = 'zh-CN', options: BrowserVoiceOptions = {}) {
  if (typeof window === 'undefined' || !('speechSynthesis' in window) || typeof SpeechSynthesisUtterance === 'undefined') return { ok: false, reason: 'speech_synthesis_unavailable' }
  stopBrowserVoice()
  const synthesis = window.speechSynthesis
  const generation = browserVoiceGeneration
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = language
  // Near-conversational defaults sound less synthetic than the previous fast/high pitch.
  utterance.rate = options.rate ?? getVoicePlaybackRate()
  utterance.pitch = options.pitch ?? 1
  utterance.volume = options.volume ?? 1
  utterance.onstart = () => {
    if (generation === browserVoiceGeneration) options.onStart?.()
  }
  utterance.onend = () => {
    if (generation === browserVoiceGeneration) options.onEnd?.()
  }
  utterance.onerror = event => {
    if (generation === browserVoiceGeneration) options.onError?.(event.error || 'speech_synthesis_failed')
  }
  let spoken = false
  const speakWhenReady = () => {
    if (spoken || generation !== browserVoiceGeneration) return
    spoken = true
    if (pendingVoicesChanged?.listener === onVoicesChanged) pendingVoicesChanged = null
    const voice = pickPreferredBrowserVoice(language, options.voiceHint)
    if (voice) utterance.voice = voice
    // Chrome can remain paused after a previous tab/utterance; resume is safe here.
    synthesis.resume?.()
    try {
      synthesis.speak(utterance)
    } catch {
      if (generation === browserVoiceGeneration) options.onError?.('speech_synthesis_failed')
    }
  }
  const onVoicesChanged = () => {
    if (generation !== browserVoiceGeneration) return
    synthesis.removeEventListener?.('voiceschanged', onVoicesChanged)
    if (pendingVoicesChanged?.listener === onVoicesChanged) pendingVoicesChanged = null
    speakWhenReady()
  }
  const voices = allVoices()
  if (voices.length) {
    // Defer one task so a preceding cancel() cannot swallow this utterance.
    scheduleBrowserVoice(speakWhenReady, 0)
  } else {
    synthesis.addEventListener?.('voiceschanged', onVoicesChanged)
    pendingVoicesChanged = { synthesis, listener: onVoicesChanged }
    // Some Chromium builds never emit voiceschanged; use the browser default after a short wait.
    scheduleBrowserVoice(() => {
      synthesis.removeEventListener?.('voiceschanged', onVoicesChanged)
      if (pendingVoicesChanged?.listener === onVoicesChanged) pendingVoicesChanged = null
      speakWhenReady()
    }, 700)
  }
  return { ok: true }
}
export function stopBrowserVoice() {
  browserVoiceGeneration += 1
  if (typeof window === 'undefined') return
  clearPendingBrowserVoice()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
}
