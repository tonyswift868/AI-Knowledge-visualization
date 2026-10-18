import * as React from "react"
import { createRoot } from "react-dom/client"
import { NewPlan, type Draft } from "../../../refactor/learning/web/src/pages/NewPlan"
import { useApp } from "../../../refactor/learning/web/src/store"
import { show, calls, resolvePlanGet } from "./source170_bridge"

const root = createRoot(document.getElementById("root")!)
const check = (v: any, why: string) => { if (!v) throw Error(why) }
async function until(f: () => any, why: string) { const end = performance.now() + 6000; while (!f()) { if (performance.now() > end) throw Error(why); await new Promise(r => setTimeout(r, 20)) } }
const data: Draft = {
  plan_id: "source170", phase: "sources", form: null, goal: "MentorFlow 项目", messages: [
    {id:"user-link", role:"user", kind:"text", text:"https://github.com/tonyswift868/mentorflow-guide"},
    {id:"sources", role:"ai", kind:"sources", text:"你提供的资料已读取", cards:["user-github","user-url","user-file"]},
  ], sources: [
    {card_id:"user-github", kind:"url", url:"https://github.com/tonyswift868/mentorflow-guide", title:"MentorFlow Guide 项目仓库", site:"GitHub", tier:"user", summary:"用户提供", relevance:null, credibility:null, note:"由你提供，正文已读取", status:"adopted", chars:71700, user_provided:true},
    {card_id:"user-url", kind:"url", url:"https://example.test/guide", title:"用户网址资料", site:"example.test", tier:"user", summary:"用户提供", relevance:null, credibility:null, note:"由你提供，正文已读取", status:"adopted", chars:2500, user_provided:true},
    {card_id:"user-file", kind:"file", url:null, title:"用户上传资料.txt", site:"本地文件", tier:"user", summary:"用户提供", relevance:null, credibility:null, note:"由你提供，正文已读取", status:"adopted", chars:1200, user_provided:true},
  ], outline:null, generation:{}, busy:null,
}
useApp.getState().set({planId:data.plan_id})
root.render(<NewPlan />)

async function run() {
  await until(() => calls.some(c => c.method === "plan.get"), "initial plan.get started")
  const oldGet = structuredClone(data)
  oldGet.sources.forEach(card => { card.status = "new" })
  const foreignDraft = structuredClone(data)
  foreignDraft.plan_id = "another-plan"
  show(foreignDraft)
  await new Promise(r => setTimeout(r, 80))
  check(!document.querySelector(".source-card"), "draft event for another plan is ignored")
  // The bridge event contains the newer adopted state while the initial read is still pending.
  show(data)
  await until(() => document.querySelectorAll(".source-card").length === 3, "all user-provided cards rendered")
  check(document.querySelectorAll('.source-card.adopted').length === 3, "new plan_draft accepted before initial get returns")
  check(!!resolvePlanGet, "deferred initial response is controllable")
  resolvePlanGet!({draft:oldGet})
  await new Promise(r => setTimeout(r, 80))
  check(document.querySelectorAll('.source-card.adopted').length === 3, "stale initial plan.get cannot overwrite newer adopted event")
  const github = document.querySelector<HTMLElement>('.source-card.adopted input[type="checkbox"]')
  check(github?.checked, "adopted GitHub card checkbox reflects backend status")
  const next = [...document.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent?.includes("按已提供的 3 份资料调整结构"))
  check(next && !next.disabled, "next-step button enabled for adopted materials")
  const boxes = [...document.querySelectorAll<HTMLInputElement>('.source-card input[type="checkbox"]')]
  check(boxes.every(b => b.checked), "upload, ordinary URL and GitHub user cards all show adopted")

  // User-provided is not a UI override: an explicit rejection stays unchecked and does not count.
  const rejected = structuredClone(data)
  rejected.sources[0].status = "rejected"
  show(rejected)
  await until(() => document.querySelectorAll(".source-card").length === 3 && document.querySelectorAll('.source-card.rejected').length === 1, "explicit rejection rendered")
  check(!document.querySelector('.source-card.rejected input[type="checkbox"]')?.checked, "explicit rejection remains unchecked")
  const disabledNext = [...document.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent?.includes("按已提供的 2 份资料调整结构"))
  check(disabledNext && !disabledNext.disabled, "remaining adopted materials still permit next step")

  // A new-plan page starts with no selected id; after plan.current resolves, it must still accept updates for that draft.
  const currentDraft = structuredClone(data)
  currentDraft.sources[0].status = "rejected"
  show(currentDraft)
  useApp.getState().set({planId:null})
  const currentStart = calls.length
  await until(() => calls.slice(currentStart).some(c => c.method === "plan.current"), "plan.current started for new page")
  await until(() => document.querySelectorAll('.source-card.rejected').length === 1, "plan.current initial state rendered")
  show(data)
  await until(() => document.querySelectorAll('.source-card.adopted').length === 3, "new-page draft event accepted after plan.current")
  check([...document.querySelectorAll<HTMLInputElement>('.source-card input[type="checkbox"]')].every(b => b.checked), "current draft event updates adopted checkbox state")
  ;(window as any).plan37Result = {ok:true, cards:3, adopted_routes:["GitHub", "URL", "upload"], explicit_rejection_preserved:true}
}
run().catch(e => { (window as any).plan37Result = {ok:false, error:String(e), body:document.body.textContent} })
