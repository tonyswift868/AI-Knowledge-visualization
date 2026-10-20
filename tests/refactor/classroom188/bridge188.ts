import initial from './states188.json'

export type CallEntry = { method: string; params: any }
export const calls: CallEntry[] = []
let room: any = structuredClone(initial.teaching)
let slowNext = false
let slowReply = false
let currentSlow: { id: string; resolve: (value: any) => void; snapshot: any } | null = null
const listeners = new Map<string, Set<(data: any) => void>>()

export class BridgeFailure extends Error {
  constructor(public code: string, message: string) { super(message) }
}

export function scenario188(name: string) {
  calls.length = 0
  currentSlow = null
  slowNext = name === 'slowTeaching'
  slowReply = name === 'slowTestOffer' || name === 'slowObjective'
  room = structuredClone((initial as any)[name] ?? initial.teaching)
}

export const on = (name: string, callback: (data: any) => void) => {
  if (!listeners.has(name)) listeners.set(name, new Set())
  listeners.get(name)!.add(callback)
  return () => listeners.get(name)?.delete(callback)
}
export function publishReady188() {
  room.test_readiness = { ...(room.test_readiness ?? {}), module_test: true, skip_test: true, retest: true, check: true }
  room.material_notice = null
  if (currentSlow) {
    currentSlow.snapshot.test_readiness = structuredClone(room.test_readiness)
    currentSlow.snapshot.material_notice = null
  }
  for (const callback of listeners.get('classroom.materials_ready') ?? []) callback({ plan_id: 'a'.repeat(32) })
}
export function finishSlow188() {
  if (!currentSlow) return
  const slow = currentSlow; currentSlow = null
  slow.resolve(structuredClone(slow.snapshot))
}

export async function call(method: string, params: any = {}, opts: any = {}) {
  calls.push({ method, params })
  if (method === 'cancel') {
    const id = params.target
    if (currentSlow?.id === id) {
      const old = currentSlow
      currentSlow = null
      // Return a stale snapshot after the replacement action, exercising the
      // component's generation fence without depending on backend timing.
      setTimeout(() => old.resolve(structuredClone(old.snapshot)), 120)
    }
    return { cancelled: true }
  }
  if (method === 'speech.status') return { ready: false, components: [], download: null }
  if (method.startsWith('speech.')) return { accepted: true, stopped: true }
  if (method === 'classroom.prefetch' || method === 'classroom.cancel_prefetch') return { ready: 0, total: 0 }
  if (method === 'classroom.draft_get') return { text: '' }
  if (method === 'classroom.draft_set') return { saved: true }
  if (method === 'classroom.enter' || method === 'classroom.state') return structuredClone(room)
  if (method === 'classroom.next' && slowNext) {
    slowNext = false
    const snapshot = structuredClone(room)
    return await new Promise(resolve => {
      currentSlow = { id: calls.length.toString(), resolve, snapshot }
      opts.onStream?.('旧操作的迟到文字')
      opts.signal?.addEventListener('abort', () => {
        const old = currentSlow
        currentSlow = null
        if (old) setTimeout(() => old.resolve(structuredClone(old.snapshot)), 120)
      }, { once: true })
    })
  }
  if ((method === 'classroom.reply' || method === 'classroom.ask') && slowReply) {
    slowReply = false
    const snapshot = structuredClone(room)
    return await new Promise(resolve => {
      currentSlow = { id: calls.length.toString(), resolve, snapshot }
      opts.onStream?.('老师回复仍在生成')
      opts.signal?.addEventListener('abort', () => {
        const old = currentSlow; currentSlow = null
        if (old) setTimeout(() => old.resolve(structuredClone(old.snapshot)), 120)
      }, { once: true })
    })
  }
  if (method === 'classroom.reply' || method === 'classroom.ask') {
    room = structuredClone((initial as any).reply)
    return structuredClone(room)
  }
  if (method === 'classroom.next_module') {
    const wasAssessmentOffer = room.step === 'test_offer' || room.step === 'test_done'
    room = structuredClone(params.module_id === 'm1' ? (initial as any).testOffer : (initial as any).module2)
    if (!params.module_id && !wasAssessmentOffer) {
      // Switching away mid-lesson saves a resumable module position, not a
      // deferred assessment entry. Only test offers/retests create that row.
      room.deferred_module_tests = []
      const saved = room.module_options?.find((option: any) => option.module_id === 'm1')
      if (saved) { saved.state = 'untested'; saved.step = 'teaching' }
    }
    return structuredClone(room)
  }
  if (method === 'classroom.test') {
    room = structuredClone((initial as any).testing)
    return structuredClone(room)
  }
  if (method === 'classroom.retry') return structuredClone(room)
  if (method === 'classroom.cancel_preparation') {
    room = structuredClone((initial as any).teaching)
    return structuredClone(room)
  }
  if (method === 'classroom.leave') return { left: true }
  return structuredClone(room)
}

