import * as React from "react"
import { createRoot } from "react-dom/client"
import { Classroom } from "../../../refactor/learning/web/src/pages/Classroom"
import { useApp } from "../../../refactor/learning/web/src/store"
import "../../../refactor/learning/web/src/theme.css"
import { addReply, requests } from "./sources79_bridge"

const checks: string[] = []
const wait = () => new Promise(resolve => setTimeout(resolve, 15))
async function until(test: () => boolean) { const end = performance.now() + 10000; while (!test()) { if (performance.now() > end) throw new Error("UI timeout"); await wait() } }
function assert(value: unknown, message: string) { if (!value) throw new Error(message); checks.push(message) }
const first = () => document.querySelector<HTMLElement>(".bubble.bot")!
const click = (selector: string, scope: ParentNode = document) => scope.querySelector<HTMLButtonElement>(selector)!.click()
const root = createRoot(document.getElementById("root")!)
useApp.setState({ planId: "sources79", route: "classroom", settings: { theme: "sakura", font_scale: 1, reduce_motion: true } as never })
root.render(<main style={{ height: "100%", padding: 18 }}><Classroom /></main>)
async function run() {
  await until(() => !!document.querySelector(".original-toggle"))
  assert(!document.querySelector(".original-groups"), "所有新回复默认只显示折叠入口")
  assert(first().querySelector(".original-toggle")?.textContent?.includes("4 处"), "出处总数按段落计")
  click(".original-toggle", first()); await until(() => !!first().querySelector(".original-group"))
  assert(first().querySelectorAll(".original-group").length === 2, "同资料三个段落合并为一组，另一份资料独立")
  assert([...first().querySelectorAll("h4")].filter(e => e.textContent === "JavaGuide").length === 1, "同资料标题只显示一次")
  assert([...first().querySelectorAll(".original-group:first-child .original-entry")].map(e => e.getAttribute("data-source-id")).join(",") === "c2,c3,c10", "按出处位置自然排序")
  click('[data-source-id="c2"] button', first()); await until(() => !!first().querySelector('[data-source-id="c2"] .original-fulltext'))
  click('[data-source-id="c3"] button', first()); await until(() => !!first().querySelector('[data-source-id="c3"] .original-fulltext'))
  click('[data-source-id="c2"] button', first()); await until(() => !first().querySelector('[data-source-id="c2"] .original-fulltext'))
  assert(!!first().querySelector('[data-source-id="c3"] .original-fulltext'), "各处全文独立展开收起")
  click(".original-toggle", first()); await until(() => !first().querySelector(".original-groups"))
  click(".original-toggle", first()); await until(() => !!first().querySelector('[data-source-id="c3"] .original-fulltext'))
  checks.push("当前会话同一回复保留各处展开状态")
  click(".original-toggle", first()); await until(() => !first().querySelector(".original-groups"))
  click(".cite:nth-of-type(3)", first())
  await until(() => !!first().querySelector('[data-source-id="c3"] .original-fulltext'))
  assert(document.activeElement?.closest(".original-entry")?.getAttribute("data-source-id") === "c3", "正文〔3〕展开并聚焦对应第三处")
  const target = first().querySelector('[data-source-id="c3"]')!.getBoundingClientRect()
  const viewport = document.querySelector(".class-scroll")!.getBoundingClientRect()
  assert(target.bottom > viewport.top && target.top < viewport.bottom, "正文标记滚动到原文可见处")
  assert(!requests.some(r => r.method === "classroom.source"), "已有原文直接展示，不重复请求弹层")
  addReply(); await until(() => document.querySelectorAll(".bubble.bot").length === 2)
  assert(!document.querySelectorAll(".bubble.bot")[1].querySelector(".original-groups"), "新回复默认折叠，不继承上一条")
  assert(!!first().querySelector('[data-source-id="c3"] .original-fulltext'), "课堂刷新不会清空旧回复展开状态")
  await until(() => !!document.querySelector(".teacher-edge") && !document.querySelector<HTMLButtonElement>(".teacher-edge")!.disabled)
  assert(!!document.querySelector(".teacher-edge .lucide-chevron-right") && !!document.querySelector(".teacher-edge .lucide-panel-right-close"), "收起细边两个箭头均朝右")
  click(".teacher-edge"); await until(() => !document.querySelector(".teacher-edge"))
  assert(!!document.querySelector('[aria-label="收起老师"] .lucide-panel-right-open'), "展开后的收起图标朝左")
  click('[aria-label="收起老师"]'); await until(() => !!document.querySelector(".teacher-edge"))
  ;(window as any).sources79Result = { ok: true, checks, requests }
}
;(window as any).sources79Show = async (theme: string, expanded: boolean) => {
  ;(window as any).sources79ShotReady = false
  document.documentElement.dataset.theme = theme
  useApp.setState({ settings: { ...useApp.getState().settings, theme } as never })
  if ((first().querySelector(".original-toggle")!.getAttribute("aria-expanded") === "true") !== expanded) click(".original-toggle", first())
  await until(() => !!first().querySelector(".original-groups") === expanded)
  document.querySelector(".class-scroll")!.scrollTop = 0
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  let marker = document.getElementById("sources79-paint-marker")
  if (!marker) { marker = document.createElement("i"); marker.id = "sources79-paint-marker"; marker.setAttribute("aria-hidden", "true"); document.body.append(marker) }
  const color = theme === "sakura" ? (expanded ? "#bb2233" : "#aa1122") : (expanded ? "#dd4455" : "#cc3344")
  marker.style.cssText = `position:fixed;left:0;top:0;width:1px;height:1px;z-index:2147483647;pointer-events:none;background:${color}`
  ;(window as any).sources79ShotReady = true
}
run().catch(error => { (window as any).sources79Result = { ok: false, checks, error: String(error.stack || error), body: document.body.innerText } })
