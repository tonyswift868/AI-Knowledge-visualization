import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Settings, providerRequestContext, reconnectSavedProvider } from '../App'
import { request } from '../providerConnection'
import { providerDiagnostic, useMentorStore } from '../stores/useMentorStore'

vi.mock('../features/voice/VoicePackSettings', () => ({ VoicePackSettings: () => null }))

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear(); useMentorStore.getState().clearProvider(); localStorage.clear() })

describe('V5 provider runtime state', () => {
  it('switches the single store to OpenAI Compatible real mode', () => { useMentorStore.getState().setProvider('openai-compatible'); const state = useMentorStore.getState(); expect(state.provider).toBe('openai-compatible'); expect(state.mode).toBe('real'); expect(state.verified).toBe(false) })
  it('keeps Base URL and model controlled by the store', () => { useMentorStore.getState().setBaseUrl('https://api.deepseek.com'); useMentorStore.getState().setModel('deepseek-v4-flash'); expect(useMentorStore.getState().baseUrl).toContain('deepseek'); expect(useMentorStore.getState().model).toBe('deepseek-v4-flash') })
  it('does not put API Key in sessionStorage or provider metadata', () => { useMentorStore.getState().setApiKey('secret-key'); expect(sessionStorage.getItem('mentorflow_provider_state') || '').not.toContain('secret-key'); expect(localStorage.getItem('mentorflow_provider_state') || '').not.toContain('secret-key') })
  it('auto-saves API Key only in provider-scoped local browser storage', () => { useMentorStore.getState().setProvider('openai-compatible'); useMentorStore.getState().setApiKey('secret-key'); expect(localStorage.getItem('mentorflow_api_key_v1:openai-compatible')).toBe('secret-key'); expect(localStorage.getItem('mentorflow_provider_state') || '').not.toContain('secret-key') })
  it('does not include API Key in generation request context', () => { useMentorStore.getState().setApiKey('secret-key'); useMentorStore.getState().setProviderSession({ providerSessionId: 'ps_123', provider: 'openai-compatible', verified: true }); expect(providerRequestContext()).toEqual({ provider_session_id: 'ps_123' }) })
  it('persists non-sensitive provider session state across browser restarts', () => { useMentorStore.getState().setProviderSession({ providerSessionId: 'ps_123', provider: 'openai-compatible', baseUrl: 'https://mock.test', model: 'm', verified: true, expiresAt: '2030-01-01T00:00:00Z', latencyMs: 12 }); const stored = localStorage.getItem('mentorflow_provider_state')!; expect(stored).toContain('ps_123'); expect(stored).not.toContain('api_key') })
  it('diagnostics are safe and contain no key', () => { useMentorStore.getState().setApiKey('secret-key'); const diagnostic = providerDiagnostic(useMentorStore.getState()); expect(JSON.stringify(diagnostic)).not.toContain('secret-key'); expect(diagnostic).not.toHaveProperty('apiKey') })
  it('supports provider sessions without an arbitrary browser-side expiry', () => { useMentorStore.getState().setProviderSession({ providerSessionId: 'ps_123', provider: 'openai-compatible', verified: true, expiresAt: null }); expect(useMentorStore.getState().verified).toBe(true); expect(useMentorStore.getState().expiresAt).toBeNull() })
  it('keeps Fake explicitly in demo mode', () => { useMentorStore.getState().setProvider('fake'); expect(useMentorStore.getState().mode).toBe('demo'); expect(useMentorStore.getState().provider).toBe('fake') })
  it('updates the test button when provider changes', () => { useMentorStore.getState().setProvider('openai-compatible'); render(<Settings />); expect(screen.getByRole('button', { name: /测试 DeepSeek/ })).toBeInTheDocument() })
  it('does not claim that an empty API key was saved', () => { useMentorStore.getState().setProvider('openai-compatible'); useMentorStore.getState().setApiKey(''); render(<Settings />); expect(screen.getByText(/尚未填写 API Key/)).toBeInTheDocument(); expect(screen.queryByText(/已自动保存到当前浏览器本机/)).not.toBeInTheDocument(); expect(screen.getByRole('button', { name: '清除 Key' })).toBeDisabled() })
  it('renders DeepSeek model presets without requiring a model ID', () => { useMentorStore.getState().setProvider('openai-compatible'); render(<Settings />); expect(screen.getByLabelText('Base URL')).toBeInTheDocument(); const select = screen.getByLabelText('Model') as HTMLSelectElement; expect(select.value).toBe('deepseek-v4-pro'); fireEvent.change(select, { target: { value: 'deepseek-v4-flash' } }); expect(select.value).toBe('deepseek-v4-flash') })
  it('uses the DeepSeek default URL when Base URL is blank', () => { useMentorStore.getState().setProvider('openai-compatible'); useMentorStore.getState().setBaseUrl(''); render(<Settings />); expect((screen.getByLabelText('Base URL') as HTMLInputElement).value).toBe('https://api.deepseek.com') })
  it('tests the selected provider and updates the store from metadata', async () => { useMentorStore.getState().setProvider('openai-compatible'); useMentorStore.getState().setBaseUrl('https://mock.test/v1'); useMentorStore.getState().setModel('deepseek-v4-pro'); useMentorStore.getState().setApiKey('secret'); const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ provider_session_id: 'ps_abc' }), { status: 200 })).mockResolvedValueOnce(new Response(JSON.stringify({ provider_session_id: 'ps_abc', provider: 'openai_compatible', model_reported: 'deepseek-v4-pro', expires_at: '2030-01-01T00:00:00Z', latency_ms: 18, last_tested_at: '2030-01-01T00:00:00Z', request_id: 'req_abc' }), { status: 200 })); vi.stubGlobal('fetch', fetchMock); render(<Settings />); fireEvent.click(screen.getByRole('button', { name: /测试 DeepSeek/ })); await waitFor(() => expect(useMentorStore.getState().verified).toBe(true)); expect(fetchMock).toHaveBeenCalledTimes(2); expect(JSON.stringify(fetchMock.mock.calls[1][1]?.body) || '').not.toContain('secret'); expect(screen.getByText(/已连接/)).toBeInTheDocument() })
  it('reuses a valid saved provider session without asking the user to reconnect', async () => { useMentorStore.getState().setProvider('openai-compatible'); useMentorStore.getState().setApiKey('secret'); useMentorStore.getState().setProviderSession({ providerSessionId: 'ps_saved', provider: 'openai-compatible', verified: true, expiresAt: '2030-01-01T00:00:00Z' }); const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ provider_session_id: 'ps_saved', provider: 'openai_compatible', verified: true, expires_at: '2030-01-01T00:00:00Z', latency_ms: 11, last_tested_at: '2026-07-27T00:00:00Z' }), { status: 200 })); vi.stubGlobal('fetch', fetchMock); await reconnectSavedProvider(); expect(fetchMock).toHaveBeenCalledTimes(1); expect(String(fetchMock.mock.calls[0][0])).toContain('/provider-sessions/ps_saved'); expect(useMentorStore.getState().verified).toBe(true) })
  it('reuses a non-expiring saved provider session after refresh', async () => { useMentorStore.getState().setProvider('openai-compatible'); useMentorStore.getState().setApiKey('secret'); useMentorStore.getState().setProviderSession({ providerSessionId: 'ps_saved', provider: 'openai-compatible', verified: true, expiresAt: null }); const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ provider_session_id: 'ps_saved', provider: 'openai_compatible', verified: true, expires_at: null, latency_ms: 11, last_tested_at: '2026-07-27T00:00:00Z' }), { status: 200 })); vi.stubGlobal('fetch', fetchMock); await reconnectSavedProvider(); expect(fetchMock).toHaveBeenCalledTimes(1); expect(String(fetchMock.mock.calls[0][0])).toContain('/provider-sessions/ps_saved'); expect(useMentorStore.getState().verified).toBe(true); expect(useMentorStore.getState().expiresAt).toBeNull() })
  it('automatically recreates a lost backend session from the browser-saved key', async () => { useMentorStore.getState().setProvider('openai-compatible'); useMentorStore.getState().setBaseUrl('https://mock.test/v1'); useMentorStore.getState().setModel('deepseek-v4-pro'); useMentorStore.getState().setApiKey('secret'); useMentorStore.getState().setProviderSession({ providerSessionId: 'ps_lost', provider: 'openai-compatible', verified: true, expiresAt: '2030-01-01T00:00:00Z' }); const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: 'PROVIDER_SESSION_NOT_FOUND', message: 'Provider 会话不存在' } }), { status: 404 })).mockResolvedValueOnce(new Response(JSON.stringify({ provider_session_id: 'ps_new' }), { status: 200 })).mockResolvedValueOnce(new Response(JSON.stringify({ provider_session_id: 'ps_new', provider: 'openai_compatible', verified: true, model_reported: 'deepseek-v4-pro', expires_at: '2030-01-01T00:00:00Z', latency_ms: 12, last_tested_at: '2026-07-27T00:00:00Z' }), { status: 200 })); vi.stubGlobal('fetch', fetchMock); await reconnectSavedProvider(); expect(fetchMock).toHaveBeenCalledTimes(3); expect(JSON.stringify(fetchMock.mock.calls[1][1]?.body)).toContain('secret'); expect(useMentorStore.getState().providerSessionId).toBe('ps_new'); expect(useMentorStore.getState().verified).toBe(true) })
  it('recovers a lost provider session and retries the interrupted request once', async () => {
    useMentorStore.getState().setProvider('openai-compatible')
    useMentorStore.getState().setBaseUrl('https://mock.test/v1')
    useMentorStore.getState().setModel('deepseek-v4-flash')
    useMentorStore.getState().setApiKey('secret')
    useMentorStore.getState().setProviderSession({ providerSessionId: 'ps_old', provider: 'openai-compatible', verified: true })
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: 'PROVIDER_SESSION_REQUIRED', message: '请先验证 Provider 连接' } }), { status: 428 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: 'PROVIDER_SESSION_NOT_FOUND', message: 'Provider 会话不存在' } }), { status: 404 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ provider_session_id: 'ps_new' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ provider_session_id: 'ps_new', provider: 'openai_compatible', verified: true, model_reported: 'deepseek-v4-flash', expires_at: null, latency_ms: 12, last_tested_at: '2026-07-31T00:00:00Z' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(request('/workspaces/ws_1/knowledge-map/generate', {
      method: 'POST',
      body: JSON.stringify({ provider_session_id: 'ps_old', refresh_learning_resources: true }),
    })).resolves.toEqual({ ok: true })

    expect(fetchMock).toHaveBeenCalledTimes(5)
    const retried = fetchMock.mock.calls[4][1] as RequestInit
    expect(String(retried.body)).toContain('ps_new')
    expect(String(retried.body)).not.toContain('ps_old')
    expect(new Headers(retried.headers).get('X-Provider-Session-ID')).toBe('ps_new')
    expect(new Headers(retried.headers).get('Content-Type')).toBe('application/json')
  })
  it('does not retry a provider/workspace mismatch as if it were an expired session', async () => {
    useMentorStore.getState().setProvider('openai-compatible')
    useMentorStore.getState().setApiKey('secret')
    useMentorStore.getState().setProviderSession({ providerSessionId: 'ps_wrong', provider: 'openai-compatible', verified: true })
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: 'PROVIDER_SESSION_MISMATCH', message: 'Provider 不匹配' } }), { status: 409 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(request('/workspaces/ws_1/knowledge-map/generate', {
      method: 'POST',
      body: JSON.stringify({ provider_session_id: 'ps_wrong' }),
    })).rejects.toMatchObject({ code: 'PROVIDER_SESSION_MISMATCH', status: 409 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
  it('shows a clear provider error and does not turn on verified state', async () => { useMentorStore.getState().setProvider('openai-compatible'); const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ provider_session_id: 'ps_bad' }), { status: 200 })).mockResolvedValueOnce(new Response(JSON.stringify({ error: { message: 'API Key 无效或没有权限' } }), { status: 401 })); vi.stubGlobal('fetch', fetchMock); render(<Settings />); fireEvent.click(screen.getByRole('button', { name: /测试 DeepSeek/ })); await waitFor(() => expect(screen.getByText(/连接失败/)).toBeInTheDocument()); expect(useMentorStore.getState().verified).toBe(false) })
})
