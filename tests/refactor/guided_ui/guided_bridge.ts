import * as data from "./guided_snapshots"

export const log: { method: string; params: Record<string, unknown> }[] = []
export let room = data.snapshot()
let saved: typeof room | null = null
let hold = false
let pending: (() => void) | null = null
let fail = false
let live = true
export function configure(value = data.snapshot(), options: { hold?: boolean; fail?: boolean; live?: boolean } = {}) {
  room = value; saved = null; hold = !!options.hold; pending = null; fail = !!options.fail; live = options.live ?? true
  log.length = 0
}
export function release() { hold = false; pending?.(); pending = null }
export class BridgeFailure extends Error { constructor(public code: string, message: string) { super(message) } }
export function on() { return () => undefined }
export async function call(method: string, params: Record<string, unknown> = {}, opts: { signal?: AbortSignal } = {}) {
  log.push({ method, params })
  if (method === "speech.status") return { ready: false, message: "离线核对：朗读未启用", components: [], download: null }
  if (method === "plan.report") return data.report(live)
  if (method === "classroom.source") return { title: "教材：等式的性质", text: "等式两边同时加上或减去同一个数，等式仍然成立。", url: null, section: ["等式"] }
  if (["classroom.enter", "classroom.state"].includes(method)) return structuredClone(room)
  if (!method.startsWith("classroom.") || method === "classroom.leave") return { accepted: true }
  if (hold) await new Promise<void>((resolve, reject) => {
    pending = resolve
    opts.signal?.addEventListener("abort", () => { pending = null; reject(new BridgeFailure("CANCELLED", "已停止")) }, { once: true })
  })
  if (fail) { fail = false; throw new BridgeFailure("FIXTURE_FAILURE", "假桥模拟失败，请重试") }
  if (method === "classroom.next") {
    room = room.lesson?.step === 1 ? data.guide(room) : data.casual(room)
  } else if (method === "classroom.choose") {
    room = data.guideReply(room, Number(params.option))
  } else if (method === "classroom.back") {
    room = { ...(saved ?? room), messages: room.messages }
  } else if (method === "classroom.reply") {
    const text = String(params.text)
    if (room.question?.options.includes(text)) {
      const correct = room.question.options[1] === text
      room = { ...room, messages: [...room.messages, data.message("choice", text, "user"),
        data.message("verdict", correct ? "✓ 答对了" : "✗ 这次还没答对")] }
      if (correct) {
        room = { ...room, step: "teach", question: null,
          lesson: { ...data.lesson(), point_id: "p2", point_title: "移项与解方程" },
          points: room.points.map((p) => ({ ...p, taught: p.id === "p1", current: p.id === "p2" })),
          messages: [...room.messages, data.message("step", "现在进入下一点：移项与解方程。移项的背后，也是等号两边做同样的操作。")] }
      } else {
        room = data.casual({ ...room, messages: [...room.messages,
          data.message("step_reply", "别急，等号两边都减去 3：左边只剩 x，右边是 8 − 3，所以 x = 5。换一道备好的随口问再试试。") ] }, true)
      }
    } else {
      saved = structuredClone(room)
      room = { ...room, question: null, lesson: { ...room.lesson!, awaiting: "return" },
        messages: [...room.messages, data.message("speech", text, "user"),
          data.message("speech", "可以想象天平两边各拿走同样重量的物品：原来平衡，拿走后还是平衡。回到刚才，我们接着看等式的性质。") ] }
    }
  }
  return structuredClone(room)
}
