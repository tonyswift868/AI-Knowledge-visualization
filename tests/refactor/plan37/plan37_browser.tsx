import * as React from "react"
import { createRoot } from "react-dom/client"
import { NewPlan } from "../../../refactor/learning/web/src/pages/NewPlan"
import { PlanDetail } from "../../../refactor/learning/web/src/pages/PlanDetail"
import { useApp } from "../../../refactor/learning/web/src/store"
import "../../../refactor/learning/web/src/theme.css"
import { draft, methods } from "./plan37_bridge"
const root = createRoot(document.getElementById("root")!)
const wait = (ms = 60) => new Promise(r => setTimeout(r, ms))
const checks: string[] = []
function check(ok: unknown, label: string) { if (!ok) throw Error(label); checks.push(label) }
function button(text: string) { return [...document.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent?.includes(text))! }
async function click(text: string) { const b = button(text); check(b && !b.disabled, text); b.click(); await wait() }
async function run() {
  useApp.getState().set({ planId: null })
  root.render(<NewPlan />); await wait(150)
  check(document.querySelector('.source-card .pill')?.textContent === "权威来源" && document.querySelector('[title="可信 5/5"]'), "source credibility")
  check(["权威（官方/教材）","成熟平台","一般网站"].every(t=>document.querySelector(`section[aria-label="${t}"]`)),"three source groups")
  check([...document.querySelectorAll(".source-title")].map(x=>x.textContent).join("|")==="OpenStax · Pressure|成熟教学平台|一般教学网站|社区解释","credibility and community source order")
  check(document.body.textContent?.includes("下载后点‘上传资料’"),"textbook instruction in chat")
  document.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click(); await wait()
  await click("调整结构")
  check(document.body.textContent?.includes("按你的资料整理出 1 个模块"), "material directory preview")
  await click("确认结构")
  // Knowledge points are in the module drawer, not all spread across the page.
  const moduleNode = document.querySelector<HTMLElement>('.outline-card .react-flow__node-module')!
  check(moduleNode, "module graph")
  moduleNode.click(); await wait()
  check(document.body.textContent?.includes("单位面积上的压力"), "point before generation")
  check(document.body.textContent?.includes("来源一般"),"general-only map label")
  document.querySelector<HTMLDetailsElement>("details")!.open = true
  check(document.body.textContent?.includes("Pressure is the force"), "literal source quote")
  await click("查看时间预估")
  check(!button("确认，保存").disabled, "user may continue despite duration warning")
  check(document.body.textContent?.includes("把结束日期延到"), "suggested end based on user cap")
  await click("把结束日期延到")
  await click("确认，保存")
  check(draft.phase === "ready" && methods.includes("plan.generate"), "first module ready")
  await click("开始计划")
  check(useApp.getState().route === "home", "start goes home")
  useApp.getState().set({ planId: "offline37" })
  root.render(<PlanDetail />); await wait(150)
  check(document.querySelector("h1")?.textContent === draft.name, "short detail title")
  check(document.body.textContent?.includes("学习目标：初二物理"), "full goal in details")
  await click("压强的定义")
  check(document.body.textContent?.includes("依据：OpenStax"), "persisted point citation")
  ;(window as any).plan37Result = { ok: true, checks }
}
;(window as any).plan37Show = async (theme: string) => {
  document.documentElement.dataset.theme = theme
  useApp.getState().set({ planId: "offline37" })
  root.render(<PlanDetail />)
  await wait(100)
}
run().catch(e => { (window as any).plan37Result = { ok: false, checks, error: String(e) } })
