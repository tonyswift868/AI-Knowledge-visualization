import type { Draft } from "../../../refactor/learning/web/src/pages/NewPlan"

export const calls196: Array<{method:string; params:Record<string,unknown>}> = []
let archived = false
const error196=(code:string,message:string)=>Object.assign(new Error(message),{code})

const freshDraft: Draft = {
  plan_id:"fresh-draft-196", name:"新建计划", goal:"", phase:"form", form:null,
  messages:[{id:"welcome",role:"ai",kind:"form",text:"新建计划表单已就绪。"}],
  sources:[], outline:null, generation:{},
}
const continuationDraft:Draft={
  plan_id:"valid-continuation",name:"有效的未完成创建",goal:"继续原创建记录",phase:"questions",
  form:{goal:"继续原创建记录",final_goal:"",level:"beginner",level_note:"",notes:"",start:"2026-10-22",end:"2026-10-29",daily_cap:30,weekday_weights:[1,1,1,1,1,1,1],special_days:[],only_mine:false},
  messages:[{id:"continue",role:"ai",kind:"questions",text:"有效创建记录仍可继续。"}],sources:[],outline:null,generation:{},
}

export async function call<T=unknown>(method:string, params:Record<string,any>={}, _opts?:unknown):Promise<T> {
  calls196.push({method,params:{...params}})
  if(method === "plans.list") return {plans:[{
    id:"archived-after-click",title:"旧计划",goal:"旧计划",final_goal:"",state:"active",archived,
    module_count:1,window:null,total_minutes:0,today:null,
  }]} as T
  if(method === "plan.archive") { archived=!!params.archived; return {ok:true} as T }
  if(method === "plan.get") {
    const id=String(params.plan_id)
    if(id==="valid-continuation")return {draft:continuationDraft} as T
    if(id==="network-failure")throw error196("NETWORK_ERROR","网络暂时不可用，请重试。")
    if(id==="unreadable-draft")throw error196("DRAFT_UNREADABLE","这份计划的创建记录读不出来了，可以删除后重新新建。")
    throw error196("DRAFT_NOT_FOUND","找不到这份计划的创建记录。")
  }
  if(method === "plan.current") return {draft:freshDraft} as T
  if(method === "sync.status") return {state:"synced",at:null,message:""} as T
  return {ok:true} as T
}

export function on(_event:string,_handler:(value:any)=>void) { return () => {} }
export function reset196() { calls196.length=0; archived=false }
export function seedArchived196() { calls196.length=0; archived=true }
