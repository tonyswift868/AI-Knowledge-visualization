import * as React from "react"
import { createRoot } from "react-dom/client"
import { NewPlan, visiblePlanMessages, type Draft } from "../../../refactor/learning/web/src/pages/NewPlan"
import { graphLayout } from "../../../refactor/learning/web/src/planning/PlanGraph"
import { useApp } from "../../../refactor/learning/web/src/store"
import { show, calls } from "./graph61_bridge"

const root = createRoot(document.getElementById("root")!)
const button = (t: string) => [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.trim() === t)!
async function until(f: () => any, why: string) { const end = performance.now() + 6000; while (!f()) { if (performance.now() > end) throw Error(why); await new Promise(r => setTimeout(r, 20)) } }
const check = (v: any, why: string, extra?: unknown) => {
  if (!v) throw Error(`${why}${extra === undefined ? "" : `: ${JSON.stringify(extra)}`}`)
}
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
  check(chapters.open, "structure chapter references visible by default")
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
  const graphBeforeFullscreen = document.querySelector<HTMLElement>('.plan-graph')!
  const graphParentBeforeFullscreen = graphBeforeFullscreen.parentElement
  const flowBeforeFullscreen = graphBeforeFullscreen.querySelector('.react-flow')
  const viewportBeforeFullscreen = graphBeforeFullscreen.querySelector('.react-flow__viewport')
  check(!!flowBeforeFullscreen && !!viewportBeforeFullscreen, "React Flow is mounted before fullscreen")
  button("全屏查看").click()
  await until(() => document.querySelector('.pg-fullscreen'), "fullscreen")
  const fullscreenGraph = document.querySelector<HTMLElement>('.pg-fullscreen')!
  check(fullscreenGraph === graphBeforeFullscreen, "fullscreen keeps the original graph section")
  check(fullscreenGraph.parentElement === graphParentBeforeFullscreen, "fullscreen keeps the graph in the NewPlan tree")
  check(fullscreenGraph.querySelector('.react-flow') === flowBeforeFullscreen, "fullscreen keeps the same React Flow instance DOM")
  check(fullscreenGraph.querySelector('.react-flow__viewport') === viewportBeforeFullscreen, "fullscreen keeps the same viewport DOM")
  const fullscreenRect = fullscreenGraph.getBoundingClientRect()
  const boundsDiagnostic = {
    graph: [fullscreenRect.x, fullscreenRect.y, fullscreenRect.width, fullscreenRect.height,
      fullscreenRect.right, fullscreenRect.bottom],
    viewport: [innerWidth, innerHeight],
    ancestors: (() => {
      const chain: any[] = []
      for (let node: HTMLElement | null = fullscreenGraph; node; node = node.parentElement) {
        const style = getComputedStyle(node), rect = node.getBoundingClientRect()
        chain.push({ tag: node.tagName, className: node.className, rect: [rect.x, rect.y, rect.width, rect.height],
          position: style.position, transform: style.transform, filter: style.filter, perspective: style.perspective,
          contain: style.contain, willChange: style.willChange, animation: style.animationName,
          animationState: node.getAnimations().map(a => a.playState) })
      }
      return chain
    })()
  }
  check(Math.abs(fullscreenRect.left - 12) <= 2 && Math.abs(fullscreenRect.top - 12) <= 2 &&
    Math.abs(fullscreenRect.right - (innerWidth - 12)) <= 2 && Math.abs(fullscreenRect.bottom - (innerHeight - 12)) <= 2,
    "fullscreen graph fills the viewport with its 12 px inset", boundsDiagnostic)
  for (const selector of ['.chat-scroll', '.plan-main']) {
    const ancestor = fullscreenGraph.closest(selector)
    check(!!ancestor, `${selector} remains an ancestor of the inline graph`)
  }
  const edgePoints = [
    [fullscreenRect.left + 4, fullscreenRect.top + fullscreenRect.height / 2],
    [fullscreenRect.right - 4, fullscreenRect.top + fullscreenRect.height / 2],
    [fullscreenRect.left + fullscreenRect.width / 2, fullscreenRect.top + 20],
    [fullscreenRect.left + fullscreenRect.width / 2, fullscreenRect.bottom - 4],
  ]
  for (const [x, y] of edgePoints) {
    check(fullscreenGraph.contains(document.elementFromPoint(x, y)),
      `fullscreen graph is hit-testable at viewport edge (${Math.round(x)}, ${Math.round(y)})`)
  }
  document.dispatchEvent(new KeyboardEvent('keydown', {key: "Escape", bubbles: true}))
  await until(() => !document.querySelector('.pg-details'), "Escape closes details first")
  check(document.querySelector('.pg-fullscreen') === graphBeforeFullscreen, "closing details keeps the same fullscreen graph")
  check(graphBeforeFullscreen.querySelector('.react-flow') === flowBeforeFullscreen, "closing details keeps the same Flow DOM")
  check(graphBeforeFullscreen.querySelector('.react-flow__viewport') === viewportBeforeFullscreen, "closing details keeps the same viewport DOM")
  document.dispatchEvent(new KeyboardEvent('keydown', {key: "Escape", bubbles: true}))
  await until(() => !document.querySelector('.pg-fullscreen'), "Escape closes fullscreen")
  check(document.querySelector('.plan-graph') === graphBeforeFullscreen, "exit keeps the original graph section")
  check(graphBeforeFullscreen.parentElement === graphParentBeforeFullscreen, "exit restores the same NewPlan parent")
  check(graphBeforeFullscreen.querySelector('.react-flow') === flowBeforeFullscreen, "exit keeps the same React Flow instance DOM")
  check(graphBeforeFullscreen.querySelector('.react-flow__viewport') === viewportBeforeFullscreen, "exit keeps the same viewport DOM")
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
