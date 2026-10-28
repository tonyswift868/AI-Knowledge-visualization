type Profile = { nickname: string; signature: string; version: number; avatar_url: string | null; available: boolean; offline: boolean }
export class BridgeFailure extends Error { code: string; constructor(code: string, message: string) { super(message); this.code = code } }
let mode = 'online'
let profile: Profile
let delayedSave: { resolve: (value: Profile) => void } | null = null
let delayNextSave = false
const svg = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='64' height='64'%3E%3Crect width='64' height='64' fill='%23d65a86'/%3E%3C/svg%3E"
function reset(next = 'online') {
  mode = next
  delayedSave = null
  delayNextSave = false
  profile = { nickname: 'Tony', signature: '仅本人可见', version: 1, avatar_url: next === 'delete' ? svg : null,
    available: next !== 'unsupported', offline: next === 'offline' }
}
reset()
declare global { interface Window { configureProfile226?: (mode?: string) => void } }
window.configureProfile226 = (next) => reset(next)
window.delayProfile226Save = () => { delayNextSave = true }
export function on(_name: string, _fn: (data: any) => void) { return () => undefined }
export async function call<T = any>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  await new Promise(resolve => setTimeout(resolve, 5))
  if (method === 'account.profile_get') {
    if (mode === 'unsupported') throw new BridgeFailure('UNKNOWN_METHOD', 'not installed')
    return { ...profile } as T
  }
  if (mode === 'offline') throw new BridgeFailure('PROFILE_OFFLINE', 'offline')
  if (method === 'account.profile_save') {
    profile = { ...profile, nickname: String(params.nickname), signature: String(params.signature), version: profile.version + 1 }
    if (delayNextSave) {
      delayNextSave = false
      const response = { ...profile }
      return await new Promise<T>(resolve => { delayedSave = { resolve: _value => resolve(response as T) } })
    }
    return { ...profile } as T
  }
  if (method === 'account.profile_choose_avatar') return { preview_url: svg, width: 320, height: 200, cancelled: false } as T
  if (method === 'account.profile_save_avatar') {
    profile = { ...profile, avatar_url: svg, version: profile.version + 1 }
    return { ...profile } as T
  }
  if (method === 'account.profile_delete_avatar') {
    profile = { ...profile, avatar_url: null, version: profile.version + 1 }
    return { ...profile } as T
  }
  return { ok: true } as T
}
declare global { interface Window { delayProfile226Save?: () => void; resolveProfile226Save?: () => void; setProfile226?: (value: Profile) => void } }
window.resolveProfile226Save = () => { delayedSave?.resolve(profile); delayedSave = null }
window.setProfile226 = value => { profile = { ...value } }
