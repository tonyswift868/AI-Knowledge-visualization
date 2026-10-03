// Explicit MF04/bridge fixture. The rendered Classroom and Markdown are real.
import states from "./states.json"
export const calls: string[] = []
let room = states.opening
export class BridgeFailure extends Error { constructor(public code: string, message: string) { super(message) } }
export const on = () => () => undefined
export async function call(method: string, params: Record<string, unknown> = {}) {
  calls.push(method)
  if (method === "speech.status") return { ready: false, message: "离线测试，未启用朗读", components: [], download: null }
  if (method === "classroom.source") return { ...states.opening.messages[0].sources[0], section: ["压强 / 第1段"] }
  if (method === "classroom.choose") room = states.teaching
  if (method === "classroom.confused") room = states.confused
  if (method === "classroom.ask") room = states.ask
  if (method === "classroom.back") room = states.back
  if (method === "classroom.next") room = states.question
  if (method === "classroom.answer") room = states.closing
  if (method === "classroom.why") room = states.why
  if (method === "classroom.leave") return { accepted: true }
  return structuredClone(room)
}
