import * as React from "react"
import { createRoot } from "react-dom/client"
import { Classroom } from "../../../refactor/learning/web/src/pages/Classroom"
import { Settings } from "../../../refactor/learning/web/src/pages/Settings"
import { useApp } from "../../../refactor/learning/web/src/store"
import { call, fixture177 } from "./bridge177"
import "../../../refactor/learning/web/src/theme.css"

type Identity = { name: string; account_id: string; role_label: string } | null
const root = createRoot(document.getElementById("root")!)
let mode: "classroom" | "settings" = "classroom"
let pageKey = 0
let phase = "initializing"
function render() {
  root.render(mode === "classroom" ? <Classroom key={pageKey}/> : <Settings key={pageKey}/> )
}
function identity(account_id: string, name = "同名学习者"): Identity {
  return account_id ? { name, account_id, role_label: "学习者" } : null
}
function scopedKey(accountId: string) {
  return `mf.classroom.unsent.v2:${encodeURIComponent(accountId)}.${encodeURIComponent("plan177")}`
}
function setIdentity(account_id: string, name = "同名学习者") {
  fixture177.accountId = account_id || null
  useApp.getState().set({ account: identity(account_id, name) })
}
function textarea() { return document.querySelector('textarea[aria-label="课堂输入"]') as HTMLTextAreaElement | null }
function setInput(value: string) {
  const el = textarea()
  if (!el) throw Error("课堂输入框缺失")
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!
  setter.call(el, value)
  el.dispatchEvent(new Event("input", { bubbles: true }))
}
function wait(ms: number) { return new Promise(resolve => setTimeout(resolve, ms)) }
async function until(test: () => boolean, message: string) {
  for (let i = 0; i < 300; i++) { if (test()) return; await wait(20) }
  throw Error(message)
}
function assert(value: any, message: string) { if (!value) throw Error(message) }
function switchPage(next: "classroom" | "settings") { mode = next; pageKey++; render() }
function mountClassroom(accountId: string, name = "同名学习者") {
  mode = "classroom"; pageKey++; fixture177.accountId = accountId || null
  useApp.getState().set({ route: "classroom", planId: "plan177", account: identity(accountId, name), ready: true,
    settings: { theme: "sakura", font_scale: 1, reduce_motion: true } as any })
  render()
}
async function mountClassroomAndWait(accountId: string, name = "同名学习者") {
  const previousTextarea = textarea()
  const previousEnterCalls = fixture177.enterCalls
  mountClassroom(accountId, name)
  // Wait for this mount's own enter request and a newly committed textarea.
  // Merely seeing a textarea can match the previous component before React
  // commits the keyed remount, then disappear as the old tree unmounts.
  await until(() => fixture177.enterCalls > previousEnterCalls && !!textarea() && textarea() !== previousTextarea,
    `Classroom remount not committed (enterCalls ${previousEnterCalls} -> ${fixture177.enterCalls})`)
}

async function run() {
  const results: string[] = []
  ;(window as any).fixture177 = fixture177
  ;(window as any).setIdentity177 = setIdentity
  ;(window as any).mountClassroom177 = mountClassroom
  phase = "initial account-A classroom mount"
  await mountClassroomAndWait("account-A")
  await wait(80)
  const keyA = scopedKey("account-A")
  const keyB = scopedKey("account-B")
  setInput("A 的未发送草稿")
  assert(localStorage.getItem(keyA) === "A 的未发送草稿", "草稿键必须使用稳定 account_id")
  setIdentity("account-B", "同名学习者")
  await until(() => textarea()?.value === "", "同名的 B 账号看到了 A 草稿")
  assert(localStorage.getItem(keyA) === "A 的未发送草稿", "切换账号不能删除 A 草稿")
  setInput("B 的未发送草稿")
  assert(localStorage.getItem(keyB) === "B 的未发送草稿", "B 草稿必须独立保存")
  setIdentity("account-A", "改过的显示名")
  await until(() => textarea()?.value === "A 的未发送草稿", "同 account_id 改显示名后未恢复原草稿")
  results.push("同名账号隔离、同账号改名恢复")

  localStorage.setItem("mf.classroom.unsent.anonymous.plan177", "匿名旧草稿")
  const oldNameKey = "mf.classroom.unsent.%E5%90%8C%E5%90%8D%E5%AD%A6%E4%B9%A0%E8%80%85.plan177"
  localStorage.setItem(oldNameKey, "显示名旧草稿")
  phase = "unresolved identity classroom mount"
  await mountClassroomAndWait("")
  await wait(50)
  assert(textarea()?.value === "", "未解析 account_id 不得读旧 anonymous/display-name key")
  assert(!fixture177.draftGets.includes("<unresolved>"), "未解析 account_id 不得请求 durable fallback")
  assert(localStorage.getItem("mf.classroom.unsent.anonymous.plan177") === "匿名旧草稿", "匿名旧键应保留但不导入")
  assert(localStorage.getItem(oldNameKey) === "显示名旧草稿", "显示名旧键应保留但不导入")
  results.push("旧 anonymous/display-name 与无 owner 草稿均不迁移")

  const collisionKey = "mf.classroom.unsent.legacy-collision.plan177"
  const namespaceLikeNameKey = "mf.classroom.unsent.v2%3Alegacy-collision.plan177"
  localStorage.setItem(collisionKey, "显示名刚好等于账号 ID 的旧草稿")
  localStorage.setItem(namespaceLikeNameKey, "含新 namespace 字符串的旧显示名草稿")
  phase = "legacy display name equals account id"
  await mountClassroomAndWait("legacy-collision", "legacy-collision")
  await wait(60)
  assert(textarea()?.value === "", "旧显示名恰等于 account_id 时不能被导入")
  phase = "legacy display name resembles v2 namespace"
  await mountClassroomAndWait("legacy-collision", "v2:legacy-collision")
  await wait(60)
  assert(textarea()?.value === "", "含新 namespace 字符串的旧显示名也不能被导入")
  assert(localStorage.getItem(collisionKey) === "显示名刚好等于账号 ID 的旧草稿", "碰撞旧键需原样保留")
  assert(localStorage.getItem(namespaceLikeNameKey) === "含新 namespace 字符串的旧显示名草稿", "namespace 类似旧键需原样保留")
  results.push("legacy key/account_id 与 namespace 类似字符串碰撞隔离")

  phase = "identity arrives after unresolved live draft"
  await mountClassroomAndWait("")
  setInput("身份解析前的 live 草稿")
  assert(localStorage.getItem("mf.classroom.unsent.anonymous.plan177") === "匿名旧草稿", "未绑定身份的输入不得覆盖旧 anonymous key")
  setIdentity("account-late", "晚到账号")
  await until(() => textarea()?.value === "", "账号晚到时应清除无 owner 的输入")
  assert(localStorage.getItem(scopedKey("account-late")) === null, "账号晚到时不得迁移 live 草稿到本地账号键")
  assert(localStorage.getItem("mf.classroom.unsent.anonymous.plan177") === "匿名旧草稿", "晚到身份不得改写或删除既有 anonymous key")
  assert(!fixture177.draftGets.includes("<unresolved>"), "账号晚到前不可 durable 读取")
  results.push("账号晚到清除未绑定 live 输入、不迁移")

  fixture177.durable.set("account-fallback:plan177", "账号 durable 旧输入")
  phase = "current account durable fallback"
  await mountClassroomAndWait("account-fallback")
  await until(() => textarea()?.value === "账号 durable 旧输入", "当前账号 durable fallback 未恢复")
  // Make a durable result arrive after the user has already typed. It must not
  // overwrite the live value or backflow into a different account's key.
  fixture177.deferNextDraft = true
  phase = "late durable response after current-account input"
  await mountClassroomAndWait("account-race")
  await until(() => !!textarea() && fixture177.draftGets.includes("account-race"), "race fallback 未开始")
  await until(() => fixture177.resolveDeferredDraft !== null, "durable race 未挂起")
  setInput("用户刚输入的新内容")
  fixture177.resolveDeferredDraft!({ text: "迟到的 durable 旧内容" })
  await wait(40)
  assert(textarea()?.value === "用户刚输入的新内容", "迟到的 durable fallback 覆盖了 live 输入")
  assert(localStorage.getItem(scopedKey("account-race")) === "用户刚输入的新内容", "live 输入应留在当前账号分区")
  results.push("durable fallback 不覆盖新输入")

  const defaults = { version: 1, theme: "sakura", font_scale: 1, reduce_motion: true, teacher_style: "warm", detail: "normal",
    examples: "normal", analogy_domains: ["life"], refresh_time: "04:00", reminder_enabled: false,
    reminder_time: "20:00", chat_persona: "" }
  phase = "notification setting UI"
  fixture177.accountId = "settings-A"
  fixture177.settingsByAccount.set("settings-A", { ...defaults })
  useApp.getState().set({ account: identity("settings-A", "提醒偏好测试"), settingsTab: "time", settings: { ...defaults } as any })
  switchPage("settings")
  await until(() => !!document.querySelector('[role="switch"][aria-label="通知里显示计划名"]'), "提醒开关未显示")
  const planNameSwitch = document.querySelector('[role="switch"][aria-label="通知里显示计划名"]') as HTMLButtonElement
  assert(planNameSwitch.getAttribute("aria-checked") === "false", "新账号的显示计划名开关默认必须关闭")
  planNameSwitch.click()
  await until(() => fixture177.settingsByAccount.get("settings-A")?.show_plan_name_in_notification === true, "开关未保存到账户设置")
  assert(fixture177.settingsSaves.some(call => call.account === "settings-A" && call.changes.show_plan_name_in_notification === true), "保存请求必须包含显示计划名字段")
  const reloadedA = await call<{settings: any}>("settings.get")
  useApp.getState().set({ settings: reloadedA.settings })
  switchPage("classroom")
  switchPage("settings")
  await until(() => (document.querySelector('[role="switch"][aria-label="通知里显示计划名"]') as HTMLButtonElement)?.getAttribute("aria-checked") === "true", "重新挂载后开关没有保持")
  fixture177.accountId = "settings-B"
  useApp.getState().set({ account: identity("settings-B", "另一个账号"), settings: await call<{settings: any}>("settings.get").then(v => v.settings) })
  switchPage("settings")
  await until(() => (document.querySelector('[role="switch"][aria-label="通知里显示计划名"]') as HTMLButtonElement)?.getAttribute("aria-checked") === "false", "开关设置串到了其他账号")
  fixture177.accountId = "settings-A"
  useApp.getState().set({ account: identity("settings-A", "提醒偏好测试改名"), settings: await call<{settings: any}>("settings.get").then(v => v.settings) })
  switchPage("settings")
  await until(() => (document.querySelector('[role="switch"][aria-label="通知里显示计划名"]') as HTMLButtonElement)?.getAttribute("aria-checked") === "true", "原账号开关设置在改显示名后未保留")
  results.push("提醒开关默认关闭、保存透传、重挂载保持且按账号隔离")
  phase = "completed"
  ;(window as any).result177 = { ok: true, checks: results }
}

run().catch(error => { (window as any).result177 = { ok: false, phase, error: String(error), body: document.body.innerText } })
