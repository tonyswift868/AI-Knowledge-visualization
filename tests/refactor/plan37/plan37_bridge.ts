import type { Draft } from "../../../refactor/learning/web/src/pages/NewPlan"
export class BridgeFailure extends Error {}
const citation = { chunk_id: "c1", title: "OpenStax · Pressure", url: "https://openstax.org", locator: "11.3 · 段落 1", quote: "Pressure is the force per unit perpendicular area over which the force is applied." }
export const draft: Draft = {
  plan_id: "offline37", name: "初二物理：压强与浮力", goal: "初二物理：压强与浮力", phase: "sources", busy: null,
  form: { goal: "初二物理：压强与浮力，掌握生活应用和计算方法", final_goal: "理解原理", level: "beginner", level_note: "", notes: "", start: "2026-10-03", end: "2026-10-03", daily_cap: 60, weekday_weights: [1,1,1,1,1,1,1], special_days: [], only_mine: false },
  messages: [{ id: "s", role: "ai", kind: "sources", text: "自动搜索已取得正文，请认可资料。", cards: ["card"] }],
  sources: [{ card_id: "card", kind: "url", url: "https://openstax.org", title: "OpenStax · Pressure", site: "OpenStax", tier: "whitelist", summary: "正规教材", relevance: 5, credibility: 5, note: "正规开放教材，已取得原文。", status: "new", chars: 3000 }],
  outline: { version: 1, stages: [{ title: "基础", modules: [{ id: "m", key: "m1", title: "压强的定义", summary: "理解压强", refs: ["s1.1"], ref_titles: ["OpenStax · Pressure"], prerequisites: [], minutes: [450,450], points: [{ title: "单位面积上的压力", summary: "理解定义", citations: [citation] }] }] }], ranges: { points: 30, learning: 450, total: [560,560], daily: [560,560], cap: 60, over_cap: true, over_300: true, suggested_end: "2026-10-12", suggested_days: 10 } }, generation: {},
}
export const methods: string[] = []
draft.sources.push(...[
  {card_id:"community39",kind:"url" as const,url:"https://zhihu.com/question/1",title:"社区解释",site:"知乎",tier:"neutral",summary:"原文",relevance:5,credibility:3,note:"一般来源",status:"new" as const,chars:3000,quality:{source_type:"community",community:true}},
  {card_id:"general39",kind:"url" as const,url:"https://news.example/lesson",title:"一般教学网站",site:"教学网站",tier:"neutral",summary:"原文",relevance:5,credibility:3,note:"一般来源",status:"new" as const,chars:3000},
  {card_id:"platform39",kind:"url" as const,url:"https://jyeoo.com/q",title:"成熟教学平台",site:"题库",tier:"whitelist",summary:"原文",relevance:4,credibility:4,note:"成熟来源",status:"new" as const,chars:3000},
])
draft.messages[0].cards=["community39","general39","platform39","card"]
draft.messages.push({id:"guide39",role:"ai",kind:"text",text:"《物理八年级下册》需登录下载。教材页面：https://basic.smartedu.cn/tchMaterial/detail；下载后点‘上传资料’传进来。"})
draft.outline!.stages[0].modules[0].points![0].source_general=true
export const detail = { plan: { id: draft.plan_id, title: draft.name, goal: draft.form!.goal, state: "ready", window: { start: "2026-10-03", end: "2026-10-12" }, total_minutes: 560 },
  stages: [{ id: "s", title: "基础", modules: [{ id: "m", title: "压强的定义", summary: "理解定义", learning_minutes: 15, check_minutes: 2,
    points: [{ id: "p", title: "单位面积上的压力", summary: "理解定义", minutes: 15, citations: [citation] }] }] }], sources: [{ id: "src", title: citation.title, url: citation.url, kind: "url" }], daily: { full_day: 56, cap: 60, ok: true, suggested_end: null } }
export async function call<T = unknown>(method: string, params: Record<string, any> = {}): Promise<T> {
  methods.push(method)
  if (method === "plan.detail") return structuredClone(detail) as T
  if (method === "plan.decide") draft.sources[0].status = params.adopt ? "adopted" : "rejected"
  if (method === "plan.outline") {
    draft.phase = "outline"
    draft.messages.push({ id: "o", role: "ai", kind: "outline", text: "这是原文知识地图。", version: 1 })
  }
  if (method === "plan.estimate" || method === "plan.extend") {
    if (method === "plan.extend") Object.assign(draft.outline!.ranges, { daily: [56,56], over_cap: false, over_300: false })
    draft.estimate = { modules: 1, points: 30, input_tokens: [0,0], output_tokens: [0,0], amount: null, model: "", blocked: draft.outline!.ranges.over_300 }
    draft.messages.push({ id: `e${methods.length}`, role: "ai", kind: "estimate", text: "按点数 × 平均 15 分钟。" })
  }
  if (method === "plan.generate") {
    draft.phase = "ready"
    draft.generation = { m: "done" }
    draft.messages.push({ id: "ready", role: "ai", kind: "ready", text: "第 1 模块已备好，可以开始。" })
  }
  return { draft: structuredClone(draft) } as T
}
export const on = (_event: string, _fn: (...args: any[]) => void) => () => {}
