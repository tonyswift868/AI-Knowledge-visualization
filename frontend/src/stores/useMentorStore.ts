import { create } from 'zustand'

export type ProviderId = 'fake' | 'openai-compatible' | 'gemini' | 'claude'
export type ProviderMode = 'demo' | 'real'

type PersistedProvider = {
  mode: ProviderMode
  provider: ProviderId
  baseUrl: string
  model: string
  providerSessionId: string | null
  credentialRef: string | null
  verified: boolean
  expiresAt: string | null
  latencyMs: number | null
  lastTestedAt: string | null
}

export type ProviderStore = PersistedProvider & {
  lastError: string
  activeWorkspaceId: string | null
  setWorkspace: (id: string) => void
  setProvider: (provider: ProviderId) => void
  setBaseUrl: (baseUrl: string) => void
  setModel: (model: string) => void
  setCredentialRef: (credentialRef: string) => void
  setProviderSession: (value: Partial<PersistedProvider> & { providerSessionId: string; provider?: ProviderId; latencyMs?: number | null; lastTestedAt?: string | null }) => void
  clearCredential: () => void
  legacyBrowserCredentialDetected: boolean
  clearLegacyBrowserCredential: () => void
  setLastError: (lastError: string) => void
  clearProvider: () => void
}

const STORAGE_KEY = 'mentorflow_provider_state'
const LEGACY_API_KEY_STORAGE_PREFIX = 'mentorflow_api_key_v1:'
const PROVIDERS: ProviderId[] = ['fake', 'openai-compatible', 'gemini', 'claude']
const defaultState: PersistedProvider = { mode: 'real', provider: 'openai-compatible', baseUrl: '', model: '', providerSessionId: null, credentialRef: null, verified: false, expiresAt: null, latencyMs: null, lastTestedAt: null }

function apiKeyStorageKey(provider: ProviderId) {
  return `${LEGACY_API_KEY_STORAGE_PREFIX}${provider}`
}

export function purgeLegacyBrowserCredentials() {
  try {
    const legacyKeys = new Set(PROVIDERS.map(apiKeyStorageKey))
    const matches: string[] = []
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index) || ''
      if (legacyKeys.has(key)) matches.push(key)
    }
    for (const key of matches) localStorage.removeItem(key)
    return matches.length > 0
  } catch { return false }
}

function readPersisted(): PersistedProvider {
  try {
    const raw = localStorage.getItem(STORAGE_KEY) || sessionStorage.getItem(STORAGE_KEY) || '{}'
    const parsed = JSON.parse(raw)
    return {
      mode: parsed.mode === 'demo' ? 'demo' : 'real',
      provider: PROVIDERS.includes(parsed.provider) ? parsed.provider : defaultState.provider,
      baseUrl: String(parsed.baseUrl || ''),
      model: String(parsed.model || ''),
      providerSessionId: typeof parsed.providerSessionId === 'string' ? parsed.providerSessionId : null,
      credentialRef: typeof parsed.credentialRef === 'string' ? parsed.credentialRef : null,
      verified: Boolean(parsed.verified),
      expiresAt: typeof parsed.expiresAt === 'string' ? parsed.expiresAt : null,
      latencyMs: Number.isFinite(parsed.latencyMs) ? Number(parsed.latencyMs) : null,
      lastTestedAt: typeof parsed.lastTestedAt === 'string' ? parsed.lastTestedAt : null,
    }
  } catch {
    return defaultState
  }
}

function persist(state: ProviderStore) {
  const value: PersistedProvider = { mode: state.mode, provider: state.provider, baseUrl: state.baseUrl, model: state.model, providerSessionId: state.providerSessionId, credentialRef: state.credentialRef, verified: state.verified, expiresAt: state.expiresAt, latencyMs: state.latencyMs, lastTestedAt: state.lastTestedAt }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
  sessionStorage.removeItem(STORAGE_KEY)
}

const persisted = readPersisted()
const legacyBrowserCredentialPurged = purgeLegacyBrowserCredentials()
export const useMentorStore = create<ProviderStore>((set, get) => ({
  ...persisted,
  legacyBrowserCredentialDetected: legacyBrowserCredentialPurged,
  lastError: '',
  activeWorkspaceId: null,
  setWorkspace: (activeWorkspaceId) => set({ activeWorkspaceId }),
  setProvider: (provider) => {
    const sameProvider = get().provider === provider
    set({ provider, mode: provider === 'fake' ? 'demo' : 'real', baseUrl: sameProvider ? get().baseUrl : '', model: sameProvider ? get().model : '', credentialRef: sameProvider ? get().credentialRef : null, verified: false, providerSessionId: sameProvider ? get().providerSessionId : null, lastError: '' })
    persist(get())
  },
  setBaseUrl: (baseUrl) => { set({ baseUrl, verified: false, lastError: '' }); persist(get()) },
  setModel: (model) => { set({ model, verified: false, lastError: '' }); persist(get()) },
  setCredentialRef: (credentialRef) => { set({ credentialRef: credentialRef.trim() || null, verified: false, lastError: '' }); persist(get()) },
  setProviderSession: (value) => { set({ ...value, provider: value.provider || get().provider, mode: value.provider === 'fake' ? 'demo' : 'real', lastError: '' }); persist(get()) },
  clearCredential: () => { set({ credentialRef: null, providerSessionId: null, verified: false, lastError: '' }); persist(get()) },
  clearLegacyBrowserCredential: () => {
    set({ legacyBrowserCredentialDetected: false })
  },
  setLastError: (lastError) => set({ lastError, verified: false }),
  clearProvider: () => {
    set({ ...defaultState, lastError: '' })
    persist(get())
  },
}))

export function providerDiagnostic(state: ProviderStore) {
  return { mode: state.mode, provider: state.provider, base_url_host: state.baseUrl ? (() => { try { return new URL(state.baseUrl).hostname } catch { return '' } })() : '', model: state.model, session_id: state.providerSessionId ? `${state.providerSessionId.slice(0, 10)}…` : null, verified: state.verified, expires_at: state.expiresAt, last_tested_at: state.lastTestedAt, latency_ms: state.latencyMs, last_error: state.lastError || null }
}
