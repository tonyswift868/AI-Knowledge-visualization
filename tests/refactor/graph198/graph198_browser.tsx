import * as React from "react"
import { createRoot } from "react-dom/client"
import { NewPlan, type Draft } from "../../../refactor/learning/web/src/pages/NewPlan"
import { PlanDetail } from "../../../refactor/learning/web/src/pages/PlanDetail"
import { useApp } from "../../../refactor/learning/web/src/store"
import { showDraft, requests } from "./graph198_bridge"

declare global { interface Window { graph198Data: any; graph198Capture?: string; graph198Result?: any } }
const errors: string[] = []
window.addEventListener("error", event => errors.push(`${event.message} at ${event.filename}:${event.lineno}`))
const root = createRoot(document.getElementById("root")!, { onUncaughtError(error) { errors.push(String(error)) }, onCaughtError(error) { errors.push(String(error)) } })
const failures: { label: string; detail?: unknown }[] = []
const wait = (ms = 20) => new Promise<void>(resolve => setTimeout(resolve, ms))
async function until(test: () => boolean, label: string, timeout = 12000) {
  const end = performance.now() + timeout
  while (!test()) { if (performance.now() > end) throw Error(`timeout: ${label}`); await wait() }
}
function check(value: unknown, label: string, detail?: unknown) {
  if (!value) failures.push({ label, detail: typeof detail === "string" || typeof detail === "number" ? detail : "inspect the case measurements" })
}

function Shell({ page }: { page: React.ReactNode }) {
  return <div className="shell" style={{ height: "100vh" }}>
    <aside className="sidebar" aria-hidden="true"><div className="brand" /></aside>
    <main className="main">
      <header className="topbar"><b>MentorFlow</b></header>
      <div className="content">{page}</div>
    </main>
  </div>
}
function setTheme(theme: "sakura" | "aurora") {
  document.documentElement.dataset.theme = theme
  useApp.setState({ settings: { ...useApp.getState().settings, theme, font_scale: 1, reduce_motion: false } as any })
}
function installInitialHide() {
  if (document.getElementById("graph198-init-hide")) return
  const style = document.createElement("style")
  style.id = "graph198-init-hide"
  style.textContent = '.chat-scroll,.plan-graph[data-testid="detail-map"]{display:none!important}'
  document.head.append(style)
}
function visibleSample(kind: string, label: string) {
  const graph = document.querySelector<HTMLElement>(kind === "detail" ? "[data-testid=detail-map]" : ".plan-graph")!
  const canvas = graph.querySelector<HTMLElement>(".pg-canvas")!
  const viewport = graph.querySelector<HTMLElement>(".react-flow__viewport")!
  const canvasRect = canvas.getBoundingClientRect()
  const nodes = [...graph.querySelectorAll<HTMLElement>(kind === "detail" ? ".react-flow__node-mapCard" : ".react-flow__node-module")]
  const measured = nodes.map(node => {
    const card = node.querySelector<HTMLElement>(kind === "detail" ? ".km-node" : ".pg-module")!
    const rect = card.getBoundingClientRect()
    const opacity = Number(getComputedStyle(card).opacity)
    const browserContained = rect.left >= -1 && rect.top >= -1 && rect.right <= innerWidth + 1 && rect.bottom <= innerHeight + 1
    const intersection = Math.max(0, Math.min(rect.right, canvasRect.right) - Math.max(rect.left, canvasRect.left)) *
      Math.max(0, Math.min(rect.bottom, canvasRect.bottom) - Math.max(rect.top, canvasRect.top))
    const fullyContained = rect.left >= canvasRect.left - 1 && rect.top >= canvasRect.top - 1 &&
      rect.right <= canvasRect.right + 1 && rect.bottom <= canvasRect.bottom + 1
    return { id: node.dataset.id, title: card.textContent?.trim().slice(0, 120), rect: [rect.x, rect.y, rect.width, rect.height], opacity, browserContained,
      animation: getComputedStyle(card).animationName, intersection, fullyContained }
  })
  const matrix = new DOMMatrix(getComputedStyle(viewport).transform)
  const wrapper = kind === "detail" ? graph : graph.closest(".plan-block")
  const ancestors: any[] = []
  for (let element: HTMLElement | null = graph; element; element = element.parentElement) {
    const style = getComputedStyle(element), rect = element.getBoundingClientRect()
    ancestors.push({ tag: element.tagName, className: typeof element.className === "string" ? element.className : "", rect: [rect.x, rect.y, rect.width, rect.height],
      opacity: style.opacity, transform: style.transform, translate: style.translate, animation: style.animationName,
      animationState: element.getAnimations().map(animation => ({ name: (animation as CSSAnimation).animationName, state: animation.playState, time: animation.currentTime })) })
  }
  return { label, graphRect: [graph.getBoundingClientRect().x, graph.getBoundingClientRect().y, graph.clientWidth, graph.clientHeight],
    canvas: [canvasRect.x, canvasRect.y, canvasRect.width, canvasRect.height], viewportZoom: matrix.a,
    moduleNodeCount: kind === "detail" ? nodes.length - 1 : nodes.length,
    domNodeCount: nodes.length,
    opaqueNodeCount: measured.filter(node => node.opacity >= .99).length,
    opaqueModuleNodeCount: measured.filter(node => node.id !== "goal" && node.opacity >= .99).length,
    containedModuleNodeCount: measured.filter(node => node.id !== "goal" && node.fullyContained).length,
    visibleModuleNodeCount: measured.filter(node => node.id !== "goal" && node.opacity >= .99 && node.fullyContained).length,
    browserVisibleModuleNodeCount: measured.filter(node => node.id !== "goal" && node.opacity >= .99 && node.fullyContained && node.browserContained).length,
    goalVisible: kind !== "detail" || measured.some(node => node.id === "goal" && node.opacity >= .99 && node.fullyContained),
    minimapNodeCount: graph.querySelectorAll(".react-flow__minimap .react-flow__minimap-node").length,
    nodes: measured, visibleCount: measured.filter(node => node.opacity >= .99 && node.intersection > node.rect[2] * node.rect[3] * .15).length,
    wrapperClass: typeof wrapper?.className === "string" ? wrapper.className : "", ancestors }
}

async function capture(name: string) {
  window.graph198Capture = name
  await until(() => (window as any).graph198CaptureAck === name, `Qt capture ${name}`)
}
async function runCase(kind: "structure" | "outline" | "detail", theme: "sakura" | "aurora") {
  setTheme(theme)
  installInitialHide()
  requests.length = 0
  const started = performance.now()
  let hiddenMount: any
  if (kind === "detail") {
    useApp.setState({ route: "plan", planId: window.graph198Data.detail.plan.id, ready: true })
    root.render(<Shell key={`${kind}-${theme}`} page={<PlanDetail />} />)
    await until(() => !!document.querySelector("[data-testid=detail-map] .react-flow__node-mapCard"), `${kind}/${theme} map mount`)
    await wait(500)
    hiddenMount = visibleSample("detail", `${kind}/${theme}/display-none-mount`)
    document.getElementById("graph198-init-hide")?.remove()
  } else {
    const draft: Draft = structuredClone(window.graph198Data.drafts[kind])
    useApp.setState({ route: "new-plan", planId: draft.plan_id, ready: true })
    showDraft(draft)
    root.render(<Shell key={`${kind}-${theme}`} page={<NewPlan />} />)
    await until(() => document.querySelectorAll(kind === "structure" ? ".plan-graph[aria-label='资料结构图'] .react-flow__node-module" : ".plan-graph[aria-label='知识地图'] .react-flow__node-module").length === 14,
      `${kind}/${theme} all 14 modules mounted`)
    const graph = document.querySelector<HTMLElement>(kind === "structure" ? ".plan-graph[aria-label='资料结构图']" : ".plan-graph[aria-label='知识地图']")!
    await wait(500)
    hiddenMount = visibleSample("newplan", `${kind}/${theme}/display-none-mount`)
    document.getElementById("graph198-init-hide")?.remove()
  }
  await wait(300)
  const graphSelector = kind === "detail" ? "[data-testid=detail-map]" : kind === "structure" ? ".plan-graph[aria-label='资料结构图']" : ".plan-graph[aria-label='知识地图']"
  const graph = document.querySelector<HTMLElement>(graphSelector)!
  const measuredKind = kind === "detail" ? "detail" : "newplan"
  const sample300 = visibleSample(measuredKind, `${kind}/${theme}/300ms`)
  await wait(300)
  const sample600 = visibleSample(measuredKind, `${kind}/${theme}/600ms`)
  await wait(300)
  const sample900 = visibleSample(measuredKind, `${kind}/${theme}/900ms`)
  await wait(100)
  const sample1000 = visibleSample(measuredKind, `${kind}/${theme}/1000ms`)
  const settleStarted = performance.now()
  let settledSample = sample1000
  while (performance.now() - settleStarted < 5000 &&
         (settledSample.opaqueModuleNodeCount !== 14 || (kind === "detail" && !settledSample.goalVisible))) {
    await wait(100)
    settledSample = visibleSample(measuredKind, `${kind}/${theme}/settled`)
  }
  const expectedNodes = kind === "detail" ? 15 : 14
  const nodes = [...graph.querySelectorAll<HTMLElement>(kind === "detail" ? ".react-flow__node-mapCard" : ".react-flow__node-module")]
  const titleNeedles = window.graph198Data.moduleTitles as string[]
  const renderedTitles = nodes.map(node => node.textContent ?? "")
  const missingTitles = titleNeedles.filter(title => !renderedTitles.some(text => text.includes(title)))
  const positive = settledSample.nodes.filter((node: any) => (node.rect?.[2] ?? 0) > 0 && (node.rect?.[3] ?? 0) > 0)
  const checkResults = {
    graphExists: !!graph,
    hiddenMountCanvasZero: hiddenMount.canvas[2] === 0 && hiddenMount.canvas[3] === 0,
    hiddenMountHasAllModuleNodes: hiddenMount.moduleNodeCount === 14,
    hiddenMount,
    expectedModuleCount: settledSample.moduleNodeCount === 14,
    expectedTotalNodeCount: nodes.length === expectedNodes,
    allModuleTitles: missingTitles.length === 0,
    canvasHasSizeAfterReveal: settledSample.canvas[2] > 0 && settledSample.canvas[3] > 0,
    cardsHaveSize: positive.length === expectedNodes,
    cardsOpaqueAfterAnimation: settledSample.opaqueModuleNodeCount === 14 && (kind !== "detail" || settledSample.opaqueNodeCount === expectedNodes),
    allModuleCardsFitInViewportAfterEntrance: settledSample.containedModuleNodeCount === 14,
    detailGoalVisible: settledSample.goalVisible,
    usableZoom: Number.isFinite(settledSample.viewportZoom) && settledSample.viewportZoom > 0 && settledSample.viewportZoom <= 1.01,
    stableWrapperTree: kind === "detail" || (graph.closest(".plan-block") !== null && graph.closest(".plan-main") !== null && graph.closest(".chat-scroll") !== null && graph.closest("[data-turn-key]") !== null),
    elapsedToAllCardsMs: Math.round(performance.now() - started),
    settledAfter1000Ms: Math.round(performance.now() - settleStarted),
    missingTitles, sample300, sample600, sample900, sample1000, settledSample,
  }
  check(Object.entries(checkResults).filter(([key]) => !["elapsedToAllCardsMs", "settledAfter1000Ms", "missingTitles", "hiddenMount", "sample300", "sample600", "sample900", "sample1000", "settledSample"].includes(key)).every(([, value]) => value === true || typeof value === "number"),
    `${kind}/${theme} module cards visible and viewport reasonable`, checkResults)
  // Reveal the tested graph in the outer page only after capturing its fit,
  // node bounds, and animation state; this does not change React Flow's zoom.
  graph.scrollIntoView({ block: "center" })
  await capture(`${kind}-${theme}`)
  return checkResults
}

async function run() {
  const outcomes: any[] = []
  try {
    const cases = window.graph198Data.cases as string[] ?? ["structure", "outline", "detail"]
    for (const kind of cases) for (const theme of ["sakura", "aurora"] as const) outcomes.push({ kind, theme, ...(await runCase(kind as any, theme)) })
    window.graph198Result = { ok: failures.length === 0, outcomes, failures, requests: requests.slice(), errors,
      modules: window.graph198Data.moduleTitles, structureDraftSha256: window.graph198Data.structureDraftSha256,
      structureDraftUnmodified: window.graph198Data.structureDraftUnmodified }
  } catch (error) {
    window.graph198Result = { ok: false, error: String((error as Error).stack ?? error), outcomes, failures, requests: requests.slice(), errors,
      body: document.body.innerText.slice(0, 3000), modules: window.graph198Data.moduleTitles,
      structureDraftSha256: window.graph198Data.structureDraftSha256, structureDraftUnmodified: window.graph198Data.structureDraftUnmodified }
  }
}
run()
