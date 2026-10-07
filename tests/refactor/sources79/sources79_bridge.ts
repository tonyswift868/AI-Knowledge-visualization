export class BridgeFailure extends Error {}
export const requests: any[] = []
const listeners = new Map<string, Set<(data: any) => void>>()
export function on(name: string, fn: (data: any) => void) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name)!.add(fn); return () => { listeners.get(name)?.delete(fn) } }
export function emit(name: string, data: any) { listeners.get(name)?.forEach(fn => fn(data)) }
const sources = [
  { id: "c10", title: "JavaGuide", url: "https://example.test/javaguide#10", locator: "段落 10", text: "线程是操作系统进行调度的基本单位。\n这是第十段的完整原文。" },
  { id: "c2", title: "JavaGuide", url: "https://example.test/javaguide#2", locator: "段落 2", text: "进程是资源分配的基本单位。\n这是第二段的完整原文。" },
  { id: "c3", title: "JavaGuide", url: "https://example.test/javaguide#3", locator: "段落 3", text: "同一进程的多个线程共享该进程的资源。\n这是第三段的完整原文。" },
  { id: "d1", title: "补充资料", url: null, locator: "第1节", text: "每处原文可以独立展开，长文本保持可读。" },
]
export const room: any = { plan: { id: "sources79", goal: "理解进程与线程" }, module: { id: "m1", title: "进程与线程", number: 1, total: 3, state: "learning" },
  points: [{ id: "p1", title: "进程和线程", minutes: 10, taught: false, state: "new", current: true }], step: "teaching", question: null,
  messages: [{ id: "reply1", role: "assistant", kind: "text", content: "**先分清两种单位：**线程是调度单位[〔1〕](mf-source:c10)，进程是资源分配单位[〔2〕](mf-source:c2)。同一进程中的线程共享资源[〔3〕](mf-source:c3)。", sources }],
  can_skip: false, red: [], more_modules: true, check: null, today: { today_target: 30, today_confirmed: 10, today_tentative: 0 } }
export function addReply() { room.messages.push({ ...room.messages[0], id: "reply2", content: "这是新回复，出处默认折叠。[〔3〕](mf-source:c3)" }); emit("classroom.materials_ready", { plan_id: "sources79" }) }
export async function call(method: string, params: any = {}) {
  requests.push({ method, params })
  if (method === "app.window") return { teacher: { open: params.action === "teacher_open" && !params.restore, width: params.width || 320, view: params.view || { scale: 1, panX: 0, panY: 0 }, mode: "internal" } }
  if (method === "teacher.assets") return { ready: false, manifest: null, message: "离线测试，不加载模型", installation: { message: "" } }
  if (method === "speech.status") return { ready: false, message: "离线测试", components: [], download: null }
  if (method === "classroom.leave") return {}
  if (method === "classroom.enter" || method === "classroom.state") return structuredClone(room)
  if (method === "classroom.source") return { ...sources.find(s => s.id === params.chunk_id), section: [] }
  throw new Error("Unexpected offline call: " + method)
}
