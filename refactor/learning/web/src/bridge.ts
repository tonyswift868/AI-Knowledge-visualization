// Page side of the Qt bridge (see refactor/learning/app/bridge.py for the wire format).
import { beginActivity, endActivity, streamActivity } from './loading/activity'
import { useApp, type ModelFault } from './store'

type Pending = { resolve: (v: any) => void; reject: (e: BridgeFailure) => void; onStream?: (d: string) => void }
type Listener = (data: any) => void

export class BridgeFailure extends Error {
  code: string
  model_fault?: ModelFault
  constructor(code: string, message: string, fault?: ModelFault) {
    super(message)
    this.code = code
    this.model_fault = fault
  }
}

declare global {
  interface Window {
    qt?: { webChannelTransport: unknown }
    QWebChannel?: new (transport: unknown, ready: (channel: any) => void) => unknown
  }
}

let counter = 0
const pending = new Map<string, Pending>()
const listeners = new Map<string, Set<Listener>>()
let send: ((text: string) => void) | null = null
const queue: string[] = []

function handle(text: string) {
  let msg: any
  try {
    msg = JSON.parse(text)
  } catch {
    return
  }
  if (msg.type === "response") {
    const p = pending.get(msg.id)
    if (!p) return
    pending.delete(msg.id)
    endActivity(msg.id)
    const fault = msg.error?.model_fault ?? msg.result?.failure?.model_fault
    if (fault) useApp.getState().setModelStatus({state:'error',short:fault.short,fault})
    if (msg.ok) p.resolve(msg.result)
    else p.reject(new BridgeFailure(msg.error?.code ?? "ERROR", msg.error?.message ?? "操作没有完成。",fault))
  } else if (msg.type === "stream") {
    streamActivity(msg.id, msg.delta ?? '')
    pending.get(msg.id)?.onStream?.(msg.delta)
  } else if (msg.type === "event") {
    if (msg.name === 'model_status') {
      const activeAccountId = useApp.getState().account?.account_id
      const statusAccountId = msg.data?.account_id
      // A worker from a session that has just been switched away may finish late.
      // Do not let its status replace the new account's chip or notify its listeners.
      if (activeAccountId && statusAccountId && activeAccountId !== statusAccountId) return
      useApp.getState().setModelStatus(msg.data)
    }
    listeners.get(msg.name)?.forEach((fn) => fn(msg.data))
  }
}

export function connect(): Promise<boolean> {
  return new Promise((resolve) => {
    if (window.qt?.webChannelTransport && window.QWebChannel) {
      new window.QWebChannel(window.qt.webChannelTransport, (channel: any) => {
        const mf = channel.objects.mf
        mf.event.connect(handle)
        send = (text) => mf.call(text)
        queue.splice(0).forEach((t) => send!(t))
        resolve(true)
      })
    } else {
      send = mockSend
      queue.splice(0).forEach((t) => send!(t))
      resolve(false)
    }
  })
}

export function call<T = any>(method: string, params: Record<string, unknown> = {},
                              opts: { onStream?: (d: string) => void; signal?: AbortSignal } = {}): Promise<T> {
  const id = `r${Date.now().toString(36)}${(counter++).toString(36)}`
  const text = JSON.stringify({ id, method, params })
  return new Promise<T>((resolve, reject) => {
    pending.set(id, { resolve, reject, onStream: opts.onStream })
    const cancelRequest = () => {
      const cancel = JSON.stringify({ id: `${id}c`, method: "cancel", params: { target: id } })
      send ? send(cancel) : queue.push(cancel)
    }
    beginActivity(id, method, opts.signal ? cancelRequest : undefined)
    opts.signal?.addEventListener("abort", () => {
      cancelRequest()
    })
    send ? send(text) : queue.push(text)
  })
}

export function on(name: string, fn: Listener): () => void {
  if (!listeners.has(name)) listeners.set(name, new Set())
  listeners.get(name)!.add(fn)
  return () => listeners.get(name)?.delete(fn)
}

// ---------------------------------------------------------------- browser mock
// Only used when the page is opened outside the desktop app (layout checks).
const mockState: any = {
  profile: { nickname: "tonyswift", signature: "账号资料仅本人可见。", version: 1,
    avatar_url: null, available: true, offline: false },
  settings_revision: 0,
  settings: {
    version: 1, theme: "sakura", font_scale: 1, reduce_motion: false, teacher_style: "warm", detail: "normal",
    examples: "normal", analogy_domains: ["life", "games"], refresh_time: "04:00", reminder_enabled: false,
    reminder_time: "20:00", chat_persona: "",
  },
  presets: [{ id: "p1", provider_id: "deepseek", display_name: "DeepSeek V4 Flash",
              base_url: "https://api.deepseek.com/anthropic", model: "deepseek-v4-flash", temperature: null,
              timeout: 120, has_api_key: true }],
}

function mockSend(text: string) {
  const req = JSON.parse(text)
  if (req.method === "cancel") return
  const reply = (result: any) => setTimeout(() => handle(JSON.stringify({ type: "response", id: req.id, ok: true, result })), 120)
  const options = { themes: ["sakura", "aurora"], teacher_styles: ["warm", "strict", "humorous", "calm"],
    detail_levels: ["brief", "normal", "detailed"], example_amounts: ["few", "normal", "many"],
    analogy_domains: ["life", "games", "programming", "sports", "music", "cooking", "film", "business", "nature"] }
  switch (req.method) {
    case "app.bootstrap":
      return reply({ version: "2.0-preview", account: { name: "MentorFlow", role_label: "管理员", settings_available: true },
                     settings: mockState.settings, settings_revision: mockState.settings_revision, options, notice: null, ready: true })
    case "settings.get": return reply({ settings: mockState.settings, settings_revision: mockState.settings_revision, options })
    case "settings.save": Object.assign(mockState.settings, req.params.changes); mockState.settings_revision++; return reply({ settings: mockState.settings, settings_revision: mockState.settings_revision })
    case "account.profile_get": return reply({ ...mockState.profile })
    case "account.profile_save":
      mockState.profile = { ...mockState.profile, nickname: req.params.nickname, signature: req.params.signature, version: mockState.profile.version + 1 }
      return reply({ ...mockState.profile })
    case "account.profile_choose_avatar": return reply({ preview_url: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='200'%3E%3Crect width='320' height='200' fill='%23d65a86'/%3E%3Ccircle cx='160' cy='100' r='70' fill='%23fff'/%3E%3C/svg%3E", width: 320, height: 200 })
    case "account.profile_save_avatar":
      mockState.profile = { ...mockState.profile, avatar_url: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='64' height='64'%3E%3Ccircle cx='32' cy='32' r='32' fill='%23d65a86'/%3E%3C/svg%3E", version: mockState.profile.version + 1 }
      return reply({ ...mockState.profile })
    case "account.profile_delete_avatar":
      mockState.profile = { ...mockState.profile, avatar_url: null, version: mockState.profile.version + 1 }
      return reply({ ...mockState.profile })
    case "models.providers": return reply({ providers: [
      { id: "deepseek", name: "DeepSeek", default_base_url: "https://api.deepseek.com/anthropic", default_model: "deepseek-v4-flash", search_supported: true, search_verified: true },
      { id: "qwen", name: "通义千问", default_base_url: "https://dashscope.aliyuncs.com/api/v1", default_model: "qwen-plus", search_supported: true, search_verified: false },
      { id: "kimi", name: "Kimi", default_base_url: "https://api.moonshot.cn/v1", default_model: "kimi-k3", search_supported: true, search_verified: false },
      { id: "zhipu", name: "智谱 GLM", default_base_url: "https://open.bigmodel.cn/api/paas/v4", default_model: "glm-5.3", search_supported: true, search_verified: false },
      { id: "openai", name: "OpenAI", default_base_url: "https://api.openai.com/v1", default_model: "gpt-6-astra", search_supported: true, search_verified: false },
      { id: "anthropic", name: "Anthropic Claude", default_base_url: "https://api.anthropic.com", default_model: "claude-opus-5-5", search_supported: true, search_verified: false },
      { id: "gemini", name: "Google Gemini", default_base_url: "https://generativelanguage.googleapis.com/v1beta", default_model: "gemini-3.8-flash", search_supported: true, search_verified: false },
    ] })
    case "models.list": return reply({ presets: mockState.presets, active_id: "p1", search_supported: true })
    case "models.test": return reply({ ok: true, latency_ms: 812, model: "deepseek-v4-flash", reply: "OK" })
    case "usage.summary": return reply({ total: { calls: 3, input_tokens: 45629, cached_input_tokens: 38656, output_tokens: 4618,
      search_requests: 8, cache_hit_rate: "0.8472", amount: "0.02621812", currency: "CNY", usage_complete: true, amount_complete: true },
      groups: [{ key: "connection_test", calls: 2, input_tokens: 40, cached_input_tokens: 0, output_tokens: 4, cache_hit_rate: "0", amount: "0.0001", currency: "CNY" },
               { key: "research", calls: 1, input_tokens: 45589, cached_input_tokens: 38656, output_tokens: 4614, cache_hit_rate: "0.8479", amount: "0.0261", currency: "CNY" }] })
    case "plans.list": return reply({ plans: [], study_day: new Date().toISOString().slice(0, 10) })
    default: return setTimeout(() => handle(JSON.stringify({ type: "response", id: req.id, ok: true, result: { ok: true } })), 80)
  }
}
