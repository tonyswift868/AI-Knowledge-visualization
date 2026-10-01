import * as React from "react"
import { createRoot } from "react-dom/client"
import { Classroom } from "../../../refactor/learning/web/src/pages/Classroom"
import { Report } from "../../../refactor/learning/web/src/pages/Report"
import { useApp, type Settings } from "../../../refactor/learning/web/src/store"
import "../../../refactor/learning/web/src/theme.css"
import * as fake from "./guided_bridge"
import * as data from "./guided_snapshots"

declare global { interface Window { guidedResult?: { ok: boolean; checks: string[]; error?: string }; guidedCaptureReady: boolean;
  guidedShow: (scenario: string, theme: string, scale?: number, reduce?: boolean) => Promise<void> } }
const wait = (ms = 20) => new Promise((r) => setTimeout(r, ms))
const checks: string[] = []
const assert = (value: unknown, message: string) => { if (!value) throw new Error(message) }
const content = () => document.body.textContent ?? ""
async function until(test: () => boolean, message: string) {
  const end = performance.now() + 5000
  while (!test()) { assert(performance.now() < end, message); await wait() }
}
function button(text: string) {
  const result = [...document.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.trim() === text)
  assert(result, `missing button ${text}`); return result!
}
function input(value: string) {
  const field = document.querySelector("textarea")!
  assert(!field.disabled, "input must always remain editable")
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(field, value)
  field.dispatchEvent(new Event("input", { bubbles: true }))
}
function enter(target: EventTarget = document) {
  target.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }))
}
const calls = (method: string) => fake.log.filter((entry) => entry.method === method)
const root = createRoot(document.getElementById("root")!)
let key = 0
function Frame({ report, version }: { report: boolean; version: number }) {
  return <main data-fixture-version={version} style={{ height: "100%", padding: 18, overflowY: report ? "auto" : "hidden" }}>
    {report ? <Report key={version} /> : <Classroom key={version} />}
  </main>
}
async function mount(value = data.snapshot(), options: { hold?: boolean; fail?: boolean; live?: boolean; report?: boolean } = {}) {
  fake.configure(value, options)
  const version = ++key
  root.render(<Frame version={version} report={!!options.report} />)
  await until(() => !!document.querySelector(`[data-fixture-version="${version}"] ${options.report ? ".hero" : ".class-main"}`), "mount timed out")
  await wait(40)
}
function theme(name: string, scale = 1, reduce = false) {
  document.documentElement.dataset.theme = name
  document.documentElement.style.setProperty("--scale", String(scale))
  document.documentElement.classList.toggle("reduce-motion", reduce)
  useApp.setState({ settings: { theme: name, font_scale: scale, reduce_motion: reduce } as Settings })
}
async function run() {
  useApp.setState({ planId: "guided-plan", route: "classroom" })
  localStorage.setItem("mf.classroom.read", "off")
  theme("sakura")
  await mount(data.snapshot(), { hold: true })
  assert(content().includes("第 1/3 步"), "step progress")
  const next = button("继续")
  next.click(); next.click(); enter()
  await until(() => next.disabled, "continue not locked")
  assert(calls("classroom.next").length === 1, "same-tick double click submitted twice")
  input("我正在组织一个问题"); await wait()
  assert(document.querySelector("textarea")!.value === "我正在组织一个问题", "busy input discarded")
  fake.release()
  await until(() => content().includes("引导小问题") || !!document.querySelector(".lesson-interaction"), "guide not visible")
  assert(document.querySelector("textarea")!.value === "我正在组织一个问题", "next must preserve draft")
  checks.push("continue: immediate duplicate lock, draft remains editable while waiting")

  const option = document.querySelector<HTMLButtonElement>(".lesson-interaction .choice:nth-child(2)")!
  option.click(); option.click()
  await until(() => content().includes("老师回应"), "prepared response missing")
  assert(calls("classroom.choose").length === 1 && calls("classroom.choose")[0].params.option === 1, "guide must send zero-based option once")
  assert(content().includes("第 3/3 步") && document.querySelector(".class-choice"), "reply/choice styles or progress missing")
  checks.push("guided choice: zero-based classroom.choose, prepared response and student-choice styles")

  input("为什么两边要做同样的操作？"); await wait(); button("发送").click()
  await until(() => !![...document.querySelectorAll("button")].find((b) => b.textContent === "回到刚才"), "return missing")
  button("回到刚才").click()
  await until(() => !![...document.querySelectorAll("button")].find((b) => b.textContent === "继续"), "resume missing")
  assert(calls("classroom.back").length === 1 && content().includes("第 3/3 步"), "back lost progress")
  checks.push("interjection: classroom.reply, return button calls classroom.back and retains step")

  input(""); await wait(); enter(document.querySelector("textarea")!)
  await until(() => !!document.querySelector('[aria-label="随口问选项"]'), "casual choice missing")
  assert(calls("classroom.next").length === 2, "empty composer Enter must continue")
  const correct = document.querySelector<HTMLButtonElement>('[aria-label="随口问选项"] .choice:nth-child(2)')!
  correct.click(); correct.click()
  await until(() => content().includes("现在进入下一点"), "correct did not advance")
  assert(calls("classroom.reply").at(-1)?.params.text === "x = 5" && content().includes("答对了"), "casual sends option original text")
  assert(content().includes("移项与解方程") && content().includes("第 1/3 步"), "next point progress")
  checks.push("casual correct: original option text via classroom.reply, verdict then next point")

  await mount(data.casual(), { hold: true })
  const wrong = document.querySelector<HTMLButtonElement>('[aria-label="随口问选项"] .choice')!
  wrong.click(); wrong.click(); await wait()
  assert(wrong.disabled && calls("classroom.reply").length === 1, "casual options not locked")
  input("等老师纠正时也可以打字"); await wait(); fake.release()
  await until(() => content().includes("再试一个"), "second casual missing")
  assert(content().includes("老师回应") && content().includes("y + 3 = 10") && content().includes("y = 7"), "wrong correction/second question missing")
  assert(document.querySelector("textarea")!.value === "等老师纠正时也可以打字", "answer response discarded new draft")
  checks.push("casual wrong: locked options, correction then second prepared question")

  await mount(data.snapshot(), { fail: true })
  enter(); await until(() => calls("classroom.state").length === 1, "failure refresh missing")
  await until(() => !button("继续").disabled, "failed operation left controls locked")
  enter(); await until(() => !!document.querySelector(".lesson-interaction"), "retry failed")
  const cite = document.querySelector<HTMLButtonElement>(".cite")!
  cite.click(); await until(() => !!document.querySelector('[role="dialog"]'), "citation not opened")
  assert(calls("classroom.source")[0].params.chunk_id === "chunk-equality", "citation alias changed")
  checks.push("keyboard continue, failed action unlock/retry, Markdown and citation bridge retained")

  await mount(data.snapshot(), { hold: true, fail: true })
  input("发送的提问"); await wait(); enter(document.querySelector("textarea")!)
  await until(() => calls("classroom.reply").length === 1, "typed Enter did not send interjection")
  input("响应期间的新草稿"); await wait(); fake.release()
  await until(() => calls("classroom.state").length === 1, "reply failure refresh missing")
  await until(() => !![...document.querySelectorAll("button")].find((b) => b.textContent === "发送"), "failed reply stayed busy")
  assert(document.querySelector("textarea")!.value === "响应期间的新草稿", "failed reply overwrote newer draft")
  await mount({ ...data.casual(), lesson: data.lesson(3, "return") })
  assert(button("回到刚才") && !document.querySelector('[aria-label="随口问选项"]'), "saved question must not hide return control")
  checks.push("draft recovery: failed reply keeps newer input; return takes precedence over saved question")

  await mount(data.snapshot())
  const before = fake.log.length
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", shiftKey: true, bubbles: true }))
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true }))
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", repeat: true, bubbles: true }))
  await wait()
  assert(!fake.log.slice(before).some((entry) => entry.method === "classroom.next"), "composition/held/modified Enter must not advance")
  await mount({ ...data.snapshot(), step: "preparing_questions", lesson: null })
  input("备题时也可以先打字"); await wait()
  assert(document.querySelector("textarea")!.value === "备题时也可以先打字", "preparation input locked")
  checks.push("keyboard safety: composing/repeated/Shift Enter ignored; preparation remains editable")

  await mount(data.snapshot(), { report: true, live: false })
  assert(!content().includes("讲稿没通过核对"), "empty live list must stay hidden")
  await mount(data.snapshot(), { report: true })
  assert(content().includes("以下知识点是老师现场讲的（讲稿没通过核对）") && content().includes("含括号的方程"), "live-point report missing")
  checks.push("report: live_points list visible, empty list hidden")

  theme("aurora", 1.4, true); await mount()
  assert(Math.abs(parseFloat(getComputedStyle(document.documentElement).fontSize) - 21) < .2, "font scale not inherited")
  assert(getComputedStyle(document.querySelector(".class-step")!).animationName === "none", "reduced motion ignored")
  checks.push("aurora, 140% text and reduced motion inherited by guided messages")
  window.guidedResult = { ok: true, checks }
}
window.guidedShow = async (scenario, name, scale = 1, reduce = true) => {
  window.guidedCaptureReady = false
  theme(name, scale, reduce)
  const value = scenario === "choice" ? data.guide() : scenario === "reply" ? data.guideReply() :
    scenario === "return" ? data.returned() : scenario === "casual" ? data.casual(data.guideReply()) :
    scenario === "wrong" ? data.casual({ ...data.guideReply(), messages: [...data.guideReply().messages,
      data.message("verdict", "✗ 这次还没答对"), data.message("step_reply", "两边同时减去 3，得到 x = 5。换一道备好的随口问再试试。") ] }, true) : data.snapshot()
  await mount(value, { report: scenario === "report" })
  await wait(150)
  window.guidedCaptureReady = true
}
run().catch((error) => { window.guidedResult = { ok: false, checks, error: String(error) } })
