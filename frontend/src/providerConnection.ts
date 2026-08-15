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
