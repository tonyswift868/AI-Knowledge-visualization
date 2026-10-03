// Explicit offline bridge fixture. The production hook and Classroom are real.
export const log: { method: string; params: any }[] = []
export let ready = true
export let heldStart: (() => void) | null = null
export let holdStart = false
export let heldEnd: (() => void) | null = null
export let holdEnd = false
export let heldAppend: (() => void) | null = null
export let holdAppend = false
export let request: { opts: any; resolve: (r: any) => void; reject: (e: any) => void } | null = null
let serial = 0
export const room = () => ({ plan: { id: "p", goal: "测试", state: "active" },
  module: { id: "m", title: "测试模块", number: 1, total: 1 }, points: [], step: "teach", question: null,
  messages: [], can_skip: false, red: [], today: null })
let restored: any = null
export function restoreAfterCancel(content: string) {
  restored = { ...room(), messages: [{ id: "cancelled", role: "assistant", kind: "speech", content }] }
}
const listeners = new Map<string, Set<(e: any) => void>>()
export class BridgeFailure extends Error { constructor(public code: string, message: string) { super(message) } }
export function on(name: string, fn: (e: any) => void) {
  if (!listeners.has(name)) listeners.set(name, new Set())
  listeners.get(name)!.add(fn)
  return () => listeners.get(name)!.delete(fn)
}
export function configure(options: { ready?: boolean; holdStart?: boolean; holdEnd?: boolean; holdAppend?: boolean }) {
  if (options.ready !== undefined) ready = options.ready
  holdStart = options.holdStart ?? false
  holdEnd = options.holdEnd ?? false
  holdAppend = options.holdAppend ?? false
  restored = null
}
export function finish(content = "第一句。第二句。") {
  const r = { ...room(), messages: [{ id: `m-${++serial}`, role: "assistant", kind: "speech", content }] }
  request?.resolve(r); request = null // An aborted/unmounted request may deliver a late response.
}
export async function call(method: string, params: any = {}, opts: any = {}) {
  log.push({ method, params })
  if (method === "speech.status") return { ready, components: [], download: null }
  if (method === "speech.stream_start" && holdStart) await new Promise<void>((r) => { heldStart = r })
  if (method === "speech.stream_end" && holdEnd) await new Promise<void>((r) => { heldEnd = r })
  if (method === "speech.stream_append" && holdAppend) await new Promise<void>((r) => { heldAppend = r })
  if (method === "classroom.state") return restored ?? room()
  if (method === "classroom.enter") return room()
  if (["classroom.next", "classroom.reply", "classroom.ask"].includes(method)) return new Promise((resolve, reject) => {
    request = { opts, resolve, reject }
    opts.signal?.addEventListener("abort", () => { reject(new BridgeFailure("CANCELLED", "已取消")); request = null })
  })
  return { accepted: true, stopped: true }
}
