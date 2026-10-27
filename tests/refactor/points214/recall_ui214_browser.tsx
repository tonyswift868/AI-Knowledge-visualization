import * as React from "react"
import { createRoot, Root } from "react-dom/client"
import { RecallQuestion } from "../../../refactor/learning/web/src/classroom/RecallQuestion"
import { call, calls, fixture } from "./recall_ui214_bridge"

const host = document.getElementById("root")!
let root: Root
let nextCount = 0
function question(revealed = false, ticked?: number[]) {
  const data = fixture()
  return { id: "question-214", kind: "recall" as const, stem: "什么是深度分页？怎么导致的？",
    options: [], purpose: "最终检验", number: 1, total: 1, revealed,
    ...(revealed ? { answer: "已作答", reference: data.reference, key_points: data.key_points,
      key_point_headings: data.key_point_headings, ticked } : {}) }
}
function render(selfJudge = true, persisted?: number[]) {
  if (root) root.unmount()
  root = createRoot(host)
  root.render(<main style={{padding: 18}}><RecallQuestion question={question(persisted !== undefined, persisted)}
    request={call} busy={false} selfJudge={selfJudge} next={() => { nextCount++ }}/></main>)
}
function button(selector: string) {
  const found = document.querySelector<HTMLButtonElement>(selector)
  if (!found || found.disabled) throw new Error(`Unavailable button: ${selector}`)
  return found
}
async function clickEnabled(selector: string) {
  await waitFor(() => {
    const found = document.querySelector<HTMLButtonElement>(selector)
    return !!found && !found.disabled
  }, `${selector} enabled`)
  button(selector).click()
}
function waitFor(predicate: () => boolean, label: string) {
  return new Promise<void>((resolve, reject) => {
    const deadline = Date.now() + 10000
    const poll = () => {
      if (predicate()) resolve()
      else if (Date.now() > deadline) reject(new Error(`Timed out waiting for ${label}`))
      else setTimeout(poll, 20)
    }
    poll()
  })
}
function fillAnswer() {
  const input = document.querySelector<HTMLTextAreaElement>(".recall-own textarea")!
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "A written answer.")
  input.dispatchEvent(new Event("input", {bubbles:true}))
}

declare global { interface Window { recall214Result?: any; recall214NextCount?: () => number; recall214Debug?: () => unknown;
  recall214DefaultShotReady?: boolean; recall214ResolveDefaultShots?: () => void;
  recall214ShotReady?: boolean; recall214ResolveShots?: () => void } }
window.recall214NextCount = () => nextCount
window.recall214Debug = () => ({calls, body:document.body.innerText,
  buttons:[...document.querySelectorAll<HTMLButtonElement>("button")].map(b=>({text:b.textContent,disabled:b.disabled})),
  inputs:[...document.querySelectorAll<HTMLInputElement>(".recall-point input")].map(i=>({checked:i.checked,disabled:i.disabled,inert:i.matches(":disabled")})),
  result:window.recall214Result})
window.addEventListener("error", event => console.error("points214 browser error", event.error || event.message))
async function run() {
  const pointCount = fixture().key_points.length
  if (pointCount < 3) throw new Error("Deep-pagination fixture needs at least three points")
  render()
  await waitFor(() => !!document.querySelector(".recall-own textarea"), "answer input render")
  fillAnswer()
  await clickEnabled(".recall-answer-actions > button")
  await waitFor(() => document.querySelectorAll(".recall-point input").length === pointCount, "fresh reveal")
  await waitFor(() => !document.querySelector<HTMLFieldSetElement>(".recall-review fieldset")?.disabled, "fresh reveal ready")
  const inputs = [...document.querySelectorAll<HTMLInputElement>(".recall-point input")]
  if (!inputs.every(input => input.checked)) throw new Error("fresh reveal did not default every point to ticked")
  const headings = fixture().key_point_headings ?? []
  const expectedHeadingCount = headings.reduce((count, value, index) =>
    count + (value?.trim() && value.trim() !== headings[index - 1]?.trim() ? 1 : 0), 0)
  if (document.querySelectorAll(".recall-point-heading").length !== expectedHeadingCount)
    throw new Error("consecutive runtime headings did not render once per group")
  if (document.querySelector(".recall-point-heading")?.closest("label")) throw new Error("group heading must not be a checkbox")
  window.recall214DefaultShotReady = true
  await new Promise<void>(resolve => { window.recall214ResolveDefaultShots = resolve })

  // Toggle one point to missed: the X must be visible before submission.
  document.querySelectorAll<HTMLInputElement>(".recall-point input")[1].click()
  await waitFor(() => !!document.querySelector(".recall-point.missed"), "missed state")
  await waitFor(() => calls.filter(call => call.method === "class.recall_draft").length === 1 &&
    !document.querySelector<HTMLFieldSetElement>(".recall-review fieldset")?.disabled, "missed draft saved")
  if (!document.querySelectorAll(".recall-point em").length) throw new Error("missed point has no visible red X")
  window.recall214ShotReady = true
  await new Promise<void>(resolve => { window.recall214ResolveShots = resolve })

  // Restoring the tick removes the X and the submitted snapshot contains that restored point.
  document.querySelectorAll<HTMLInputElement>(".recall-point input")[1].click()
  await waitFor(() => calls.filter(call => call.method === "class.recall_draft").length === 2 &&
    document.querySelectorAll<HTMLInputElement>(".recall-point input")[1]?.checked, "restore missed point")
  if (document.querySelectorAll(".recall-point em").length) throw new Error("restored point still shows missed X")
  await waitFor(() => !document.querySelector<HTMLFieldSetElement>(".recall-review fieldset")?.disabled, "restore draft saved")
  button(".recall-review .row button").click()
  await waitFor(() => !!document.querySelector(".recall-result"), "self-judgement submit")
  const ownSubmit = calls.filter(call => call.method === "class.recall_submit").at(-1)?.params
  const expectedTicks = Array.from({length: pointCount}, (_, index) => index)
  if (JSON.stringify(ownSubmit?.ticked) !== JSON.stringify(expectedTicks) || ownSubmit.correct !== true)
    throw new Error(`self-judgement submit did not preserve the actual ticks: ${JSON.stringify(ownSubmit)}`)
  button(".recall-result button").click()
  if (nextCount !== 1) throw new Error("self-judgement did not continue to the next question")

  // Old already-revealed snapshots are not rewritten: an empty stored array stays empty.
  render(true, [])
  await waitFor(() => document.querySelectorAll(".recall-point input").length === pointCount, "old revealed snapshot")
  if ([...document.querySelectorAll<HTMLInputElement>(".recall-point input")].some(input => input.checked))
    throw new Error("empty legacy ticked snapshot was unexpectedly changed")

  // Runtime headings are independent of checkbox state and repeated labels render once per group.
  const data = fixture()
  data.key_point_headings = ["原因", "原因", "方案"]
  render(true, Array.from({length: pointCount}, (_, index) => index))
  await waitFor(() => document.querySelectorAll(".recall-point input").length === pointCount, "heading snapshot")
  if (document.querySelectorAll(".recall-point-heading").length !== 2 ||
      document.querySelector(".recall-point-heading")?.closest("label"))
    throw new Error("headings must be separate and de-duplicated by consecutive group")

  // Uploaded self-test uses the same defaults and reports the number of X marks from missed indices.
  calls.splice(0, calls.length)
  render(false)
  await waitFor(() => !!document.querySelector(".recall-own textarea"), "self-test answer input render")
  fillAnswer()
  await clickEnabled(".recall-answer-actions > button")
  await waitFor(() => document.querySelectorAll(".recall-point input").length === pointCount, "self-test reveal")
  await waitFor(() => !document.querySelector<HTMLFieldSetElement>(".recall-review fieldset")?.disabled, "self-test reveal ready")
  const selfInputs = [...document.querySelectorAll<HTMLInputElement>(".recall-point input")]
  if (!selfInputs.every(input => input.checked)) throw new Error("self-test reveal did not default every point to ticked")
  const selfMissed = [...new Set([1, pointCount - 1])]
  for (const index of selfMissed) document.querySelectorAll<HTMLInputElement>(".recall-point input")[index].click()
  await waitFor(() => [...document.querySelectorAll<HTMLInputElement>(".recall-point input")].filter(i => !i.checked).length === 2, "self-test toggles")
  button(".recall-review > button").click()
  await waitFor(() => !!document.querySelector(".recall-result"), "self-test submit")
  const selfSubmit = calls.filter(call => call.method === "class.recall_submit").at(-1)?.params
  const actualMisses = selfInputs.map((input, index) => input.checked ? -1 : index).filter(index => index >= 0)
  if (JSON.stringify(selfSubmit?.ticked) !== JSON.stringify(expectedTicks.filter(index => !selfMissed.includes(index))))
    throw new Error(`self-test submit recorded wrong ticks: ${JSON.stringify(selfSubmit)}`)
  if (!document.querySelector(".recall-result")?.textContent?.includes(`漏了 ${actualMisses.length} 条`))
    throw new Error("self-test result did not count missed points using the prior semantics")
  button(".recall-result button").click()
  if (nextCount !== 2) throw new Error("self-test continuation control changed")
  window.recall214Result = {ok:true, pointCount, expectedTicks, selfTestMissed:actualMisses.length, nextCount}
}
run().catch(error => { window.recall214Result = {ok:false, error:String(error?.stack || error), calls}; console.error(error) })
