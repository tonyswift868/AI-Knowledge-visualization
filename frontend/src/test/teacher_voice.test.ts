import { afterEach, describe, expect, it, vi } from "vitest"
import {
  pickPreferredBrowserVoice,
  speakBrowser,
  stopBrowserVoice,
} from "../features/voice/browserVoice"
import {
  normalizeTeacherVoiceText,
  resetLocalTeacherVoiceCache,
  speakLocalTeacherVoice,
  stopLocalTeacherVoice,
  warmLocalTeacherVoice,
} from "../features/voice/localTeacherVoice"
import {
  getVoicePlaybackRate,
  setVoicePlaybackRate,
} from "../features/voice/voicePlaybackRate"

type TestUtterance = {
  text: string
  lang: string
  rate: number
  pitch: number
  volume: number
  voice?: SpeechSynthesisVoice
  onstart: (() => void) | null
  onend: (() => void) | null
  onerror: ((event: { error?: string }) => void) | null
}

function voice(name: string, lang = "zh-CN", isDefault = false): SpeechSynthesisVoice {
  return {
    default: isDefault,
    lang,
    localService: false,
    name,
    voiceURI: name,
  }
}

function installSpeechSynthesis(voices: SpeechSynthesisVoice[]) {
  const speak = vi.fn((utterance: TestUtterance) => utterance.onstart?.())
  const synthesis = {
    cancel: vi.fn(),
    getVoices: vi.fn(() => voices),
    resume: vi.fn(),
    speak,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }
  class MockUtterance implements TestUtterance {
    lang = ""
    rate = 1
    pitch = 1
    volume = 1
    voice?: SpeechSynthesisVoice
    onstart: (() => void) | null = null
    onend: (() => void) | null = null
    onerror: ((event: { error?: string }) => void) | null = null
    constructor(public text: string) {}
  }
  Object.defineProperty(window, "speechSynthesis", { configurable: true, value: synthesis })
  vi.stubGlobal("SpeechSynthesisUtterance", MockUtterance)
  return { speak, synthesis }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function voiceStatus(
  active: boolean,
  fallbackPolicy: "off" | "system_voice" = "off",
  runtimeState = active ? "ready" : "stopped",
) {
  const pack = {
    pack_id: "mentorflow-original-iris",
    version: "1.0.0",
    display_name: "艾莉丝原创教师声包",
    state: active ? "active" : "not_installed",
    styles: ["lively", "relaxed"],
    runtime_adapter: "qwen3_tts",
    archive_bytes: 320_000_000,
  }
  return {
    schema: "mentorflow.teacher_voice.status.v1",
    fallback_policy: fallbackPolicy,
    active_pack: active ? pack : null,
    runtime: { state: runtimeState, starts_model: runtimeState === "starting" },
    packs: [pack],
  }
}

function installAudio(play = vi.fn(async function (this: { onplaying?: () => void }) { this.onplaying?.() })) {
  class MockAudio {
    onplaying: (() => void) | null = null
    onended: (() => void) | null = null
    onerror: (() => void) | null = null
    preload = ""
    constructor(public src = "") {}
    play = play
    pause = vi.fn()
    load = vi.fn()
    removeAttribute = vi.fn()
  }
  vi.stubGlobal("Audio", MockAudio)
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:teacher-voice"),
    revokeObjectURL: vi.fn(),
  })
  return play
}

afterEach(() => {
  resetLocalTeacherVoiceCache()
  stopBrowserVoice()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  Reflect.deleteProperty(window, "speechSynthesis")
  localStorage.removeItem("mentorflow_voice_playback_rate_v1")
})

describe("teacher voice", () => {
  it("uses the saved global rate for each new system utterance", async () => {
    vi.useFakeTimers()
    const { speak } = installSpeechSynthesis([voice("System Chinese Voice")])

    setVoicePlaybackRate(0.65)
    speakBrowser("慢一点")
    await vi.runAllTimersAsync()
    expect(speak.mock.calls[0][0].rate).toBe(0.65)

    setVoicePlaybackRate(1.2)
    speakBrowser("快一点")
    await vi.runAllTimersAsync()
    expect(speak.mock.calls[1][0].rate).toBe(1.2)
  })

  it("applies and live-updates the global rate for cached offline audio without resynthesis", async () => {
    const audios: Array<{
      playbackRate: number
      defaultPlaybackRate: number
      preservesPitch: boolean
      webkitPreservesPitch?: boolean
    }> = []
    class RateAudio {
      onplaying: (() => void) | null = null
      onended: (() => void) | null = null
      onerror: (() => void) | null = null
      preload = ""
      playbackRate = 1
      defaultPlaybackRate = 1
      preservesPitch = false
      webkitPreservesPitch?: boolean
      constructor(public src = "") { audios.push(this) }
      play = vi.fn(async () => undefined)
      pause = vi.fn()
      load = vi.fn()
      removeAttribute = vi.fn()
    }
    vi.stubGlobal("Audio", RateAudio)
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:teacher-rate"),
      revokeObjectURL: vi.fn(),
    })
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith("/api/teacher-voice/status")) return jsonResponse(voiceStatus(true))
      if (url.endsWith("/api/casual-chat/voice/synthesize")) {
        return new Response(new Blob(["voice"], { type: "audio/wav" }), {
          status: 200,
          headers: { "Content-Type": "audio/wav" },
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    setVoicePlaybackRate(0.65)
    await expect(speakLocalTeacherVoice("缓存也要变速")).resolves.toEqual({ ok: true })
    expect(audios[0]).toMatchObject({
      playbackRate: 0.65,
      defaultPlaybackRate: 0.65,
      preservesPitch: true,
      webkitPreservesPitch: true,
    })

    setVoicePlaybackRate(0.8)
    expect(audios[0].playbackRate).toBe(0.8)
    stopLocalTeacherVoice()
    await expect(speakLocalTeacherVoice("缓存也要变速")).resolves.toEqual({ ok: true })
    expect(audios[1].playbackRate).toBe(0.8)
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/voice/synthesize"))).toHaveLength(1)
  })

  it("does not start a stale system fallback after playback is stopped", async () => {
    const { speak } = installSpeechSynthesis([voice("System Chinese Voice")])
    let releaseFailure!: () => void
    const failedSynthesis = new Promise<Response>(resolve => {
      releaseFailure = () => resolve(new Response("failed", { status: 503 }))
    })
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith("/api/teacher-voice/status")) {
        return jsonResponse(voiceStatus(true, "system_voice"))
      }
      if (url.endsWith("/api/casual-chat/voice/synthesize")) return failedSynthesis
      throw new Error(`unexpected request: ${url}`)
    }))

    const pending = speakLocalTeacherVoice("这条旧语音不能回退播放")
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    stopLocalTeacherVoice()
    releaseFailure()

    await expect(pending).resolves.toEqual({ ok: false, reason: "cancelled" })
    expect(speak).not.toHaveBeenCalled()
  })

  it("normalizes invalid and out-of-range persisted rates", () => {
    localStorage.setItem("mentorflow_voice_playback_rate_v1", "not-a-number")
    expect(getVoicePlaybackRate()).toBe(1)
    expect(setVoicePlaybackRate(99)).toBe(1.5)
    expect(setVoicePlaybackRate(-2)).toBe(0.4)
  })

  it("keeps Markdown code out of the teacher voice and speaks common code tokens naturally", () => {
    const normalized = normalizeTeacherVoiceText(
      "先看 `ListNode`，`fast.next.next` 表示快指针。\n\n```python\nreturn None\n```",
    )

    expect(normalized).toContain("链表节点")
    expect(normalized).toContain("快指针连续向前两步")
    expect(normalized).not.toContain("```")
    expect(normalized).not.toContain("fast.next.next")
  })

  it("cancels the pending voices listener and fallback timer before they can speak old text", async () => {
    vi.useFakeTimers()
    const { speak, synthesis } = installSpeechSynthesis([])

    expect(speakBrowser("这句旧文本不应播放").ok).toBe(true)
    expect(synthesis.addEventListener).toHaveBeenCalledWith("voiceschanged", expect.any(Function))
    stopBrowserVoice()
    await vi.runAllTimersAsync()

    expect(speak).not.toHaveBeenCalled()
    expect(synthesis.removeEventListener).toHaveBeenCalledWith("voiceschanged", expect.any(Function))
  })

  it("plays only the newest text when browser speech is switched rapidly", async () => {
    vi.useFakeTimers()
    const { speak } = installSpeechSynthesis([voice("System Chinese Voice")])

    speakBrowser("旧文本")
    speakBrowser("最新文本")
    await vi.runAllTimersAsync()

    expect(speak).toHaveBeenCalledTimes(1)
    expect(speak.mock.calls[0][0].text).toBe("最新文本")
  })

  it("can select the configured compatibility voice after fallback is explicitly enabled", () => {
    installSpeechSynthesis([
      voice("Microsoft Yunxi Online (Natural) - Chinese (Mainland)"),
      voice("Google 普通话（中国大陆）", "zh-CN", true),
      voice("Microsoft Xiaoxiao Online (Natural) - Chinese (Mainland)"),
    ])

    expect(pickPreferredBrowserVoice("zh-CN", "xiaoxiao")?.name).toContain("Xiaoxiao")
  })

  it("never warms a model or sends a network request when the page opens", async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)

    await expect(warmLocalTeacherVoice()).resolves.toBe(true)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("does not synthesize or silently use browser speech without an active pack", async () => {
    const { speak } = installSpeechSynthesis([
      voice("Microsoft Xiaoxiao Online (Natural) - Chinese (Mainland)"),
    ])
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      if (String(input).endsWith("/api/teacher-voice/status")) return jsonResponse(voiceStatus(false))
      if (String(input).endsWith("/api/casual-chat/voice/warm")) {
        return jsonResponse({ ok: false, warmed: false, engine: "voice-pack" })
      }
      throw new Error(`unexpected request: ${input}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    const result = await speakLocalTeacherVoice("我们先看这条链表。")

    expect(result).toMatchObject({ ok: false })
    expect(result.reason).toMatch(/安装.*声包/)
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/voice/synthesize"))).toBe(false)
    expect(speak).not.toHaveBeenCalled()
  })

  it("uses the server-owned MiniMax voice without asking the user for an API key", async () => {
    const play = installAudio()
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/api/teacher-voice/status")) return jsonResponse(voiceStatus(false))
      if (url.endsWith("/api/casual-chat/voice/warm")) {
        return jsonResponse({
          ok: true,
          warmed: false,
          engine: "minimax",
          profile: "tsubaki-private-v1",
          release: "minimax:speech-2.8-hd-v1",
        })
      }
      if (url.endsWith("/api/casual-chat/voice/synthesize")) {
        expect(init?.headers).toEqual({ "Content-Type": "application/json" })
        expect(String(init?.body)).not.toMatch(/api[_-]?key|Bearer/i)
        return new Response(new Blob(["ID3-cloud-voice"], { type: "audio/mpeg" }), {
          status: 200,
          headers: { "Content-Type": "audio/mpeg" },
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    await expect(speakLocalTeacherVoice("这是云端克隆声线。", { style: "relaxed" })).resolves.toEqual({ ok: true })

    expect(play).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/teacher-voice/status",
      "/api/casual-chat/voice/warm",
      "/api/casual-chat/voice/synthesize",
    ])
  })

  it("does not synthesize when active pack identity lacks an exact version", async () => {
    const completeStatus = voiceStatus(true)
    const { version: _omittedVersion, ...activePackWithoutVersion } = completeStatus.active_pack!
    const incompleteStatus = { ...completeStatus, active_pack: activePackWithoutVersion }
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/api/teacher-voice/status")) return jsonResponse(incompleteStatus)
      throw new Error(`unexpected request: ${input}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    const result = await speakLocalTeacherVoice("缺少精确版本时不要合成。")

    expect(result.ok).toBe(false)
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/voice/synthesize"))).toBe(false)
  })

  it("uses browser speech only when the compatibility fallback is explicitly enabled", async () => {
    const { speak } = installSpeechSynthesis([
      voice("Microsoft Xiaoxiao Online (Natural) - Chinese (Mainland)"),
    ])
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/api/teacher-voice/status")) {
        return jsonResponse(voiceStatus(false, "system_voice"))
      }
      throw new Error(`unexpected request: ${input}`)
    }))
    const onStart = vi.fn()

    await expect(speakLocalTeacherVoice("我们先看这条链表。", { onStart })).resolves.toEqual({ ok: true })
    await vi.waitFor(() => expect(speak).toHaveBeenCalledTimes(1))
    expect(onStart).toHaveBeenCalledTimes(1)
  })

  it("synthesizes and plays only after an active offline pack is confirmed", async () => {
    const play = installAudio()
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/api/teacher-voice/status")) return jsonResponse(voiceStatus(true))
      if (url.endsWith("/api/casual-chat/voice/synthesize")) {
        return new Response(new Blob(["RIFF-voice"], { type: "audio/wav" }), {
          status: 200,
          headers: { "Content-Type": "audio/wav" },
        })
      }
      throw new Error(`unexpected request: ${input}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    await expect(speakLocalTeacherVoice("文字已经完整显示。", { replyKind: "lesson" })).resolves.toEqual({ ok: true })

    expect(play).toHaveBeenCalledTimes(1)
    const synthCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/voice/synthesize"))
    expect(JSON.parse(String(synthCall?.[1]?.body))).toMatchObject({
      text: "文字已经完整显示。",
      reply_kind: "lesson",
    })
  })

  it("starts long offline replies with complete WAV chunks instead of a raw stream", async () => {
    const audios: Array<{
      onended: (() => void) | null
      onerror: (() => void) | null
      src: string
    }> = []
    class QueueAudio {
      onplaying: (() => void) | null = null
      onended: (() => void) | null = null
      onerror: (() => void) | null = null
      preload = ""
      playbackRate = 1
      defaultPlaybackRate = 1
      preservesPitch = false
      constructor(public src = "") { audios.push(this) }
      play = vi.fn(async () => this.onplaying?.())
      pause = vi.fn()
      load = vi.fn()
      removeAttribute = vi.fn()
    }
    vi.stubGlobal("Audio", QueueAudio)
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/api/teacher-voice/status")) return jsonResponse(voiceStatus(true))
      if (url.endsWith("/api/casual-chat/voice/synthesize")) {
        return new Response(new Blob(["RIFF-voice"], { type: "audio/wav" }), {
          status: 200,
          headers: { "Content-Type": "audio/wav" },
        })
      }
      throw new Error(`unexpected request: ${url} ${String(init?.body || "")}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    const text = Array.from(
      { length: 20 },
      (_, index) => `这是第${index + 1}段需要尽快开始播放的长教师回复。`,
    ).join("")
    const onStart = vi.fn()
    const onEnd = vi.fn()
    const resultPromise = speakLocalTeacherVoice(text, {
      replyKind: "teaching.explain",
      onStart,
      onEnd,
    })
    await expect(resultPromise).resolves.toEqual({ ok: true })

    const synthesisCalls = () => fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/voice/synthesize"))
    // The first complete WAV starts playback without waiting for the next
    // phrase; the next request is prefetched in parallel with that playback.
    expect(synthesisCalls()).toHaveLength(2)
    expect(String(fetchMock.mock.calls[1]?.[1]?.body)).toContain("teaching.explain")
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body)).text.length).toBeLessThan(text.length)
    expect(String(fetchMock.mock.calls[1]?.[0])).not.toContain("/voice/stream")
    expect(audios).toHaveLength(1)
    expect(onStart).toHaveBeenCalledTimes(1)

    let ended = 0
    while (!onEnd.mock.calls.length && ended < 12) {
      await vi.waitFor(() => expect(audios.length).toBeGreaterThan(ended))
      audios[ended].onended?.()
      ended += 1
      if (!onEnd.mock.calls.length) {
        await vi.waitFor(() => expect(
          audios.length > ended || onEnd.mock.calls.length > 0,
        ).toBe(true))
      }
    }
    expect(audios.length).toBeGreaterThan(1)
    expect(synthesisCalls().length).toBeGreaterThan(1)
    expect(onEnd).toHaveBeenCalledTimes(1)
  })

  it("allows a cold local model more than 45 seconds and reports the startup phase", async () => {
    vi.useFakeTimers()
    let synthesisSignal: AbortSignal | undefined
    const preparing = vi.fn()
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = String(input)
      if (url.endsWith("/api/teacher-voice/status")) {
        return Promise.resolve(jsonResponse(voiceStatus(true, "off", "starting")))
      }
      if (url.endsWith("/api/casual-chat/voice/synthesize")) {
        synthesisSignal = init?.signal || undefined
        return new Promise((_resolve, reject) => {
          const abort = () => reject(new DOMException("aborted", "AbortError"))
          if (synthesisSignal?.aborted) abort()
          else synthesisSignal?.addEventListener("abort", abort, { once: true })
        })
      }
      return Promise.reject(new Error(`unexpected request: ${input}`))
    })
    vi.stubGlobal("fetch", fetchMock)

    const resultPromise = speakLocalTeacherVoice("首次启动可能比较慢。", { onPreparing: preparing })
    await vi.advanceTimersByTimeAsync(0)

    expect(preparing).toHaveBeenCalledWith("starting")
    expect(synthesisSignal?.aborted).toBe(false)
    await vi.advanceTimersByTimeAsync(45_001)
    expect(synthesisSignal?.aborted).toBe(false)
    await vi.advanceTimersByTimeAsync(240_000 - 45_001)

    await expect(resultPromise).resolves.toMatchObject({
      ok: false,
      reason: expect.stringContaining("首次启动超时"),
    })
    expect(synthesisSignal?.aborted).toBe(true)
  })

  it("reports ready-worker synthesis and remains explicitly cancellable", async () => {
    let synthesisSignal: AbortSignal | undefined
    const preparing = vi.fn()
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = String(input)
      if (url.endsWith("/api/teacher-voice/status")) {
        return Promise.resolve(jsonResponse(voiceStatus(true, "off", "ready")))
      }
      if (url.endsWith("/api/casual-chat/voice/synthesize")) {
        synthesisSignal = init?.signal || undefined
        return new Promise((_resolve, reject) => {
          const abort = () => reject(new DOMException("aborted", "AbortError"))
          if (synthesisSignal?.aborted) abort()
          else synthesisSignal?.addEventListener("abort", abort, { once: true })
        })
      }
      return Promise.reject(new Error(`unexpected request: ${input}`))
    }))

    const resultPromise = speakLocalTeacherVoice("模型已就绪，合成另一段文本。", { onPreparing: preparing })
    await vi.waitFor(() => expect(synthesisSignal).toBeDefined())
    expect(preparing).toHaveBeenCalledWith("synthesizing")

    stopLocalTeacherVoice()

    await expect(resultPromise).resolves.toEqual({ ok: false, reason: "cancelled" })
    expect(synthesisSignal?.aborted).toBe(true)
  })

  it("turns a structured synthesis failure into an actionable repair prompt", async () => {
    installSpeechSynthesis([voice("Microsoft Xiaoxiao Online (Natural) - Chinese (Mainland)")])
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith("/api/teacher-voice/status")) return jsonResponse(voiceStatus(true))
      if (url.endsWith("/api/casual-chat/voice/synthesize")) {
        return jsonResponse({
          error: {
            code: "VOICE_PACK_RUNTIME_FAILED",
            message: "voice pack runtime failed",
          },
        }, 503)
      }
      throw new Error(`unexpected request: ${input}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    const result = await speakLocalTeacherVoice("请读这句话。")

    expect(result.ok).toBe(false)
    expect(result.reason).toMatch(/修复.*声包.*重试/)
  })
})
