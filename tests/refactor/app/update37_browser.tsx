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
const banner = () => document.querySelector('[aria-label="软件更新提示"]')
const assert = (ok: any, message: string) => { if (!ok) throw Error(message) }
const button = (text: string) => [...document.querySelectorAll("button")].find((b) => b.textContent?.trim() === text) as HTMLButtonElement

async function run() {
  const passed: string[] = []
  await wait(180)
  assert(fixture.checks === 0 && !dialog(), "登录前不得检查/弹框")
  passed.push("登录前无提示")
  useApp.getState().set({ ready: true })
  await wait(1300)
  assert(fixture.checks === 1 && banner()?.textContent?.includes("发现新版本 2.0.2"), "登录后显示可选更新横幅")
  assert(!dialog() && !document.querySelector(".shell")?.inert, "可选更新不得弹框或阻断主界面")
  passed.push("登录后检查/可选更新非模态横幅")
  advance = 30 * 60 * 1000 - 5000; await wait(1300)
  assert(fixture.checks === 1, "不到30分钟不自动检查")
  advance = 30 * 60 * 1000; await wait(2300)
  assert(fixture.checks === 2 && !dialog() && !!banner(), "30分钟自动检查不弹框且横幅仍可见")
  passed.push("30分钟自动检查仍保持非模态")
  useApp.getState().go("settings", "account"); await wait(80)
  button("检查更新").click(); await wait(1300)
  assert(banner() && !dialog() && !document.querySelector(".shell")?.inert, "手动检查到可选更新仍保持非模态")
  fixture.newest = false
  button("检查更新").click(); await wait(1300)
  assert(!dialog() && document.body.textContent?.includes("已是最新版本 2.0.1"), "手动无新版提示")
  passed.push("手动有/无新版")
  fixture.newest = true
  fixture.status.deferred = false // a fresh synthetic run for the classroom scenario
  useUpdates.setState({ later: false, open: false })
  useApp.getState().set({ planId: "synthetic" })
  useApp.getState().go("classroom"); await checkUpdate(false); await wait(1300)
  assert(!dialog() && banner() && !document.querySelector(".shell")?.inert, "可选更新不得打扰课堂")
  passed.push("课堂可选更新保持安静")
  button("立即更新").click(); await wait(100)
  assert(fixture.installs === 1 && dialog()?.textContent?.includes("2.00 MB / 10.00 MB"), "显式点击横幅后开始更新并显示下载进度")
  assert(!button("稍后") || button("稍后").disabled, "下载开始后不能把进度误当成尚未同意的弹框")
  passed.push("横幅点击启动下载/进度")
  // A required update must persist the active classroom before opening
  // its modal, even when discovered while the classroom route is active.
  fixture.mandatory = true
  fixture.classroomEvents.length = 0
  fixture.status = { ...fixture.status, state: "UPDATE_AVAILABLE", busy: false, mandatory: true }
  useUpdates.setState({ status: fixture.status, open: false, later: false })
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
  assert(fixture.installs === 2 && dialog()?.textContent?.includes("2.00 MB / 10.00 MB"), "框内进度")
  passed.push("强制更新/框内进度")
  window.result = { ok: true, passed, checks: fixture.checks, installs: fixture.installs }
}
run().catch((e) => { window.result = { ok: false, error: String(e), body: document.body.textContent } })
