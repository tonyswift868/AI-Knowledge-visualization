export const settings = { version: 1, theme: "sakura", font_scale: 1, reduce_motion: true }

export type Reveal = { reference: string; key_points: string[]; key_point_headings?: (string | null)[]; ticked?: number[] }
declare global { interface Window { __recall214Fixture?: Reveal } }
const fallback: Reveal = {
  reference: "深度分页在偏移量很大时，需要扫描并丢弃前面的记录，随着页码增加开销上升。",
  key_points: [
    "深度分页通常通过较大的 OFFSET 跳过前面记录。",
    "数据库仍需扫描并丢弃跳过的记录，不能直接从目标位置开始。",
    "偏移量越大，扫描和丢弃的工作越多，查询通常越慢。",
    "可用基于上次位置的游标分页避免重复扫描大量前置记录。",
  ],
  key_point_headings: ["深度分页的原因", "深度分页的原因", "深度分页的原因", "游标分页"],
}

export const calls: Array<{ method: string; params: any }> = []
export const fixture = () => window.__recall214Fixture ?? fallback
export async function call(method: string, params: any = {}) {
  calls.push({ method, params })
  const data = fixture()
  if (method === "class.recall_reveal") return { ...data }
  if (method === "class.recall_draft") return {}
  if (method === "class.recall_submit") {
    const missed = data.key_points.map((_, index) => index).filter(index => !params.ticked.includes(index))
    return { correct: params.correct ?? missed.length === 0, missed }
  }
  throw new Error("Unexpected offline bridge call: " + method)
}
