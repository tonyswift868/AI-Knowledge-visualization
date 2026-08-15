import {
  COMPATIBILITY_TEACHER_BROWSER_VOICE_HINT,
  speakBrowser,
  stopBrowserVoice,
} from "./browserVoice"
import {
  getTeacherVoiceSettings,
  getTeacherVoiceStatus,
  type TeacherVoiceStatus,
  type VoiceFallbackPolicy,
} from "./voicePackApi"
import { getVoicePlaybackRate, subscribeVoicePlaybackRate } from "./voicePlaybackRate"

export type LocalTeacherVoiceOptions = {
  onStart?: () => void
  onEnd?: () => void
  onError?: (reason: string) => void
  onPreparing?: (phase: LocalTeacherVoicePreparingPhase) => void
  style?: "lively" | "relaxed"
  replyKind?: string
}

export type LocalTeacherVoicePreparingPhase = "starting" | "synthesizing"

export type LocalTeacherVoiceResult = {
  ok: boolean
  reason?: string
}

let activeAudio: HTMLAudioElement | null = null
let activeObjectUrl = ""
let playbackGeneration = 0
let activeRequest: AbortController | null = null
let activeRateUnsubscribe: (() => void) | null = null
let activePlaybackCancel: (() => void) | null = null
const voiceCache = new Map<string, Blob>()
const MAX_CACHED_VOICES = 12
const MAX_CACHED_VOICE_BYTES = 24 * 1024 * 1024
const READY_SYNTHESIS_TIMEOUT_MS = 90_000
const COLD_START_SYNTHESIS_TIMEOUT_MS = 240_000
const PROGRESSIVE_STREAM_THRESHOLD_CHARS = 180
// Keep chunks large enough to preserve a sentence's prosody.  The next chunk
// is prefetched while the current WAV is playing, so there is no reason to
// trade a natural phrase for the old 110-character hard boundary.
const PROGRESSIVE_CHUNK_MAX_CHARS = 220
let cachedVoiceBytes = 0

const normalizeTeacherVoiceCode = (value: string): string => value
  .replace(/\b__init__\b/g, "初始化")
  .replace(/\bNone\b/g, "空值")
  .replace(/\bTrue\b/g, "真")
  .replace(/\bFalse\b/g, "假")
  .replace(/\bbool\b/g, "布尔值")
  .replace(/->/g, "，返回 ")
  .replace(/===?/g, " 等于 ")
  .replace(/!=/g, " 不等于 ")
  .replace(/<=/g, " 小于等于 ")
  .replace(/>=/g, " 大于等于 ")
  .replace(/\.([A-Za-z_]\w*)\b/g, " 的 $1")
  .replace(/\bself\b/g, "自身")
  .replace(/([a-z])([A-Z])/g, "$1 $2")
  .replace(/[_()[\]{}]/g, " ")
  .replace(/[:;,]/g, "，")
  .replace(/\s+/g, " ")
  .trim()

/**
 * Markdown/code is for the eye, not for a character voice.  Keep the visible
 * teacher reply unchanged, but remove fenced code and turn small inline code
 * fragments into a few natural-language words before GPT-SoVITS sees them.
 */
export function normalizeTeacherVoiceText(text: string): string {
  const source = String(text || "").replace(/\r\n?/g, "\n")
  return source
    .replace(/```[^\n]*\n?[\s\S]*?```/g, "。")
    .replace(/`([^`\n]+)`/g, (_match, code: string) => ` ${normalizeTeacherVoiceCode(code)} `)
    .replace(/\bNone\b/g, "空值")
    .replace(/\bTrue\b/g, "真")
    .replace(/\bFalse\b/g, "假")
    .replace(/\[\[重点:([^\]\n]{1,120})\]\]/g, "$1")
    .replace(/\*\*([^*\n]{1,120})\*\*/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
}

/** Kept for existing callers: teacher speech is deliberately never warmed on page load. */
export async function warmLocalTeacherVoice(): Promise<boolean> {
  return true
}

function releaseObjectUrl() {
  if (!activeObjectUrl) return
  URL.revokeObjectURL(activeObjectUrl)
  activeObjectUrl = ""
}

export function stopLocalTeacherVoice() {
  playbackGeneration += 1
  activeRequest?.abort()
  activeRequest = null
  activePlaybackCancel?.()
  activePlaybackCancel = null
  activeRateUnsubscribe?.()
  activeRateUnsubscribe = null
  stopBrowserVoice()
  if (activeAudio) {
    activeAudio.pause()
    activeAudio.removeAttribute("src")
    activeAudio.load()
    activeAudio = null
  }
  releaseObjectUrl()
}

export function resetLocalTeacherVoiceCache() {
  stopLocalTeacherVoice()
  voiceCache.clear()
  cachedVoiceBytes = 0
}

function cacheVoice(key: string, blob: Blob) {
  const previous = voiceCache.get(key)
  if (previous) cachedVoiceBytes -= previous.size
  voiceCache.delete(key)
  if (blob.size > MAX_CACHED_VOICE_BYTES) return
  voiceCache.set(key, blob)
  cachedVoiceBytes += blob.size
  while (voiceCache.size > MAX_CACHED_VOICES || cachedVoiceBytes > MAX_CACHED_VOICE_BYTES) {
    const oldest = voiceCache.keys().next().value
    if (typeof oldest !== "string") break
    const evicted = voiceCache.get(oldest)
    if (evicted) cachedVoiceBytes -= evicted.size
    voiceCache.delete(oldest)
  }
}

function speakWithBrowserFallback(
  text: string,
  generation: number,
  options: LocalTeacherVoiceOptions,
): LocalTeacherVoiceResult {
  if (generation !== playbackGeneration) return { ok: false, reason: "cancelled" }
  const fallback = speakBrowser(text, "zh-CN", {
    voiceHint: COMPATIBILITY_TEACHER_BROWSER_VOICE_HINT,
    pitch: 1,
    onStart: () => {
      if (generation === playbackGeneration) options.onStart?.()
    },
    onEnd: () => {
      if (generation === playbackGeneration) options.onEnd?.()
    },
    onError: reason => {
      if (generation === playbackGeneration) options.onError?.(reason || "browser_voice_failed")
    },
  })
  return fallback.ok ? { ok: true } : { ok: false, reason: fallback.reason }
}

async function speakWithConfiguredFallback(
  text: string,
  generation: number,
  options: LocalTeacherVoiceOptions,
  knownPolicy?: VoiceFallbackPolicy,
): Promise<LocalTeacherVoiceResult | null> {
  if (generation !== playbackGeneration) return { ok: false, reason: "cancelled" }
  let policy = knownPolicy
  if (!policy) {
    try {
      policy = (await getTeacherVoiceSettings()).fallbackPolicy
    } catch {
      return null
    }
  }
  if (generation !== playbackGeneration) return { ok: false, reason: "cancelled" }
  if (policy !== "system_voice") return null
  return speakWithBrowserFallback(text, generation, options)
}

function unavailablePackReason(state: string): string {
  if (["staging", "installing", "verifying"].includes(state)) {
    return "离线教师声包正在安装或校验，完成后再试。"
  }
  if (["failed", "error", "corrupt"].includes(state)) {
    return "离线教师声包不可用，请前往“设置与安全”修复或重试安装。"
  }
  if (state === "installed") {
    return "离线教师声包尚未启用，请前往“设置与安全”激活声包。"
  }
  if (state === "active") {
    return "无法确认当前启用的声包版本，请前往“设置与安全”刷新或重新启用声包。"
  }
  return "尚未安装并启用离线教师声包，请前往“设置与安全”安装声包。"
}

async function voiceSynthesisFailureReason(
  response: Response,
  fallback: string,
): Promise<string> {
  let payload: unknown = null
  try {
    payload = await response.clone().json()
  } catch {
    return fallback
  }
  if (!payload || typeof payload !== "object") return fallback
  const root = payload as Record<string, unknown>
  const detail = root.detail
  const detailRecord = detail && typeof detail === "object"
    ? detail as Record<string, unknown>
    : null
  const nestedError = detailRecord?.error
  const errorRecord = nestedError && typeof nestedError === "object"
    ? nestedError as Record<string, unknown>
    : root.error && typeof root.error === "object"
      ? root.error as Record<string, unknown>
      : null
  const code = String(errorRecord?.code || detailRecord?.code || root.code || "")
  const message = String(
    errorRecord?.message
      || detailRecord?.message
      || root.message
      || "",
  ).trim()
  const runtimeError = String(
    (errorRecord?.details && typeof errorRecord.details === "object"
      ? (errorRecord.details as Record<string, unknown>).error
      : "") || "",
  )
  if (code === "VOICE_PACK_RUNTIME_UNAVAILABLE" && /timeout/i.test(runtimeError)) {
    return "离线教师声包生成较慢，刚才等待超时；请重试朗读。"
  }
  if (message) {
    if (response.status === 422) return message
    return `${message}；请前往“设置与安全”修复声包后重试。`
  }
  return fallback
}

async function playProgressiveTeacherVoice(
  streamUrl: string,
  text: string,
  generation: number,
  options: LocalTeacherVoiceOptions,
  voiceStatus: TeacherVoiceStatus,
): Promise<LocalTeacherVoiceResult> {
  if (generation !== playbackGeneration) return { ok: false, reason: "cancelled" }
  const audio = new Audio(streamUrl)
  activeAudio = audio
  audio.preload = "none"
  const applyPlaybackRate = () => {
    const rate = getVoicePlaybackRate()
    audio.playbackRate = rate
    audio.defaultPlaybackRate = rate
    audio.preservesPitch = true
    ;(audio as HTMLAudioElement & { webkitPreservesPitch?: boolean }).webkitPreservesPitch = true
  }
  const cleanup = () => {
    activeRateUnsubscribe?.()
    activeRateUnsubscribe = null
    if (activeAudio === audio) activeAudio = null
  }
  applyPlaybackRate()
  activeRateUnsubscribe = subscribeVoicePlaybackRate(applyPlaybackRate)
  audio.onplaying = () => {
    if (generation === playbackGeneration) options.onStart?.()
  }
  audio.onended = () => {
    if (activeAudio !== audio) return
    cleanup()
    if (generation === playbackGeneration) options.onEnd?.()
  }
  audio.onerror = () => {
    if (activeAudio !== audio) return
    cleanup()
    if (generation !== playbackGeneration) return
    if (voiceStatus.fallbackPolicy === "system_voice") {
      const fallback = speakWithBrowserFallback(text, generation, options)
      if (fallback.ok) return
    }
    options.onError?.("流式教师语音播放失败，请稍后重试。")
  }
  try {
    await audio.play()
    if (generation !== playbackGeneration) return { ok: false, reason: "cancelled" }
    return { ok: true }
  } catch (error) {
    const reason = error instanceof Error && error.message
      ? error.message
      : "audio_playback_blocked"
    cleanup()
    const fallback = await speakWithConfiguredFallback(
      text,
      generation,
      options,
      voiceStatus.fallbackPolicy,
    )
    return fallback || {
      ok: false,
      reason: `流式教师语音播放失败，请重试。${reason ? `（${reason}）` : ""}`,
    }
  }
}

function splitTeacherVoiceChunks(text: string): string[] {
  const pieces = text.match(/[^。！？!?；;\n]+[。！？!?；;\n]?/g) || [text]
  const chunks: string[] = []
  let pending = ""

  const flushPending = () => {
    const value = pending.trim()
    if (value) chunks.push(value)
    pending = ""
  }

  for (const piece of pieces) {
    let remaining = piece.trim()
    if (!remaining) continue
    while (remaining.length > PROGRESSIVE_CHUNK_MAX_CHARS) {
      const head = remaining.slice(0, PROGRESSIVE_CHUNK_MAX_CHARS + 1)
      const punctuation = Math.max(
        head.lastIndexOf("，"),
        head.lastIndexOf(","),
        head.lastIndexOf("、"),
        head.lastIndexOf(" "),
      )
      const cut = punctuation >= Math.floor(PROGRESSIVE_CHUNK_MAX_CHARS * 0.55)
        ? punctuation + 1
        : PROGRESSIVE_CHUNK_MAX_CHARS
      const part = remaining.slice(0, cut).trim()
      if (part) chunks.push(part)
      remaining = remaining.slice(cut).trim()
    }
    if (!remaining) continue
    if (
      pending
      && pending.length + remaining.length > PROGRESSIVE_CHUNK_MAX_CHARS
    ) {
      flushPending()
    }
    pending += remaining
  }
  flushPending()
  return chunks.length ? chunks : [text.trim()]
}

type QueuedAudioResult = LocalTeacherVoiceResult & { started?: boolean }

function playQueuedAudioBlob(
  blob: Blob,
  generation: number,
  onStarted: () => void,
): Promise<QueuedAudioResult> {
  return new Promise(resolve => {
    if (generation !== playbackGeneration) {
      resolve({ ok: false, reason: "cancelled" })
      return
    }
    const objectUrl = URL.createObjectURL(blob)
    const audio = new Audio(objectUrl)
    let settled = false
    let started = false
    const cleanup = () => {
      activeRateUnsubscribe?.()
      activeRateUnsubscribe = null
      if (activeAudio === audio) activeAudio = null
      if (activeObjectUrl === objectUrl) {
        URL.revokeObjectURL(objectUrl)
        activeObjectUrl = ""
      }
    }
    const finish = (result: QueuedAudioResult) => {
      if (settled) return
      settled = true
      if (activePlaybackCancel === cancel) activePlaybackCancel = null
      cleanup()
      resolve(result)
    }
    const cancel = () => finish({ ok: false, reason: "cancelled" })
    const notifyStarted = () => {
      if (started || generation !== playbackGeneration) return
      started = true
      onStarted()
    }

    activeAudio = audio
    activeObjectUrl = objectUrl
    activePlaybackCancel = cancel
    audio.preload = "auto"
    const applyPlaybackRate = () => {
      const rate = getVoicePlaybackRate()
      audio.playbackRate = rate
      audio.defaultPlaybackRate = rate
      audio.preservesPitch = true
      ;(audio as HTMLAudioElement & { webkitPreservesPitch?: boolean }).webkitPreservesPitch = true
    }
    applyPlaybackRate()
    activeRateUnsubscribe = subscribeVoicePlaybackRate(applyPlaybackRate)
    audio.onplaying = notifyStarted
    audio.onended = () => finish({ ok: true, started })
    audio.onerror = () => finish({ ok: false, reason: "audio_playback_failed", started })
    void audio.play()
      .then(notifyStarted)
      .catch(() => finish({ ok: false, reason: "audio_playback_blocked", started }))
  })
}

async function playQueuedTeacherVoice(
  text: string,
  generation: number,
  options: LocalTeacherVoiceOptions,
  voiceStatus: TeacherVoiceStatus,
  activePack: NonNullable<TeacherVoiceStatus["activePack"]>,
  coldStart: boolean,
): Promise<LocalTeacherVoiceResult> {
  const chunks = splitTeacherVoiceChunks(text)
  let firstPlaybackResolved = false
  let resolveFirstPlayback!: (result: LocalTeacherVoiceResult) => void
  const firstPlayback = new Promise<LocalTeacherVoiceResult>(resolve => {
    resolveFirstPlayback = resolve
  })
  const resolveFirst = (result: LocalTeacherVoiceResult) => {
    if (firstPlaybackResolved) return
    firstPlaybackResolved = true
    resolveFirstPlayback(result)
  }

  const synthesizeChunk = async (chunk: string, chunkIndex: number): Promise<Blob | null> => {
    if (generation !== playbackGeneration) {
      resolveFirst({ ok: false, reason: "cancelled" })
      return null
    }
    const cacheKey = JSON.stringify([
      activePack.packId,
      activePack.version,
      chunk,
      options.style || "",
      options.replyKind || "",
    ])
    const cached = voiceCache.get(cacheKey)
    if (cached) return cached

    let synthesisTimedOut = false
    const controller = new AbortController()
    activeRequest = controller
    const timeout = window.setTimeout(() => {
      synthesisTimedOut = true
      controller.abort()
    }, chunkIndex === 0 && coldStart
      ? COLD_START_SYNTHESIS_TIMEOUT_MS
      : READY_SYNTHESIS_TIMEOUT_MS)
    let response: Response
    try {
      response = await fetch("/api/casual-chat/voice/synthesize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: chunk,
          ...(options.style ? { style: options.style } : {}),
          ...(options.replyKind ? { reply_kind: options.replyKind } : {}),
        }),
        signal: controller.signal,
      })
    } catch (error) {
      if (generation !== playbackGeneration) {
        resolveFirst({ ok: false, reason: "cancelled" })
        return null
      }
      if (error instanceof DOMException && error.name === "AbortError") {
        resolveFirst({
          ok: false,
          reason: synthesisTimedOut
            ? chunkIndex === 0 && coldStart
              ? "本地语音模型首次启动超时，请重试；若持续超时请前往设置修复声包。"
              : "离线教师声包合成超时，请重试；若持续超时请前往设置修复声包。"
            : "cancelled",
        })
        return null
      }
      const reason = "离线教师声包暂不可用，请前往“设置与安全”修复声包后重试。"
      if (chunkIndex === 0 && voiceStatus.fallbackPolicy === "system_voice") {
        const fallback = speakWithBrowserFallback(text, generation, options)
        if (fallback.ok) {
          resolveFirst(fallback)
          return null
        }
      }
      options.onError?.(reason)
      resolveFirst({ ok: false, reason })
      return null
    } finally {
      window.clearTimeout(timeout)
      if (activeRequest === controller) activeRequest = null
    }
    if (!response.ok) {
      const reason = await voiceSynthesisFailureReason(
        response,
        "离线教师语音合成失败，请稍后重试。",
      )
      if (chunkIndex === 0 && voiceStatus.fallbackPolicy === "system_voice") {
        const fallback = speakWithBrowserFallback(text, generation, options)
        if (fallback.ok) {
          resolveFirst(fallback)
          return null
        }
      }
      options.onError?.(reason)
      resolveFirst({ ok: false, reason })
      return null
    }
    const blob = await response.blob()
    if (!blob.size || !blob.type.toLowerCase().includes("audio")) {
      const reason = "离线教师声包返回了无效音频，请前往“设置与安全”修复声包后重试。"
      options.onError?.(reason)
      resolveFirst({ ok: false, reason })
      return null
    }
    cacheVoice(cacheKey, blob)
    return blob
  }

  const runQueue = async () => {
    let blob: Blob | null = null
    let nextBlob: Promise<Blob | null> | null = null
    for (const [chunkIndex] of chunks.entries()) {
      if (chunkIndex === 0) {
        blob = await synthesizeChunk(chunks[chunkIndex], chunkIndex)
      } else {
        blob = await (nextBlob || synthesizeChunk(chunks[chunkIndex], chunkIndex))
      }
      if (!blob) return
      // Synthesize only one phrase ahead.  This keeps GPT-SoVITS serial while
      // the browser plays the current phrase, so playback does not wait at a
      // chunk boundary and two model requests never compete for the runtime.
      nextBlob = chunkIndex + 1 < chunks.length
        ? synthesizeChunk(chunks[chunkIndex + 1], chunkIndex + 1)
        : null
      const playback = await playQueuedAudioBlob(
        blob,
        generation,
        () => {
          if (chunkIndex === 0) {
            options.onStart?.()
            resolveFirst({ ok: true })
          }
        },
      )
      if (!playback.ok) {
        if (playback.reason === "cancelled" || generation !== playbackGeneration) {
          resolveFirst({ ok: false, reason: "cancelled" })
          return
        }
        const reason = "离线教师声包播放失败，请修复声包后重试。"
        options.onError?.(reason)
        resolveFirst({ ok: false, reason })
        return
      }
    }
    if (generation === playbackGeneration) {
      options.onEnd?.()
      resolveFirst({ ok: true })
    } else {
      resolveFirst({ ok: false, reason: "cancelled" })
    }
  }

  void runQueue().catch(() => {
    if (generation !== playbackGeneration) {
      resolveFirst({ ok: false, reason: "cancelled" })
      return
    }
    const reason = "离线教师声包暂不可用，请前往“设置与安全”修复声包后重试。"
    options.onError?.(reason)
    resolveFirst({ ok: false, reason })
  })
  return firstPlayback
}

function runtimeNeedsColdStart(state: string): boolean {
  return !["ready", "prepared", "busy", "synthesizing"].includes(
    String(state || "").trim().toLowerCase(),
  )
}

type TeacherVoiceAvailability = {
  ok: boolean
  engine: string
  profile: string
  release: string
}

async function getTeacherVoiceAvailability(): Promise<TeacherVoiceAvailability | null> {
  try {
    const response = await fetch("/api/casual-chat/voice/warm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    })
    if (!response.ok) return null
    const raw = await response.json() as Record<string, unknown>
    return {
      ok: raw.ok === true,
      engine: typeof raw.engine === "string" ? raw.engine : "",
      profile: typeof raw.profile === "string" ? raw.profile : "",
      release: typeof raw.release === "string" ? raw.release : "",
    }
  } catch {
    return null
  }
}

export async function speakLocalTeacherVoice(
  text: string,
  options: LocalTeacherVoiceOptions = {},
): Promise<LocalTeacherVoiceResult> {
  stopLocalTeacherVoice()
  const generation = playbackGeneration
  const readableText = normalizeTeacherVoiceText(text)
  let synthesisTimedOut = false
  if (!readableText) return { ok: false, reason: "empty_voice_text" }
  let remoteVoice: TeacherVoiceAvailability | null = null
  let voiceStatus: TeacherVoiceStatus
  try {
    voiceStatus = await getTeacherVoiceStatus()
  } catch {
    remoteVoice = await getTeacherVoiceAvailability()
    if (generation !== playbackGeneration) return { ok: false, reason: "cancelled" }
    if (remoteVoice?.ok && remoteVoice.engine === "minimax") {
      voiceStatus = {
        schema: "mentorflow.teacher_voice.remote.v1",
        fallbackPolicy: "off",
        activePack: null,
        runtime: { state: "ready", startsModel: false, adapter: "minimax" },
        packs: [],
      }
    } else {
      const fallback = await speakWithConfiguredFallback(readableText, generation, options)
      return fallback || {
        ok: false,
        reason: "无法读取教师语音状态，请稍后重试。",
      }
    }
  }
  if (generation !== playbackGeneration) return { ok: false, reason: "cancelled" }
  if ((!voiceStatus.activePack?.packId || !voiceStatus.activePack.version) && !remoteVoice) {
    remoteVoice = await getTeacherVoiceAvailability()
    if (generation !== playbackGeneration) return { ok: false, reason: "cancelled" }
  }
  if (
    (!voiceStatus.activePack?.packId || !voiceStatus.activePack.version)
    && !(remoteVoice?.ok && remoteVoice.engine === "minimax")
  ) {
    const fallback = await speakWithConfiguredFallback(
      readableText,
      generation,
      options,
      voiceStatus.fallbackPolicy,
    )
    if (fallback) return fallback
    const relevantState = voiceStatus.packs.find(pack => pack.state !== "not_installed")?.state
      || voiceStatus.runtime.state
    return { ok: false, reason: unavailablePackReason(relevantState) }
  }
  const activePack = voiceStatus.activePack
  const isRemoteVoice = Boolean(remoteVoice?.ok && remoteVoice.engine === "minimax")
  const coldStart = isRemoteVoice ? false : runtimeNeedsColdStart(voiceStatus.runtime.state)
  options.onPreparing?.(coldStart ? "starting" : "synthesizing")
  if (!isRemoteVoice && readableText.length >= PROGRESSIVE_STREAM_THRESHOLD_CHARS) {
    if (!activePack) {
      return { ok: false, reason: "当前没有可用的离线教师声包。" }
    }
    // The GPT-SoVITS runtime's native stream starts with a WAV header whose
    // data length is zero, then appends raw PCM. Chromium may decode that as
    // clicks, breath-like noise, or a stuck player. Queue complete WAVs
    // instead: the first phrase starts as soon as it is ready, and the next
    // phrase is prefetched during playback without using raw PCM streaming.
    return playQueuedTeacherVoice(
      readableText,
      generation,
      options,
      voiceStatus,
      activePack,
      coldStart,
    )
  }
  const cacheKey = JSON.stringify([
    isRemoteVoice ? remoteVoice?.release || "minimax" : activePack?.packId,
    isRemoteVoice ? remoteVoice?.profile || "remote" : activePack?.version,
    readableText,
    options.style || "",
    options.replyKind || "",
  ])
  try {
    let blob = voiceCache.get(cacheKey)
    if (!blob) {
      const controller = new AbortController()
      activeRequest = controller
      const timeout = window.setTimeout(() => {
        synthesisTimedOut = true
        controller.abort()
      }, coldStart ? COLD_START_SYNTHESIS_TIMEOUT_MS : READY_SYNTHESIS_TIMEOUT_MS)
      let response: Response
      try {
        response = await fetch("/api/casual-chat/voice/synthesize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: readableText,
            ...(options.style ? { style: options.style } : {}),
            ...(options.replyKind ? { reply_kind: options.replyKind } : {}),
          }),
          signal: controller.signal,
        })
      } finally {
        window.clearTimeout(timeout)
        if (activeRequest === controller) activeRequest = null
      }
      if (!response.ok) {
        const fallback = await speakWithConfiguredFallback(
          readableText,
          generation,
          options,
          voiceStatus.fallbackPolicy,
        )
        if (fallback) return fallback
        return {
          ok: false,
          reason: await voiceSynthesisFailureReason(
            response,
            isRemoteVoice
              ? "云端教师语音合成失败，请稍后重试。"
              : "离线教师语音合成失败，请稍后重试。",
          ),
        }
      }
      blob = await response.blob()
      if (!blob.size || !blob.type.toLowerCase().includes("audio")) {
        const fallback = await speakWithConfiguredFallback(
          readableText,
          generation,
          options,
          voiceStatus.fallbackPolicy,
        )
        return fallback || {
          ok: false,
          reason: isRemoteVoice
            ? "云端教师语音返回了无效音频，请稍后重试。"
            : "离线教师声包返回了无效音频，请前往“设置与安全”修复声包后重试。",
        }
      }
      cacheVoice(cacheKey, blob)
    }
    if (generation !== playbackGeneration) return { ok: false, reason: "cancelled" }
    const objectUrl = URL.createObjectURL(blob)
    const audio = new Audio(objectUrl)
    activeObjectUrl = objectUrl
    activeAudio = audio
    audio.preload = "auto"
    const applyPlaybackRate = () => {
      const rate = getVoicePlaybackRate()
      audio.playbackRate = rate
      audio.defaultPlaybackRate = rate
      audio.preservesPitch = true
      ;(audio as HTMLAudioElement & { webkitPreservesPitch?: boolean }).webkitPreservesPitch = true
    }
    applyPlaybackRate()
    activeRateUnsubscribe = subscribeVoicePlaybackRate(applyPlaybackRate)
    audio.onplaying = () => {
      if (generation === playbackGeneration) options.onStart?.()
    }
    audio.onended = () => {
      if (activeAudio !== audio) return
      activeRateUnsubscribe?.()
      activeRateUnsubscribe = null
      activeAudio = null
      releaseObjectUrl()
      if (generation === playbackGeneration) options.onEnd?.()
    }
    audio.onerror = () => {
      if (activeAudio !== audio) return
      activeRateUnsubscribe?.()
      activeRateUnsubscribe = null
      activeAudio = null
      releaseObjectUrl()
      if (generation !== playbackGeneration) return
      if (voiceStatus.fallbackPolicy === "system_voice") {
        const fallback = speakWithBrowserFallback(readableText, generation, options)
        if (fallback.ok) return
      }
      options.onError?.(
        isRemoteVoice
          ? "云端教师语音播放失败，请稍后重试。"
          : "离线教师声包播放失败，请修复声包后重试。",
      )
    }
    try {
      await audio.play()
      return { ok: true }
    } catch (error) {
      const reason = error instanceof Error && error.message
        ? error.message
        : "audio_playback_blocked"
      if (activeAudio === audio) {
        activeRateUnsubscribe?.()
        activeRateUnsubscribe = null
        activeAudio = null
        releaseObjectUrl()
      }
      const fallback = await speakWithConfiguredFallback(
        readableText,
        generation,
        options,
        voiceStatus.fallbackPolicy,
      )
      return fallback || {
        ok: false,
        reason: `音频播放失败，请重试；若持续失败请修复声包。${reason ? `（${reason}）` : ""}`,
      }
    }
  } catch (error) {
    if (generation !== playbackGeneration) return { ok: false, reason: "cancelled" }
    if (synthesisTimedOut) {
      const fallback = await speakWithConfiguredFallback(
        readableText,
        generation,
        options,
        voiceStatus.fallbackPolicy,
      )
      return fallback || {
        ok: false,
        reason: coldStart
          ? "本地语音模型首次启动超时，请重试；若持续超时请前往设置修复声包。"
          : "离线教师声包合成超时，请重试；若持续超时请前往设置修复声包。",
      }
    }
    if (error instanceof DOMException && error.name === "AbortError") {
      return { ok: false, reason: "cancelled" }
    }
    const fallback = await speakWithConfiguredFallback(
      readableText,
      generation,
      options,
      voiceStatus.fallbackPolicy,
    )
    return fallback || {
      ok: false,
      reason: "离线教师声包暂不可用，请前往“设置与安全”修复声包后重试。",
    }
  }
}
