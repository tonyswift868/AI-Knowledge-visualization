import type {
  TeacherLiveApiClient,
  TeacherLiveEvent,
  TeacherLiveSession,
  TeacherLiveTurnReceipt,
} from "./types"

export class TeacherLiveApiError extends Error {
  code: string

  constructor(code: string, message: string) {
    super(message)
    this.name = "TeacherLiveApiError"
    this.code = code
  }
}

async function jsonRequest<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(init.headers || {}),
    },
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = payload?.detail?.error || payload?.error || {}
    throw new TeacherLiveApiError(
      String(detail.code || "REQUEST_FAILED"),
      String(detail.message || "课堂请求失败，请稍后重试。"),
    )
  }
  return payload as T
}

export function parseTeacherLiveSseFrame(frame: string): TeacherLiveEvent | null {
  const data = frame
    .split(/\r?\n/)
    .filter(line => line.startsWith("data:"))
    .map(line => line.slice(5).trimStart())
    .join("\n")
  if (!data) return null
  const parsed = JSON.parse(data) as TeacherLiveEvent
  if (!parsed || typeof parsed.sequence !== "number" || typeof parsed.event !== "string") {
    throw new TeacherLiveApiError("STREAM_EVENT_INVALID", "课堂流返回了无法识别的事件。")
  }
  return parsed
}

export async function streamTeacherLiveEvents(
  streamPath: string,
  onEvent: (event: TeacherLiveEvent) => void,
  options: { signal?: AbortSignal; after?: number } = {},
): Promise<void> {
  const separator = streamPath.includes("?") ? "&" : "?"
  const path = options.after
    ? `${streamPath}${separator}after=${options.after}`
    : streamPath
  const response = await fetch(path, {
    method: "GET",
    headers: { Accept: "text/event-stream" },
    signal: options.signal,
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const detail = payload?.detail?.error || payload?.error || {}
    throw new TeacherLiveApiError(
      String(detail.code || "STREAM_UNAVAILABLE"),
      String(detail.message || "课堂流暂时不可用。"),
    )
  }
  if (!response.body) {
    throw new TeacherLiveApiError("STREAM_BODY_MISSING", "浏览器没有收到课堂流。")
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const frames = buffer.split(/\r?\n\r?\n/)
    buffer = frames.pop() || ""
    for (const frame of frames) {
      const event = parseTeacherLiveSseFrame(frame)
      if (event) onEvent(event)
    }
    if (done) break
  }
  if (buffer.trim()) {
    const event = parseTeacherLiveSseFrame(buffer)
    if (event) onEvent(event)
  }
}

export const teacherLiveApi: TeacherLiveApiClient = {
  createSession(input = {}) {
    return jsonRequest<TeacherLiveSession>("/api/teacher-live/sessions", {
      method: "POST",
      body: JSON.stringify(input),
    })
  },
  submitTurn(sessionId, input) {
    return jsonRequest<TeacherLiveTurnReceipt>(
      `/api/teacher-live/sessions/${encodeURIComponent(sessionId)}/turns`,
      { method: "POST", body: JSON.stringify(input) },
    )
  },
  streamTurn: streamTeacherLiveEvents,
  pauseSession(sessionId) {
    return jsonRequest<TeacherLiveSession>(
      `/api/teacher-live/sessions/${encodeURIComponent(sessionId)}/pause`,
      { method: "POST" },
    )
  },
  resumeSession(sessionId) {
    return jsonRequest<TeacherLiveSession>(
      `/api/teacher-live/sessions/${encodeURIComponent(sessionId)}/resume`,
      { method: "POST" },
    )
  },
  abortSession(sessionId) {
    return jsonRequest<TeacherLiveSession>(
      `/api/teacher-live/sessions/${encodeURIComponent(sessionId)}/abort`,
      { method: "POST" },
    )
  },
}
