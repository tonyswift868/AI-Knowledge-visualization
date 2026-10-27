export const settings = { version: 1, theme: "sakura", font_scale: 1, reduce_motion: true }

const grouped = [
  "分片键特点：应具备高基数和分布均匀等特征。\n这些特征有助于把数据均衡分散到各个分片。",
  "选择分片键时要结合查询条件，避免查询无法定位目标分片。",
  "复合分片键要按字段组合的实际区分能力评估。",
  "订单表可能通过用户、商户或时间等不同路径查询。",
  "查询路径不同会影响分片键选择与路由效率。",
  "按访问模式权衡数据分布和查询代价。",
]

export const calls: Array<{method:string; params:any}> = []
export const groupedPoints = grouped
export const oldSnapshot = [
  "旧快照要点一：选择键要考虑数据分布。",
  "旧快照要点二：高基数让数据更容易均衡分布。",
  "旧快照要点三：查询条件应包含分片键。",
  "旧快照要点四：不含分片键可能需要访问多个分片。",
  "旧快照要点五：组合字段共同决定数据路由。",
  "旧快照要点六：按订单查询方式权衡选择。",
]
let lastVerdict: boolean | null = null

export function resetCalls() { calls.splice(0, calls.length); lastVerdict = null }

export async function call(method:string, params:any = {}) {
  calls.push({method, params})
  if (method === "class.recall_reveal") return {reference:"# 分片键特点\n\n高基数且分布均匀的特征有助于均衡数据。\n\n选择时结合实际查询路径。", key_points:grouped}
  if (method === "class.recall_draft") return {}
  if (method === "class.recall_submit") {
    lastVerdict = params.correct
    return {correct:params.correct, missed:grouped.map((_,i)=>i).filter(i=>!params.ticked.includes(i))}
  }
  throw new Error("Unexpected offline bridge call: " + method)
}

export function verdictForHarness() { return lastVerdict }
