const INSTALLED_KEY = "__mentorflowFetchTraceContextV1" as const

function randomHex(bytes: number): string {
  const values = new Uint8Array(bytes)
  globalThis.crypto.getRandomValues(values)
  return Array.from(values, (value) => value.toString(16).padStart(2, "0")).join("")
}

export type BrowserTraceContext = {
  requestId: string
  traceId: string
  spanId: string
  traceparent: string
}

export function createBrowserTraceContext(): BrowserTraceContext {
  const traceId = randomHex(16)
  const spanId = randomHex(8)
  return {
    requestId: randomHex(16),
    traceId,
    spanId,
    traceparent: `00-${traceId}-${spanId}-01`,
  }
}

export function withTraceContext(init?: RequestInit): RequestInit {
  const context = createBrowserTraceContext()
  const headers = new Headers(init?.headers)
  if (!headers.has("traceparent")) headers.set("traceparent", context.traceparent)
  if (!headers.has("X-Request-ID")) headers.set("X-Request-ID", context.requestId)
  return { ...init, headers }
}

export function installFetchTraceContext(): void {
  const target = globalThis as typeof globalThis & { [INSTALLED_KEY]?: boolean }
  if (target[INSTALLED_KEY] || typeof globalThis.fetch !== "function") return
  const originalFetch = globalThis.fetch.bind(globalThis)
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const existing = input instanceof Request ? input.headers : undefined
    const headers = new Headers(existing)
    new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
    return originalFetch(input, withTraceContext({ ...init, headers }))
  }) as typeof fetch
  target[INSTALLED_KEY] = true
}
