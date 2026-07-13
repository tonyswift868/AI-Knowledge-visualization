import { create } from 'zustand'

export type ProviderId = 'fake' | 'openai-compatible' | 'gemini' | 'claude'
export type ProviderMode = 'demo' | 'real'

type PersistedProvider = {
  mode: ProviderMode
  provider: ProviderId
  baseUrl: string
  model: string
  providerSessionId: string | null
  verified: boolean
  expiresAt: string | null
  latencyMs: number | null
  lastTestedAt: string | null
}

export type ProviderStore = PersistedProvider & {
  apiKey: string
  lastError: string
  activeWorkspaceId: string | null
  setWorkspace: (id: string) => void
  setProvider: (provider: ProviderId) => void
  setBaseUrl: (baseUrl: string) => void
  setModel: (model: string) => void
  setApiKey: (apiKey: string) => void
  setProviderSession: (value: Partial<PersistedProvider> & { providerSessionId: string; provider?: ProviderId; latencyMs?: number | null; lastTestedAt?: string | null }) => void
  setLastError: (lastError: string) => void
  clearProvider: () => void
}

const STORAGE_KEY = 'mentorflow_provider_state'
const API_KEY_STORAGE_PREFIX = 'mentorflow_api_key_v1:'
const defaultState: PersistedProvider = { mode: 'demo', provider: 'fake', baseUrl: '', model: 'fake', providerSessionId: null, verified: false, expiresAt: null, latencyMs: null, lastTestedAt: null }

function apiKeyStorageKey(provider: ProviderId) { return `${API_KEY_STORAGE_PREFIX}${provider}` }
function readApiKey(provider: ProviderId) {
  try { return localStorage.getItem(apiKeyStorageKey(provider)) || '' } catch { return '' }
}

function readPersisted(): PersistedProvider {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '{}')
    return { ...defaultState, ...parsed, apiKey: undefined } as PersistedProvider
  } catch {
    return defaultState
  }
}

function persist(state: ProviderStore) {
  const value: PersistedProvider = { mode: state.mode, provider: state.provider, baseUrl: state.baseUrl, model: state.model, providerSessionId: state.providerSessionId, verified: state.verified, expiresAt: state.expiresAt, latencyMs: state.latencyMs, lastTestedAt: state.lastTestedAt }
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value))
}

const persisted = readPersisted()
export const useMentorStore = create<ProviderStore>((set, get) => ({
  ...persisted,
  apiKey: readApiKey(persisted.provider),
  lastError: '',
  activeWorkspaceId: null,
  setWorkspace: (activeWorkspaceId) => set({ activeWorkspaceId }),
  setProvider: (provider) => { const sameProvider = get().provider === provider; set({ provider, mode: provider === 'fake' ? 'demo' : 'real', apiKey: provider === 'fake' ? '' : readApiKey(provider), verified: false, providerSessionId: provider === 'fake' || !sameProvider ? null : get().providerSessionId, lastError: '' }); persist(get()) },
  setBaseUrl: (baseUrl) => { set({ baseUrl, verified: false, lastError: '' }); persist(get()) },
  setModel: (model) => { set({ model, verified: false, lastError: '' }); persist(get()) },
  setApiKey: (apiKey) => { try { if (apiKey) localStorage.setItem(apiKeyStorageKey(get().provider), apiKey); else localStorage.removeItem(apiKeyStorageKey(get().provider)); } catch { /* storage can be unavailable */ } set({ apiKey, verified: false, lastError: '' }) },
  setProviderSession: (value) => { set({ ...value, provider: value.provider || get().provider, mode: value.provider === 'fake' ? 'demo' : 'real', lastError: '' }); persist(get()) },
  setLastError: (lastError) => set({ lastError, verified: false }),
  clearProvider: () => { try { (['openai-compatible', 'gemini', 'claude'] as ProviderId[]).forEach(provider => localStorage.removeItem(apiKeyStorageKey(provider))) } catch { /* storage can be unavailable */ } set({ ...defaultState, apiKey: '', lastError: '' }); persist(get()) },
}))

export function providerDiagnostic(state: ProviderStore) {
  return { mode: state.mode, provider: state.provider, base_url_host: state.baseUrl ? (() => { try { return new URL(state.baseUrl).hostname } catch { return '' } })() : '', model: state.model, session_id: state.providerSessionId ? `${state.providerSessionId.slice(0, 10)}…` : null, verified: state.verified, expires_at: state.expiresAt, last_tested_at: state.lastTestedAt, latency_ms: state.latencyMs, last_error: state.lastError || null }
}
