import * as React from "react"
import { createRoot } from "react-dom/client"
import { App } from "../../../refactor/learning/web/src/App"
import { useApp } from "../../../refactor/learning/web/src/store"
import { fixture } from "./update37_browser_bridge"
import { useUpdates, checkUpdate } from "../../../refactor/learning/web/src/updates/UpdateNotice"

declare global { interface Window { result: any } }
const realNow = Date.now
let advance = 0
Date.now = () => realNow() + advance
createRoot(document.getElementById("root")!).render(<App />)
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))
const dialog = () => document.querySelector('[role="dialog"]')
const assert = (ok: any, message: string) => { if (!ok) throw Error(message) }
const button = (text: string) => [...document.querySelectorAll("button")].find((b) => b.textContent?.trim() === text) as HTMLButtonElement

async function run() {
  const passed: string[] = []
  await wait(180)
  assert(fixture.checks === 0 && !dialog(), "登录前不得检查/弹框")
  passed.push("登录前无提示")
  useApp.getState().set({ ready: true })
  await wait(1300)
  assert(fixture.checks === 1 && dialog()?.textContent?.includes("2.0.1 → 新版本 2.0.2"), "登录后版本弹框")
  assert(dialog()?.textContent?.includes("慢网自动续传与新课堂"), "更新内容必须来自清单")
  passed.push("登录后检查/版本/更新内容")
  button("稍后").click(); await wait(80)
  assert(!dialog() && document.querySelector('[aria-label="有新版本"]'), "稍后后设置红点")
  advance = 30 * 60 * 1000 - 5000; await wait(1300)
  assert(fixture.checks === 1, "不到30分钟不自动检查")
  advance = 30 * 60 * 1000; await wait(2300)
  assert(fixture.checks === 2 && !dialog(), "30分钟自动检查但本次运行不再提醒可选更新")
  passed.push("稍后/30分钟/设置红点")
  useApp.getState().go("settings", "account"); await wait(80)
  assert(document.querySelectorAll('[aria-label="有新版本"]').length >= 2, "关于也必须有红点")
  button("检查更新").click(); await wait(1300)
  assert(dialog(), "手动检查有新版使用同一弹框")
  button("稍后").click(); await wait(80)
  fixture.newest = false
  button("检查更新").click(); await wait(1300)
  assert(!dialog() && document.body.textContent?.includes("已是最新版本 2.0.1"), "手动无新版提示")
  passed.push("手动有/无新版")
  fixture.newest = true
  fixture.status.deferred = false // a fresh synthetic run for the classroom scenario
  useUpdates.setState({ later: false, open: false })
  useApp.getState().set({ planId: "synthetic" })
  useApp.getState().go("classroom"); await checkUpdate(false); await wait(1300)
  assert(!dialog(), "可选更新不得打扰课堂")
  passed.push("课堂可选更新保持安静")
  // A required update must persist the active classroom before opening
  // its modal, even when discovered while the classroom route is active.
  fixture.mandatory = true
  fixture.classroomEvents.length = 0
  await checkUpdate()
  await wait(1500)
  assert(fixture.classroomEvents.includes("draft_set") && fixture.classroomEvents.includes("classroom.state"), "强制更新前持久保存输入和课堂进度")
  const draftSavedAt = fixture.classroomEvents.indexOf("draft_set")
  assert(fixture.classroomEvents.indexOf("classroom.state", draftSavedAt + 1) > draftSavedAt, "未发送输入先于强制更新课堂状态保存")
  assert(dialog() && button("立即更新") && !button("稍后"), "课堂保存后显示无稍后的强制更新框")
  passed.push("课堂强制更新先保存再弹框")
  useApp.getState().go("home"); await wait(100)
  assert(dialog(), "强制更新离开课堂后仍保持弹框")
  useUpdates.setState({ open: false })
  useApp.getState().go("settings", "account")
  await checkUpdate(); await wait(1300)
  assert(dialog() && button("立即更新") && !button("稍后"), "强制更新不提供稍后")
  button("立即更新").click(); await wait(1300)
  assert(fixture.installs === 1 && dialog()?.textContent?.includes("2.00 MB / 10.00 MB"), "框内进度")
  passed.push("强制更新/框内进度")
  window.result = { ok: true, passed, checks: fixture.checks, installs: fixture.installs }
}
run().catch((e) => { window.result = { ok: false, error: String(e), body: document.body.textContent } })
