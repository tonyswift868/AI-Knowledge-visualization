import * as React from "react"
import { createRoot } from "react-dom/client"
import { NewPlan, visiblePlanMessages, type Draft } from "../../../refactor/learning/web/src/pages/NewPlan"
import { graphLayout } from "../../../refactor/learning/web/src/planning/PlanGraph"
import { useApp } from "../../../refactor/learning/web/src/store"
import { show, calls } from "./graph61_bridge"

const root = createRoot(document.getElementById("root")!)
const button = (t: string) => [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.trim() === t)!
async function until(f: () => any, why: string) { const end = performance.now() + 6000; while (!f()) { if (performance.now() > end) throw Error(why); await new Promise(r => setTimeout(r, 20)) } }
const check = (v: any, why: string) => { if (!v) throw Error(why) }
const data = (window as any).graph61Data
function render(name: string, theme = "sakura") { document.documentElement.dataset.theme = theme; show(structuredClone(data[name])); root.render(<NewPlan />) }
;(window as any).graph61Show = render
useApp.getState().set({planId: data.structure.plan_id})

async function run() {
  render("structure")
  await until(() => document.querySelectorAll('.react-flow__node-module').length === data.structure.material_structure.modules.length, "structure nodes")
  check(button("确认结构，开始填知识点"), "confirmation explains next step")
  check(document.body.textContent?.includes("生成完会给你看完整知识地图再确认"), "confirmation helper")
  check(!document.body.textContent?.includes("旧截断错误"), "resolved error hidden")
  check(!document.body.textContent?.includes("原来的超长章节正文"), "preview prose hidden")
  check(!document.querySelector('.pg-details'), "details collapsed initially")
  document.querySelector<HTMLElement>('.react-flow__node-module')!.click()
  await until(() => document.querySelector('.pg-details'), "open drawer")
  const chapters = document.querySelector<HTMLDetailsElement>('.pg-chapters')!
  check(!chapters.open, "all chapter references collapsed by default")
  chapters.open = true
  check(document.querySelectorAll('.pg-chapters li').length === data.structure.material_structure.modules[0].chapters.length, "all chapters retained")
  button("确认结构，开始填知识点").click()
  await until(() => calls.some(c => c.method === "plan.missing"), "action request")
  check(calls.find(c => c.method === "plan.missing")?.params.action === "continue", "action unchanged")
  render("outline")
  await until(() => document.querySelector('[aria-label="知识地图"]'), "outline render")
  const modules = data.outline.outline.stages.flatMap((s: any) => s.modules)
  check(document.querySelectorAll('.react-flow__node-module').length === modules.length, "only modules drawn, not 163 points")
  document.querySelector<HTMLElement>('.react-flow__node-module')!.click()
  await until(() => document.querySelectorAll('.pg-points > li').length === modules[0].points.length, "all knowledge points retained")
  check(document.body.textContent?.includes(modules[0].points[0].citations[0].quote), "citation quote retained")
  check(document.body.textContent?.includes("预计总时长"), "duration preserved")
  button("全屏查看").click()
  await until(() => document.querySelector('.pg-fullscreen'), "fullscreen")
  check(document.querySelector('.pg-fullscreen')?.parentElement === document.body, "portal fills window outside chat clipping")
  document.dispatchEvent(new KeyboardEvent('keydown', {key: "Escape", bubbles: true}))
  await until(() => !document.querySelector('.pg-fullscreen'), "Escape closes fullscreen")
  const messages: any[] = [{id:'s',role:'ai',kind:'sources',text:''},{id:'e',role:'ai',kind:'error',text:'旧截断错误'}, {id:'ok',role:'ai',kind:'material_preview',text:''}]
  check(visiblePlanMessages(messages).length === 2 && messages.length === 3, "filter does not delete history")
  check(visiblePlanMessages(messages.slice(0,2)).length === 2, "unresolved error kept")
  check(visiblePlanMessages([{...messages[0],kind:'outline'},messages[1],{...messages[2],kind:'sources'}]).length === 3, "unrelated success keeps error")
  check(visiblePlanMessages([messages[0], {...messages[1],text:'https://bad.example：未能导入'},messages[2]]).length === 3, "different import cannot hide failure")
  const stress = graphLayout([{title:'大量模块',modules:Array.from({length:60},(_,i)=>({...modules[0],key:`stress-${i}`,title:`模块${i}`,points:modules[0].points}))}], "outline")
  const items = stress.nodes.filter(n => n.type === "module")
  check(items.length === 60 && stress.nodes.length === 61, "thousands of points don't increase node count")
  for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
    const a = items[i].position, b = items[j].position
    check(Math.abs(a.x-b.x) >= 294 || Math.abs(a.y-b.y) >= 174, "Dagre cards do not overlap")
  }
  const large = structuredClone(data.outline)
  large.outline.stages = [{title:"大量模块", modules:items.map(n => n.data.module)}]
  const started = performance.now()
  show(large)
  await until(() => document.querySelectorAll('.react-flow__node-module').length === 60, "60 modules render")
  const renderMs = Math.round(performance.now()-started)
  const missing = structuredClone(data.structure)
  delete missing.material_structure
  missing.messages = [{id:'absent',role:'ai',kind:'material_preview',text:'旧结构',version:undefined}]
  show(missing)
  await until(() => document.body.textContent?.includes("没有可显示的结构"), "absent payload doesn't crash")
  check(!document.querySelector('.plan-graph'), "absent data doesn't invent a graph")
  check(!button("确认结构，开始填知识点"), "absent structure cannot be confirmed")
  render("structure", "aurora")
  await until(() => document.querySelector('[aria-label="资料结构图"]'), "dark structure")
  check(document.documentElement.scrollWidth <= innerWidth, "no horizontal overflow")
  ;(window as any).plan37Result = {ok:true, render_60_modules_ms:renderMs, checks: ["structure version/actions", "all chapter and point details", "fullscreen/Escape", "error history filtering", "60-module layout/render", "missing historical structure", "dark theme no overflow"]}
}
run().catch(e => { (window as any).plan37Result = {ok:false, error:String(e), body:document.body.textContent} })
