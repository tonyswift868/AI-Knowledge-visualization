import * as React from "react"
import { createRoot } from "react-dom/client"
import { PlanGraph, type GraphStage } from "../../../refactor/learning/web/src/planning/PlanGraph"
import { KnowledgeMap } from "../../../refactor/learning/web/src/planning/KnowledgeMap"
import { useApp, type KnowledgeDetail } from "../../../refactor/learning/web/src/store"

const w = window as any
const root = createRoot(document.getElementById("root")!)
const wait = (ms = 25) => new Promise(r => setTimeout(r, ms))
async function until(fn: () => boolean, why: string, ms = 10000) {
  const end = performance.now() + ms
  while (!fn()) { if (performance.now() > end) throw Error(`timeout: ${why}`); await wait() }
}
function check(ok: unknown, why: string) { if (!ok) throw Error(why); w.graph171Checks.push(why) }
const stages: GraphStage[] = [{ title: "计划阶段", modules: Array.from({ length: 7 }, (_, i) => ({
  key: `m${i + 1}`, title: `模块 ${i + 1}`, summary: "掌握本模块的学习目标和资料内容。", chapters: [`第 ${i + 1} 章`], refs: [`c${i + 1}`],
  prerequisites: i ? [`m${i}`] : [], minutes: [24, 40] as [number, number], points: []
})) }]
const detail: KnowledgeDetail = {
  plan: { id: "graph171", goal: "掌握课程内容" }, point_links_status: "none",
  stages: [{ id: "s1", title: "计划阶段", modules: Array.from({ length: 7 }, (_, i) => ({
    id: `m${i + 1}`, title: `模块 ${i + 1}`, summary: "掌握本模块的学习目标。", question_count: 1, passed_questions: 0,
    learning_minutes: 24, check_minutes: 4, points: Array.from({ length: 8 }, (_, p) => ({
      id: `m${i + 1}-p${p + 1}`, title: `知识点 ${p + 1}`, summary: "理解资料中的核心概念。", minutes: 8,
      citations: [{ chunk_id: "c1", title: "测试资料", url: null, locator: "第 1 段", quote: "这是可核对的资料原文。" }]
    }))
  })) }]
}
function Harness() {
  const [graphStages, setGraphStages] = React.useState(stages)
  const [mapDetail, setMapDetail] = React.useState(detail)
  const [showMap, setShowMap] = React.useState(false)
  w.graph171SameContent = () => setGraphStages(prev => JSON.parse(JSON.stringify(prev)))
  w.graph171ChangeStructure = () => setGraphStages(prev => {
    const next = JSON.parse(JSON.stringify(prev)) as GraphStage[]
    next[0].modules.push({ ...next[0].modules[0], key: "m8", title: "模块 8", prerequisites: ["m7"] })
    return next
  })
  w.graph171ChangeText = () => setGraphStages(prev => {
    const next = JSON.parse(JSON.stringify(prev)) as GraphStage[]
    next[0].modules[0].title = "刷新后的模块名称"
    next[0].modules[0].summary = "父组件传入了新的标题和摘要内容。"
    return next
  })
  w.graph171ChangeEdgesOnly = () => setGraphStages(prev => {
    const next = JSON.parse(JSON.stringify(prev)) as GraphStage[]
    const target = next[0].modules.find(m => m.key === "m7")!
    target.prerequisites = target.prerequisites?.includes("m1") ? ["m2"] : ["m1"]
    return next
  })
  w.graph171ShowMap = () => setShowMap(true)
  w.graph171MapSameContent = () => setMapDetail(prev => JSON.parse(JSON.stringify(prev)))
  w.graph171Theme = (theme: string) => { document.documentElement.dataset.theme = theme }
  return showMap
    ? <KnowledgeMap detail={mapDetail} onRetry={() => {}} />
    : <PlanGraph stages={graphStages} mode="structure" />
}
useApp.setState({ settings: { ...useApp.getState().settings, reduce_motion: false } as any })
root.render(<Harness />)

const viewport = () => document.querySelector<HTMLElement>(".react-flow__viewport")!
function matrix() { const m = new DOMMatrix(getComputedStyle(viewport()).transform); return { x: m.e, y: m.f, zoom: m.a } }
function nodeRect(selector: string) { const r = document.querySelector<HTMLElement>(selector)!.getBoundingClientRect(); return { x: r.x, y: r.y } }
function close(a: number, b: number, tol = 1.5) { return Math.abs(a - b) <= tol }
function sameZoom(a: number, b: number) { return close(a, b, .001) }
function wheelZoom() {
  const pane = document.querySelector<HTMLElement>(".react-flow__pane")!
  const r = pane.getBoundingClientRect()
  pane.dispatchEvent(new WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: -650, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 }))
}
async function run() {
  await until(() => document.querySelectorAll(".plan-graph[aria-label='资料结构图'] .react-flow__node-module").length === 7, "initial PlanGraph modules")
  await wait(500)
  const initial = matrix()
  wheelZoom()
  await until(() => Math.abs(matrix().zoom - initial.zoom) > .06, "wheel zoom changes React Flow viewport")
  await wait(350)
  const zoomed = matrix()
  check(zoomed.zoom > initial.zoom, "wheel zooms in")
  w.graph171CaptureZoom = true
  await wait(250)
  w.graph171At = "plan-same-content"
  w.graph171SameContent()
  await wait(550)
  const afterSame = matrix()
  w.graph171Evidence = { initial, zoomed, afterSame }
  check(sameZoom(afterSame.zoom, zoomed.zoom), "same-content parent rerender preserves user zoom")
  check(close(afterSame.x, zoomed.x) && close(afterSame.y, zoomed.y), "same-content parent rerender preserves viewport position")
  w.graph171Theme("aurora")
  w.graph171CaptureDark = true
  await wait(250)

  // Bring nodes back into the viewport before testing their native drag; with
  // onlyRenderVisibleElements, the user's high zoom can legitimately virtualize
  // every module node after the viewport regression assertion has passed.
  document.querySelector<HTMLButtonElement>(".plan-graph .react-flow__controls-fitview")!.click()
  await wait(350)
  const visibleNode = [...document.querySelectorAll<HTMLElement>(".plan-graph .react-flow__node-module")]
    .find(n => { const r=n.getBoundingClientRect(); return r.width>0 && r.height>0 && r.right>0 && r.bottom>0 && r.left<innerWidth && r.top<innerHeight })!
  check(!!visibleNode, "fit overview renders visible module nodes for drag check")
  const dragId = visibleNode.dataset.id!
  const dragSelector = `.plan-graph .react-flow__node-module[data-id='${dragId}']`
  w.graph171DragSelector = dragSelector
  const beforeCard = nodeRect(dragSelector)
  w.graph171WaitForNodeDrag = true
  await until(() => w.graph171NodeDragDone === true, "React Flow module drag", 15000)
  w.graph171WaitForNodeDrag = false
  await wait(250)
  const movedCard = nodeRect(dragSelector)
  check(Math.abs(movedCard.x - beforeCard.x) > 15 || Math.abs(movedCard.y - beforeCard.y) > 15, `module node drag changes its coordinates: ${JSON.stringify({beforeCard,movedCard})}`)
  w.graph171SameContent(); await wait(450)
  const afterNodeRefresh = nodeRect(dragSelector)
  check(close(afterNodeRefresh.x, movedCard.x, 3) && close(afterNodeRefresh.y, movedCard.y, 3), "same-content rerender preserves dragged module coordinates")

  const beforeText = matrix()
  w.graph171ChangeText()
  await until(() => document.body.textContent?.includes("刷新后的模块名称"), "new title and summary rendered")
  await wait(300)
  const afterText = matrix()
  check(sameZoom(afterText.zoom, beforeText.zoom) && close(afterText.x, beforeText.x) && close(afterText.y, beforeText.y), "title and summary refresh does not refit viewport")
  check(document.body.textContent?.includes("父组件传入了新的标题和摘要内容。"), "content refresh updates module summary")

  // Explicitly zoom in before an edge-only change to prove topology refits even when node IDs stay fixed.
  const beforeEdgeZoom = matrix()
  document.querySelector<HTMLButtonElement>(".plan-graph .react-flow__controls-zoomin")!.click()
  await until(() => matrix().zoom > beforeEdgeZoom.zoom + .05, "zoom in before edge-only topology change")
  const beforeEdgeRefit = matrix()
  w.graph171ChangeEdgesOnly()
  await wait(450)
  const afterEdgeRefit = matrix()
  check(!sameZoom(afterEdgeRefit.zoom, beforeEdgeRefit.zoom), "edge-only id change refits after user zoom")

  const beforeStructure = matrix()
  document.querySelector<HTMLButtonElement>(".plan-graph .react-flow__controls-zoomin")!.click()
  await until(() => matrix().zoom > beforeStructure.zoom + .05, "zoom again before structure refit")
  const beforeStructureZoom = matrix()
  w.graph171ChangeStructure()
  await until(() => document.querySelectorAll(".plan-graph[aria-label='资料结构图'] .react-flow__node-module").length === 8, "new module structure rendered")
  await wait(450)
  const changedStructure = matrix()
  check(!close(changedStructure.zoom, beforeStructureZoom.zoom, .025), "module id structure change refits graph")

  const beforeFullscreen = matrix()
  ;[...document.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent?.includes("全屏查看"))!.click()
  await until(() => !!document.querySelector(".pg-fullscreen"), "fullscreen opens")
  await wait(450)
  const fullscreen = matrix()
  check(!close(fullscreen.zoom, beforeFullscreen.zoom, .025), "fullscreen change adapts viewport")
  ;[...document.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent?.includes("退出全屏"))!.click()
  await until(() => !document.querySelector(".pg-fullscreen"), "fullscreen closes")
  await wait(300)
  const afterFullscreenExit = matrix()
  check(close(afterFullscreenExit.zoom, beforeFullscreen.zoom, .05) && close(afterFullscreenExit.x, beforeFullscreen.x, 12) && close(afterFullscreenExit.y, beforeFullscreen.y, 12), "exiting fullscreen restores normal canvas overview")
  const beforeFit = matrix()
  document.querySelector<HTMLButtonElement>(".plan-graph .react-flow__controls-zoomin")!.click()
  await until(() => matrix().zoom > beforeFit.zoom + .05, "React Flow zoom control enlarges view")
  const zoomControl = matrix()
  const fit = document.querySelector<HTMLButtonElement>(".plan-graph .react-flow__controls-fitview")
  check(!!fit, "React Flow fit control is present")
  fit!.click()
  await until(() => matrix().zoom < zoomControl.zoom - .025, "fit control restores graph overview")
  check(true, "fit control adapts the graph overview")
  const locator = document.querySelector<HTMLSelectElement>(".plan-graph select[aria-label='定位模块']")!
  locator.value = "m8"; locator.dispatchEvent(new Event("change", { bubbles: true }))
  await until(() => !!document.querySelector(".plan-graph .pg-details h3")?.textContent?.includes("模块 8"), "module locator opens target")
  // Opening the drawer and fitting the node animate independently; assert the
  // settled result rather than sampling the first frame of the 250ms fit.
  await until(() => {
    const target = document.querySelector<HTMLElement>(".plan-graph .react-flow__node-module[data-id='m8']")?.getBoundingClientRect()
    const bounds = document.querySelector<HTMLElement>(".plan-graph .pg-canvas")!.getBoundingClientRect()
    return !!target && target.width > 0 && target.left >= bounds.left && target.right <= bounds.right && target.top >= bounds.top && target.bottom <= bounds.bottom
  }, "module locator finishes bringing target into canvas")
  const located = document.querySelector<HTMLElement>(".plan-graph .react-flow__node-module[data-id='m8']")!.getBoundingClientRect()
  const canvas = document.querySelector<HTMLElement>(".plan-graph .pg-canvas")!.getBoundingClientRect()
  check(located.width > 0 && located.height > 0 && located.left >= canvas.left && located.right <= canvas.right && located.top >= canvas.top && located.bottom <= canvas.bottom, "定位模块 places target inside the graph canvas")

  w.graph171At = "knowledge-map"
  w.graph171ShowMap()
  await until(() => document.querySelectorAll(".knowledge-map .react-flow__node").length >= 8, "KnowledgeMap visible")
  await wait(450)
  const mapTransform = matrix()
  const mapBefore = nodeRect(".knowledge-map .react-flow__node:not([data-id='goal'])")
  w.graph171WaitForMapDrag = true
  await until(() => w.graph171MapDragDone === true, "React Flow KnowledgeMap drag", 15000)
  w.graph171WaitForMapDrag = false
  await wait(250)
  const mapMoved = nodeRect(".knowledge-map .react-flow__node:not([data-id='goal'])")
  check(Math.abs(mapMoved.x - mapBefore.x) > 15 || Math.abs(mapMoved.y - mapBefore.y) > 15, "KnowledgeMap module drag changes its coordinates")
  wheelZoom()
  await until(() => Math.abs(matrix().zoom - mapTransform.zoom) > .06, "KnowledgeMap wheel zoom changes viewport")
  await wait(300)
  const mapZoomed = matrix()
  const mapMovedAfterZoom = nodeRect(".knowledge-map .react-flow__node:not([data-id='goal'])")
  w.graph171MapSameContent(); await wait(400)
  const mapAfter = matrix(), mapAfterCard = nodeRect(".knowledge-map .react-flow__node:not([data-id='goal'])")
  check(sameZoom(mapAfter.zoom, mapZoomed.zoom) && close(mapAfter.x, mapZoomed.x, 2) && close(mapAfter.y, mapZoomed.y, 2), "KnowledgeMap same-content rerender preserves user zoom and viewport")
  check(close(mapAfterCard.x, mapMovedAfterZoom.x, 3) && close(mapAfterCard.y, mapMovedAfterZoom.y, 3), "KnowledgeMap same-content rerender preserves dragged module coordinates")
  w.graph171Result = { ok: true, checks: w.graph171Checks, planGraph: w.graph171Evidence, knowledgeMap: { mapTransform, mapAfter, mapBefore, mapMoved, mapMovedAfterZoom, mapAfterCard } }
}
w.graph171Checks = []
w.graph171At = "loading"
run().catch((error: unknown) => { w.graph171Result = { ok: false, error: String(error), phase: w.graph171At, checks: w.graph171Checks, evidence: w.graph171Evidence } })
