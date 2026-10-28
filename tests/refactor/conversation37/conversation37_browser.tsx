import * as React from "react"
import { createRoot } from "react-dom/client"
import { Classroom } from "../../../refactor/learning/web/src/pages/Classroom"
import { QuestionInputs } from "../../../refactor/learning/web/src/classroom/Conversation"
import { useApp } from "../../../refactor/learning/web/src/store"
import "../../../refactor/learning/web/src/theme.css"
import { calls, requests, showNumeric } from "./conversation37_bridge"
declare global { interface Window { conversationResult?: { ok: boolean; checks: string[]; error?: string }; conversationShow: (stage: string) => void } }
const root = createRoot(document.getElementById("root")!)
const wait = (ms = 20) => new Promise((r) => setTimeout(r, ms))
const checks: string[] = []
function button(text: string) {
  const b = [...document.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.trim() === text)
  if (!b) throw new Error(`missing button ${text}`)
  return b
}
async function until(predicate: () => boolean) { for (let i = 0; i < 250; i++) { if (predicate()) return; await wait() }; throw new Error("UI timed out") }
const content = () => document.body.textContent ?? ""
function fill(value: string) {
  const input = document.querySelector("textarea")!
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, value)
  input.dispatchEvent(new Event("input", { bubbles: true }))
  input.dispatchEvent(new Event("change", { bubbles: true }))
}
async function run() {
  root.render(<QuestionInputs question={{ id: "legacy-quick", kind: "choice", stem: "旧随口问", options: ["甲", "乙"],
    source: { title: "旧题", url: "", locator: "" }, origin: "cloze", purpose: "quick" }} act={async () => { throw new Error("quick must not submit") }} busy={false} />)
  await wait(50)
  if (document.querySelector(".bank-question-controls")) throw new Error("Legacy quick card remains")
  checks.push("旧随口问题卡无题目输入或提交入口")
  useApp.setState({ planId: "a".repeat(32), route: "classroom", settings: { theme: "sakura", font_scale: 1, reduce_motion: true } as never })
  document.documentElement.dataset.theme = "sakura"
  root.render(<main style={{ height: "100%", padding: 18 }}><Classroom /></main>)
  await until(() => !!document.querySelector(".original-toggle"))
  button("针尖").click(); await until(() => content().includes("懂了，继续")); checks.push("开场选项→讲解，常驻三个按钮")
  if (/第\s*\d+\s*\/\s*\d+\s*步/.test(content())) throw new Error("Old step counter remains")
  checks.push("无第N/M步")
  document.querySelector<HTMLButtonElement>(".original-toggle")!.click(); await until(() => !!document.querySelector(".original-card"))
  document.querySelector<HTMLButtonElement>(".original-card")!.click(); await until(() => !!document.querySelector(".original-fulltext"))
  if (!content().includes("p=F/S")) throw new Error("Original text missing")
  document.querySelector<HTMLButtonElement>(".original-toggle")!.click(); checks.push("原文卡默认折叠，展开真实段落与位置")
  button("没太懂").click(); await until(() => calls.includes("classroom.confused") && !button("我想问…").disabled); checks.push("没太懂独立桥方法")
  await until(() => document.querySelectorAll('.bubble.user').length === 2)
  button("我想问…").click(); fill("硬度相同吗？"); await wait(); button("发送").click()
  await until(() => content().includes("讲义里没有，下面是补充") && !button("懂了，继续").disabled); checks.push("讲课提问走统一reply，保留原位置")
  button("懂了，继续").click(); await until(() => !!document.querySelector(".class-options")); checks.push("题目卡显示题库来源")
  const selected = [...document.querySelectorAll<HTMLButtonElement>(".class-options button")][0]
  selected.click(); selected.click(); await until(() => content().includes("答对了"))
  if (calls.filter((m) => m === "classroom.answer").length !== 1) throw new Error("Double submit")
  checks.push("计分单选传下标，双击只提交一次")
  button("为什么？").click(); await until(() => content().includes("参考答案原文")); checks.push("计分题为什么展开解析或答案出处")
  if (document.querySelector<HTMLTextAreaElement>("textarea")?.disabled) throw new Error("Input disabled")
  checks.push("输入框保持可用")
  showNumeric()
  root.render(<main style={{ height: "100%", padding: 18 }}><Classroom key="numeric" /></main>)
  await until(() => document.querySelector<HTMLTextAreaElement>("textarea")?.placeholder === "填写计算数值，可带单位")
  fill("2000 Pa"); await wait(); button("发送").click()
  await until(() => requests.filter((r) => r.method === "classroom.answer").length === 2)
  const numeric = requests.filter((r) => r.method === "classroom.answer")[1]
  if (numeric.params.value !== "2000 Pa" || "text" in numeric.params) throw new Error("Numeric answer routed through reply")
  await until(() => content().includes("答对了"))
  checks.push("计算值含单位经answer提交，由程序判，未走AI回复")
  window.conversationResult = { ok: true, checks }
}
run().catch((e) => { window.conversationResult = { ok: false, checks, error: String(e.stack || e) + JSON.stringify({ calls, content: content(), input: document.querySelector("textarea")?.value }) } })
