import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  TeacherLiveClassroom,
  parseTeacherLiveSseFrame,
} from "../features/teacher-live"
import type {
  TeacherLiveApiClient,
  TeacherLiveEvent,
  TeacherLiveSession,
} from "../features/teacher-live"


afterEach(() => {
  cleanup()
})


function session(
  overrides: Partial<TeacherLiveSession> = {},
): TeacherLiveSession {
  return {
    session_id: "live-session-1",
    state: "active",
    provider_mode: "DEMO_FAKE",
    provider_label: "DEMO_FAKE",
    native_provider_streaming: true,
    current_concept: "HashMap threshold",
    next_sequence_number: 1,
    demo_fixture: true,
    ...overrides,
  }
}


function event(
  sequence: number,
  name: TeacherLiveEvent["event"],
  data: Record<string, unknown>,
  providerMode: TeacherLiveEvent["provider_mode"] = "DEMO_FAKE",
): TeacherLiveEvent {
  return {
    sequence,
    event: name,
    session_id: "live-session-1",
    turn_id: "turn-1",
    provider_mode: providerMode,
    data,
  }
}


function client(
  overrides: Partial<TeacherLiveApiClient> = {},
): TeacherLiveApiClient {
  const active = session()
  return {
    createSession: vi.fn().mockResolvedValue(active),
    submitTurn: vi.fn().mockResolvedValue({
      session_id: active.session_id,
      turn_id: "turn-1",
      idempotency_key: "idem-1",
      stream_path: "/api/teacher-live/sessions/live-session-1/turns/turn-1/stream",
      provider_mode: active.provider_mode,
      duplicate: false,
      state: "queued",
    }),
    streamTurn: vi.fn().mockResolvedValue(undefined),
    pauseSession: vi.fn().mockResolvedValue(session({ state: "paused" })),
    resumeSession: vi.fn().mockResolvedValue(active),
    abortSession: vi.fn().mockResolvedValue(session({ state: "aborted" })),
    ...overrides,
  }
}


describe("TeacherLiveClassroom", () => {
  it("appends chunks, exposes citations and keeps diagnostics collapsed", async () => {
    const fake = client({
      streamTurn: vi.fn(async (_path, onEvent) => {
        onEvent(event(1, "turn_started", { response_mode: "deep" }))
        onEvent(event(2, "response_chunk", { text_delta: "threshold 是", response_mode: "deep" }))
        onEvent(event(3, "response_chunk", { text_delta: "扩容阈值。", response_mode: "deep" }))
        onEvent(event(4, "citation", {
          evidence_id: "hashmap-threshold",
          source_reference: "offline://hashmap",
          excerpt: "capacity × loadFactor",
        }))
        onEvent(event(5, "metrics", {
          response_mode: "deep",
          ttft_ms: 12.5,
          total_response_ms: 38,
          provider_call_count: 0,
          native_provider_streaming: true,
          cache_status: "not_supported",
        }))
        onEvent(event(6, "completed", { response_mode: "deep" }))
      }),
    })
    const user = userEvent.setup()

    render(<TeacherLiveClassroom client={fake} />)

    expect(await screen.findByTestId("teacher-live-provider-mode")).toHaveTextContent("DEMO_FAKE")
    expect(screen.getByText("当前没有使用真实 AI，也不会消耗 Provider 配额。")).toBeInTheDocument()
    await user.type(screen.getByTestId("teacher-live-input"), "为什么？")
    await user.click(screen.getByTestId("teacher-live-send"))

    expect(await screen.findByText("threshold 是扩容阈值。")).toBeInTheDocument()
    expect(screen.getByTestId("teacher-live-response-mode")).toHaveTextContent("DEEP")
    const diagnostics = screen.getByTestId("teacher-live-diagnostics")
    expect(diagnostics).not.toHaveAttribute("open")
    expect(diagnostics).toHaveTextContent("12.5 ms")
    expect(diagnostics).toHaveTextContent("Provider 调用0")

    expect(screen.queryByTestId("teacher-live-citations")).not.toBeInTheDocument()
    await user.click(screen.getByTestId("teacher-live-evidence-toggle"))
    expect(screen.getByTestId("teacher-live-citations")).toHaveTextContent("capacity × loadFactor")
  })

  it("pauses, resumes, and aborts an active stream", async () => {
    const active = session()
    const fake = client({
      streamTurn: vi.fn((_path, _onEvent, options) => new Promise<void>((_resolve, reject) => {
        options?.signal?.addEventListener("abort", () => {
          reject(new DOMException("aborted", "AbortError"))
        }, { once: true })
      })),
      pauseSession: vi.fn().mockResolvedValue(session({ state: "paused" })),
      resumeSession: vi.fn().mockResolvedValue(active),
      abortSession: vi.fn().mockResolvedValue(session({ state: "aborted" })),
    })
    const user = userEvent.setup()

    render(<TeacherLiveClassroom client={fake} />)
    await screen.findByText("离线演示已就绪")

    await user.click(screen.getByTestId("teacher-live-pause-resume"))
    expect(await screen.findByTestId("teacher-live-session-state")).toHaveTextContent("已暂停")
    expect(screen.getByTestId("teacher-live-input")).toBeDisabled()
    await user.click(screen.getByTestId("teacher-live-pause-resume"))
    expect(await screen.findByTestId("teacher-live-session-state")).toHaveTextContent("进行中")

    await user.type(screen.getByTestId("teacher-live-input"), "我没懂")
    await user.click(screen.getByTestId("teacher-live-send"))
    await screen.findByText("正在生成")
    await user.click(screen.getByTestId("teacher-live-abort"))

    expect(await screen.findByTestId("teacher-live-session-state")).toHaveTextContent("已结束")
    expect(screen.getByRole("alert")).toHaveTextContent("本次回复已取消，课堂已经结束。")
    expect(fake.abortSession).toHaveBeenCalledOnce()
  })

  it("retries with the same idempotency identity and recovers a duplicate stream", async () => {
    let streamAttempt = 0
    const fake = client({
      submitTurn: vi.fn()
        .mockResolvedValueOnce({
          session_id: "live-session-1",
          turn_id: "turn-1",
          idempotency_key: "idem-1",
          stream_path: "/stream/turn-1",
          provider_mode: "DEMO_FAKE",
          duplicate: false,
          state: "queued",
        })
        .mockResolvedValueOnce({
          session_id: "live-session-1",
          turn_id: "turn-1",
          idempotency_key: "idem-1",
          stream_path: "/stream/turn-1",
          provider_mode: "DEMO_FAKE",
          duplicate: true,
          state: "completed",
        }),
      streamTurn: vi.fn(async (_path, onEvent) => {
        streamAttempt += 1
        if (streamAttempt === 1) {
          onEvent(event(1, "error", { message: "Provider 暂时不可用，请安全重试。", retryable: true }))
          return
        }
        onEvent(event(1, "response_chunk", { text_delta: "已恢复的回复", response_mode: "fast" }))
        onEvent(event(2, "completed", { response_mode: "fast" }))
      }),
    })
    const user = userEvent.setup()

    render(<TeacherLiveClassroom client={fake} />)
    await screen.findByText("离线演示已就绪")
    await user.type(screen.getByTestId("teacher-live-input"), "换个例子")
    await user.click(screen.getByTestId("teacher-live-send"))

    expect(await screen.findByRole("alert")).toHaveTextContent("Provider 暂时不可用")
    await user.click(screen.getByTestId("teacher-live-retry"))
    expect(await screen.findByText("已恢复的回复")).toBeInTheDocument()

    const calls = vi.mocked(fake.submitTurn).mock.calls
    expect(calls).toHaveLength(2)
    expect(calls[1]?.[1].turn_id).toBe(calls[0]?.[1].turn_id)
    expect(calls[1]?.[1].idempotency_key).toBe(calls[0]?.[1].idempotency_key)
    expect(screen.getByRole("status")).toHaveTextContent("已恢复本轮记录")
  })

  it("renders REAL exactly and explains non-native provider streaming", async () => {
    const real = session({
      provider_mode: "REAL",
      provider_label: "REAL",
      native_provider_streaming: false,
      demo_fixture: false,
    })
    const fake = client({
      createSession: vi.fn().mockResolvedValue(real),
      streamTurn: vi.fn(async (_path, onEvent) => {
        onEvent(event(1, "metrics", {
          response_mode: "fast",
          ttft_ms: 50,
          total_response_ms: 70,
          provider_call_count: 1,
          native_provider_streaming: false,
          cache_status: "unknown",
        }, "REAL"))
        onEvent(event(2, "response_chunk", { text_delta: "真实适配器回复", response_mode: "fast" }, "REAL"))
        onEvent(event(3, "completed", { response_mode: "fast" }, "REAL"))
      }),
    })
    const user = userEvent.setup()

    render(<TeacherLiveClassroom client={fake} />)
    expect(await screen.findByTestId("teacher-live-provider-mode")).toHaveTextContent("REAL")
    expect(screen.queryByText("离线 DEMO")).not.toBeInTheDocument()
    await user.type(screen.getByTestId("teacher-live-input"), "真实问题")
    await user.click(screen.getByTestId("teacher-live-send"))
    expect(await screen.findByText("真实适配器回复")).toBeInTheDocument()
    expect(screen.getByTestId("teacher-live-diagnostics")).toHaveTextContent(
      "此 TTFT 不能当作原生 Provider 首 token",
    )
  })
})


describe("teacher-live SSE parser", () => {
  it("parses named SSE frames without exposing the event transport envelope", () => {
    const parsed = parseTeacherLiveSseFrame([
      "id: 3",
      "event: response_chunk",
      'data: {"sequence":3,"event":"response_chunk","session_id":"s","turn_id":"t","provider_mode":"DEMO_FAKE","data":{"text_delta":"你好"}}',
    ].join("\n"))

    expect(parsed?.event).toBe("response_chunk")
    expect(parsed?.data.text_delta).toBe("你好")
  })
})
