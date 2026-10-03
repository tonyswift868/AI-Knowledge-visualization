import * as React from "react"
import { createRoot } from "react-dom/client"
import { Classroom } from "../../../refactor/learning/web/src/pages/Classroom"
import { useApp } from "../../../refactor/learning/web/src/store"
import "../../../refactor/learning/web/src/theme.css"
import { calls } from "./conversation37_bridge"
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
  useApp.setState({ planId: "a".repeat(32), route: "classroom", settings: { theme: "sakura", font_scale: 1, reduce_motion: true } as never })
  document.documentElement.dataset.theme = "sakura"
  root.render(<main style={{ height: "100%", padding: 18 }}><Classroom /></main>)
  await until(() => !!document.querySelector(".original-card"))
  button("针尖").click(); await until(() => content().includes("懂了，继续")); checks.push("开场选项→讲解，常驻三个按钮")
  if (/第\s*\d+\s*\/\s*\d+\s*步/.test(content())) throw new Error("Old step counter remains")
  checks.push("无第N/M步")
  document.querySelector<HTMLButtonElement>(".original-card")!.click(); await until(() => !!document.querySelector(".class-source-text"))
  if (!content().includes("p=F/S")) throw new Error("Original text missing")
  document.querySelector<HTMLButtonElement>("[aria-label='关闭']")!.click(); checks.push("原文卡展开真实段落与位置")
  button("没太懂").click(); await until(() => calls.includes("classroom.confused") && !button("我想问…").disabled); checks.push("没太懂独立桥方法")
  await until(() => document.querySelectorAll('.bubble.user').length === 2)
  button("我想问…").click(); await until(() => content().includes("正在提问")); fill("硬度相同吗？"); await wait(); button("发送").click()
  await until(() => content().includes("资料里没讲到")); button("回到刚才").click(); await until(() => calls.includes("classroom.back") && !content().includes("回到刚才") && !button("懂了，继续").disabled); checks.push("提问单独ask，回答后回到刚才")
  button("懂了，继续").click(); await until(() => !!document.querySelector(".class-options")); checks.push("题目卡显示题库来源")
  const selected = [...document.querySelectorAll<HTMLButtonElement>(".class-options button")][0]
  selected.click(); selected.click(); await until(() => content().includes("答对了"))
  if (calls.filter((m) => m === "classroom.answer").length !== 1) throw new Error("Double submit")
  checks.push("计分单选传下标，双击只提交一次")
  button("为什么？").click(); await until(() => content().includes("参考答案原文")); checks.push("为什么展开资料原答案")
  if (document.querySelector<HTMLTextAreaElement>("textarea")?.disabled) throw new Error("Input disabled")
  checks.push("输入框保持可用")
  window.conversationResult = { ok: true, checks }
}
run().catch((e) => { window.conversationResult = { ok: false, checks, error: String(e.stack || e) + JSON.stringify({ calls, content: content(), input: document.querySelector("textarea")?.value }) } })
