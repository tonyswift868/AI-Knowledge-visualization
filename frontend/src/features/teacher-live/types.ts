export type TeacherLiveProviderMode = "REAL" | "DEMO_FAKE"
export type TeacherLiveSessionState = "active" | "paused" | "aborted"
export type TeacherResponseMode = "fast" | "deep"

export type TeacherLiveSession = {
  session_id: string
  state: TeacherLiveSessionState
  provider_mode: TeacherLiveProviderMode
  provider_label: TeacherLiveProviderMode
  native_provider_streaming: boolean
  current_concept: string
  next_sequence_number: number
  demo_fixture: boolean
}

export type TeacherLiveTurnReceipt = {
  session_id: string
  turn_id: string
  idempotency_key: string
  stream_path: string
  provider_mode: TeacherLiveProviderMode
  duplicate: boolean
  state: "queued" | "streaming" | "completed" | "error" | "cancelled"
}

export type TeacherLiveCitation = {
  evidence_id: string
  source_reference?: string | null
  excerpt?: string | null
}

export type TeacherLiveMetrics = {
  response_mode: TeacherResponseMode
  ttft_ms?: number | null
  total_response_ms: number
  provider_call_count: number
  native_provider_streaming: boolean
  input_tokens?: number | null
  output_tokens?: number | null
  cached_input_tokens?: number | null
  cache_status: "unknown" | "hit" | "miss" | "not_supported"
}

export type TeacherLiveEventName =
  | "turn_started"
  | "response_chunk"
  | "citation"
  | "metrics"
  | "completed"
  | "error"

export type TeacherLiveEvent = {
  sequence: number
  event: TeacherLiveEventName
  session_id: string
  turn_id: string
  provider_mode: TeacherLiveProviderMode
  data: Record<string, unknown>
}

export type TeacherLiveApiClient = {
  createSession(input?: {
    current_concept?: string
    provider_session_id?: string
  }): Promise<TeacherLiveSession>
  submitTurn(
    sessionId: string,
    input: { message: string; turn_id: string; idempotency_key: string },
  ): Promise<TeacherLiveTurnReceipt>
  streamTurn(
    streamPath: string,
    onEvent: (event: TeacherLiveEvent) => void,
    options?: { signal?: AbortSignal; after?: number },
  ): Promise<void>
  pauseSession(sessionId: string): Promise<TeacherLiveSession>
  resumeSession(sessionId: string): Promise<TeacherLiveSession>
  abortSession(sessionId: string): Promise<TeacherLiveSession>
}
