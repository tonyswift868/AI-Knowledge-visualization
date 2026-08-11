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
const DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
const DEEPSEEK_DEFAULT_MODEL = 'deepseek-v4-pro'
const GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta'
const GEMINI_DEFAULT_MODEL = 'gemini-2.5-flash'
const defaultState: PersistedProvider = { mode: 'real', provider: 'openai-compatible', baseUrl: DEEPSEEK_BASE_URL, model: DEEPSEEK_DEFAULT_MODEL, providerSessionId: null, verified: false, expiresAt: null, latencyMs: null, lastTestedAt: null }

function apiKeyStorageKey(provider: ProviderId) {
  return `${API_KEY_STORAGE_PREFIX}${provider}`
}

function readPersisted(): PersistedProvider {
  try {
    const raw = localStorage.getItem(STORAGE_KEY) || sessionStorage.getItem(STORAGE_KEY) || '{}'
    const parsed = JSON.parse(raw)
    const value = { ...defaultState, ...parsed } as PersistedProvider
    const isDeepSeek = value.provider === 'openai-compatible' && (!value.baseUrl || value.baseUrl.includes('api.deepseek.com'))
    if (isDeepSeek) {
      value.baseUrl = DEEPSEEK_BASE_URL
      if (!value.model || value.model === 'fake' || value.model === 'deepseek-chat' || value.model === 'deepseek-reasoner') value.model = DEEPSEEK_DEFAULT_MODEL
    }
    if (value.provider === 'gemini') {
      value.baseUrl = value.baseUrl || GEMINI_BASE_URL
      value.model = value.model || GEMINI_DEFAULT_MODEL
    }
    return value
  } catch {
    return defaultState
  }
}

function readApiKey(provider: ProviderId) {
  try {
    return localStorage.getItem(apiKeyStorageKey(provider)) || ''
  } catch {
    return ''
  }
}

function persist(state: ProviderStore) {
  const value: PersistedProvider = { mode: state.mode, provider: state.provider, baseUrl: state.baseUrl, model: state.model, providerSessionId: state.providerSessionId, verified: state.verified, expiresAt: state.expiresAt, latencyMs: state.latencyMs, lastTestedAt: state.lastTestedAt }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
  sessionStorage.removeItem(STORAGE_KEY)
}

const persisted = readPersisted()
export const useMentorStore = create<ProviderStore>((set, get) => ({
  ...persisted,
  // BYOK credentials stay on this browser only and are never sent to project files or the database.
  apiKey: readApiKey(persisted.provider),
  lastError: '',
  activeWorkspaceId: null,
  setWorkspace: (activeWorkspaceId) => set({ activeWorkspaceId }),
  setProvider: (provider) => {
    const sameProvider = get().provider === provider
    const deepSeekDefaults = provider === 'openai-compatible' && !sameProvider
      ? { baseUrl: DEEPSEEK_BASE_URL, model: DEEPSEEK_DEFAULT_MODEL }
      : {}
    const geminiDefaults = provider === 'gemini' && !sameProvider
      ? { baseUrl: GEMINI_BASE_URL, model: GEMINI_DEFAULT_MODEL }
      : {}
    set({ ...deepSeekDefaults, ...geminiDefaults, provider, mode: provider === 'fake' ? 'demo' : 'real', apiKey: readApiKey(provider), verified: false, providerSessionId: sameProvider ? get().providerSessionId : null, lastError: '' })
    persist(get())
  },
  setBaseUrl: (baseUrl) => { set({ baseUrl, verified: false, lastError: '' }); persist(get()) },
  setModel: (model) => { set({ model, verified: false, lastError: '' }); persist(get()) },
  setApiKey: (apiKey) => {
    set({ apiKey, verified: false, lastError: '' })
    if (apiKey) localStorage.setItem(apiKeyStorageKey(get().provider), apiKey)
    else localStorage.removeItem(apiKeyStorageKey(get().provider))
  },
  setProviderSession: (value) => { set({ ...value, provider: value.provider || get().provider, mode: value.provider === 'fake' ? 'demo' : 'real', lastError: '' }); persist(get()) },
  setLastError: (lastError) => set({ lastError, verified: false }),
  clearProvider: () => {
    for (const provider of ['fake', 'openai-compatible', 'gemini', 'claude'] as ProviderId[]) localStorage.removeItem(apiKeyStorageKey(provider))
    set({ ...defaultState, apiKey: '', lastError: '' })
    persist(get())
  },
}))

export function providerDiagnostic(state: ProviderStore) {
  return { mode: state.mode, provider: state.provider, base_url_host: state.baseUrl ? (() => { try { return new URL(state.baseUrl).hostname } catch { return '' } })() : '', model: state.model, session_id: state.providerSessionId ? `${state.providerSessionId.slice(0, 10)}…` : null, verified: state.verified, expires_at: state.expiresAt, last_tested_at: state.lastTestedAt, latency_ms: state.latencyMs, last_error: state.lastError || null }
}
