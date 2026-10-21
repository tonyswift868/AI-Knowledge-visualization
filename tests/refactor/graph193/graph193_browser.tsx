import * as React from "react"
import { createRoot } from "react-dom/client"
import { PlanGraph, type GraphStage } from "../../../refactor/learning/web/src/planning/PlanGraph"
import { useApp } from "../../../refactor/learning/web/src/store"

const w = window as any
const root = createRoot(document.getElementById("root")!)
const probeStyle = document.createElement("style")
probeStyle.textContent = ".shell:has(.pg-fullscreen) #graph193-underlay{visibility:visible}"
document.head.append(probeStyle)
const stages: GraphStage[] = [{ title: "计划阶段", modules: Array.from({ length: 8 }, (_, i) => ({
  key: `m${i + 1}`, title: `模块 ${i + 1}`, summary: "理解本模块中的核心知识和相互关系。",
  chapters: [`第 ${i + 1} 章`], refs: [`c${i + 1}`], prerequisites: i ? [`m${i}`] : [],
  minutes: [24, 40] as [number, number], points: []
})) }]
useApp.setState({ settings: { ...useApp.getState().settings, reduce_motion: false } as any })
function Harness() {
  return <div className="shell" style={{ height: "100vh", display: "flex", position: "relative" }}>
    <main className="content" style={{ height: "100%", padding: 0, overflow: "auto" }}>
      <article className="page plan-page" style={{ width: "100%", maxWidth: "none", height: "100%", transform: "translateZ(0)" }}>
        <section className="card plan-main" style={{ height: "100%", overflow: "hidden", position: "relative" }}>
          <div className="chat-scroll" style={{ height: "100%", overflow: "hidden", padding: 0 }}>
            <div data-turn-key="graph193-material-preview" data-turn-role="assistant">
              <div className="plan-block fade-in">
                <h3 className="material-graph-heading">按你的资料整理出 8 个模块</h3>
                <PlanGraph stages={stages} mode="structure" />
              </div>
            </div>
          </div>
        </section>
      </article>
    </main>
    <div id="graph193-underlay" style={{ position: "fixed", inset: 0, zIndex: 50, visibility: "hidden", background: "rgba(255,0,0,.02)" }} />
  </div>
}
root.render(<Harness />)

const sleep = (ms = 20) => new Promise<void>(resolve => setTimeout(resolve, ms))
async function until(test: () => boolean, label: string, timeout = 10000) {
  const end = performance.now() + timeout
  while (!test()) { if (performance.now() > end) throw Error(`timeout: ${label}`); await sleep() }
}
function check(ok: unknown, label: string, extra?: unknown) {
  if (!ok) {
    const failure = { label, extra }
    w.graph193Failures.push(failure)
    throw Error(`${label}${extra === undefined ? "" : `: ${JSON.stringify(extra)}`}`)
  }
  w.graph193Checks.push(label)
}
const flow = () => document.querySelector<HTMLElement>(".plan-graph .react-flow")!
const viewport = () => document.querySelector<HTMLElement>(".plan-graph .react-flow__viewport")!
const cards = () => [...document.querySelectorAll<HTMLElement>(".plan-graph .react-flow__node-module")]
function transform() {
  const m = new DOMMatrix(getComputedStyle(viewport()).transform)
  return { x: m.e, y: m.f, zoom: m.a }
}
function allNodesVisible() {
  const canvas = document.querySelector<HTMLElement>(".pg-canvas")!.getBoundingClientRect()
  const modules = cards()
  return modules.length === 8 && modules.every(card => {
    const r = card.getBoundingClientRect(), opacity = Number(getComputedStyle(card.querySelector(".pg-module")!).opacity)
    return r.width > 0 && r.height > 0 && r.left >= canvas.left - 1 && r.right <= canvas.right + 1 &&
      r.top >= canvas.top - 1 && r.bottom <= canvas.bottom + 1 && opacity >= .99
  })
}
function animationTimes() {
  return cards().map(card => {
    const target = card.querySelector<HTMLElement>(".pg-module")!
    return { id: card.dataset.id, animation: getComputedStyle(target).animationName,
      start: target.getAnimations().map(a => a.startTime), current: target.getAnimations().map(a => a.currentTime),
      opacity: getComputedStyle(target).opacity }
  })
}
function sampleCards() {
  const canvas = document.querySelector<HTMLElement>(".pg-canvas")!.getBoundingClientRect()
  return cards().map(card => {
    const target = card.querySelector<HTMLElement>(".pg-module")!
    const r = card.getBoundingClientRect(), style = getComputedStyle(target)
    return { id: card.dataset.id, rect: { x: r.x, y: r.y, left: r.left, top: r.top, right: r.right,
      bottom: r.bottom, width: r.width, height: r.height }, opacity: style.opacity, animationName: style.animationName,
      animations: target.getAnimations().map(a => ({ startTime: a.startTime, currentTime: a.currentTime })),
      canvas: { left: canvas.left, top: canvas.top, right: canvas.right, bottom: canvas.bottom } }
  })
}
function pageAppearance() {
  const page = document.querySelector<HTMLElement>(".page.plan-page")!
  return { opacity: getComputedStyle(page).opacity,
    start: page.getAnimations().map(a => a.startTime), transform: getComputedStyle(page).transform }
}
function planMessageIdentity() {
  const turn = document.querySelector<HTMLElement>('.chat-scroll [data-turn-key="graph193-material-preview"]')!
  const block = turn.querySelector<HTMLElement>('.plan-block.fade-in')!
  return { turn, block, animations: block.getAnimations() }
}
function stablePlanMessage(before: ReturnType<typeof planMessageIdentity>, label: string) {
  const current = planMessageIdentity()
  check(current.turn === before.turn && current.block === before.block, `${label}: same NewPlan message wrappers`)
  check(current.animations.length === before.animations.length &&
    current.animations.every((animation, i) => animation === before.animations[i]),
    `${label}: same plan-block pageIn animation objects`, {
      before: before.animations.map(a => ({ name: (a as CSSAnimation).animationName, start: a.startTime, state: a.playState })),
      after: current.animations.map(a => ({ name: (a as CSSAnimation).animationName, start: a.startTime, state: a.playState }))
    })
}
function flowIdentity() {
  const element = flow(), vp = viewport(), nodes = cards()
  return { element, vp, nodes, ids: nodes.map(n => n.dataset.id) }
}
function stableFlow(before: ReturnType<typeof flowIdentity>, label: string) {
  check(flow() === before.element, `${label}: same ReactFlow DOM root`)
  check(viewport() === before.vp, `${label}: same canvas viewport DOM node`)
  const current = cards()
  const replaced = current.filter((n, i) => n !== before.nodes[i]).map(n => n.dataset.id)
  const sameIds = current.length === before.nodes.length && current.every((n, i) => n.dataset.id === before.ids[i])
  check(sameIds, `${label}: same module node IDs`, { currentIds: current.map(n => n.dataset.id), beforeIds: before.ids })
  if (replaced.length) w.graph193NodeReplacements.push({ label, replaced })
}
function button(text: string) {
  return [...document.querySelectorAll<HTMLButtonElement>(".pg-expand")].find(b => b.textContent?.includes(text))!
}
function capture(name: string) {
  w.graph193Capture = name
  return until(() => w.graph193CaptureAck === name, `screenshot saved ${name}`)
}
async function setWindowSize(width: number, height: number) {
  w.graph193ResizeRequest = `${width}x${height}`
  await until(() => w.graph193ResizeAck === `${width}x${height}`, `Qt window resized ${width}x${height}`)
  await sleep(250)
}
function checkStackAndParent() {
  const section = document.querySelector<HTMLElement>(".pg-fullscreen")!
  const r = section.getBoundingClientRect()
  const hit = document.elementFromPoint(innerWidth / 2, innerHeight / 2)
  check(section.closest(".plan-main") !== null && section.closest(".chat-scroll") !== null &&
    section.closest(".plan-block.fade-in") !== null && section.closest("[data-turn-key]") !== null,
    "fullscreen stays in the actual NewPlan message/page tree")
  check(Math.abs(r.left - 12) <= 2 && Math.abs(r.top - 12) <= 2 &&
    Math.abs(r.right - (innerWidth - 12)) <= 2 && Math.abs(r.bottom - (innerHeight - 12)) <= 2,
    "fullscreen graph fills the actual NewPlan viewport with its 12 px inset",
    { r: [r.x, r.y, r.width, r.height], viewport: [innerWidth, innerHeight] })
  check(!!hit && (hit === section || section.contains(hit)), "fullscreen is above other content at center", hit?.className)
}
function near(a: number, b: number, tolerance: number) { return Math.abs(a - b) <= tolerance }

async function cycle(theme: "sakura" | "aurora", width: number, height: number, rounds: number) {
  document.documentElement.dataset.theme = theme
  await setWindowSize(width, height)
  await until(() => cards().length === 8, `${theme} ${width}px nodes present`)
  await sleep(750) // settle initial CSS entrance animations before measuring replay
  check(allNodesVisible(), `${theme} normal graph has all nodes visible before fullscreen`)
  const pageStart = pageAppearance()
  check(Number(pageStart.opacity) >= .99, `${theme} page entrance animation settled before measuring`)
  await capture(`${theme}-${width}-normal`)
  for (let round = 1; round <= rounds; round++) {
    const identity = flowIdentity()
    const planMessage = planMessageIdentity()
    const normalBefore = transform()
    const animations = animationTimes()
    const pageBefore = pageAppearance()
    button("全屏查看").click()
    await until(() => !!document.querySelector(".pg-fullscreen"), `${theme} fullscreen opens #${round}`)
    await sleep(300)
    w.graph193Samples.push({ label: `${theme}-${width}-enter-${round}-300ms`, matrix: transform(), nodes: sampleCards() })
    stableFlow(identity, `${theme} enter #${round}`)
    stablePlanMessage(planMessage, `${theme} enter #${round}`)
    const pageAfterEnter = pageAppearance()
    check(Number(pageAfterEnter.opacity) >= .99 && pageBefore.start.length === pageAfterEnter.start.length &&
      pageBefore.start.every((t, i) => t === pageAfterEnter.start[i]), `${theme} page entrance does not replay on enter #${round}`,
    { before: pageBefore, after: pageAfterEnter })
    check(allNodesVisible(), `${theme} all nodes visible at 300ms after enter #${round}`)
    checkStackAndParent()
    const fullMatrix = transform()
    check(Number.isFinite(fullMatrix.zoom) && fullMatrix.zoom > 0, `${theme} fullscreen viewport transform valid`)
    const currentAnimations = animationTimes()
    check(currentAnimations.length === animations.length && currentAnimations.every((a, i) =>
      a.animation === animations[i].animation && a.start.length === animations[i].start.length &&
      a.start.every((t, j) => t === animations[i].start[j])), `${theme} entering does not replay card animations #${round}`,
    { before: animations, after: currentAnimations })
    if (round === 1) await capture(`${theme}-${width}-fullscreen`)

    // Exercise an actual Qt window resize while the fullscreen graph is live.
    // ResizeObserver should adapt the existing ReactFlow canvas without replacing
    // the provider, viewport element or cards.
    const resizedWhileFull = round === 2
    if (resizedWhileFull) {
      const newWidth = width === 390 ? 470 : 520
      const newHeight = height - 70
      await setWindowSize(newWidth, newHeight)
      stableFlow(identity, `${theme} resize while fullscreen #${round}`)
      w.graph193Samples.push({ label: `${theme}-${width}-fullscreen-resize-${round}`, matrix: transform(), nodes: sampleCards() })
      check(allNodesVisible(), `${theme} all nodes fit after fullscreen resize`, { newWidth, newHeight, matrix: transform() })
      checkStackAndParent()
      await capture(`${theme}-${width}-resized-fullscreen`)
    }

    // Test Escape and focus restoration on the last round for each viewport.
    if (round === rounds) {
      button("退出全屏").focus()
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }))
    } else button("退出全屏").click()
    await until(() => !document.querySelector(".pg-fullscreen"), `${theme} fullscreen closes #${round}`)
    await sleep(300)
    w.graph193Samples.push({ label: `${theme}-${width}-exit-${round}-300ms`, matrix: transform(), nodes: sampleCards() })
    stableFlow(identity, `${theme} exit #${round}`)
    stablePlanMessage(planMessage, `${theme} exit #${round}`)
    const pageAfterExit = pageAppearance()
    check(Number(pageAfterExit.opacity) >= .99 && pageBefore.start.length === pageAfterExit.start.length &&
      pageBefore.start.every((t, i) => t === pageAfterExit.start[i]), `${theme} page entrance does not replay on exit #${round}`,
    { before: pageBefore, after: pageAfterExit })
    check(allNodesVisible(), `${theme} all nodes visible at 300ms after exit #${round}`)
    const restored = transform()
    check(resizedWhileFull || (near(restored.zoom, normalBefore.zoom, .05) && near(restored.x, normalBefore.x, 12) && near(restored.y, normalBefore.y, 12)),
      `${theme} viewport restored within existing tolerance #${round}`,
      { expected: normalBefore, actual: restored })
    const afterAnimations = animationTimes()
    check(afterAnimations.every((a, i) => a.animation === animations[i].animation &&
      a.start.length === animations[i].start.length && a.start.every((t, j) => t === animations[i].start[j])),
      `${theme} exit does not replay card animations #${round}`)
    if (round === rounds) check(document.activeElement === button("全屏查看"), `${theme} Escape returns focus to fullscreen toggle`)
    if (round === 1) await capture(`${theme}-${width}-after-exit`)
  }
}

async function run() {
  w.graph193Checks = []
  w.graph193Failures = []
  w.graph193Samples = []
  w.graph193NodeReplacements = []
  try {
    await until(() => cards().length === 8 && !!flow() && !!viewport(), "initial graph mounted")
    await cycle("sakura", 1280, 900, 5)
    await cycle("aurora", 1280, 900, 5)
    await cycle("sakura", 390, 820, 3)
    await cycle("aurora", 390, 820, 3)
    w.graph193Result = { ok: w.graph193Failures.length === 0, checks: w.graph193Checks,
      failures: w.graph193Failures, samples: w.graph193Samples, nodeReplacements: w.graph193NodeReplacements,
      cycles: 16, themes: ["sakura", "aurora"], widths: [1280, 390] }
  } catch (error) {
    w.graph193Result = { ok: false, error: String(error), checks: w.graph193Checks, failures: w.graph193Failures,
      samples: w.graph193Samples, nodeReplacements: w.graph193NodeReplacements }
  }
}
// Qt host acknowledges screenshot and resize requests without using user input.
w.graph193Capture = null
w.graph193CaptureAck = null
w.graph193ResizeRequest = null
run()

