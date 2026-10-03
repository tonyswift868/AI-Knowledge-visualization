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
  check(document.body.textContent?.includes("可信依据"), "source credibility")
  document.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click(); await wait()
  await click("整理知识地图")
  check(document.body.textContent?.includes("单位面积上的压力"), "point before generation")
  document.querySelector<HTMLDetailsElement>("details")!.open = true
  check(document.body.textContent?.includes("Pressure is the force"), "literal source quote")
  await click("查看时间预估")
  check(button("确认，保存").disabled, "300-minute gate")
  check(document.body.textContent?.includes("共 10 天"), "suggested days")
  await click("延长到")
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
