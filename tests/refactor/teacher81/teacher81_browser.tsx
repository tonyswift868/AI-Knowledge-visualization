import * as React from "react"
import { createRoot } from "react-dom/client"
import { Settings } from "../../../refactor/learning/web/src/pages/Settings"
import { useApp } from "../../../refactor/learning/web/src/store"
import { connect, call, on } from "../../../refactor/learning/web/src/bridge"

const wait = (ms: number) => new Promise(r => setTimeout(r, ms))
const button = (name: string) => [...document.querySelectorAll("button")].find(b => b.textContent?.trim() === name) as HTMLButtonElement
const assert = (value: any, why: string) => { if (!value) throw Error(why) }
async function until(fn: () => any) { for (let i=0;i<500;i++) { if(fn()) return; await wait(20) }; throw Error("page timeout") }
function input(label: string, value: string) {
  const el = document.querySelector(`[aria-label="${label}"]`) as HTMLInputElement
  Object.getOwnPropertyDescriptor(el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value")!.set!.call(el, value)
  el.dispatchEvent(new Event("input", { bubbles: true }))
}
async function run() {
  assert(await connect(), "必须使用正式 Qt bridge，不能使用浏览器 mock")
  const r = await call("settings.get")
  useApp.getState().set({ settings: r.settings, settingsTab: "learning", ready: true })
  on("settings", settings => useApp.getState().set({ settings }))
  createRoot(document.getElementById("root")!).render(<Settings />)
  await until(() => document.querySelectorAll("[data-preset-id]").length === 4)
  assert(button("新建预设").disabled, "4 个预设不能再新建")
  assert(document.body.textContent?.includes("最多 4 个预设"), "上限说明")
  button("编辑 相声老师").click()
  await until(() => document.querySelector('[aria-label="老师人设"]'))
  input("老师人设", "说话像相声演员，以生活例子解释概念")
  input("预设名称", "相声新老师")
  await wait(80)
  assert((document.querySelector('[aria-label="老师人设"]') as HTMLTextAreaElement).maxLength === 2000, "人设长度")
  button("保存预设").click()
  await until(() => button("编辑 相声新老师") && !document.querySelector('[aria-label="编辑老师预设"]'))
  ;(window as any).captureReady = true
  await until(() => (window as any).captured)
  const card = document.querySelector('[data-preset-id="teacher-2"]')!
  ;([...card.querySelectorAll("button")].find(b => b.textContent?.trim() === "删除") as HTMLButtonElement).click()
  await until(() => document.querySelector('[aria-label="确认删除预设"]'))
  assert(document.body.textContent?.includes("有 1 个计划在用"), "删除在用预设必须先提示")
  button("取消").click(); await wait(80)
  assert(document.querySelectorAll("[data-preset-id]").length === 4, "取消不删除")
  ;([...card.querySelectorAll("button")].find(b => b.textContent?.trim() === "删除") as HTMLButtonElement).click()
  await until(() => button("确认删除")); button("确认删除").click()
  await until(() => document.querySelectorAll("[data-preset-id]").length === 3)
  const detail = await call("plan.detail", { plan_id: "8".repeat(32) })
  assert(detail.teacher_preset.id === "teacher-1", "已使用的计划回退默认")
  assert(!button("新建预设").disabled, "删除后可再新建")
  ;(window as any).result = { ok: true, checks: ["正式 Qt 桥接", "4 槽位限制", "编辑名称与人设", "2000 字", "删除确认与取消", "计划回退默认"] }
}
run().catch(error => { (window as any).result = { ok: false, error: String(error), body: document.body.textContent } })
