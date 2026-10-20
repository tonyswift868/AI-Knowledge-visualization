export const fixture160: any = {
  checks: 0, stateCalls: 0, stateSaved: false, releaseState: null as null | (() => void),
  appMandatory: false, appStatusCalls: 0,
  bootstrapAccount: {name:"account-A",role_label:"学习者"},
  accountId: "account-A",
  draftGetCalls: 0, draftSetCalls: 0, draftSetFail: false, durableDraft: "", events: [] as string[],
  status: {available:false,busy:false,state:"HEALTHY",version:"9.160.0",current:"9.160.0",message:"尚未检查更新。"},
}
export class BridgeFailure extends Error { constructor(public code:string,message:string){super(message)} }
export const on = () => () => undefined
export async function connect() { return true }
export async function call(method:string, _params:any = {}) {
  if (method === "app.bootstrap") return {version:"9.160.0",account:fixture160.bootstrapAccount,settings:{theme:"sakura",font_scale:1,reduce_motion:true},options:{},notice:null,ready:true}
  if (method === "sync.status") return null
  if (method === "models.status") return {state:"unknown",short:"模型待验证"}
  if (method === "classroom.enter") return {
    plan:{id:"plan160",goal:"测试课程",state:"active"},
    module:{id:"module160",title:"课堂保存测试",number:1,total:1,state:"active"},
    points:[{id:"point160",title:"当前知识点",minutes:15,taught:false,state:"active",current:true}],
    step:"teaching",question:null,messages:[],can_skip:false,red:[],more_modules:false,check:null,
    conversation:null,teaching_version:null,direct_review:null,today:null,
  }
  if (method === "classroom.draft_get") {
    fixture160.draftGetCalls++
    fixture160.events.push("draft_get")
    return {text:fixture160.durableDraft}
  }
  if (method === "classroom.draft_set") {
    fixture160.draftSetCalls++
    fixture160.events.push("draft_set")
    if (fixture160.draftSetFail) return {saved:false}
    fixture160.durableDraft = _params.text || ""
    return {saved:true}
  }
  if (method === "classroom.state") {
    fixture160.stateCalls++
    fixture160.events.push("classroom.state")
    return await new Promise(resolve => { fixture160.releaseState = () => { fixture160.stateSaved = true; resolve({saved:true}) } })
  }
  if (method === "classroom.leave" || method.startsWith("classroom.cancel_prefetch") || method.startsWith("speech.")) return {ok:true}
  if (method === "speech.status") return {ready:false,components:[],download:null}
  if (method === "update.status") {
    fixture160.appStatusCalls++
    if (fixture160.appMandatory) fixture160.status = {available:true,busy:false,state:"MANDATORY_UPDATE",version:"9.160.5",current:"9.160.5",latest:"9.160.6",mandatory:true,mandatory_floor:"9.160.6",mandatory_reason:"support_floor",message:"必须安装新版本"}
    return {...fixture160.status}
  }
  if (method === "update.automatic" || method === "update.check") {
    fixture160.checks++
    fixture160.status = {available:false,busy:false,state:"UP_TO_DATE",version:"9.160.0",current:"9.160.0",message:"当前已是最新版本"}
    return {...fixture160.status}
  }
  return {ok:true}
}
