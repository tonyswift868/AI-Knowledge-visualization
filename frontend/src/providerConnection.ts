import { useMentorStore } from "./stores/useMentorStore";

const API = "/api";

export type RequestError = Error & {
  code?: string;
  status?: number;
};

const RECOVERABLE_PROVIDER_SESSION_ERRORS = new Set([
  "PROVIDER_SESSION_REQUIRED",
  "PROVIDER_SESSION_NOT_FOUND",
  "PROVIDER_SESSION_UNVERIFIED",
  "PROVIDER_SESSION_EXPIRED",
]);

function retryInitWithProviderSession(
  init: RequestInit | undefined,
  providerSessionId: string,
): RequestInit {
  const headers = new Headers(init?.headers);
  headers.set("X-Provider-Session-ID", providerSessionId);
  let body = init?.body;
  if (typeof body === "string") {
    if (!headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    try {
      const parsed = JSON.parse(body);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        body = JSON.stringify({
          ...parsed,
          provider_session_id: providerSessionId,
        });
      }
    } catch {
      // Non-JSON bodies still receive the refreshed session through the header.
    }
  }
  return { ...init, headers, body };
}

export async function request<T>(
  path: string,
  init?: RequestInit,
  providerRecoveryAttempted = false,
): Promise<T> {
  const response = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const data = await response.json();
  if (!response.ok) {
    const code = data.error?.code || data.detail?.error?.code;
    const message =
      data.error?.message
      || data.detail?.error?.message
      || data.detail?.message
      || "请求失败";
    const state = useMentorStore.getState();
    if (
      !providerRecoveryAttempted
      && !path.startsWith("/provider-sessions")
      && RECOVERABLE_PROVIDER_SESSION_ERRORS.has(code)
      && state.provider !== "fake"
      && state.credentialRef
    ) {
      try {
        const restored = await reconnectSavedProvider();
        const restoredSessionId = String(
          restored?.provider_session_id
          || useMentorStore.getState().providerSessionId
          || "",
        );
        if (restoredSessionId) {
          return request<T>(
            path,
            retryInitWithProviderSession(init, restoredSessionId),
            true,
          );
        }
      } catch {
        // Fall through to the original request error. The learner can retry,
        // while diagnostics preserve the server response that triggered recovery.
      }
    }
    if (typeof code === "string" && code.startsWith("PROVIDER_SESSION")) {
      useMentorStore.getState().setLastError(message || "模型会话已失效，请重新连接");
    }
    const error = new Error(message) as RequestError;
    error.code = code;
    error.status = response.status;
    throw error;
  }
  return data;
}

export function providerRequestContext(): Record<string, string> {
  const state = useMentorStore.getState();
  const sessionId = state.verified ? state.providerSessionId : null;
  return sessionId ? { provider_session_id: sessionId } : {};
}

let providerReconnectInFlight: Promise<any> | null = null;

let currentProviderSyncInFlight: Promise<any> | null = null;

const PROVIDER_SYNC_TIMEOUT_MS = 4_000;

/**
 * Adopt the verified process-local Provider handle exposed by the native
 * desktop shell.  The embedded WebEngine has its own Zustand/localStorage
 * state, so it cannot rely on the desktop's in-memory session directly.
 *
 * This endpoint only returns a session handle and a credential reference; the
 * raw Provider credential is never accepted or copied into browser state.
 */
export function syncCurrentProviderSession(timeoutMs = PROVIDER_SYNC_TIMEOUT_MS): Promise<any> {
  if (currentProviderSyncInFlight) return currentProviderSyncInFlight;
  const initialState = useMentorStore.getState();
  // Explicit Fake/Demo mode is intentionally local and must not be replaced by
  // an unrelated desktop Provider session discovered on the shared backend.
  if (initialState.provider === "fake" || initialState.mode === "demo") {
    return Promise.resolve(null);
  }
  currentProviderSyncInFlight = (async () => {
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const timeoutPromise = new Promise<never>((_, reject) => {
      timeout = setTimeout(() => {
        controller?.abort();
        const error = new Error("模型连接同步超时，请稍候重试") as RequestError;
        error.code = "PROVIDER_SESSION_SYNC_TIMEOUT";
        error.status = 408;
        reject(error);
      }, Math.max(1, timeoutMs));
    });
    try {
      const response = await Promise.race([
        fetch(`${API}/provider-sessions/current`, {
          headers: { "Content-Type": "application/json" },
          signal: controller?.signal,
        }),
        timeoutPromise,
      ]);
      if (response.status === 404 || response.status === 428) return null;
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || "模型连接同步失败");
      const state = useMentorStore.getState();
      if (state.provider === "fake" || state.mode === "demo") return null;
      const rawProvider = String(data.provider || "openai-compatible");
      const provider = rawProvider === "openai_compatible" ? "openai-compatible" : rawProvider;
      if (
        !["openai-compatible", "gemini", "claude"].includes(provider)
        || data.verified !== true
        || !String(data.provider_session_id || "").trim()
      ) return null;
      if (data.expires_at && new Date(String(data.expires_at)).getTime() <= Date.now()) return null;
      const latency = Number(data.latency_ms);
      state.setProviderSession({
        providerSessionId: String(data.provider_session_id),
        provider: provider as any,
        baseUrl: String(data.base_url || state.baseUrl || ""),
        model: String(data.model_reported || data.model_requested || state.model || ""),
        credentialRef: String(data.credential_ref || state.credentialRef || "") || null,
        verified: true,
        expiresAt: data.expires_at || null,
        latencyMs: Number.isFinite(latency) ? latency : null,
        lastTestedAt: data.last_tested_at || null,
      });
      return data;
    } finally {
      if (timeout !== undefined) clearTimeout(timeout);
    }
  })().finally(() => {
    currentProviderSyncInFlight = null;
  });
  return currentProviderSyncInFlight;
}

export function reconnectSavedProvider(): Promise<any> {
  if (providerReconnectInFlight) return providerReconnectInFlight;
  const state = useMentorStore.getState();
  if (state.provider === "fake" || !state.credentialRef) {
    return Promise.resolve(null);
  }
  providerReconnectInFlight = (async () => {
    if (state.providerSessionId) {
      try {
        const current = await request<any>(
          `/provider-sessions/${encodeURIComponent(state.providerSessionId)}`,
        );
        if (
          current.verified
          && (!current.expires_at || new Date(current.expires_at).getTime() > Date.now())
        ) {
          useMentorStore.getState().setProviderSession({
            providerSessionId: current.provider_session_id,
            provider: state.provider,
            baseUrl: state.baseUrl,
            model: state.model,
            credentialRef: current.credential_ref || state.credentialRef,
            verified: true,
            expiresAt: current.expires_at,
            latencyMs: current.latency_ms,
            lastTestedAt: current.last_tested_at,
          });
          return current;
        }
      } catch {
        // Backend restarts discard the in-memory session. Recreate it below
        // from the browser-local provider configuration and credential.
      }
    }
    const baseUrl = state.baseUrl.trim();
    const created = await request<any>("/provider-sessions", {
      method: "POST",
      body: JSON.stringify({
        provider: state.provider,
        base_url: baseUrl,
        model: state.model,
        credential_ref: state.credentialRef,
      }),
    });
    const result = await request<any>(
      `/provider-sessions/${encodeURIComponent(created.provider_session_id)}/test`,
      { method: "POST" },
    );
    useMentorStore.getState().setProviderSession({
      providerSessionId: result.provider_session_id,
      provider: state.provider,
      baseUrl,
      model: state.model,
      credentialRef: result.credential_ref || state.credentialRef,
      verified: true,
      expiresAt: result.expires_at,
      latencyMs: result.latency_ms,
      lastTestedAt: result.last_tested_at,
    });
    return result;
  })().finally(() => {
    providerReconnectInFlight = null;
  });
  return providerReconnectInFlight;
}
