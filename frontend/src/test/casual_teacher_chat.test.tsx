import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

const localTeacherVoiceHarness = vi.hoisted(() => ({
  speak: null as null | ((
    text: string,
    options?: unknown,
  ) => Promise<{ ok: boolean; reason?: string }>),
}))

vi.mock("../features/voice/localTeacherVoice", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../features/voice/localTeacherVoice")>()
  return {
    ...actual,
    speakLocalTeacherVoice: (text: string, options?: unknown) => (
      localTeacherVoiceHarness.speak
        ? localTeacherVoiceHarness.speak(text, options)
        : actual.speakLocalTeacherVoice(text, options as never)
    ),
  }
})

import { CasualTeacherChat } from "../features/casual-chat/CasualTeacherChat"

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  localTeacherVoiceHarness.speak = null
  localStorage.clear()
})

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function response(body: unknown, ok = true) {
  return {
    ok,
    status: ok ? 200 : 500,
    headers: new Headers({ "Content-Type": "application/json" }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  }
}

const activeVoiceStatus = {
  schema: "mentorflow.teacher_voice.status.v1",
  fallback_policy: "off",
  active_pack: {
    pack_id: "mentorflow-original-iris",
    version: "1.0.0",
    display_name: "艾莉丝原创教师声包",
    state: "active",
    styles: ["lively", "relaxed"],
    runtime_adapter: "qwen3_tts",
  },
  runtime: { state: "ready", starts_model: false },
  packs: [],
}

describe("casual teacher chat", () => {
  it("lets the learner choose a teacher, add a setting, and receive a fast reply", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/active-teaching/teacher-personas")) {
        return response({ items: [{ id: "persona_1", name: "小熊老师", prompt: "活泼", teaching_style: "轻松陪伴", visual_profile_id: "warm", visual_profile_name: "小熊", visual_manifest_url: "", usage_count: 0, is_default: true, created_at: "", updated_at: "" }] })
      }
      if (url.endsWith("/casual-chat/sessions") && init?.method === "POST") return response({ id: "casual_test" })
      if (url.includes("/casual-chat/sessions/casual_test/messages")) return response({ message: "当然可以，我们慢慢聊。", teacher_id: "persona_1", teacher_name: "小熊老师", generation_meta: { surface: "casual_chat" } })
      return response({ messages: [] })
    })
    vi.stubGlobal("fetch", fetchMock)

    render(<CasualTeacherChat workspaceId="ws_test" />)
    expect(screen.getByTestId("casual-teacher-chat")).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId("casual-teacher-option-persona_1")).toBeInTheDocument())
    fireEvent.change(screen.getByTestId("casual-teacher-setting"), { target: { value: "更活泼一点" } })
    fireEvent.change(screen.getByTestId("casual-teacher-input"), { target: { value: "今天想聊聊" } })
    fireEvent.click(screen.getByTestId("casual-teacher-send"))
    await waitFor(() => expect(screen.getAllByText("当然可以，我们慢慢聊。").length).toBeGreaterThan(0))
    const sessionCall = fetchMock.mock.calls.find(([input, init]) => String(input).endsWith("/casual-chat/sessions") && init?.method === "POST")
    expect(JSON.parse(String(sessionCall?.[1]?.body))).toMatchObject({ workspace_id: "ws_test", extra_setting: "更活泼一点" })
  })

  it("does not let a late session response overwrite a setting typed during loading", async () => {
    let resolveSessions!: (value: unknown) => void
    const sessions = new Promise(resolve => { resolveSessions = resolve })
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/active-teaching/teacher-personas")) return response({ items: [] })
      if (url.includes("/casual-chat/sessions?workspace_id=ws_setting_race")) {
        return sessions.then(body => response(body))
      }
      if (url.endsWith("/casual-chat/sessions/session-setting-race") && init?.method === "PATCH") return response({ id: "session-setting-race" })
      if (url.includes("/casual-chat/sessions/session-setting-race/messages")) return response({ message: "我会按你的设定来。" })
      return response({})
    })
    vi.stubGlobal("fetch", fetchMock)

    const { unmount } = render(<CasualTeacherChat workspaceId="ws_setting_race" />)
    const setting = screen.getByTestId("casual-teacher-setting")
    fireEvent.change(setting, { target: { value: "用户专属设定：只用三个短句回答，不要恢复默认设定。" } })
    expect(setting).toHaveValue("用户专属设定：只用三个短句回答，不要恢复默认设定。")

    resolveSessions({ items: [{
      id: "session-setting-race",
      workspace_id: "ws_setting_race",
      teacher_id: "warm",
      teacher_name: "艾莉丝",
      extra_setting: "服务器上的旧默认设定",
      messages: [],
    }] })
    await waitFor(() => expect(setting).toHaveValue("用户专属设定：只用三个短句回答，不要恢复默认设定。"))
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) =>
      String(input).endsWith("/casual-chat/sessions/session-setting-race")
      && init?.method === "PATCH"
      && String(init.body).includes("用户专属设定：只用三个短句回答，不要恢复默认设定。"),
    )).toBe(true))

    // A remount represents a browser refresh: localStorage must win over a
    // stale server session value as well.
    unmount()
    render(<CasualTeacherChat workspaceId="ws_setting_race" />)
    await waitFor(() => expect(screen.getByTestId("casual-teacher-setting")).toHaveValue("用户专属设定：只用三个短句回答，不要恢复默认设定。"))
  })

  it("sends a changed supplemental setting before the next reply", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/active-teaching/teacher-personas")) return response({ items: [] })
      if (url.endsWith("/casual-chat/sessions") && init?.method === "POST") return response({ id: "casual_setting_test" })
      if (url.endsWith("/casual-chat/sessions/casual_setting_test") && init?.method === "PATCH") return response({ id: "casual_setting_test", extra_setting: "新的设定" })
      if (url.includes("/casual-chat/sessions/casual_setting_test/messages")) return response({ message: "收到，我会按新的设定来。" })
      return response({})
    })
    vi.stubGlobal("fetch", fetchMock)

    render(<CasualTeacherChat />)
    fireEvent.change(screen.getByTestId("casual-teacher-setting"), { target: { value: "初始设定" } })
    fireEvent.change(screen.getByTestId("casual-teacher-input"), { target: { value: "第一句" } })
    fireEvent.click(screen.getByTestId("casual-teacher-send"))
    await waitFor(() => expect(screen.getAllByText("收到，我会按新的设定来。").length).toBeGreaterThan(0))

    fireEvent.change(screen.getByTestId("casual-teacher-setting"), { target: { value: "新的设定" } })
    fireEvent.change(screen.getByTestId("casual-teacher-input"), { target: { value: "第二句" } })
    fireEvent.click(screen.getByTestId("casual-teacher-send"))
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input).endsWith("/casual-chat/sessions/casual_setting_test") && init?.method === "PATCH" && String(init.body).includes("新的设定"))).toBe(true))
  })

  it("shows manual voice controls without warming or touching curriculum routes", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => response({ items: [] }))
    vi.stubGlobal("fetch", fetchMock)
    render(<CasualTeacherChat />)
    expect(screen.getByTestId("casual-teacher-portrait")).toBeInTheDocument()
    expect(screen.getByTestId("casual-teacher-voice-mode")).toHaveTextContent("点击喇叭才朗读")
    expect(screen.getByTestId("casual-teacher-voice")).toHaveTextContent("系统配置的教师音色")
    expect(screen.queryByText(/小晓/i)).not.toBeInTheDocument()
    const speakButton = screen.getByTestId("casual-teacher-speak")
    expect(speakButton).toHaveTextContent("听语音")
    expect(speakButton).toHaveAttribute("title", "听语音")
    expect(speakButton).toHaveAttribute("aria-label", "朗读教师回复")
    expect(screen.queryByText(/学习计划已修改/)).not.toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/voice/warm"))).toBe(false)
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/voice/synthesize"))).toBe(false)
  })

  it("restores each role's history and keeps the conversations isolated when switching", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith("/active-teaching/teacher-personas")) return response({ items: [] })
      if (url.includes("/casual-chat/sessions?workspace_id=ws_roles")) {
        return response({ items: [
          {
            id: "session-warm",
            workspace_id: "ws_roles",
            teacher_id: "warm",
            teacher_name: "艾莉丝",
            extra_setting: "温柔一点",
            updated_at: "2026-08-01T08:15:00Z",
            messages: [
              { role: "user", content: "暖角色历史问题" },
              { role: "teacher", content: "暖角色历史回复" },
            ],
          },
          {
            id: "session-researcher",
            workspace_id: "ws_roles",
            teacher_id: "researcher",
            teacher_name: "林老师",
            extra_setting: "保持清晰",
            updated_at: "2026-08-01T09:20:00Z",
            messages: [
              { role: "user", content: "理性角色历史问题" },
              { role: "teacher", content: "理性角色历史回复" },
            ],
          },
        ] })
      }
      return response({})
    })
    vi.stubGlobal("fetch", fetchMock)

    render(<CasualTeacherChat workspaceId="ws_roles" />)
    const messagePane = () => within(document.querySelector('[aria-live="polite"]') as HTMLElement)
    await waitFor(() => expect(messagePane().getByText("暖角色历史回复")).toBeInTheDocument())
    expect(messagePane().queryByText("理性角色历史回复")).not.toBeInTheDocument()
    expect(screen.getByTestId("casual-teacher-setting")).toHaveValue("温柔一点")

    fireEvent.click(screen.getByTestId("casual-teacher-option-researcher"))
    await waitFor(() => expect(messagePane().getByText("理性角色历史回复")).toBeInTheDocument())
    expect(messagePane().queryByText("暖角色历史回复")).not.toBeInTheDocument()
    expect(screen.getByTestId("casual-teacher-setting")).toHaveValue("保持清晰")

    fireEvent.click(screen.getByTestId("casual-teacher-option-warm"))
    await waitFor(() => expect(messagePane().getByText("暖角色历史回复")).toBeInTheDocument())
    expect(messagePane().queryByText("理性角色历史回复")).not.toBeInTheDocument()
  })

  it("requests and starts the preset voice only when the learner clicks listen", async () => {
    const play = vi.fn(async function (this: any) { this.onplaying?.() })
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
      createObjectURL: vi.fn(() => "blob:mentorflow-voice"),
      revokeObjectURL: vi.fn(),
    })
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/active-teaching/teacher-personas")) return response({ items: [] })
      if (url.endsWith("/teacher-voice/status")) return response(activeVoiceStatus)
      if (url.endsWith("/casual-chat/voice/synthesize")) {
        return {
          ok: true,
          blob: async () => new Blob(["RIFF-voice"], { type: "audio/wav" }),
        }
      }
      return response({})
    })
    vi.stubGlobal("fetch", fetchMock)

    render(<CasualTeacherChat />)
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/casual-chat/sessions?workspace_id="))).toBe(true))
    expect(screen.getByText(/嗨，我是艾莉丝/)).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/voice/synthesize"))).toBe(false)
    fireEvent.click(screen.getByTestId("casual-teacher-speak"))
    await waitFor(() => expect(play).toHaveBeenCalledTimes(1))
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/casual-chat/voice/synthesize",
      expect.objectContaining({ method: "POST" }),
    )
    const voiceCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/casual-chat/voice/synthesize"))
    expect(JSON.parse(String(voiceCall?.[1]?.body))).toMatchObject({ reply_kind: "after_class.small_talk" })
    // The play callback and React state update happen in separate microtasks;
    // the status can briefly move through the loading state before onplaying.
    // Accept either the active playback state or its immediate completion state
    // while still proving that the listen flow reached a user-visible status.
    await waitFor(() => expect(screen.getByText(/正在播放教师语音|播放完成/)).toBeInTheDocument())
  })

  it.each([
    ["resolve", "resolve"],
    ["resolve", "reject"],
    ["reject", "resolve"],
    ["reject", "reject"],
  ] as const)(
    "keeps B stoppable after stale A %s and preserves stop status after B %s",
    async (staleSettlement, currentSettlement) => {
      const staleRequest = deferred<{ ok: boolean; reason?: string }>()
      const currentRequest = deferred<{ ok: boolean; reason?: string }>()
      const staleText = `旧请求 A-${staleSettlement}-${currentSettlement}`
      const currentText = `当前请求 B-${staleSettlement}-${currentSettlement}`
      const hydratedSetting = `voice-scope-ready-${staleSettlement}-${currentSettlement}`
      const speak = vi.fn()
        .mockImplementationOnce(() => staleRequest.promise)
        .mockImplementationOnce(() => currentRequest.promise)
      localTeacherVoiceHarness.speak = speak

      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith("/active-teaching/teacher-personas")) return response({ items: [] })
        if (url.includes("/casual-chat/sessions?workspace_id=ws_voice_scope")) {
          return response({ items: [{
            id: "session-voice-scope",
            workspace_id: "ws_voice_scope",
            teacher_id: "warm",
            teacher_name: "艾莉丝",
            extra_setting: hydratedSetting,
            messages: [
              { role: "teacher", content: staleText },
              { role: "teacher", content: currentText },
            ],
          }] })
        }
        return response({})
      })
      vi.stubGlobal("fetch", fetchMock)

      render(<CasualTeacherChat workspaceId="ws_voice_scope" />)
      await screen.findByText(currentText)
      await waitFor(() => {
        expect(screen.getByTestId("casual-teacher-setting")).toHaveValue(
          hydratedSetting,
        )
      })
      const [staleButton, currentButton] = screen.getAllByTestId("casual-teacher-speak")

      fireEvent.click(staleButton)
      await waitFor(() => {
        expect(speak).toHaveBeenCalledTimes(1)
        expect(staleButton).toHaveAttribute("aria-pressed", "true")
      })

      fireEvent.click(currentButton)
      await waitFor(() => {
        expect(speak).toHaveBeenCalledTimes(2)
        expect(staleButton).toHaveAttribute("aria-pressed", "false")
        expect(currentButton).toHaveAttribute("aria-pressed", "true")
        expect(screen.getByRole("status")).toHaveTextContent(
          "正在准备教师语音",
        )
      })

      const staleCallbacks = speak.mock.calls[0]?.[1] as {
        onStart?: () => void;
        onEnd?: () => void;
        onError?: (reason: string) => void;
      }
      const expectCurrentBUnchanged = () => waitFor(() => {
        expect(currentButton).toHaveAttribute("aria-pressed", "true")
        expect(screen.getByRole("status")).toHaveTextContent(
          "正在准备教师语音",
        )
        expect(screen.getByRole("status")).not.toHaveTextContent(
          "stale_A_callback_failed",
        )
      })
      act(() => staleCallbacks.onStart?.())
      await expectCurrentBUnchanged()
      act(() => staleCallbacks.onEnd?.())
      await expectCurrentBUnchanged()
      act(() => staleCallbacks.onError?.("stale_A_callback_failed"))
      await expectCurrentBUnchanged()

      await act(async () => {
        if (staleSettlement === "resolve") {
          staleRequest.resolve({ ok: false, reason: "cancelled" })
          await staleRequest.promise
        } else {
          staleRequest.reject(new Error("stale_A_failed"))
          await staleRequest.promise.catch(() => undefined)
        }
      })

      await waitFor(() => {
        expect(currentButton).toHaveAttribute("aria-pressed", "true")
        expect(currentButton).toHaveAttribute("aria-label", "停止朗读教师回复")
        expect(currentButton).toHaveAttribute("title", "停止朗读")
        expect(screen.getByRole("status")).toHaveTextContent(
          "正在准备教师语音",
        )
      })

      fireEvent.click(currentButton)
      await waitFor(() => {
        expect(currentButton).toHaveAttribute("aria-pressed", "false")
        expect(currentButton).toHaveTextContent("听语音")
        expect(screen.getByRole("status")).toHaveTextContent("已停止朗读")
      })

      await act(async () => {
        if (currentSettlement === "resolve") {
          currentRequest.resolve({ ok: false, reason: "late_B_result" })
          await currentRequest.promise
        } else {
          currentRequest.reject(new Error("late_B_failed"))
          await currentRequest.promise.catch(() => undefined)
        }
      })
      await waitFor(() => {
        expect(currentButton).toHaveAttribute("aria-pressed", "false")
        expect(currentButton).toHaveTextContent("听语音")
        expect(screen.getByRole("status")).toHaveTextContent("已停止朗读")
        expect(screen.getByRole("status")).not.toHaveTextContent(
          /late_B_result|late_B_failed/,
        )
      })
    },
  )

  it("surfaces playback rejection and releases the temporary audio URL", async () => {
    class MockAudio {
      onplaying: (() => void) | null = null
      onended: (() => void) | null = null
      onerror: (() => void) | null = null
      preload = ""
      constructor(public src = "") {}
      play = vi.fn(async () => { throw new Error("playback_blocked") })
      pause = vi.fn()
      load = vi.fn()
      removeAttribute = vi.fn()
    }
    const revokeObjectURL = vi.fn()
    vi.stubGlobal("Audio", MockAudio)
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:mentorflow-blocked-voice"),
      revokeObjectURL,
    })
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith("/active-teaching/teacher-personas")) return response({ items: [] })
      if (url.endsWith("/teacher-voice/status")) return response(activeVoiceStatus)
      if (url.endsWith("/casual-chat/voice/synthesize")) {
        return {
          ok: true,
          blob: async () => new Blob(["RIFF-voice"], { type: "audio/wav" }),
        }
      }
      return response({})
    }))

    render(<CasualTeacherChat />)
    fireEvent.click(screen.getByTestId("casual-teacher-speak"))

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("playback_blocked"))
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mentorflow-blocked-voice")
  })
})
