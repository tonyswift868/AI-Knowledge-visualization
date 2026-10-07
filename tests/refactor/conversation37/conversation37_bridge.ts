// Explicit MF04/bridge fixture. The rendered Classroom and Markdown are real.
import states from "./states.json"
export const calls: string[] = []
export const requests: { method: string; params: Record<string, unknown> }[] = []
let room = states.opening
let numeric = false
export function showNumeric() { numeric = true; room = states.calc }
export function showRecall() {
  room = { ...states.question, question: { ...states.question.question!, id: "recall81", kind: "recall",
    options: [], revealed: false, reference: "压力相同时面积越小压强越大。", key_points: ["压力相同", "面积越小压强越大"] } } as typeof room
}
export class BridgeFailure extends Error { constructor(public code: string, message: string) { super(message) } }
export const on = () => () => undefined
export async function call(method: string, params: Record<string, unknown> = {}) {
  calls.push(method)
  requests.push({ method, params })
  if (method === "speech.status") return { ready: false, message: "离线测试，未启用朗读", components: [], download: null }
  if (method === "classroom.source") return { ...states.opening.messages[0].sources[0], section: ["压强 / 第1段"] }
  if (method === "classroom.choose") room = states.teaching
  if (method === "classroom.confused") room = states.confused
  if (method === "classroom.ask") room = room.question ? { ...room, messages: states.ask.messages } : states.ask
  if (method === "classroom.reply") room = room.question ? states.closing : states.back
  if (method === "classroom.back") room = states.back
  if (method === "classroom.next") room = states.question
  if (method === "classroom.answer") room = numeric ? states.calc_answer : states.closing
  if (method === "classroom.why") room = states.why
  if (method === "classroom.leave") return { accepted: true }
  return structuredClone(room)
}
