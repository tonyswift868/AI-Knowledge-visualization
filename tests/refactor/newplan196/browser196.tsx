import * as React from "react"
import {createRoot} from "react-dom/client"
import {Plans} from "../../../refactor/learning/web/src/pages/Plans"
import {Home} from "../../../refactor/learning/web/src/pages/Home"
import {NewPlan} from "../../../refactor/learning/web/src/pages/NewPlan"
import {useApp} from "../../../refactor/learning/web/src/store"
import "../../../refactor/learning/web/src/theme.css"
import {calls196,reset196,seedArchived196} from "./bridge196"

const root=createRoot(document.getElementById("root")!)
const wait=(ms=40)=>new Promise(r=>setTimeout(r,ms))
async function until(test:()=>boolean,label:string){const end=performance.now()+5000;while(!test()){if(performance.now()>end)throw Error(`timeout: ${label}`);await wait(20)}}
function button(label:string){return [...document.querySelectorAll<HTMLButtonElement>("button")].find(b=>b.textContent?.trim().includes(label))}
function check(ok:unknown,label:string){if(!ok)throw Error(label)}
let stage="initial"
function Harness(){const route=useApp(s=>s.route);return route==="new-plan"?<NewPlan/>:route==="home"?<Home/>:<Plans/>}

async function run(){
  const checks:string[]=[]
  const evidence:any={}
  // A stale pointer must be discarded only when the backend says that draft is absent.
  stage="stale fallback";reset196()
  useApp.getState().set({ready:true,account:{name:"测试",role_label:"学习者"},planId:"missing-draft-196",route:"new-plan"})
  root.render(<Harness/>)
  await until(()=>document.body.textContent?.includes("新建计划表单已就绪"),"stale draft recovers into a fresh form")
  check(!document.querySelector('[role="alert"]'),"missing draft no longer leaves the error page")
  evidence.stale={methods:calls196.map(c=>c.method),getIds:calls196.filter(c=>c.method==="plan.get").map(c=>c.params.plan_id),currentCalls:calls196.filter(c=>c.method==="plan.current").length}
  check(evidence.stale.getIds.length===1&&evidence.stale.getIds[0]==="missing-draft-196"&&evidence.stale.currentCalls===1,"DRAFT_NOT_FOUND retries through plan.current exactly once")
  checks.push("stale NewPlan pointer recovers to a fresh form after DRAFT_NOT_FOUND")

  // A valid continuation remains bound to its existing draft.
  stage="valid continuation";reset196()
  useApp.getState().set({planId:"valid-continuation",route:"new-plan"})
  root.render(<Harness/>)
  await until(()=>document.body.textContent?.includes("有效的未完成创建"),"valid continuation remains open")
  evidence.continuation={methods:calls196.map(c=>c.method),getIds:calls196.filter(c=>c.method==="plan.get").map(c=>c.params.plan_id),currentCalls:calls196.filter(c=>c.method==="plan.current").length}
  check(evidence.continuation.getIds.join()==="valid-continuation"&&evidence.continuation.currentCalls===0,"valid continue action still uses plan.get")
  checks.push("valid draft continuation still loads its selected ID")

  // Ordinary network and unreadable-file failures must retain the retry path.
  for(const [id,label] of [["network-failure","network"],["unreadable-draft","unreadable"]] as const){
    stage=label
    reset196()
    useApp.getState().set({planId:id,route:"new-plan"})
    root.render(<Harness/>)
    await until(()=>!!document.querySelector('[role="alert"]'),`${label} error remains visible`)
    check(!!button("重试"),`${label} keeps retry button`)
    button("重试")!.click()
    await until(()=>calls196.filter(c=>c.method==="plan.get").length===2,`${label} retries original get`)
    evidence[label]={methods:calls196.map(c=>c.method),getIds:calls196.filter(c=>c.method==="plan.get").map(c=>c.params.plan_id),currentCalls:calls196.filter(c=>c.method==="plan.current").length}
    check(evidence[label].currentCalls===0&&evidence[label].getIds.every((value:string)=>value===id),`${label} is not misclassified as an absent draft`)
    checks.push(`${label} failure remains retryable and never falls through to plan.current`)
  }

  // Reproduce the user's actual sequence: archive in Plans, then start a new plan.
  stage="archive to Plans new";reset196()
  useApp.getState().set({planId:"archived-after-click",route:"plans"})
  root.render(<Harness/>)
  await until(()=>!!document.querySelector('[title^="归档"]'),"active plan and archive action rendered")
  document.querySelector<HTMLButtonElement>('[title^="归档"]')!.click()
  await until(()=>calls196.some(c=>c.method==="plan.archive"),"archive action called")
  await until(()=>!document.querySelector('[title^="归档"]'),"archived plan removed from current list")
  button("新建计划")!.click()
  await until(()=>document.body.textContent?.includes("新建计划表单已就绪"),"archive then new plan opens a fresh form")
  evidence.archiveToPlansNew={methods:calls196.map(c=>c.method),getIds:calls196.filter(c=>c.method==="plan.get").map(c=>c.params.plan_id),currentCalls:calls196.filter(c=>c.method==="plan.current").length,planIdAfter:useApp.getState().planId}
  check(evidence.archiveToPlansNew.methods.includes("plan.archive")&&evidence.archiveToPlansNew.getIds.length===0&&evidence.archiveToPlansNew.currentCalls===1,"Plans archive then new clears stale ID and requests plan.current")
  checks.push("Plans page archive control followed by its NewPlan control opens a fresh form")

  // The empty-home CTA also clears a selected plan ID before navigation.
  stage="Home empty CTA";seedArchived196()
  useApp.getState().set({planId:"archived-after-click",route:"home"})
  root.render(<Harness/>)
  await until(()=>!!button("开始第一个计划"),"empty home CTA rendered")
  button("开始第一个计划")!.click()
  await until(()=>document.body.textContent?.includes("新建计划表单已就绪"),"home starts a fresh plan")
  evidence.homeNew={methods:calls196.map(c=>c.method),getIds:calls196.filter(c=>c.method==="plan.get").map(c=>c.params.plan_id),currentCalls:calls196.filter(c=>c.method==="plan.current").length,planIdAfter:useApp.getState().planId}
  check(evidence.homeNew.getIds.length===0&&evidence.homeNew.currentCalls===1,"Home new plan clears stale ID")
  checks.push("Home empty-state new-plan CTA opens a fresh form")

  stage="Home header CTA";seedArchived196()
  useApp.getState().set({planId:"archived-after-click",route:"home"})
  root.render(<Harness/>)
  await until(()=>!!button("新建计划"),"home header CTA rendered")
  button("新建计划")!.click()
  await until(()=>document.body.textContent?.includes("新建计划表单已就绪"),"home header starts a fresh plan")
  evidence.homeHeaderNew={methods:calls196.map(c=>c.method),getIds:calls196.filter(c=>c.method==="plan.get").map(c=>c.params.plan_id),currentCalls:calls196.filter(c=>c.method==="plan.current").length,planIdAfter:useApp.getState().planId}
  check(evidence.homeHeaderNew.getIds.length===0&&evidence.homeHeaderNew.currentCalls===1,"Home header clears stale ID")
  checks.push("Home header new-plan CTA opens a fresh form")

  ;(window as any).plan196Result={ok:true,checks,evidence}
}

run().catch(e=>{(window as any).plan196Result={ok:false,error:String(e?.stack||e),checks,evidence,stage,body:document.body.innerText,calls:calls196,route:useApp.getState().route,planId:useApp.getState().planId}})
