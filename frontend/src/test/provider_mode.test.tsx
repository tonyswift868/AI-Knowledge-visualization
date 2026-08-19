import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { Settings } from "../App";
import {
  providerRequestContext,
  reconnectSavedProvider,
  request,
  syncCurrentProviderSession,
} from "../providerConnection";
import {
  providerDiagnostic,
  purgeLegacyBrowserCredentials,
  useMentorStore,
} from "../stores/useMentorStore";

vi.mock("../features/voice/VoicePackSettings", () => ({ VoicePackSettings: () => null }));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  sessionStorage.clear();
  useMentorStore.getState().clearProvider();
  localStorage.clear();
});

describe("reference-only provider runtime state", () => {
  it("deletes known legacy browser credentials without migrating their value", () => {
    localStorage.setItem("mentorflow_api_key_v1:openai-compatible", "raw-secret-must-not-survive");

    expect(purgeLegacyBrowserCredentials()).toBe(true);
    expect(localStorage.getItem("mentorflow_api_key_v1:openai-compatible")).toBeNull();
    expect(localStorage.getItem("mentorflow_provider_state") || "").not.toContain("raw-secret-must-not-survive");
  });

  it("switches the single store to OpenAI Compatible real mode", () => {
    useMentorStore.getState().setProvider("openai-compatible");
    expect(useMentorStore.getState()).toMatchObject({
      provider: "openai-compatible",
      mode: "real",
      verified: false,
    });
  });

  it("has no raw API-key field or browser input", () => {
    expect(useMentorStore.getState()).not.toHaveProperty("apiKey");
    render(<Settings />);
    expect(screen.queryByLabelText("API Key")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Credential Reference")).toBeInTheDocument();
  });

  it("persists only non-sensitive provider configuration and a credential reference", () => {
    useMentorStore.getState().setProvider("openai-compatible");
    useMentorStore.getState().setCredentialRef("mfcred_test_reference");
    useMentorStore.getState().setProviderSession({
      providerSessionId: "ps_123",
      provider: "openai-compatible",
      baseUrl: "https://mock.test",
      model: "configured-model",
      credentialRef: "mfcred_test_reference",
      verified: true,
      expiresAt: null,
    });
    const stored = localStorage.getItem("mentorflow_provider_state") || "";
    expect(stored).toContain("mfcred_test_reference");
    expect(stored).not.toContain("apiKey");
    expect(stored).not.toContain("api_key");
  });

  it("sends only a verified provider-session handle to generation APIs", () => {
    useMentorStore.getState().setProviderSession({
      providerSessionId: "ps_123",
      provider: "openai-compatible",
      verified: true,
    });
    expect(providerRequestContext()).toEqual({ provider_session_id: "ps_123" });
  });

  it("adopts the desktop shell's verified session without an API key", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      provider_session_id: "ps_desktop_verified",
      provider: "openai_compatible",
      credential_ref: "mfcred_saved_reference",
      base_url: "https://api.deepseek.com",
      model_requested: "deepseek-v4-flash",
      model_reported: "deepseek-v4-flash",
      verified: true,
      expires_at: null,
      latency_ms: 19,
      last_tested_at: "2026-08-17T00:00:00Z",
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await syncCurrentProviderSession();

    expect(useMentorStore.getState()).toMatchObject({
      providerSessionId: "ps_desktop_verified",
      provider: "openai-compatible",
      model: "deepseek-v4-flash",
      credentialRef: "mfcred_saved_reference",
      verified: true,
    });
    expect(providerRequestContext()).toEqual({ provider_session_id: "ps_desktop_verified" });
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("sk-");
  });

  it("does not replace explicit Fake mode with a desktop session", async () => {
    useMentorStore.getState().setProvider("fake");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(syncCurrentProviderSession()).resolves.toBeNull();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(useMentorStore.getState()).toMatchObject({
      provider: "fake",
      mode: "demo",
      verified: false,
      providerSessionId: null,
    });
  });

  it("bounds a stalled desktop session lookup", async () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));

    await expect(syncCurrentProviderSession(10)).rejects.toMatchObject({
      code: "PROVIDER_SESSION_SYNC_TIMEOUT",
      status: 408,
    });
  });

  it("keeps diagnostics reference-safe", () => {
    useMentorStore.getState().setCredentialRef("mfcred_test_reference");
    const diagnostic = providerDiagnostic(useMentorStore.getState());
    expect(diagnostic).not.toHaveProperty("apiKey");
    expect(diagnostic).not.toHaveProperty("credentialRef");
  });

  it("requires explicit Base URL, model, and credential reference", () => {
    useMentorStore.getState().setProvider("openai-compatible");
    render(<Settings />);
    expect((screen.getByLabelText("Base URL") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Credential Reference") as HTMLInputElement).value).toBe("");
    expect(screen.getByRole("button", { name: "清除凭据" })).toBeDisabled();
  });

  it("tests a selected reference without putting a key in the API request", async () => {
    const state = useMentorStore.getState();
    state.setProvider("openai-compatible");
    state.setBaseUrl("https://mock.test/v1");
    state.setModel("configured-model");
    state.setCredentialRef("mfcred_test_reference");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        provider_session_id: "ps_abc",
        credential_ref: "mfcred_test_reference",
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        provider_session_id: "ps_abc",
        credential_ref: "mfcred_test_reference",
        provider: "openai_compatible",
        model_reported: "configured-model",
        expires_at: null,
        latency_ms: 18,
        last_tested_at: "2030-01-01T00:00:00Z",
        request_id: "req_abc",
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<Settings />);
    fireEvent.click(screen.getByRole("button", { name: /测试 OpenAI Compatible/ }));
    await waitFor(() => expect(useMentorStore.getState().verified).toBe(true));
    const createBody = String(fetchMock.mock.calls[0][1]?.body || "");
    expect(createBody).toContain("mfcred_test_reference");
    expect(createBody).not.toContain("api_key");
    expect(screen.getByText(/已连接/)).toBeInTheDocument();
  });

  it("reuses a valid non-expiring provider session", async () => {
    const state = useMentorStore.getState();
    state.setProvider("openai-compatible");
    state.setCredentialRef("mfcred_saved");
    state.setProviderSession({
      providerSessionId: "ps_saved",
      credentialRef: "mfcred_saved",
      provider: "openai-compatible",
      verified: true,
      expiresAt: null,
    });
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      provider_session_id: "ps_saved",
      credential_ref: "mfcred_saved",
      provider: "openai_compatible",
      verified: true,
      expires_at: null,
      latency_ms: 11,
      last_tested_at: "2026-07-27T00:00:00Z",
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await reconnectSavedProvider();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/provider-sessions/ps_saved");
  });

  it("recreates a lost backend session from the reference only", async () => {
    const state = useMentorStore.getState();
    state.setProvider("openai-compatible");
    state.setBaseUrl("https://mock.test/v1");
    state.setModel("configured-model");
    state.setCredentialRef("mfcred_saved");
    state.setProviderSession({
      providerSessionId: "ps_lost",
      credentialRef: "mfcred_saved",
      provider: "openai-compatible",
      verified: true,
      expiresAt: null,
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        error: { code: "PROVIDER_SESSION_NOT_FOUND", message: "Provider 会话不存在" },
      }), { status: 404 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        provider_session_id: "ps_new",
        credential_ref: "mfcred_saved",
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        provider_session_id: "ps_new",
        credential_ref: "mfcred_saved",
        provider: "openai_compatible",
        verified: true,
        model_reported: "configured-model",
        expires_at: null,
        latency_ms: 12,
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await reconnectSavedProvider();
    const restoreBody = String(fetchMock.mock.calls[1][1]?.body || "");
    expect(restoreBody).toContain("mfcred_saved");
    expect(restoreBody).not.toContain("api_key");
    expect(useMentorStore.getState().providerSessionId).toBe("ps_new");
  });

  it("recovers a lost session and retries an interrupted request once", async () => {
    const state = useMentorStore.getState();
    state.setProvider("openai-compatible");
    state.setBaseUrl("https://mock.test/v1");
    state.setModel("configured-model");
    state.setCredentialRef("mfcred_saved");
    state.setProviderSession({
      providerSessionId: "ps_old",
      credentialRef: "mfcred_saved",
      provider: "openai-compatible",
      verified: true,
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "PROVIDER_SESSION_REQUIRED", message: "请先验证 Provider 连接" } }), { status: 428 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "PROVIDER_SESSION_NOT_FOUND", message: "Provider 会话不存在" } }), { status: 404 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ provider_session_id: "ps_new", credential_ref: "mfcred_saved" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ provider_session_id: "ps_new", credential_ref: "mfcred_saved", provider: "openai_compatible", verified: true, expires_at: null }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(request("/workspaces/ws_1/knowledge-map/jobs", {
      method: "POST",
      body: JSON.stringify({ provider_session_id: "ps_old" }),
    })).resolves.toEqual({ ok: true });
    const retried = fetchMock.mock.calls[4][1] as RequestInit;
    expect(String(retried.body)).toContain("ps_new");
    expect(String(retried.body)).not.toContain("ps_old");
  });

  it("does not retry a provider/workspace mismatch", async () => {
    const state = useMentorStore.getState();
    state.setProvider("openai-compatible");
    state.setCredentialRef("mfcred_saved");
    state.setProviderSession({
      providerSessionId: "ps_wrong",
      credentialRef: "mfcred_saved",
      provider: "openai-compatible",
      verified: true,
    });
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({
      error: { code: "PROVIDER_SESSION_MISMATCH", message: "Provider 不匹配" },
    }), { status: 409 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(request("/workspaces/ws_1/knowledge-map/jobs", {
      method: "POST",
      body: JSON.stringify({ provider_session_id: "ps_wrong" }),
    })).rejects.toMatchObject({ code: "PROVIDER_SESSION_MISMATCH", status: 409 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
