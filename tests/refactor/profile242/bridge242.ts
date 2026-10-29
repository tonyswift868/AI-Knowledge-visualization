type Profile = { nickname: string; signature: string; version: number; avatar_url: string | null; available: boolean; offline: boolean }
export class BridgeFailure extends Error { code: string; constructor(code: string, message: string) { super(message); this.code = code } }
let mode = 'online'
let profile: Profile
let delayedSave: { resolve: (value: Profile) => void } | null = null
let delayNextSave = false
let delayedGet: { resolve: (value: Profile) => void } | null = null
let delayNextGet = false
let lastCrop: {x:number;y:number;size:number}|null = null
const svg = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='200' viewBox='0 0 320 200'%3E%3Crect width='160' height='200' fill='%23d65a86'/%3E%3Crect x='160' width='160' height='200' fill='%234a81d4'/%3E%3Ccircle cx='240' cy='100' r='46' fill='%23f5cb56'/%3E%3C/svg%3E"
function reset(next = 'online') {
  mode = next
  delayedSave = null
  delayedGet = null
  delayNextSave = false
  delayNextGet = false
  profile = { nickname: 'Tony', signature: '仅本人可见', version: 1, avatar_url: next === 'unsupported' ? null : svg,
    available: next !== 'unsupported', offline: next === 'offline' }
}
reset()
declare global { interface Window { configureProfile242?: (mode?: string) => void } }
window.configureProfile242 = (next) => reset(next)
window.delayProfile242Save = () => { delayNextSave = true }
export function on(_name: string, _fn: (data: any) => void) { return () => undefined }
export async function call<T = any>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  await new Promise(resolve => setTimeout(resolve, 5))
  if (method === 'account.profile_get') {
    if (mode === 'unsupported') throw new BridgeFailure('UNKNOWN_METHOD', 'not installed')
    if (delayNextGet) {
      delayNextGet = false
      const response = { ...profile }
      return await new Promise<T>(resolve => { delayedGet = { resolve: _value => resolve(response as T) } })
    }
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
    lastCrop = params.crop as {x:number;y:number;size:number}
    profile = { ...profile, avatar_url: svg, version: profile.version + 1 }
    return { ...profile } as T
  }
  if (method === 'account.profile_delete_avatar') {
    profile = { ...profile, avatar_url: null, version: profile.version + 1 }
    return { ...profile } as T
  }
  return { ok: true } as T
}
declare global { interface Window { delayProfile242Save?: () => void; resolveProfile242Save?: () => void; setProfile242?: (value: Profile) => void } }
window.resolveProfile242Save = () => { delayedSave?.resolve(profile); delayedSave = null }
window.setProfile242 = value => { profile = { ...value } }

declare global { interface Window { getProfile242Crop?: () => typeof lastCrop } }
window.getProfile242Crop = () => lastCrop

declare global { interface Window { delayProfile242Get?: () => void; resolveProfile242Get?: () => void } }
window.delayProfile242Get = () => { delayNextGet = true }
window.resolveProfile242Get = () => { delayedGet?.resolve(profile); delayedGet = null }

declare global { interface Window { getBridgeProfile242?: () => Profile } }
window.getBridgeProfile242 = () => ({ ...profile })

declare global { interface Window { hasPendingProfile242Save?: () => boolean; hasPendingProfile242Get?: () => boolean } }
window.hasPendingProfile242Save = () => !!delayedSave
window.hasPendingProfile242Get = () => !!delayedGet
